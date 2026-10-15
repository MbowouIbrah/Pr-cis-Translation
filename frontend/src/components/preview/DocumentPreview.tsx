import { useTranslation } from 'react-i18next';
import { motion } from 'motion/react';
import { Loader2, AlertTriangle } from 'lucide-react';
import PdfViewer from './PdfViewer';
import { usePdfPreview } from '../../hooks/usePdfPreview';

interface DocumentPreviewProps {
  sourceFile?: File | null;
  translatedBlob?: Blob | null;
  /** Format du document (pdf, docx, pptx, txt). */
  ext: string;
  /** Extension du blob traduit. Si absente, utilise `ext`.
   *   Utile pour le streaming PPTX : le partial est déjà un PDF. */
  translatedExt?: string;
  currentPage: number;
  zoom: number;
  isTrialMode: boolean;
  onPagesLoaded?: (numPages: number) => void;
  /** La page courante est-elle déjà traduite (streaming page par page) ? */
  translatedPageReady?: boolean;
  /** Statut de la page courante côté traduction (pour le placeholder). */
  translatedPageStatus?: string;
  sourceLabel?: string;
  targetLabel?: string;
  /** true = chargement en cours (ne PAS afficher la démo). */
  previewLoading?: boolean;
  /** Manque de crédits (402) : pages à régler pour ce document. Affiché dans le
   *  panneau traduit, à la place du message de progression. */
  creditsNeeded?: number | null;
  /** Ouvre le paiement à la page pour `n` pages. */
  onBuyPages?: (pages: number) => void;
  /** Essai : quitter l'aperçu pour les tarifs. */
  onUnlock?: () => void;
}

/**
 * Aperçu unifié pour tous les formats. Les formats non-PDF (DOCX, PPTX, TXT)
 * sont convertis en PDF côté serveur (LibreOffice) pour un rendu EXACT, puis
 * affichés avec le même viewer pdf.js que les PDF natifs.
 */
export default function DocumentPreview({
  sourceFile,
  translatedBlob,
  ext,
  translatedExt,
  currentPage,
  zoom,
  isTrialMode,
  onPagesLoaded,
  translatedPageReady = true,
  translatedPageStatus,
  sourceLabel,
  targetLabel,
  previewLoading,
  creditsNeeded,
  onBuyPages,
  onUnlock,
}: DocumentPreviewProps) {
  const { t } = useTranslation();
  const { sourcePdf, translatedPdf, loading, error } = usePdfPreview(sourceFile, translatedBlob, ext, translatedExt);

  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '14px',
          minHeight: '300px',
          width: '100%',
          color: 'var(--color-text-secondary)',
        }}
      >
        <motion.span
          animate={{ rotate: 360 }}
          transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
          style={{ display: 'inline-flex' }}
        >
          <Loader2 size={28} strokeWidth={2.2} />
        </motion.span>
        <span style={{ fontSize: '13px' }}>{t('preview.loading')}</span>
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '8px',
          minHeight: '300px',
          width: '100%',
          padding: '24px',
          textAlign: 'center',
          color: '#dc2626',
        }}
      >
        <AlertTriangle size={26} strokeWidth={2} />
        <span style={{ fontSize: '13px' }}>{t('preview.error', { error })}</span>
        <span style={{ fontSize: '12px', color: 'var(--color-text-secondary)' }}>
          {t('preview.error_hint')}
        </span>
      </div>
    );
  }

  return (
    <PdfViewer
      sourceFile={sourcePdf}
      translatedBlob={translatedPdf}
      currentPage={currentPage}
      zoom={zoom}
      isTrialMode={isTrialMode}
      onPagesLoaded={onPagesLoaded}
      translatedPageReady={translatedPageReady}
      translatedPageStatus={translatedPageStatus}
      sourceLabel={sourceLabel}
      previewLoading={previewLoading}
      targetLabel={targetLabel}
      creditsNeeded={creditsNeeded}
      onBuyPages={onBuyPages}
      onUnlock={onUnlock}
    />
  );
}
