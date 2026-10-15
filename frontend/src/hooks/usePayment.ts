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
  /** Achat d'abonnement : durée payée d'avance (1 ou 12 mois). */
  months?: number;
}

/** Abonnement à acheter : un plan et sa périodicité. */
export interface PlanChoice {
  key: string;
  label: string;
  annual: boolean;
}

export type PayMethodId = 'mtn_momo' | 'orange_money' | 'card';

/** Moyen de paiement de la zone. Indisponible = affiché grisé, jamais caché. */
export interface PayMethod {
  id: PayMethodId;
  available: boolean;
  needs_phone: boolean;
}

export type PayPhase ='idle' | 'quoting' | 'starting' | 'waiting' | 'done' | 'failed';

export interface PaymentState {
  phase: PayPhase;
  quote: Quote | null;
  /** `null` tant que la liste n'est pas arrivée. */
  methods: PayMethod[] | null;
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
    phase: 'idle', quote: null, methods: null, ussdCode: null, operator: null, error: null,
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
    setState({ phase: 'idle', quote: null, methods: null, ussdCode: null, operator: null, error: null });
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

  /** Prix d'un abonnement, tel que le serveur l'encaissera. Ramené à la
   *  forme d'un devis de pages pour que la modale n'ait qu'un récapitulatif. */
  const getPlanQuote = useCallback(async (plan: PlanChoice) => {
    setState((s) => ({ ...s, phase: 'quoting', error: null }));
    const res = await api.post('/api/payments/plan/quote', {
      plan: plan.key, annual: plan.annual,
    });
    if (!res.ok) {
      const detail = (res.data as any)?.detail;
      setState((s) => ({ ...s, phase: 'failed', error: detail || 'Tarif indisponible.' }));
      return null;
    }
    const d = res.data as any;
    const quote: Quote = {
      pages: 0, billed_pages: 0, unit_price: d.monthly_price, amount: d.amount,
      min_amount: 0, currency: d.currency, zone: d.zone, months: d.months,
    };
    setState((s) => ({ ...s, phase: 'idle', quote }));
    return quote;
  }, []);

  /** Moyens proposés dans la zone du client. Un échec n'en propose aucun :
   *  mieux vaut ne rien offrir que d'offrir ce que le serveur refusera. */
  const getMethods = useCallback(async () => {
    const res = await api.get('/api/payments/methods');
    const methods = res.ok ? ((res.data as any)?.methods as PayMethod[]) ?? [] : [];
    setState((s) => ({ ...s, methods }));
    return methods;
  }, []);

  /**
   * Déclenche l'invite sur le téléphone, puis suit le paiement jusqu'à son
   * issue. Résout à `true` seulement si l'encaissement a RÉUSSI.
   */
  const launch = useCallback(async (url: string, body: object): Promise<boolean> => {
    setState((s) => ({ ...s, phase: 'starting', error: null }));

    const res = await api.post(url, body);
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

  const pay = useCallback((
    pages: number, method: PayMethodId, phone: string | null, documentId?: string,
  ) => launch('/api/payments/collect', {
    pages, method, phone, document_id: documentId ?? null,
  }), [launch]);

  /** Achète (ou renouvelle) un abonnement ; même suivi que l'achat de pages. */
  const payPlan = useCallback((
    plan: PlanChoice, method: PayMethodId, phone: string | null,
  ) => launch('/api/payments/plan', {
    plan: plan.key, annual: plan.annual, method, phone,
  }), [launch]);

  return { ...state, getQuote, getPlanQuote, getMethods, pay, payPlan, reset };
}
