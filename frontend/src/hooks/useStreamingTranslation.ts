import { useState, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { authHeader, accessToken } from '../services/api';
import i18n from '../i18n';

// Pas de valeur de repli : cf. usePdfPreview.ts — une clé écrite ici part dans
// le dépôt, et le repli rendait l'oubli de `VITE_API_KEY` invisible.
const API_KEY = import.meta.env.VITE_API_KEY || '';
const API_BASE = import.meta.env.VITE_API_BASE || '';

/** État d'une page dans le pipeline progressif. */
export type PageStatus =
  | 'waiting'      // pas encore commencée
  | 'extracting'   // extraction des objets
  | 'translating'  // traduction DeepSeek
  | 'rendering'    // reconstruction du PDF
  | 'done'         // page traduite, affichable
  | 'copied';      // hors sélection : copiée de l'original (déjà affichable)

export interface StreamingState {
  isTranslating: boolean;
  totalPages: number | null;
  /** Statut par numéro de page (1-basé). */
  pageStatuses: Record<number, PageStatus>;
  /** PDF partiel, mis à jour au fil de l'eau. */
  partialBlob: Blob | null;
  /** Pages dont `partialBlob` montre VRAIMENT la traduction. Un ensemble, pas
   *  un « jusqu'à N » : les diapositives PPTX se traduisent en parallèle et
   *  dans le désordre, et le partiel PPTX contient TOUTES les pages (celles
   *  pas encore traduites y sont l'original). « Pages 1..N prêtes » y faisait
   *  passer des pages en langue source pour traduites. */
  readyPages: Record<number, true>;
  /** Résultat final complet (quand terminé). */
  result: { blob: Blob; filename: string } | null;
  error: string | null;
  /** L'erreur vient d'un 402 (limite de forfait atteinte) → afficher un CTA. */
  limitReached: boolean;
  /** 402 rattrapable par un ACHAT : nombre de pages à régler pour ce document
   *  (le forfait Gratuit n'a plus de crédit). `null` = pas ce cas. */
  creditsNeeded: number | null;
  /** En file d'attente : aucun worker libre. `null` = pas en file (démarré ou
   *  terminé). Le nombre est une estimation des demandes devant la vôtre. */
  queuePosition: number | null;
}

const EMPTY: StreamingState = {
  isTranslating: false,
  totalPages: null,
  pageStatuses: {},
  partialBlob: null,
  readyPages: {},
  result: null,
  error: null,
  limitReached: false,
  creditsNeeded: null,
  queuePosition: null,
};

export function useStreamingTranslation() {
  const { t } = useTranslation();
  const [state, setState] = useState<StreamingState>(EMPTY);
  const esRef = useRef<EventSource | null>(null);
  const jobIdRef = useRef<string | null>(null);
  // Évite les fetch de partiel concurrents (une page peut terminer pendant
  // qu'on télécharge encore le partiel de la précédente).
  const fetchingRef = useRef(false);
  const pendingFetchRef = useRef(false);
  // Dernier partiel reçu. En essai, /result est refusé (402) : c'est lui qui
  // fait office de résultat. Un ref, pas un state : on le lit dans le
  // gestionnaire SSE, hors du cycle de rendu.
  const lastPartialRef = useRef<Blob | null>(null);
  // Statuts vus par le gestionnaire SSE, lus hors du cycle de rendu.
  const statusesRef = useRef<Record<number, PageStatus>>({});
  // Pages terminées dont l'aperçu progressif a échoué (`apercu: false`) : le
  // partiel les montre encore en langue source, jusqu'au résultat final.
  const noPreviewRef = useRef<Set<number>>(new Set());

  const cancel = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setState((s) => ({ ...s, isTranslating: false }));
  }, []);

  const fetchPartial = useCallback(async () => {
    const jobId = jobIdRef.current;
    if (!jobId) return;
    if (fetchingRef.current) {
      pendingFetchRef.current = true;      // coalescer : on refera après
      return;
    }
    fetchingRef.current = true;
    // Relevé AVANT la requête : le serveur écrit le partiel puis émet `done`,
    // donc toute page déjà terminée à cet instant est dans ce qu'on va lire.
    // Relevée après, une page finie pendant le transfert serait déclarée
    // prête sur un fichier qui la montre encore en langue source.
    const ready: Record<number, true> = {};
    for (const [p, st] of Object.entries(statusesRef.current)) {
      const n = Number(p);
      if ((st === 'done' || st === 'copied') && !noPreviewRef.current.has(n)) ready[n] = true;
    }
    try {
      const res = await fetch(`${API_BASE}/api/translate/partial/${jobId}`, {
        headers: { 'X-API-Key': API_KEY, ...authHeader() },
      });
      if (res.ok) {
        const blob = await res.blob();
        lastPartialRef.current = blob;   // repli si /result est refusé (essai)
        setState((s) => ({
          ...s,
          partialBlob: blob,
          readyPages: { ...s.readyPages, ...ready },
        }));
      }
    } catch {
      // Le partiel peut être momentanément indisponible (réécriture atomique).
    } finally {
      fetchingRef.current = false;
      if (pendingFetchRef.current) {
        pendingFetchRef.current = false;
        fetchPartial();
      }
    }
  }, []);

  const start = useCallback(
    (
      file: File,
      targetLang: string,
      pages: string = '',
      debug: boolean = false,
      precise: boolean = false,
      onAccepted?: () => void,
    ): Promise<{ blob: Blob; filename: string }> => {
      return new Promise(async (resolve, reject) => {
        // Fermer TOUT flux encore ouvert avant d'en démarrer un nouveau. Relancer
        // une traduction alors qu'une était en cours (ou quittée par Retour)
        // laissait l'ancien `EventSource` ouvert : une connexion fuitée, et ses
        // événements `page`/`partial` qui venaient se mélanger à ceux du nouveau
        // job. L'ancien job continue côté serveur et reste dans la bibliothèque
        // (voulu) ; seul son SUIVI est abandonné proprement.
        esRef.current?.close();
        esRef.current = null;

        setState({ ...EMPTY, isTranslating: true });
        // Sans ça, un 402 survenant avant le premier partiel du NOUVEAU
        // document résoudrait avec celui du PRÉCÉDENT — on afficherait le
        // mauvais document sans que rien ne signale l'erreur.
        lastPartialRef.current = null;
        jobIdRef.current = null;
        fetchingRef.current = false;
        pendingFetchRef.current = false;
        statusesRef.current = {};
        noPreviewRef.current = new Set();

        try {
          // `quality` et `format_options` ne sont plus envoyés : le moteur PDF v2
          // ne les lit pas (il préserve toujours la mise en page et n'accepte pas
          // de modèle alternatif). Le serveur garde des valeurs par défaut.
          const formData = new FormData();
          formData.append('file', file);
          formData.append('target_lang', targetLang);
          if (debug) formData.append('debug', '1');
          if (precise) formData.append('precise', '1');
          if (pages && pages.trim()) formData.append('pages', pages.trim());

          // `authHeader()` est INDISPENSABLE ici, pas décoratif : le backend lit
          // l'utilisateur via `optional_auth`. Sans token il voit un visiteur
          // anonyme, n'enregistre AUCUN `Document` (la bibliothèque reste vide)
          // et n'applique pas le quota freemium. Un visiteur envoie `{}` et
          // reste anonyme, comme avant.
          const startRes = await fetch(`${API_BASE}/api/translate`, {
            method: 'POST',
            headers: { 'X-API-Key': API_KEY, ...authHeader() },
            body: formData,
          });
          if (!startRes.ok) {
            const err = await startRes.json().catch(() => ({}));
            const detail = err.detail || err.error || t('story.error_default');
            const is402 = startRes.status === 402;
            // 402 « crédits insuffisants » : le serveur dit COMBIEN de pages
            // acheter — on le garde pour ouvrir le paiement à la page directement.
            const needed = (err.reason === 'need_credits' && typeof err.pages_needed === 'number')
              ? err.pages_needed as number : null;
            setState((s) => ({
              ...s, isTranslating: false, error: detail,
              limitReached: is402, creditsNeeded: needed,
            }));
            throw new Error(detail);
          }
          const { job_id } = await startRes.json();
          jobIdRef.current = job_id;
          // Le serveur a enregistré le Document AVANT de répondre : ce qui
          // dépendait de son existence peut maintenant se faire.
          onAccepted?.();

          // Le JWT passe en query : EventSource ne porte pas de header, et le
          // backend n'accepte plus un flux anonyme (la file d'événements est à
          // consommateur unique — un tiers la viderait).
          const es = new EventSource(
            `${API_BASE}/api/translate/events/${job_id}?token=${encodeURIComponent(accessToken() ?? '')}`,
          );
          esRef.current = es;

          es.onmessage = async (e) => {
            if (!e.data || e.data.startsWith(':')) return;
            let msg: Record<string, unknown>;
            try { msg = JSON.parse(e.data); } catch { return; }
            const type = msg.type as string;

            if (type === 'queued') {
              // Aucun worker libre : le job attend son tour dans la file de
              // priorité. On l'affiche sans rien changer d'autre.
              const ahead = (msg.ahead as number) ?? 0;
              setState((s) => ({ ...s, queuePosition: ahead }));
              return;
            }
            // Tout autre événement veut dire que le job a QUITTÉ la file (un
            // worker l'a pris) : on efface l'état d'attente une bonne fois.
            setState((s) => (s.queuePosition === null ? s : { ...s, queuePosition: null }));

            if (type === 'start') {
              const total = (msg.total as number) ?? null;
              setState((s) => {
                const statuses: Record<number, PageStatus> = {};
                if (total) for (let i = 1; i <= total; i++) statuses[i] = 'waiting';
                return { ...s, totalPages: total, pageStatuses: statuses };
              });
            } else if (type === 'page') {
              const page = msg.page as number;
              const status = msg.status as PageStatus;
              statusesRef.current[page] = status;
              if (status === 'done' && msg.apercu === false) noPreviewRef.current.add(page);
              setState((s) => ({
                ...s,
                pageStatuses: { ...s.pageStatuses, [page]: status },
                totalPages: (msg.total as number) ?? s.totalPages,
              }));
              if (status === 'done' || status === 'copied') {
                fetchPartial();
              }
            } else if (type === 'partial') {
              // PPTX PROGRESSIF : le backend signale qu'un nouveau PDF partiel
              // est PRÊT à être récupéré — d'abord le SOCLE (le document
              // d'origine, `pages: 0`), puis chaque lot de diapositives
              // greffées. C'est LE signal d'affichage progressif.
              //
              // Sans ce handler, l'aperçu ne se rafraîchissait que sur les
              // événements `page`. En traduction IA (lente), ça suffisait par
              // hasard : les `page` s'espaçaient assez pour laisser le partiel
              // se préparer. Mais en mode STRUCTURE (sans IA), toutes les pages
              // « terminent » en quelques millisecondes — AVANT que LibreOffice
              // ait converti la moindre diapositive. Les `fetchPartial` de ces
              // `page` tombaient dans le vide, et les partiels réellement prêts
              // (émis APRÈS, en `partial`) étaient ignorés : on ne voyait donc
              // RIEN jusqu'à la fin. En écoutant `partial`, l'original apparaît
              // dès que le socle est prêt, puis l'aperçu se remplit au rythme
              // réel des conversions — quelle que soit la vitesse de traduction.
              fetchPartial();
            } else if (type === 'done') {
              es.close();
              esRef.current = null;
              try {
                const dlRes = await fetch(`${API_BASE}/api/translate/result/${job_id}`, {
                  headers: { 'X-API-Key': API_KEY, ...authHeader() },
                });
                const filename = (msg.filename as string) ||
                  file.name.replace(/\.[^/.]+$/, '') + '_TRADUIT.' + file.name.split('.').pop();

                // 402 = le FORFAIT parle, ce n'est pas une panne. Un plan
                // d'essai n'a pas droit au résultat téléchargeable ; il a droit
                // à l'aperçu (partiel rastérisé et filigrané). Traiter ce refus
                // comme une erreur laissait la traduction bloquée sur « page en
                // attente » alors qu'elle avait parfaitement abouti.
                //
                // On REDEMANDE le partiel ICI, maintenant qu'il est GARANTI
                // complet côté serveur. Indispensable pour le PPTX : son partiel
                // est converti en PDF de façon asynchrone (LibreOffice, ~10 s) et
                // n'était souvent pas encore prêt au dernier événement `page` —
                // `lastPartialRef` restait alors vide et l'essai voyait une
                // erreur au lieu de son aperçu.
                if (dlRes.status === 402) {
                  let blob = lastPartialRef.current;
                  try {
                    const pr = await fetch(`${API_BASE}/api/translate/partial/${job_id}`, {
                      headers: { 'X-API-Key': API_KEY, ...authHeader() },
                    });
                    if (pr.ok) blob = await pr.blob();
                  } catch {
                    // réseau : on se rabat sur le dernier partiel connu
                  }
                  if (blob) {
                    // Partiel complet côté serveur : toutes les pages terminées
                    // y sont, sauf celles dont la greffe a échoué.
                    const ready: Record<number, true> = {};
                    for (const p of Object.keys(statusesRef.current).map(Number)) {
                      if (!noPreviewRef.current.has(p)) ready[p] = true;
                    }
                    setState((s) => ({ ...s, isTranslating: false, partialBlob: blob, readyPages: ready, result: { blob, filename } }));
                    resolve({ blob, filename });
                    return;
                  }
                }
                if (!dlRes.ok) {
                  const err = await dlRes.json().catch(() => ({}));
                  throw new Error(err.detail || i18n.t('common.download_failed'));
                }
                const blob = await dlRes.blob();
                setState((s) => {
                  // Résultat final : toutes les pages sont traduites.
                  const ready: Record<number, true> = {};
                  for (let p = 1; p <= (s.totalPages ?? 0); p++) ready[p] = true;
                  return { ...s, isTranslating: false, partialBlob: blob, readyPages: ready, result: { blob, filename } };
                });
                resolve({ blob, filename });
              } catch (dlErr) {
                const m = dlErr instanceof Error ? dlErr.message : t('story.error_translation_failed');
                setState((s) => ({ ...s, isTranslating: false, error: m }));
                reject(new Error(m));
              }
            } else if (type === 'error') {
              es.close();
              esRef.current = null;
              const m = (msg.message as string) || t('story.error_translation_failed');
              setState((s) => ({ ...s, isTranslating: false, error: m }));
              reject(new Error(m));
            }
          };

          es.onerror = () => {
            es.close();
            esRef.current = null;
            setState((s) => {
              // Une coupure APRÈS le done final n'est pas une erreur.
              if (!s.isTranslating) return s;
              return { ...s, isTranslating: false, error: t('story.error_connection_lost') };
            });
            reject(new Error(t('story.error_connection_lost')));
          };
        } catch (err) {
          const m = err instanceof Error ? err.message : t('story.error_unknown');
          setState((s) => ({ ...s, isTranslating: false, error: m }));
          reject(new Error(m));
        }
      });
    },
    [fetchPartial],
  );

  const reset = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    jobIdRef.current = null;
    lastPartialRef.current = null;
    setState(EMPTY);
  }, []);

  return { ...state, start, cancel, reset };
}
