import { useEffect, useState } from 'react';
import {
  exchangeAuthorizationCode,
  redirectOriginProblem,
  storeOidcTransaction,
  takeOidcTransaction,
} from './oauth.mjs';
import type { OAuthSession } from './oauth.mjs';

type Session = OAuthSession;
const SESSION_KEY = 'spendctl_cognito_session';

export function useSpendctlAuth() {
  const isConfigured = Boolean(
    import.meta.env.VITE_COGNITO_HOSTED_UI_DOMAIN && import.meta.env.VITE_COGNITO_CLIENT_ID,
  );
  const [user, setUser] = useState<Session | null>(() => readSession());
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let mounted = true;
    const url = new URL(window.location.href);
    const authorizationError = url.searchParams.get('error');
    const code = url.searchParams.get('code');
    const returnedState = url.searchParams.get('state');

    // If we already have a valid session, skip callback processing entirely.
    // This makes the OAuth callback idempotent under React.StrictMode double-mount
    // and page reloads: the first mount redeems the code and stores the session;
    // subsequent mounts find the session and skip redemption so state validation
    // cannot fail on a state value already consumed by the first mount.
    const existing = readSession();
    if (existing && existing.expires_at * 1000 > Date.now()) {
      if (!mounted) return;
      setUser(existing);
      setLoading(false);
      clearCallbackQuery();
      return () => { mounted = false; };
    }

    if (authorizationError) {
      setError(new Error(url.searchParams.get('error_description') || authorizationError));
      clearCallbackQuery();
      setLoading(false);
    } else if (code) {
      void redeemCode(code, returnedState).then(session => {
        // Persist before the mounted check: a StrictMode remount must still find
        // the redeemed session even if this effect instance was torn down.
        sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
        if (!mounted) return;
        setError(null);
        setUser(session);
      }).catch(cause => mounted && setError(cause instanceof Error ? cause : new Error('Cognito sign-in could not be completed')))
        .finally(() => {
          // Never leave code/state in the address bar: a reload would replay a
          // consumed authorization code.
          clearCallbackQuery();
          if (mounted) setLoading(false);
        });
    } else {
      const session = readSession();
      if (session && session.expires_at * 1000 <= Date.now()) {
        sessionStorage.removeItem(SESSION_KEY);
        setUser(null);
      } else setUser(session);
      setLoading(false);
    }
    return () => { mounted = false; };
  }, []);

  const removeUser = async () => { sessionStorage.removeItem(SESSION_KEY); setUser(null); };
  const signinRedirect = async () => {
    try {
      setError(null);
      const { domain, clientId, redirectUri } = config();
      // A cross-origin redirect can never see this origin's stored PKCE
      // transaction, so fail now with an actionable message instead of
      // redirecting into an opaque callback-state error.
      const problem = redirectOriginProblem(window.location.origin, redirectUri);
      if (problem) throw new Error(problem);
      const verifier = randomText(48);
      const state = randomText(24);
      const nonce = randomText(24);
      storeOidcTransaction(localStorage, { state, verifier, nonce });
      const challenge = base64Url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
      const authorize = new URL('/oauth2/authorize', domain);
      Object.entries({ client_id: clientId, response_type: 'code', scope: 'openid email profile',
        redirect_uri: redirectUri, code_challenge_method: 'S256', code_challenge: challenge, state, nonce })
        .forEach(([key, value]) => authorize.searchParams.set(key, value));
      window.location.assign(authorize.toString());
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error('Sign-in could not be started.'));
    }
  };
  const signoutRedirect = async () => {
    const { domain, clientId } = config();
    sessionStorage.removeItem(SESSION_KEY);
    setUser(null);
    const logout = new URL('/logout', domain);
    logout.searchParams.set('client_id', clientId);
    logout.searchParams.set('logout_uri', import.meta.env.VITE_COGNITO_LOGOUT_URI || window.location.origin);
    window.location.assign(logout.toString());
  };

  return { user, isLoading, isAuthenticated: Boolean(user), activeNavigator: false,
    isConfigured, error, signinRedirect, signoutRedirect, removeUser };
}

let redeemInFlight: Promise<Session> | null = null;

async function redeemCode(code: string, returnedState: string | null): Promise<Session> {
  // Return the in-flight redemption if StrictMode double-mount triggers concurrent calls.
  if (redeemInFlight) return redeemInFlight;

  redeemInFlight = (async () => {
    // If we already have a valid session (from a prior mount or page reload), return it.
    const existing = readSession();
    if (existing && existing.expires_at * 1000 > Date.now()) {
      return existing;
    }

    const transaction = takeOidcTransaction(localStorage, returnedState);
    if (!transaction) {
      const { redirectUri } = config();
      const problem = redirectOriginProblem(window.location.origin, redirectUri);
      if (problem) {
        throw new Error(`Cognito callback state validation failed. ${problem}`);
      }
      throw new Error(
        'Cognito callback state validation failed. The sign-in attempt expired or was started in another browser. Sign in again.',
      );
    }
    return exchangeAuthorizationCode({
      code,
      verifier: transaction.verifier,
      expectedNonce: transaction.nonce,
      config: config(),
    });
  })();

  try {
    return await redeemInFlight;
  } finally {
    redeemInFlight = null;
  }
}

function config() {
  const domain = import.meta.env.VITE_COGNITO_HOSTED_UI_DOMAIN;
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID;
  const redirectUri = import.meta.env.VITE_COGNITO_REDIRECT_URI || `${window.location.origin}/callback`;
  if (!domain || !clientId) throw new Error('Set VITE_COGNITO_HOSTED_UI_DOMAIN and VITE_COGNITO_CLIENT_ID in frontend/.env.local.');
  return { domain: domain.replace(/\/$/, ''), clientId, redirectUri };
}
function clearCallbackQuery() {
  const url = new URL(window.location.href);
  if (!url.search) return;
  url.search = '';
  window.history.replaceState({}, document.title, `${url.pathname}${url.hash}`);
}
function readSession(): Session | null { try { const raw = sessionStorage.getItem(SESSION_KEY); return raw ? JSON.parse(raw) as Session : null; } catch { return null; } }
function randomText(length: number) { const bytes = crypto.getRandomValues(new Uint8Array(length)); return base64Url(bytes); }
function base64Url(bytes: Uint8Array) { return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
