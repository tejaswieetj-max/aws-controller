import test from 'node:test';
import assert from 'node:assert/strict';
import {
  OIDC_TX_KEY,
  OIDC_TX_TTL_MS,
  exchangeAuthorizationCode,
  redirectOriginProblem,
  storeOidcTransaction,
  takeOidcTransaction,
} from '../src/oauth.mjs';

function memoryStorage(backing = new Map()) {
  return {
    get length() { return backing.size; },
    key: index => [...backing.keys()][index] ?? null,
    getItem: key => (backing.has(key) ? backing.get(key) : null),
    setItem: (key, value) => backing.set(key, String(value)),
    removeItem: key => backing.delete(key),
    clear: () => backing.clear(),
  };
}

function jwt(payload) {
  const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${encode({ alg: 'none' })}.${encode(payload)}.signature`;
}

const config = {
  domain: 'https://auth.example.com',
  clientId: 'client-123',
  redirectUri: 'http://localhost:5173/callback',
};

test('OAuth transaction is keyed by state and is single use', () => {
  const storage = memoryStorage();
  storeOidcTransaction(storage, { state: 'state-abcdefghijklmnop', verifier: 'verifier', nonce: 'nonce' });

  assert.deepEqual(takeOidcTransaction(storage, 'state-abcdefghijklmnop'), {
    verifier: 'verifier',
    nonce: 'nonce',
  });
  // A replayed callback cannot redeem the same verifier again.
  assert.equal(takeOidcTransaction(storage, 'state-abcdefghijklmnop'), null);
});

test('OAuth transaction is visible from a second tab on the same origin', () => {
  const backing = new Map();
  storeOidcTransaction(memoryStorage(backing), {
    state: 'state-abcdefghijklmnop',
    verifier: 'verifier',
    nonce: 'nonce',
  });

  // The callback runs in another tab, represented by a separate Storage facade.
  assert.deepEqual(takeOidcTransaction(memoryStorage(backing), 'state-abcdefghijklmnop'), {
    verifier: 'verifier',
    nonce: 'nonce',
  });
});

test('OAuth transaction expires after the TTL and is pruned on write', () => {
  const storage = memoryStorage();
  const now = 1_000_000;
  storeOidcTransaction(storage, {
    state: 'state-abcdefghijklmnop',
    verifier: 'verifier',
    nonce: 'nonce',
    createdAt: now,
  });

  assert.equal(takeOidcTransaction(storage, 'state-abcdefghijklmnop', now + OIDC_TX_TTL_MS + 1), null);

  const future = now + OIDC_TX_TTL_MS + 2;
  storeOidcTransaction(storage, {
    state: 'state-abcdefghijklmnop',
    verifier: 'verifier',
    nonce: 'nonce',
    createdAt: future,
  });
  storeOidcTransaction(storage, {
    state: 'state-qrstuvwxyzabcdef',
    verifier: 'other-verifier',
    nonce: 'other-nonce',
    createdAt: future + OIDC_TX_TTL_MS + 1,
  });
  const stored = JSON.parse(storage.getItem(OIDC_TX_KEY));
  assert.deepEqual(Object.keys(stored), ['state-qrstuvwxyzabcdef']);
});

test('OAuth transaction rejects an unknown or malformed state', () => {
  const storage = memoryStorage();
  storeOidcTransaction(storage, { state: 'state-abcdefghijklmnop', verifier: 'v', nonce: 'n' });

  assert.equal(takeOidcTransaction(storage, null), null);
  assert.equal(takeOidcTransaction(storage, ''), null);
  assert.equal(takeOidcTransaction(storage, 'short'), null);
  assert.equal(takeOidcTransaction(storage, 'state-not-present-xxxx'), null);
  // A rejected lookup must not consume a valid transaction.
  assert.ok(takeOidcTransaction(storage, 'state-abcdefghijklmnop'));
});

test('corrupt transaction storage degrades to no transaction', () => {
  const storage = memoryStorage();
  storage.setItem(OIDC_TX_KEY, 'not-json');
  assert.equal(takeOidcTransaction(storage, 'state-abcdefghijklmnop'), null);
  assert.doesNotThrow(() =>
    storeOidcTransaction(storage, { state: 'state-abcdefghijklmnop', verifier: 'v', nonce: 'n' }),
  );
});

test('origin preflight accepts the configured origin and explains a mismatch', () => {
  assert.equal(redirectOriginProblem('http://localhost:5173', config.redirectUri), null);

  const mismatch = redirectOriginProblem('http://127.0.0.1:5173', config.redirectUri);
  assert.match(mismatch, /http:\/\/127\.0\.0\.1:5173/);
  assert.match(mismatch, /http:\/\/localhost:5173/);

  assert.match(redirectOriginProblem('http://localhost:5173', 'not a url'), /not a valid URL/);
});

test('authorization code exchange posts PKCE fields and returns the session', async () => {
  let captured;
  const fetchImpl = async (url, init) => {
    captured = { url, init };
    return new Response(
      JSON.stringify({
        id_token: jwt({ nonce: 'expected-nonce', exp: 1_800_000_000, email: 'dev@example.com' }),
        access_token: 'access-token',
        expires_in: 3600,
      }),
      { status: 200 },
    );
  };

  const session = await exchangeAuthorizationCode({
    code: 'auth-code',
    verifier: 'code-verifier',
    expectedNonce: 'expected-nonce',
    config,
    fetchImpl,
  });

  assert.equal(String(captured.url), 'https://auth.example.com/oauth2/token');
  assert.equal(captured.init.method, 'POST');
  const body = new URLSearchParams(captured.init.body);
  assert.equal(body.get('grant_type'), 'authorization_code');
  assert.equal(body.get('client_id'), 'client-123');
  assert.equal(body.get('code'), 'auth-code');
  assert.equal(body.get('redirect_uri'), config.redirectUri);
  assert.equal(body.get('code_verifier'), 'code-verifier');

  assert.equal(session.id_token.split('.').length, 3);
  assert.equal(session.access_token, 'access-token');
  assert.equal(session.expires_at, 1_800_000_000);
  assert.equal(session.profile.email, 'dev@example.com');
});

test('authorization code exchange rejects a mismatched nonce', async () => {
  const fetchImpl = async () =>
    new Response(JSON.stringify({ id_token: jwt({ nonce: 'attacker-nonce' }), access_token: 'a' }), {
      status: 200,
    });

  await assert.rejects(
    exchangeAuthorizationCode({
      code: 'auth-code',
      verifier: 'code-verifier',
      expectedNonce: 'expected-nonce',
      config,
      fetchImpl,
    }),
    /nonce validation failed/,
  );
});

test('authorization code exchange reports token endpoint failures', async () => {
  const fetchImpl = async () =>
    new Response(JSON.stringify({ error: 'invalid_grant', error_description: 'Code expired' }), {
      status: 400,
    });

  await assert.rejects(
    exchangeAuthorizationCode({
      code: 'auth-code',
      verifier: 'code-verifier',
      expectedNonce: 'expected-nonce',
      config,
      fetchImpl,
    }),
    /Cognito token exchange failed \(400\): Code expired/,
  );
});

test('authorization code exchange rejects a malformed ID token', async () => {
  const fetchImpl = async () =>
    new Response(JSON.stringify({ id_token: 'not-a-jwt', access_token: 'a' }), { status: 200 });

  await assert.rejects(
    exchangeAuthorizationCode({
      code: 'auth-code',
      verifier: 'code-verifier',
      expectedNonce: 'expected-nonce',
      config,
      fetchImpl,
    }),
    /invalid ID token/,
  );
});
