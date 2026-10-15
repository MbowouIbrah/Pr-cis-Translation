import {
  createContext, useContext, useState, useEffect, useCallback,
  type ReactNode,
} from 'react';
import api, { setTokens, onTokensRefreshed, onForceLogout } from '../services/api';
import i18n from '../i18n';

export interface AuthUser {
  id: string;
  email: string;
  name: string | null;
  email_verified: boolean;
  avatar_url: string | null;
  plan: string;
  /** Fin de l'abonnement acheté (ISO) ; null = sans échéance. */
  plan_expires_at?: string | null;
  storage_used: number;
  storage_limit: number;
}

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string) => Promise<{ ok: boolean; error?: string }>;
  register: (email: string, name?: string) => Promise<{ ok: boolean; error?: string }>;
  verifyCode: (email: string, code: string) => Promise<{ ok: boolean; error?: string }>;
  resendVerification: (email: string) => Promise<void>;
  loginPassword: (email: string, password: string) => Promise<{ ok: boolean; error?: string }>;
  registerPassword: (email: string, password: string, name?: string) => Promise<{ ok: boolean; error?: string }>;
  forgotPassword: (email: string) => Promise<void>;
  resetPassword: (email: string, code: string, password: string) => Promise<{ ok: boolean; error?: string }>;
  googleAuth: (credential: string) => Promise<{ ok: boolean; error?: string }>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

const TOKENS_KEY = 'precis_tokens';

function loadTokens(): { access: string; refresh: string } | null {
  try {
    const raw = localStorage.getItem(TOKENS_KEY);
    if (!raw) return null;
    const { access_token, refresh_token } = JSON.parse(raw);
    if (access_token && refresh_token) return { access: access_token, refresh: refresh_token };
  } catch { /* ignoré */ }
  return null;
}

function saveTokens(access: string, refresh: string) {
  localStorage.setItem(TOKENS_KEY, JSON.stringify({ access_token: access, refresh_token: refresh }));
}

function clearTokens() {
  localStorage.removeItem(TOKENS_KEY);
  setTokens(null, null);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Restaurer la session au montage
  useEffect(() => {
    onTokensRefreshed((access, refresh) => saveTokens(access, refresh));
    onForceLogout(() => { clearTokens(); setUser(null); });

    (async () => {
      const tokens = loadTokens();
      if (!tokens) { setLoading(false); return; }
      setTokens(tokens.access, tokens.refresh);
      const res = await api.get('/api/auth/me');
      if (res.ok) {
        setUser(res.data as AuthUser);
      } else {
        clearTokens();
      }
      setLoading(false);
    })();
  }, []);

  const login = useCallback(async (email: string) => {
    const res = await api.post('/api/auth/login', { email });
    if (!res.ok) return { ok: false, error: (res.data as any)?.detail || i18n.t('auth.err_generic') };
    return { ok: true }; // code envoyé, pas encore connecté
  }, []);

  const register = useCallback(async (email: string, name?: string) => {
    const res = await api.post('/api/auth/register', { email, name });
    if (!res.ok) return { ok: false, error: (res.data as any)?.detail || i18n.t('auth.err_generic') };
    return { ok: true }; // code envoyé
  }, []);

  const verifyCode = useCallback(async (email: string, code: string) => {
    const res = await api.post('/api/auth/verify-email', { email, code });
    if (!res.ok) return { ok: false, error: (res.data as any)?.detail || i18n.t('auth.err_code_invalid') };
    const d = res.data as any;
    saveTokens(d.access_token, d.refresh_token);
    setTokens(d.access_token, d.refresh_token);
    setUser(d.user);
    return { ok: true };
  }, []);

  const resendVerification = useCallback(async (email: string) => {
    await api.post('/api/auth/resend-verification', { email });
  }, []);

  /** Ouvre la session immédiatement — aucun aller-retour par la boîte mail. */
  const loginPassword = useCallback(async (email: string, password: string) => {
    const res = await api.post('/api/auth/login-password', { email, password });
    if (!res.ok) return { ok: false, error: (res.data as any)?.detail || i18n.t('auth.err_credentials') };
    const d = res.data as any;
    saveTokens(d.access_token, d.refresh_token);
    setTokens(d.access_token, d.refresh_token);
    setUser(d.user);
    return { ok: true };
  }, []);

  /** Inscription avec mot de passe : l'email reste à vérifier par code. */
  const registerPassword = useCallback(async (email: string, password: string, name?: string) => {
    const res = await api.post('/api/auth/register-password', { email, password, name });
    if (!res.ok) return { ok: false, error: (res.data as any)?.detail || i18n.t('auth.err_generic') };
    return { ok: true }; // code envoyé
  }, []);

  const forgotPassword = useCallback(async (email: string) => {
    await api.post('/api/auth/forgot-password', { email });
  }, []);

  /** Nouveau mot de passe contre un code reçu par email → session ouverte. */
  const resetPassword = useCallback(async (email: string, code: string, password: string) => {
    const res = await api.post('/api/auth/reset-password', { email, code, password });
    if (!res.ok) return { ok: false, error: (res.data as any)?.detail || i18n.t('auth.err_code_invalid') };
    const d = res.data as any;
    saveTokens(d.access_token, d.refresh_token);
    setTokens(d.access_token, d.refresh_token);
    setUser(d.user);
    return { ok: true };
  }, []);

  const googleAuth = useCallback(async (credential: string) => {
    const res = await api.post('/api/auth/google', { credential });
    if (!res.ok) return { ok: false, error: (res.data as any)?.detail || i18n.t('auth.err_google') };
    const d = res.data as any;
    saveTokens(d.access_token, d.refresh_token);
    setTokens(d.access_token, d.refresh_token);
    setUser(d.user);
    return { ok: true };
  }, []);

  const logout = useCallback(async () => {
    try { await api.post('/api/auth/logout'); } catch { /* ignoré */ }
    clearTokens();
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    const res = await api.get('/api/auth/me');
    if (res.ok) setUser(res.data as AuthUser);
  }, []);

  return (
    <AuthContext.Provider value={{
      user, loading, login, register, verifyCode, resendVerification,
      loginPassword, registerPassword, forgotPassword, resetPassword,
      googleAuth, logout, refreshUser,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
