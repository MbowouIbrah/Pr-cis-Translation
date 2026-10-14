/**
 * Barre latérale des documents : le compte, la liste, les actions groupées.
 *
 * CE QUI A CHANGÉ, ET CE QUI N'A PAS CHANGÉ
 * -----------------------------------------
 * Le comportement est le MÊME : mêmes requêtes, mêmes droits, même modale de
 * paiement. Ce qui a bougé, c'est la répartition — ce fichier faisait 444
 * lignes et portait à la fois le panneau, le profil, chaque carte de document,
 * deux formateurs d'octets divergents et une cinquantaine de styles en ligne.
 *
 * Il ne garde que son rôle : ouvrir, lister, orchestrer.
 *   • le compte    -> `AccountPanel`
 *   • un document  -> `DocumentCard`
 *   • le tiroir    -> `ui/Drawer` (Échap, focus, défilement gelé)
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { FolderOpen, LifeBuoy, LogOut, X } from 'lucide-react';

import type { DocMeta } from '../../hooks/useDocumentLibrary';
import { useAuth } from '../../contexts/AuthContext';
import { isTrialFor } from '../../lib/plans';
import Button from '../ui/Button';
import Drawer from '../ui/Drawer';
import EmptyState from '../ui/EmptyState';
import IconButton from '../ui/IconButton';
import PaymentModal from '../payment/PaymentModal';
import { openSupport } from '../support/supportBus';
import AccountPanel from './AccountPanel';
import DocumentCard from './DocumentCard';

interface DocumentLibraryProps {
  isOpen: boolean;
  onClose: () => void;
  documents: DocMeta[];
  /** L'aperçu s'ouvre AVANT que les fichiers soient là : on remonte des
   *  promesses, pas des blobs. Attendre le rendu avant d'ouvrir, c'était offrir
   *  une fenêtre figée pendant plusieurs secondes.
   *
   *  La TRADUCTION n'est pas remontée ici : elle se demande page par page, et
   *  `Home` en est le seul propriétaire. Deux endroits qui la chargent, c'est
   *  deux requêtes pour la même page et une course pour savoir laquelle gagne. */
  onPreview: (req: {
    docId: string;
    filename: string;
    ext: string;
    source: Promise<Blob | undefined>;
  }) => void;
  onDelete: (id: string) => void;
  onClearAll: () => void;
  getBlob: (id: string) => Promise<Blob | undefined>;
  getOriginalBlob: (id: string, asPdf?: boolean) => Promise<Blob | undefined>;
  /** Un paiement vient d'aboutir : la liste doit être relue (le `paid` du
   *  document a changé côté serveur). */
  onPaid?: () => void;
  /** Relance la traduction d'un document en erreur. */
  onRetry?: (doc: DocMeta) => void;
}

export default function DocumentLibrary({
  isOpen, onClose, documents, onPreview, onDelete, onClearAll,
  getBlob, getOriginalBlob, onPaid, onRetry,
}: DocumentLibraryProps) {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  // Le serveur refuse le téléchargement d'une traduction à un plan d'essai
  // (402). On lit le MÊME plan côté client pour ne pas promettre un bouton qui
  // ne peut pas tenir.
  const trial = isTrialFor(user);

  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  // Document dont on est en train de régler les pages (null = pas de paiement).
  const [payFor, setPayFor] = useState<DocMeta | null>(null);

  // On n'ATTEND rien ici. L'aperçu rastérisé se calcule côté serveur en
  // quelques secondes ; le retenir jusqu'au bout laissait l'utilisateur devant
  // une interface morte, sans même un indicateur. On lance la requête et on
  // passe la main tout de suite.
  //
  // `as=pdf` : le viewer affiche tout en PDF et le serveur détient déjà cette
  // conversion. Lui renvoyer le natif pour qu'il la refasse était un
  // aller-retour de plusieurs mégaoctets par ouverture.
  const handlePreview = useCallback((doc: DocMeta) => {
    onPreview({
      docId: doc.id,
      filename: doc.filename,
      ext: doc.ext,
      source: getOriginalBlob(doc.id, true),
    });
  }, [getOriginalBlob, onPreview]);

  const handleDownload = useCallback(async (doc: DocMeta) => {
    // Un compte gratuit dont le document est payé télécharge ; sinon on lui
    // propose de le régler — sur place, pour ce document précis. L'ancienne
    // version le renvoyait vers la grille de tarifs : la seule issue offerte
    // était de s'abonner, alors qu'il ne voulait qu'un fichier.
    if (trial && !doc.paid) {
      setPayFor(doc);
      return;
    }
    setLoadingId(doc.id);
    try {
      const blob = await getBlob(doc.id);
      if (blob) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = doc.filename;
        document.body.appendChild(a);
        a.click();
        URL.revokeObjectURL(url);
        document.body.removeChild(a);
      }
    } finally {
      setLoadingId(null);
    }
  }, [getBlob, trial]);

  return (
    <>
      <Drawer open={isOpen} onClose={onClose} label={t('library.title')}>
        {user && <AccountPanel user={user}
          refreshKey={documents.map(d => `${d.id}:${d.status}`).join('|')} />}

        <header className="biblio__tete">
          <div>
            <h2 className="biblio__titre">{t('library.title')}</h2>
            <p className="biblio__compte">
              {documents.length} {t('library.count_suffix')}
            </p>
          </div>

          <div className="biblio__outils">
            {documents.length > 0 && (
              confirmClear ? (
                <>
                  <Button variant="danger" size="sm"
                    onClick={() => { onClearAll(); setConfirmClear(false); }}>
                    {t('library.confirm_clear')}
                  </Button>
                  <Button variant="ghost" size="sm"
                    onClick={() => setConfirmClear(false)}>
                    {t('library.cancel')}
                  </Button>
                </>
              ) : (
                <Button variant="ghost" size="sm"
                  onClick={() => setConfirmClear(true)}>
                  {t('library.clear_all')}
                </Button>
              )
            )}
            <IconButton label={t('library.cancel')} onClick={onClose}>
              <X size={16} strokeWidth={2.2} />
            </IconButton>
          </div>
        </header>

        <div className="biblio__liste">
          {documents.length === 0 ? (
            <EmptyState
              icon={<FolderOpen size={44} strokeWidth={1.4} />}
              message={user ? t('library.empty') : t('library.empty_guest')}
              action={!user && (
                <Button variant="primary" size="md"
                  onClick={() => { onClose(); navigate('/login'); }}>
                  {t('nav.signIn')}
                </Button>
              )}
            />
          ) : (
            documents.map((doc) => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                trial={trial}
                busy={loadingId === doc.id}
                onPreview={handlePreview}
                onDownload={handleDownload}
                onDelete={onDelete}
                onRetry={onRetry}
              />
            ))
          )}
        </div>

        {/* Pied de panneau — la déconnexion.
            Elle était au bas du bloc de compte, en gris clair sur blanc : on ne
            savait pas qu'elle était là. Ici, elle ne défile pas avec la liste
            et reste visible quel que soit le nombre de documents. Le ton
            « danger » lui donne sa présence sans en faire l'action principale :
            c'est une sortie, pas une suppression. */}
        {user && (
          <footer className="biblio__pied" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {/* Aide au-DESSUS de la déconnexion : c'est une action qu'on cherche
                quand ça coince, elle doit être à portée sans quitter le compte. */}
            <Button variant="ghost" size="md" block
              icon={<LifeBuoy size={15} strokeWidth={2.2} />}
              onClick={() => { onClose(); openSupport(); }}>
              {t('support.open', 'Aide & support')}
            </Button>
            <Button variant="danger" size="md" block
              icon={<LogOut size={15} strokeWidth={2.2} />}
              onClick={async () => { onClose(); await logout(); }}>
              {t('account.logout')}
            </Button>
          </footer>
        )}
      </Drawer>

      <PaymentModal
        open={!!payFor}
        onClose={() => setPayFor(null)}
        pages={payFor?.pageCount ?? 1}
        documentId={payFor?.id}
        label={t('library.unlock_label', { name: payFor?.originalName ?? '' })}
        onPaid={() => { onPaid?.(); setPayFor(null); }}
      />
    </>
  );
}
