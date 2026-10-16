import os
import json
import time
import threading
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from dotenv import load_dotenv

try:
    from . import runtags                       # importé en paquet
except ImportError:                             # ... ou à plat (sys.path backend)
    import runtags

load_dotenv()

class FournisseursIndisponibles(Exception):
    """Aucun fournisseur ne peut répondre (solde, clé ou quota épuisés).
    Ni une relance ni une scission du lot n'y changent rien : on s'arrête."""


# ── Fournisseurs de traduction ───────────────────────────────────────────────
#
# Un solde épuisé (402) bloquait TOUTE l'app, et pire : l'erreur était traitée
# comme un incident passager — 3 relances, puis scission du lot en deux,
# récursivement, soit des dizaines d'appels voués à l'échec par page.
#
# Ces statuts disent que CE fournisseur ne répondra pas tout de suite. On le
# met donc hors service un moment et on passe au suivant, sans relancer.
# Durées : un quota par minute (429) se rouvre vite ; un solde ou une clé
# (401/402/403) demande une action humaine, inutile de réessayer avant.
_HORS_SERVICE_S = {401: 600, 402: 600, 403: 600, 429: 60}


class _ErreurDansLeCorps(Exception):
    """OpenRouter répond parfois 200 avec un corps `{"error": {...}}` et
    aucun `choices` (erreur de l'hébergeur relayée telle quelle)."""
    def __init__(self, erreur):
        erreur = erreur if isinstance(erreur, dict) else {}
        code = erreur.get("code")
        self.status_code = code if isinstance(code, int) else None
        super().__init__(f"réponse sans traduction ({code}) : "
                         f"{str(erreur.get('message', ''))[:200]}")

# Échéances PARTAGÉES par nom de fournisseur : le moteur PDF crée un
# TranslatorAI par page ; un état porté par l'instance oublierait le 402 à la
# page suivante et redépenserait une requête (et du quota) pour le réapprendre.
_hors_service: dict[str, float] = {}


class _Debit:
    """Au plus `par_minute` requêtes sur toute fenêtre glissante de 60 s.

    Les modèles :free d'OpenRouter plafonnent à 20 requêtes/min ; les pages
    traduites en parallèle dépassaient ce plafond (essai réel : 37 requêtes
    puis 429, et le secours tombait hors service). Mieux vaut attendre son
    tour que recevoir une 429. Partagé entre threads, comme `_hors_service`."""
    def __init__(self, par_minute):
        self.par_minute = par_minute
        self._envois = deque()
        self._verrou = threading.Lock()

    def attendre(self):
        """Bloque jusqu'à ce qu'un envoi soit permis ; renvoie l'attente (s)."""
        attendu = 0.0
        while True:
            with self._verrou:
                maintenant = time.monotonic()
                while self._envois and maintenant - self._envois[0] >= 60:
                    self._envois.popleft()
                if len(self._envois) < self.par_minute:
                    self._envois.append(maintenant)
                    return attendu
                pause = 60 - (maintenant - self._envois[0])
            # Hors du verrou : les autres threads voient la file pendant ce temps.
            time.sleep(pause)
            attendu += pause


_debits: dict[str, _Debit] = {}


class _Fournisseur:
    def __init__(self, nom, client, modele=None, modeles_secours=(),
                 max_sortie=None, corps_extra=None, par_minute=0):
        self.nom = nom
        self.client = client
        # None = le modèle demandé par l'appelant (DeepSeek : rapide/précis).
        # Un secours a SON modèle : un nom DeepSeek n'existe pas ailleurs.
        self.modele = modele
        self.modeles_secours = list(modeles_secours)
        self.max_sortie = max_sortie
        # Champs ajoutés au corps de chaque requête (propres au fournisseur).
        self.corps_extra = dict(corps_extra or {})
        # 0 = aucune limite. Sinon, limiteur PARTAGÉ par nom (un TranslatorAI
        # par page : un limiteur par instance ne limiterait rien).
        self.debit = None
        if par_minute > 0:
            d = _debits.get(nom)
            if d is None or d.par_minute != par_minute:
                d = _debits[nom] = _Debit(par_minute)
            self.debit = d

    @property
    def hors_service_jusqua(self):
        return _hors_service.get(self.nom, 0.0)

    @hors_service_jusqua.setter
    def hors_service_jusqua(self, t):
        _hors_service[self.nom] = t


def _entete_ascii(valeur):
    """Un en-tête HTTP n'accepte que l'ASCII : « PRÉCIS » ferait échouer
    CHAQUE requête avant l'envoi. On retire les accents."""
    return (unicodedata.normalize("NFKD", valeur)
            .encode("ascii", "ignore").decode("ascii"))


def _fournisseurs_depuis_env():
    """Chaîne ordonnée : DeepSeek (principal) puis OpenRouter (secours).
    `TRADUCTION_FOURNISSEURS` (ex. « openrouter,deepseek ») réordonne ou
    restreint la chaîne — le banc de comparaison s'en sert."""
    dispo = {}
    cle = os.getenv("DEEPSEEK_API_KEY")
    if cle:
        dispo["deepseek"] = _Fournisseur("deepseek", OpenAI(
            api_key=cle,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=120.0))
    cle = os.getenv("OPENROUTER_API_KEY")
    if cle:
        # Plusieurs modèles : OpenRouter essaie le suivant si le premier est
        # indisponible chez son hébergeur (paramètre `models`). Le quota
        # quotidien des modèles :free, lui, est commun au compte.
        modeles = [m.strip() for m in os.getenv(
            "OPENROUTER_MODELS", "google/gemma-4-31b-it:free").split(",")
            if m.strip()]
        dispo["openrouter"] = _Fournisseur(
            "openrouter",
            OpenAI(api_key=cle,
                   base_url=os.getenv("OPENROUTER_BASE_URL",
                                      "https://openrouter.ai/api/v1"),
                   timeout=180.0,
                   default_headers={"X-Title": _entete_ascii(
                       os.getenv("BRAND_NAME", "PRÉCIS"))}),
            modele=modeles[0], modeles_secours=modeles[1:],
            max_sortie=int(os.getenv("OPENROUTER_MAX_TOKENS", "32768")),
            # Traduire ne demande aucun raisonnement : actif, il multiplie la
            # durée par ~5 sur les modèles « qui réfléchissent » (Nemotron :
            # 173 s contre 37 s pour la même page). Modèles sans raisonnement :
            # champ ignoré.
            corps_extra=({} if os.getenv("OPENROUTER_RAISONNEMENT", "0") == "1"
                         else {"reasoning": {"enabled": False}}),
            # Plafond des modèles :free : 20/min. Marge pour les requêtes
            # d'autres outils (banc) sur la même clé. 0 = pas de limite.
            par_minute=int(os.getenv("OPENROUTER_REQ_PAR_MIN", "16")))
    ordre = [n.strip() for n in os.getenv(
        "TRADUCTION_FOURNISSEURS", "deepseek,openrouter").split(",") if n.strip()]
    return [dispo[n] for n in ordre if n in dispo]


class TranslatorAI:
    def __init__(self):
        self.fournisseurs = _fournisseurs_depuis_env()
        if not self.fournisseurs:
            raise ValueError("Aucune clé d'API de traduction (DEEPSEEK_API_KEY "
                             "ou OPENROUTER_API_KEY) dans le fichier .env")
        # Modèle de REPLI (fallback). Le vrai choix se fait PAR REQUÊTE via le
        # paramètre `quality` du formulaire (rapide vs précis) : app.py sélectionne
        # le modèle et le passe à translate_json(). Ici, défaut = rapide/stable.
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
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
        """

    def _clean_json_text(self, text):
        text = text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return text.strip()

    def _appel(self, messages, model, max_tokens, progress_callback):
        """Un appel de complétion, sur le premier fournisseur en service."""
        fournisseurs = getattr(self, "fournisseurs", None)
        if fournisseurs is None:
            # Instance montée à la main (tests) : un seul client, sans chaîne.
            return self.client.chat.completions.create(
                model=model or self.model, messages=messages, temperature=0.1,
                max_tokens=max_tokens, response_format={"type": "json_object"})
        ecartes = []
        for f in fournisseurs:
            if time.monotonic() < f.hors_service_jusqua:
                ecartes.append(f"{f.nom} (hors service)")
                continue
            kw = dict(model=f.modele or model or self.model, messages=messages,
                      temperature=0.1, max_tokens=max_tokens,
                      response_format={"type": "json_object"})
            if f.max_sortie:
                kw["max_tokens"] = min(max_tokens, f.max_sortie)
            extra = dict(f.corps_extra)
            if f.modeles_secours:
                extra["models"] = [f.modele] + f.modeles_secours
            if extra:
                kw["extra_body"] = extra
            if f.debit:
                attendu = f.debit.attendre()
                if attendu >= 1 and progress_callback:
                    progress_callback(f"Limite de débit {f.nom} : "
                                      f"{attendu:.0f} s d'attente.")
            try:
                reponse = f.client.chat.completions.create(**kw)
                if not getattr(reponse, "choices", None):
                    raise _ErreurDansLeCorps(getattr(reponse, "error", None))
                return reponse
            except Exception as e:
                statut = getattr(e, "status_code", None)
                if statut not in _HORS_SERVICE_S:
                    raise                     # incident passager : relance
                f.hors_service_jusqua = time.monotonic() + _HORS_SERVICE_S[statut]
                ecartes.append(f"{f.nom} ({statut})")
                if progress_callback:
                    progress_callback(f"Fournisseur {f.nom} indisponible "
                                      f"({statut}) : bascule sur le suivant.")
        raise FournisseursIndisponibles(
            "Aucun fournisseur de traduction disponible (solde, clé ou quota "
            "épuisé) : " + ", ".join(ecartes))

    def _translate_batch(self, batch, target_lang, progress_callback, retries=3,
                         model=None, max_tokens=8192, passes=1):
        """`passes` : passes de QUALITÉ encore autorisées (cf. _passe_qualite).
        Les scissions de lot le transmettent tel quel ; la passe de qualité le
        décrémente, ce qui borne la récursion."""
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

        for attempt in range(retries):
            try:
                # max_tokens dépend du mode (passé par translate_json) :
                #  • rapide (non-raisonnant)  → 8192 suffit ;
                #  • précis (raisonnement)    → 65536, car le modèle consomme
                #    d'abord ~12-14k tokens de "réflexion" AVANT d'écrire la
                #    réponse ; un budget trop bas tronque tout avant la sortie.
                response = self._appel(
                    [{"role": "system", "content": self.system_instruction},
                     {"role": "user", "content": prompt}],
                    model, max_tokens, progress_callback)

                # Réponse coupée à max_tokens : le JSON est tronqué, inutile de
                # réessayer le même lot — on le scinde en deux directement.
                if response.choices[0].finish_reason == "length":
                    if len(batch) > 1:
                        if progress_callback:
                            progress_callback(f"Lot trop grand ({len(batch)} blocs), scission en deux.")
                        mid = len(batch) // 2
                        return (self._translate_batch(batch[:mid], target_lang, progress_callback, retries, model, max_tokens, passes)
                                and self._translate_batch(batch[mid:], target_lang, progress_callback, retries, model, max_tokens, passes))
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

                # Un modèle de secours peut ignorer `response_format` et
                # entourer le JSON d'une clôture ```json.
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
                                    retries, model, max_tokens, passes)
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
            return (self._translate_batch(batch[:mid], target_lang, progress_callback, retries, model, max_tokens, passes)
                    and self._translate_batch(batch[mid:], target_lang, progress_callback, retries, model, max_tokens, passes))
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
    }

    def _passe_qualite(self, batch, target_lang, progress_callback, retries,
                       model, max_tokens, passes):
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
                              model, max_tokens, passes - 1)

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

    def translate_json(self, json_path, target_lang="en", progress_callback=None, limit=None,
                       model=None, max_tokens=8192):
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
                                            model=model, max_tokens=max_tokens)
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
