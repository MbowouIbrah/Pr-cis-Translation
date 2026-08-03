"""Registre des moteurs : le seul endroit du projet qui nomme un format.

L'application demande « qui traite le .pptx ? » et reçoit une CLASSE. Elle
n'importe jamais un moteur directement, donc en ajouter un ne demande de
toucher à aucune route, à aucun exécuteur : on écrit le paquet, on l'inscrit
ici, c'est tout.

Le registre rend une CLASSE et jamais une instance — voir `base.py` : un moteur
porte un dossier temporaire, deux opérations qui le partagent se marchent
dessus.

L'import du moteur PPTX est protégé : ses dépendances (lxml) peuvent manquer
sur une installation réduite. Le format devient alors simplement indisponible,
au lieu d'empêcher toute l'application de démarrer.
"""
from __future__ import annotations

from .base import TranslationEngine
from .docx.engine import DOCXTranslatorEngine

_REGISTRE: dict[str, type] = {"docx": DOCXTranslatorEngine}

#: Pourquoi un format manque à l'appel, s'il en manque un. L'application le lit
#: au démarrage pour le dire à l'exploitant. Le registre ne journalise pas
#: lui-même : il n'a pas à connaître le logger de qui l'utilise.
INDISPONIBLES: dict[str, str] = {}

try:
    from .pptx.engine import PPTXTranslatorEngine
    _REGISTRE["pptx"] = PPTXTranslatorEngine
except ImportError as _e:                       # pragma: no cover
    PPTXTranslatorEngine = None
    INDISPONIBLES["pptx"] = str(_e)

# XLSX (0.2.0). La couverture du TEXTE est complète — les dix endroits où Excel
# range du texte sont traités. Reste un défaut de RENDU : l'expansion n'est pas
# gérée (une traduction plus longue déborde ou s'affiche en `#####`). Voir
# `xlsx/CONTEXTE.md`, section « Limites au-delà du texte ».
try:
    from .xlsx.engine import XLSXTranslatorEngine
    _REGISTRE["xlsx"] = XLSXTranslatorEngine
except ImportError as _e:                       # pragma: no cover
    XLSXTranslatorEngine = None
    INDISPONIBLES["xlsx"] = str(_e)


def engine_class_for(ext: str) -> type | None:
    """La classe de moteur qui traite cette extension, ou None."""
    return _REGISTRE.get(ext.lower().lstrip("."))


def new_engine(ext: str):
    """Une instance NEUVE du moteur pour ce format.

    Neuve à chaque appel, jamais mise en cache : c'est tout l'intérêt. Voir la
    docstring de `TranslationEngine`.
    """
    cls = engine_class_for(ext)
    return cls() if cls else None


def supports(ext: str) -> bool:
    return engine_class_for(ext) is not None


def formats() -> list[str]:
    """Extensions prises en charge, pour l'affichage et les diagnostics."""
    return sorted(_REGISTRE)


__all__ = ["TranslationEngine", "INDISPONIBLES", "engine_class_for",
           "new_engine", "supports", "formats", "DOCXTranslatorEngine",
           "PPTXTranslatorEngine", "XLSXTranslatorEngine"]
