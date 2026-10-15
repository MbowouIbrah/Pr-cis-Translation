"""
pdf_engine_v2.stream — Traduction PROGRESSIVE page par page.

Contrairement au pipeline « tout le document puis rendu », chaque page est
extraite, traduite puis rendue AVANT de passer à la suivante :

    pour chaque page :  extract_page_data → translate (+ retraduction compacte
                        de la page) → render_page_into → PDF PARTIEL réécrit

Le PDF partiel (`partial_path`) contient à tout instant les pages 1..k déjà
traitées — le client l'affiche au fil de l'eau (la page k+1 est « en cours »).
Les pages hors sélection sont COPIÉES de l'original (fidélité parfaite, aucune
traduction) pour que la pagination du partiel suive celle du document.

`on_event(event: dict)` reçoit :
    {"type": "start", "total": N}
    {"type": "page",  "page": i, "status": "extracting"|"translating"|
                       "rendering"|"done"|"copied", "done": k, "total": N}
    (les erreurs remontent en exception à l'appelant)
"""

import json
import os

import fitz

from .engine import PDFObjectEngine
from . import translate


def _emit(on_event, payload):
    if on_event:
        try:
            on_event(payload)
        except Exception:
            pass


def _save_partial(out_doc, partial_path):
    """Réécrit le PDF partiel de façon ATOMIQUE (tmp + rename) : un client qui
    le télécharge pendant l'écriture ne voit jamais un fichier tronqué."""
    if not partial_path:
        return
    tmp = partial_path + ".tmp"
    out_doc.save(tmp, garbage=2, deflate=True)
    os.replace(tmp, partial_path)


def translate_pdf_progressive(pdf_path, output_path, target_lang="fr",
                              pages=None, partial_path=None, on_event=None,
                              debug=False, cache_path=None, engine=None):
    """Traduit `pdf_path` page par page vers `output_path`.

    pages        : ensemble de numéros 1-basés à traduire (None = toutes) ; les
                   autres pages sont copiées telles quelles de l'original.
    partial_path : PDF partiel réécrit après CHAQUE page (affichage progressif).
    debug        : mode structure — aucune traduction, bordures de debug.
    cache_path   : JSON de reprise — les pages déjà traduites (tr_tagged) y sont
                   relues au lieu d'être re-payées, et chaque page traduite y est
                   ajoutée aussitôt (un job interrompu reprend où il en était).
    """
    eng = engine or PDFObjectEngine()
    lang = "fr_FR" if str(target_lang).lower().startswith("fr") else \
        f"{str(target_lang).lower()[:2]}_{str(target_lang).upper()[:2]}"
    eng.reflow_lang = lang

    # Cache de reprise : pages déjà traduites lors d'un job précédent.
    cached_pages = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                prev = json.load(f)
            for pg in prev.get("pages", []):
                cached_pages[pg.get("page_num")] = pg
        except Exception:
            cached_pages = {}

    # MÉMOIRE DE DOCUMENT (cohérence inter-pages) : un texte court déjà traduit
    # dans CE document est resservi tel quel par les pages suivantes. Semée
    # depuis le cache de reprise pour qu'un job repris garde les choix du
    # premier passage.
    doc_memory = {}
    from . import tagging as _tagging
    for pg in cached_pages.values():
        for el in pg.get("elements", []):
            if el.get("type") != "paragraph":
                continue
            tr = el.get("tr_tagged")
            if not tr:
                continue
            try:
                tagged, _m = _tagging.tag_paragraph(el)
            except Exception:
                continue
            cle = translate._plain_key(tagged)
            if (cle and tr != tagged
                    and len(cle) <= translate._MEMO_MAX_LEN
                    and cle not in doc_memory):
                doc_memory[cle] = (tagged, tr)

    src = fitz.open(pdf_path)
    out = fitz.open()
    done_pages = []
    try:
        total = len(src)
        _emit(on_event, {"type": "start", "total": total})

        # Polices embarquées : extraites UNE fois (dictionnaire de document).
        data = {"pages": [], "fonts": eng._extract_fonts(src)}
        eng._build_fonts(data)

        for page_num in range(total):
            human = page_num + 1
            if pages is not None and human not in pages:
                # Hors sélection : copie fidèle de la page d'origine.
                out.insert_pdf(src, from_page=page_num, to_page=page_num)
                _save_partial(out, partial_path)
                _emit(on_event, {"type": "page", "page": human,
                                 "status": "copied",
                                 "done": human, "total": total})
                continue

            page_data = cached_pages.get(human)
            # En mode structure, l'EXTRACTION sauvegardée suffit : elle n'a pas
            # de `tr_tagged` à attendre. Sans ce cas, chaque réouverture
            # ré-extrayait toutes les pages et réécrivait le cache.
            has_translation = bool(page_data) and (debug or any(
                e.get("tr_tagged") for e in page_data.get("elements", [])
                if e.get("type") == "paragraph"))
            if not has_translation:
                _emit(on_event, {"type": "page", "page": human,
                                 "status": "extracting",
                                 "done": human - 1, "total": total})
                page_data = eng.extract_page_data(src, page_num, src[page_num])

                if not debug:
                    _emit(on_event, {"type": "page", "page": human,
                                     "status": "translating",
                                     "done": human - 1, "total": total})
                    one = {"pages": [page_data], "fonts": {}}
                    translate.translate_extraction(one, target_lang=target_lang,
                                                   doc_memory=doc_memory)
                    # Recalcule l'expansion puis retraduction compacte de la page
                    # (paragraphes qui ne tiendraient pas sans compression visible).
                    try:
                        translate.retranslate_overflows(one, eng,
                                                        target_lang=target_lang)
                    except Exception:
                        pass
                # Reprise : la page traduite rejoint le cache immédiatement.
                if cache_path:
                    cached_pages[human] = page_data
                    try:
                        tmp = cache_path + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump({"pages": [cached_pages[k] for k in
                                                 sorted(cached_pages)]},
                                      f, ensure_ascii=False)
                        os.replace(tmp, cache_path)
                    except Exception:
                        pass

            _emit(on_event, {"type": "page", "page": human,
                             "status": "rendering",
                             "done": human - 1, "total": total})
            eng.render_page_into(out, page_data, draw_borders=debug,
                                 translated=not debug)
            _save_partial(out, partial_path)
            done_pages.append(human)
            _emit(on_event, {"type": "page", "page": human, "status": "done",
                             "done": human, "total": total})

        out.save(output_path, garbage=4, deflate=True, clean=True)
    finally:
        out.close()
        src.close()
    return output_path
