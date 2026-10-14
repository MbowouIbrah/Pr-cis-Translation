import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { X, Smartphone, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { usePayment } from '../../hooks/usePayment';
import { usePricing, formatMoney } from '../../hooks/usePricing';

interface PaymentModalProps {
  open: boolean;
  onClose: () => void;
  /** Nombre de pages à régler. */
  pages: number;
  /** Débloquer un document déjà traduit (sinon : achat de pages d'avance). */
  documentId?: string;
  /** Ce que l'utilisateur paie, en une ligne. */
  label: string;
  onPaid: () => void;
}

const input: React.CSSProperties = {
  width: '100%', padding: '11px 12px 11px 38px', borderRadius: '10px',
  fontSize: '15px', outline: 'none', border: '1.5px solid var(--gray-200)',
  background: 'var(--gray-50)', fontFamily: 'inherit', boxSizing: 'border-box',
};

export default function PaymentModal({
  open, onClose, pages, documentId, label, onPaid,
}: PaymentModalProps) {
  const { pricing } = usePricing();
  const { phase, quote, ussdCode, operator, error, getQuote, pay, reset } = usePayment();
  const [phone, setPhone] = useState('');
  // Ce que le serveur facture réellement (montant minimum de l'opérateur).
  const billed = quote?.billed_pages ?? pages;

  useEffect(() => {
    if (open) { reset(); getQuote(pages); }
  }, [open, pages, reset, getQuote]);

  // Fermer pendant l'attente laisserait l'utilisateur sans retour sur un
  // paiement peut-être abouti. On ne verrouille rien, mais on prévient.
  const busy = phase === 'starting' || phase === 'waiting';

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    // Campay attend un numéro international sans « + ». On retire tout ce qui
    // n'est pas un chiffre : personne ne tape son numéro de la même façon.
    const digits = phone.replace(/\D/g, '');
    const ok = await pay(pages, digits, documentId);
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
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            style={{
              position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
              width: 'min(400px, 92vw)', background: 'white', borderRadius: '18px',
              boxShadow: '0 24px 70px rgba(13,27,62,0.28)', zIndex: 1201, padding: '26px 24px',
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
                  Votre document est débloqué.
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
                  Paiement mobile money
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
                    <span>{billed} page{billed > 1 ? 's' : ''}</span>
                    {quote && pricing && (
                      <span>{formatMoney(quote.unit_price, quote.currency, pricing.decimals, 'fr')} / page</span>
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
                  {quote && pricing && billed > pages && (
                    <p style={{ fontSize: '12px', color: 'var(--gray-500)', margin: '10px 0 0', lineHeight: 1.45 }}>
                      Montant minimum de {formatMoney(quote.min_amount, quote.currency, pricing.decimals, 'fr')} :{' '}
                      {billed - pages} page{billed - pages > 1 ? 's' : ''} en plus, créditée{billed - pages > 1 ? 's' : ''}{' '}
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
                  <label style={{ display: 'block', fontSize: '13px', fontWeight: 500,
                                  color: 'var(--gray-700)', marginBottom: 5 }}>
                    Numéro MTN MoMo ou Orange Money
                  </label>
                  <div style={{ position: 'relative' }}>
                    <Smartphone size={15} style={{ position: 'absolute', left: 12, top: '50%',
                                                   transform: 'translateY(-50%)', color: 'var(--gray-400)' }} />
                    <input
                      type="tel" required inputMode="tel" autoFocus value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      placeholder="237 6XX XX XX XX" style={input}
                    />
                  </div>
                  <p style={{ fontSize: '11.5px', color: 'var(--gray-400)', margin: '6px 0 16px' }}>
                    Avec l’indicatif pays, sans le « + ».
                  </p>

                  <button type="submit" disabled={phase === 'starting' || !quote}
                    style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                             gap: 8, padding: '12px', borderRadius: '10px', border: 'none',
                             background: 'var(--blue)', color: 'white', fontWeight: 600,
                             fontSize: '14.5px', cursor: 'pointer', fontFamily: 'inherit',
                             opacity: phase === 'starting' || !quote ? 0.6 : 1 }}>
                    {phase === 'starting'
                      ? <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
                      : <Smartphone size={16} />}
                    Payer {quote && pricing
                      ? formatMoney(quote.amount, quote.currency, pricing.decimals, 'fr')
                      : ''}
                  </button>
                </form>
              </>
            )}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
