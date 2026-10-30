import test from 'node:test';
import assert from 'node:assert/strict';
import { SpendctlApi, formatUsd, formatUsdPrecise, usdToMicro } from '../src/api.mjs';
import { prepareSubmission } from '../src/idempotency.mjs';

test('API sends ID token and stable request idempotency key', async () => {
  const original = globalThis.fetch;
  let captured;
  globalThis.fetch = async (_url, init) => { captured = init; return new Response('{"reservation_id":"rsv-1"}', { status: 202 }); };
  try {
    const api = new SpendctlApi('https://api.example/v1', async () => 'signed-id-token');
    await api.submit({ team: 'team-a' }, 'stable-key');
    const headers = new Headers(captured.headers);
    assert.equal(headers.get('Authorization'), 'Bearer signed-id-token');
    assert.equal(headers.get('Idempotency-Key'), 'stable-key');
  } finally { globalThis.fetch = original; }
});

test('CALIBUR read endpoints send the authenticated token and encode IDs', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url, headers: new Headers(init.headers) }); return new Response('{}', { status: 200 }); };
  try {
    const api = new SpendctlApi('https://api.example/v1', async () => 'verified-token');
    await api.stateEstimate();
    await api.evidenceChain('rsv/a');
    assert.match(calls[0].url, /\/calibur\/state-estimate$/);
    assert.match(calls[1].url, /\/requests\/rsv%2Fa\/evidence-chain$/);
    assert.equal(calls[1].headers.get('Authorization'), 'Bearer verified-token');
  } finally { globalThis.fetch = original; }
});

test('historical estimate is an explicit authenticated mode with region in its request', async () => {
  const original = globalThis.fetch;
  let call;
  globalThis.fetch = async (url, init) => { call = { url, init }; return new Response('{"mode":"HISTORICAL_ESTIMATE"}', { status: 200 }); };
  try {
    const api = new SpendctlApi('https://api.example/v1', async () => 'verified-token');
    await api.historicalEstimate({ team: 'team-a', instance_type: 't3.micro', region: 'ap-south-1', ebs_gb: 8, duration_hours: 2, purpose: 'qa' });
    assert.match(call.url, /\/calibur\/historical-estimate$/);
    assert.equal(JSON.parse(call.init.body).region, 'ap-south-1');
    assert.equal(new Headers(call.init.headers).get('Authorization'), 'Bearer verified-token');
  } finally { globalThis.fetch = original; }
});

test('admission and estimate payloads match the backend resource request schema', async () => {
  const original = globalThis.fetch;
  const captured = [];
  globalThis.fetch = async (_url, init) => {
    captured.push(JSON.parse(init.body));
    return new Response('{}', { status: 200 });
  };
  try {
    const api = new SpendctlApi('https://api.example/v1', async () => 'signed-id-token');
    const payload = { team: 'team-a', instance_type: 't3.small', region: 'ignored-by-handler', ebs_gb: 8, duration_hours: 3, purpose: 'qa' };
    await api.estimate(payload);
    await api.submit(payload, 'stable-key');
    const expected = { team: 'team-a', resource: { type: 'ec2', instance_type: 't3.small', ebs_gb: 8 }, duration_hours: 3, purpose: 'qa' };
    assert.deepEqual(captured, [expected, expected]);
  } finally { globalThis.fetch = original; }
});

test('network error from a keyed request is explicitly ambiguous', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError('offline'); };
  try {
    const api = new SpendctlApi('https://api.example/v1', async () => 'token');
    await assert.rejects(api.submit({ team: 'team-a' }, 'same-key'), error => error.pendingUnknown === true);
  } finally { globalThis.fetch = original; }
});

test('401 API responses trigger local session clearing and return contract error', async () => {
  const originalFetch = globalThis.fetch;
  const originalDispatch = globalThis.dispatchEvent;
  let event;
  globalThis.dispatchEvent = value => { event = value; return true; };
  globalThis.fetch = async () => new Response('{"error":{"code":"UNAUTHENTICATED","message":"expired"}}', { status: 401 });
  try {
    const api = new SpendctlApi('https://api.example/v1', async () => 'expired');
    await assert.rejects(api.budget('team'), error => error.code === 'UNAUTHENTICATED' && error.status === 401);
    assert.equal(event.type, 'spendctl:unauthorized');
  } finally { globalThis.fetch = originalFetch; globalThis.dispatchEvent = originalDispatch; }
});

test('CALIBUR denial preserves server decision evidence for the UI', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: {
    code: 'CALIBUR_POLICY_DENIED',
    message: 'CALIBUR denied admission',
    details: { calibur_proposal: { decision: 'DENY', reason_codes: ['UPPER_BOUND_MISSING'] } },
  } }), { status: 409 });
  try {
    const api = new SpendctlApi('https://api.example/v1', async () => 'token');
    await assert.rejects(
      api.submit({ team: 'team-a' }, 'same-key'),
      error => error.code === 'CALIBUR_POLICY_DENIED'
        && error.detail.calibur_proposal.reason_codes[0] === 'UPPER_BOUND_MISSING',
    );
  } finally { globalThis.fetch = original; }
});

test('micro-USD formatting is safe for empty values', () => {
  assert.equal(formatUsd(1_234_567), '$1.23');
  assert.equal(formatUsd(undefined), '—');
});

test('same logical request reuses its idempotency key and changed input cannot overwrite unknown request', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const payload = { team: 'team-a', purpose: 'qa' };
  const first = prepareSubmission(storage, payload, () => 'stable-1');
  const replay = prepareSubmission(storage, payload, () => 'must-not-be-used');
  assert.equal(replay.key, first.key);
  assert.throws(() => prepareSubmission(storage, { ...payload, purpose: 'new' }, () => 'stable-2'), error => error.pendingUnknown === true);
  assert.equal(JSON.parse(values.get('spendctl_pending_submission')).key, 'stable-1');
});

test('precise micro-USD formatting keeps sub-cent amounts and rejects missing values', () => {
  assert.equal(formatUsdPrecise(1_234_567), '$1.23');
  assert.equal(formatUsdPrecise(1_200_000), '$1.20');
  assert.equal(formatUsdPrecise(1_234), '$0.001234');
  assert.equal(formatUsdPrecise(0), '$0.00');
  assert.equal(formatUsdPrecise(undefined), '—');
  assert.equal(formatUsdPrecise(Number.NaN), '—');
});

test('usdToMicro parses validated USD input into integer micro-USD', () => {
  assert.equal(usdToMicro('250'), 250_000_000);
  assert.equal(usdToMicro('1.5'), 1_500_000);
  assert.equal(usdToMicro('0.000001'), 1);
  assert.throws(() => usdToMicro('-1'), /positive USD amount/);
  assert.throws(() => usdToMicro('1.1234567'), /positive USD amount/);
  assert.throws(() => usdToMicro('abc'), /positive USD amount/);
});
