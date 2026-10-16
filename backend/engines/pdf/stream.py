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
from .version import ENGINE_VERSION
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


def _alleger_images(pages) -> bool:
    """Retire les octets d'image recopiés dans des pages déjà extraites ; seul
    le `xref` reste. Vrai si quelque chose a été retiré."""
    allege = False
    for pg in pages:
        for el in pg.get("elements", []):
            if el.get("type") == "image" and el.get("xref") and (
                    el.get("asset_b64") or "asset_file" in el):
                for k in ("asset_b64", "asset_file", "ext"):
                    el.pop(k, None)
                allege = True
    return allege


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
    # FICHE DE DOCUMENT par langue cible : écrite une fois, gardée avec la
    # reprise pour qu'un job repris garde le même vocabulaire.
    fiches = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                prev = json.load(f)
            for pg in prev.get("pages", []):
                cached_pages[pg.get("page_num")] = pg
            fiches = dict(prev.get("fiches") or {})
        except Exception:
            cached_pages = {}

    def _ecrire_cache():
        try:
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"pages": [cached_pages[k] for k in
                                     sorted(cached_pages)],
                           "fiches": {k: v for k, v in fiches.items() if v}},
                          f, ensure_ascii=False)
            os.replace(tmp, cache_path)
        except Exception:
            pass

    # Images PAR RÉFÉRENCE : le rendu les relit dans l'original (`src_doc`).
    # Un cache écrit avant ce choix embarquait leurs octets en base64 (94 %
    # du fichier) : on les retire une fois, le rendu reste identique puisque
    # l'original est le même (le dossier est adressé par son empreinte).
    if _alleger_images(cached_pages.values()) and cache_path:
        _ecrire_cache()

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
    traduites = {}          # page -> page_data : matière de la cohérence
    nouvelles = 0           # pages traduites PAR CE passage (hors reprise)
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
            if (has_translation and page_data.get("moteur") != ENGINE_VERSION
                    and page_data.get("amelioration") != ENGINE_VERSION):
                # Page découpée par une AUTRE version du moteur : elle rejouerait
                # les décisions d'avant le correctif. On la redécoupe ; la
                # traduction suit si le texte est retrouvé à l'identique, sinon
                # la page ancienne reste (aucun texte changé sans accord).
                _emit(on_event, {"type": "page", "page": human,
                                 "status": "extracting",
                                 "done": human - 1, "total": total})
                neuve = eng.extract_page_data(
                    src, page_num, src[page_num], images_par_reference=True)
                if debug or translate.reattacher_traductions(neuve, page_data):
                    page_data = neuve
                else:
                    # Le texte changerait : la page garde sa version, et la
                    # mise à jour reste PROPOSÉE (pas retentée à chaque visite).
                    page_data["amelioration"] = ENGINE_VERSION
                if cache_path:
                    cached_pages[human] = page_data
                    _ecrire_cache()
            if not has_translation:
                _emit(on_event, {"type": "page", "page": human,
                                 "status": "extracting",
                                 "done": human - 1, "total": total})
                page_data = eng.extract_page_data(
                    src, page_num, src[page_num], images_par_reference=True)

                if not debug:
                    _emit(on_event, {"type": "page", "page": human,
                                     "status": "translating",
                                     "done": human - 1, "total": total})
                    one = {"pages": [page_data], "fonts": {}}
                    cle_fiche = str(target_lang).lower()
                    if cle_fiche not in fiches:
                        from engines.translation_ai import TranslatorAI
                        try:
                            matiere = translate.matiere_fiche_pdf(src)
                        except Exception:       # page illisible : sans fiche
                            matiere = ([], [])
                        fiches[cle_fiche] = TranslatorAI().fiche_document(
                            *matiere, target_lang)
                    translate.translate_extraction(one, target_lang=target_lang,
                                                   doc_memory=doc_memory,
                                                   fiche=fiches[cle_fiche])
                    nouvelles += 1
                    # Recalcule l'expansion puis retraduction compacte de la page
                    # (paragraphes qui ne tiendraient pas sans compression visible).
                    try:
                        translate.retranslate_overflows(
                            one, eng, target_lang=target_lang,
                            fiche=fiches[cle_fiche])
                    except Exception:
                        pass
                # Reprise : la page traduite rejoint le cache immédiatement.
                if cache_path:
                    cached_pages[human] = page_data
                    _ecrire_cache()

            if not debug:
                traduites[human] = page_data
            _emit(on_event, {"type": "page", "page": human,
                             "status": "rendering",
                             "done": human - 1, "total": total})
            eng.render_page_into(out, page_data, draw_borders=debug,
                                 translated=not debug, src_doc=src)
            _save_partial(out, partial_path)
            done_pages.append(human)
            _emit(on_event, {"type": "page", "page": human, "status": "done",
                             "done": human, "total": total})

        # COHÉRENCE DE DOCUMENT : chaque page est traduite sans voir les
        # suivantes ; un terme laissé en langue source ici, traduit ailleurs,
        # ne se voit qu'une fois tout le document lu. Seules les pages
        # touchées sont reconstruites (et ré-annoncées : l'aperçu se recharge).
        # Jamais sur une pure reprise : rien de neuf à confronter, et les
        # cognats légitimes seraient redemandés à chaque ouverture.
        if nouvelles and not debug:
            fiche = fiches.get(str(target_lang).lower(), "")
            try:
                touchees = translate.coherence_document(
                    [traduites[h] for h in sorted(traduites)],
                    target_lang=target_lang, fiche=fiche)
            except Exception:
                touchees = set()
            for human in sorted(touchees):
                page_data = traduites[human]
                try:
                    translate.retranslate_overflows(
                        {"pages": [page_data], "fonts": {}}, eng,
                        target_lang=target_lang, fiche=fiche)
                except Exception:
                    pass
                neuve = fitz.open()
                try:
                    eng.render_page_into(neuve, page_data, translated=True,
                                         src_doc=src)
                    out.delete_page(human - 1)
                    out.insert_pdf(neuve, start_at=human - 1)
                finally:
                    neuve.close()
            if touchees:
                if cache_path:
                    _ecrire_cache()
                _save_partial(out, partial_path)
                for human in sorted(touchees):
                    _emit(on_event, {"type": "page", "page": human,
                                     "status": "done",
                                     "done": total, "total": total})

        out.save(output_path, garbage=4, deflate=True, clean=True)
    finally:
        out.close()
        src.close()
    return output_path
