"""Ménage du magasin : on ne garde que ce qui sert à reconstruire.

Ce qui vit dans `translations/{empreinte}/`, du plus précieux au plus jetable :

1. L'ORIGINAL et la TRADUCTION (`pages*.json`, `translated*.json`) : la seule
   chose qui a coûté des appels IA. Gardés tant qu'un Document y renvoie.
2. Les RENDUS en cache (`<traduction>.render.<version>.pdf`) : recalculables
   à tout moment depuis 1. Seule la version courante du moteur sert ; les
   autres sont introuvables par construction (cf. render_cache.py).
3. Les sorties de JOB (`render*_<job>.ext`, `partial*_<job>.pdf`) :
   transitoires, propres à un job vivant en mémoire.

Mesuré le 15/10/2026 sur le poste de dev : 70 Mo sur 90 appartenaient à des
documents SUPPRIMÉS — la suppression n'effaçait que les deux chemins connus de
la base, jamais les rendus, les extractions ni les sorties de job à côté.

Deux moments sûrs pour effacer, sans heuristique :
- à la SUPPRESSION, quand plus aucun Document ne renvoie à la racine : tout le
  dossier part (`purger_racine`) ;
- au DÉMARRAGE, quand la table des jobs (en mémoire) est vide : tout ce qui
  appartient à un job est orphelin par construction (`balayer_magasin`).
"""
from __future__ import annotations

import os
import re
import shutil
import time

from app.config import (PREVIEW_CACHE_DIR, PREVIEW_CACHE_MAX_BYTES,
                        TRADUCTION_ORPHELINE_GRACE_S, TRANSLATIONS_DIR, logger)

# Une racine de job = l'empreinte du contenu, en hexadécimal. Tout le reste
# (`_previews`, dossiers de test `_test_*`…) n'est jamais une racine à balayer.
_RACINE_JOB = re.compile(r"^[0-9a-f]{8,64}$")
_SORTIE_JOB = re.compile(r"^(render|partial).*_[0-9a-f]{8}\.[a-z]+(\.tmp)?$")
_RENDU_CACHE = re.compile(r"^(?P<trad>.+)\.render\.(?P<version>.+)\.pdf(\.tmp)?$")
_TRADUCTION = re.compile(r"^(pages|translated).*\.json$")


def _dans_magasin(path: str, magasin: str) -> bool:
    try:
        real = os.path.realpath(path)
        store = os.path.realpath(magasin)
        return real != store and os.path.commonpath([real, store]) == store
    except (ValueError, OSError):
        return False


def purger_racine(racine: str, magasin: str = TRANSLATIONS_DIR) -> bool:
    """Efface TOUT le dossier d'un job. L'appelant a vérifié qu'il est orphelin."""
    if not _dans_magasin(racine, magasin) or not os.path.isdir(racine):
        return False
    shutil.rmtree(racine, ignore_errors=True)
    return not os.path.exists(racine)


def _effacer(path: str, compte: dict, cle: str) -> None:
    try:
        taille = os.path.getsize(path)
        os.remove(path)
        compte[cle] += 1
        compte["octets"] += taille
    except OSError:
        pass


def balayer_magasin(references: set[str], version_moteur: str,
                    magasin: str = TRANSLATIONS_DIR,
                    maintenant: float | None = None) -> dict:
    """Balaye le magasin. À n'appeler QUE sans job vivant (au démarrage).

    `references` : chemins (réels, normalisés) cités par un Document, original
    ou traduction. Une racine dont aucun n'est cité part entière. Dans une
    racine citée, on efface les sorties de job, les rendus d'une autre version
    du moteur ou d'une traduction disparue, et les traductions que plus rien ne
    cite depuis `TRADUCTION_ORPHELINE_GRACE_S` (délai laissé à une relance
    après échec, qui reprend les pages déjà traduites au lieu de les repayer).
    """
    maintenant = time.time() if maintenant is None else maintenant
    compte = {"racines": 0, "sorties_job": 0, "rendus": 0,
              "traductions": 0, "octets": 0}
    refs = {os.path.normcase(os.path.realpath(p)) for p in references if p}
    if not os.path.isdir(magasin):
        return compte

    for nom in os.listdir(magasin):
        racine = os.path.join(magasin, nom)
        if not _RACINE_JOB.match(nom) or not os.path.isdir(racine):
            continue
        prefixe = os.path.normcase(os.path.realpath(racine)) + os.sep
        if not any(r.startswith(prefixe) for r in refs):
            taille = sum(os.path.getsize(os.path.join(d, f))
                         for d, _, fs in os.walk(racine) for f in fs)
            if purger_racine(racine, magasin):
                compte["racines"] += 1
                compte["octets"] += taille
            continue

        for dossier, _, fichiers in os.walk(racine):
            # Traductions d'abord : leurs rendus tombent avec elles.
            for f in sorted(fichiers, key=lambda n: not _TRADUCTION.match(n)):
                chemin = os.path.join(dossier, f)
                cle = os.path.normcase(os.path.realpath(chemin))
                if _SORTIE_JOB.match(f):
                    _effacer(chemin, compte, "sorties_job")
                elif m := _RENDU_CACHE.match(f):
                    trad = os.path.join(dossier, m["trad"])
                    if (m["version"] != version_moteur or f.endswith(".tmp")
                            or not os.path.isfile(trad)):
                        _effacer(chemin, compte, "rendus")
                elif _TRADUCTION.match(f) and cle not in refs:
                    try:
                        age = maintenant - os.path.getmtime(chemin)
                    except OSError:
                        continue
                    if age > TRADUCTION_ORPHELINE_GRACE_S:
                        _effacer(chemin, compte, "traductions")
        _elaguer_dossiers_vides(racine)
    return compte


def _elaguer_dossiers_vides(racine: str) -> None:
    for dossier, _, _ in sorted(os.walk(racine), key=lambda t: -len(t[0])):
        try:
            os.rmdir(dossier)               # lève si non vide : il reste
        except OSError:
            pass


def limiter_apercus(max_octets: int = PREVIEW_CACHE_MAX_BYTES,
                    dossier: str = PREVIEW_CACHE_DIR) -> int:
    """Borne `_previews` : efface les conversions les moins récemment servies.

    Ce cache d'aperçus Office n'avait aucune purge. Une conversion effacée se
    refait (LibreOffice) à la prochaine demande : on perd du temps, jamais de
    donnée. Renvoie le nombre de fichiers effacés.
    """
    try:
        noms = os.listdir(dossier)
    except OSError:
        return 0
    fichiers = []
    for n in noms:
        p = os.path.join(dossier, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if os.path.isfile(p):
            fichiers.append((st.st_mtime, st.st_size, p))
    total = sum(t for _, t, _ in fichiers)
    efface = 0
    for _, taille, p in sorted(fichiers):    # plus ancien d'abord
        if total <= max_octets:
            break
        try:
            os.remove(p)
            total -= taille
            efface += 1
        except OSError:
            pass
    return efface


async def balayer_au_demarrage() -> None:
    """Point d'entrée du lifespan. Ne lève JAMAIS : un ménage n'empêche pas de
    démarrer. Sans la liste des références (base injoignable), on n'efface
    AUCUNE racine : ne pas savoir qui tient à un fichier n'autorise pas à
    l'effacer."""
    import asyncio
    try:
        from sqlalchemy import select
        from app.core.database import async_session
        from app.models import Document
        from engines.pdf.version import ENGINE_VERSION
        async with async_session() as db:
            lignes = (await db.execute(
                select(Document.original_path, Document.translated_path)
            )).all()
        refs = {p for ligne in lignes for p in ligne if p}
        compte = await asyncio.to_thread(balayer_magasin, refs, ENGINE_VERSION)
        apercus = await asyncio.to_thread(limiter_apercus)
    except Exception as e:
        logger.warning(f"Ménage du magasin impossible : {e}")
        return
    if compte["octets"] or apercus:
        logger.info(
            f"Magasin : {compte['racines']} dossier(s) orphelin(s), "
            f"{compte['sorties_job']} sortie(s) de job, {compte['rendus']} "
            f"rendu(s) périmé(s), {compte['traductions']} traduction(s) "
            f"orpheline(s), {apercus} aperçu(s) — "
            f"{compte['octets'] / 1e6:.1f} Mo libérés.")
