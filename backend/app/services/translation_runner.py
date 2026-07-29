"""Execution d'une traduction, de bout en bout.

TROIS CHEMINS, UN SEUL PRINCIPE
-------------------------------
  * `run_pdf_v2_job`        -- PDF : moteur v2 PROGRESSIF, page par page. Le
    PDF partiel grandit au fil des pages et le client l'affiche sans attendre
    la fin.
  * `run_pptx_progressive_job` -- PPTX : meme idee, diapositive par
    diapositive, plus une passe de coherence terminologique sur le document
    entier.
  * `run_translation_job`   -- DOCX et repli : extraction, traduction,
    reinjection en un bloc.

Tous tournent dans un THREAD worker, jamais dans la boucle asyncio : leur
travail est bloquant (reseau, LibreOffice, rendu) et le tenir dans la boucle
figerait toute l'application, flux SSE compris.

Aucun d'eux ne touche a FastAPI. Ils recoivent des chemins et un identifiant de
job, et publient leur avancement par `jobs.emit`. C'est ce qui les rend
appelables depuis un script ou un test sans monter de serveur.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading


import engines
from app.config import logger, note
from app.services import render_cache
from engines import runtags
from app.services.jobs import jobs
from app.services.office import SOFFICE_PATH, convert_to_pdf_bytes
from app.services.progressive_preview import (ProgressivePreview,
                                              ecrire_atomiquement)
from engines.translation_ai import TranslatorAI
from engines.pdf import stream as pdf_v2_stream

# Le moteur de traduction par IA : une instance suffit, elle est sans etat
# mutable partage (le client OpenAI est thread-safe).
try:
    ai_translator = TranslatorAI()
    ai_active = True
except Exception as _e:                          # pragma: no cover
    ai_translator = None
    ai_active = False
    note(logging.WARNING, f"IA non initialisée : {_e}")

pptx_available = engines.supports("pptx")

# Le registre SIGNALE un format manquant, il ne le journalise pas : c'est ici,
# dans la couche applicative, qu'on sait a qui le dire.
for _fmt, _raison in engines.INDISPONIBLES.items():
    note(logging.WARNING, f"{_fmt.upper()} indisponible : {_raison}")


def run_translation_job(
    job_id: str, file_bytes: bytes, filename: str, ext: str,
    target_lang: str, format_opts: dict,
    job_dir: str, lang_dir: str, original_path: str,
    extraction_path: str, translated_path: str,
    output_path: str, output_filename: str,
    model: str, max_tokens: int, pages_set=None, debug: bool = False,
):
    """Exécute toute la pipeline dans un thread de fond et émet des events SSE.

    `translated_path` (translated.json) est la traduction PERSISTANTE ; le rendu
    `output_path` est transitoire. Un document déjà traduit relit ce JSON et se
    contente de ré-injecter (aucun appel DeepSeek)."""
    # Moteur PPTX PROPRE à ce job : son dossier temporaire ne doit être partagé
    # avec aucun autre traitement (cf. PPTXTranslatorEngine).
    pptx_eng = engines.new_engine("pptx") if ext == "pptx" else None
    try:
        jobs.emit(job_id, "progress", {"step": "start", "message": "Démarrage du job...", "page": 0, "total": None})

        # 2. Sauvegarde de l'original
        if not os.path.exists(original_path):
            with open(original_path, "wb") as f:
                f.write(file_bytes)

        # 3. Extraction
        if not os.path.exists(extraction_path):
            cb_extract = jobs.progress_callback(job_id, "extract")
            jobs.emit(job_id, "progress", {"step": "extract", "message": "Extraction du texte...", "page": 0, "total": None})
            extraction = None
            if ext == "docx":
                filters = {"paragraphs": True, "tables": True, "headers_footers": True, "text_boxes": True, "smartarts": True}
                extraction, _ = engines.new_engine("docx").extract_text(original_path, extraction_path, filters=filters, progress_callback=cb_extract)
            elif ext == "pptx" and pptx_eng:
                filters = {"shapes": True, "smartarts": True, "tables": True, "connectors": True}
                extraction, _ = pptx_eng.extract_text(original_path, extraction_path, filters=filters, progress_callback=cb_extract, pages=pages_set)
            elif ext == "xlsx":
                # Le moteur XLSX était ENREGISTRÉ et l'extension ACCEPTÉE à
                # l'envoi, mais aucune branche ne l'appelait : tout classeur
                # échouait ici sur « Type de fichier .xlsx non supporté ». Le
                # moteur passait ses 29 contrôles sans jamais servir.
                #
                # Pas de `filters` : un classeur n'a pas de sections à cocher
                # (en-têtes, zones de texte, SmartArts). Ce que le moteur sait
                # lire, il le lit ; ce qu'il ne sait pas encore lire est déclaré
                # dans `workbook.non_traite` (cf. `_PARTIES`).
                extraction, _ = engines.new_engine("xlsx").extract_text(
                    original_path, extraction_path,
                    progress_callback=cb_extract)
            else:
                raise ValueError(f"Type de fichier .{ext} non supporté.")
            if not extraction:
                if os.path.exists(extraction_path):
                    os.remove(extraction_path)
                raise ValueError("Échec de l'extraction du texte.")
        else:
            jobs.emit(job_id, "progress", {"step": "extract", "message": "Extraction : cache utilisé.", "page": 0, "total": None})

        # 4. Traduction IA — ou IDENTITÉ en mode structure/debug
        if debug:
            # Aucune traduction : translated_text = texte d'origine, comme
            # test_extract_inject.py. Le rendu (étape 5) ajoute les bordures.
            if not os.path.exists(translated_path):
                jobs.emit(job_id, "progress", {"step": "translate", "message": "Mode structure : copie identité (sans traduction).", "page": 0, "total": None})
                with open(extraction_path, "r", encoding="utf-8") as f:
                    _ext = json.load(f)
                for _pg in _ext.get("pages", []):
                    for _b in _pg.get("text_blocks", []):
                        _b["translated_text"] = _b.get("text", "")
                with open(translated_path, "w", encoding="utf-8") as f:
                    json.dump(_ext, f, ensure_ascii=False)
            else:
                jobs.emit(job_id, "progress", {"step": "translate", "message": "Mode structure : cache utilisé.", "page": 0, "total": None})
        elif not os.path.exists(translated_path):
            if not ai_active:
                raise ValueError("Le traducteur IA n'est pas disponible.")
            cb_translate = jobs.progress_callback(job_id, "translate")
            jobs.emit(job_id, "progress", {"step": "translate", "message": "Traduction IA en cours...", "page": 0, "total": None})
            success, result = ai_translator.translate_json(extraction_path, target_lang=target_lang, progress_callback=cb_translate, model=model, max_tokens=max_tokens)
            if not success:
                raise ValueError(f"Traduction échouée : {result}")
            if os.path.exists(result) and os.path.abspath(result) != os.path.abspath(translated_path):
                shutil.move(result, translated_path)
        else:
            jobs.emit(job_id, "progress", {"step": "translate", "message": "Traduction : cache utilisé.", "page": 0, "total": None})

        # 5. Injection / génération (DOCX/PPTX — le PDF passe par le moteur v2).
        jobs.emit(job_id, "progress", {"step": "inject", "message": "Génération du document traduit...", "page": 0, "total": None})
        if ext == "docx":
            inj_ok, inj_msg = engines.new_engine("docx").inject_translation(original_path, translated_path, output_path, format_options=format_opts)
        elif ext == "pptx" and pptx_eng:
            inj_ok, inj_msg = pptx_eng.inject_translation(original_path, translated_path, output_path, format_options=format_opts)
            if inj_ok:
                pptm_auto_path = output_path[:-5] + "_autorefresh.pptm" if output_path.endswith(".pptx") else output_path + "_autorefresh.pptm"
                pptx_eng.generate_autorefresh_pptm(output_path, pptm_auto_path)
        elif ext == "xlsx":
            # Instance NEUVE, comme partout ailleurs : tout l'état du moteur
            # tient dans son dossier temporaire, et deux opérations qui le
            # partagent écrivent dans les mêmes fichiers (cf. engines/CONTEXTE).
            # Ce n'est PAS le moteur qui a servi à l'extraction : celui-là a
            # déjà nettoyé son dossier.
            inj_ok, inj_msg = engines.new_engine("xlsx").inject_translation(
                original_path, translated_path, output_path,
                format_options=format_opts)
        else:
            raise ValueError("Type de fichier non supporté pour la génération.")

        if not inj_ok:
            if os.path.exists(output_path):
                os.remove(output_path)
            raise ValueError(f"Injection échouée : {inj_msg}")

        if not os.path.exists(output_path):
            raise ValueError("Le fichier traduit est introuvable après génération.")

        jobs.done(job_id, output_path, output_filename,
                  translation_path=translated_path)

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        jobs.error(job_id, "La traduction a rencontré une erreur. Réessayez ou contactez le support.")


def _elements_de(slide_data: dict):
    """Tous les fragments traduisibles d'une slide, à plat.

    Le même parcours était recopié à chaque usage (construction de la carte de
    réinjection, mode debug, contrôles) : cinq copies à tenir d'accord, et une
    famille oubliée quelque part passait inaperçue.
    """
    yield from slide_data.get("text_elements", [])
    for groupe in ("diagram_elements", "chart_elements", "layout_elements"):
        for bloc in slide_data.get(groupe, []):
            yield from bloc.get("text_elements", [])
    yield from slide_data.get("excel_elements", [])


# ── Cohérence terminologique du DOCUMENT ─────────────────────────────────────
#
# Un même mot source doit recevoir la même traduction d'un bout à l'autre d'un
# document. Le modèle traduit slide par slide et n'a aucune mémoire d'une slide
# à l'autre : il a rendu « Gerbeur » par « Stacker » quinze fois, et l'a laissé
# en français une seizième (mesuré, slide 17). Aucune règle de FORME ne peut
# l'attraper — le fragment est parfaitement bien balisé.
#
# La preuve vient du document lui-même (cf. runtags.termes_incoherents), jamais
# d'une liste de termes. Reste que les COGNATS (« motivation », « progression »)
# y ressemblent à s'y méprendre : la consigne ci-dessous est donc écrite pour
# qu'un cognat reste INCHANGÉ sans dommage, et pour que la reprise ne touche
# QUE le terme — pas le reste d'une phrase déjà correcte.

_CONSIGNE_COHERENCE = (
    "COHÉRENCE DU DOCUMENT. Dans ta traduction ci-dessous, ces termes sont "
    "restés identiques à la source : {termes}. Ailleurs dans le MÊME document, "
    "tu les as traduits. Reprends ta traduction en ne changeant QUE ce qui "
    "concerne ces termes — garde le reste MOT POUR MOT.\n"
    "Si l'un d'eux s'écrit de la même façon dans la langue cible, ou s'il "
    "s'agit d'un nom propre, d'une marque ou d'un sigle, LAISSE-LE TEL QUEL : "
    "c'est légitime, et le changer serait une faute.\n"
    "Ta traduction précédente : {precedente}"
)


def _coherence_document(slides_traduites: dict, pptx_eng, target_lang: str,
                        progress_cb=None) -> int:
    """Reprend les fragments qui laissent en langue source un terme que le
    document traduit ailleurs. Renvoie le nombre de fragments repris.

    Best-effort : toute erreur laisse la traduction en l'état. Une passe de
    confort ne doit jamais faire échouer un travail déjà abouti.
    """
    fragments = [(sn, el) for sn, sd in slides_traduites.items()
                 for el in _elements_de(sd) if el.get("translated_text")]
    if not fragments:
        return 0

    incoherents = runtags.termes_incoherents(
        (el["text"], el["translated_text"]) for _sn, el in fragments)
    if not incoherents:
        return 0

    a_reprendre, slides_touchees = [], set()
    for sn, el in fragments:
        termes = runtags.termes_a_reprendre(el["text"], el["translated_text"],
                                            incoherents)
        if not termes:
            continue
        el["consigne"] = _CONSIGNE_COHERENCE.format(
            termes=", ".join(termes), precedente=el["translated_text"])
        a_reprendre.append(el)
        slides_touchees.add(sn)
    if not a_reprendre:
        return 0

    if progress_cb:
        progress_cb(f"cohérence : {len(a_reprendre)} fragment(s) repris "
                    f"({len(incoherents)} terme(s) concerné(s)).")
    # `_translate_batch` écrit `translated_text` DANS ces dictionnaires, qui
    # sont ceux de `slides_traduites` : la correction se propage donc au JSON
    # persistant sans qu'on ait à le reconstruire.
    ai_translator._translate_batch(a_reprendre, target_lang, progress_cb,
                                   passes=0)
    for el in a_reprendre:
        el.pop("consigne", None)

    # Réinjection des seules slides touchées. L'injection écrit par
    # identifiant de paragraphe : la repasser sur un XML déjà injecté remplace
    # simplement le texte des runs, sans avoir besoin de la source.
    for sn in sorted(slides_touchees):
        sd = slides_traduites.get(sn)
        if sd:
            pptx_eng.inject_slide(sn, {
                el["id"]: (el.get("translated_text") or el["text"])
                for el in _elements_de(sd)})
    return len(a_reprendre)


def run_pptx_progressive_job(
    job_id: str, file_bytes: bytes, original_path: str,
    output_path: str, output_filename: str, partial_path: str,
    translation_path: str, target_lang: str,
    pages_set=None, debug: bool = False, is_admin: bool = False,
):
    """Pipeline PPTX PROGRESSIF : extraction, traduction et réinjection SLIDE
    PAR SLIDE. Le PPTX partiel est construit puis converti en PDF pour être
    servi via /api/translate/partial/{job_id}. Événements SSE comme le PDF v2.

    Optimisation ADMIN : si is_admin=True, traduit 5 slides en parallèle (5 workers)
    pour diviser le temps de traitement PPTX par 5."""
    # Moteur PROPRE à ce job. Il était partagé avec les aperçus : le
    # `_cleanup_temp()` d'un aperçu ouvert pendant la traduction supprimait le
    # dossier temporaire SOUS le job (cf. PPTXTranslatorEngine).
    pptx_eng = engines.new_engine("pptx")
    try:
        if not pptx_eng:
            raise ValueError("Moteur PPTX indisponible.")

        # ── Sauvegarde de l'original ──────────────────────────────────────
        if not os.path.exists(original_path):
            with open(original_path, "wb") as f:
                f.write(file_bytes)

        # ── Décompression ─────────────────────────────────────────────────
        jobs.emit(job_id, "progress", {"step": "extract", "message": "Décompression du PPTX...", "page": 0, "total": None})
        pptx_eng._extract_zip(original_path)
        total_slides = pptx_eng.slide_count()

        if not total_slides:
            raise ValueError("Aucune slide trouvée dans le PPTX.")

        # Appliquer la sélection de pages
        if pages_set:
            slides_to_process = sorted(s for s in pages_set if 1 <= s <= total_slides)
            if not slides_to_process:
                slides_to_process = [1]
        else:
            slides_to_process = list(range(1, total_slides + 1))

        total = len(slides_to_process)
        jobs.emit(job_id, "start", {"total": total})

        # Enregistrer le partial_path dans le job pour que /partial le trouve.
        jobs.set(job_id, partial_path=partial_path)

        # ── Filtres d'extraction ──────────────────────────────────────────
        filters = {"shapes": True, "smartarts": True, "tables": True, "connectors": True}

        # ── Traduction PROGRESSIVE slide par slide ─────────────────────────
        done = 0
        done_lock = threading.Lock()

        # PARALLÉLISME PAR CYCLE COMPLET (et non plus par étape).
        # Chaque worker mène UNE diapositive de bout en bout — extraction →
        # traduction → injection → conversion → affichage — puis passe à la
        # suivante. La TRADUCTION (réseau) se recouvre donc entre pages : c'est
        # le vrai gain en mode IA. En revanche la section d'AFFICHAGE (injection
        # + conversion + greffe) est SÉRIALISÉE par `_display_lock` : le dossier
        # temporaire, le PDF partiel et LibreOffice sont partagés, et deux
        # greffes concurrentes corrompraient le partiel (c'est la raison pour
        # laquelle l'ancien mode parallèle sautait l'aperçu progressif — ici on
        # le garde, en protégeant la seule zone qui ne supporte pas la
        # concurrence). L'aperçu reste donc page par page, mais alimenté par
        # plusieurs traductions menées de front.
        _WORKERS = 4
        _display_lock = threading.Lock()
        _ = is_admin           # signature conservée ; parallélisme non lié au plan
        # Slide -> son extraction AVEC sa traduction, retenue avant injection.
        # C'est ce qui devient `translated.json` (cf. plus bas).
        slides_traduites: dict[int, dict] = {}

        # ── Aperçu PROGRESSIF : le PDF se remplit page par page ───────────
        # Le flux est celui du moteur PDF, et il tient en deux temps :
        #
        #   1. SOCLE — le document d'ORIGINE, converti une fois en PDF. Il est
        #      affichable immédiatement : l'utilisateur voit tout le document,
        #      en langue source, dès la première seconde.
        #   2. GREFFE — chaque diapositive traduite est convertie SEULE, et sa
        #      page remplace la page correspondante du socle. Le document se
        #      traduit sous les yeux, page après page.
        #
        # POURQUOI C'EST POSSIBLE MAINTENANT, ET PAS AVANT
        # -------------------------------------------------
        # La campagne précédente avait mesuré 11 s par diapositive isolée et
        # conclu — j'avais conclu — que le page-par-page coûtait 288 s contre
        # 17 s pour le deck entier. La conclusion était juste, la CAUSE était
        # fausse : ce n'était pas le poids des médias, c'était le PROFIL
        # LibreOffice, reconstruit à chaque appel.
        #
        # MESURÉ à nouveau, profil partagé (cf. `engines/office.py`) :
        #     profil neuf ...................... 6,99 s
        #     profil réutilisé ................. 2,66 s
        #     UNE diapositive, profil chaud .... 1,58 s   ← 7× plus rapide
        #
        # Mesuré aussi, et faux : élaguer les médias d'une diapositive isolée
        # (12,7 Mo → 0,5 Mo) ne gagne RIEN. LibreOffice ne lit pas les images
        # que rien ne référence. Ne pas réécrire cette optimisation-là.
        #
        # LE PRIX, ET CE QUI NOUS EN PROTÈGE
        # -----------------------------------
        # 26 × 1,58 s = 41 s de calcul contre 2,66 s pour le deck entier :
        # quinze fois plus de CPU par document. Invisible pour un utilisateur,
        # ruineux pour cent. D'où la COALESCENCE : un seul convertisseur par
        # job, qui prend à chaque tour TOUTES les diapositives en attente et les
        # convertit d'un coup. Seul, l'utilisateur obtient ses pages une par
        # une ; sous charge, la conversion prend du retard, les pages
        # s'accumulent et sont traitées par lots — le système glisse tout seul
        # vers le régime économe, sans seuil à deviner.
        #
        # Réservé au mode SÉQUENTIEL : en parallèle (admin), le dossier
        # temporaire est muté par plusieurs slides à la fois et un partiel lu au
        # vol serait incohérent. L'admin échange l'aperçu progressif contre la
        # vitesse (il verra le partiel final, comme avant).
        # Diapositives dont l'aperçu Excel incorporé a été refait EN LIGNE : la
        # passe finale n'y reviendra pas.
        _ole_faits: set[int] = set()

        _apercu = ProgressivePreview(
            extraire=lambda chemin, pages: pptx_eng.build_partial_pptx(
                chemin, max(pages), only_slides=pages),
            convertir=lambda octets: convert_to_pdf_bytes(octets, "pptx",
                                                          use_cache=False),
            extension="pptx",
        )

        def _preparer_socle():
            """Convertit le document d'ORIGINE et l'écrit comme partiel initial.

            Mis en cache par CONTENU : retraduire le même document dans une
            autre langue ne le reconvertit pas. C'est ce qui rend l'affichage
            initial quasi instantané dès le deuxième passage.
            """
            with open(original_path, "rb") as f:
                brut = f.read()
            pdf = convert_to_pdf_bytes(brut, "pptx", use_cache=True)
            ecrire_atomiquement(partial_path, _apercu.poser_socle(pdf))

        def _afficher_slide(slide_num: int):
            """Convertit CETTE diapositive et l'affiche — EN LIGNE, tout de suite.

            C'est le cœur du page-par-page : la conversion se fait DANS la boucle
            de traitement, pas dans un thread séparé. L'aperçu suit donc
            exactement le rythme réel, une diapositive à la fois.

            Best-effort : un échec de rendu n'interrompt JAMAIS la traduction. La
            diapositive reste correcte dans le document final ; seul son aperçu
            progressif est manqué, et le socle continue de la montrer.
            """
            try:
                # Le socle a pu manquer au premier essai (LibreOffice occupé,
                # disque plein). Sans lui, toute greffe échoue : on retente.
                if not _apercu.pret:
                    _preparer_socle()

                # PIÈCE JOINTE EXCEL D'ABORD. Un objet Excel incorporé s'affiche
                # via une IMAGE de remplacement figée à la création : sans cette
                # passe, la diapositive « terminée » montrerait son tableau en
                # langue SOURCE. Best-effort et silencieux si rien à faire (la
                # plupart des diapositives n'ont aucun objet OLE).
                try:
                    pptx_eng.regenerate_ole_previews(
                        soffice_path=SOFFICE_PATH, only_slides={slide_num})
                    _ole_faits.add(slide_num)
                except Exception as e:
                    logger.warning("Aperçu Excel diapo %s : %s", slide_num, e)

                ecrire_atomiquement(partial_path, _apercu.greffer([slide_num]))
                jobs.emit(job_id, "partial",
                          {"ready": True, "pages": _apercu.pages_traduites})
            except Exception as e:
                logger.warning("Aperçu progressif diapo %s ignoré : %s",
                               slide_num, e)

        def _process_one_slide(slide_num: int):
            nonlocal done
            jobs.emit(job_id, "page", {"page": slide_num, "status": "extracting",
                         "done": done, "total": total})
            jobs.sync_progress(job_id, done, total)
            slide_data, info = pptx_eng.extract_slide(slide_num, filters)
            if not slide_data:
                with done_lock:
                    done += 1
                    cur_done = done
                jobs.emit(job_id, "page", {"page": slide_num, "status": "copied",
                             "done": cur_done, "total": total})
                jobs.sync_progress(job_id, cur_done, total)
                # Rien à greffer : sans texte traduisible, la diapositive est
                # DÉJÀ visible telle quelle dans le socle (l'original).
                return

            jobs.emit(job_id, "page", {"page": slide_num, "status": "translating",
                         "done": done, "total": total})
            jobs.sync_progress(job_id, done, total)

            if debug:
                for el in slide_data.get("text_elements", []):
                    el["translated_text"] = el["text"]
                for diag in slide_data.get("diagram_elements", []):
                    for el in diag.get("text_elements", []):
                        el["translated_text"] = el["text"]
                for chart in slide_data.get("chart_elements", []):
                    for el in chart.get("text_elements", []):
                        el["translated_text"] = el["text"]
                for layout in slide_data.get("layout_elements", []):
                    for el in layout.get("text_elements", []):
                        el["translated_text"] = el["text"]
                for el in slide_data.get("excel_elements", []):
                    el["translated_text"] = el["text"]
            else:
                if not ai_active:
                    raise ValueError("Le traducteur IA n'est pas disponible.")
                mini_json = os.path.join(
                    pptx_eng._get_temp_dir(), f"_slide{slide_num}.json")
                with open(mini_json, "w", encoding="utf-8") as f:
                    json.dump({"slides": [slide_data]}, f, ensure_ascii=False)

                success, result = ai_translator.translate_json(
                    mini_json, target_lang=target_lang,
                    progress_callback=jobs.progress_callback(job_id, "translate"),
                )
                if not success:
                    raise ValueError(f"Traduction slide {slide_num} échouée : {result}")

                with open(result, "r", encoding="utf-8") as f:
                    translated = json.load(f)
                if translated.get("slides"):
                    slide_data = translated["slides"][0]
                for p in (mini_json, result):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

            jobs.emit(job_id, "page", {"page": slide_num, "status": "rendering",
                         "done": done, "total": total})
            jobs.sync_progress(job_id, done, total)

            tmap = {el["id"]: (el.get("translated_text") or el["text"])
                    for el in _elements_de(slide_data)}

            # La traduction PERSISTANTE se retient ICI, AVANT l'injection —
            # c'est le seul moment où l'on tient encore la SOURCE et la CIBLE
            # côte à côte. Elle était reconstituée à la fin en ré-extrayant les
            # XML DÉJÀ INJECTÉS : `text` y contenait la traduction et
            # `translated_text` valait None. Le document perdait donc sa source,
            # le rendu à la demande resservait une traduction prise pour un
            # original, et aucune vérification ultérieure n'avait plus de quoi
            # comparer.
            # SECTION SÉRIALISÉE — injection + affichage. Le dossier temporaire,
            # le PDF partiel et LibreOffice sont partagés : deux threads qui
            # injecteraient/greffieraient à la fois corrompraient le partiel.
            # La traduction (au-dessus) reste parallèle ; SEULE cette zone est
            # exclusive. Chaque thread la traverse à son tour pour SA page.
            with _display_lock:
                slides_traduites[slide_num] = slide_data
                pptx_eng.inject_slide(slide_num, tmap)
                _afficher_slide(slide_num)   # OLE + conversion + greffe + emit

            with done_lock:
                done += 1
                cur_done = done

            jobs.emit(job_id, "page", {"page": slide_num, "status": "done",
                         "done": cur_done, "total": total})
            jobs.sync_progress(job_id, cur_done, total)

        # ── SOCLE d'abord : l'original, visible AVANT toute traduction ────
        try:
            _preparer_socle()
            jobs.emit(job_id, "partial", {"ready": True, "pages": 0})
        except Exception as e:
            logger.warning("Socle d'aperçu non produit : %s", e)

        # ── CYCLE COMPLET PAR PAGE, en parallèle ──────────────────────────
        # `_WORKERS` threads, chacun menant UNE diapositive de bout en bout
        # (extraction → traduction → injection → conversion → affichage) avant de
        # prendre la suivante. Les traductions se recouvrent ; les affichages
        # sont sérialisés par `_display_lock` (cf. `_process_one_slide`). Le
        # socle est déjà prêt, donc chaque greffe a sa base.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=_WORKERS) as executor:
            futures = [executor.submit(_process_one_slide, sn)
                       for sn in slides_to_process]
            # `.result()` propage la première exception rencontrée (le job passe
            # alors en erreur, comportement inchangé).
            for f in futures:
                f.result()

        # ── Cohérence terminologique, une fois TOUT le document connu ──────
        # Elle ne peut pas se faire plus tôt : la preuve qu'un terme est
        # traduisible vient des AUTRES slides. Après l'arrêt du convertisseur
        # de partiel, donc sans accès concurrent au dossier temporaire.
        if not debug and ai_active:
            try:
                jobs.emit(job_id, "progress", {"step": "coherence", "message": "Contrôle de cohérence des termes...", "page": 0, "total": None})
                n_coh = _coherence_document(
                    slides_traduites, pptx_eng, target_lang,
                    jobs.progress_callback(job_id, "coherence"))
                if n_coh:
                    logger.info(f"Job {job_id}: {n_coh} fragment(s) repris "
                                f"pour cohérence terminologique")
            except Exception as e:
                logger.warning(f"Job {job_id}: contrôle de cohérence ignoré : {e}")

        # ── Aperçus OLE Excel : régénérés depuis les classeurs traduits ────
        # Pour que le fichier téléchargé (et l'aperçu final) montre les
        # tableaux/graphiques Excel EN LANGUE CIBLE sans devoir activer l'objet
        # dans PowerPoint. Best-effort : un échec n'interrompt pas la traduction.
        try:
            jobs.emit(job_id, "progress", {"step": "ole", "message": "Régénération des aperçus Excel...", "page": 0, "total": None})
            # Ce qui RESTE : les diapositives que le convertisseur progressif
            # n'a pas traitées — mode parallèle (admin), lot en échec, ou aperçu
            # désactivé. `None` = tout le document, le comportement d'origine.
            reste = ({n for n in slides_to_process if n not in _ole_faits}
                     if _ole_faits else None)
            n_ole = (0 if reste == set() else
                     pptx_eng.regenerate_ole_previews(
                         soffice_path=SOFFICE_PATH, only_slides=reste))
            if n_ole:
                logger.info(f"Job {job_id}: {n_ole} aperçu(s) OLE Excel régénéré(s)")
        except Exception as e:
            logger.warning(f"Job {job_id}: régénération OLE ignorée : {e}")

        # ── PPTX final ────────────────────────────────────────────────────
        jobs.emit(job_id, "progress", {"step": "inject", "message": "Génération du PPTX final...", "page": 0, "total": None})
        pptx_eng.build_partial_pptx(output_path, max(slides_to_process))

        # ── Sauvegarde de la traduction (JSON complet pour reprise) ───────
        # Assemblée depuis ce qu'on a retenu AVANT injection : `text` y est la
        # SOURCE et `translated_text` la traduction. Ré-extraire les XML ici
        # revenait à relire notre propre sortie et à la déclarer originale.
        full_json = {"slides": [slides_traduites[sn]
                                for sn in slides_to_process
                                if sn in slides_traduites]}
        with open(translation_path, "w", encoding="utf-8") as f:
            json.dump(full_json, f, ensure_ascii=False)

        pptx_eng._cleanup_temp()
        jobs.done(job_id, output_path, output_filename,
                  translation_path=translation_path)

    except Exception as e:
        logger.error(f"Job PPTX {job_id} failed: {e}")
        # Plus de thread convertisseur à arrêter (traitement séquentiel).
        try:
            pptx_eng._cleanup_temp()
        except Exception:
            pass
        jobs.error(job_id, "La traduction a rencontré une erreur. Réessayez ou contactez le support.")


def run_pdf_v2_job(
    job_id: str, file_bytes: bytes, original_path: str,
    output_path: str, output_filename: str, partial_path: str,
    translation_path: str, target_lang: str, pages_set=None, debug: bool = False,
):
    """Pipeline PDF v2 PROGRESSIF : chaque page est extraite, traduite et rendue
    avant la suivante. Le PDF partiel grandit page après page — le client
    l'affiche au fil de l'eau via /api/translate/partial/{job_id}. Événements
    SSE émis : start{total} puis page{page,status,done,total}.

    `translation_path` (pages.json) est la traduction PERSISTANTE : le moteur y
    relit les pages déjà traduites au lieu de rappeler DeepSeek. Un document
    déjà traité n'y déclenche donc aucun appel — il se contente d'un rendu."""
    try:
        jobs.emit(job_id, "progress", {"step": "start", "message": "Démarrage du job...", "page": 0, "total": None})

        if not os.path.exists(original_path):
            with open(original_path, "wb") as f:
                f.write(file_bytes)

        jobs.set(job_id, partial_path=partial_path)

        def on_event(ev: dict):
            et = ev.get("type")
            if et == "start":
                jobs.emit(job_id, "start", {"total": ev.get("total")})
            elif et == "page":
                jobs.emit(job_id, "page", {
                    "page": ev.get("page"),
                    "status": ev.get("status"),
                    "done": ev.get("done"),
                    "total": ev.get("total"),
                })
                # Même avancement, mais PERSISTÉ : c'est lui qui permet à une
                # autre session — ou à la même après reconnexion — de savoir
                # que le document avance encore. Les événements ci-dessus ne
                # vivent que le temps du flux SSE de celui qui l'a lancé.
                jobs.sync_progress(job_id, ev.get("done") or 0,
                                        ev.get("total"))
                # Compatibilité barre de progression générique.
                jobs.emit(job_id, "progress", {
                    "step": "translate",
                    "message": f"Page {ev.get('page')}/{ev.get('total')} : {ev.get('status')}",
                    "page": ev.get("done"),
                    "total": ev.get("total"),
                })

        pdf_v2_stream.translate_pdf_progressive(
            original_path, output_path, target_lang=target_lang,
            pages=pages_set, partial_path=partial_path, on_event=on_event,
            debug=debug, cache_path=translation_path,
        )

        if not os.path.exists(output_path):
            raise ValueError("Le fichier traduit est introuvable après génération.")

        # Le rendu complet vient d'être produit — on le CONSERVE au lieu de le
        # laisser au GC des transitoires. C'est lui qui rend l'aperçu et le
        # téléchargement instantanés ensuite (mesuré : 280 s à reconstruire
        # sur 285 pages). Seule la sortie CANONIQUE est conservée : un rendu
        # de débogage ou remis en page ne doit jamais être resservi comme la
        # traduction fidèle.
        if not debug and "_STRUCTURE" not in output_filename \
                and output_filename.endswith(".pdf") \
                and "_TRADUIT." in output_filename:
            try:
                with open(output_path, "rb") as f:
                    render_cache.store_render(f.read(), translation_path)
            except OSError as e:
                logger.warning("Rendu du job non conservé : %s", e)

        jobs.done(job_id, output_path, output_filename,
                  translation_path=translation_path)

    except Exception as e:
        logger.error(f"Job PDF v2 {job_id} failed: {e}")
        jobs.error(job_id, "La traduction a rencontré une erreur. Réessayez ou contactez le support.")


def render_translation_bytes(original_path: str, translation_path: str,
                             ext: str, target_lang: str,
                             only_pages: set[int] | None = None) -> bytes:
    """Recalcule le document traduit à partir de l'original + la traduction
    stockée, SANS jamais rappeler DeepSeek. C'est le pilier du nouveau modèle :
    on ne conserve plus le rendu, on le reconstruit à la demande.

    PDF : le moteur v2 relit `pages.json` (déjà traduit) et se contente de
    rendre. On lui passe EXPLICITEMENT les pages déjà traduites — jamais None,
    qui le pousserait à traduire les pages manquantes (donc à payer l'API). Une
    page absente du JSON est simplement recopiée de l'original.
    DOCX/PPTX : ré-injection locale de `translated.json` dans l'original.
    """
    if not os.path.isfile(original_path) or not os.path.isfile(translation_path):
        raise FileNotFoundError("Original ou traduction manquant pour le rendu.")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, f"render.{ext}")
        if ext == "pdf":
            with open(translation_path, encoding="utf-8") as f:
                prev = json.load(f)
            pages_traduites = {
                pg.get("page_num") for pg in prev.get("pages", [])
                if any(e.get("tr_tagged") for e in pg.get("elements", [])
                       if e.get("type") == "paragraph")
            }
            pages_traduites.discard(None)
            # `only_pages` restreint ce qu'on RECONSTRUIT, pas ce qu'on livre :
            # les pages écartées sont recopiées de l'original, donc la
            # pagination reste identique et le lecteur n'a rien à recalculer.
            #
            # Mesuré sur un document de 285 pages : 280 s pour tout rendre,
            # ~1 s par page. L'aperçu n'affiche QU'UNE page à la fois — en
            # reconstruire 285 pour en montrer une était le blocage.
            if only_pages is not None:
                pages_traduites &= set(only_pages)
            pdf_v2_stream.translate_pdf_progressive(
                original_path, out, target_lang=target_lang,
                pages=pages_traduites, partial_path=None, on_event=None,
                debug=False, cache_path=translation_path,
            )
        elif ext == "docx":
            ok, msg = engines.new_engine("docx").inject_translation(original_path,
                                                     translation_path, out)
            if not ok:
                raise ValueError(f"Ré-injection DOCX échouée : {msg}")
        elif ext == "pptx" and pptx_available:
            # Moteur NEUF à chaque rendu. Un moteur partagé faisait se recouvrir
            # deux rendus simultanés dans le même dossier temporaire : l'aperçu
            # d'une langue ressortait dans celui d'une autre.
            ok, msg = engines.new_engine("pptx").inject_translation(original_path,
                                                          translation_path, out)
            if not ok:
                raise ValueError(f"Ré-injection PPTX échouée : {msg}")
        else:
            raise ValueError(f"Rendu à la demande non supporté pour .{ext}")
        with open(out, "rb") as f:
            return f.read()
