/**
 * En-tête de compte de la barre latérale : qui je suis, ce qu'il me reste,
 * comment je sors.
 *
 * Tout y était écrit en français EN DUR — « Déconnexion », « Illimité »,
 * « Utilisateur », « Traduction seule — sans stockage ». L'interface anglaise
 * affichait donc du français à cet endroit précis, et seulement là.
 *
 * LA DÉCONNEXION N'EST PLUS ICI
 * -----------------------------
 * Elle vivait au bas de ce bloc, en gris clair sur blanc, et se perdait : on
 * ne savait pas qu'elle existait. Elle est descendue en PIED DE PANNEAU, où
 * elle reste visible quelle que soit la position de la liste — le seul endroit
 * qui ne défile jamais. Voir `DocumentLibrary`.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Crown, HardDrive, Languages } from 'lucide-react';

import { useAuth, type AuthUser } from '../../contexts/AuthContext';
import api from '../../services/api';
import { formatSize, percent } from '../../lib/format';
import Avatar from '../ui/Avatar';
import ProgressBar from '../ui/ProgressBar';

interface AccountPanelProps {
  user: AuthUser;
  /** Change quand la liste des documents change : la consommation est relue. */
  refreshKey?: string;
}

/** Réponse de `/api/auth/me/usage` — calculée par le module qui FAIT
 *  respecter les quotas : ce qui est affiché est ce que la route acceptera. */
interface Usage {
  kind: 'free' | 'plan';
  free_translations_remaining?: number;
  monthly_pages?: number | null;
  pages_used?: number;
  pages_remaining?: number | null;
  page_credits: number;
  resets_at: string;
}

export default function AccountPanel({ user, refreshKey }: AccountPanelProps) {
  const { t, i18n } = useTranslation();
  const { refreshUser } = useAuth();
  const [usage, setUsage] = useState<Usage | null>(null);

  // Relu à chaque changement de la liste : une suppression libère du stockage,
  // une traduction consomme — le profil chargé à la connexion serait périmé.
  useEffect(() => {
    let vivant = true;
    refreshUser().catch(() => { /* l'affichage précédent reste valable */ });
    api.get('/api/auth/me/usage').then(res => {
      if (vivant && res.ok) setUsage(res.data as Usage);
    });
    return () => { vivant = false; };
  }, [refreshKey, user.plan, refreshUser]);

  const renouvellement = usage
    ? new Date(usage.resets_at).toLocaleDateString(i18n.language, { day: 'numeric', month: 'long' })
    : '';

  const illimite = user.storage_limit <= 0 && user.plan === 'admin';
  const sansStockage = user.storage_limit <= 0 && user.plan !== 'admin';
  const occupation = percent(user.storage_used, user.storage_limit);
  // Au-delà de 90 %, la barre passe au rouge : l'information utile n'est pas
  // « voici votre quota », c'est « vous allez le heurter ».
  const ton = occupation >= 90 ? 'danger' : occupation >= 70 ? 'gold' : 'blue';

  return (
    <section className="compte">
      <div className="compte__identite">
        <Avatar name={user.name} email={user.email} size={46} />
        <div className="compte__noms">
          <p className="compte__nom">{user.name || t('account.default_name')}</p>
          <p className="compte__email" title={user.email}>{user.email}</p>
        </div>
        <span className={`compte__forfait compte__forfait--${user.plan === 'free' ? 'base' : 'paye'}`}>
          {t(`plans.${user.plan}`, user.plan)}
        </span>
      </div>

      {usage && (
        <div className="compte__stockage compte__conso">
          {usage.kind === 'free' ? (
            <div className="compte__quota">
              <span className="compte__ligne">
                <Languages size={13} aria-hidden />
                {t('account.translations_left')}
              </span>
              <span className="compte__chiffres">
                {t('account.free_left', { count: usage.free_translations_remaining ?? 0 })}
              </span>
            </div>
          ) : usage.monthly_pages == null ? (
            <p className="compte__ligne">
              <Crown size={13} className="compte__couronne" aria-hidden />
              {t('account.translations_left')} · {t('account.unlimited')}
            </p>
          ) : (
            <>
              <div className="compte__quota">
                <span className="compte__ligne">
                  <Languages size={13} aria-hidden />
                  {t('account.pages_left')}
                </span>
                <span className="compte__chiffres">
                  {usage.pages_remaining} / {usage.monthly_pages}
                </span>
              </div>
              <ProgressBar value={percent(usage.pages_used ?? 0, usage.monthly_pages)}
                tone={(usage.pages_remaining ?? 0) <= usage.monthly_pages * 0.1 ? 'danger' : 'blue'}
                height={5} label={t('account.pages_left')} />
            </>
          )}
          {usage.page_credits > 0 && (
            <p className="compte__note">{t('account.credits', { count: usage.page_credits })}</p>
          )}
          {usage.monthly_pages !== null && (
            <p className="compte__note">{t('account.renews', { date: renouvellement })}</p>
          )}
        </div>
      )}

      <div className="compte__stockage">
        {illimite ? (
          <p className="compte__ligne">
            <Crown size={13} className="compte__couronne" aria-hidden />
            {formatSize(user.storage_used, t)} · {t('account.unlimited')}
          </p>
        ) : sansStockage ? (
          <p className="compte__ligne compte__ligne--discrete">
            <HardDrive size={13} aria-hidden />
            {t('account.no_storage')}
          </p>
        ) : (
          <>
            <div className="compte__quota">
              <span className="compte__ligne">
                <HardDrive size={13} aria-hidden />
                {t('account.storage_left')}
              </span>
              <span className="compte__chiffres">
                {formatSize(Math.max(0, user.storage_limit - user.storage_used), t)} / {formatSize(user.storage_limit, t)}
              </span>
            </div>
            <ProgressBar value={occupation} tone={ton} height={5}
              label={t('account.storage')} />
          </>
        )}
      </div>
    </section>
  );
}
