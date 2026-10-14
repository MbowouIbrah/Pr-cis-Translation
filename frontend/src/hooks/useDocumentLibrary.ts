import { useState, useCallback, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';
import api, { authFetch } from '../services/api';

export interface DocMeta {
  id: string;
  filename: string;
  originalName: string;
  targetLang: string;
  date: string;
  sizeByes: number;
  ext: string;
  /** Ce document est-il PAYÉ ? C'est lui, et non le plan, qui ouvre le
   *  téléchargement et l'aperçu en clair (miroir de `_may_read_clear`). */
  paid: boolean;
  /** Nombre de pages — sert à calculer ce que coûtera son déblocage. */
  pageCount: number;
  /** `translating` · `done` · `error`. C'est la SEULE source de vérité sur
   *  l'état d'une traduction : elle vit en base, donc elle survit à une
   *  déconnexion comme à un redémarrage du serveur. */
  status: string;
  /** Pages terminées, à comparer à `pageCount`. */
  pagesDone: number;
  /** blob dispo uniquement pour les visiteurs (en mémoire) */
  _blob?: Blob;
}

/** UNE traduction de la réponse serveur. Elle existait en double dans ce
 *  fichier : le jour où un champ s'ajoute, une des deux copies l'oublie —
 *  c'est exactement ce qui serait arrivé à `paid`. */
function toMeta(d: any): DocMeta {
  return {
    id: d.id,
    filename: d.original_name,
    originalName: d.original_name,
    targetLang: d.target_lang,
    date: d.created_at,
    sizeByes: d.size_bytes,
    ext: d.original_name.split('.').pop()?.toLowerCase() ?? 'pdf',
    paid: !!d.paid,
    pageCount: d.page_count ?? 1,
    status: d.status ?? 'done',
    pagesDone: d.pages_done ?? 0,
    _blob: undefined,
  };
}

const LS_LEGACY = 'precis_doc_library';

/**
 * Bibliothèque de documents.
 *
 * - Visiteur (non connecté) : stockage mémoire uniquement, rien n'est persisté.
 *   Fermer l'onglet = tout est perdu.
 * - Connecté : documents chargés depuis /api/documents, téléchargement via
 *   /api/documents/{id}/download. Pas de localStorage, pas d'IndexedDB.
 */
export function useDocumentLibrary() {
  const { user } = useAuth();
  const [documents, setDocuments] = useState<DocMeta[]>([]);
  const [loading, setLoading] = useState(false);

  // Nettoyer les anciennes données localStorage au montage
  useEffect(() => {
    try { localStorage.removeItem(LS_LEGACY); } catch { /* ignore */ }
  }, []);

  /** Relire la liste au serveur. Après un paiement, `paid` a changé côté
   *  base : sans relecture, le bouton continuerait d'afficher « Payer » sur un
   *  document que l'utilisateur vient de régler. */
  const refresh = useCallback(async () => {
    if (!user) return;
    const res = await api.get('/api/documents');
    if (res.ok) setDocuments((res.data as any[]).map(toMeta));
  }, [user]);

  // Connecté → charger depuis l'API
  useEffect(() => {
    if (!user) return;
    (async () => {
      setLoading(true);
      await refresh();
      setLoading(false);
    })();
  }, [user, refresh]);

  /** Y a-t-il au moins une traduction en cours ? */
  const enCours = documents.some(d => d.status === 'translating');

  // Tant qu'une traduction tourne, on relit la liste. C'est ce qui rend le
  // suivi INDÉPENDANT de la session qui a lancé le travail : l'avancement est
  // en base, donc un autre onglet, un autre appareil, ou la même personne
  // après reconnexion, le voient avancer.
  //
  // On n'interroge QUE s'il y a quelque chose à suivre — un intervalle qui
  // tourne en permanence sur une bibliothèque au repos ne fait que du bruit.
  useEffect(() => {
    if (!user || !enCours) return;
    const id = window.setInterval(() => { refresh(); }, 4000);
    return () => clearInterval(id);
  }, [user, enCours, refresh]);

  /** Ajouter un document (blob + meta). Visiteur = mémoire, Connecté = déjà fait côté backend. */
  const saveDocument = useCallback(async (
    blob: Blob, filename: string,
    // `paid` et `pageCount` ne sont PAS demandés à l'appelant : ils viennent du
    // serveur pour un compte connecté, et n'ont pas de sens pour un visiteur
    // (rien n'est persisté, donc rien n'est payé).
    meta: Omit<DocMeta,
      'id' | 'date' | 'sizeByes' | 'filename' | '_blob'
      | 'paid' | 'pageCount' | 'status' | 'pagesDone'>,
  ): Promise<string> => {
    const id = crypto.randomUUID();
    const doc: DocMeta = {
      ...meta, id, filename,
      date: new Date().toISOString(),
      sizeByes: blob.size,
      paid: false,
      pageCount: 1,
      status: 'done',
      pagesDone: 1,
      _blob: blob,
    };
    if (user) {
      // Pour les connectés, le blob est déjà sauvegardé côté backend
      // lors de la traduction. On recharge la liste.
      const res = await api.get('/api/documents');
      if (res.ok) setDocuments((res.data as any[]).map(toMeta));
      return id;
    }
    setDocuments(prev => [doc, ...prev]);
    return id;
  }, [user]);

  /** Récupérer le blob d'un document. Connecté = download API, Visiteur = mémoire.
   *
   *  `authFetch` et pas `api.get` : ce dernier fait `res.json()` et ne peut donc
   *  JAMAIS rendre un blob (`res.data instanceof Blob` était toujours faux, le
   *  premier appel était perdu à chaque fois). Le repli lisait le token dans
   *  `localStorage` — donc l'ANCIEN après un refresh, d'où des 401 sur une
   *  session pourtant valide. `authFetch` prend le token en mémoire et rejoue
   *  l'appel après refresh. */
  const getBlob = useCallback(async (id: string): Promise<Blob | undefined> => {
    if (user) {
      const res = await authFetch(`/api/documents/${id}/download`);
      return res.ok ? res.blob() : undefined;
    }
    return documents.find(d => d.id === id)?._blob;
  }, [user, documents]);

  /** Blob d'AFFICHAGE : passe par /preview, qui rastérise et filigrane pour un
   *  plan d'essai. « Aperçu » ne doit jamais emprunter /download — c'est par là
   *  qu'un compte gratuit récupérait sa traduction en clair. */
  const getPreviewBlob = useCallback(async (
    id: string, page?: number,
  ): Promise<{ blob: Blob | null; full: boolean; inProgress: boolean } | undefined> => {
    if (user) {
      // `page` restreint le RENDU à la page regardée (280 s le document
      // entier contre ~10 s la page seule, mesuré sur 285 pages). Mais si le
      // serveur possède le rendu COMPLET en cache, il l'envoie directement et
      // le dit par `X-Render: full` : le client a alors tout le document
      // traduit et n'a PLUS RIEN à demander pendant la navigation.
      //
      // Document encore EN COURS de traduction : `partial` (pages déjà
      // traduites) ou `pending` (aucune encore, 202 sans corps). Ni l'un ni
      // l'autre ne se garde en cache : l'appelant redemande jusqu'au rendu final.
      const q = page && page > 0 ? `?page=${page}` : '';
      const res = await authFetch(`/api/documents/${id}/preview${q}`);
      if (!res.ok) return undefined;
      const render = res.headers.get('X-Render');
      const pending = render === 'pending';
      return {
        blob: pending ? null : await res.blob(),
        full: render === 'full',
        inProgress: pending || render === 'partial',
      };
    }
    const blob = documents.find(d => d.id === id)?._blob;
    return blob ? { blob, full: true, inProgress: false } : undefined;
  }, [user, documents]);

  /** Le fichier SOURCE tel que déposé — panneau gauche de l'aperçu.
   *  `/download` rend la traduction dès qu'elle existe : il ne peut pas servir
   *  à ça. Sans cette route, le viewer n'avait pas de source et affichait le
   *  PDF de DÉMO à côté du document de l'utilisateur. */
  /** @param asPdf demander la source DÉJÀ convertie en PDF (panneau gauche de
   *   l'aperçu). Sans lui, le client téléchargeait le fichier natif puis le
   *   renvoyait au serveur pour conversion : trois transferts, et un panneau
   *   blanc dès que ce détour échouait. La relance d'une traduction, elle, a
   *   besoin du fichier NATIF — elle appelle donc sans ce drapeau. */
  const getOriginalBlob = useCallback(async (
    id: string, asPdf = false,
  ): Promise<Blob | undefined> => {
    if (user) {
      const q = asPdf ? '?as=pdf' : '';
      const res = await authFetch(`/api/documents/${id}/original${q}`);
      return res.ok ? res.blob() : undefined;
    }
    return undefined;
  }, [user]);

  const deleteDocument = useCallback(async (id: string) => {
    if (user) {
      await api.delete(`/api/documents/${id}`);
      setDocuments(prev => prev.filter(d => d.id !== id));
      return;
    }
    setDocuments(prev => prev.filter(d => d.id !== id));
  }, [user]);

  const clearAll = useCallback(async () => {
    if (user) {
      for (const d of documents) await api.delete(`/api/documents/${d.id}`);
    }
    setDocuments([]);
  }, [user, documents]);

  return { documents, loading, refresh, saveDocument, getBlob, getPreviewBlob,
           getOriginalBlob, deleteDocument, clearAll };
}
