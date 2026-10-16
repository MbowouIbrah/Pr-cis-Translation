"""Alertes de coût DeepSeek : solde bas, dépense du jour, solde épuisé.

POURQUOI LE SOLDE, ET PAS UN COMPTE DE JETONS
---------------------------------------------
Les jetons comptés par l'application ignorent ce qu'elle ne voit pas : un autre
outil sur la même clé, un changement de tarif, les heures creuses à -50 %. Le
solde du compte (`GET /user/balance`) est ce que DeepSeek facture réellement :
c'est lui qu'on surveille.

Trois alertes, envoyées aux admins (plan `admin`, à défaut ADMIN_ALERT_EMAIL)
et inscrites au journal d'erreurs :

  • solde sous DEEPSEEK_SOLDE_ALERTE — une fois par passage sous le seuil,
    réarmée quand une recharge le fait remonter ;
  • dépense des dernières 24 h au-delà de DEEPSEEK_DEPENSE_JOUR_ALERTE — au
    plus une fois par 24 h. La dépense est la somme des BAISSES du solde : une
    recharge n'efface pas ce qui a été dépensé ;
  • refus 402 (solde épuisé) pendant une traduction — les traductions sont
    bloquées jusqu'à la recharge.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque

import httpx
from sqlalchemy import select

from app.config import ADMIN_ALERT_EMAIL, logger

SEUIL_SOLDE = float(os.getenv("DEEPSEEK_SOLDE_ALERTE", "2"))
SEUIL_DEPENSE_JOUR = float(os.getenv("DEEPSEEK_DEPENSE_JOUR_ALERTE", "3"))
INTERVALLE_S = max(60, int(os.getenv("DEEPSEEK_SOLDE_INTERVALLE_S", "1800")))
_JOUR_S = 24 * 3600


class Surveillance:
    """État des alertes, sans réseau ni base : testable tel quel."""

    def __init__(self, seuil_solde=SEUIL_SOLDE, seuil_jour=SEUIL_DEPENSE_JOUR):
        self.seuil_solde = seuil_solde
        self.seuil_jour = seuil_jour
        self.releves: deque[tuple[float, float]] = deque()
        self.solde: float | None = None
        self.lu_a: float | None = None
        self._solde_bas_signale = False
        self._depense_signalee_a: float | None = None

    def depense_24h(self, maintenant: float) -> float:
        """Somme des baisses du solde sur les 24 dernières heures."""
        total, precedent = 0.0, None
        for t, s in self.releves:
            if precedent is not None and t >= maintenant - _JOUR_S and s < precedent:
                total += precedent - s
            precedent = s
        return round(total, 4)

    def enregistrer(self, solde: float, maintenant: float) -> list[tuple[str, str]]:
        """Note un relevé ; renvoie les alertes (sujet, corps) à envoyer."""
        self.solde, self.lu_a = solde, maintenant
        self.releves.append((maintenant, solde))
        # On garde le dernier relevé d'avant la fenêtre : il sert de point de
        # départ à la première baisse comptée.
        while len(self.releves) > 2 and self.releves[1][0] < maintenant - _JOUR_S:
            self.releves.popleft()

        alertes = []
        if solde < self.seuil_solde:
            if not self._solde_bas_signale:
                self._solde_bas_signale = True
                alertes.append((
                    f"Solde DeepSeek bas : {solde:.2f} $",
                    f"Le solde DeepSeek est de {solde:.2f} $, sous le seuil "
                    f"d'alerte de {self.seuil_solde:.2f} $. À zéro, plus "
                    f"aucune traduction ne passe : recharger le compte sur "
                    f"https://platform.deepseek.com/top_up"))
        else:
            self._solde_bas_signale = False     # rechargé : alerte réarmée

        depense = self.depense_24h(maintenant)
        if depense > self.seuil_jour and (
                self._depense_signalee_a is None
                or maintenant - self._depense_signalee_a >= _JOUR_S):
            self._depense_signalee_a = maintenant
            alertes.append((
                f"Dépense DeepSeek élevée : {depense:.2f} $ en 24 h",
                f"{depense:.2f} $ dépensés sur DeepSeek ces dernières 24 h "
                f"(seuil d'alerte : {self.seuil_jour:.2f} $). Solde restant : "
                f"{solde:.2f} $. Vérifier le volume de traductions et le "
                f"modèle configuré (DEEPSEEK_MODEL*)."))
        return alertes


surveillance = Surveillance()
_boucle: asyncio.AbstractEventLoop | None = None
_lecture = asyncio.Lock()


def lire_solde() -> float | None:
    """Solde total du compte, en USD de préférence. None si illisible.
    Appel réseau BLOQUANT : depuis une coroutine, passer par un thread."""
    cle = os.getenv("DEEPSEEK_API_KEY")
    if not cle:
        return None
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    try:
        r = httpx.get(f"{base}/user/balance", timeout=15,
                      headers={"Authorization": f"Bearer {cle}"})
        r.raise_for_status()
        infos = r.json().get("balance_infos") or []
    except Exception as exc:
        logger.warning("Solde DeepSeek illisible : %s", exc)
        return None
    info = next((i for i in infos if i.get("currency") == "USD"),
                infos[0] if infos else None)
    try:
        return float(info["total_balance"]) if info else None
    except (KeyError, TypeError, ValueError):
        return None


async def _alerter(sujet: str, corps: str) -> None:
    from app.core.database import async_session
    from app.core.email import send_plain
    from app.models import User
    from app.services.error_log import log_error

    await log_error("deepseek_solde", f"{sujet} — {corps}", level="warning",
                    trigger_alert=False)
    try:
        async with async_session() as db:
            rows = await db.execute(select(User.email).where(User.plan == "admin"))
            emails = [e for (e,) in rows.all() if e]
    except Exception:
        emails = []
    if not emails and ADMIN_ALERT_EMAIL:
        emails = [ADMIN_ALERT_EMAIL]
    await send_plain(emails, f"[Précis] {sujet}", corps)


async def verifier() -> float | None:
    """Relit le solde et envoie les alertes dues. Ne lève jamais."""
    try:
        # Une lecture à la fois : deux relevés notés dans le désordre
        # compteraient deux fois la même baisse dans la dépense du jour.
        async with _lecture:
            solde = await asyncio.to_thread(lire_solde)
            if solde is None:
                return None
            alertes = surveillance.enregistrer(solde, time.time())
        for sujet, corps in alertes:
            await _alerter(sujet, corps)
        return solde
    except Exception as exc:
        logger.warning("Surveillance du solde DeepSeek : %s", exc)
        return None


def _depuis_un_thread(coro_fn) -> None:
    """Planifie `coro_fn()` sur la boucle de l'application depuis un worker
    de traduction (thread) ; sans boucle connue, ne fait rien."""
    if _boucle is None or _boucle.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(coro_fn(), _boucle)
    except Exception:
        pass


def verifier_depuis_un_thread() -> None:
    """Après une traduction : la dépense vient d'avoir lieu."""
    _depuis_un_thread(verifier)


def signaler_solde_epuise() -> None:
    """Branché sur `translation_ai.sur_solde_epuise` (premier 402 d'une série)."""
    async def _envoyer():
        await _alerter(
            "Solde DeepSeek épuisé : traductions bloquées",
            "DeepSeek refuse les requêtes pour solde insuffisant (402). "
            "Aucune traduction ne peut aboutir avant la recharge du compte : "
            "https://platform.deepseek.com/top_up")
    _depuis_un_thread(_envoyer)


async def surveiller() -> None:
    """Tâche de fond du lifespan : branche l'alerte 402, puis relit le solde
    à intervalle régulier."""
    global _boucle
    from engines import translation_ai

    _boucle = asyncio.get_running_loop()
    if signaler_solde_epuise not in translation_ai.sur_solde_epuise:
        translation_ai.sur_solde_epuise.append(signaler_solde_epuise)
    while True:
        await verifier()
        await asyncio.sleep(INTERVALLE_S)


def etat() -> dict:
    """Pour la vue admin."""
    from engines import translation_ai

    maintenant = time.time()
    return {
        "solde_usd": surveillance.solde,
        "lu_a": surveillance.lu_a,
        "seuil_solde_usd": surveillance.seuil_solde,
        "depense_24h_usd": surveillance.depense_24h(maintenant),
        "seuil_depense_jour_usd": surveillance.seuil_jour,
        "consommation_jetons": translation_ai.consommation_instantane(),
    }
