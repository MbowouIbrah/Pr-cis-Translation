"""Ordonnanceur des traductions — file de PRIORITÉ bornée.

LE PROBLÈME QU'IL RÉSOUT
------------------------
Chaque `/api/translate` lançait AUSSITÔT son propre thread, sans aucune borne :
tout tournait en parallèle, à la merci de la machine. Rien ne distinguait un
compte payant d'un compte gratuit — la « vitesse » vendue sur la carte de tarifs
n'existait nulle part dans le code.

CE QU'IL FAIT
-------------
Un nombre FIXE de workers (`TRANSLATION_WORKERS`) mène les traductions de front.
Au-delà, les demandes attendent dans une `PriorityQueue` : le worker suivant
sert TOUJOURS la plus prioritaire (priorité du plan : admin > pro > starter >
gratuit), et à priorité égale la plus ancienne (FIFO). Payer, c'est passer
devant ceux qui attendent.

CE QU'IL NE FAIT PAS
--------------------
Pas de PRÉEMPTION : un job prioritaire ne coupe pas un job déjà en cours, il
passe seulement devant ceux qui n'ont pas encore démarré. Interrompre un rendu
à mi-course gâcherait le travail fait sans rien garantir de mieux. « Prioritaire »
veut donc dire « en tête de file », pas « instantané » — l'interface ne doit
promettre que ça.
"""
from __future__ import annotations

import itertools
import queue
import threading
from typing import Any, Callable

from app.config import TRANSLATION_WORKERS, logger
from app.services.jobs import jobs


class TranslationScheduler:
    """Pool de workers + file de priorité. Un seul exemplaire par processus."""

    def __init__(self, workers: int) -> None:
        self._workers = max(1, workers)
        self._pq: queue.PriorityQueue = queue.PriorityQueue()
        # Compteur monotone : départage deux jobs de MÊME priorité par ordre
        # d'arrivée (FIFO), et — tout aussi important — garantit que la file ne
        # compare JAMAIS les charges utiles entre elles (fonctions, tuples
        # d'arguments non ordonnables). La clé (-prio, seq) est toujours unique.
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._running = 0                      # jobs occupant un worker
        self._threads: list[threading.Thread] = []
        for i in range(self._workers):
            t = threading.Thread(target=self._worker, name=f"xl-worker-{i}",
                                 daemon=True)
            t.start()
            self._threads.append(t)

    # ── Soumission ───────────────────────────────────────────────────────────
    def submit(self, job_id: str, priority: int,
               target: Callable[..., Any], args: tuple) -> None:
        """Met un job en file. `priority` = niveau du plan (plus grand = plus
        prioritaire). Émet un événement `queued` SI aucun worker n'est libre —
        c'est ce qui permet à l'interface d'afficher « en file d'attente »."""
        seq = next(self._seq)
        with self._lock:
            # Un worker est-il disponible tout de suite ? Sinon, ce job attend
            # DERRIÈRE ceux déjà en file. `ahead` est une estimation d'affichage,
            # pas un contrat : il ne tient pas compte des priorités relatives,
            # juste du nombre de gens devant.
            will_wait = self._running >= self._workers
            ahead = self._pq.qsize() if will_wait else 0
        # -priority : PriorityQueue sert le plus PETIT en premier, or on veut le
        # plus prioritaire d'abord.
        self._pq.put((-priority, seq, job_id, target, args))
        if will_wait:
            jobs.emit(job_id, "queued", {"ahead": ahead})
            logger.info("Job %s en file (priorité %s, %s devant)",
                        job_id, priority, ahead)

    # ── Boucle worker ────────────────────────────────────────────────────────
    def _worker(self) -> None:
        while True:
            _negprio, _seq, job_id, target, args = self._pq.get()
            with self._lock:
                self._running += 1
            try:
                target(*args)
            except Exception as e:
                # Le job lui-même signale déjà ses propres erreurs (`jobs.error`).
                # Ce filet n'existe que pour qu'une exception inattendue ne TUE
                # pas le worker — sans quoi on perdrait une place de traitement à
                # chaque plantage, jusqu'à figer toute la file.
                logger.error("Worker : job %s a levé %s", job_id, e)
                try:
                    jobs.error(job_id, "Erreur interne pendant la traduction.")
                except Exception:
                    pass
            finally:
                with self._lock:
                    self._running -= 1
                self._pq.task_done()


# Point d'accès unique du processus.
scheduler = TranslationScheduler(TRANSLATION_WORKERS)
