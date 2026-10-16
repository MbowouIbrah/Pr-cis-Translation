import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import Navbar from '../components/navbar/Navbar';
import HeroSection from '../components/hero/HeroSection';
import LanguageMarquee from '../components/hero/LanguageMarquee';
import FeaturesGrid from '../components/features/FeaturesGrid';
import StorySection from '../components/story/StorySection';
import PricingSection from '../components/pricing/PricingSection';
import AboutSection from '../components/about/AboutSection';
import ToastContainer, { showToast } from '../components/ui/Toast';
import SupportModal from '../components/support/SupportModal';
import PaymentModal from '../components/payment/PaymentModal';
import DocumentLibrary from '../components/library/DocumentLibrary';
import { useAuth } from '../contexts/AuthContext';
import { useDocumentLibrary } from '../hooks/useDocumentLibrary';
import { useStreamingTranslation } from '../hooks/useStreamingTranslation';
import { isTrialFor } from '../lib/plans';
import type { TranslateConfig } from '../components/upload/TranslationSection';

export default function Home() {
  const { t } = useTranslation();
  const [isAnnual, setIsAnnual] = useState(true);
  const [activeSection, setActiveSection] = useState('hero');
  const [showPreview, setShowPreview] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [numPages, setNumPages] = useState(1);
  const [zoom, setZoom] = useState(1.0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  // Le fichier SOURCE affiché est-il déjà un PDF ? Vrai pour un aperçu ouvert
  // depuis la bibliothèque (le serveur le convertit). Faux pour une traduction
  // en direct, où la source est le fichier local que l'utilisateur vient de
  // déposer, dans son format d'origine.
  const [sourceIsPdf, setSourceIsPdf] = useState(false);
  const [translatedBlob, setTranslatedBlob] = useState<Blob | null>(null);
  // Le blob traduit affiché est-il DÉJÀ un PDF ?
  //
  // Il l'est chaque fois qu'il vient du serveur pour être REGARDÉ (/preview,
  // /partial : le backend y convertit lui-même). Il est au format d'origine
  // seulement quand c'est le RÉSULTAT téléchargeable de la traduction.
  //
  // Sans cette distinction, l'aperçu bibliothèque d'un PPTX renvoyait le PDF
  // reçu au serveur sous le nom `preview.pptx` : LibreOffice l'importait dans
  // Draw et le ré-exportait, à chaque changement de page. Un aller-retour de
  // plusieurs secondes pour ne rien changer — et un rendu abîmé au passage.
  const [translatedIsPdf, setTranslatedIsPdf] = useState(false);
  const [translatedFilename, setTranslatedFilename] = useState<string>('');
  const [showLibrary, setShowLibrary] = useState(false);
  const [targetLang, setTargetLang] = useState('en');
  // Nombre de pages à régler pour débloquer la traduction courante (402 crédits
  // insuffisants). `null` = pas de paiement en cours. La dernière config est
  // mémorisée pour REJOUER la traduction telle quelle une fois les pages payées.
  const [payPages, setPayPages] = useState<number | null>(null);
  const lastConfigRef = useRef<TranslateConfig | null>(null);
  // Aperçu ouvert depuis la bibliothèque : le panneau s'affiche tout de suite,
  // ce drapeau dit au viewer de montrer « rendu en cours » au lieu du sablon
  // « page en attente », qui ferait croire à une traduction inachevée.
  const [previewLoading, setPreviewLoading] = useState(false);
  // Mode AGRANDI (focus) : l'aperçu prend tout le cadre sous une navbar allégée.
  const [previewFocus, setPreviewFocus] = useState(false);
  // DEUX compteurs, et non un seul partagé. L'ouverture d'un aperçu et le
  // chargement d'une page sont deux courses distinctes : avec un compteur
  // unique, l'effet de page incrémentait le jeton juste après l'ouverture et
  // périmait le chargement du document SOURCE — le panneau gauche restait vide
  // et la comparaison côte à côte disparaissait.
  const openToken = useRef(0);    // une ouverture d'aperçu
  const pageToken = useRef(0);    // une demande de page
  // Pages déjà reçues (clé `docId:page`) et document dont on possède le rendu
  // COMPLET : c'est ce qui rend la navigation instantanée au retour sur une
  // page, et muette côté réseau quand le serveur a livré tout le document.
  const pageBlobCache = useRef(new Map<string, Blob>());
  const fullDocFor = useRef<string | null>(null);
  // Document rouvert pendant sa traduction : chaque tick redemande l'aperçu.
  // Les relances ne repassent pas l'écran en « chargement » (clignotement).
  const [previewPoll, setPreviewPoll] = useState(0);
  const enSuivi = useRef(false);
  // Échecs d'affilée PENDANT le suivi : un raté réseau ne doit pas figer
  // l'aperçu, mais un document disparu ne doit pas être redemandé sans fin.
  const echecsSuivi = useRef(0);
  // Document de la BIBLIOTHÈQUE en cours d'aperçu (null = traduction en direct,
  // qui reçoit ses pages par le flux et n'a rien à redemander).
  const [libraryDocId, setLibraryDocId] = useState<string | null>(null);

  // ---- Traduction PROGRESSIVE (page par page) ----
  const stream = useStreamingTranslation();
  const { user, refreshUser } = useAuth();

  // Mode ESSAI : DÉRIVÉ du plan, jamais stocké. C'était un `useState(true)`
  // dont le setter n'était appelé nulle part — l'aperçu restait donc assombri
  // et le téléchargement verrouillé pour TOUT LE MONDE, abonnés et admin
  // compris. Un état qui ne change jamais n'est pas un état : c'est un calcul.
  const isTrialMode = isTrialFor(user);

  // ---- Document library ----
  const { documents, refresh, saveDocument, getBlob, getPreviewBlob, getOriginalBlob, deleteDocument, clearAll } = useDocumentLibrary();

  // ---- Scroll spy ----
  useEffect(() => {
    const sections = document.querySelectorAll('section[id]');
    const observerOptions = { root: null, rootMargin: '-10% 0px -60% 0px', threshold: 0 };

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          setActiveSection(entry.target.id);
        }
      });
    }, observerOptions);

    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, []);

  // ---- Preview mode class ----
  // `preview-active` = l'aperçu est ouvert (ajuste la hauteur de la section pour
  // qu'elle tienne sur un écran). `preview-focus` = mode agrandi (navbar
  // allégée, surcouche plein cadre, snap coupé). Le focus n'a de sens que si
  // l'aperçu est ouvert.
  useEffect(() => {
    const htmlEl = document.documentElement;
    htmlEl.classList.toggle('preview-active', showPreview);
    htmlEl.classList.toggle('preview-focus', showPreview && previewFocus);
    return () => {
      htmlEl.classList.remove('preview-active');
      htmlEl.classList.remove('preview-focus');
    };
  }, [showPreview, previewFocus]);

  // L'aperçu est une surcouche fixe (index.css) : l'ouvrir ne demande plus de
  // défiler jusqu'à lui. En le fermant, on revient sur le formulaire de
  // traduction, quel que soit l'endroit d'où l'aperçu a été ouvert.
  const apercuEtaitOuvert = useRef(false);
  const retourVers = useRef<'story' | 'pricing'>('story');
  useEffect(() => {
    if (apercuEtaitOuvert.current && !showPreview) {
      document.getElementById(retourVers.current)?.scrollIntoView({ behavior: 'instant' });
      retourVers.current = 'story';
    }
    apercuEtaitOuvert.current = showPreview;
  }, [showPreview]);

  const toggleFocus = useCallback(() => setPreviewFocus((v) => !v), []);

  // ---- Nav click handler ----
  const handleNavClick = useCallback((sectionId: string) => {
    // Quitter une autre section depuis le mode agrandi : on revient d'abord au
    // rendu NORMAL (la surcouche plein cadre `preview-focus` est `position:fixed`
    // + `overflow:hidden` — sans ça, le scroll vers la cible n'aurait aucun effet).
    setPreviewFocus(false);
    const el = document.getElementById(sectionId);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' });
    }
  }, []);

  // ---- Démarrage de la traduction : l'aperçu s'ouvre IMMÉDIATEMENT, les pages
  //      traduites y apparaissent au fil de l'eau (streaming page par page). ----
  const handleStartTranslate = useCallback(
    (config: TranslateConfig) => {
      // Le catalogue ne propose que des codes de base ('en', 'fr'…) : plus de
      // variante régionale à réduire avant l'envoi.
      const { file, targetLang, pages, debug, precise } = config;
      // Mémorisé pour rejouer À L'IDENTIQUE après un achat de pages (402).
      lastConfigRef.current = config;
      setSelectedFile(file);
      setSourceIsPdf(false);      // fichier local, format d'origine
      setTargetLang(targetLang);
      // Une traduction EN DIRECT reçoit ses pages par le flux : elle n'a rien
      // à redemander au serveur. Sans cette remise à zéro, l'effet d'aperçu
      // continuerait de réclamer les pages du document précédent et les
      // poserait par-dessus celles qui arrivent.
      setLibraryDocId(null);
      setPreviewLoading(false);
      setTranslatedBlob(null);
      setTranslatedFilename('');
      setCurrentPage(1);
      setShowPreview(true);

      stream
        .start(file, targetLang, pages, debug, precise)
        .then((result) => {
          setTranslatedBlob(result.blob);
          setTranslatedIsPdf(false);   // le RÉSULTAT est au format d'origine
          setTranslatedFilename(result.filename);
          const ext = result.filename.split('.').pop()?.toLowerCase() ?? 'pdf';
          saveDocument(result.blob, result.filename, {
            originalName: file.name,
            targetLang,
            ext,
          });
          // Forfait freemium = 1 page max, on invite à upgrader
          if (user?.plan === 'free') {
            showToast('success', t('story.free_plan_title'), t('story.free_plan_hint'));
          } else {
            showToast('success', t('story.success_done'), result.filename);
          }
        })
        .catch(() => {
          // L'erreur est déjà dans stream.error (et stream.limitReached pour
          // les 402) — on laisse StorySection l'afficher en bannière inline.
        });
    },
    [stream, saveDocument, t],
  );

  // ---- Library preview ----
  const handleLibraryPreview = useCallback(
    (req: {
      docId: string;
      filename: string;
      ext: string;
      source: Promise<Blob | undefined>;
    }) => {
      // Chaque ouverture reçoit un jeton. Sans lui, ouvrir A puis B pendant que
      // A charge encore laisse la réponse de A — arrivée en dernier — écraser
      // le document B affiché à l'écran.
      const token = ++openToken.current;
      const fresh = () => openToken.current === token;

      stream.reset();
      setTranslatedBlob(null);
      setSelectedFile(null);
      setTranslatedFilename(req.filename);
      // Caches d'aperçu REMIS À ZÉRO : ils appartiennent à l'ouverture
      // précédente. Les garder servirait les pages d'une traduction
      // potentiellement retraduite depuis.
      pageBlobCache.current.clear();
      fullDocFor.current = null;
      enSuivi.current = false;
      echecsSuivi.current = 0;
      setLibraryDocId(req.docId);
      setCurrentPage(1);
      setPreviewLoading(true);
      setShowLibrary(false);
      setShowPreview(true);   // ← l'aperçu est visible AVANT le premier octet

      // La source arrive DÉJÀ CONVERTIE en PDF (`?as=pdf`) : le panneau gauche
      // s'affiche sans un seul appel de plus. Elle était servie au format natif,
      // et le viewer la retournait au serveur pour conversion — un aller-retour
      // de plusieurs mégaoctets qui, en échouant, laissait le panneau blanc.
      setSourceIsPdf(true);
      req.source.then((src) => {
        if (fresh() && src) {
          setSelectedFile(new File([src], req.filename, { type: 'application/pdf' }));
        }
      }).catch(() => { /* panneau gauche vide : le viewer le gère */ });

      // La traduction est chargée par l'effet ci-dessous, page par page.
    },
    [stream],
  );

  // ---- Retry : relance la traduction d'un document en erreur ----------------
  const handleRetry = useCallback(
    async (doc: import('../hooks/useDocumentLibrary').DocMeta) => {
      const blob = await getOriginalBlob(doc.id);
      if (!blob) return;
      const file = new File([blob], doc.originalName);
      deleteDocument(doc.id);
      handleStartTranslate({
        file,
        targetLang: doc.targetLang,
        pages: '',
        debug: false,
        precise: false,
      });
    },
    [getOriginalBlob, deleteDocument, handleStartTranslate],
  );

  // ---- Aperçu bibliothèque : réseau seulement quand on ne SAIT pas ---------
  //
  // Trois niveaux, du plus rapide au plus lent, et on s'arrête au premier :
  //   1. le serveur a déjà envoyé le rendu COMPLET (`X-Render: full`) →
  //      navigation 100 % locale, plus une seule requête ;
  //   2. la page a déjà été visitée → blob repris du cache mémoire, instantané
  //      (revenir sur une page relançait ~10 s de reconstruction serveur) ;
  //   3. sinon seulement, on demande la page au serveur.
  useEffect(() => {
    if (!libraryDocId || !showPreview) {
      // Plus d'aperçu de bibliothèque : le suivi en cours n'a plus d'objet.
      enSuivi.current = false; echecsSuivi.current = 0;
      return;
    }
    if (fullDocFor.current === libraryDocId) return;          // niveau 1
    const key = `${libraryDocId}:${currentPage}`;
    const hit = pageBlobCache.current.get(key);
    if (hit) {                                                    // niveau 2
      setTranslatedBlob(hit); setTranslatedIsPdf(true);
      setPreviewLoading(false); return;
    }

    const token = ++pageToken.current;
    let relance: ReturnType<typeof setTimeout> | undefined;
    if (!enSuivi.current) setPreviewLoading(true);
    const echec = () => {
      if (pageToken.current !== token) return;
      if (enSuivi.current && echecsSuivi.current < 3) {
        echecsSuivi.current += 1;
        relance = setTimeout(() => setPreviewPoll((n) => n + 1), 3000);
      } else {
        enSuivi.current = false;
        echecsSuivi.current = 0;
      }
    };
    getPreviewBlob(libraryDocId, currentPage)
      .then((res) => {
        // Jeton : tourner vite les pages lance plusieurs requêtes, et rien ne
        // garantit qu'elles reviennent dans l'ordre. Sans lui, une page lente
        // demandée avant écrase la page rapide demandée après.
        if (pageToken.current !== token) return;
        if (!res) { echec(); return; }
        echecsSuivi.current = 0;
        enSuivi.current = res.inProgress;
        if (res.inProgress) {
          // Document rouvert PENDANT sa traduction : on montre ce qui est
          // traduit (rien en cache, ça change) et on redemande jusqu'au rendu
          // final — sans quoi l'aperçu restait figé sur le premier état.
          if (res.blob) { setTranslatedBlob(res.blob); setTranslatedIsPdf(true); }
          relance = setTimeout(() => setPreviewPoll((n) => n + 1), 3000);
          return;
        }
        if (!res.blob) return;
        if (res.full) {
          // Tout le document est là : les blobs par page n'ont plus d'objet.
          fullDocFor.current = libraryDocId;
          pageBlobCache.current.clear();
        } else {
          pageBlobCache.current.set(key, res.blob);
          // Borne mémoire : ~3 Mo par blob, on garde les 20 dernières pages
          // visitées (FIFO — Map préserve l'ordre d'insertion).
          while (pageBlobCache.current.size > 20) {
            const oldest = pageBlobCache.current.keys().next().value as string;
            pageBlobCache.current.delete(oldest);
          }
        }
        // `/preview` renvoie TOUJOURS un PDF, quel que soit le format d'origine.
        setTranslatedBlob(res.blob);
        setTranslatedIsPdf(true);
      })
      .catch(() => { echec(); /* le lecteur garde son écran d'attente */ })
      .finally(() => { if (pageToken.current === token) setPreviewLoading(false); });
    return () => { if (relance) clearTimeout(relance); };
  }, [libraryDocId, currentPage, showPreview, getPreviewBlob, previewPoll]);

  // ---- Download (le résultat complet, une fois la traduction terminée) ----
  const handleDownload = useCallback(() => {
    const blob = stream.result?.blob ?? translatedBlob;
    if (blob && translatedFilename) {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = translatedFilename;
      document.body.appendChild(a);
      a.click();
      URL.revokeObjectURL(url);
      document.body.removeChild(a);
    }
  }, [stream.result, translatedBlob, translatedFilename]);

  // ---- Back from preview ----
  const handleBack = useCallback(() => {
    stream.cancel();
    setShowPreview(false);
    setPreviewFocus(false);         // on quitte aussi le mode agrandi
    setLibraryDocId(null);          // plus d'aperçu ouvert : plus rien à charger
    setPreviewLoading(false);
  }, [stream]);

  // « Débloquer » depuis l'essai : les tarifs sont sous la surcouche d'aperçu,
  // il faut la fermer pour les montrer.
  const handleUnlock = useCallback(() => {
    retourVers.current = 'pricing';
    handleBack();
  }, [handleBack]);

  // Aperçu du panneau « traduit » : blob final si dispo, sinon PDF partiel
  // (pages déjà prêtes), mis à jour au fil de l'eau pendant le streaming.
  const previewTranslatedBlob = translatedBlob ?? stream.partialBlob;
  // Le partiel du flux est TOUJOURS un PDF (le backend le convertit avant de
  // le servir), y compris pour un PPTX.
  const previewTranslatedIsPdf = translatedBlob ? translatedIsPdf : true;
  const effectiveNumPages = stream.totalPages ?? numPages;

  // ---- Raccourcis clavier + souris de l'aperçu (simples) ----
  // Actifs seulement quand l'aperçu est ouvert. Ignorés si l'on tape dans un
  // champ. Réutilisent les setters existants (page, zoom, focus, retour).
  useEffect(() => {
    if (!showPreview) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return;
      switch (e.key) {
        case 'ArrowLeft':  setCurrentPage((p) => Math.max(1, p - 1)); break;
        case 'ArrowRight': setCurrentPage((p) => Math.min(effectiveNumPages, p + 1)); break;
        case 'Home':       setCurrentPage(1); break;
        case 'End':        setCurrentPage(effectiveNumPages); break;
        case '+': case '=': setZoom((z) => Math.min(3, +(z + 0.25).toFixed(2))); break;
        case '-':          setZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2))); break;
        case '0':          setZoom(1); break;
        case 'f': case 'F': setPreviewFocus((v) => !v); break;
        case 'Escape':
          if (previewFocus) setPreviewFocus(false);
          else handleBack();
          break;
        default: return;
      }
    };
    // Ctrl + molette = zoom (comme un lecteur PDF). `passive:false` pour pouvoir
    // annuler le zoom natif de la page.
    const scrollEl = document.getElementById('scroll');
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      setZoom((z) => Math.min(3, Math.max(0.5,
        +(z + (e.deltaY < 0 ? 0.15 : -0.15)).toFixed(2))));
    };
    window.addEventListener('keydown', onKey);
    scrollEl?.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      window.removeEventListener('keydown', onKey);
      scrollEl?.removeEventListener('wheel', onWheel);
    };
  }, [showPreview, effectiveNumPages, previewFocus, handleBack]);

  return (
    <div className="min-h-screen bg-white text-gray-900 font-body">
      <ToastContainer />
      <SupportModal />

      {/* Paiement à la page : achat des crédits nécessaires à la traduction
          courante (402). Une fois payé, on REJOUE la traduction telle quelle. */}
      <PaymentModal
        open={payPages !== null}
        onClose={() => setPayPages(null)}
        pages={payPages ?? 1}
        label={t('payment.buy_pages_label', 'Débloquez la traduction de ce document')}
        onPaid={() => {
          const cfg = lastConfigRef.current;
          if (cfg) handleStartTranslate(cfg);
        }}
      />

      <Navbar
        activeSection={activeSection}
        onNavClick={handleNavClick}
        onLibraryOpen={() => setShowLibrary(true)}
      />

      <LanguageMarquee />

      <DocumentLibrary
        isOpen={showLibrary}
        onClose={() => setShowLibrary(false)}
        documents={documents}
        onPreview={handleLibraryPreview}
        onDelete={deleteDocument}
        onClearAll={clearAll}
        getBlob={getBlob}
        getOriginalBlob={getOriginalBlob}
        onPaid={() => { refresh(); refreshUser(); }}
        onRetry={handleRetry}
      />

      <main>
        <HeroSection />

        <FeaturesGrid />

        <StorySection
          showPreview={showPreview}
          translatedBlob={previewTranslatedBlob}
          translatedIsPdf={previewTranslatedIsPdf}
          translatedFilename={translatedFilename}
          selectedFile={selectedFile}
          sourceIsPdf={sourceIsPdf}
          currentPage={currentPage}
          numPages={effectiveNumPages}
          zoom={zoom}
          isTrialMode={isTrialMode}
          targetLang={targetLang}
          isTranslating={stream.isTranslating}
          pageStatuses={stream.pageStatuses}
          queuePosition={stream.queuePosition}
          readyPages={stream.readyPages}
          previewRendering={previewLoading}
          translationError={stream.error}
          limitReached={stream.limitReached}
          creditsNeeded={stream.creditsNeeded}
          onBuyPages={(p) => setPayPages(p)}
          focus={previewFocus}
          onStartTranslate={handleStartTranslate}
          onBack={handleBack}
          onUnlock={handleUnlock}
          onToggleFocus={toggleFocus}
          onZoomChange={setZoom}
          onPageChange={setCurrentPage}
          onDownload={handleDownload}
          onPagesLoaded={setNumPages}
          onLibraryOpen={() => setShowLibrary(true)}
        />

        <PricingSection isAnnual={isAnnual} onAnnualChange={setIsAnnual} />

        <AboutSection />
      </main>
    </div>
  );
}
