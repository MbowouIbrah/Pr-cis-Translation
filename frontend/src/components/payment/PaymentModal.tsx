import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useTranslation } from 'react-i18next';
import { X, Smartphone, Loader2, CheckCircle2, AlertCircle, CreditCard } from 'lucide-react';
import { usePayment, type PayMethodId, type PlanChoice } from '../../hooks/usePayment';
import { usePricing, formatMoney } from '../../hooks/usePricing';
import { detectOperator, OPERATOR_LABEL, type Operator } from '../../lib/mobileMoney';
import { MtnLogo, OrangeLogo, VisaLogo, MastercardLogo } from './PayLogos';

interface PaymentModalProps {
  open: boolean;
  onClose: () => void;
  /** Nombre de pages à régler. */
  pages: number;
  /** Débloquer un document déjà traduit (sinon : achat de pages d'avance). */
  documentId?: string;
  /** Ce que l'utilisateur paie, en une ligne. */
  label: string;
  /** Achat d'un abonnement au lieu de pages (`pages` est alors ignoré). */
  plan?: PlanChoice;
  onPaid: () => void;
}

// Opérateur reconnu au numéro → moyen correspondant.
const METHOD_OF: Record<Operator, PayMethodId> = { mtn: 'mtn_momo', orange: 'orange_money' };
const OPERATOR_LOGO: Record<Operator, typeof MtnLogo> = { mtn: MtnLogo, orange: OrangeLogo };
// Deux choix pour le client : le mobile money (MTN et Orange réunis, l'opérateur
// se lit sur le numéro) et la carte.
type Kind = 'mobile' | 'card';

const input: React.CSSProperties = {
  width: '100%', padding: '11px 12px 11px 38px', borderRadius: '10px',
  fontSize: '15px', outline: 'none', border: '1.5px solid var(--gray-200)',
  background: 'var(--gray-50)', fontFamily: 'inherit', boxSizing: 'border-box',
};

export default function PaymentModal({
  open, onClose, pages, documentId, label, plan, onPaid,
}: PaymentModalProps) {
  const { t } = useTranslation();
  const { pricing } = usePricing();
  const {
    phase, quote, methods, ussdCode, operator, error,
    getQuote, getPlanQuote, getMethods, pay, payPlan, reset,
  } = usePayment();
  const [phone, setPhone] = useState('');
  const [kind, setKind] = useState<Kind | null>(null);
  const isOn = (id: PayMethodId) => !!methods?.some((m) => m.id === id && m.available);
  const mobileOn = isOn('mtn_momo') || isOn('orange_money');
  const cardOn = isOn('card');
  const noMethod = methods !== null && !mobileOn && !cardOn;
  // Ce que le serveur facture réellement (montant minimum de l'opérateur).
  const billed = quote?.billed_pages ?? pages;
  // Taille retenue par le SERVEUR : pour un document, elle peut différer de
  // la prop (taille jamais consignée côté bibliothèque, recomptée au devis).
  const dues = quote?.pages ?? pages;
  const op = detectOperator(phone);
  // Le numéro décide de l'opérateur (le serveur fait de même). Préfixe inconnu :
  // on ne bloque pas, le premier moyen ouvert part et le serveur tranche.
  const opMethod = op ? METHOD_OF[op] : null;
  const opClosed = kind === 'mobile' && opMethod !== null && !isOn(opMethod);
  const chosen: PayMethodId | null =
    kind === 'card' ? (cardOn ? 'card' : null)
    : kind === 'mobile'
      ? (opMethod ? (isOn(opMethod) ? opMethod : null)
         : isOn('mtn_momo') ? 'mtn_momo' : isOn('orange_money') ? 'orange_money' : null)
      : null;

  useEffect(() => {
    if (!open) return;
    reset();
    if (plan) getPlanQuote(plan); else getQuote(pages, documentId);
    getMethods();
    // `plan` est un objet recréé à chaque rendu du parent : on suit ses champs.
  }, [open, pages, documentId, plan?.key, plan?.annual, reset, getQuote, getPlanQuote, getMethods]);

  // Premier choix ouvert présélectionné : un clic de moins dans le cas
  // courant, et jamais un choix grisé.
  useEffect(() => {
    if (!methods) return;
    if (kind === 'mobile' && mobileOn) return;
    if (kind === 'card' && cardOn) return;
    setKind(mobileOn ? 'mobile' : cardOn ? 'card' : null);
  }, [methods, kind, mobileOn, cardOn]);

  // Fermer pendant l'attente laisserait l'utilisateur sans retour sur un
  // paiement peut-être abouti. On ne verrouille rien, mais on prévient.
  const busy = phase === 'starting' || phase === 'waiting';

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    // Campay attend un numéro international sans « + ». On retire tout ce qui
    // n'est pas un chiffre : personne ne tape son numéro de la même façon.
    if (!chosen) return;
    const digits = kind === 'mobile' ? phone.replace(/\D/g, '') : null;
    const ok = plan
      ? await payPlan(plan, chosen, digits)
      : await pay(pages, chosen, digits, documentId);
    if (ok) {
      onPaid();
      // On laisse la confirmation à l'écran une seconde : une fenêtre qui
      // disparaît à l'instant du succès ne dit pas qu'il y a eu succès.
      setTimeout(onClose, 1400);
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={busy ? undefined : onClose}
            style={{ position: 'fixed', inset: 0, background: 'rgba(13,27,62,0.45)',
                     zIndex: 1200, backdropFilter: 'blur(3px)' }}
          />
          {/* Centrage par flex : `motion` réécrit `transform` pour animer
              y/scale, un translate(-50%,-50%) y serait perdu. */}
          <div style={{ position: 'fixed', inset: 0, zIndex: 1201, display: 'flex',
                        alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}>
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            style={{
              position: 'relative', pointerEvents: 'auto',
              width: 'min(400px, 92vw)', maxHeight: '92vh', overflowY: 'auto',
              background: 'white', borderRadius: '18px',
              boxShadow: '0 24px 70px rgba(13,27,62,0.28)', padding: '26px 24px',
            }}
          >
            {!busy && (
              <button onClick={onClose} aria-label="Fermer"
                style={{ position: 'absolute', top: 14, right: 14, background: 'none',
                         border: 'none', cursor: 'pointer', color: 'var(--gray-400)' }}>
                <X size={18} />
              </button>
            )}

            {/* ── Succès ─────────────────────────────────────────────────── */}
            {phase === 'done' ? (
              <div style={{ textAlign: 'center', padding: '18px 0' }}>
                <CheckCircle2 size={44} color="#16a34a" strokeWidth={2} />
                <h2 style={{ fontSize: '18px', fontWeight: 700, margin: '14px 0 6px' }}>
                  Paiement confirmé
                </h2>
                <p style={{ fontSize: '13.5px', color: 'var(--gray-500)', margin: 0 }}>
                  {plan ? t('payment.plan_done', { plan: plan.label })
                    : 'Votre document est débloqué.'}
                </p>
              </div>
            ) : phase === 'waiting' ? (
              /* ── Attente de la validation sur le téléphone ─────────────── */
              <div style={{ textAlign: 'center', padding: '10px 0' }}>
                <motion.span
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                  style={{ display: 'inline-flex', color: 'var(--blue)' }}
                >
                  <Loader2 size={38} strokeWidth={2.2} />
                </motion.span>
                <h2 style={{ fontSize: '17px', fontWeight: 700, margin: '14px 0 6px' }}>
                  Validez sur votre téléphone
                </h2>
                <p style={{ fontSize: '13.5px', color: 'var(--gray-500)', lineHeight: 1.55, margin: 0 }}>
                  {operator ? `Une demande ${operator} ` : 'Une demande de paiement '}
                  vient d’être envoyée au <strong>{phone}</strong>. Saisissez votre code
                  secret pour confirmer.
                </p>
                {/* Sur certains téléphones l'invite n'arrive jamais. Sans ce
                    code, l'utilisateur n'a aucun recours et le paiement meurt. */}
                {ussdCode && (
                  <div style={{ marginTop: 16, padding: '11px', borderRadius: '10px',
                                background: 'var(--gray-50)', border: '1px dashed var(--gray-300)' }}>
                    <p style={{ fontSize: '12px', color: 'var(--gray-500)', margin: '0 0 4px' }}>
                      Rien reçu ? Composez :
                    </p>
                    <code style={{ fontSize: '16px', fontWeight: 700, letterSpacing: '.5px' }}>
                      {ussdCode}
                    </code>
                  </div>
                )}
              </div>
            ) : (
              /* ── Saisie ─────────────────────────────────────────────────── */
              <>
                <h2 style={{ fontSize: '18px', fontWeight: 700, margin: '0 0 4px' }}>
                  {t('payment.title')}
                </h2>
                <p style={{ fontSize: '13.5px', color: 'var(--gray-500)', margin: '0 0 18px' }}>
                  {label}
                </p>

                {/* Le montant est annoncé AVANT de payer, et c'est exactement
                    celui qui sera débité — aucun frais ne s'ajoute ensuite. */}
                <div style={{ padding: '14px 16px', borderRadius: '12px',
                              background: 'var(--gray-50)', marginBottom: 18 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13.5px',
                                color: 'var(--gray-600)', marginBottom: 6 }}>
                    <span>
                      {plan ? t('payment.plan_months', { count: quote?.months ?? (plan.annual ? 12 : 1) })
                        : `${billed} page${billed > 1 ? 's' : ''}`}
                    </span>
                    {quote && pricing && (
                      <span>
                        {formatMoney(quote.unit_price, quote.currency, pricing.decimals, 'fr')}
                        {plan ? t('pricing.per_month') : ' / page'}
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between',
                                alignItems: 'baseline', fontWeight: 700, fontSize: '20px' }}>
                    <span style={{ fontSize: '14px', fontWeight: 600 }}>Total</span>
                    <span>
                      {quote && pricing
                        ? formatMoney(quote.amount, quote.currency, pricing.decimals, 'fr')
                        : '…'}
                    </span>
                  </div>
                  {/* Montant minimum de l'opérateur : on le dit avant l'invite,
                      et on dit où vont les pages payées en plus. */}
                  {!plan && quote && pricing && billed > dues && (
                    <p style={{ fontSize: '12px', color: 'var(--gray-500)', margin: '10px 0 0', lineHeight: 1.45 }}>
                      Montant minimum de {formatMoney(quote.min_amount, quote.currency, pricing.decimals, 'fr')} :{' '}
                      {billed - dues} page{billed - dues > 1 ? 's' : ''} en plus, créditée{billed - dues > 1 ? 's' : ''}{' '}
                      sur votre compte pour une prochaine traduction.
                    </p>
                  )}
                </div>

                {error && (
                  <div style={{ display: 'flex', gap: 8, padding: '10px 12px', borderRadius: '10px',
                                background: '#fef2f2', color: '#991b1b', border: '1px solid #fecaca',
                                fontSize: '13px', marginBottom: 14 }}>
                    <AlertCircle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
                    <span>{error}</span>
                  </div>
                )}

                <form onSubmit={submit}>
                  <fieldset style={{ border: 'none', margin: '0 0 16px', padding: 0 }}>
                    <legend style={{ fontSize: '13px', fontWeight: 500, color: 'var(--gray-700)',
                                     marginBottom: 6, padding: 0 }}>
                      {t('payment.method_title')}
                    </legend>
                    <div role="radiogroup" style={{ display: 'grid', gap: 8,
                                                    gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
                      {methods && ([
                        { k: 'mobile' as const, avail: mobileOn, label: t('payment.method_mobile'),
                          logos: (['mtn', 'orange'] as Operator[]).map((o) => {
                            const Logo = OPERATOR_LOGO[o];
                            // Le numéro reconnu : son opérateur ressort, l'autre s'efface.
                            const dim = kind === 'mobile' && op !== null && op !== o;
                            return <span key={o} style={{ display: 'flex', opacity: dim ? 0.25 : 1,
                                                          transition: 'opacity .2s' }}><Logo /></span>;
                          }) },
                        { k: 'card' as const, avail: cardOn, label: t('payment.method_card'),
                          logos: [<VisaLogo key="v" />, <MastercardLogo key="m" />] },
                      ]).map(({ k, avail, label, logos }) => {
                        const on = kind === k;
                        return (
                          <button key={k} type="button" role="radio" aria-checked={on}
                            disabled={!avail || phase === 'starting'}
                            onClick={() => setKind(k)}
                            style={{
                              display: 'flex', flexDirection: 'column', alignItems: 'center',
                              justifyContent: 'center', gap: 7,
                              padding: '12px 8px', borderRadius: '10px', fontFamily: 'inherit',
                              border: `1.5px solid ${on ? 'var(--blue)' : 'var(--gray-200)'}`,
                              background: on ? 'var(--gray-50)' : 'white',
                              cursor: avail ? 'pointer' : 'not-allowed',
                              opacity: avail ? 1 : 0.55, minHeight: 84,
                            }}>
                            <span aria-hidden style={{ display: 'flex', alignItems: 'center', gap: 6,
                                                       height: 22 }}>
                              {logos}
                            </span>
                            <span style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--gray-800)',
                                           lineHeight: 1.2, textAlign: 'center' }}>
                              {label}
                            </span>
                            {!avail && (
                              <span style={{ fontSize: '10.5px', color: 'var(--gray-500)', lineHeight: 1.2,
                                             textAlign: 'center' }}>
                                {t('payment.method_soon')}
                              </span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                    {noMethod && (
                      <p style={{ fontSize: '12.5px', color: 'var(--gray-500)', margin: '8px 0 0' }}>
                        {t('payment.no_method')}
                      </p>
                    )}
                  </fieldset>

                  {kind === 'mobile' && (<>
                  <label htmlFor="pay-phone" style={{ display: 'block', fontSize: '13px', fontWeight: 500,
                                  color: 'var(--gray-700)', marginBottom: 5 }}>
                    {t('payment.phone_label')}
                  </label>
                  <div style={{ position: 'relative' }}>
                    <Smartphone size={15} style={{ position: 'absolute', left: 12, top: '50%',
                                                   transform: 'translateY(-50%)', color: 'var(--gray-400)' }} />
                    <input
                      id="pay-phone" type="tel" required inputMode="tel" autoFocus value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      placeholder="6XX XX XX XX" style={input}
                      aria-describedby="pay-phone-hint"
                    />
                  </div>
                  {/* Opérateur reconnu au préfixe : l'utilisateur sait sur quel
                      compte l'invite arrivera. Indicatif seulement, jamais bloquant. */}
                  <p id="pay-phone-hint" aria-live="polite"
                     style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '11.5px',
                              color: 'var(--gray-500)', margin: '6px 0 16px', minHeight: 16 }}>
                    {op ? (() => {
                      const Logo = OPERATOR_LOGO[op];
                      return (
                        <>
                          <Logo height={14} />
                          {OPERATOR_LABEL[op]}
                          {opClosed && <span style={{ color: 'var(--red, #c0392b)' }}>
                            — {t('payment.method_soon').toLowerCase()}</span>}
                        </>
                      );
                    })() : t('payment.phone_hint')}
                  </p>
                  </>)}

                  <button type="submit" disabled={phase === 'starting' || !quote || !chosen}
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                             gap: 8, padding: '12px', borderRadius: '10px', border: 'none',
                             background: 'var(--blue)', color: 'white', fontWeight: 600,
                             fontSize: '14.5px', cursor: 'pointer', fontFamily: 'inherit',
                             opacity: phase === 'starting' || !quote || !chosen ? 0.6 : 1 }}>
                    {phase === 'starting'
                      ? <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
                      : chosen === 'card' ? <CreditCard size={16} /> : <Smartphone size={16} />}
                    {t('payment.pay')} {quote && pricing
                      ? formatMoney(quote.amount, quote.currency, pricing.decimals, 'fr')
                      : ''}
                  </button>
                </form>
              </>
            )}
          </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>
  );
}
