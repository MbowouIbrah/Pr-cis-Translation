import { useState, useEffect } from 'react';

/**
 * Grille tarifaire — lue au serveur, JAMAIS écrite ici.
 *
 * Les prix vivaient en dur dans `PricingCards.tsx` pendant que les quotas
 * vivaient dans `models.py`. Deux sources pour un même contrat commercial, donc
 * deux vérités dès la première modification. Le frontend AFFICHE des prix ; il
 * n'en décide aucun.
 */

export interface PricingPlan {
  key: string;
  label: string;
  /** Montant en unités MINEURES (centimes, ou francs pour le XAF). `null` = sur devis. */
  monthly: number | null;
  annual: number | null;
  monthly_pages: number | null;   // null = illimité
  storage: number;                // octets
  /** Niveau de VITESSE vendu : 0 = file standard, 1 = prioritaire, 2 = max,
   *  3 = dédié. L'application réelle attend le chantier « file de priorité ». */
  priority: number;
  /** Rang dans la grille d'achat (null = hors grille : entreprise). */
  rank: number | null;
  /** S'achète en ligne (Starter, Pro). */
  for_sale: boolean;
}

export interface Pricing {
  zone: string;
  currency: string;
  decimals: number;
  page_price: number;             // unités mineures
  plans: PricingPlan[];
}

const API_BASE = import.meta.env.VITE_API_BASE || '';

/**
 * Formate un montant en unités mineures selon sa devise.
 *
 * `decimals` vient du serveur et n'est pas décoratif : le franc CFA n'a pas de
 * subdivision. Diviser un montant XAF par 100 afficherait « 25 FCFA » là où il
 * faut lire « 2 500 FCFA » — un facteur cent sur un prix de vente.
 */
export function formatMoney(
  minor: number, currency: string, decimals: number, locale: string,
): string {
  const value = decimals === 0 ? minor : minor / 10 ** decimals;
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency', currency,
      minimumFractionDigits: decimals, maximumFractionDigits: decimals,
    }).format(value);
  } catch {
    // Devise inconnue du navigateur : mieux vaut un montant juste sans symbole
    // qu'une exception qui vide la section tarifs.
    return `${value.toFixed(decimals)} ${currency}`;
  }
}

export function usePricing(): { pricing: Pricing | null; loading: boolean } {
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/pricing`);
        if (!res.ok) throw new Error(String(res.status));
        const data = (await res.json()) as Pricing;
        if (active) setPricing(data);
      } catch {
        // Pas de repli sur des prix codés en dur : afficher un tarif inventé
        // parce que le serveur n'a pas répondu, c'est afficher un prix faux.
        if (active) setPricing(null);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, []);

  return { pricing, loading };
}
