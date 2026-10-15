import { useTranslation } from 'react-i18next';
import {
  ArrowLeft, ZoomIn, ZoomOut, ChevronLeft, ChevronRight,
  Download, Loader2, CheckCircle2, ArrowRight, Lock,
  Maximize2, Minimize2, Flag,
} from 'lucide-react';

import { baseCode } from '../../lib/languages';
import ProgressBar from '../ui/ProgressBar';
import { openSupport } from '../support/supportBus';

// Le badge de langue passait par une table `LANG_LABELS` codée à la main —
// dix entrées, à tenir à jour à chaque langue ajoutée, et un doublon de ce que
// `baseCode` fait déjà (et que `DocumentCard` utilise). « en-US » -> « EN ».

interface ViewerToolbarProps {
  zoom: number;
  currentPage: number;
  numPages: number;
  isTrialMode: boolean;
  sourceFilename?: string;
  translatedFilename?: string;
  targetLang?: string;
  /** Progression du streaming : pages traduites / total. */
  isTranslating?: boolean;
  doneCount?: number;
  /** Mode agrandi (focus) actif ? */
  focus?: boolean;
  onZoomChange: (zoom: number) => void;
  onPageChange: (page: number) => void;
  onBack: () => void;
  onDownload: () => void;
  onToggleFocus?: () => void;
}

export default function ViewerToolbar({
  zoom,
  currentPage,
  numPages,
  isTrialMode,
  sourceFilename,
  translatedFilename,
  targetLang,
  isTranslating = false,
  doneCount = 0,
  focus = false,
  onZoomChange,
  onPageChange,
  onBack,
  onDownload,
  onToggleFocus,
}: ViewerToolbarProps) {
  const { t } = useTranslation();

  const pct = numPages > 0 ? Math.round((doneCount / numPages) * 100) : 0;
  const isComplete = !isTranslating && doneCount > 0 && doneCount >= numPages;
  const canDownload = !isTranslating;

  return (
    <div className="docbar">
      <button
        onClick={onBack}
        className="tb-btn ghost"
        aria-label={t('viewer.back', 'Retour')}
        title={t('viewer.back', 'Retour')}
        style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
      >
        <ArrowLeft size={15} strokeWidth={2.2} />
        <span>{t('viewer.back', 'Retour')}</span>
      </button>

      {/* Zoom */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '2px',
        borderLeft: '1px solid var(--color-border-secondary)',
        paddingLeft: '10px', marginLeft: '4px',
      }}>
        <button
          onClick={() => onZoomChange(Math.max(0.5, +(zoom - 0.25).toFixed(2)))}
          className="tb-btn ghost"
          disabled={zoom <= 0.5}
          aria-label={t('viewer.zoom_out', 'Dézoomer')}
          title={t('viewer.zoom_out', 'Dézoomer')}
          style={{ display: 'flex', alignItems: 'center', padding: '6px' }}
        >
          <ZoomOut size={15} strokeWidth={2.2} />
        </button>
        <span style={{
          fontSize: '12px', fontWeight: 600, minWidth: '42px',
          textAlign: 'center', color: 'var(--color-text-secondary)',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {Math.round(zoom * 100)}%
        </span>
        <button
          onClick={() => onZoomChange(Math.min(3, +(zoom + 0.25).toFixed(2)))}
          className="tb-btn ghost"
          disabled={zoom >= 3}
          aria-label={t('viewer.zoom_in', 'Zoomer')}
          title={t('viewer.zoom_in', 'Zoomer')}
          style={{ display: 'flex', alignItems: 'center', padding: '6px' }}
        >
          <ZoomIn size={15} strokeWidth={2.2} />
        </button>
      </div>

      {/* Fil du document : source → traduction */}
      {(sourceFilename || translatedFilename) ? (
        <div className="docbar-fil" style={{
          display: 'flex', alignItems: 'center', gap: '7px',
          borderLeft: '1px solid var(--color-border-secondary)', paddingLeft: '12px', marginLeft: '6px',
          flex: 1, overflow: 'hidden', minWidth: 0,
        }}>
          {sourceFilename && (
            <span style={{
              fontSize: '12px', color: 'var(--color-text-secondary)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '130px',
            }} title={sourceFilename}>
              {sourceFilename}
            </span>
          )}
          {sourceFilename && translatedFilename && (
            <ArrowRight size={13} strokeWidth={2} style={{ flexShrink: 0, opacity: 0.4 }} />
          )}
          {translatedFilename && (
            <span style={{
              fontSize: '12px', fontWeight: 600, color: 'var(--color-text-primary)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '150px',
            }} title={translatedFilename}>
              {translatedFilename}
            </span>
          )}
          {targetLang && (
            <span style={{
              fontSize: '10px', fontWeight: 700, letterSpacing: '0.03em',
              background: 'var(--blue-light, #eff6ff)', color: 'var(--blue)',
              padding: '2px 7px', borderRadius: '999px', flexShrink: 0,
            }}>
              {baseCode(targetLang).toUpperCase()}
            </span>
          )}
        </div>
      ) : (
        <div className="spacer" />
      )}

      {/* Progression du streaming (pages traduites / total) */}
      {(isTranslating || isComplete) && numPages > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '9px',
          borderLeft: '1px solid var(--color-border-secondary)',
          paddingLeft: '12px', marginLeft: '2px', flexShrink: 0,
        }}>
          {isTranslating ? (
            <Loader2 size={14} strokeWidth={2.5} className="ui-spin"
              style={{ color: 'var(--blue)' }} />
          ) : (
            <CheckCircle2 size={14} strokeWidth={2.2} style={{ color: '#16a34a' }} />
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: '96px' }}>
            <span style={{
              fontSize: '11px', fontWeight: 600,
              color: isTranslating ? 'var(--blue)' : '#16a34a',
              fontVariantNumeric: 'tabular-nums',
            }}>
              {isTranslating
                ? t('viewer.pages_done', '{{done}}/{{total}} pages', { done: doneCount, total: numPages })
                : t('viewer.translation_done', 'Traduction terminée')}
            </span>
            <ProgressBar
              value={isComplete ? 100 : pct}
              tone={isTranslating ? 'blue' : 'green'}
              height={3}
            />
          </div>
        </div>
      )}

      {/* Pagination */}
      <PageNav className="pg-ctrl docbar-pages" currentPage={currentPage}
        numPages={numPages} onPageChange={onPageChange} />

      {/* Mode agrandi (focus) — exploite tout l'écran, barre du haut allégée */}
      {onToggleFocus && (
        <button
          onClick={onToggleFocus}
          className="tb-btn ghost docbar-agrandir"
          aria-label={focus ? t('viewer.exit_enlarge', 'Réduire') : t('viewer.enlarge', 'Agrandir')}
          title={focus ? t('viewer.exit_enlarge', 'Réduire') : t('viewer.enlarge', 'Agrandir')}
          style={{ display: 'flex', alignItems: 'center', padding: '6px' }}
        >
          {focus ? <Minimize2 size={15} strokeWidth={2.2} /> : <Maximize2 size={15} strokeWidth={2.2} />}
        </button>
      )}

      {/* Signaler un problème sur CE document — référencé automatiquement par
          son nom (le nom traduit s'il existe, sinon le nom source). */}
      <button
        onClick={() => openSupport({
          category: 'problem',
          document: { name: translatedFilename || sourceFilename },
        })}
        className="tb-btn ghost"
        aria-label={t('support.report', 'Signaler un problème')}
        title={t('support.report', 'Signaler un problème')}
        style={{ display: 'flex', alignItems: 'center', padding: '6px' }}
      >
        <Flag size={15} strokeWidth={2.2} />
      </button>

      {/* Téléchargement — indisponible tant que la traduction n'est pas finie */}
      <button
        onClick={() => {
          if (isTrialMode) {
            document.getElementById('pricing')?.scrollIntoView({ behavior: 'smooth' });
            return;
          }
          onDownload();
        }}
        className="tb-btn ghost"
        disabled={!canDownload && !isTrialMode}
        aria-label={t('viewer.download', 'Télécharger la traduction')}
        title={
          isTrialMode
            ? t('viewer.download_locked', 'Version d’essai — débloquez le téléchargement')
            : canDownload
              ? t('viewer.download', 'Télécharger la traduction')
              : t('viewer.download_wait', 'Disponible à la fin de la traduction')
        }
        style={{
          display: 'flex', alignItems: 'center', gap: '6px',
          opacity: !canDownload && !isTrialMode ? 0.45 : 1,
          cursor: !canDownload && !isTrialMode ? 'not-allowed' : 'pointer',
        }}
      >
        {isTrialMode ? <Lock size={15} strokeWidth={2.2} /> : <Download size={15} strokeWidth={2.2} />}
      </button>
    </div>
  );
}

/** Pagination ‹ n / N › de la barre du haut. Sur téléphone, masquée : la bande
 *  de miniatures sous le document en tient lieu (voir index.css). */
function PageNav({ className, currentPage, numPages, onPageChange }: {
  className: string;
  currentPage: number;
  numPages: number;
  onPageChange: (page: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={className}>
      <button
        className="pg-arrow"
        onClick={() => onPageChange(Math.max(1, currentPage - 1))}
        disabled={currentPage <= 1}
        aria-label={t('viewer.prev_page', 'Page précédente')}
      >
        <ChevronLeft size={14} strokeWidth={2.4} />
      </button>
      <span className="pg-num" style={{ fontVariantNumeric: 'tabular-nums' }}>
        {currentPage} / {numPages}
      </span>
      <button
        className="pg-arrow"
        onClick={() => onPageChange(Math.min(numPages, currentPage + 1))}
        disabled={currentPage >= numPages}
        aria-label={t('viewer.next_page', 'Page suivante')}
      >
        <ChevronRight size={14} strokeWidth={2.4} />
      </button>
    </div>
  );
}
