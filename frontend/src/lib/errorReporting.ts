/**
 * Remontée des erreurs de l'INTERFACE vers le journal central du backend.
 *
 * Tout ce qui casse côté navigateur — erreur JS non rattrapée, promesse rejetée,
 * appel réseau en échec, rendu React qui plante — finit ici, puis dans
 * `POST /api/logs/client`. L'admin le retrouve dans `/admin/logs`, à côté des
 * erreurs backend.
 *
 * TROIS PRÉCAUTIONS
 *  1. On n'appelle JAMAIS le client `api` (qui reporterait ses propres échecs) :
 *     un `fetch` direct, pour ne pas boucler.
 *  2. Dédup/throttle local : une même erreur qui se répète en boucle (rendu) ne
 *     doit pas marteler le réseau ni noyer le journal.
 *  3. L'échec du report lui-même est avalé en silence — sinon on rapporterait
 *     l'erreur de rapport d'erreur, à l'infini.
 */
import i18n from '../i18n';
import { authHeader } from '../services/api';

const API_BASE = import.meta.env.VITE_API_BASE || '';

// Signature → dernier envoi (ms). Une même erreur n'est renvoyée qu'une fois par
// fenêtre, pour absorber les rafales.
const _lastSent = new Map<string, number>();
const THROTTLE_MS = 10_000;

interface ReportOptions {
  stack?: string;
  level?: 'error' | 'warning' | 'critical';
  url?: string;
  component?: string;
  context?: Record<string, unknown>;
}

// Avertissements du navigateur qui ne sont pas des pannes : « ResizeObserver
// loop… » signale qu'un rappel de redimensionnement a été reporté d'une image,
// sans effet visible. Les remonter noyait le journal (40 sur 50 erreurs).
const BENIGN = [/^ResizeObserver loop/];

function shouldSend(signature: string): boolean {
  const now = Date.now();
  const last = _lastSent.get(signature) ?? 0;
  if (now - last < THROTTLE_MS) return false;
  _lastSent.set(signature, now);
  // Bornage mémoire : on ne garde pas un historique infini de signatures.
  if (_lastSent.size > 200) {
    const oldest = _lastSent.keys().next().value as string | undefined;
    if (oldest) _lastSent.delete(oldest);
  }
  return true;
}

export function reportClientError(message: string, opts: ReportOptions = {}): void {
  try {
    const msg = String(message || 'Erreur inconnue').slice(0, 2000);
    if (BENIGN.some((re) => re.test(msg))) return;
    const signature = `${msg}|${opts.component || opts.url || ''}`;
    if (!shouldSend(signature)) return;

    fetch(`${API_BASE}/api/logs/client`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept-Language': i18n?.language || 'fr',
        ...authHeader(),
      },
      body: JSON.stringify({
        message: msg,
        stack: opts.stack ? String(opts.stack).slice(0, 16000) : undefined,
        level: opts.level || 'error',
        url: opts.url ?? (typeof location !== 'undefined' ? location.href : undefined),
        component: opts.component,
        context: opts.context,
      }),
      // `keepalive` : le report part même si la page se ferme dans la foulée.
      keepalive: true,
    }).catch(() => { /* un report qui échoue ne fait pas plus de bruit */ });
  } catch { /* jamais d'erreur depuis le rapporteur d'erreurs */ }
}

let _installed = false;

/** Branche les capteurs globaux du navigateur. Idempotent. */
export function installGlobalErrorReporting(): void {
  if (_installed || typeof window === 'undefined') return;
  _installed = true;

  window.addEventListener('error', (e: ErrorEvent) => {
    reportClientError(e.message || 'window.onerror', {
      stack: e.error?.stack,
      url: e.filename,
      component: 'window.onerror',
      context: { line: e.lineno, col: e.colno },
    });
  });

  window.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => {
    const r = e.reason;
    reportClientError(
      (r && (r.message || String(r))) || 'unhandledrejection',
      { stack: r?.stack, component: 'unhandledrejection' },
    );
  });
}
