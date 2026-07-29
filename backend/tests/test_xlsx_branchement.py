"""XLSX — le moteur est-il RÉELLEMENT branché à l'application ?

LE DÉFAUT VERROUILLÉ ICI
------------------------
Le moteur XLSX passait ses 29 contrôles, `engines.supports("xlsx")` rendait
`True`, et `xlsx` figurait dans `ALLOWED_EXTENSIONS` — donc l'envoi était
accepté. Mais AUCUNE branche de `translation_runner` ne l'appelait : tout
classeur mourait sur « Type de fichier .xlsx non supporté », après avoir été
accepté, enregistré et facturé au quota.

Un moteur vert et une extension acceptée ne prouvent RIEN sur le chemin qui
va de l'un à l'autre. C'est ce chemin que cette suite mesure, et rien d'autre.

Le second maillon manquant était dans le traducteur : le relevé d'un classeur a
pour racine `workbook`, or `translate_json` ne connaissait que `pages`,
`slides` et, par défaut, `document`. Un classeur tombait donc dans la branche
DOCX, qui lit `data["document"]` — absent — ne formait aucun lot et s'arrêtait
sur « Aucun texte à traduire ».

CE QUI EST MESURÉ
-----------------
1. Les trois maillons du chemin : extension acceptée -> moteur atteint ->
   traducteur capable de former des lots.
2. La chaîne complète extraction -> traduction (SIMULÉE) -> injection, sur un
   classeur synthétique, avec vérification que le classeur produit est
   RÉELLEMENT ouvrable — pas seulement que la fonction a rendu `True`.
3. Les deux garanties propres au tableur, qu'un branchement maladroit casse en
   premier : un nombre pur n'est jamais traduit, et une cellule à deux graisses
   ne perd pas la moitié de son contenu.

AUCUN APPEL AU MODÈLE : la traduction est simulée en préfixant chaque segment
balisé. On teste le CHEMIN, pas la qualité de la traduction — et un test qui
dépend du réseau ne se lance pas.

    backend/venv/Scripts/python.exe backend/tests/test_xlsx_branchement.py
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile

import racine  # noqa: F401  -- met backend/ sur le chemin

import engines                                              # noqa: E402
from test_xlsx_squelette import construire                  # noqa: E402

_checks: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    _checks.append((bool(cond), label))
    print(f"  {'OK  ' if cond else 'ECHEC'} {label}"
          + (f"   [{detail}]" if detail and not cond else ""))


def _traduire_sur_place(extraction: dict) -> dict:
    """Traduction SIMULÉE : préfixe « EN: » chaque segment, balises intactes.

    Reproduit ce que rend le modèle — le texte change, la structure de balises
    est conservée — sans dépendre du réseau ni d'un quota.
    """
    for el in extraction["workbook"]["elements"]:
        el["translated_text"] = re.sub(
            r"(\[\[(\d+)\]\])(.*?)(\[\[/\2\]\])",
            lambda m: m.group(1) + "EN:" + m.group(3) + m.group(4),
            el["text"])
    return extraction


def main() -> int:
    travail = tempfile.mkdtemp(prefix="precis_test_xlsx_br_")
    src = construire(os.path.join(travail, "src.xlsx"))
    ex_json = os.path.join(travail, "extraction.json")
    tr_json = os.path.join(travail, "translated.json")
    sortie = os.path.join(travail, "sortie.xlsx")

    # ── 1. Les trois maillons du chemin ─────────────────────────────────────
    from app.config import ALLOWED_EXTENSIONS
    check("xlsx" in ALLOWED_EXTENSIONS,
          "CHEMIN  l'extension xlsx est acceptée à l'envoi")
    check(engines.supports("xlsx"),
          "CHEMIN  le moteur xlsx est enregistré")

    # Le maillon qui manquait : le RUNNER route-t-il vers le moteur ? On lit la
    # source plutôt que d'exécuter un job complet (qui exigerait une base, un
    # quota et le réseau) — ce qu'on vérifie est l'existence de la branche.
    import inspect
    from app.services import translation_runner
    source = inspect.getsource(translation_runner.run_translation_job)
    check('ext == "xlsx"' in source,
          "CHEMIN  le runner comporte une branche xlsx",
          "aucune branche : tout classeur echoue en 'non supporte'")
    check(source.count('ext == "xlsx"') >= 2,
          "CHEMIN  il route l'EXTRACTION *et* l'INJECTION",
          f"{source.count('ext == \"xlsx\"')} branche(s) — il en faut 2")

    # L'API aiguille-t-elle un classeur vers le job PROGRESSIF ? Sans cette
    # branche, un .xlsx retomberait sur `run_translation_job`, qui n'a pas de
    # `partial_path` : la traduction marcherait, mais sans aucun aperçu.
    import app.api.translate as api_translate
    src_api = inspect.getsource(api_translate)
    check('elif ext == "xlsx":' in src_api
          and "run_xlsx_progressive_job" in src_api,
          "CHEMIN  l'API aiguille le classeur vers le job PROGRESSIF",
          "sans cette branche : traduction sans apercu")

    # ── 2. Le traducteur sait-il lire un relevé de classeur ? ───────────────
    extraction, _ = engines.new_engine("xlsx").extract_text(src, ex_json)
    check("workbook" in extraction,
          "SCHEMA  le relevé d'un classeur a pour racine `workbook`")

    import engines.translation_ai as tai
    src_tr = inspect.getsource(tai.TranslatorAI.translate_json)
    check('"workbook" in data' in src_tr,
          "SCHEMA  le traducteur reconnaît la racine `workbook`",
          "sans cette branche, un classeur tombe en mode DOCX et ne forme "
          "aucun lot")

    # Le contre-test qui donne son sens au précédent : la branche DOCX, elle,
    # ne verrait RIEN dans ce relevé. C'est ce qui rend la branche nécessaire.
    check(not extraction.get("document", {}).get("elements"),
          "SCHEMA  la branche DOCX ne verrait aucun bloc (d'où la branche)")

    # ── 3. La chaîne complète ───────────────────────────────────────────────
    n_elements = len(extraction["workbook"]["elements"])
    check(n_elements > 0, "CHAINE  l'extraction relève des chaînes",
          f"{n_elements} élément(s)")

    with open(tr_json, "w", encoding="utf-8") as f:
        json.dump(_traduire_sur_place(extraction), f, ensure_ascii=False)

    ok, msg = engines.new_engine("xlsx").inject_translation(
        src, tr_json, sortie)
    check(ok, "CHAINE  l'injection réussit", msg)
    check(os.path.exists(sortie) and os.path.getsize(sortie) > 0,
          "CHAINE  le classeur traduit existe et n'est pas vide")

    # Ouvrable, et non « la fonction a rendu True ». Un ZIP corrompu passerait
    # le contrôle précédent sans passer celui-ci.
    with zipfile.ZipFile(sortie) as z:
        abime = z.testzip()
        parts = set(z.namelist())
        shared = (z.read("xl/sharedStrings.xml").decode("utf-8")
                  if "xl/sharedStrings.xml" in parts else "")
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    check(abime is None, "CHAINE  l'archive produite est intacte", str(abime))
    check("[Content_Types].xml" in parts,
          "CHAINE  [Content_Types].xml est présent (sinon Excel refuse)")

    textes = re.findall(r"<t[^>]*>([^<]*)</t>", shared)
    check(any(t.startswith("EN:") for t in textes),
          "CHAINE  les chaînes partagées portent la traduction", str(textes))
    en_ligne = re.findall(r"<is>.*?<t[^>]*>([^<]*)</t>", sheet)
    check(any(t.startswith("EN:") for t in en_ligne),
          "CHAINE  les chaînes EN LIGNE aussi (classeurs sans magasin)",
          str(en_ligne))

    # ── 4. Les deux garanties du tableur ────────────────────────────────────
    # Un nombre pur reste INTACT : traduit, il cesserait d'être un nombre et
    # toute formule qui le lit se casserait.
    check(any(t == "1234,50" for t in textes),
          "TABLEUR un nombre pur n'est jamais traduit", str(textes))

    # La cellule à DEUX GRAISSES garde ses deux morceaux. C'est le défaut qui
    # avait été trouvé par le classeur synthétique : écarter les morceaux
    # numériques un à un vidait le second nœud, et la cellule perdait la
    # moitié de son contenu SANS erreur.
    check(sum(1 for t in textes if t.startswith("EN:Total")) == 1
          and any("2026" in t for t in textes),
          "TABLEUR la cellule à deux graisses garde ses DEUX morceaux",
          str(textes))

    rates = [lbl for ok_, lbl in _checks if not ok_]
    print(f"\n{len(_checks) - len(rates)}/{len(_checks)} verifications")
    if rates:
        for lbl in rates:
            print(f"  ECHEC {lbl}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
