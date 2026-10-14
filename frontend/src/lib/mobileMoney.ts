/**
 * Opérateur mobile money déduit du numéro camerounais.
 *
 * INDICATIF seulement : l'opérateur réel est celui que le prestataire de
 * paiement constate. Une table de préfixes se périme (portabilité, nouvelles
 * tranches) ; un numéro inconnu ne doit donc jamais BLOQUER le paiement, on
 * se contente de ne rien afficher.
 */

export type Operator = 'mtn' | 'orange';

// Plan de numérotation camerounais, 9 chiffres commençant par 6.
//   MTN    : 650-654, 67x, 680-684
//   Orange : 655-659, 69x, 685-689
const RULES: Array<[RegExp, Operator]> = [
  [/^6(5[0-4]|7\d|8[0-4])/, 'mtn'],
  [/^6(5[5-9]|9\d|8[5-9])/, 'orange'],
];

/** Les 9 chiffres nationaux, indicatif 237 retiré s'il est saisi. */
export function nationalDigits(raw: string): string {
  const d = raw.replace(/\D/g, '');
  return d.length === 12 && d.startsWith('237') ? d.slice(3) : d;
}

export function detectOperator(raw: string): Operator | null {
  const d = nationalDigits(raw);
  if (d.length < 3) return null;
  for (const [re, op] of RULES) if (re.test(d)) return op;
  return null;
}

export const OPERATOR_LABEL: Record<Operator, string> = {
  mtn: 'MTN Mobile Money',
  orange: 'Orange Money',
};

// Couleurs de marque des opérateurs : repère visuel immédiat, c'est ce que
// l'utilisateur reconnaît sur son téléphone.
export const OPERATOR_COLOR: Record<Operator, string> = {
  mtn: '#ffcb05',
  orange: '#ff7900',
};
