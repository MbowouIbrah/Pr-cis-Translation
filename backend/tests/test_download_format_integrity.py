"""Intégrité de FORMAT du téléchargement — le bug du PPTX corrompu.

Le viewer affiche tout en PDF : un PPTX/DOCX est converti en PDF pour l'aperçu,
et ce PDF est mis en cache (`render_cache`). Ce cache d'APERÇU s'écrit sous la
clé du document. Le téléchargement lisait ce cache SANS regarder l'extension :
il servait donc le PDF d'aperçu sous le nom `.pptx`. Résultat mesuré côté
utilisateur : PowerPoint refusait d'ouvrir le fichier (« PowerPoint ne peut pas
lire … .pptx »), car les octets commençaient par `%PDF`, pas par `PK`.

Ce test verrouille la règle : un téléchargement rend TOUJOURS le format natif
(ZIP `PK` pour un PPTX), quel que soit l'état du cache d'aperçu. L'aperçu, lui,
reste un PDF — c'est son rôle.

Aucun test existant ne vérifiait le FORMAT du téléchargement (ils vérifiaient le
droit d'accès et l'extractibilité du texte) : c'est exactement l'angle mort par
lequel la corruption est passée.

Exécution :  backend/venv/Scripts/python.exe backend/test_download_format_integrity.py
Requiert PostgreSQL.
"""
from __future__ import annotations
import asyncio
import io
import os
import shutil
import sys
import uuid

import racine  # noqa: F401  -- met backend/ sur le chemin

from sqlalchemy.ext.asyncio import (                     # noqa: E402
    create_async_engine, async_sessionmaker, AsyncSession,
)
from sqlalchemy.pool import NullPool                     # noqa: E402
from app.core.database import DATABASE_URL                        # noqa: E402
from app.models import User, Document                        # noqa: E402

# Les substitutions ciblent le module OU LE NOM EST UTILISE, pas celui qui
# le definit : `app.api.documents` importe `render_translation_bytes` et en
# garde SA propre reference. Remplacer la definition d'origine ne changerait
# rien a ce que la route appelle -- le test passerait a cote de son sujet.
from app.api import documents as appmod                  # noqa: E402
from app.services import render_cache                                      # noqa: E402
from app.api.documents import (                           # noqa: E402
    STORAGE_BASE, download_document, preview_document,
)

_eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
async_session = async_sessionmaker(_eng, class_=AsyncSession,
                                   expire_on_commit=False)


def _real_pptx_bytes() -> bytes:
    """Un vrai PPTX minimal (ZIP OPC valide, en-tête `PK`)."""
    from pptx import Presentation
    buf = io.BytesIO()
    Presentation().save(buf)  # pyrefly: ignore[not-callable]
    return buf.getvalue()


def _fake_pdf_bytes() -> bytes:
    return b"%PDF-1.7\n% fake preview\n1 0 obj\n<<>>\nendobj\n"


async def _monter(ext: str):
    racine = os.path.join(STORAGE_BASE, f"_test_fmt_{uuid.uuid4().hex[:8]}")
    lang_dir = os.path.join(racine, "en")
    os.makedirs(lang_dir, exist_ok=True)
    orig = os.path.join(racine, f"original.{ext}")
    # PPTX/DOCX : la traduction persistante s'appelle translated.json.
    trad = os.path.join(lang_dir, "translated.json")
    with open(orig, "wb") as f:
        f.write(_real_pptx_bytes() if ext == "pptx" else b"stub")
    with open(trad, "w", encoding="utf-8") as f:
        f.write('{"slides": []}')

    u = User(email=f"fmt-{uuid.uuid4().hex[:10]}@example.com",
             plan="starter", email_verified=True)   # payant : accès au clair
    async with async_session() as db:
        db.add(u)
        await db.commit()
        d = Document(user_id=u.id, original_name=f"presentation.{ext}",
                     source_lang="auto", target_lang="en",
                     original_path=orig, translated_path=trad,
                     size_bytes=1000, status="done", paid=True)
        db.add(d)
        await db.commit()
        doc_id = d.id
    return {"racine": racine, "user_id": u.id, "doc_id": doc_id, "trad": trad}


async def _demonter(ctx):
    async with async_session() as db:
        obj = await db.get(User, ctx["user_id"])
        if obj:
            await db.delete(obj)
        await db.commit()
    shutil.rmtree(ctx["racine"], ignore_errors=True)


async def run():
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    # Substitution : le rendu natif rend un VRAI PPTX ; la conversion d'aperçu
    # rend un PDF. C'est exactement la topologie réelle (inject_translation vs
    # convert_to_pdf_bytes), sans dépendre de LibreOffice.
    vrai_rendu = appmod.render_translation_bytes
    vrai_conv = appmod.convert_to_pdf_bytes
    appmod.render_translation_bytes = lambda *a, **k: _real_pptx_bytes()
    appmod.convert_to_pdf_bytes = lambda *a, **k: _fake_pdf_bytes()

    ctx = await _monter("pptx")
    try:
        async with async_session() as db:
            user = await db.get(User, ctx["user_id"])

            # 1. On EMPOISONNE délibérément le cache comme le faisait l'aperçu :
            #    un PDF écrit sous la clé du document PPTX.
            render_cache.store_render(_fake_pdf_bytes(), ctx["trad"],
                                       render_cache.marque_polices())
            assert render_cache.cache_valid(ctx["trad"]), \
                "le cache empoisonné devrait être considéré valide (préparation du test)"

            # 2. Téléchargement : DOIT rendre le PPTX natif (PK…), jamais le PDF.
            res = await download_document(ctx["doc_id"], user=user, db=db)
            head = bytes(res.body[:4])
            ok("DOWNLOAD  PPTX : rend un ZIP natif (PK), pas le PDF du cache",
               head[:2] == b"PK", f"head={head!r}")
            ok("DOWNLOAD  PPTX : ne commence PAS par %PDF",
               not bytes(res.body[:5]).startswith(b"%PDF"),
               f"head={bytes(res.body[:5])!r}")

            # 3. Aperçu : DOIT rester un PDF (c'est son rôle), cache réutilisé.
            res = await preview_document(ctx["doc_id"], user=user, db=db)
            ok("PREVIEW  PPTX : rend bien un PDF",
               bytes(res.body[:5]).startswith(b"%PDF"),
               f"head={bytes(res.body[:5])!r}")
    finally:
        await _demonter(ctx)
        appmod.render_translation_bytes = vrai_rendu
        appmod.convert_to_pdf_bytes = vrai_conv

    print()
    n_ok = sum(1 for _, c, _ in checks if c)
    for nom, cond, detail in checks:
        etat = "  OK  " if cond else " FAIL "
        line = f"{etat}  {nom}"
        if not cond and detail:
            line += f"   [{detail}]"
        print(line)
    print(f"\n{n_ok}/{len(checks)}")
    return n_ok == len(checks)


if __name__ == "__main__":
    ok_all = asyncio.run(run())
    sys.exit(0 if ok_all else 1)
