"""Ménage du magasin : on garde ce qui reconstruit, on efface le reste.

Tout se joue dans un magasin TEMPORAIRE : le balayage efface des dossiers
entiers, il ne touche jamais aux vraies données pendant un test.

Exécution :  cd backend && venv/Scripts/python.exe tests/test_stockage.py
"""
from __future__ import annotations
import os
import shutil
import sys
import tempfile
import time

import racine  # noqa: F401  -- met backend/ sur le chemin

from app.config import TRADUCTION_ORPHELINE_GRACE_S          # noqa: E402
from app.services.stockage import (                           # noqa: E402
    balayer_magasin, limiter_apercus, purger_racine,
)

V = "v9.9.9"


def _ecrire(path: str, taille: int = 10, age_s: float = 0) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * taille)
    if age_s:
        t = time.time() - age_s
        os.utime(path, (t, t))
    return path


def run() -> int:
    checks = []

    def ok(nom, cond, detail=""):
        checks.append((nom, bool(cond), detail))

    magasin = tempfile.mkdtemp(prefix="magasin_")
    try:
        j = lambda *p: os.path.join(magasin, *p)          # noqa: E731
        vieux = TRADUCTION_ORPHELINE_GRACE_S + 3600

        # Racine ORPHELINE : document supprimé, ses fichiers sont restés.
        [_ecrire(j("aaaa1111bbbb", "original.pdf")),
                _ecrire(j("aaaa1111bbbb", "en", "pages.json")),
                _ecrire(j("aaaa1111bbbb", "en", f"pages.json.render.{V}.pdf"))]
        # Racine CITÉE par un Document.
        orig = _ecrire(j("cccc2222dddd", "original.pdf"))
        extr = _ecrire(j("cccc2222dddd", "extraction.json"))
        trad = _ecrire(j("cccc2222dddd", "en", "pages_debug.json"))
        rendu_ok = _ecrire(j("cccc2222dddd", "en", f"pages_debug.json.render.{V}.pdf"))
        rendu_vieux = _ecrire(j("cccc2222dddd", "en", "pages_debug.json.render.v1.0.0.pdf"))
        rendu_tmp = _ecrire(j("cccc2222dddd", "en", f"pages_debug.json.render.{V}.pdf.tmp"))
        rendu_sans_trad = _ecrire(j("cccc2222dddd", "en", f"pages_disparu.json.render.{V}.pdf"))
        sortie_job = _ecrire(j("cccc2222dddd", "en", "render_debug_p1_6fca45f9.pdf"))
        sortie_pptx = _ecrire(j("cccc2222dddd", "fr", "render_5f1af709.pptx"))
        partiel = _ecrire(j("cccc2222dddd", "en", "partial_debug_ec410289.pdf.tmp"))
        trad_orph_vieille = _ecrire(j("cccc2222dddd", "de", "pages_p1.json"), age_s=vieux)
        rendu_orph_vieille = _ecrire(j("cccc2222dddd", "de", f"pages_p1.json.render.{V}.pdf"))
        trad_orph_recente = _ecrire(j("cccc2222dddd", "it", "pages.json"))
        # Ce qui n'est PAS une racine de job.
        hors = [_ecrire(j("_previews", "abc.pdf")),
                _ecrire(j("_test_x", "original.pdf"))]

        compte = balayer_magasin({orig, trad}, V, magasin=magasin)

        ok("ORPHELIN  la racine sans référence part ENTIÈRE",
           not os.path.exists(j("aaaa1111bbbb")))
        ok("GARDE  original, extraction et traduction citée survivent",
           all(map(os.path.isfile, (orig, extr, trad))))
        ok("GARDE  le rendu de la version COURANTE survit", os.path.isfile(rendu_ok))
        ok("PÉRIMÉ  rendu d'une autre version du moteur effacé",
           not os.path.exists(rendu_vieux))
        ok("PÉRIMÉ  rendu à moitié écrit (.tmp) effacé", not os.path.exists(rendu_tmp))
        ok("PÉRIMÉ  rendu dont la traduction a disparu effacé",
           not os.path.exists(rendu_sans_trad))
        ok("JOB  sorties de job (pdf, pptx, partiel) effacées",
           not any(map(os.path.exists, (sortie_job, sortie_pptx, partiel))))
        ok("ORPHELINE  vieille traduction non citée effacée, avec son rendu",
           not os.path.exists(trad_orph_vieille)
           and not os.path.exists(rendu_orph_vieille))
        ok("GRÂCE  traduction non citée RÉCENTE gardée (relance après échec)",
           os.path.isfile(trad_orph_recente))
        ok("BORNE  `_previews` et dossiers non-hex jamais balayés",
           all(map(os.path.isfile, hors)))
        ok("ÉLAGAGE  les dossiers vidés disparaissent",
           not os.path.exists(j("cccc2222dddd", "fr"))
           and not os.path.exists(j("cccc2222dddd", "de")))
        ok("COMPTE  octets libérés comptés", compte["octets"] > 0, str(compte))

        # Sans aucune référence connue, rien n'est jamais considéré cité :
        # c'est à l'APPELANT de ne pas balayer s'il n'a pas pu lire la base
        # (cf. balayer_au_demarrage). Ici on vérifie la borne du magasin.
        dehors = tempfile.mkdtemp(prefix="dehors_")
        ok("BORNE  purger_racine refuse un dossier hors magasin",
           purger_racine(dehors, magasin) is False and os.path.isdir(dehors))
        ok("BORNE  purger_racine refuse le magasin lui-même",
           purger_racine(magasin, magasin) is False and os.path.isdir(magasin))
        shutil.rmtree(dehors, ignore_errors=True)

        # Aperçus Office : borne de taille, plus anciens d'abord.
        prev = j("_previews_borne")
        a = _ecrire(os.path.join(prev, "a.pdf"), 100, age_s=300)
        b = _ecrire(os.path.join(prev, "b.pdf"), 100, age_s=200)
        c = _ecrire(os.path.join(prev, "c.pdf"), 100, age_s=100)
        n = limiter_apercus(max_octets=250, dossier=prev)
        ok("APERÇUS  au-delà du plafond, le plus ancien part seul",
           n == 1 and not os.path.exists(a) and os.path.isfile(b)
           and os.path.isfile(c), f"effacés={n}")
        ok("APERÇUS  sous le plafond, rien ne bouge",
           limiter_apercus(max_octets=250, dossier=prev) == 0)
    finally:
        shutil.rmtree(magasin, ignore_errors=True)

    print()
    for nom, cond, detail in checks:
        print(f"  {'OK  ' if cond else 'ECHEC'}  {nom}"
              + (f"   [{detail}]" if detail and not cond else ""))
    passe = sum(1 for _, c, _ in checks if c)
    print(f"\n  {passe}/{len(checks)}\n")
    return 0 if passe == len(checks) else 1


if __name__ == "__main__":
    sys.exit(run())
