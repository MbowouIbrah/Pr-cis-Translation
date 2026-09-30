"""
Client Campay — encaissement mobile money (MTN MoMo, Orange Money).

**Pourquoi pas le SDK officiel `campay` (PyPI).** Il appelle `requests` avec
`verify=False` sur *tous* ses endpoints, jeton d'authentification compris : la
vérification du certificat TLS est désactivée, donc n'importe qui en position
d'intercepter le trafic peut lire nos identifiants marchand et rejouer nos
encaissements. Il est aussi entièrement synchrone et attend en boucle bloquante
(`while status == "PENDING"`), ce qui figerait la boucle d'événements — le
problème même qu'on vient de corriger ailleurs.

Ce client parle donc directement à la même API HTTP, en asynchrone et avec la
vérification TLS active.

Endpoints (relevés dans le SDK officiel v1.1.0, 19/07/2026) :
  POST /api/token/            {username, password}          → {token}
  POST /api/collect/          {amount, currency, from, description, external_reference}
                                                            → {reference, ussd_code, operator}
  GET  /api/transaction/<ref> Authorization: Token <token>   → {status, ...}
"""
from __future__ import annotations

import functools
import logging
import os
import time

import httpx
import jwt

logger = logging.getLogger(__name__)

# Codes d'erreur Campay, traduits pour l'utilisateur. Renvoyer « ER101 » à
# quelqu'un qui vient de taper son numéro ne l'aide pas à le corriger.
ERROR_MESSAGES = {
    "ER101": "Numéro invalide — indiquez l'indicatif pays (237…).",
    "ER102": "Opérateur non pris en charge : seuls MTN et Orange sont acceptés.",
    "ER201": "Montant invalide : les centimes ne sont pas acceptés en FCFA.",
    "ER301": "Solde insuffisant.",
}

DEMO_HOST = "https://demo.campay.net"
PROD_HOST = "https://www.campay.net"

# Campay ne connaît que le franc CFA. Une zone tarifaire qui facture en EUR ou
# en USD ne peut PAS être encaissée ici — il lui faut un autre fournisseur.
SUPPORTED_CURRENCY = "XAF"


def normalize_phone(raw: str, default_cc: str = "237") -> str:
    """Met un numéro saisi à la main au format attendu par Campay.

    Campay exige l'indicatif pays et refuse tout le reste par ER101 — mesuré :
    `698358935` (la façon dont un Camerounais écrit son numéro) est rejeté,
    `237698358935` passe. Laisser l'utilisateur découvrir ça par un message
    d'erreur, c'est perdre un paiement sur un détail de présentation.

    On ne devine RIEN d'ambigu : seuls les 9 chiffres du plan de numérotation
    camerounais (commençant par 6) reçoivent l'indicatif. Un numéro déjà
    préfixé, ou d'un autre pays, passe intact — mieux vaut une erreur de Campay
    qu'un préfixe inventé qui enverrait l'argent ailleurs.
    """
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 9 and digits.startswith("6"):
        return default_cc + digits
    return digits


class CampayError(RuntimeError):
    """Échec d'un appel Campay.

    `message` va aux journaux (il peut contenir des détails de l'API) ;
    `user_message` est ce qu'on peut montrer sans rien divulguer — et surtout
    ce qui permet à l'utilisateur de CORRIGER. « ER101 » ne dit à personne
    qu'il a oublié l'indicatif pays.
    """

    def __init__(self, message: str, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or (
            "Le paiement n'a pas pu être initié. Réessayez."
        )


def _as_campay_error(fn):
    """Convertit toute panne de transport en `CampayError`.

    Mesuré, pas supposé : une interrogation d'état a levé `httpx.ReadTimeout`
    en conditions réelles. Or les appelants ne rattrapent que `CampayError` —
    ce timeout serait donc remonté en HTTP 500 au client, EN PLEIN paiement,
    au moment précis où il attend de savoir si son argent est parti.

    Un décorateur et pas trois `try` recopiés : la prochaine méthode ajoutée
    est couverte par construction, alors qu'une recopie s'oublie.
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except CampayError:
            raise                                  # déjà qualifiée
        except httpx.HTTPError as e:
            # `HTTPError` couvre timeouts, connexions refusées, erreurs TLS et
            # réponses illisibles — tout ce que le réseau sait rater.
            raise CampayError(
                f"{type(e).__name__} vers Campay : {e}",
                "Le service de paiement ne répond pas. Réessayez dans un instant.",
            ) from e
        except ValueError as e:
            # `res.json()` sur une réponse qui n'est pas du JSON (page d'erreur
            # HTML d'un proxy, typiquement).
            raise CampayError(f"réponse Campay illisible : {e}") from e
    return wrapper


class CampayClient:
    def __init__(self) -> None:
        self.username = os.getenv("CAMPAY_USERNAME", "")
        self.password = os.getenv("CAMPAY_PASSWORD", "")
        # Production sur demande EXPLICITE. Un défaut à « PROD » ferait qu'un
        # oubli de configuration encaisse de l'argent réel pendant les tests ;
        # l'inverse ne fait rien de grave.
        self.host = PROD_HOST if os.getenv("CAMPAY_ENV") == "PROD" else DEMO_HOST
        self._token: str | None = None
        self._token_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    @_as_campay_error
    async def _get_token(self, client: httpx.AsyncClient) -> str:
        # Le jeton temporaire de Campay vit une heure. On le garde 50 minutes :
        # redemander un jeton à chaque encaissement double le nombre d'allers-
        # retours, et le garder jusqu'à la dernière seconde le fait expirer en
        # vol sur les paiements lents.
        if self._token and (time.monotonic() - self._token_at) < 3000:
            return self._token

        res = await client.post(
            f"{self.host}/api/token/",
            json={"username": self.username, "password": self.password},
        )
        if res.status_code != 200:
            raise CampayError(f"jeton refusé ({res.status_code})")
        token = res.json().get("token")
        if not token:
            raise CampayError("réponse de jeton sans champ `token`")
        self._token, self._token_at = token, time.monotonic()
        return token

    @_as_campay_error
    async def collect(self, *, amount: int, phone: str, description: str,
                      external_reference: str) -> dict:
        """Déclenche la demande de paiement. NE BLOQUE PAS jusqu'au résultat.

        L'utilisateur reçoit une invite USSD sur son téléphone et saisit son
        code. Ça prend le temps que ça prend — parfois plus d'une minute. On
        rend donc la main tout de suite avec la référence, et l'état se suit
        par `transaction_status` ou par le webhook.

        `amount` est en francs CFA entiers (le XAF n'a pas de subdivision).
        """
        if not self.configured:
            raise CampayError("CAMPAY_USERNAME / CAMPAY_PASSWORD absents")

        async with httpx.AsyncClient(timeout=30.0) as client:   # verify=True par défaut
            token = await self._get_token(client)
            res = await client.post(
                f"{self.host}/api/collect/",
                json={
                    "amount": str(amount),
                    "currency": SUPPORTED_CURRENCY,
                    "from": phone,
                    "description": description,
                    # Notre identifiant à nous. C'est lui qui permet de
                    # retrouver le paiement si la réponse se perd en route.
                    "external_reference": external_reference,
                },
                headers={"Authorization": f"Token {token}"},
            )
            if res.status_code != 200:
                # Campay renvoie un code (ER101…) qu'on traduit en conseil
                # actionnable ; tout le reste reste dans les journaux.
                brut = res.text[:200]
                code = next((c for c in ERROR_MESSAGES if c in brut), None)
                raise CampayError(
                    f"collect refusé ({res.status_code}) : {brut}",
                    ERROR_MESSAGES.get(code) if code else None,
                )
            data = res.json()
            if not data.get("reference"):
                raise CampayError("collect sans référence")
            return data

    @_as_campay_error
    async def transaction_status(self, reference: str) -> dict:
        """État d'une transaction. PENDING · SUCCESSFUL · FAILED."""
        if not self.configured:
            raise CampayError("CAMPAY_USERNAME / CAMPAY_PASSWORD absents")

        async with httpx.AsyncClient(timeout=20.0) as client:
            token = await self._get_token(client)
            # Barre oblique FINALE obligatoire : la doc écrit
            # `/api/transaction/(reference)/`. Le SDK officiel l'omet et s'en
            # tire parce que `requests` suit les redirections ; httpx ne les
            # suit PAS par défaut, on aurait donc lu un 301 comme un échec de
            # statut — c'est-à-dire un paiement réussi vu comme en attente,
            # jusqu'à l'expiration.
            res = await client.get(
                f"{self.host}/api/transaction/{reference}/",
                headers={"Authorization": f"Token {token}"},
            )
            if res.status_code != 200:
                raise CampayError(f"statut indisponible ({res.status_code})")
            return res.json()


    # ── Signature du webhook ────────────────────────────────────────────────

    def verify_webhook_signature(self, signature: str) -> bool:
        """La notification vient-elle BIEN de Campay ?

        Campay joint au rappel un JWT signé en HS256 avec la « webhook key » de
        l'application, dont la charge utile porte `source: "campay"`. C'est la
        seule preuve d'origine disponible : le point d'entrée est public, tout
        le reste du corps est déclaratif.

        Sans `CAMPAY_WEBHOOK_KEY` configurée, on renvoie False — donc on refuse.
        Traiter une clé absente comme « signature valide » transformerait un
        oubli de configuration en porte ouverte, silencieusement.
        """
        key = os.getenv("CAMPAY_WEBHOOK_KEY", "")
        if not key or not signature:
            return False
        try:
            # PyJWT vérifie la signature ET l'expiration (`exp`) ; on exige en
            # plus l'émetteur annoncé par la doc.
            payload = jwt.decode(signature, key, algorithms=["HS256"])
        except jwt.PyJWTError as e:
            logger.warning("Signature de webhook Campay rejetée : %s", e)
            return False
        return str(payload.get("source", "")).lower() == "campay"


campay = CampayClient()
