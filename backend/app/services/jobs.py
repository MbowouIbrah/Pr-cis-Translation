"""Gestionnaire des travaux de traduction.

UN JOB, C'EST QUOI
------------------
Une traduction lancée par une requête, exécutée dans un THREAD worker, et
suivie par le client via SSE. Il porte :
  - une file thread-safe pour les messages de progression ;
  - un état (pending / running / done / error) ;
  - le chemin du rendu une fois terminé ;
  - le propriétaire, sans lequel un `job_id` deviné suffirait à récupérer le
    document d'autrui.

POURQUOI UNE CLASSE
-------------------
Ces données vivaient dans deux variables de module (`_jobs`, `_jobs_lock`) que
huit fonctions manipulaient. Rien n'empêchait un accès sans verrou, et rien ne
disait où était la frontière. Ici l'état est privé et le verrou est pris par
les méthodes : on ne peut plus lire la table sans passer par elles.

L'instance `jobs` en fin de module est le point d'accès unique du processus.
"""
from __future__ import annotations

import asyncio
import os
import queue
import re
import threading
import time
import uuid
from typing import Any, Callable

from app.config import (
    JOB_MAX_AGE_SECONDS,
    JOB_RETENTION_SECONDS,
    PROGRESS_EVERY_S,
    logger,
)


def _dans_une_boucle_jetable(faire: Callable[[Any], Any]) -> None:
    """Exécute une coroutine de base de données depuis un THREAD worker.

    Moteur DÉDIÉ à connexion NON poolée : on tourne dans une boucle jetable, or
    le pool du moteur global est lié à la boucle principale d'uvicorn — y
    réutiliser une connexion lèverait « Event loop is closed ». NullPool ouvre
    une connexion neuve sur CETTE boucle et la ferme avec le moteur.

    `faire` reçoit une session ouverte et fait son travail ; le commit et la
    fermeture sont pris en charge ici.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import DATABASE_URL

    async def _run():
        eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
        try:
            async with AsyncSession(eng) as db:
                await faire(db)
                await db.commit()
        finally:
            await eng.dispose()

    asyncio.run(_run())


class JobManager:
    """Table des travaux en cours, et leur report en base."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ── Cycle de vie ─────────────────────────────────────────────────────
    def create(self) -> str:
        """Ouvre un job et retourne son identifiant."""
        self.collect()
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "state": "pending",
                "created_at": time.time(),
                "q": queue.Queue(),
                "result_path": None,
                "result_filename": None,
                "partial_path": None,   # PDF v2 : fichier partiel (pages prêtes)
                "error": None,
                # PROPRIÉTAIRE du job. Sans lui, un job_id deviné suffisait à
                # récupérer le document d'autrui : /partial et /result ne
                # vérifiaient que la clé d'API, laquelle est publique (elle est
                # dans le bundle du frontend).
                "user_id": None,
            }
        return job_id

    def collect(self) -> None:
        """Oublie les jobs finis depuis > 30 min et efface leurs fichiers
        TRANSITOIRES (PDF partiel + rendu). La traduction persistante
        (pages.json) n'est jamais touchée ici : elle vit dans le magasin,
        protégée par la purge par références."""
        now = time.time()
        with self._lock:
            morts = [
                (jid, j) for jid, j in self._jobs.items()
                if (j.get("finished_at")
                    and now - j["finished_at"] > JOB_RETENTION_SECONDS)
                or (now - j.get("created_at", now) > JOB_MAX_AGE_SECONDS)
            ]
            for jid, _ in morts:
                del self._jobs[jid]
        for _, j in morts:
            for f in (j.get("partial_path"), j.get("result_path")):
                if f and os.path.isfile(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass

    # ── Accès ────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> dict | None:
        """Le job, ou None. Lecture sous verrou."""
        with self._lock:
            return self._jobs.get(job_id)

    def set(self, job_id: str, **champs) -> None:
        """Pose des champs sur un job existant. Écriture sous verrou."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(champs)

    def forget(self, job_id: str) -> None:
        """Oublie un job sans attendre le ménage.

        Le ménage automatique ne prend que les jobs FINIS depuis assez
        longtemps ; celui-ci retire un job à la demande — un abandon, ou un
        contexte de test qui range derrière lui. Ne touche à aucun fichier :
        seul `collect()` en efface, et seulement ceux qu'il a laissés mûrir.
        """
        with self._lock:
            self._jobs.pop(job_id, None)

    # ── Progression ──────────────────────────────────────────────────────
    def emit(self, job_id: str, event_type: str, payload: dict) -> None:
        """Publie un événement vers le flux SSE du job."""
        job = self.get(job_id)
        if job:
            job["q"].put({"type": event_type, **payload})

    def progress_callback(self, job_id: str, step: str,
                          total: int | None = None) -> Callable[[str], None]:
        """Retourne un progress_callback lié à un job et à une étape."""
        counter = [0]

        def cb(msg: str) -> None:
            # Détecter le pattern "page X/N" pour afficher la progression fine
            page, tot = None, total
            m = re.search(r'(\d+)/(\d+)', msg)
            if m:
                page, tot = int(m.group(1)), int(m.group(2))
                counter[0] = page
            self.emit(job_id, "progress", {
                "step": step,
                "message": msg,
                "page": page or counter[0],
                "total": tot,
            })
            logger.info(msg)

        return cb

    # ── Fin de vie ───────────────────────────────────────────────────────
    def done(self, job_id: str, result_path: str, filename: str,
             translation_path: str | None = None) -> None:
        """`result_path` = le rendu (transitoire) que /result sert tout de
        suite. `translation_path` = la traduction PERSISTANTE
        (pages.json/translated.json) consignée en base : c'est elle qui permet
        de recalculer le rendu plus tard, pas le fichier transitoire. Faute de
        quoi `Document.translated_path` pointerait sur un rendu que le ménage
        efface au bout de 30 min."""
        job = self.get(job_id)
        if job:
            job["result_path"] = result_path
            job["result_filename"] = filename
            job["state"] = "done"
            job["finished_at"] = time.time()
            job["q"].put({"type": "done", "filename": filename})
        self.sync_status(job_id, "done",
                         translated_path=translation_path or result_path)

    def error(self, job_id: str, message: str) -> None:
        job = self.get(job_id)
        if job:
            job["error"] = message
            job["state"] = "error"
            job["finished_at"] = time.time()
            job["q"].put({"type": "error", "message": message})
        self.sync_status(job_id, "error")

    # ── Report en base ───────────────────────────────────────────────────
    def _document_id(self, job_id: str) -> str | None:
        job = self.get(job_id)
        return job.get("document_id") if job else None

    def sync_progress(self, job_id: str, done: int,
                      total: int | None = None) -> None:
        """Consigne l'avancement en base, pour qu'il survive à la session.

        ÉCRITURE LIMITÉE. Un document de 285 pages ferait 285 transactions,
        chacune ouvrant sa propre connexion (voir `_dans_une_boucle_jetable`).
        On n'écrit donc que toutes les `PROGRESS_EVERY_S` secondes — sauf la
        DERNIÈRE page, qu'on écrit toujours : c'est celle qui fait passer la
        barre à 100 %, et la sauter laisserait un document terminé affiché à
        97 %.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            doc_id = job.get("document_id") if job else None
            if not doc_id:
                return
            derniere = total is not None and done >= total
            if not derniere:
                precedent = job.get("_progress_at", 0.0)
                if time.time() - precedent < PROGRESS_EVERY_S:
                    return
            job["_progress_at"] = time.time()

        async def _maj(db):
            from app.models import Document
            doc = await db.get(Document, doc_id)
            if doc is None:
                return
            doc.pages_done = done
            if total and not doc.page_count:
                doc.page_count = total

        try:
            _dans_une_boucle_jetable(_maj)
        except Exception as e:
            # L'avancement est un CONFORT : son échec ne doit jamais
            # interrompre une traduction en cours, qui elle a de la valeur.
            # Mais il doit se VOIR — un `except: pass` muet a déjà coûté une
            # heure de recherche ici même : la barre restait à zéro sans que
            # rien ne le signale. `asyncio.run` échoue notamment si on
            # l'appelle depuis une boucle déjà en cours ; ce chemin n'est
            # légitime que depuis un thread worker.
            logger.warning("Avancement non consigné (doc %s, %s pages) : %s",
                           doc_id, done, e)

    def sync_status(self, job_id: str, status: str,
                    translated_path: str | None = None) -> None:
        """Reporte l'état d'un job sur le Document en base (si l'utilisateur
        était connecté). Sans ce report, le Document restait éternellement
        `translating` et `translated_path` NULL : le téléchargement servait
        alors l'ORIGINAL au lieu de la traduction."""
        doc_id = self._document_id(job_id)
        if not doc_id:
            return

        async def _maj(db):
            from app.models import Document
            doc = await db.get(Document, doc_id)
            if doc is None:
                return
            doc.status = status
            if translated_path:
                doc.translated_path = translated_path

        try:
            _dans_une_boucle_jetable(_maj)
        except Exception:
            pass


# Point d'accès unique du processus.
jobs = JobManager()
