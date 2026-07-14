"""
Étape 0 — Identification de l'alignement (métadonnée + overlay visuel).

Extrait CV + 20 pages du Handbook, calcule l'alignement de chaque paragraphe
(align : 0=gauche 1=centre 2=droite 3=justifié + align_conf), puis génère un
PDF overlay où chaque paragraphe est cerné d'un contour COLORÉ par alignement :
    gauche=VERT   centre=BLEU   droite=ORANGE   justifié=VIOLET
Les tirets gris = cadre [L,R] inféré de la colonne (référence du mono-ligne).

Usage :
    python test_alignment.py            # CV + Handbook
    python test_alignment.py cv
    python test_alignment.py handbook

Pré-requis : DEEPSEEK_API_KEY dans backend/.env (regroupement paragraphes).
"""
import os
import sys
import json
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from pdf_translator_engine import PDFTranslatorEngine

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(SCRIPT_DIR, "tests files")
CV_PATH = os.path.join(
    SCRIPT_DIR, "..", "frontend", "public", "CV_Mbowou_Ibrahim_Pigier.pdf")
HANDBOOK_PATH = os.path.join(TESTS_DIR, "The Data Science Handbook.pdf")
HANDBOOK_PAGES = 20

ALIGN_NAME = {0: "gauche", 1: "centre", 2: "droite", 3: "justifié"}


def _engine():
    eng = PDFTranslatorEngine()
    key = os.getenv("DEEPSEEK_API_KEY")
    if key:
        eng.configure_llm(
            key,
            base_url=os.getenv("DEEPSEEK_API_URL_BASE", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL_FAST", "deepseek-chat"))
        print("[mode] Regroupement IA (DeepSeek) actif.")
    else:
        print("[mode] DEEPSEEK_API_KEY absente → repli géométrique.")
    return eng


def _report(extraction):
    """Distribution des alignements détectés, par page + global."""
    glob = Counter()
    for pg in extraction["pages"]:
        seen = {}   # paragraph_key -> align (un compte par paragraphe)
        for b in pg.get("text_blocks", []):
            if abs(b.get("rotation", 0.0)) > 1.0 or not (b.get("text") or "").strip():
                continue
            k = b.get("paragraph_key", b["id"])
            if k not in seen:
                seen[k] = b.get("align", 0)
        c = Counter(seen.values())
        glob.update(c)
        desc = ", ".join(f"{ALIGN_NAME[a]}={c[a]}" for a in sorted(c))
        print(f"  Page {pg['page_num']:>2} : {len(seen):>3} para → {desc}")
    print("  " + "-" * 50)
    print("  GLOBAL : " + ", ".join(
        f"{ALIGN_NAME[a]}={glob[a]}" for a in sorted(glob)))


def run(name, pdf_path, prefix, pages=None):
    if not os.path.exists(pdf_path):
        print(f"✗ {name} introuvable : {pdf_path}")
        return
    print("\n" + "=" * 70)
    print(name)
    print("=" * 70)

    eng = _engine()
    extract_json = os.path.join(TESTS_DIR, f"{prefix}_extract.json")
    extraction, _ = eng.extract_text(
        pdf_path, output_json=extract_json, progress_callback=print, pages=pages)
    if not extraction:
        print("✗ Extraction échouée."); return

    print("\n— Alignements détectés —")
    _report(extraction)

    # Identité + overlay coloré par alignement.
    for pg in extraction["pages"]:
        for b in pg.get("text_blocks", []):
            b["translated_text"] = b.get("text", "")
    translated_json = os.path.join(TESTS_DIR, f"{prefix}_translated.json")
    with open(translated_json, "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False, indent=2)

    dbg = PDFTranslatorEngine()
    dbg.debug_draw_borders = True
    dbg.debug_draw_alignment = True
    dbg.debug_para_attr = "paragraph_key"
    out_pdf = os.path.join(TESTS_DIR, f"{prefix}_ALIGN.pdf")
    ok, msg = dbg.inject_translation(
        pdf_path, translated_json, out_pdf, progress_callback=lambda m: None)
    print(f"\n{'✓' if ok else '✗'} overlay : {out_pdf if ok else msg}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    which = args[0].lower() if args else "all"
    if which in ("cv", "all"):
        run("CV", CV_PATH, "_align_cv")
    if which in ("handbook", "all"):
        run(f"Handbook ({HANDBOOK_PAGES} pages)", HANDBOOK_PATH,
            "_align_handbook", pages=set(range(1, HANDBOOK_PAGES + 1)))
    print("\nLégende : VERT=gauche  BLEU=centre  ORANGE=droite  VIOLET=justifié")
    print("✓ Terminé.")
