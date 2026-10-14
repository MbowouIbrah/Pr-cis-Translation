"""Menage au demarrage : ce qu'un processus precedent a laisse en vol.

Les deux fonctions reposent sur le MEME fait, vrai par construction : la table
des jobs vit en memoire et elle est vide au demarrage. Tout ce qui pretend etre
« en cours » a donc perdu son worker. Aucune heuristique, aucun delai a
deviner.
"""
from __future__ import annotations

import glob
import os

from app.config import TRANSLATIONS_DIR, logger


async def reconcilier_jobs_orphelins():
    """Clôt les Documents restés `translating` d'un processus précédent.

    Le registre des jobs (`JobManager`) vit en MÉMOIRE : il est vide au démarrage.
    Tout Document encore `translating` à cet instant a donc perdu son worker —
    serveur arrêté, rechargement, plantage. Aucune heuristique là-dedans, aucun
    délai à deviner : c'est vrai par construction.

    Sans ça, la ligne reste en vol POUR TOUJOURS. L'utilisateur voit un document
    éternellement « en cours » dans sa bibliothèque, et comme `translated_path`
    est NULL, le téléchargement lui sert l'ORIGINAL en silence — un document
    présenté comme traduit qui ne l'est pas. Mesuré sur cette base : 1 zombie
    de 08:11 que rien n'aurait jamais nettoyé.
    """
    # LIMITE ASSUMÉE : si un second processus démarre pendant qu'un premier
    # traduit encore, il marquera en erreur un job bien vivant. Le dégât est
    # transitoire — le worker du premier appellera `jobs.done`, qui repasse la
    # ligne à `done` avec son `translated_path` et annule le remboursement
    # fait ici (`rebill_for_document`) : livré, donc facturé.
    try:
        from app.core.database import async_session
        from app.models import Document
        from sqlalchemy import update
        async with async_session() as db:
            r = await db.execute(
                update(Document)
                .where(Document.status == "translating")
                .values(status="error")
                .returning(Document.id)
            )
            ids = [row[0] for row in r.fetchall()]
            perdus = len(ids)
            # Même sortie qu'un échec ordinaire (`jobs.error`) : la
            # consommation est remboursée, sinon l'arrêt du serveur coûterait
            # au client ses pages achetées ou son offre du mois.
            from app.services.usage import refund_for_document
            for doc_id in ids:
                await refund_for_document(db, doc_id)
            await db.commit()
        if perdus:
            logger.warning(
                f"{perdus} traduction(s) interrompue(s) par un arrêt précédent : "
                f"marquée(s) en erreur."
            )
    except Exception as e:
        # Ne JAMAIS empêcher le serveur de démarrer pour un ménage.
        logger.warning(f"Réconciliation des jobs orphelins impossible : {e}")


def balayer_partiels_orphelins() -> None:
    """Efface les `partial_*.pdf` d'un processus précédent.

    Un PDF partiel n'appartient qu'à UN job, et les jobs vivent en mémoire :
    au démarrage, tout partiel présent sur le disque est orphelin par
    construction (même raisonnement que `reconcilier_jobs_orphelins`). Sans ce
    balayage ils s'accumulaient et maintenaient leur dossier en vie.
    """
    efface = 0
    for path in glob.glob(os.path.join(TRANSLATIONS_DIR, "**", "partial_*.pdf"),
                          recursive=True):
        try:
            os.remove(path)
            efface += 1
        except OSError:
            pass
    if efface:
        logger.info(f"{efface} PDF partiel(s) orphelin(s) balayé(s).")
