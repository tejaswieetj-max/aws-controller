/**
 * Browser-only OAuth/PKCE helpers.
 *
 * The authorization transaction (state, verifier, nonce) is kept in
 * localStorage keyed by state. sessionStorage is per-tab and is lost when the
 * Cognito hosted UI returns in a different tab; localStorage is shared by the
 * origin and lets a callback find the transaction that started it. The signed
 * session itself stays in sessionStorage.
 */

export const OIDC_TX_KEY = 'spendctl_oidc_transactions';
export const OIDC_TX_TTL_MS = 15 * 60 * 1000;
const STATE_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;

export function storeOidcTransaction(storage, { state, verifier, nonce, createdAt = Date.now() }) {
  if (!STATE_PATTERN.test(String(state ?? ''))) throw new Error('Sign-in state is invalid.');
  if (typeof verifier !== 'string' || !verifier) throw new Error('Sign-in verifier is missing.');
  if (typeof nonce !== 'string' || !nonce) throw new Error('Sign-in nonce is missing.');
  const transactions = readTransactions(storage);
  for (const [key, value] of Object.entries(transactions)) {
    if (!isValidTransaction(value, createdAt)) delete transactions[key];
  }
  transactions[state] = { verifier, nonce, createdAt };
  storage.setItem(OIDC_TX_KEY, JSON.stringify(transactions));
}

/**
 * Removes and returns the transaction for the returned state. A transaction is
 * single-use: a replayed callback cannot redeem the same verifier again.
 */
export function takeOidcTransaction(storage, state, now = Date.now()) {
  if (!STATE_PATTERN.test(String(state ?? ''))) return null;
  const transactions = readTransactions(storage);
  if (!Object.prototype.hasOwnProperty.call(transactions, state)) return null;
  const value = transactions[state];
  delete transactions[state];
  storage.setItem(OIDC_TX_KEY, JSON.stringify(transactions));
  if (!isValidTransaction(value, now)) return null;
  return { verifier: value.verifier, nonce: value.nonce };
}

/**
 * Returns an actionable message when the page origin and the configured
 * Cognito redirect origin differ. sessionStorage/localStorage are per-origin,
 * so a callback on another origin can never see the PKCE transaction.
 */
export function redirectOriginProblem(currentOrigin, redirectUri) {
  let target;
  try {
    target = new URL(redirectUri);
  } catch {
    return `VITE_COGNITO_REDIRECT_URI is not a valid URL: ${String(redirectUri)}`;
  }
  if (target.origin === currentOrigin) return null;
  return `This page is running at ${currentOrigin}, but the Cognito redirect URI is ${target.origin}. Open the app at ${target.origin} and sign in again, or update VITE_COGNITO_REDIRECT_URI and the Cognito app client callback URL to match.`;
}

export async function exchangeAuthorizationCode({
  code,
  verifier,
  expectedNonce,
  config,
  fetchImpl = globalThis.fetch,
  now = Date.now,
}) {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: config.clientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier,
  });
  const response = await fetchImpl(new URL('/oauth2/token', config.domain), {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!response.ok) {
    let detail = '';
    try {
      const payload = await response.clone().json();
      detail = payload?.error_description || payload?.error || '';
    } catch {
      // A non-JSON error body still leaves the HTTP status as useful evidence.
    }
    throw new Error(
      `Cognito token exchange failed (${response.status})${detail ? `: ${detail}` : '.'}`,
    );
  }
  const token = await response.json();
  const payload = decodeJwt(token.id_token);
  if (payload.nonce !== expectedNonce) {
    throw new Error('Cognito ID token nonce validation failed. Sign in again.');
  }
  return {
    id_token: token.id_token,
    access_token: token.access_token,
    profile: payload,
    expires_at: Number(payload.exp ?? Math.floor(now() / 1000) + Number(token.expires_in ?? 3600)),
  };
}

function readTransactions(storage) {
  try {
    const parsed = JSON.parse(storage.getItem(OIDC_TX_KEY) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return Object.create(null);
    }
    return Object.assign(Object.create(null), parsed);
  } catch {
    return Object.create(null);
  }
}

function isValidTransaction(value, now) {
  return Boolean(
    value &&
      typeof value.verifier === 'string' &&
      value.verifier &&
      typeof value.nonce === 'string' &&
      value.nonce &&
      Number.isFinite(value.createdAt) &&
      now - value.createdAt <= OIDC_TX_TTL_MS,
  );
}

function decodeJwt(token) {
  const part = String(token ?? '').split('.')[1];
  if (!part) throw new Error('Cognito returned an invalid ID token.');
  const base64 = part.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, '=');
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}
