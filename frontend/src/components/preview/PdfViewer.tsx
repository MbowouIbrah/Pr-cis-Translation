import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'motion/react';
import { Loader2, Hourglass, Lock } from 'lucide-react';

// Largeur du cadre d'aperçu (#scroll) sous laquelle deux volets côte à côte
// tombent sous ~300 px chacun : texte illisible. C'est le CADRE qui compte, pas
// l'écran — une barre latérale peut le rétrécir sur un écran large.
const COMPACT_MAX_PX = 680;
const COMPACT_MARGE_PX = 24;

const largeurCadre = () => document.getElementById('scroll')?.clientWidth ?? window.innerWidth;

interface PdfViewerProps {
  sourceFile?: Blob | null;
  translatedBlob?: Blob | null;
  demoSource?: string;
  demoTarget?: string;
  currentPage: number;
  zoom: number;
  isTrialMode: boolean;
  onPagesLoaded?: (numPages: number) => void;
  className?: string;
  /** La page courante est-elle traduite (streaming) ? Sinon on affiche un
      placeholder à la place du canevas traduit. */
  translatedPageReady?: boolean;
  translatedPageStatus?: string;
  /** Libellés des panneaux (langue source détectée / langue cible). */
  sourceLabel?: string;
  targetLabel?: string;
  /** true = chargement en cours (ne PAS afficher la démo). */
  previewLoading?: boolean;
  /** Manque de crédits (402) : pages à régler. Le panneau traduit affiche
   *  l'invitation à payer, avec le bouton dessous, au lieu du message d'attente. */
  creditsNeeded?: number | null;
  onBuyPages?: (pages: number) => void;
  /** Essai : quitter l'aperçu pour les tarifs. */
  onUnlock?: () => void;
}

export default function PdfViewer({
  sourceFile,
  translatedBlob,
  // Vitrine : la page de journal montre les cas qui comptent (colonnes,
  // manchette, encadrés, filets) là où un CV n'en montrait aucun.
  demoSource = '/demo_journal_avant.pdf',
  demoTarget = '/demo_journal_apres.pdf',
  currentPage,
  zoom,
  isTrialMode,
  onPagesLoaded,
  className,
  translatedPageReady = true,
  translatedPageStatus,
  sourceLabel,
  targetLabel,
  previewLoading = false,
  creditsNeeded,
  onBuyPages,
  onUnlock,
}: PdfViewerProps) {
  const { t } = useTranslation();
  const [cursorPos, setCursorPos] = useState<{ x: number; y: number } | null>(null);
  // True quand le canevas traduit affiche RÉELLEMENT la page courante.
  const [translatedShown, setTranslatedShown] = useState(false);
  // Orientation de la PAGE COURANTE : paysage → panneaux empilés (vertical),
  // portrait → panneaux côte à côte (horizontal). Détectée par page — un même
  // document peut mêler des pages paysage et portrait, et la disposition suit.
  const [isLandscape, setIsLandscape] = useState(false);
  // Écran ÉTROIT (téléphone) : deux volets côte à côte n'y font que ~150 px
  // chacun, texte illisible. On n'en montre alors qu'UN, en pleine largeur, et
  // une bascule passe de l'un à l'autre — la traduction d'abord.
  const [compact, setCompact] = useState(
    () => typeof window !== 'undefined' && largeurCadre() < COMPACT_MAX_PX,
  );
  const [volet, setVolet] = useState<'source' | 'target'>('target');
  const containerRef = useRef<HTMLDivElement>(null);
  const basculeRef = useRef<HTMLDivElement>(null);
  // Sans original, il n'y a rien à basculer : le volet traduit seul.
  const voletVu = !compact ? null : sourceFile ? volet : 'target';
  const libelleSource = sourceLabel ?? t('preview.source_label');
  const libelleCible = targetLabel ?? t('preview.target_label');
  // Écran sans survol (tactile) : l'invite d'essai dit « touchez », pas « survolez ».
  const sansSurvol = typeof window !== 'undefined' && !!window.matchMedia?.('(hover: none)').matches;

  useEffect(() => {
    // Hystérésis : la barre de défilement qui apparaît ou disparaît change la
    // largeur de quelques pixels, de quoi faire osciller la bascule (et
    // recharger le PDF à chaque fois) si l'entrée et la sortie coïncidaient.
    const suivre = () => setCompact((c) => {
      const l = largeurCadre();
      return c ? l < COMPACT_MAX_PX + COMPACT_MARGE_PX : l < COMPACT_MAX_PX;
    });
    suivre();   // le cadre a pu changer entre le premier rendu et cet effet
    const cadre = document.getElementById('scroll');
    if (cadre && typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(suivre);
      ro.observe(cadre);
      return () => ro.disconnect();
    }
    window.addEventListener('resize', suivre);
    return () => window.removeEventListener('resize', suivre);
  }, []);

  useEffect(() => {
    let active = true;
    const renderTasks: any[] = [];
    const loadingTasks: any[] = [];

    const loadPDFs = async () => {
      const pdfjsLib = (window as any).pdfjsLib;
      if (!pdfjsLib) {
        console.error('PDF.js not loaded');
        return;
      }

      pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';

      // Ouvre un document en RECENSANT sa tâche et en désamorçant son rejet.
      // `destroy()` au démontage fait rejeter `task.promise` ; si personne ne
      // l'attend encore (cas courant du second document, détruit avant son
      // `await`), le rejet part en « Uncaught (in promise) ». Ce `catch` vide
      // n'avale rien d'utile : l'`await` plus bas reçoit le même rejet et le
      // traite. Il dit seulement au navigateur que cette promesse a un
      // responsable.
      const open = (src: any) => {
        const task = pdfjsLib.getDocument(src);
        loadingTasks.push(task);
        task.promise.catch(() => { /* voir `await` ci-dessous */ });
        return task;
      };

      try {
        let pdfSourceOrig: any;
        let pdfSourceTrad: any = null;

        // Le document CHOISI prime toujours. Sans données, on n'affiche rien :
        // le composant n'est rendu que dans l'aperçu, jamais en vitrine.
        // PLUS DE DÉMO : l'ancien fallback chargeait le journal d'exemple
        // dès que les deux requêtes échouaient — c'est exactement le bug
        // « un autre document s'affiche » qui revenait à chaque échec.
        if (sourceFile) {
          // Données passées directement à pdf.js (PAS d'URL blob : l'effet se
          // relance à chaque changement de page/zoom et le cleanup révoquait
          // l'URL pendant que le worker la chargeait encore → blob introuvable).
          const origBuf = await sourceFile.arrayBuffer();
          if (!active) return;
          pdfSourceOrig = open({ data: origBuf });
          if (translatedBlob) {
            const tradBuf = await translatedBlob.arrayBuffer();
            if (!active) return;
            pdfSourceTrad = open({ data: tradBuf });
          }
        } else if (translatedBlob) {
          // Un document RÉEL sans sa source.
          const tradBuf = await translatedBlob.arrayBuffer();
          if (!active) return;
          pdfSourceTrad = open({ data: tradBuf });
          pdfSourceOrig = null;
        } else {
          // Aucun document — ni source, ni traduction. On ne montre rien.
          return;
        }

        const pdfOrig = pdfSourceOrig ? await pdfSourceOrig.promise : null;
        if (!active) return;

        const pdfTrad = pdfSourceTrad ? await pdfSourceTrad.promise : null;
        if (!active) return;

        // La pagination suit l'original quand il existe, le traduit sinon :
        // sans ça, un aperçu sans source resterait bloqué sur une seule page.
        const pageSource = pdfOrig ?? pdfTrad;
        if (pageSource) onPagesLoaded?.(pageSource.numPages);

        // Orientation de la PAGE COURANTE, déterminée UNE fois AVANT tout rendu.
        // On la calcule ici (valeur locale `landscape`) et non depuis l'état
        // React : un `setIsLandscape` ne prend effet qu'au rendu suivant, donc
        // lire l'état ici donnait l'orientation de la page PRÉCÉDENTE et cadrait
        // mal la première frame. La source et la traduction sont la même page,
        // donc une seule mesure suffit pour les deux panneaux.
        let landscape = false;
        if (pageSource) {
          const probeNum = Math.min(Math.max(1, currentPage), pageSource.numPages);
          const probe = await pageSource.getPage(probeNum);
          if (!active) return;
          const pv = probe.getViewport({ scale: 1.0 });
          landscape = pv.width > pv.height;
        }
        setIsLandscape(landscape);

        const renderPage = async (pdf: any, canvasId: string, pageNum: number) => {
          // Borne la page demandée : un document mono-page recevait encore le
          // numéro de page de l'état précédent → « Invalid page request ».
          const safePage = Math.min(Math.max(1, pageNum), pdf.numPages);
          const page = await pdf.getPage(safePage);
          if (!active) return;

          const canvas = document.getElementById(canvasId) as HTMLCanvasElement;
          if (!canvas) return;

          // Paysage : chaque panneau prend toute la largeur (empilés verticalement,
          // donc lisibles). Portrait : chaque panneau prend la moitié (côte à côte).
          // Le dimensionnement lit la valeur LOCALE `landscape`, jamais l'état —
          // il est donc juste dès la première frame, sans saut d'encadrement.
          let containerWidth = 600;
          let containerHeight = 700;
          const scrollEl = document.getElementById('scroll');
          if (scrollEl) {
            // Écran étroit : le cadre n'a plus de marge (index.css), le volet
            // prend toute la largeur utile — on la lit, on ne la suppose pas.
            const cs = getComputedStyle(scrollEl);
            const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
            containerWidth = compact
              ? scrollEl.clientWidth - padX
              : landscape
                ? scrollEl.clientWidth - 24
                : (scrollEl.clientWidth - 70) / 2;
            // Hauteur disponible : le conteneur moins son padding (24px × 2) et
            // la hauteur du badge de langue au-dessus de chaque panneau (~34px).
            // Paysage : deux panneaux EMPILÉS → chacun a la MOITIÉ de la hauteur.
            // Écran étroit : un seul volet visible, sous la bascule (sa hauteur
            // mesurée + l'écart de 12px qui la sépare du volet).
            const hDispo = scrollEl.clientHeight - 48 - 34;
            const hBascule = basculeRef.current ? basculeRef.current.offsetHeight + 12 : 0;
            containerHeight = compact
              ? hDispo - hBascule
              : landscape ? (hDispo - 24) / 2 : hDispo;
          } else {
            containerWidth = canvas.parentElement?.clientWidth || 600;
          }

          const unscaledViewport = page.getViewport({ scale: 1.0 });
          // Caler sur la dimension la PLUS CONTRAIGNANTE : la page tient alors
          // ENTIÈREMENT dans le cadre (largeur ET hauteur) — plus de débordement,
          // « tient sur une page ». `zoom` agrandit ensuite au-delà si besoin.
          // Écran étroit : calé sur la LARGEUR, comme une visionneuse — une page
          // entière en hauteur y serait illisible ; on défile verticalement.
          const baseScale = Math.max(0.05, compact
            ? containerWidth / unscaledViewport.width
            : Math.min(
              containerWidth / unscaledViewport.width,
              containerHeight / unscaledViewport.height,
            ));
          const scale = baseScale * zoom;
          const viewport = page.getViewport({ scale });
          const context = canvas.getContext('2d');
          if (!context) return;

          // Rendu à la densité de l'écran (×2, ×3 sur téléphone), affiché à la
          // taille CSS du viewport : sans cela le texte est flou. Le voile et le
          // curseur d'essai se calent sur la taille AFFICHÉE, inchangée.
          const dpr = Math.min(window.devicePixelRatio || 1, 3);
          canvas.width = Math.floor(viewport.width * dpr);
          canvas.height = Math.floor(viewport.height * dpr);
          canvas.style.width = `${Math.floor(viewport.width)}px`;
          canvas.style.height = `${Math.floor(viewport.height)}px`;

          const renderContext = {
            canvasContext: context, viewport,
            transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
          };
          const renderTask = page.render(renderContext);
          renderTasks.push(renderTask);
          await renderTask.promise;
        };

        if (pdfOrig) {
          await renderPage(pdfOrig, 'pdf-canvas-original', currentPage);
        } else {
          // Pas de source : on laisse le panneau VIDE plutôt que d'y mettre un
          // document étranger.
          const canvas = document.getElementById('pdf-canvas-original') as HTMLCanvasElement;
          const ctx = canvas?.getContext('2d');
          if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
        }

        // Panneau TRADUIT : n'affiche la page que si le PDF traduit existe, que
        // la page est prête ET présente dedans (sinon le viewer clamperait sur
        // une autre page).
        const tradHasPage = !!pdfTrad && translatedPageReady && currentPage <= pdfTrad.numPages;
        if (tradHasPage) {
          // UN SEUL canevas, même en mode essai : l'assombrissement se fait par
          // un voile au-dessus (avec un trou sous le curseur), plus par un second
          // canevas « net » qu'il fallait aligner au pixel. Deux calques à caler
          // se décalaient au zoom — un seul ne peut pas se décaler de lui-même.
          await renderPage(pdfTrad, 'pdf-canvas-translated', currentPage);
          if (active) setTranslatedShown(true);
        } else {
          // Page pas encore traduite : on efface le canevas et on montre le
          // placeholder (dimensionné comme l'original pour un cadre stable).
          if (active) setTranslatedShown(false);
          const canvas = document.getElementById('pdf-canvas-translated') as HTMLCanvasElement;
          const origCanvas = document.getElementById('pdf-canvas-original') as HTMLCanvasElement;
          if (canvas && origCanvas) {
            canvas.width = origCanvas.width;
            canvas.height = origCanvas.height;
            canvas.style.width = origCanvas.style.width;
            canvas.style.height = origCanvas.style.height;
            const ctx = canvas.getContext('2d');
            if (ctx) { ctx.fillStyle = '#f8fafc'; ctx.fillRect(0, 0, canvas.width, canvas.height); }
          }
        }
      } catch (error) {
        // Un effet démonté a DÉJÀ appelé `cancel()`/`destroy()` : le rejet qui
        // arrive ici est celui qu'on a demandé (« Worker was destroyed »,
        // « RenderingCancelledException »…). Le journaliser revenait à crier
        // au feu en voyant l'extincteur : en StrictMode, React monte-démonte-
        // remonte chaque effet, donc la console s'en remplissait à chaque
        // aperçu. On ne signale que ce qui casse un rendu ENCORE attendu.
        if (!active) return;
        if (error instanceof Error && error.name === 'RenderingCancelledException') return;
        console.error('Error loading PDF:', error);
      }
    };

    loadPDFs();

    return () => {
      active = false;
      renderTasks.forEach((task) => {
        try {
          task.cancel();
        } catch (e) {
          // ignore
        }
      });
      loadingTasks.forEach((task) => {
        try {
          task.destroy();
        } catch (e) {
          // ignore
        }
      });
    };
  }, [currentPage, zoom, translatedBlob, sourceFile, isTrialMode, demoSource, demoTarget, onPagesLoaded, translatedPageReady, previewLoading, compact]);

  const [isHovering, setIsHovering] = useState(false);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{
        display: 'inline-flex',
        flexDirection: isLandscape || compact ? 'column' : 'row',
        gap: compact ? '12px' : isLandscape ? '24px' : '20px',
        alignItems: 'center',
        justifyContent: 'center',   // panneaux avant/après centrés dans le cadre
        minWidth: '100%',
      }}
    >
      {compact && sourceFile && (
        <div ref={basculeRef} className="cv-bascule" role="group" aria-label={t('preview.toggle_label')}>
          {(['source', 'target'] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={volet === v}
              onClick={() => setVolet(v)}
            >
              {v === 'source' ? libelleSource : libelleCible}
            </button>
          ))}
        </div>
      )}

      {/* Original Panel */}
      <div
        className="cv-wrap"
        style={{ flexShrink: 0, display: voletVu && voletVu !== 'source' ? 'none' : undefined }}
      >
        <span className="cv-lang-badge fr">{libelleSource}</span>
        <div className="cv" id="cv-fr">
          <canvas
            id="pdf-canvas-original"
            style={{ display: 'block', height: 'auto', margin: '0 auto' }}
          />
        </div>
      </div>

      {/* Séparateur : horizontal quand les panneaux sont empilés (paysage),
          vertical quand ils sont côte à côte (portrait) */}
      <div
        style={{
          display: compact ? 'none' : undefined,
          width: isLandscape ? '100%' : '1px',
          height: isLandscape ? '1px' : undefined,
          alignSelf: 'stretch',
          background: 'var(--color-border-tertiary)',
          flexShrink: 0,
        }}
      />

      {/* Translation Panel */}
      <div
        className="cv-wrap"
        style={{ flexShrink: 0, display: voletVu && voletVu !== 'target' ? 'none' : undefined }}
      >
        <span
          className="cv-lang-badge en"
          style={{ background: '#f0fdf4', color: '#15803d' }}
        >
          {libelleCible}
        </span>
        <div
          className="cv trial-viewer"
          id="cv-en"
          style={{ position: 'relative' }}
        >
          {/* Pile canevas + surcouches, calée EXACTEMENT sur la taille du canevas
              (donc à la même échelle que le zoom). On mesure le curseur ICI, pas
              sur `cv-en` : le canevas est centré (`margin:auto`), et sa marge de
              centrage change avec le zoom. Repérer le masque et le cadre dans le
              référentiel de `cv-en` les décalait d'autant — d'où le décalage au
              zoom. Dans cette pile, tout partage le repère du canevas. */}
          <div
            style={{
              position: 'relative',
              width: 'fit-content',
              margin: '0 auto',
              // Contient l'ombre portée géante du voile d'essai : elle ne doit
              // assombrir QUE le document, pas déborder autour.
              overflow: 'hidden',
              // Curseur masqué seulement là où le voile le remplace : sur l'écran
              // « pages à régler » ou d'attente, l'utilisateur doit le voir.
              cursor: isTrialMode && translatedShown ? 'none' : 'auto',
            }}
            onMouseMove={(e) => {
              if (!isTrialMode) return;
              const rect = e.currentTarget.getBoundingClientRect();
              setCursorPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
              setIsHovering(true);
            }}
            onMouseEnter={() => setIsHovering(true)}
            onMouseLeave={() => { setCursorPos(null); setIsHovering(false); }}
            // Écran tactile : pas de survol. Toucher (ou glisser) place le trou,
            // qui reste là au lâcher — sinon la traduction resterait toute voilée.
            onTouchStart={(e) => {
              if (!isTrialMode) return;
              const rect = e.currentTarget.getBoundingClientRect();
              const tp = e.touches[0];
              setCursorPos({ x: tp.clientX - rect.left, y: tp.clientY - rect.top });
              setIsHovering(true);
            }}
            onTouchMove={(e) => {
              if (!isTrialMode) return;
              const rect = e.currentTarget.getBoundingClientRect();
              const tp = e.touches[0];
              setCursorPos({ x: tp.clientX - rect.left, y: tp.clientY - rect.top });
            }}
          >
          {/* Canevas traduit — UNIQUE. En essai il n'est pas filtré : c'est le
              voile au-dessus qui l'assombrit, en laissant un trou sous le curseur. */}
          <canvas
            id="pdf-canvas-translated"
            style={{ display: 'block', height: 'auto' }}
          />

          {/* Placeholder « page en cours / en attente » (streaming) */}
          {!translatedShown && (
            <div
              style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
                background: 'rgba(248,250,252,0.75)',
                backdropFilter: 'blur(1px)',
                color: 'var(--gray-500)',
                textAlign: 'center',
                padding: '20px',
              }}
            >
              {creditsNeeded && onBuyPages ? (
                /* Manque de crédits : ici, dans le panneau traduit, on dit
                   simplement qu'il faut régler les pages — et le bouton juste
                   dessous lance le paiement. Pas de bannière ailleurs. */
                <>
                  <Lock size={26} strokeWidth={1.8} style={{ color: 'var(--blue)' }} />
                  <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--gray-700)', maxWidth: '260px' }}>
                    {t('preview.credits_needed', { count: creditsNeeded })}
                  </span>
                  <button
                    onClick={() => onBuyPages(creditsNeeded)}
                    style={{
                      marginTop: '4px', padding: '10px 20px', borderRadius: '10px', border: 'none',
                      background: 'linear-gradient(135deg, var(--blue) 0%, #1d4ed8 100%)',
                      color: 'white', fontSize: '13px', fontWeight: 600,
                      fontFamily: 'inherit', cursor: 'pointer',
                      boxShadow: '0 4px 14px rgba(37,99,235,0.28)',
                    }}
                  >
                    {t('story.buy_pages_cta', 'Payer {{count}} page(s)', { count: creditsNeeded })}
                  </button>
                </>
              ) : translatedPageStatus && translatedPageStatus !== 'waiting' ? (
                <>
                  <motion.span
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                    style={{ display: 'inline-flex', color: '#2563eb' }}
                  >
                    <Loader2 size={30} strokeWidth={2.2} />
                  </motion.span>
                  <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--gray-700)' }}>
                    {translatedPageStatus === 'extracting' && t('preview.status_extracting')}
                    {translatedPageStatus === 'translating' && t('preview.status_translating')}
                    {translatedPageStatus === 'rendering' && t('preview.status_rendering')}
                  </span>
                </>
              ) : (
                <>
                  <Hourglass size={26} strokeWidth={1.8} style={{ opacity: 0.55 }} />
                  <span style={{ fontSize: '13px' }}>{t('preview.page_pending')}</span>
                </>
              )}
            </div>
          )}

          {/* Voile d'essai : tout est assombri SAUF une fenêtre sous le curseur.
              Réalisé par l'ombre portée géante d'un petit cadre — donc un SEUL
              canevas, aucune couche « nette » à aligner (c'était la source du
              décalage). Sans curseur : voile plein, rien n'est lisible. */}
          {isTrialMode && translatedShown && (
            cursorPos ? (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                style={{
                  position: 'absolute',
                  width: '190px',
                  height: '190px',
                  left: cursorPos.x - 95,
                  top: cursorPos.y - 95,
                  border: '2px solid rgba(37,99,235,0.7)',
                  borderRadius: '8px',
                  pointerEvents: 'none',
                  zIndex: 20,
                  boxShadow: '0 0 0 9999px rgba(13,27,62,0.86)',
                }}
              />
            ) : (
              <div style={{
                position: 'absolute', inset: 0,
                background: 'rgba(13,27,62,0.86)', pointerEvents: 'none',
              }} />
            )
          )}

          </div>

          {/* Bandeau d'essai — hors de la pile, collant en bas de cv-en. Affiché
              dès que la page traduite l'est (pas seulement au survol : c'est lui
              qui dit QU'il faut survoler). Une seule ligne, jamais plus large
              que la page : la consigne se tronque, le lien reste entier. */}
          {isTrialMode && translatedShown && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              style={{
                position: 'sticky',
                bottom: '0',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
                width: '100%', boxSizing: 'border-box', minWidth: 0,
                background: 'rgba(13,27,62,0.92)',
                color: 'white',
                padding: '7px 10px',
                fontSize: '12px',
                whiteSpace: 'nowrap',
                zIndex: 30,
                backdropFilter: 'blur(4px)',
              }}
            >
              <Lock size={12} strokeWidth={2.2} style={{ flexShrink: 0 }} />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>
                {t(sansSurvol ? 'preview.trial_touch' : 'preview.trial_hover')}
              </span>
              <a
                href="#pricing"
                onClick={(e) => { if (onUnlock) { e.preventDefault(); onUnlock(); } }}
                style={{ color: '#93c5fd', textDecoration: 'underline', flexShrink: 0 }}
              >
                {t('preview.trial_buy')}
              </a>
            </motion.div>
          )}
        </div>
      </div>
    </div>
  );
}
