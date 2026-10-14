import { useState, useCallback, useRef, useEffect } from 'react';
import api from '../services/api';
import i18n from '../i18n';

/**
 * Paiement mobile money — devis, encaissement, attente.
 *
 * Le temps long est ici la RÈGLE, pas l'exception : l'utilisateur doit sortir
 * son téléphone, attendre l'invite USSD et saisir son code. Une minute passe
 * très bien si on montre où on en est ; elle passe très mal derrière un écran
 * figé. Rien dans ce hook ne bloque : on lance, et on interroge.
 */

export interface Quote {
  pages: number;
  /** Pages facturées : ≥ `pages`, arrondi au montant minimum de l'opérateur. */
  billed_pages: number;
  unit_price: number;
  amount: number;
  min_amount: number;
  currency: string;
  zone: string;
}

export type PayPhase = 'idle' | 'quoting' | 'starting' | 'waiting' | 'done' | 'failed';

export interface PaymentState {
  phase: PayPhase;
  quote: Quote | null;
  /** Code à composer si l'invite n'arrive pas d'elle-même. */
  ussdCode: string | null;
  operator: string | null;
  error: string | null;
}

// Campay laisse l'invite ouverte quelques minutes. On interroge toutes les 3 s
// et on renonce à 3 min : au-delà, l'invite a expiré côté opérateur et
// continuer à interroger n'apprendrait plus rien.
const POLL_MS = 3000;
const TIMEOUT_MS = 180_000;

export function usePayment() {
  const [state, setState] = useState<PaymentState>({
    phase: 'idle', quote: null, ussdCode: null, operator: null, error: null,
  });
  const timer = useRef<number | null>(null);
  const startedAt = useRef(0);

  const stop = useCallback(() => {
    if (timer.current !== null) { clearInterval(timer.current); timer.current = null; }
  }, []);

  // Un composant démonté (dialogue fermé, navigation) ne doit pas laisser un
  // intervalle courir : il interrogerait le serveur indéfiniment.
  useEffect(() => stop, [stop]);

  const reset = useCallback(() => {
    stop();
    setState({ phase: 'idle', quote: null, ussdCode: null, operator: null, error: null });
  }, [stop]);

  /** Combien coûtera la traduction — AVANT de la lancer. */
  const getQuote = useCallback(async (pages: number, documentId?: string) => {
    setState((s) => ({ ...s, phase: 'quoting', error: null }));
    const res = await api.post('/api/payments/quote', {
      pages, document_id: documentId ?? null,
    });
    if (!res.ok) {
      setState((s) => ({ ...s, phase: 'failed', error: 'Tarif indisponible.' }));
      return null;
    }
    const quote = res.data as Quote;
    setState((s) => ({ ...s, phase: 'idle', quote }));
    return quote;
  }, []);

  /**
   * Déclenche l'invite sur le téléphone, puis suit le paiement jusqu'à son
   * issue. Résout à `true` seulement si l'encaissement a RÉUSSI.
   */
  const pay = useCallback(async (
    pages: number, phone: string, documentId?: string,
  ): Promise<boolean> => {
    setState((s) => ({ ...s, phase: 'starting', error: null }));

    const res = await api.post('/api/payments/collect', {
      pages, phone, document_id: documentId ?? null,
    });
    if (!res.ok) {
      const detail = (res.data as any)?.detail;
      setState((s) => ({ ...s, phase: 'failed', error: detail || i18n.t('payment.pay_launch_failed') }));
      return false;
    }

    const { payment_id, ussd_code, operator } = res.data as any;
    setState((s) => ({ ...s, phase: 'waiting', ussdCode: ussd_code ?? null, operator: operator ?? null }));
    startedAt.current = Date.now();

    return new Promise<boolean>((resolve) => {
      stop();
      timer.current = window.setInterval(async () => {
        if (Date.now() - startedAt.current > TIMEOUT_MS) {
          stop();
          setState((s) => ({
            ...s, phase: 'failed',
            error: i18n.t('payment.pay_expired'),
          }));
          resolve(false);
          return;
        }

        const st = await api.get(`/api/payments/${payment_id}`);
        // Une interrogation qui échoue n'est PAS un paiement échoué : le
        // réseau du navigateur peut tomber pendant que l'encaissement, lui,
        // aboutit. On laisse le cycle suivant retenter.
        if (!st.ok) return;

        const status = (st.data as any)?.status;
        if (status === 'SUCCESSFUL') {
          stop();
          setState((s) => ({ ...s, phase: 'done' }));
          resolve(true);
        } else if (status === 'FAILED') {
          stop();
          setState((s) => ({
            ...s, phase: 'failed',
            error: i18n.t('payment.pay_refused'),
          }));
          resolve(false);
        }
      }, POLL_MS);
    });
  }, [stop]);

  return { ...state, getQuote, pay, reset };
}
