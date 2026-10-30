export const OIDC_TX_KEY: string;
export const OIDC_TX_TTL_MS: number;

export type OidcTransaction = {
  state: string;
  verifier: string;
  nonce: string;
  createdAt?: number;
};

export type OAuthSession = {
  id_token: string;
  access_token: string;
  profile: Record<string, unknown>;
  expires_at: number;
};

export type OAuthConfig = {
  domain: string;
  clientId: string;
  redirectUri: string;
};

export function storeOidcTransaction(storage: Storage, transaction: OidcTransaction): void;
export function takeOidcTransaction(
  storage: Storage,
  state: string | null,
  now?: number,
): { verifier: string; nonce: string } | null;
export function redirectOriginProblem(
  currentOrigin: string,
  redirectUri: string,
): string | null;
export function exchangeAuthorizationCode(input: {
  code: string;
  verifier: string;
  expectedNonce: string;
  config: OAuthConfig;
  fetchImpl?: typeof fetch;
  now?: () => number;
}): Promise<OAuthSession>;
