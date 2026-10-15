"""XLSX — zones de texte et commentaires, sans toucher aux identités.

CE QUI EST EN JEU
-----------------
Une zone de texte posée sur une feuille porte souvent l'essentiel du
commentaire éditorial : un encadré « Attention », une note de bas de tableau.
Elle est aussi visible que le tableau lui-même. Un commentaire de cellule, lui,
est ce qu'on lit quand on ne comprend pas un chiffre.

DEUX FORMATS DE COMMENTAIRES COEXISTENT, et ils ne se remplacent pas :
`comments*.xml` (historique, celui qui porte le texte affiché) et
`threadedComments/*` (moderne, les fils de discussion). Excel maintient les
deux ; n'en traiter qu'un laisse la moitié des notes en langue source.

CE QU'ON NE TRADUIT JAMAIS, ET POURQUOI
---------------------------------------
`name="TextBox 1"` — un IDENTIFIANT interne de forme, jamais affiché, que des
macros ou des références peuvent citer. Le traduire ne se verrait nulle part et
casserait ce qui s'y réfère.

`<author>Marie Durand</author>` — un NOM DE PERSONNE. Un nom propre traduit
devient quelqu'un d'autre. Pire : `authorId` renvoie à cette liste PAR INDEX,
si bien que la toucher réattribuerait les notes.

C'est la même règle que partout dans ce moteur : on traduit ce qui s'affiche,
jamais ce qui identifie.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_annotations.py
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import engines                                              # noqa: E402
from engines import runtags                                  # noqa: E402
from fabrique_classeurs import (COMMENT_TRADUISIBLES,        # noqa: E402
                                DRAWING_TRADUISIBLES,
                                JAMAIS_TRADUIT,
                                THREAD_TRADUISIBLES,
                                construire_complet,
                                construire_multi)

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def _traduire(src: str, dossier: str) -> str:
    """Traduit tout sauf les noms d'onglets (hors sujet ici)."""
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(dossier, "ex.json"))
    for el in extraction["workbook"]["elements"]:
        if el["context"]["part"] == "sheetName":
            el["translated_text"] = el["text"]
            continue
        el["translated_text"] = re.sub(
            r"(\[\[(\d+)\]\])(.*?)(\[\[/\2\]\])",
            lambda m: m.group(1) + "EN:" + m.group(3) + m.group(4),
            el["text"])
    tr = os.path.join(dossier, "tr.json")
    with open(tr, "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False)
    sortie = os.path.join(dossier, "out.xlsx")
    ok, msg = engines.new_engine("xlsx").inject_translation(src, tr, sortie)
    assert ok, msg
    return sortie


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_ann_")
    src = construire_complet(os.path.join(travail, "src.xlsx"))

    # ── 1. Le relevé, partie par partie ────────────────────────────────────
    extraction, _ = engines.new_engine("xlsx").extract_text(
        src, os.path.join(travail, "ex0.json"))
    par_partie: dict[str, list[str]] = {}
    for el in extraction["workbook"]["elements"]:
        par_partie.setdefault(el["context"]["part"], []).append(
            runtags.sans_balises(el["text"]))

    check(par_partie.get("drawing") == DRAWING_TRADUISIBLES,
          "RELEVE  les zones de texte sont relevées",
          str(par_partie.get("drawing")))
    check(par_partie.get("comment") == COMMENT_TRADUISIBLES,
          "RELEVE  les commentaires ANCIENS le sont aussi",
          str(par_partie.get("comment")))
    check(par_partie.get("threadedComment") == THREAD_TRADUISIBLES,
          "RELEVE  et les fils de discussion MODERNES",
          str(par_partie.get("threadedComment")))

    # Le commentaire à DEUX runs (« Chiffre » + « a confirmer ») doit arriver
    # au modèle d'un seul tenant : découpé, il perdrait son sens.
    check("Chiffre a confirmer" in (par_partie.get("comment") or []),
          "RELEVE  un commentaire à deux runs est relevé ENTIER",
          str(par_partie.get("comment")))

    # Aucune identité ne part au modèle.
    tous = [t for liste in par_partie.values() for t in liste]
    check(all(x not in tous for x in JAMAIS_TRADUIT),
          "RELEVE  aucune IDENTITÉ n'est relevée (nom de forme, auteur)",
          str([x for x in JAMAIS_TRADUIT if x in tous]))

    # ── 2. L'injection ────────────────────────────────────────────────────
    sortie = _traduire(src, travail)
    with zipfile.ZipFile(sortie) as z:
        abime = z.testzip()
        dessin = z.read("xl/drawings/drawing1.xml").decode("utf-8")
        commentaires = z.read("xl/comments1.xml").decode("utf-8")
        fil = z.read(
            "xl/threadedComments/threadedComment1.xml").decode("utf-8")
    check(abime is None, "INJECTE l'archive reste intacte", str(abime))

    textes_dessin = re.findall(r"<a:t>([^<]*)</a:t>", dessin)
    check(all(t.startswith("EN:") for t in textes_dessin) and textes_dessin,
          "INJECTE la zone de texte est traduite", str(textes_dessin))

    textes_com = re.findall(r"<t>([^<]*)</t>", commentaires)
    check(all(t.startswith("EN:") for t in textes_com) and textes_com,
          "INJECTE les commentaires anciens le sont", str(textes_com))

    textes_fil = re.findall(r"<text>([^<]*)</text>", fil)
    check(all(t.startswith("EN:") for t in textes_fil) and textes_fil,
          "INJECTE les fils modernes aussi", str(textes_fil))

    # ── 3. Les identités, intactes ────────────────────────────────────────
    check('name="TextBox 1"' in dessin,
          "IDENTITE le NOM DE FORME est rendu intact",
          str(re.findall(r'name="([^"]*)"', dessin)))
    check("<author>Marie Durand</author>" in commentaires,
          "IDENTITE l'AUTEUR d'un commentaire est rendu intact",
          str(re.findall(r"<author>([^<]*)</author>", commentaires)))

    # ── 4. Un classeur sans aucune de ces parties ─────────────────────────
    # `xl/drawings/`, `comments*.xml` et `threadedComments/` sont absents de la
    # plupart des classeurs : les releveurs et les injecteurs doivent traverser
    # sans rien lever.
    nu = construire_multi(os.path.join(travail, "nu.xlsx"))
    dossier_nu = os.path.join(travail, "nu")
    os.makedirs(dossier_nu, exist_ok=True)
    ex_nu, _ = engines.new_engine("xlsx").extract_text(
        nu, os.path.join(dossier_nu, "ex.json"))
    parts_nu = {el["context"]["part"]
                for el in ex_nu["workbook"]["elements"]}
    check(not ({"drawing", "comment", "threadedComment"} & parts_nu),
          "ABSENT  un classeur sans annotations n'en relève aucune",
          str(parts_nu))
    sortie_nu = _traduire(nu, dossier_nu)
    check(os.path.exists(sortie_nu),
          "ABSENT  et son injection réussit quand même")

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
