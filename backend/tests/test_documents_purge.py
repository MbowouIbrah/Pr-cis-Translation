"""Magasin PARTAGÉ : purge par comptage de références + clé de cache honnête.

Ce que ces tests défendent, dans les mots de la demande :
  « si un utilisateur supprime le document dans son compte, l'autre doit
    toujours pouvoir y accéder »

Deux comptes qui déposent le même fichier pointent sur les MÊMES octets (le
magasin est adressé par le hash du contenu). Une suppression naïve effacerait
donc la traduction d'autrui — sans le moindre bruit, et sans retour possible.

Exécution :  backend/venv/Scripts/python.exe backend/test_documents_purge.py
Requiert la base PostgreSQL (comme le reste du backend).
"""
from __future__ import annotations
import asyncio
import os
import shutil
import sys
import uuid

import racine  # noqa: F401  -- met backend/ sur le chemin

from app.core.database import async_session                      # noqa: E402
from app.models import User, Document                       # noqa: E402
from app.api.documents import (                          # noqa: E402
    STORAGE_BASE, _purge_if_orphan, _inside_store, _prune_empty_dirs,
    delete_document,
)

import racine  # noqa: F401


def _mk_user() -> User:
    return User(email=f"purge-{uuid.uuid4().hex[:10]}@example.com",
                plan="starter", email_verified=True)


def _mk_doc(user_id: str, original: str, translated: str | None) -> Document:
    return Document(user_id=user_id, original_name="partage.pdf",
                    source_lang="auto", target_lang="en",
                    original_path=original, translated_path=translated,
                    size_bytes=1234, status="done")


async def _monter_partage():
    """Un fichier partagé DANS LE VRAI MAGASIN + deux comptes qui y pointent.

    Le fichier doit être dans le vrai magasin : `_inside_store` refuse tout ce
    qui est ailleurs, un dossier temporaire quelconque ne prouverait donc rien.
    """
    racine = os.path.join(STORAGE_BASE, f"_test_{uuid.uuid4().hex[:8]}")
    dossier = os.path.join(racine, "en")
    os.makedirs(dossier, exist_ok=True)
    trad = os.path.join(dossier, "pages.json")
    orig = os.path.join(racine, "original.pdf")
    for p in (trad, orig):
        with open(p, "wb") as f:
            f.write(b"%PDF-1.4\n%octets partages\n")

    a, b = _mk_user(), _mk_user()
    async with async_session() as db:
        db.add_all([a, b])
        await db.commit()
        da, dbb = _mk_doc(a.id, orig, trad), _mk_doc(b.id, orig, trad)
        db.add_all([da, dbb])
        await db.commit()
        ids = (da.id, dbb.id)
    return {"trad": trad, "orig": orig, "racine": racine,
            "users": (a.id, b.id), "ids": ids}


async def _demonter(ctx):
    async with async_session() as db:
        for uid in ctx["users"]:
            u = await db.get(User, uid)
            if u:
                await db.delete(u)          # les Document tombent en cascade
        await db.commit()
    shutil.rmtree(ctx["racine"], ignore_errors=True)


async def run():
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    # ── A supprime : B y renvoie encore ─────────────────────────────────────
    ctx = await _monter_partage()
    try:
        async with async_session() as db:
            await db.delete(await db.get(Document, ctx["ids"][0]))
            await db.commit()
            purge_trad = await _purge_if_orphan(db, ctx["trad"])
            purge_orig = await _purge_if_orphan(db, ctx["orig"])

        ok("REF  A supprime : la TRADUCTION de B survit",
           purge_trad is False and os.path.isfile(ctx["trad"]))
        ok("REF  A supprime : l'ORIGINAL partagé survit",
           purge_orig is False and os.path.isfile(ctx["orig"]))
    finally:
        await _demonter(ctx)

    # ── Le dernier qui part libère les octets ───────────────────────────────
    ctx = await _monter_partage()
    try:
        async with async_session() as db:
            for doc_id in ctx["ids"]:
                await db.delete(await db.get(Document, doc_id))
            await db.commit()
            purge_trad = await _purge_if_orphan(db, ctx["trad"])

        ok("REF  plus aucune référence : les octets sont LIBÉRÉS",
           purge_trad is True and not os.path.exists(ctx["trad"]),
           "sans ça le disque enfle indéfiniment")
    finally:
        await _demonter(ctx)

    # ── La fenêtre de concurrence : B TRADUIT pendant que A supprime ────────
    # Pendant toute la traduction, le Document de B a `translated_path` NULL :
    # un comptage au fichier près ne voyait AUCUNE référence au rendu partagé
    # et le déclarait orphelin — purgé sous les pieds de B. Le comptage se fait
    # sur la RACINE du job : l'original de B suffit à protéger tout le dossier.
    ctx = await _monter_partage()
    try:
        async with async_session() as db:
            # B est en cours : sa ligne ne référence que l'original.
            b_doc = await db.get(Document, ctx["ids"][1])
            b_doc.translated_path = None
            b_doc.status = "translating"
            # A supprime son document (qui référençait orig + trad).
            await db.delete(await db.get(Document, ctx["ids"][0]))
            await db.commit()
            purge_trad = await _purge_if_orphan(db, ctx["trad"])

        ok("FENÊTRE  B traduit (translated NULL) : le rendu partagé survit",
           purge_trad is False and os.path.isfile(ctx["trad"]),
           "un comptage au fichier près purgeait le rendu en cours de production")
    finally:
        await _demonter(ctx)

    # ── Suppression : TOUT le dossier part avec le dernier document ───────────
    # Avant, seuls les deux chemins connus de la base partaient : rendus en
    # cache, extraction, autres plages et sorties de job restaient pour toujours.
    def _annexes(ctx):
        d = os.path.dirname(ctx["trad"])
        chemins = [ctx["trad"] + ".render.v1.0.4.pdf",
                   os.path.join(ctx["racine"], "extraction.json"),
                   os.path.join(d, "pages_p1.json"),
                   os.path.join(d, "render_p1_6fca45f9.pdf")]
        for p in chemins:
            with open(p, "wb") as f:
                f.write(b"annexe")
        return chemins

    async def _supprimer(ctx, i):
        async with async_session() as db:
            u = await db.get(User, ctx["users"][i])
            return await delete_document(ctx["ids"][i], user=u, db=db)

    ctx = await _monter_partage()
    try:
        annexes = _annexes(ctx)
        await _supprimer(ctx, 0)
        ok("SUPPR  B tient encore au job : rien n'est effacé, annexes comprises",
           all(map(os.path.isfile, annexes + [ctx["trad"], ctx["orig"]])))
        r = await _supprimer(ctx, 1)
        ok("SUPPR  le dernier document parti : le dossier ENTIER disparaît",
           not os.path.exists(ctx["racine"]) and r["files_purged"] == 1,
           str(r))
    finally:
        await _demonter(ctx)

    # ── Garde-fous : ce qu'on refuse d'effacer ──────────────────────────────
    # CONTRÔLE NÉGATIF : sans cette borne, un `original_path` mal formé ferait
    # effacer un fichier quelconque de la machine.
    ok("BORNE  un chemin DANS le magasin est accepté",
       _inside_store(os.path.join(STORAGE_BASE, "x", "y.pdf")) is True)
    ok("BORNE  une remontée hors magasin est refusée",
       _inside_store(os.path.join(STORAGE_BASE, "..", "app.py")) is False)
    ok("BORNE  un chemin système est refusé",
       _inside_store(r"C:\Windows\System32\drivers\etc\hosts") is False)

    intrus_dir = os.path.join(os.path.dirname(STORAGE_BASE), "_test_intrus")
    os.makedirs(intrus_dir, exist_ok=True)
    intrus = os.path.join(intrus_dir, "precieux.pdf")
    with open(intrus, "wb") as f:
        f.write(b"%PDF-1.4\n")
    try:
        async with async_session() as db:
            purge = await _purge_if_orphan(db, intrus)
        ok("BORNE  orphelin mais HORS magasin : jamais effacé",
           purge is False and os.path.isfile(intrus))
    finally:
        shutil.rmtree(intrus_dir, ignore_errors=True)

    # ── Le ménage des dossiers ──────────────────────────────────────────────
    base = os.path.join(STORAGE_BASE, f"_test_prune_{uuid.uuid4().hex[:8]}")
    profond = os.path.join(base, "en", "sous_dossier_vide")
    os.makedirs(profond, exist_ok=True)
    temoin = os.path.join(base, "en", "partial_abc.pdf")
    with open(temoin, "wb") as f:
        f.write(b"partiel en cours")
    try:
        _prune_empty_dirs(profond)
        ok("PRUNE  le dossier VIDE est nettoyé", not os.path.exists(profond))
        ok("PRUNE  un partiel de job n'est pas emporté", os.path.isfile(temoin))
        ok("PRUNE  le dossier NON VIDE survit", os.path.isdir(os.path.dirname(temoin)))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    vide = os.path.join(STORAGE_BASE, f"_test_root_{uuid.uuid4().hex[:8]}")
    os.makedirs(vide, exist_ok=True)
    _prune_empty_dirs(vide)
    ok("PRUNE  le MAGASIN lui-même n'est jamais effacé",
       not os.path.exists(vide) and os.path.isdir(STORAGE_BASE))

    # ── La clé d'identité : l'EMPREINTE DU CONTENU, seule ───────────────────
    # On interroge le VRAI constructeur de clé d'app.py — une vérification qui
    # recomposerait le chemin elle-même serait une tautologie.
    from app.config import TRANSLATIONS_DIR              # noqa: E402
    from app.services.documents import build_job_paths   # noqa: E402

    # ── Les deux modules désignent-ils le MÊME magasin ? ────────────────────
    # LE défaut d'origine : `documents.py` calculait `backend/routes/translations`
    # (dirname de son PROPRE fichier) pendant qu'`app.py` écrivait dans
    # `backend/translations`. La suppression « protégeait » les fichiers d'autrui
    # en ne trouvant jamais rien à effacer. L'invariant n'est pas la valeur :
    # c'est l'ACCORD entre les modules (un test de mutation l'a réclamé).
    ok("MAGASIN  documents.py et app.py désignent le même dossier",
       os.path.realpath(STORAGE_BASE) == os.path.realpath(TRANSLATIONS_DIR),
       f"{STORAGE_BASE} != {TRANSLATIONS_DIR}")

    # DÉDUP ENTRE NOMS : le nom du fichier ne doit PAS entrer dans la clé. Deux
    # noms différents pour le même contenu = un seul dossier, une seule
    # traduction. C'était le défaut : la clé contenait le nom, `contrat.pdf` et
    # `contrat-final.pdf` identiques étaient traduits (et payés) deux fois.
    j_pdf, _ = build_job_paths("abc123", "en")
    ok("CLÉ  l'empreinte SEULE identifie le document (nom hors clé)",
       os.path.basename(j_pdf) == "abc123", j_pdf)

    # PDF et DOCX de même contenu partagent la racine (le magasin est adressé par
    # le contenu, pas par le moteur) ; seule la sous-langue diffère.
    _, pdf_dir = build_job_paths("abc123", "en")
    _, en2 = build_job_paths("abc123", "en")
    _, fr_dir = build_job_paths("abc123", "fr")
    ok("CLÉ  aucun segment de version de moteur dans le chemin",
       "v21" not in pdf_dir.split(os.sep) and "v20" not in pdf_dir.split(os.sep),
       pdf_dir)
    ok("CLÉ  à contenu+langue constants, la clé est stable (dédup entre comptes)",
       pdf_dir == en2)
    ok("CLÉ  deux langues du même contenu ne se mélangent pas",
       pdf_dir != fr_dir)

    # ── Rapport ─────────────────────────────────────────────────────────────
    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ECHEC'}  {nom}"
              + (f"   [{detail}]" if detail and not cond else ""))
    passe = sum(1 for _, c, _ in checks if c)
    print(f"\n  {passe}/{len(checks)}\n")
    return 0 if passe == len(checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
