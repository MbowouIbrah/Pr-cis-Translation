"""
Moyens de paiement proposés au client, et prestataire qui encaisse chacun.

Le client choisit un MOYEN (MTN MoMo, Orange Money, carte) ; il ne voit jamais
le prestataire. Le lien moyen → prestataire vit dans la configuration
(`PAYMENT_METHODS`), pas dans le code : brancher ZitoPay pour la carte, ou
basculer Orange Money d'un prestataire à l'autre, c'est changer une ligne de
`.env`, pas une route.

    PAYMENT_METHODS=mtn_momo=campay,orange_money=campay,card=

Un moyen sans prestataire, ou dont le prestataire n'a pas ses identifiants, ou
qui n'encaisse pas la devise de la zone, est AFFICHÉ mais indisponible : le
client sait qu'il viendra, et rien ne part chez un prestataire incapable de
l'honorer.
"""
from __future__ import annotations

import os
import re

from app.core.payment_gateway import campay, SUPPORTED_CURRENCY

MTN_MOMO = "mtn_momo"
ORANGE_MONEY = "orange_money"
CARD = "card"
# Ordre d'affichage.
METHODS = (MTN_MOMO, ORANGE_MONEY, CARD)
# Moyens qui encaissent par une invite sur le téléphone : ils exigent un numéro.
PHONE_METHODS = frozenset({MTN_MOMO, ORANGE_MONEY})

DEFAULT_ROUTING = "mtn_momo=campay,orange_money=campay,card="


class CampayProvider:
    """Invite USSD poussée sur le téléphone ; l'opérateur se déduit du numéro."""
    name = "campay"
    currencies = frozenset({SUPPORTED_CURRENCY})
    # Ce que le prestataire sait encaisser : un routage `card=campay` par
    # erreur de configuration reste indisponible au lieu d'échouer à chaque essai.
    methods = frozenset({MTN_MOMO, ORANGE_MONEY})

    @property
    def configured(self) -> bool:
        return campay.configured

    async def start(self, *, amount: int, currency: str, phone: str | None,
                    description: str, reference: str) -> dict:
        res = await campay.collect(amount=amount, phone=phone,
                                   description=description,
                                   external_reference=reference)
        return {"provider_ref": res.get("reference"),
                "ussd_code": res.get("ussd_code"),
                "operator": res.get("operator")}

    async def status(self, provider_ref: str) -> str:
        res = await campay.transaction_status(provider_ref)
        return res.get("status", "PENDING")


PROVIDERS = {p.name: p for p in (CampayProvider(),)}


def routing() -> dict[str, str]:
    """Moyen → nom du prestataire. Relu à chaque appel : la configuration se
    change sans toucher au code (et les tests la changent sans redémarrer)."""
    out = {m: "" for m in METHODS}
    for part in os.getenv("PAYMENT_METHODS", DEFAULT_ROUTING).split(","):
        method, _, provider = part.partition("=")
        method = method.strip()
        if method in out:
            out[method] = provider.strip().lower()
    return out


def provider_for(method: str, currency: str):
    """(prestataire, None) si le moyen peut encaisser cette devise, sinon
    (None, raison). La raison va aux journaux et aux tests ; le client, lui,
    lit seulement « pas encore disponible »."""
    if method not in METHODS:
        return None, "unknown"
    provider = PROVIDERS.get(routing()[method])
    if provider is None:
        return None, "no_provider"
    if method not in provider.methods:
        return None, "unsupported"
    if not provider.configured:
        return None, "not_configured"
    if currency not in provider.currencies:
        return None, "currency"
    return provider, None


def list_methods(currency: str) -> list[dict]:
    # Sans la raison : l'état de la configuration ne regarde pas le client.
    return [{"id": m, "available": provider_for(m, currency)[0] is not None,
             "needs_phone": m in PHONE_METHODS} for m in METHODS]


# Plan de numérotation camerounais (miroir de frontend/src/lib/mobileMoney.ts).
#   MTN    : 650-654, 67x, 680-684
#   Orange : 655-659, 69x, 685-689
_OPERATOR_RULES = ((re.compile(r"^6(5[0-4]|7\d|8[0-4])"), MTN_MOMO),
                   (re.compile(r"^6(5[5-9]|9\d|8[5-9])"), ORANGE_MONEY))


def method_of_phone(phone: str) -> str | None:
    """Moyen que le prestataire utilisera réellement pour ce numéro : c'est
    le numéro, pas la tuile cochée, qui choisit l'opérateur. `None` pour un
    préfixe inconnu (table indicative, elle se périme) : ne bloque rien."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 12 and digits.startswith("237"):
        digits = digits[3:]
    if len(digits) != 9:
        return None
    for rule, method in _OPERATOR_RULES:
        if rule.match(digits):
            return method
    return None
