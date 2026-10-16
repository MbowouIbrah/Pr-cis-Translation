import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from dotenv import load_dotenv

try:
    from . import runtags                       # importé en paquet
except ImportError:                             # ... ou à plat (sys.path backend)
    import runtags

load_dotenv()

class FournisseursIndisponibles(Exception):
    """DeepSeek ne peut pas répondre (solde, clé ou quota épuisés). Ni une
    relance ni une scission du lot n'y changent rien : on s'arrête."""


# ── DeepSeek ─────────────────────────────────────────────────────────────────
#
# Un solde épuisé (402) était traité comme un incident passager — 3 relances,
# puis scission du lot en deux, récursivement, soit des dizaines d'appels voués
# à l'échec par page. Ces statuts demandent une action humaine (recharger,
# changer la clé) ou une pause (429) : on cesse d'appeler pendant ce délai.
_HORS_SERVICE_S = {401: 600, 402: 600, 403: 600, 429: 60}

# Échéance PARTAGÉE : le moteur PDF crée un TranslatorAI par page ; un état
# porté par l'instance oublierait le 402 à la page suivante.
_hors_service = {"jusqua": 0.0, "statut": None, "serie_402": False}


def hors_service():
    """Vrai si DeepSeek refuse durablement (solde, clé) : un appel échouerait
    sans rien coûter, et un texte non traduit ne doit pas passer pour fini.
    Une PREMIÈRE 429 n'en est pas : on attend son tour (cf. _appel) ; une
    seconde, juste après l'attente, si (quota durablement dépassé)."""
    return (time.monotonic() < _hors_service["jusqua"]
            and _hors_service["statut"] != 429)

# Appelés (sans argument d'état) au premier 402 d'une série : l'application y
# branche son alerte « solde épuisé ». Le moteur n'importe rien de l'app.
sur_solde_epuise: list = []

# ── Réflexion ────────────────────────────────────────────────────────────────
#
# DeepSeek active la réflexion PAR DÉFAUT (effort « high »). `deepseek-chat`,
# l'ancien alias, était sans réflexion ; depuis sa suppression (24/07/2026)
# chaque lot payait donc un raisonnement que personne n'avait demandé, dans un
# budget de 8192 jetons qu'il épuisait parfois avant d'écrire la réponse.
#
# Le niveau est désormais EXPLICITE, porté par le nom du modèle :
# « deepseek-flash » = sans réflexion ; « deepseek-flash:high » = avec.
EFFORTS = ("low", "high", "max")
# Budget de sortie minimal quand le modèle réfléchit : le raisonnement se
# décompte du même `max_tokens` que la réponse.
_MAX_TOKENS_REFLEXION = 65536


def decouper_modele(spec):
    """« deepseek-v4-pro:high » → ("deepseek-v4-pro", "high") ; sans suffixe
    (ou « :off ») → effort None = réflexion coupée."""
    nom, _, effort = (spec or "").partition(":")
    effort = effort.strip().lower()
    if effort in ("", "off", "non", "aucune"):
        return nom.strip(), None
    if effort not in EFFORTS:
        raise ValueError(f"Niveau de réflexion inconnu « {effort} » "
                         f"(attendu : off, {', '.join(EFFORTS)})")
    return nom.strip(), effort


# ── Consommation ─────────────────────────────────────────────────────────────
#
# Cumul des jetons facturés, par modèle, pour tout le process. Sert au banc de
# coût et au suivi d'exploitation ; le SOLDE du compte reste la seule vérité
# comptable (cf. app/services/solde_ia.py).
_conso_verrou = threading.Lock()
consommation: dict[str, dict[str, int]] = {}


def consommation_instantane():
    """Copie de `consommation` prise sous verrou : la sérialiser en place
    pendant qu'un thread y ajoute un modèle lèverait une erreur."""
    with _conso_verrou:
        return {m: dict(c) for m, c in consommation.items()}


def _compter(modele, usage):
    if usage is None:
        return
    details = getattr(usage, "completion_tokens_details", None)
    with _conso_verrou:
        c = consommation.setdefault(modele, dict.fromkeys(
            ("appels", "entree_cache", "entree_hors_cache", "sortie",
             "reflexion"), 0))
        c["appels"] += 1
        c["entree_cache"] += getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        c["entree_hors_cache"] += (getattr(usage, "prompt_cache_miss_tokens", None)
                                   if getattr(usage, "prompt_cache_miss_tokens", None) is not None
                                   else getattr(usage, "prompt_tokens", 0) or 0)
        c["sortie"] += getattr(usage, "completion_tokens", 0) or 0
        c["reflexion"] += getattr(details, "reasoning_tokens", 0) or 0


def _client_deepseek():
    cle = os.getenv("DEEPSEEK_API_KEY")
    if not cle:
        return None
    return OpenAI(api_key=cle,
                  base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                  timeout=float(os.getenv("DEEPSEEK_TIMEOUT_S", "300")))


# ── Contexte ─────────────────────────────────────────────────────────────────
#
# Les contresens mesurés (« Probatoire » → « Probationary » dans un CV) venaient
# d'un MANQUE DE CONTEXTE, pas du modèle : un lot de fragments ne disait ni ce
# qu'était le document, ni ce qui entourait le fragment. Deux remèdes, communs
# à tous les formats :
#   • la FICHE DE DOCUMENT : un appel par document, qui décrit sa nature, son
#     pays d'origine, son public et ses termes propres ; jointe au prompt
#     système de chaque lot (préfixe constant : mis en cache par DeepSeek) ;
#   • le CONTEXTE DE PAGE : le texte de la page dans l'ordre de LECTURE,
#     reconstruit par la géométrie — l'ordre d'extraction peut être faux
#     (titres relevés en dernier, colonnes mêlées).

# Taille de la matière envoyée pour écrire la fiche, et de la fiche elle-même.
_FICHE_ENTREE = 6000
_FICHE_MAX = 1500
# Texte de page joint à un lot qui ne couvre pas toute la page.
_CONTEXTE_MAX = 2500


def ordre_lecture(boites):
    """Indices de `boites` (x0, y0, x1, y1) dans l'ordre de lecture.

    Découpe récursive par bandes vides (« XY-cut ») : une bande horizontale
    sans aucune boîte sépare deux blocs lus l'un après l'autre (haut → bas),
    une bande verticale sépare deux colonnes (gauche → droite). La coupe en
    COLONNES prime quand elle existe : des paragraphes de deux colonnes
    alignés à la même hauteur laissent aussi des bandes horizontales vides,
    et les lire ligne à ligne mêlerait les colonnes. Un titre ou un pied
    pleine largeur empêche la coupe verticale : il est alors détaché en
    premier, puis ses colonnes sont lues l'une après l'autre.
    Ne dépend QUE des positions, jamais de l'ordre reçu."""
    def bandes(ids, axe):
        s = sorted(ids, key=lambda i: (boites[i][axe], boites[i][1 - axe]))
        groupes, cur = [], [s[0]]
        fin = boites[s[0]][axe + 2]
        for i in s[1:]:
            if boites[i][axe] >= fin:
                groupes.append(cur)
                cur = []
            cur.append(i)
            fin = max(fin, boites[i][axe + 2])
        groupes.append(cur)
        return groupes

    def colonnes(ids):
        return len(ids) > 1 and len(bandes(ids, 0)) > 1

    def coupe(ids):
        if len(ids) <= 1:
            return list(ids)
        if colonnes(ids):
            return [j for g in bandes(ids, 0) for j in coupe(g)]
        groupes = bandes(ids, 1)
        if len(groupes) == 1:
            return sorted(ids, key=lambda i: (boites[i][1], boites[i][0]))
        # Bandes voisines qui partagent une gouttière = les mêmes colonnes,
        # coupées par l'interligne de paragraphes alignés : on les ressoude.
        soudes = [groupes[0]]
        for g in groupes[1:]:
            if (colonnes(soudes[-1]) and colonnes(g)
                    and colonnes(soudes[-1] + g)):
                soudes[-1] = soudes[-1] + g
            else:
                soudes.append(g)
        return [j for g in soudes for j in coupe(g)]

    return coupe(list(range(len(boites))))


def echantillon_reparti(textes, budget=_FICHE_ENTREE):
    """Extraits pris RÉGULIÈREMENT sur tout le document, dans l'ordre, jusqu'à
    `budget` caractères. Les premiers caractères seuls décrivent une page de
    garde ; la nature d'un document se lit sur toute sa longueur."""
    textes = [t.strip() for t in textes if t and t.strip()]
    total = sum(len(t) for t in textes)
    if total <= budget:
        return textes
    pas = total / budget                 # 1 caractère retenu sur `pas`
    pris, cumul, prochain = [], 0, 0.0
    for t in textes:
        if cumul >= prochain:
            pris.append(t[:400])
            prochain += max(len(t[:400]) * pas, 1)
        cumul += len(t)
    return pris


def contexte_page(textes, titres=(), autour=None):
    """Texte d'une page déjà ORDONNÉE, titres mis en avant (« # »). Trop long,
    il se réduit à une fenêtre élargie de part et d'autre de `autour` =
    (début, fin) des fragments du lot : le haut de page seul priverait le bas
    de son voisinage."""
    lignes = []
    for i, t in enumerate(textes):
        t = " ".join((t or "").split())
        lignes.append(("# " if i in titres else "") + t if t else "")
    d, f = 0, len(lignes)
    if autour is not None and sum(len(x) + 1 for x in lignes) > _CONTEXTE_MAX:
        d = min(max(0, autour[0]), len(lignes))
        f = min(len(lignes), max(autour[1], d + 1))
        taille = sum(len(x) + 1 for x in lignes[d:f])
        while taille < _CONTEXTE_MAX and (d > 0 or f < len(lignes)):
            if d > 0:
                d -= 1
                taille += len(lignes[d]) + 1
            if f < len(lignes) and taille < _CONTEXTE_MAX:
                taille += len(lignes[f]) + 1
                f += 1
    return "\n".join(x for x in lignes[d:f] if x)[:_CONTEXTE_MAX]


class TranslatorAI:
    def __init__(self):
        self.client = _client_deepseek()
        if self.client is None:
            raise ValueError("DEEPSEEK_API_KEY absente du fichier .env : "
                             "aucune traduction possible.")
        # Modèle de REPLI (fallback). Le vrai choix se fait PAR REQUÊTE via le
        # paramètre `quality` du formulaire (rapide vs précis) : app.py sélectionne
        # le modèle et le passe à translate_json(). Ici, défaut = rapide/stable.
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.max_retries = 3
        self.target_batch_size = 50

        self.system_instruction = """
        Tu es un traducteur expert spécialisé dans la localisation de documents structurés.

        RÈGLE DE REGISTRE — TRÈS IMPORTANTE :
        Adapte le registre au SUPPORT du fragment, pas seulement à son sens. Un
        bandeau de presse, un titre d'affiche, un libellé de bouton ou une
        notification obéissent à des formules CONSACRÉES : rends l'USAGE, pas le
        mot à mot (« BREAKING NEWS » en bandeau = « FLASH INFO » / « EN DIRECT »,
        jamais « dernières minutes » ; « Read more » = « Lire la suite »). Une
        traduction sémantiquement juste mais qui ne se dirait pas dans ce support
        est une FAUTE. Si un fragment porte un champ "consigne" de TERMINOLOGIE,
        elle prime sur ton choix spontané.

        RÈGLES CRITIQUES :
        1. PRÉSERVE les balises structurelles comme [[n]] et [[/n]] exactement à leur place.
        1-bis. COMPTE DES BALISES — NON NÉGOCIABLE : si le texte d'entrée contient
        les balises [[0]]…[[/0]] [[1]]…[[/1]] [[2]]…[[/2]], ta réponse DOIT contenir
        CES MÊMES TROIS balises, dans le même ordre, chacune avec sa part de la
        traduction. N'entasse JAMAIS toute la traduction dans [[0]] en omettant les
        suivantes : chaque balise porte une mise en forme différente (gras, taille,
        couleur) et une balise omise laisse ce morceau NON TRADUIT dans le document
        final. Si un morceau n'a pas d'équivalent dans la langue cible (un symbole
        qui disparaît, un accord qui se déplace), rends la balise VIDE — jamais
        absente.
        2. Traduis uniquement le contenu textuel à l'intérieur ou autour des balises.
        3. Ne modifie JAMAIS les identifiants "id".
        4. RÉPONDS UNIQUEMENT avec un objet JSON contenant une liste "translations".
        Chaque élément de la liste doit être un objet :
        {"id": "...", "translated_text": "..."}.

        RÈGLE D'ANCRAGE (nombres & symboles) — TRÈS IMPORTANTE :
        Si le texte ENTIER d'un fragment est uniquement un nombre, un seul symbole
        ou un jeton mathématique (ex. « 2 », « 11 », « n », « 2n », « O(k) », « k »),
        recopie-le À L'IDENTIQUE dans "translated_text". Ne lui attribue JAMAIS de
        mots ni de phrase. Ces fragments sont des points fixes immuables : ils
        servent d'ancres et empêchent le texte de glisser d'un fragment à l'autre.

        RÈGLE ANTI-EMPRUNT (anti-décalage) — TRÈS IMPORTANTE :
        Chaque fragment est une case FIGÉE : son "translated_text" est la traduction
        de SON PROPRE texte, jamais celle du fragment voisin. Si une phrase est
        coupée sur plusieurs fragments, répartis la traduction entre eux dans les
        mêmes proportions — n'entasse JAMAIS toute la phrase dans un seul fragment
        en laissant les suivants « emprunter » le contenu du fragment d'après. La
        sortie doit avoir EXACTEMENT le même nombre d'éléments que l'entrée, les
        mêmes "id", dans le même ordre, et AUCUN id ne doit être inventé ou omis.

        7. CÉSURES : un fragment peut se terminer par un mot coupé avec un trait
        d'union de fin de ligne (ex. « mo- » puis « dèle » au début du fragment
        suivant). Dans la traduction, reconstitue TOUJOURS les mots entiers : le
        fragment contenant le début du mot reçoit le mot complet (« modèle »), et
        le fragment suivant ne répète pas la fin du mot. N'écris JAMAIS de césure
        (trait d'union de coupure de ligne) dans "translated_text" — seuls les
        traits d'union lexicaux (« peut-être », « c'est-à-dire ») sont permis.
        5. LONGUEUR : la traduction remplace le texte dans une mise en page figée,
        elle doit donc occuper un espace aussi proche que possible de l'original.
        Par ordre de préférence : (1) même longueur ; (2) légèrement plus courte ;
        (3) plus longue — à éviter si une formulation équivalente plus compacte existe.
        INTERDICTIONS ABSOLUES : ne JAMAIS abréger des mots, ne JAMAIS tronquer ou
        omettre une partie du contenu, ne JAMAIS utiliser d'abréviations absentes du
        texte original, ne JAMAIS remplacer des mots par des symboles ou caractères
        de substitution (« & » au lieu de « et », « + » au lieu de « plus », « / » au
        lieu de « ou », chiffres au lieu de nombres écrits en lettres, etc.) ni aucune
        astuce typographique de ce genre : le résultat doit rester irréprochable dans
        un document professionnel. La traduction doit toujours être COMPLÈTE, naturelle
        et fidèle : la concision s'obtient uniquement par le choix de tournures et de
        synonymes naturellement plus courts, jamais en sacrifiant du contenu ni la
        qualité rédactionnelle.
        6. FIDÉLITÉ : n'AJOUTE rien que la source ne dise — ni explication, ni
        parenthèse, ni équivalent étranger, ni note. Un diplôme, une
        institution, un grade propre à un pays garde son NOM OFFICIEL (sigle
        compris), seuls ses mots communs traduits ; jamais remplacé par celui
        d'un autre pays, jamais par une glose.
        """

    def _clean_json_text(self, text):
        text = text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return text.strip()

    def _appel(self, messages, model, max_tokens, progress_callback):
        """Un appel de complétion DeepSeek. `model` peut porter un niveau de
        réflexion (« deepseek-flash:high », cf. decouper_modele)."""
        reste = _hors_service["jusqua"] - time.monotonic()
        if reste > 0 and _hors_service["statut"] == 429:
            time.sleep(reste)       # quota par minute : attendre, pas échouer
        elif reste > 0:
            raise FournisseursIndisponibles(
                "DeepSeek indisponible (solde, clé ou quota épuisé) : "
                "nouvel essai dans quelques minutes.")
        nom, effort = decouper_modele(model or self.model)
        kw = dict(model=nom, messages=messages, max_tokens=max_tokens,
                  response_format={"type": "json_object"})
        if effort:
            kw["reasoning_effort"] = effort
            kw["extra_body"] = {"thinking": {"type": "enabled"}}
            kw["max_tokens"] = max(max_tokens, _MAX_TOKENS_REFLEXION)
        else:
            kw["extra_body"] = {"thinking": {"type": "disabled"}}
            kw["temperature"] = 0.1    # sans effet en réflexion
        try:
            reponse = self.client.chat.completions.create(**kw)
        except Exception as e:
            statut = getattr(e, "status_code", None)
            if statut not in _HORS_SERVICE_S:
                raise                             # incident passager : relance
            if statut == 429 and _hors_service["statut"] != 429:
                _hors_service.update(jusqua=time.monotonic() + 60, statut=429)
                raise               # relancé par l'appelant, après la pause
            # 429 encore APRÈS la pause : quota durablement dépassé, on cesse
            # (« 429+ » : hors service, cf. hors_service).
            _hors_service.update(
                jusqua=time.monotonic() + _HORS_SERVICE_S[statut],
                statut="429+" if statut == 429 else statut)
            # Une alerte par SÉRIE de 402 : la série ne finit qu'à un appel
            # réussi, pas à l'échéance (sinon une alerte toutes les 10 min).
            premier = statut == 402 and not _hors_service["serie_402"]
            if premier:
                _hors_service["serie_402"] = True
                for rappel in list(sur_solde_epuise):
                    try:
                        rappel()
                    except Exception:
                        pass                      # une alerte ne bloque rien
            raise FournisseursIndisponibles(
                f"DeepSeek refuse la requête ({statut}) : "
                + ("solde épuisé, recharger le compte." if statut == 402
                   else "clé refusée." if statut in (401, 403)
                   else "quota momentanément dépassé.")) from e
        _hors_service["serie_402"] = False
        if _hors_service["statut"] == 429:          # pause tenue, quota rouvert
            _hors_service.update(jusqua=0.0, statut=None)
        _compter(model or self.model, getattr(reponse, "usage", None))
        return reponse

    def _translate_batch(self, batch, target_lang, progress_callback, retries=3,
                         model=None, max_tokens=8192, passes=1, cadre=None):
        """`passes` : passes de QUALITÉ encore autorisées (cf. _passe_qualite).
        Les scissions de lot le transmettent tel quel ; la passe de qualité le
        décrémente, ce qui borne la récursion.

        `cadre` : ce que le lot ne dit pas de lui-même — {"fiche": fiche de
        document, "page": texte de la page en ordre de lecture, "n_page":
        nombre de fragments de la page}. Transmis aux scissions et à la passe
        de qualité, qui en ont le plus besoin (quelques fragments isolés)."""
        if not batch:
            return True

        items = []
        for b in batch:
            it = {"id": b["id"], "text": b["text"]}
            # SUPPORT joint à TOUT fragment. Savoir qu'un fragment est un titre
            # / un bandeau / un libellé suffit au modèle pour choisir la formule
            # consacrée plutôt que la traduction mot à mot. Coût : quelques
            # jetons par item.
            if b.get("support"):
                it["support"] = b["support"]
            consignes = []
            if b.get("consigne"):
                # Consigne PAR ITEM (ex. budget de caractères pour une
                # retraduction compacte) — voir translate.retranslate_overflows.
                consignes.append(b["consigne"])
            if consignes:
                it["consigne"] = "\n".join(consignes)
            items.append(it)

        prompt = (f"Traduis ces éléments vers la langue : {target_lang}. "
                  "Conserve la structure JSON et les balises [[n]]. "
                  "Le champ \"support\" dit CE QU'EST le fragment dans la page : "
                  "\"titre\" = titre, bandeau, affiche, libellé ou notification "
                  "(texte court au grand corps) → emploie la formule CONSACRÉE de "
                  "ce support, pas la traduction mot à mot ; \"corps\" = phrase "
                  "courante → registre normal. "
                  "Si un élément comporte un champ \"consigne\", applique-la "
                  "STRICTEMENT (par exemple une longueur maximale à respecter "
                  "en reformulant, jamais en abrégeant ni en omettant du sens)."
                  f"\n\n{json.dumps(items, ensure_ascii=False)}")
        cadre = cadre or {}
        # Page entière déjà dans le lot : son texte n'apprendrait rien.
        if cadre.get("page") and len(batch) < cadre.get("n_page", 0):
            prompt = ("CONTEXTE DE LA PAGE — texte complet dans l'ordre de "
                      "lecture, titres marqués « # ». Il sert à COMPRENDRE les "
                      "fragments ; ne le traduis pas, ne le renvoie pas.\n"
                      f"<<<\n{cadre['page']}\n>>>\n\n" + prompt)
        systeme = self.system_instruction
        if cadre.get("fiche"):
            systeme += ("\n\nFICHE DU DOCUMENT — sert à choisir les mots "
                        "justes (domaine, pays, registre). Elle ne s'ajoute "
                        "JAMAIS au texte : aucune explication, parenthèse ni "
                        f"équivalent absent de la source.\n{cadre['fiche']}")

        for attempt in range(retries):
            try:
                # max_tokens dépend du mode (passé par translate_json) :
                #  • rapide (non-raisonnant)  → 8192 suffit ;
                #  • précis (raisonnement)    → 65536, car le modèle consomme
                #    d'abord ~12-14k tokens de "réflexion" AVANT d'écrire la
                #    réponse ; un budget trop bas tronque tout avant la sortie.
                response = self._appel(
                    [{"role": "system", "content": systeme},
                     {"role": "user", "content": prompt}],
                    model, max_tokens, progress_callback)

                # Réponse coupée à max_tokens : le JSON est tronqué, inutile de
                # réessayer le même lot — on le scinde en deux directement.
                if response.choices[0].finish_reason == "length":
                    if len(batch) > 1:
                        if progress_callback:
                            progress_callback(f"Lot trop grand ({len(batch)} blocs), scission en deux.")
                        mid = len(batch) // 2
                        return (self._translate_batch(batch[:mid], target_lang, progress_callback, retries, model, max_tokens, passes, cadre)
                                and self._translate_batch(batch[mid:], target_lang, progress_callback, retries, model, max_tokens, passes, cadre))
                    if max_tokens < 32768:
                        # Bloc unique tronqué : doubler le budget de SORTIE
                        # avant d'abandonner (le modèle divague parfois avant de
                        # fermer le JSON ; un budget plus large le laisse finir).
                        max_tokens = min(32768, max_tokens * 2)
                        if progress_callback:
                            progress_callback(
                                f"Bloc unique tronqué : budget de sortie porté à {max_tokens}.")
                        continue
                    raise ValueError("Réponse tronquée (max_tokens atteint) sur un bloc unique.")

                content = response.choices[0].message.content
                if not content:
                    raise ValueError("Réponse vide du modèle.")

                translated_data = json.loads(self._clean_json_text(content))

                # Le modèle ne respecte pas toujours {"translations": [...]} :
                # il renvoie parfois une liste à la racine, ou un objet avec une
                # autre clé, ou un mapping direct {id: texte}. On accepte tout.
                if isinstance(translated_data, list):
                    translated_results = translated_data
                elif isinstance(translated_data, dict):
                    translated_results = translated_data.get("translations")
                    if translated_results is None:
                        # n'importe quelle clé contenant une liste de dicts
                        translated_results = next(
                            (v for v in translated_data.values() if isinstance(v, list)),
                            None
                        )
                    if translated_results is None and "id" in translated_data:
                        # objet NU d'un résultat unique (fréquent sur un lot
                        # d'un seul item) : {"id": "...", "translated_text": "..."}
                        translated_results = [translated_data]
                    if translated_results is None:
                        # mapping direct {id: texte_traduit}
                        translated_results = [
                            {"id": k, "translated_text": v}
                            for k, v in translated_data.items() if isinstance(v, str)
                        ]
                else:
                    raise ValueError(f"Format de réponse inattendu : {type(translated_data).__name__}")

                res_dict = {}
                for res in translated_results:
                    if isinstance(res, dict) and "id" in res:
                        res_dict[res["id"]] = res.get("translated_text") or res.get("text", "")

                # Moteur stable bloc par bloc : chaque bloc reçoit SA traduction,
                # aucune notion de paragraphe ni de regroupement.
                count = 0
                for b in batch:
                    if b["id"] in res_dict:
                        b["translated_text"] = res_dict[b["id"]]
                        count += 1

                if count == 0 and len(batch) > 0:
                    raise ValueError(f"Aucune correspondance d'ID trouvée dans la réponse. Début de la réponse : {content[:200]}")

                self._passe_qualite(batch, target_lang, progress_callback,
                                    retries, model, max_tokens, passes, cadre)
                return True

            except FournisseursIndisponibles as e:
                # Ni relance ni scission : chaque morceau échouerait pareil.
                if progress_callback:
                    progress_callback(f"⚠️ {e}")
                return False
            except Exception as e:
                if progress_callback:
                    progress_callback(f"⚠️ Erreur lot (tentative {attempt+1}/{retries}) : {e}")
                time.sleep(2 ** attempt)

        # Dernier recours : scinder le lot (un JSON tronqué ou malformé sur un
        # gros lot passe souvent une fois divisé).
        if len(batch) > 1:
            if progress_callback:
                progress_callback(f"Échec du lot de {len(batch)} blocs, scission en deux.")
            mid = len(batch) // 2
            return (self._translate_batch(batch[:mid], target_lang, progress_callback, retries, model, max_tokens, passes, cadre)
                    and self._translate_batch(batch[mid:], target_lang, progress_callback, retries, model, max_tokens, passes, cadre))
        return False

    # ── Passe de QUALITÉ ─────────────────────────────────────────────────────
    #
    # Le modèle se trompe parfois de FORME, pas de sens : il oublie des balises,
    # recopie un morceau sans le traduire, ou change un chiffre. Ces trois écarts
    # se CONSTATENT sans rien connaître du document (cf. runtags.defauts) — et
    # aucun ne se répare en aval sans deviner.
    #
    # On ne devine donc pas : on REDEMANDE. C'est le principe du moteur PDF —
    # mesurer un invariant, et refaire le travail quand il est violé, plutôt que
    # rafistoler la sortie avec des seuils calés sur un document.
    #
    # Coût : un appel supplémentaire pour les seuls fragments fautifs. Un faux
    # positif (« Almeida » qui ne se traduit pas) coûte cet appel et rend le même
    # texte — jamais une suppression.
    _CONSIGNES_DEFAUT = {
        "balises": ("Ta réponse précédente a OMIS des balises [[n]]. Rends "
                    "EXACTEMENT les mêmes balises que le texte d'entrée, dans "
                    "le même ordre, chacune avec sa part de la traduction. Une "
                    "balise sans équivalent dans la langue cible doit être VIDE, "
                    "jamais absente."),
        "recopie": ("Ta réponse précédente a RECOPIÉ un morceau à l'identique "
                    "alors que les autres étaient traduits. Traduis CHAQUE "
                    "morceau. Si un morceau est un nom propre, un sigle ou une "
                    "date qui ne se traduit pas, laisse-le tel quel — c'est "
                    "légitime — mais vérifie qu'il ne s'agit pas d'un oubli."),
        "nombres": ("Ta réponse précédente a MODIFIÉ un nombre. Les chiffres "
                    "d'un document sont des données : reporte-les à l'identique, "
                    "y compris dans les ordinaux (« 6ème » → « 6th », jamais "
                    "« 5th »)."),
        "ajout": ("Ta réponse précédente a AJOUTÉ une parenthèse absente de "
                  "la source (explication, équivalent, note). Traduis "
                  "seulement ce qui est écrit, sans rien ajouter."),
    }

    def _passe_qualite(self, batch, target_lang, progress_callback, retries,
                       model, max_tokens, passes, cadre=None):
        """Redemande les fragments dont la FORME est fautive."""
        if passes <= 0:
            return
        fautifs = []
        for b in batch:
            out = b.get("translated_text")
            if not out:
                continue
            maux = runtags.defauts(b["text"], out)
            if maux:
                b["consigne"] = "\n".join(self._CONSIGNES_DEFAUT[m]
                                          for m in maux
                                          if m in self._CONSIGNES_DEFAUT)
                fautifs.append(b)
        if not fautifs:
            return
        if progress_callback:
            progress_callback(f"contrôle : {len(fautifs)} fragment(s) à "
                              f"reprendre (forme).")
        self._translate_batch(fautifs, target_lang, progress_callback, retries,
                              model, max_tokens, passes - 1, cadre)

    # Nom ANGLAIS de la langue cible, injecté dans le prompt. Un code absent de
    # cette table y partirait tel quel (« Translate to sv-SE »), ce qui est une
    # consigne bien plus faible qu'un nom de langue explicite.
    #
    # Les VARIANTES régionales sont nommées une par une : « British English »
    # est une consigne que le modèle sait suivre (colour, whilst, 12/03/2026),
    # là où « en-GB » n'en est pas vraiment une. C'est ce nommage qui rend le
    # choix de variante réellement effectif — sans lui, en-GB et en-US
    # produisent le même texte. Clés en minuscules (cf. lang_name).
    _LANG_NAMES = {
        # Bases
        "fr": "French", "en": "English", "es": "Spanish", "de": "German",
        "it": "Italian", "pt": "Portuguese", "ar": "Arabic", "zh": "Chinese",
        "ja": "Japanese", "ko": "Korean", "ru": "Russian", "nl": "Dutch",
        "pl": "Polish", "ro": "Romanian", "cs": "Czech", "hu": "Hungarian",
        "tr": "Turkish", "sv": "Swedish", "da": "Danish", "nb": "Norwegian",
        "fi": "Finnish", "uk": "Ukrainian", "el": "Greek",
        # Variantes régionales
        "en-us": "American English (US spelling and conventions)",
        "en-gb": "British English (UK spelling and conventions)",
        "en-ca": "Canadian English",
        "en-au": "Australian English",
        "fr-fr": "French (France)",
        "fr-ca": "Canadian French (Québec usage)",
        "fr-be": "French (Belgium)",
        "fr-ch": "French (Switzerland)",
        "es-es": "European Spanish (Castilian, Spain)",
        "es-mx": "Mexican Spanish",
        "es-ar": "Argentine Spanish (rioplatense, voseo)",
        "de-de": "German (Germany)",
        "de-at": "Austrian German",
        "de-ch": "Swiss German (Swiss standard German, no ß)",
        "pt-pt": "European Portuguese (Portugal)",
        "pt-br": "Brazilian Portuguese",
        "nl-nl": "Dutch (Netherlands)",
        "nl-be": "Flemish (Dutch, Belgium)",
        "zh-cn": "Simplified Chinese",
        "zh-tw": "Traditional Chinese",
    }


    @classmethod
    def lang_name(cls, code):
        """Nom de langue à injecter dans le prompt. Reconnaît la variante
        ('en-GB' → British English) et, à défaut, retombe sur la langue de base
        ('en-NZ' → English) plutôt que de laisser passer un code brut."""
        c = str(code).strip().lower()
        if c in cls._LANG_NAMES:
            return cls._LANG_NAMES[c]
        base = c.split("-")[0]
        return cls._LANG_NAMES.get(base, code)

    def fiche_document(self, titres, textes, target_lang, model=None,
                       progress_callback=None):
        """FICHE DE DOCUMENT : un appel, sans réflexion, qui DÉCRIT le document
        (nature, pays et système d'origine, public, registre, termes propres et
        le mot à employer pour chacun en langue cible). Elle oriente le choix
        des mots de chaque lot ; elle ne traduit rien.

        `titres` : intitulés repérés par la mise en page ; `textes` : tout le
        texte, dont on prend un échantillon RÉPARTI. Renvoie "" en cas
        d'échec : sans fiche la traduction se fait comme avant, elle n'est
        jamais bloquée pour autant."""
        titres = [" ".join(t.split()) for t in titres if t and t.strip()][:60]
        corps = echantillon_reparti(textes)
        if not titres and not corps:
            return ""
        nom = decouper_modele(model or self.model)[0]   # jamais de réflexion
        consigne = (
            "Voici des extraits d'un document à traduire vers "
            f"{self.lang_name(target_lang)}. Ne traduis RIEN : décris-le pour "
            "le traducteur, en français, en 150 mots au plus :\n"
            "- nature du document, domaine, pays et système d'origine "
            "(scolaire, juridique, administratif…), public, registre ;\n"
            "- termes PROPRES à ce contexte : pour chacun, le terme UNIQUE à "
            f"employer en {self.lang_name(target_lang)}, jamais une "
            "explication. Notion technique ou courante : son terme usuel. "
            "Diplôme, examen, institution, grade, titre ou désignation "
            "administrative du système d'origine : son NOM OFFICIEL conservé "
            "(sigle compris) mais ses mots communs TRADUITS (spécialité, "
            "matière, langue : « Licence de Droit » donne en anglais "
            "« Licence in Law ») — JAMAIS le diplôme ou l'organisme "
            "« équivalent » d'un autre pays, qui changerait le fait rapporté. "
            "Les seuls termes ambigus ou propres ; pas le vocabulaire banal.\n"
            'Réponds en JSON : {"fiche": "..."}.\n\n'
            "TITRES :\n" + "\n".join(titres) +
            "\n\nEXTRAITS (pris sur tout le document) :\n" + "\n".join(corps))
        try:
            rep = self._appel([{"role": "user", "content": consigne}], nom,
                              1024, progress_callback)
            fiche = json.loads(self._clean_json_text(
                rep.choices[0].message.content or "")).get("fiche", "")
            return str(fiche).strip()[:_FICHE_MAX]
        except Exception as e:
            # Solde épuisé compris : les lots le constateront et s'arrêteront
            # proprement ; la fiche n'a pas à interrompre l'appelant.
            if progress_callback:
                progress_callback(f"⚠️ Fiche de document indisponible : {e}")
            return ""

    def translate_json(self, json_path, target_lang="en", progress_callback=None, limit=None,
                       model=None, max_tokens=8192, fiche=None):
        """`fiche` : fiche de document déjà écrite (PPTX traduit diapositive
        par diapositive : une seule fiche pour tout le fichier). None = l'écrire
        d'après ce JSON ; "" = s'en passer."""
        target_lang = self.lang_name(target_lang)
        if not os.path.exists(json_path):
            return False, "Fichier JSON introuvable."

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        batches = []

        if "pages" in data:
            if progress_callback: progress_callback("Mode PDF : Groupement par page détecté.")
            for page in data["pages"]:
                page_blocks = [b for b in page.get("text_blocks", []) if b.get("text", "").strip()]
                if page_blocks:
                    batches.append(page_blocks)

        elif "slides" in data:
            if progress_callback: progress_callback("Mode PPTX : Groupement par slide détecté.")
            for slide in data["slides"]:
                slide_blocks = []
                for block in slide.get("text_elements", []):
                    if block.get("text", "").strip(): slide_blocks.append(block)
                for diag in slide.get("diagram_elements", []):
                    for block in diag.get("text_elements", []):
                        if block.get("text", "").strip(): slide_blocks.append(block)
                for chart in slide.get("chart_elements", []):
                    for block in chart.get("text_elements", []):
                        if block.get("text", "").strip(): slide_blocks.append(block)
                for layout in slide.get("layout_elements", []):
                    for block in layout.get("text_elements", []):
                        if block.get("text", "").strip(): slide_blocks.append(block)
                for block in slide.get("excel_elements", []):
                    if block.get("text", "").strip(): slide_blocks.append(block)

                if slide_blocks:
                    batches.append(slide_blocks)

        elif "workbook" in data:
            # ── Mode XLSX ────────────────────────────────────────────────────
            # Un classeur n'a ni pages ni diapositives : son relevé est une
            # LISTE PLATE de chaînes, chacune portant son identité (partie +
            # index) dans `context`. On la découpe donc en lots de taille fixe.
            #
            # POURQUOI UNE BRANCHE À PART, ET NON UN RELEVÉ DÉGUISÉ EN `document`
            # ------------------------------------------------------------------
            # Sans elle, `workbook` tombait dans la branche DOCX, qui lit
            # `data["document"]` — absent. Aucun lot n'était formé et la
            # traduction s'arrêtait sur « Aucun texte à traduire ». Le classeur
            # ne ressortait donc PAS silencieusement inchangé (c'est déjà ça),
            # mais il ne se traduisait pas.
            #
            # Le remède inverse — renommer `workbook` en `document` dans le
            # moteur XLSX — a été écarté : le relevé dirait alors qu'un classeur
            # est un document, et l'injection, qui relit `workbook`, devrait
            # mentir de la même façon. On préfère une branche qui NOMME le
            # format à un schéma qui le travestit.
            #
            # Le GROUPEMENT est volontairement plat. Regrouper par feuille
            # serait plus proche de l'esprit des autres formats, mais le magasin
            # `sharedStrings` est partagé PAR TOUT LE CLASSEUR : une même chaîne
            # y sert plusieurs feuilles, et aucune ne peut la revendiquer.
            if progress_callback:
                progress_callback("Mode XLSX : groupement des chaînes du classeur.")
            wb_blocks = [b for b in data["workbook"].get("elements", [])
                         if b.get("text", "").strip()]
            for i in range(0, len(wb_blocks), self.target_batch_size):
                batches.append(wb_blocks[i:i + self.target_batch_size])

        else:
            if progress_callback: progress_callback("Mode DOCX : Groupement par sections et éléments détecté.")
            doc_data = data.get("document", {})
            for section in ["elements", "headers", "footers", "textboxes", "smartarts"]:
                section_blocks = [b for b in doc_data.get(section, []) if b.get("text", "").strip()]
                if not section_blocks:
                    continue

                if section == "elements":
                    current_group = []
                    last_type = None

                    for b in section_blocks:
                        b_type = b.get("context", {}).get("type", "paragraph")

                        if last_type and b_type != last_type and len(current_group) >= self.target_batch_size:
                            batches.append(current_group)
                            current_group = []

                        current_group.append(b)
                        last_type = b_type

                        if len(current_group) >= self.target_batch_size:
                            batches.append(current_group)
                            current_group = []

                    if current_group:
                        batches.append(current_group)
                else:
                    for i in range(0, len(section_blocks), self.target_batch_size):
                        batches.append(section_blocks[i:i+self.target_batch_size])

        if limit and isinstance(limit, int):
            flat_all = [b for batch in batches for b in batch]
            batches = [flat_all[:limit]]
            if progress_callback:
                progress_callback(f"Mode TEST : Traduction limitée aux {limit} premiers blocs.")

        total_batches = len(batches)
        if total_batches == 0:
            return False, "Aucun texte à traduire."

        if fiche is None:
            fiche = self.fiche_document(
                [], [runtags.sans_balises(b["text"]) for lot in batches for b in lot],
                target_lang, model, progress_callback)
        # Le lot EST l'unité de contexte (diapositive, suite de paragraphes,
        # chaînes du classeur) : son texte ne part qu'avec ses morceaux
        # isolés (scission, passe de qualité).
        cadres = [{"fiche": fiche, "n_page": len(lot),
                   "page": contexte_page([runtags.sans_balises(b["text"])
                                          for b in lot])}
                  for lot in batches]

        if progress_callback:
            progress_callback(f"Début de la traduction ({total_batches} lots structurels)...")

        # Parallélisation des lots : plusieurs pages traduits simultanément.
        # MAX_WORKERS limité à 4 pour respecter les rate-limits DeepSeek et
        # éviter les collisions mémoire sur les gros documents. Le progress_callback
        # est thread-safe (GIL + appel simple sans état partagé).
        MAX_WORKERS = min(4, total_batches)
        failed_batch = [None]
        failed_lock = threading.Lock()
        batches_done = [0]
        done_lock = threading.Lock()

        def _run(idx_batch):
            idx, batch = idx_batch
            with failed_lock:
                if failed_batch[0] is not None:
                    return idx, False
            success = self._translate_batch(batch, target_lang, progress_callback,
                                            model=model, max_tokens=max_tokens,
                                            cadre=cadres[idx])
            with done_lock:
                batches_done[0] += 1
                n = batches_done[0]
            if progress_callback:
                progress_callback(f"Progression : {n}/{total_batches} lots traités.")
            return idx, success

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(_run, (i, b)): i for i, b in enumerate(batches)}
            for future in as_completed(futures):
                idx, success = future.result()
                if not success:
                    with failed_lock:
                        if failed_batch[0] is None:
                            failed_batch[0] = idx + 1

        if failed_batch[0] is not None:
            return False, f"Échec lors de la traduction du lot {failed_batch[0]}."

        output_path = json_path.replace(".json", "_translated.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if progress_callback:
            progress_callback("Traduction terminée avec succès !")

        return True, output_path
