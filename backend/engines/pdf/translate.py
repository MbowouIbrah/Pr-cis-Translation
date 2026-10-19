"""
pdf_engine_v2.translate — Orchestration de la traduction d'une extraction v2.

Chaîne : pour chaque paragraphe (des `max_pages` premières pages), on produit un
texte BALISÉ par style (`tagging.tag_paragraph`), on l'envoie par lots au
traducteur existant (`backend/translator_ai.py`, qui préserve les balises
`[[n]]`), puis on stocke sur le paragraphe :

    el["tr_tagged"]   = texte traduit balisé  (rendu par le reflow)
    el["tr_segments"] = styles des balises    (police/gras/italique/taille/couleur)

Aucune modification du reste de l'extraction : images, dessins, mise en page
restent identiques ; seule la source du TEXTE change à la réinjection
(`engine.reinject(..., translated=True)`).
"""

import os
import re
import sys

from . import tagging


def _tags_ok(src_tagged, out_tagged):
    """Les balises `[[n]]…[[/n]]` de la traduction correspondent-elles à celles
    de la source ? (mêmes numéros ouvrants/fermants — l'ordre peut varier)."""
    tags = lambda t: sorted(re.findall(r"\[\[/?\d+\]\]", t or ""))
    return tags(src_tagged) == tags(out_tagged)

# `translator_ai` vit dans backend/ : on l'ajoute au chemin d'import.
_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _para_size(el):
    """Corps dominant d'un paragraphe (la plus grande taille de ses runs)."""
    return max((r.get("size", 0) or 0
                for ln in el.get("lines", []) for r in ln.get("runs", [])),
               default=0.0)


def _page_support(page):
    """Classe chaque paragraphe d'une page en 'titre' ou 'corps'.

    Signal de MISE EN PAGE, pas de contenu (aucune règle liée à un document) :
    le corps de référence est la taille du texte qui occupe le plus de
    CARACTÈRES sur la page ; un paragraphe nettement plus gros ET court est un
    titre / bandeau / affiche. C'est l'information qui manquait au traducteur —
    « BREAKING NEWS » en 60 pt centré est un bandeau, et un bandeau ne se traduit
    pas comme une phrase.

    Retourne {id(el): 'titre'|'corps'}.
    """
    paras = [e for e in page.get("elements", []) if e.get("type") == "paragraph"]
    weights = {}
    for el in paras:
        s = round(_para_size(el), 1)
        if s:
            weights[s] = weights.get(s, 0) + len((el.get("text") or ""))
    if not weights:
        return {}
    body = max(weights, key=weights.get)        # taille la plus « écrite »
    out = {}
    for el in paras:
        s = _para_size(el)
        txt = (el.get("text") or "").strip()
        out[id(el)] = ("titre" if s >= 1.5 * body and len(txt) <= 80
                       else "corps")
    return out


def _paragraphes_lus(page):
    """Paragraphes non vides de la page dans l'ordre de LECTURE, reconstruit
    par leur position : l'ordre d'extraction peut être faux (titre relevé en
    dernier, colonnes mêlées) et ne doit rien décider."""
    from engines.translation_ai import ordre_lecture
    paras = [e for e in page.get("elements", [])
             if e.get("type") == "paragraph" and (e.get("text") or "").strip()]
    boites = [tuple(e.get("bbox") or (0, 0, 0, 0))[:4] for e in paras]
    return [paras[i] for i in ordre_lecture(boites)]


def _cadre_page(page, support, fiche):
    """Ce que chaque lot de la page doit savoir et que ses fragments ne disent
    pas : la fiche du document et le texte de la page, ordonné."""
    lus = _paragraphes_lus(page)
    return {"fiche": fiche, "n_page": len(lus),
            "textes": [e.get("text") for e in lus],
            "titres": {i for i, e in enumerate(lus)
                       if support.get(id(e)) == "titre"},
            "rang": {id(e): k for k, e in enumerate(lus)}}


def _cadre_lot(cadre, rangs, n_page):
    """Cadre d'UN lot : la fiche, et la page AUTOUR de ses fragments.
    `n_page` : fragments de la page envoyés au modèle ; un lot qui les porte
    tous n'a que faire du texte de sa page (cf. _translate_batch)."""
    from engines.translation_ai import contexte_page
    autour = (min(rangs), max(rangs) + 1) if rangs else None
    return {"fiche": cadre["fiche"], "n_page": n_page,
            "page": contexte_page(cadre["textes"], cadre["titres"], autour)}


def matiere_fiche(pages):
    """(titres, textes) d'une extraction, de quoi écrire la fiche du
    document : titres repérés par la mise en page, texte en ordre de lecture."""
    titres, textes = [], []
    for page in pages:
        support = _page_support(page)
        for e in _paragraphes_lus(page):
            t = e.get("text") or ""
            textes.append(t)
            if support.get(id(e)) == "titre":
                titres.append(t)
    return titres, textes


def matiere_fiche_pdf(doc, n_pages=15):
    """Comme `matiere_fiche`, mais lue directement dans le PDF ouvert (fitz)
    sur `n_pages` pages RÉPARTIES : la traduction progressive n'a pas encore
    extrait les pages suivantes quand elle écrit la fiche. Même règle de titre
    que `_page_support` (nettement plus gros que le corps, et court)."""
    total = len(doc)
    pas = max(1, total / n_pages)
    titres, textes = [], []
    for k in sorted({int(i * pas) for i in range(min(total, n_pages))}):
        lignes = []
        for bloc in doc[k].get_text("dict").get("blocks", []):
            for ln in bloc.get("lines", []):
                spans = ln.get("spans", [])
                t = "".join(sp.get("text", "") for sp in spans).strip()
                if t:
                    lignes.append((t, max(sp.get("size", 0) for sp in spans)))
        poids = {}
        for t, taille in lignes:
            poids[round(taille, 1)] = poids.get(round(taille, 1), 0) + len(t)
        corps = max(poids, key=poids.get) if poids else 0
        textes.append(" ".join(t for t, _ in lignes))
        titres += [t for t, taille in lignes
                   if corps and taille >= 1.5 * corps and len(t) <= 80]
    return titres, textes


def _hors_service():
    from engines.translation_ai import hors_service
    return hors_service()


def _lots_de_page(items, taille):
    """Découpe les items d'UNE page (déjà en ordre de lecture) en lots d'au
    plus `taille`, sans jamais séparer un titre du texte qui le suit."""
    lots, cur = [], []
    for it in items:
        if len(cur) >= taille:
            reste = []
            while len(cur) > 1 and cur[-1].get("support") == "titre":
                reste.insert(0, cur.pop())
            lots.append(cur)
            cur = reste
        cur.append(it)
    if cur:
        lots.append(cur)
    return lots


# Taille maximale (en caractères, hors balises) d'un texte mémorisable dans la
# MÉMOIRE DE DOCUMENT. Au-delà, une phrase répétée mot pour mot est rare et son
# contexte peut légitimement changer sa traduction ; en deçà (titres courants,
# en-têtes, intitulés répétés), la cohérence prime.
_MEMO_MAX_LEN = 120


def _sans_typo(texte):
    """Blancs normalisés, sans l'espace que la typographie française met devant
    « : ; ! ? % » et dans les guillemets : « Tax Structure : » et « Tax
    Structure: » sont le même texte saisi selon deux usages, pas deux textes
    (mesuré : un même libellé traduit de deux façons d'une page à l'autre)."""
    return re.sub(r"\s+(?=[:;!?%»])|(?<=«)\s+", "", " ".join(texte.split()))


def _plain_key(tagged):
    """Clé de mémoire : le texte SANS balises, blancs normalisés (sensible à la
    casse — « Part One » et « PART ONE » sont deux choses)."""
    return _sans_typo(re.sub(r"\[\[/?\d+\]\]", "", tagged or ""))


def reattacher_traductions(nouvelle, ancienne):
    """Pose sur la page REDÉCOUPÉE `nouvelle` les traductions de `ancienne`.

    Un correctif de découpage ne doit ni repayer la traduction, ni changer en
    silence le texte que l'utilisateur a lu. On réattache donc par le texte
    source balisé : un paragraphe redécoupé à l'identique retrouve sa
    traduction mot pour mot. Tout ou rien : si UN paragraphe est nouveau (texte
    regroupé autrement), rien n'est posé et la page ancienne reste en place —
    la retraduire est une décision de l'utilisateur, pas la nôtre.

    Renvoie True si toutes les traductions ont été posées (zéro appel IA).
    """
    connues = {}
    for el in ancienne.get("elements", []):
        if el.get("type") == "paragraph" and el.get("tr_tagged") is not None:
            connues[tagging.tag_paragraph(el)[0]] = el["tr_tagged"]
    poses = []
    for el in nouvelle.get("elements", []):
        if el.get("type") != "paragraph":
            continue
        tagged, meta = tagging.tag_paragraph(el)
        if tagged.strip() and tagged not in connues:
            return False
        poses.append((el, meta, connues.get(tagged, tagged)))
    for el, meta, tr in poses:
        el["tr_segments"] = meta
        el["tr_tagged"] = tr
    return True


def translate_extraction(data, target_lang="fr", max_pages=None,
                         batch_size=40, progress=None, doc_memory=None,
                         fiche=None):
    """Traduit les paragraphes des `max_pages` premières pages de `data` (dict
    d'extraction v2). Modifie `data` en place et le retourne.

    target_lang : code court ('fr', 'en', …) ou nom.
    max_pages   : limite le nombre de pages traitées (None = tout).
    doc_memory  : MÉMOIRE DE DOCUMENT (dict partagé entre les pages d'un même
        document). La traduction page par page n'a aucune mémoire : le même
        titre courant, le même intitulé de chapitre, ressortent traduits
        différemment d'une page à l'autre (mesuré : « PARTIE UN » p4 contre
        « DEUXIÈME PARTIE » plus bas ; sommaire vs tête de chapitre). Un texte
        court DÉJÀ traduit dans ce document est resservi tel quel — garantie
        déterministe, zéro jeton — et n'est plus envoyé au modèle.
    fiche       : FICHE DE DOCUMENT (cf. TranslatorAI.fiche_document). None =
        l'écrire d'après les pages reçues ; "" = s'en passer. Une traduction
        page par page la reçoit, écrite une fois pour tout le document.
    """
    from engines.translation_ai import TranslatorAI
    tr = TranslatorAI()
    lang_name = tr.lang_name(target_lang)

    pages = data.get("pages", [])
    if max_pages is not None:
        pages = pages[:max_pages]
    if fiche is None:
        fiche = tr.fiche_document(*matiere_fiche(pages), lang_name,
                                  progress_callback=progress)

    # 1) Balisage + collecte des items à traduire (id unique -> paragraphe).
    items, refs = [], {}
    resservis = 0
    par_page = []                               # [(cadre, [items])]
    rang_de = {}                                # id d'item -> rang de lecture
    for page in pages:
        support = _page_support(page)
        cadre = _cadre_page(page, support, fiche)
        rang = cadre["rang"]
        a_traduire = []
        for el in page.get("elements", []):
            if el.get("type") != "paragraph":
                continue
            tagged, meta = tagging.tag_paragraph(el)
            el["tr_segments"] = meta
            if not tagged.strip():
                el["tr_tagged"] = tagged           # paragraphe vide : rien à faire
                continue
            # MÉMOIRE DE DOCUMENT : même texte, même structure de balises,
            # déjà traduit dans ce document → resservi, pas renvoyé au modèle.
            if doc_memory is not None:
                cle = _plain_key(tagged)
                memo = doc_memory.get(cle)
                # Mêmes balises aux mêmes mots : le style resservi tombe juste.
                if (memo and _sans_typo(memo[0]) == _sans_typo(tagged)
                        and len(cle) <= _MEMO_MAX_LEN):
                    el["tr_tagged"] = memo[1]
                    resservis += 1
                    continue
            _id = f"p{page['page_num']}_e{len(refs)}"
            # `support` / `contexte` : ce que le traducteur ignorait (un bandeau
            # ne se traduit pas comme une phrase) — cf. backend/glossary.py.
            it = {"id": _id, "text": tagged,
                  "support": support.get(id(el), "corps")}
            items.append(it)
            rang_de[_id] = rang.get(id(el), len(rang))
            a_traduire.append((rang_de[_id], it))
            refs[_id] = el
            el["tr_tagged"] = tagged                # repli si la trad échoue
        if a_traduire:
            a_traduire.sort(key=lambda r: r[0])
            par_page.append((cadre, [it for _r, it in a_traduire]))
    if resservis and progress:
        progress(f"{resservis} texte(s) resservi(s) depuis la mémoire du document.")

    if progress:
        progress(f"{len(items)} paragraphe(s) à traduire vers {lang_name}.")

    # 2) Traduction PAR PAGE (le traducteur préserve les balises [[n]]) : un
    #    lot ne mêle jamais deux pages, et chaque lot reçoit le cadre de la
    #    sienne. Une tranche fixe de 40 fragments coupait une page en deux et
    #    privait la seconde moitié de ses titres.
    lots = [(_cadre_lot(cadre, [rang_de[b["id"]] for b in lot], len(its)),
             lot)
            for cadre, its in par_page
            for lot in _lots_de_page(its, batch_size)]
    cadre_de = {}
    for n, (cadre, batch) in enumerate(lots):
        for b in batch:
            cadre_de[b["id"]] = cadre
        try:
            tr._translate_batch(batch, lang_name, progress, cadre=cadre)
        except Exception as e:
            if progress:
                progress(f"[!] lot {n} échoué : {e}")
        for b in batch:
            el = refs[b["id"]]
            el["tr_tagged"] = b.get("translated_text") or b["text"]
        if progress:
            progress(f"  lot {n + 1}/{len(lots)} traduit.")

    # 3) VÉRIFICATION PAR ITEM + relance individuelle : un id absent de la
    #    réponse ou des balises mutilées restaient silencieux (texte source
    #    conservé / styles perdus). On re-traduit ces items un à un (2 essais),
    #    et on JOURNALISE ce qui reste en repli au lieu de le taire.
    failed = [it for it in items
              if not it.get("translated_text")
              or not _tags_ok(it["text"], it["translated_text"])]
    for attempt in range(2):
        if not failed or _hors_service():
            break
        if progress:
            progress(f"{len(failed)} item(s) à re-traduire individuellement "
                     f"(id manquant ou balises mutilées), essai {attempt + 1}.")
        still = []
        for it in failed:
            one = {"id": it["id"], "text": it["text"],
                   "support": it.get("support")}
            try:
                tr._translate_batch([one], lang_name, None,
                                    cadre=cadre_de.get(it["id"]))
            except Exception:
                pass
            out = one.get("translated_text")
            if out and _tags_ok(it["text"], out):
                it["translated_text"] = out
                refs[it["id"]]["tr_tagged"] = out
            else:
                still.append(it)
        failed = still
    if failed and _hors_service():
        # Solde ou clé refusés : le texte source n'est pas une traduction, la
        # page ne doit pas entrer au cache comme traduite. L'échec du job la
        # laisse à reprendre une fois le compte rechargé.
        from engines.translation_ai import FournisseursIndisponibles
        raise FournisseursIndisponibles(
            f"{len(failed)} paragraphe(s) non traduits : DeepSeek indisponible.")
    if failed and progress:
        for it in failed:
            progress(f"[!] non traduit (repli source) : {it['text'][:60]!r}")

    # 4) MÉMOIRE DE DOCUMENT : la PREMIÈRE traduction d'un texte court devient
    #    la référence pour la suite du document (ordre de lecture). On
    #    n'enregistre que les items réellement traduits (jamais un repli
    #    source) et on ne réécrit jamais une entrée existante.
    if doc_memory is not None:
        rates = {id(it) for it in failed}
        for it in items:
            if id(it) in rates or not it.get("translated_text"):
                continue
            cle = _plain_key(it["text"])
            if cle and len(cle) <= _MEMO_MAX_LEN and cle not in doc_memory:
                doc_memory[cle] = (it["text"], refs[it["id"]]["tr_tagged"])

    return data


def coherence_document(pages, target_lang="fr", fiche="", progress=None):
    """COHÉRENCE DE DOCUMENT, en fin de traduction : reprend les paragraphes
    qui laissent en langue source un terme que le document traduit ailleurs
    (« Ambassade » gardé une fois, « Embassy » partout ailleurs). La preuve
    vient du document lui-même (runtags.termes_incoherents), jamais d'une
    liste de termes ; même consigne que la passe PPTX.

    Modifie `tr_tagged` en place et renvoie les numéros des pages touchées.
    Une reprise aux balises mutilées est écartée : la traduction d'avant
    reste. Best-effort : une erreur laisse tout en l'état.
    """
    from engines import runtags
    paires = []                                 # (page, el, source balisée)
    for page in pages:
        for el in page.get("elements", []):
            if el.get("type") != "paragraph" or not el.get("tr_tagged"):
                continue
            try:
                tagged, _m = tagging.tag_paragraph(el)
            except Exception:
                continue
            if tagged.strip():
                paires.append((page, el, tagged))
    incoherents = runtags.termes_incoherents(
        (src, el["tr_tagged"]) for _p, el, src in paires)
    if not incoherents:
        return set()

    par_page = {}                               # id(page) -> (page, [(el, it)])
    for k, (page, el, src) in enumerate(paires):
        termes = runtags.termes_a_reprendre(src, el["tr_tagged"], incoherents)
        if not termes:
            continue
        it = {"id": f"c{page['page_num']}_{k}",
              "text": src,
              "consigne": runtags.CONSIGNE_COHERENCE.format(
                  termes=", ".join(termes), precedente=el["tr_tagged"])}
        par_page.setdefault(id(page), (page, []))[1].append((el, it))
    if not par_page:
        return set()

    from engines.translation_ai import TranslatorAI
    tr = TranslatorAI()
    lang_name = tr.lang_name(target_lang)
    if progress:
        progress(f"cohérence : {sum(len(v[1]) for v in par_page.values())} "
                 f"paragraphe(s) repris ({len(incoherents)} terme(s)).")
    touchees = set()
    for page, couples in par_page.values():
        support = _page_support(page)
        for el, it in couples:
            it["support"] = support.get(id(el), "corps")
        cadre = _cadre_page(page, support, fiche)
        batch = [it for _el, it in couples]
        try:
            tr._translate_batch(batch, lang_name, progress, passes=0,
                                cadre=_cadre_lot(
                                    cadre, [cadre["rang"].get(id(el), 0)
                                            for el, _it in couples],
                                    cadre["n_page"]))
        except Exception as e:
            if progress:
                progress(f"[!] cohérence page {page.get('page_num')} : {e}")
            continue
        for el, it in couples:
            out = it.get("translated_text")
            if out and out != el["tr_tagged"] and _tags_ok(it["text"], out):
                el["tr_tagged"] = out
                touchees.add(page["page_num"])
    return touchees


def retranslate_overflows(data, engine, target_lang="fr", max_pages=None,
                          progress=None, max_rounds=2, fiche=""):
    """RETRADUCTION COMPACTE (décision produit : compression par REFORMULATION,
    jamais une mise en forme dégradée). Pour chaque paragraphe dont la
    traduction ne tient pas dans son conteneur sans compression visible
    (`engine.translated_fit`), on re-demande une traduction avec un BUDGET DE
    CARACTÈRES strict. Deux tours max ; en dernier recours le rendu garde sa
    garantie force-fit (jamais de chevauchement)."""
    from engines.translation_ai import TranslatorAI
    tr = TranslatorAI()
    lang_name = tr.lang_name(target_lang)

    pages = data.get("pages", [])
    if max_pages is not None:
        pages = pages[:max_pages]

    for rnd in range(max_rounds):
        # Prépare la page (grow/_vscale recalculés sur l'état courant) puis
        # mesure la tenue de chaque paragraphe traduit.
        offenders = []
        for page in pages:
            try:
                engine._prepare_translated_page(page)
            except Exception:
                pass
            support = _page_support(page)
            cadre = None
            for el in page.get("elements", []):
                if el.get("type") != "paragraph" or not el.get("_needs_shorter"):
                    continue
                fit = engine.translated_fit(el)
                if fit is None:
                    continue
                cadre = cadre or _cadre_page(page, support, fiche)
                offenders.append((el, fit, support.get(id(el), "corps"),
                                  cadre))
        if not offenders:
            break
        if progress:
            progress(f"{len(offenders)} paragraphe(s) trop longs -> "
                     f"retraduction compacte (tour {rnd + 1}).")
        shrink = 1.0 - 0.08 * rnd               # budgets resserrés au 2e tour
        par_cadre = {}                          # id(cadre) -> (cadre, items)
        refs, rang_de = {}, {}
        for k, (el, fit, support, cadre) in enumerate(offenders):
            src_tagged, _meta = tagging.tag_paragraph(el)
            if not src_tagged.strip():
                continue
            src_len = len(re.sub(r"\[\[/?\d+\]\]", "", src_tagged))
            # PLANCHER : ne jamais demander moins de ~0,92 × la longueur
            # SOURCE — en dessous, le modèle n'a d'autre issue que d'abréger
            # (« Ch7 », sigles), ce que la règle produit interdit. Le petit
            # dépassement restant est absorbé par la compression du rendu
            # (préférable à une abréviation).
            budget = max(int(0.92 * src_len), int(fit["budget"] * shrink))
            cur_len = len(re.sub(r"\[\[/?\d+\]\]", "", el.get("tr_tagged") or ""))
            if cur_len <= budget:
                continue                        # rien à gagner : garder tel quel
            _id = f"c{rnd}_{k}"
            par_cadre.setdefault(id(cadre), (cadre, []))[1].append(
                         {"id": _id, "text": src_tagged,
                          "support": support,
                          "consigne": (f"MAXIMUM {budget} caractères (hors "
                                       "balises), par REFORMULATION uniquement :"
                                       " AUCUNE abréviation, AUCUN sigle absent"
                                       " du texte source, ne rien omettre "
                                       "d'essentiel.")})
            refs[_id] = el
            rang_de[_id] = cadre["rang"].get(id(el), 0)
        # Une page entière n'est pas renvoyée : n_page = ses paragraphes, le
        # lot n'en porte que les fautifs et reçoit la page autour d'eux.
        lots = [(_cadre_lot(cadre, [rang_de[b["id"]] for b in sub],
                            cadre["n_page"]), sub)
                for cadre, its in par_cadre.values()
                for sub in (its[i:i + 20] for i in range(0, len(its), 20))]
        for cadre, sub in lots:
            try:
                tr._translate_batch(sub, lang_name, progress, cadre=cadre)
            except Exception as e:
                if progress:
                    progress(f"[!] retraduction : lot échoué : {e}")
            for b in sub:
                el = refs[b["id"]]
                out = b.get("translated_text")
                if out and _tags_ok(b["text"], out):
                    el["tr_tagged"] = out
    return data
