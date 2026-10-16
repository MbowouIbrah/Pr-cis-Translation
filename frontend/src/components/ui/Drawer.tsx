/**
 * Panneau latéral coulissant (voile + tiroir), avec ce qu'un panneau modal doit
 * au clavier.
 *
 * CE QUE LA VERSION PRÉCÉDENTE NE FAISAIT PAS
 * -------------------------------------------
 * La barre latérale des documents était un `motion.aside` posé à la main. Elle
 * s'ouvrait, elle glissait, et c'est tout :
 *
 *   • `Échap` ne la fermait pas — seule la souris pouvait, sur le voile ou la
 *     croix ;
 *   • le focus restait DERRIÈRE elle : en tabulant, on parcourait la page
 *     masquée sans jamais atteindre le panneau ouvert ;
 *   • la page continuait de défiler sous le voile.
 *
 * Ces trois manques se réglaient à un seul endroit, à condition qu'il y en ait
 * un. C'est ce composant.
 */
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { motion, AnimatePresence } from 'motion/react';

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  /** Nom accessible du panneau — annoncé à l'ouverture. */
  label: string;
  side?: 'left' | 'right';
  width?: string;
  children: ReactNode;
}

export default function Drawer({
  open, onClose, label, side = 'right', width = 'min(440px, 96vw)', children,
}: DrawerProps) {
  const panneau = useRef<HTMLElement>(null);

  // Échap ferme, et le défilement de la page est gelé tant que le panneau est
  // ouvert (sans quoi la molette fait défiler l'arrière-plan sous le voile).
  useEffect(() => {
    if (!open) return;
    const surTouche = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', surTouche);
    // La barre de défilement qui disparaît est compensée : sinon toute la page
    // se ré-agence d'un coup au premier instant de l'animation.
    const avant = document.body.style.overflow;
    const margeAvant = document.body.style.paddingRight;
    const barre = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = 'hidden';
    if (barre > 0) document.body.style.paddingRight = `${barre}px`;
    return () => {
      document.removeEventListener('keydown', surTouche);
      document.body.style.overflow = avant;
      document.body.style.paddingRight = margeAvant;
    };
  }, [open, onClose]);

  // Le focus entre dans le panneau à l'ouverture : sans cela, la tabulation
  // continuait de parcourir la page masquée derrière le voile.
  useEffect(() => {
    if (!open) return;
    const premier = panneau.current?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    // preventScroll : sinon le navigateur fait défiler le panneau encore hors
    // champ pour montrer l'élément, ce qui saccade la glissade.
    premier?.focus({ preventScroll: true });
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="ui-drawer__voile"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
            aria-hidden
          />
          <motion.aside
            ref={panneau}
            className={`ui-drawer ui-drawer--${side}`}
            style={{ width }}
            role="dialog"
            aria-modal="true"
            aria-label={label}
            initial={{ x: side === 'right' ? '100%' : '-100%' }}
            animate={{ x: 0 }}
            exit={{ x: side === 'right' ? '100%' : '-100%' }}
            transition={{ duration: 0.26, ease: [0.32, 0.72, 0, 1] }}
          >
            {children}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
