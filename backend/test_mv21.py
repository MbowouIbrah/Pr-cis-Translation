"""
Test du moteur PDF sur mv21.pdf : extraction → reconstitution (identité).
Aucune traduction IA — le texte est réinjecté tel quel pour valider
le pipeline extraction/injection.
"""
import os, sys, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pdf_translator_engine import PDFTranslatorEngine

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PATH  = os.path.join(SCRIPT_DIR, "tests files", "mv21.pdf")
OUT_DIR   = os.path.join(SCRIPT_DIR, "tests files")
EXTRACT   = os.path.join(OUT_DIR, "mv21_extract.json")
TRANSLATED = os.path.join(OUT_DIR, "mv21_translated.json")
OUT_PDF   = os.path.join(OUT_DIR, "mv21_RECONSTITUTION.pdf")

def progress(msg):
    print(f"  {msg}")

print("=" * 60)
print("PDF Engine — Reconstitution de mv21.pdf")
print("=" * 60)

t0 = time.time()

# ── Étape 1 : Extraction ──────────────────────────────────────────────
print("\n[1/3] Extraction du texte...")
engine = PDFTranslatorEngine()
extraction, _ = engine.extract_text(
    PDF_PATH, output_json=EXTRACT, progress_callback=progress)

if not extraction:
    print("\n❌ Extraction échouée.")
    sys.exit(1)

n_pages = extraction.get("num_pages", "?")
n_blocks = sum(len(p.get("text_blocks", [])) for p in extraction.get("pages", []))
print(f"  ✅ {n_pages} pages, {n_blocks} blocs extraits → {EXTRACT}")

# ── Étape 2 : Identité (pas de traduction) ────────────────────────────
print("\n[2/3] Préparation du JSON traduit (identité)...")
for pg in extraction["pages"]:
    for b in pg.get("text_blocks", []):
        b["translated_text"] = b.get("text", "")

with open(TRANSLATED, "w", encoding="utf-8") as f:
    json.dump(extraction, f, ensure_ascii=False, indent=2)
print(f"  ✅ {TRANSLATED}")

# ── Étape 3 : Injection / Reconstitution ──────────────────────────────
print("\n[3/3] Génération du PDF reconstitué...")
engine2 = PDFTranslatorEngine()
ok, msg = engine2.inject_translation(
    PDF_PATH, TRANSLATED, OUT_PDF, progress_callback=progress)

elapsed = time.time() - t0
if ok:
    size_kb = os.path.getsize(OUT_PDF) / 1024
    print(f"\n✅ Reconstitution réussie ! ({elapsed:.1f}s)")
    print(f"   Fichier : {OUT_PDF} ({size_kb:.0f} Ko)")
else:
    print(f"\n❌ Échec : {msg}")

print("=" * 60)
