"""
Grille tarifaire — SOURCE UNIQUE.

Quatre offres, trois zones de prix. Rien d'autre. Chaque montant vécu par
l'utilisateur (carte de tarifs, écran de paiement, reçu Campay) descend d'ici :
un prix qui existe à deux endroits finit par exister en deux versions.

Les montants sont en **unités mineures entières** (centimes pour EUR/USD,
francs pour XAF qui n'a pas de subdivision). Jamais de `float` sur de l'argent :
0,1 + 0,2 != 0,3 en binaire, et un centime perdu par transaction se voit à la
fin du mois.

Positionnement (relevé le 19/07/2026) : DocTranslator vend 100 pages 14,99 $/mois.
Starter offre le même volume à 7,90 € — c'est l'argument de bascule, il doit
rester vrai. Si leur prix bouge, ce commentaire ment : le revérifier avant de
s'en servir en communication.

Coût de revient : ~0,0007 $/page d'API (deepseek-v4-flash). Le coût dominant
est FIXE (CPU de rendu, stockage, hébergement), pas variable — d'où une grille
calée sur la valeur et la concurrence, pas sur le coût à la page.
"""
from __future__ import annotations

import os

# ── Zones ────────────────────────────────────────────────────────────────────
#
# Indexées sur le pouvoir d'achat. Le découpage est GROSSIER volontairement :
# trois zones se maintiennent, trente ne se maintiennent pas.

ZONE_AFRICA = "A"      # Afrique subsaharienne — encaissement Campay (MoMo/OM)
ZONE_EMERGING = "B"    # Maghreb, Asie, Amérique latine, Europe de l'Est
ZONE_GLOBAL = "C"      # Europe de l'Ouest, Amérique du Nord, Golfe, Océanie

DEFAULT_ZONE = ZONE_GLOBAL   # sans localisation fiable, on ne BRADE pas

# Pays → zone. Tout pays absent tombe en zone C. On énumère donc ce qui donne
# droit à une RÉDUCTION : oublier un pays le fait payer le tarif plein (perte
# commerciale, visible et corrigible), jamais l'inverse (perte sèche).
COUNTRY_ZONE: dict[str, str] = {
    # Zone A — CEMAC / UEMOA et Afrique subsaharienne
    "CM": ZONE_AFRICA, "GA": ZONE_AFRICA, "TD": ZONE_AFRICA, "CF": ZONE_AFRICA,
    "CG": ZONE_AFRICA, "GQ": ZONE_AFRICA, "SN": ZONE_AFRICA, "CI": ZONE_AFRICA,
    "BF": ZONE_AFRICA, "ML": ZONE_AFRICA, "NE": ZONE_AFRICA, "TG": ZONE_AFRICA,
    "BJ": ZONE_AFRICA, "GW": ZONE_AFRICA, "NG": ZONE_AFRICA, "GH": ZONE_AFRICA,
    "KE": ZONE_AFRICA, "TZ": ZONE_AFRICA, "UG": ZONE_AFRICA, "RW": ZONE_AFRICA,
    "CD": ZONE_AFRICA, "MG": ZONE_AFRICA, "ET": ZONE_AFRICA, "ZM": ZONE_AFRICA,
    # Zone B — émergents
    "MA": ZONE_EMERGING, "DZ": ZONE_EMERGING, "TN": ZONE_EMERGING,
    "EG": ZONE_EMERGING, "IN": ZONE_EMERGING, "PK": ZONE_EMERGING,
    "BD": ZONE_EMERGING, "ID": ZONE_EMERGING, "PH": ZONE_EMERGING,
    "VN": ZONE_EMERGING, "TH": ZONE_EMERGING, "BR": ZONE_EMERGING,
    "MX": ZONE_EMERGING, "CO": ZONE_EMERGING, "AR": ZONE_EMERGING,
    "PE": ZONE_EMERGING, "TR": ZONE_EMERGING, "UA": ZONE_EMERGING,
    "RO": ZONE_EMERGING, "BG": ZONE_EMERGING, "RS": ZONE_EMERGING,
}


def zone_for_country(country: str | None) -> str:
    """Zone tarifaire d'un code pays ISO-3166 alpha-2. Inconnu → tarif plein.

    `PRICING_DEFAULT_COUNTRY` sert quand AUCUN en-tête géographique n'est posé —
    c'est-à-dire hors production, où il n'y a pas de proxy pour le fournir. Sans
    lui, un développement local est toujours en zone C : l'encaissement mobile
    money, réservé à la zone FCFA, répondrait 501 et serait intestable sur la
    machine de celui qui l'écrit. En production derrière le proxy, l'en-tête
    existe et cette variable n'est jamais consultée.
    """
    if not country:
        country = os.getenv("PRICING_DEFAULT_COUNTRY") or None
    if not country:
        return DEFAULT_ZONE
    return COUNTRY_ZONE.get(country.strip().upper(), DEFAULT_ZONE)


# ── Devises ──────────────────────────────────────────────────────────────────

CURRENCY: dict[str, str] = {
    ZONE_AFRICA: "XAF",
    ZONE_EMERGING: "USD",
    ZONE_GLOBAL: "EUR",
}

# Nombre de décimales de la devise. Le XAF n'en a AUCUNE : afficher « 2 500,00 »
# à un utilisateur camerounais est aussi faux que d'afficher « 7 € » à un
# Français qui doit payer 7,90.
CURRENCY_DECIMALS: dict[str, int] = {"XAF": 0, "USD": 2, "EUR": 2}


# ── Les quatre offres ────────────────────────────────────────────────────────
#
# Prix en unités mineures. `None` = pas de prix public (devis).
# L'annuel est le prix MENSUEL facturé à l'année, pas le total : c'est le
# nombre que la carte affiche, et le comparer au mensuel doit être immédiat.

PLAN_PRICES: dict[str, dict[str, dict[str, int | None]]] = {
    ZONE_GLOBAL: {                                    # EUR, centimes
        "free":       {"monthly": 0,    "annual": 0},
        "starter":    {"monthly": 790,  "annual": 590},
        "pro":        {"monthly": 1990, "annual": 1490},
        "enterprise": {"monthly": None, "annual": None},
    },
    ZONE_EMERGING: {                                  # USD, cents
        "free":       {"monthly": 0,    "annual": 0},
        "starter":    {"monthly": 490,  "annual": 390},
        "pro":        {"monthly": 1190, "annual": 890},
        "enterprise": {"monthly": None, "annual": None},
    },
    ZONE_AFRICA: {                                    # XAF, francs
        # Baissé le 24/07/2026 pour l'adoption bêta (2500/6900 → 1500/4500). Le
        # coût est fixe, pas à la page : sur ce marché, du volume à petit prix
        # vaut mieux qu'une marge affichée que personne ne teste.
        "free":       {"monthly": 0,    "annual": 0},
        "starter":    {"monthly": 1500, "annual": 1200},
        "pro":        {"monthly": 4500, "annual": 3500},
        "enterprise": {"monthly": None, "annual": None},
    },
}

# Prix d'UNE page à l'unité, pour le forfait Gratuit une fois sa page offerte
# consommée. Un seul prix, pas de paliers : trois paliers dégressifs sur une
# carte de tarifs, c'est un tableau à lire là où il faut un chiffre à
# comprendre. La dégressivité existe déjà — elle s'appelle « Starter ».
#
# Rapport voulu : ~2,4× le prix à la page d'un abonnement (0,079 €/page en
# Starter). C'est la norme du secteur pour du paiement à l'acte, et ça rend
# Starter évident dès ~40 pages/mois sans avoir à l'expliquer.
PAGE_PRICE: dict[str, int] = {
    ZONE_GLOBAL:   19,    # 0,19 €
    ZONE_EMERGING: 12,    # 0,12 $
    ZONE_AFRICA:   75,    # 75 FCFA (baissé de 100 le 24/07/2026, cf. zone A)
}

# Les frais Campay (2 % encaissement + 1 % reversement) sont DÉJÀ absorbés dans
# les montants ci-dessus — on ne les ajoute jamais au moment de payer. Un prix
# annoncé qui grossit à l'écran de paiement est la première cause d'abandon.
PSP_FEE_RATE = 0.03


def plan_price(plan: str, zone: str, annual: bool = False) -> int | None:
    """Prix mensuel d'un plan dans une zone, en unités mineures. None = devis."""
    grid = PLAN_PRICES.get(zone) or PLAN_PRICES[DEFAULT_ZONE]
    entry = grid.get(plan)
    if entry is None:
        return None
    return entry["annual" if annual else "monthly"]


def page_price(zone: str) -> int:
    """Prix d'une page à l'unité, en unités mineures."""
    return PAGE_PRICE.get(zone, PAGE_PRICE[DEFAULT_ZONE])
