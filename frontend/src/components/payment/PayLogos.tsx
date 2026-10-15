import { useId } from 'react';

/**
 * Marques des moyens de paiement, aux couleurs officielles : c'est ce que
 * l'utilisateur reconnaît sur son téléphone ou sa carte. Visa et l'emblème
 * Orange reprennent les tracés de simple-icons (CC0) ; MTN et Mastercard sont
 * reconstruits à partir de leurs formes de marque (ovale, deux cercles).
 */

interface Props { height?: number; title?: string }

export function MtnLogo({ height = 22, title = 'MTN Mobile Money' }: Props) {
  return (
    <svg role="img" aria-label={title} height={height} viewBox="0 0 44 28">
      <rect width="44" height="28" rx="5" fill="#FFCB05" />
      <ellipse cx="22" cy="14" rx="18" ry="10" fill="none" stroke="#000" strokeWidth="2" />
      <text x="22" y="18.2" textAnchor="middle" fontFamily="Arial Black, Arial, sans-serif"
            fontWeight={900} fontSize="11.5" fill="#000">MTN</text>
    </svg>
  );
}

export function OrangeLogo({ height = 22, title = 'Orange Money' }: Props) {
  return (
    <svg role="img" aria-label={title} height={height} viewBox="0 0 24 24">
      <path fill="#FF7900" d="M0 0h24v24H0V0Z" />
      <path fill="#FFF" d="M3.43 20.572h17.143v-3.429H3.43v3.429Z" />
    </svg>
  );
}

export function VisaLogo({ height = 16, title = 'Visa' }: Props) {
  return (
    <svg role="img" aria-label={title} height={height} viewBox="0 7.9 24 8.2">
      <path fill="#1434CB" d="M9.112 8.262L5.97 15.758H3.92L2.374 9.775c-.094-.368-.175-.503-.461-.658C1.447 8.864.677 8.627 0 8.479l.046-.217h3.3a.904.904 0 01.894.764l.817 4.338 2.018-5.102zm8.033 5.049c.008-1.979-2.736-2.088-2.717-2.972.006-.269.262-.555.822-.628a3.66 3.66 0 011.913.336l.34-1.59a5.207 5.207 0 00-1.814-.333c-1.917 0-3.266 1.02-3.278 2.479-.012 1.079.963 1.68 1.698 2.04.756.367 1.01.603 1.006.931-.005.504-.602.725-1.16.734-.975.015-1.54-.263-1.992-.473l-.351 1.642c.453.208 1.289.39 2.156.398 2.037 0 3.37-1.006 3.377-2.564m5.061 2.447H24l-1.565-7.496h-1.656a.883.883 0 00-.826.55l-2.909 6.946h2.036l.405-1.12h2.488zm-2.163-2.656l1.02-2.815.588 2.815zm-8.16-4.84l-1.603 7.496H8.34l1.605-7.496z" />
    </svg>
  );
}

export function MastercardLogo({ height = 22, title = 'Mastercard' }: Props) {
  const clip = useId();
  return (
    <svg role="img" aria-label={title} height={height} viewBox="0 0 38 24">
      <defs>
        <clipPath id={clip}><circle cx="12" cy="12" r="11" /></clipPath>
      </defs>
      <circle cx="12" cy="12" r="11" fill="#EB001B" />
      <circle cx="26" cy="12" r="11" fill="#F79E1B" />
      <circle cx="26" cy="12" r="11" fill="#FF5F00" clipPath={`url(#${clip})`} />
    </svg>
  );
}
