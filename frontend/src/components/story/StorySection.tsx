import { useTranslation } from 'react-i18next';
import { motion, useScroll, useTransform } from 'motion/react';
import { useRef } from 'react';
import TranslationSection from '../upload/TranslationSection';
import type { TranslateConfig } from '../upload/TranslationSection';
import ViewerToolbar from '../preview/ViewerToolbar';
import DocumentPreview from '../preview/DocumentPreview';
import type { PageStatus } from '../../hooks/useStreamingTranslation';
import { baseCode } from '../../lib/languages';


interface StorySectionProps {
  showPreview: boolean;
  translatedBlob: Blob | null;
  /** Le blob traduit est-il DÉJÀ un PDF ? Vrai pour tout ce qui vient du
   *  serveur pour être regardé (/preview, /partial) ; faux pour le résultat
   *  téléchargeable, qui garde son format d'origine. */
  translatedIsPdf?: boolean;
  translatedFilename: string;
  selectedFile: File | null;
  /** Le fichier source est-il déjà un PDF (converti par le serveur) ? */
  sourceIsPdf?: boolean;
  currentPage: number;
  numPages: number;
  zoom: number;
  isTrialMode: boolean;
  targetLang?: string;
  isTranslating: boolean;
  pageStatuses: Record<number, PageStatus>;
  renderedUpTo: number;
  /** En file d'attente (aucun worker libre) : nombre de demandes devant, ou
   *  `null` si le job a démarré / n'attend pas. */
  queuePosition?: number | null;
  /** Aperçu ouvert depuis la bibliothèque, document encore en route. Distinct
   *  de `isTranslating` : rien n'est traduit ici, on attend un rendu. */
  previewRendering?: boolean;
  /** Erreur de traduction à afficher en bannière inline (pas de toast). */
  translationError?: string | null;
  /** L'erreur est un 402 → afficher un CTA vers les offres. */
  limitReached?: boolean;
  /** 402 rattrapable par un achat : pages à régler pour ce document. */
  creditsNeeded?: number | null;
  /** Ouvre le paiement à la page pour `n` pages. */
  onBuyPages?: (pages: number) => void;
  /** Mode agrandi (focus) actif ? */
  focus?: boolean;
  onStartTranslate: (config: TranslateConfig) => void;
  onBack: () => void;
  onToggleFocus?: () => void;
  onZoomChange: (z: number) => void;
  onPageChange: (p: number) => void;
  onDownload: () => void;
  onPagesLoaded: (n: number) => void;
  onLibraryOpen?: () => void;
}

export default function StorySection({
  showPreview,
  translatedBlob,
  translatedIsPdf = false,
  translatedFilename,
  selectedFile,
  sourceIsPdf = false,
  currentPage,
  numPages,
  zoom,
  isTrialMode,
  targetLang,
  isTranslating,
  pageStatuses,
  queuePosition = null,
  renderedUpTo,
  previewRendering = false,
  translationError = null,
  limitReached = false,
  creditsNeeded = null,
  onBuyPages,
  focus = false,
  onStartTranslate,
  onBack,
  onToggleFocus,
  onZoomChange,
  onPageChange,
  onDownload,
  onPagesLoaded,
  onLibraryOpen,
}: StorySectionProps) {
  // Nombre de pages déjà prêtes (traduites ou copiées) — barre de progression.
  const doneCount = Object.values(pageStatuses).filter(
    (s) => s === 'done' || s === 'copied',
  ).length;
  const { t } = useTranslation();
  const storyRef = useRef<HTMLElement>(null);

  // Format du document à prévisualiser : déterminé d'abord par le fichier
  // traduit (toujours présent), sinon par l'original. Les formats non-PDF sont
  // convertis en PDF côté serveur pour un rendu exact (cf. DocumentPreview).
  const previewExt = (
    translatedFilename.split('.').pop() ||
    selectedFile?.name.split('.').pop() ||
    'pdf'
  ).toLowerCase();

  const { scrollYProgress: storyScrollProgress } = useScroll({
    target: storyRef,
    offset: ['start end', 'end start'],
  });
  const storyVisualY = useTransform(storyScrollProgress, [0, 1], [40, -40]);

  return (
    <section
      className="story-band"
      id="story"
      ref={storyRef}
      style={{
        position: 'relative',
        overflow: 'hidden',
        background: showPreview
          ? 'var(--white)'
          : 'linear-gradient(135deg, #fbfaf7 0%, #f6f8fb 100%)',
        padding: showPreview ? '0' : '40px 0',
      }}
    >
      {/* Floating BG elements (only when not in preview) */}
      {!showPreview && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            pointerEvents: 'none',
            zIndex: 0,
          }}
        >
          <motion.div
            animate={{ y: [0, 20, 0], rotate: [0, -10, 0] }}
            transition={{ duration: 7, repeat: Infinity, ease: 'easeInOut' }}
            style={{
              position: 'absolute',
              top: '20%',
              right: '10%',
              opacity: 0.1,
              color: 'var(--blue)',
            }}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
              style={{ width: '90px', height: '90px' }}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-.778.099-1.533.284-2.253"
              />
            </svg>
          </motion.div>
          <motion.div
            animate={{ y: [0, -20, 0], rotate: [0, 15, 0] }}
            transition={{ duration: 9, repeat: Infinity, ease: 'easeInOut', delay: 2 }}
            style={{
              position: 'absolute',
              bottom: '15%',
              left: '10%',
              opacity: 0.1,
              color: 'var(--gold)',
            }}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
              style={{ width: '80px', height: '80px' }}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607zM10.5 7.5v6m3-3h-6"
              />
            </svg>
          </motion.div>
        </div>
      )}

      <div
        className="section-inner"
        style={{
          position: 'relative',
          zIndex: 1,
          maxWidth: '1240px',
          padding: '0 24px',
          margin: '0 auto',
          height: showPreview ? '100%' : 'auto',
          display: showPreview ? 'flex' : 'block',
          flexDirection: showPreview ? 'column' : 'unset',
        }}
      >
        {!showPreview ? (
          <div className="story-grid">
            {/* Left: Steps — la grille ne centre plus ses colonnes (cf. index.css),
                on centre donc ce bloc explicitement pour conserver son rendu. */}
            <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
              <span className="section-tag">{t('story.tag')}</span>
              <h2
                className="section-title"
                dangerouslySetInnerHTML={{ __html: t('story.title') }}
              />
              <p className="section-desc">{t('story.desc')}</p>

              <div className="story-steps">
                {[1, 2, 3, 4].map((step) => (
                  <div key={step} className="story-step">
                    <div className="story-step-line">
                      <div className="story-step-num">
                        {String(step).padStart(2, '0')}
                      </div>
                      {step < 4 && <div className="story-step-connector" />}
                    </div>
                    <div className="story-step-content">
                      <h3>{t(`story.step${step}_title`)}</h3>
                      <p>{t(`story.step${step}_desc`)}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Right: Translation Widget */}
            <motion.div
              className="story-visual"
              style={{ height: '100%', y: storyVisualY }}
            >
              <div
                style={{
                  backgroundColor: 'white',
                  padding: '26px 30px',
                  borderRadius: '16px',
                  boxShadow: 'var(--shadow-lg)',
                  border: '1px solid var(--gray-100)',
                  height: '100%',
                  // Plancher : la carte ne rétrécit plus sous les états courts
                  // (aucun fichier choisi), donc l'en-tête et la zone de dépôt
                  // gardent la même position d'un état à l'autre.
                  minHeight: '580px',
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                <TranslationSection onStartTranslate={onStartTranslate} isTranslating={isTranslating} onLibraryOpen={onLibraryOpen} />
              </div>
            </motion.div>
          </div>
        ) : (
          <div className="content-viewer w-full flex-1 pr-[8px]">
            <h2 className="sr-only">
              Interface de prévisualisation de traduction de document côte-à-côte
            </h2>

            {queuePosition !== null && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                padding: '14px 18px', marginBottom: '16px', borderRadius: '12px',
                background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1e40af',
                fontSize: '14px', lineHeight: 1.5,
              }}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                  style={{ flexShrink: 0, animation: 'spin 1.2s linear infinite' }}>
                  <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                </svg>
                <span style={{ flex: 1 }}>
                  {queuePosition > 0
                    ? t('story.queued_ahead', { count: queuePosition })
                    : t('story.queued_soon')}
                </span>
              </div>
            )}

            {/* Le manque de crédits (402) n'est PLUS une bannière en tête : il
                s'affiche directement DANS la zone du document (voir plus bas),
                là où l'utilisateur regarde ses pages. Ici ne restent que les
                vraies erreurs (réseau, échec de traduction) et la limite de
                forfait sans achat possible. */}
            {translationError && !creditsNeeded && (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
                padding: '14px 18px',
                marginBottom: '16px',
                borderRadius: '12px',
                background: '#fef2f2',
                border: '1px solid #fecaca',
                color: '#991b1b',
                fontSize: '14px',
                lineHeight: 1.5,
              }}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                <span style={{ flex: 1 }}>{translationError}</span>
                {limitReached && (
                  <button
                    onClick={() => {
                      const el = document.getElementById('pricing');
                      if (el) el.scrollIntoView({ behavior: 'smooth' });
                    }}
                    style={{
                      padding: '8px 16px',
                      borderRadius: '8px',
                      border: '1px solid #991b1b',
                      background: 'transparent',
                      color: '#991b1b',
                      fontSize: '13px',
                      fontWeight: 600,
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {t('story.error_limit_cta')}
                  </button>
                )}
              </div>
            )}

            <div className="app">
              <div className="body">
                <div className="sidebar">
                  {Array.from({ length: numPages }).map((_, i) => {
                    const pageNo = i + 1;
                    const status = pageStatuses[pageNo];
                    const isReady = status === 'done' || status === 'copied' || (!isTranslating && pageNo <= renderedUpTo) || (!isTranslating && Object.keys(pageStatuses).length === 0);
                    const inProgress = status === 'extracting' || status === 'translating' || status === 'rendering';
                    // Pastille d'état : vert = prête, bleu animé = en cours, gris = en attente.
                    const dotColor = isReady ? '#16a34a' : inProgress ? '#2563eb' : 'var(--gray-300)';
                    return (
                      <div
                        key={i}
                        className="thumb"
                        onClick={() => onPageChange(pageNo)}
                        title={
                          isReady ? t('story.page_ready', 'Page traduite')
                          : inProgress ? t('story.page_progress', 'Traduction en cours…')
                          : t('story.page_waiting', 'En attente')
                        }
                      >
                        <div
                          className="thumb-frame"
                          style={{
                            borderColor:
                              currentPage === pageNo ? '#2563eb' : 'var(--color-border-primary)',
                            position: 'relative',
                            opacity: isReady || currentPage === pageNo ? 1 : 0.55,
                          }}
                        >
                          <div className="tl" style={{ width: '55%', height: '3px' }} />
                          <div className="tl muted" style={{ width: '42%' }} />
                          <div className="tl accent" style={{ width: '100%', margin: '3px 0' }} />
                          <div className="tl" style={{ width: '88%' }} />
                          <div className="tl muted" style={{ width: '72%' }} />
                          <div className="tl muted" style={{ width: '80%' }} />
                          <div className="tl muted" style={{ width: '65%' }} />
                          <div className="tl accent" style={{ width: '100%', margin: '3px 0' }} />
                          {/* Pastille de statut */}
                          <span
                            style={{
                              position: 'absolute', top: '3px', right: '3px',
                              width: '8px', height: '8px', borderRadius: '50%',
                              background: dotColor,
                              boxShadow: inProgress ? '0 0 0 0 rgba(37,99,235,0.5)' : 'none',
                              animation: inProgress ? 'thumb-pulse 1.2s infinite' : 'none',
                            }}
                          />
                          {/* Voile « en cours » sur la vignette active du travail */}
                          {inProgress && (
                            <span style={{
                              position: 'absolute', inset: 0, display: 'flex',
                              alignItems: 'center', justifyContent: 'center',
                              background: 'rgba(255,255,255,0.4)',
                            }}>
                              <motion.span
                                animate={{ rotate: 360 }}
                                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                                style={{ display: 'inline-flex', color: '#2563eb' }}
                              >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                                  <circle cx="12" cy="12" r="10" opacity="0.25" />
                                  <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
                                </svg>
                              </motion.span>
                            </span>
                          )}
                        </div>
                        <div className="thumb-num">{pageNo}</div>
                      </div>
                    );
                  })}
                </div>

                <div className="main">
                  <ViewerToolbar
                    zoom={zoom}
                    currentPage={currentPage}
                    numPages={numPages}
                    isTrialMode={isTrialMode}
                    sourceFilename={selectedFile?.name}
                    translatedFilename={translatedFilename}
                    targetLang={targetLang}
                    isTranslating={isTranslating}
                    doneCount={doneCount}
                    focus={focus}
                    onZoomChange={onZoomChange}
                    onPageChange={onPageChange}
                    onBack={onBack}
                    onDownload={onDownload}
                    onToggleFocus={onToggleFocus}
                  />


                  <div className="scroll" id="scroll">
                    <DocumentPreview
                      sourceFile={selectedFile}
                      translatedBlob={translatedBlob}
                      // `ext` désigne le format de la SOURCE. Servie convertie
                      // par le serveur, elle n'a plus rien à faire convertir.
                      ext={sourceIsPdf ? 'pdf' : previewExt}
                      translatedExt={
                        // Le blob traduit est déjà un PDF chaque fois qu'il
                        // vient du serveur pour être REGARDÉ : partiel du
                        // streaming ET aperçu bibliothèque. La condition ne
                        // couvrait que le streaming — hors streaming, on
                        // renvoyait donc au serveur, sous le nom `.pptx`, un
                        // PDF qu'il venait lui-même de produire, pour un
                        // aller-retour LibreOffice complet à chaque page.
                        translatedIsPdf ? 'pdf' : undefined
                      }
                      currentPage={currentPage}
                      zoom={zoom}
                      isTrialMode={isTrialMode}
                      previewLoading={previewRendering}
                      onPagesLoaded={onPagesLoaded}
                      translatedPageReady={
                        // `previewRendering` PRIME sur tout le reste. Pendant
                        // le rendu d'une page (bibliothèque), le blob affiché
                        // est encore celui de la page PRÉCÉDENTE — laisser le
                        // canevas visible montrait la page recopiée de
                        // l'original, donc en langue source : en tournant les
                        // pages, on ne voyait QUE de l'anglais, alors que le
                        // français arrivait 10 s plus tard. Un spinner honnête
                        // vaut mieux qu'une page fausse.
                        !previewRendering && (
                          (!isTranslating && Object.keys(pageStatuses).length === 0)
                          || pageStatuses[currentPage] === 'done'
                          || pageStatuses[currentPage] === 'copied'
                          || currentPage <= renderedUpTo
                        )
                      }
                      translatedPageStatus={previewRendering ? 'rendering' : pageStatuses[currentPage]}
                      sourceLabel={t('preview.source_label', 'Document original')}
                      targetLabel={`${baseCode(targetLang ?? 'en').toUpperCase()} — ${t('preview.target_label', 'Traduction')}`}
                      // Manque de crédits (402) : le panneau TRADUIT affiche
                      // l'invitation à régler les pages, avec le bouton dessous —
                      // à la place du message « page en attente ».
                      creditsNeeded={creditsNeeded}
                      onBuyPages={onBuyPages}
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
