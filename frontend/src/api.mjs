/** Typed-at-the-boundary API wrapper; server authorization remains authoritative. */
export class SpendctlApi {
  constructor(baseUrl, token) { this.baseUrl = baseUrl; this.token = token; }
  async request(path, init = {}, idempotencyKey) {
    const bearer = await this.token();
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${bearer}`);
    if (init.body) headers.set('Content-Type', 'application/json');
    if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey);
    const controller = new AbortController();
    const timeout = globalThis.setTimeout(() => controller.abort(), 15_000);
    let response;
    try {
      response = await fetch(`${this.baseUrl.replace(/\/$/, '')}${path}`, { ...init, headers, signal: controller.signal });
    } catch (cause) {
      const timedOut = cause instanceof Error && cause.name === 'AbortError';
      const detail = timedOut ? 'Request timed out' : 'Network request failed';
      const suffix = idempotencyKey ? '; the outcome may be unknown, so retry with the same idempotency key.' : '.';
      const error = new Error(`${detail}${suffix}`);
      error.pendingUnknown = Boolean(idempotencyKey);
      throw error;
    } finally { globalThis.clearTimeout(timeout); }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) globalThis.dispatchEvent?.(new Event('spendctl:unauthorized'));
      const error = new Error(body?.error?.message ?? `Request failed (${response.status})`);
      error.code = body?.error?.code;
      error.status = response.status;
      // The backend's _error envelope uses `details`; accept the singular key
      // too for older integrations so denial evidence is not dropped.
      error.detail = body?.error?.detail ?? body?.error?.details;
      error.pendingUnknown = error.code === 'PENDING_UNKNOWN' || response.status >= 500;
      throw error;
    }
    return body;
  }
  estimate(payload) { return this.request('/estimate', { method: 'POST', body: JSON.stringify(toBackendRequest(payload)) }); }
  historicalEstimate(payload) { return this.request('/calibur/historical-estimate', { method: 'POST', body: JSON.stringify({ ...toBackendRequest(payload), region: payload.region }) }); }
  submit(payload, key) { return this.request('/requests', { method: 'POST', body: JSON.stringify(toBackendRequest(payload)) }, key); }
  budget(team) { return this.request(`/budgets/${encodeURIComponent(team)}`); }
  reservations(nextToken) {
    const query = nextToken ? `?nextToken=${encodeURIComponent(nextToken)}` : '';
    return this.request(`/reservations${query}`);
  }
  requestById(id) { return this.request(`/requests/${encodeURIComponent(id)}`); }
  cancel(id) { return this.request(`/requests/${encodeURIComponent(id)}/cancel`, { method: 'POST' }); }
  updateBudget(team, budget_usd) { return this.request(`/budgets/${encodeURIComponent(team)}`, { method: 'PUT', body: JSON.stringify({ budget_usd }) }); }
  calibration() { return this.request('/calibration/summary'); }
  stateEstimate() { return this.request('/calibur/state-estimate'); }
  tauState(jobId) { return this.request(`/calibur/jobs/${encodeURIComponent(jobId)}/early-updates`); }
  evidenceChain(requestId) { return this.request(`/requests/${encodeURIComponent(requestId)}/evidence-chain`); }
  releasePlan() { return this.request('/calibur/release-plan'); }
  telemetryTrace(resourceId) { return this.request(`/calibur/resources/${encodeURIComponent(resourceId)}/telemetry`); }
  alerts(nextToken) {
    const query = nextToken ? `?nextToken=${encodeURIComponent(nextToken)}` : '';
    return this.request(`/alerts${query}`);
  }
}
function toBackendRequest(payload) {
  return {
    team: payload.team,
    resource: { type: 'ec2', instance_type: payload.instance_type, ebs_gb: payload.ebs_gb },
    duration_hours: payload.duration_hours,
    purpose: payload.purpose,
  };
}
export function formatUsd(micro) {
  if (micro == null || !Number.isFinite(micro)) return '—';
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(micro / 1_000_000);
}

/** Preserve sub-cent precision for estimates and ledger deltas. */
export function formatUsdPrecise(micro) {
  if (micro == null || !Number.isFinite(micro)) return '—';
  const value = micro / 1_000_000;
  const maximumFractionDigits = Math.abs(value) < 1 ? 6 : 2;
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits,
  }).format(value);
}

/** Convert a validated USD input string to integer micro-USD. */
export function usdToMicro(value) {
  const text = String(value).trim();
  if (!/^\d+(\.\d{1,6})?$/.test(text)) throw new Error('Enter a positive USD amount with up to 6 decimals.');
  const [whole, fraction = ''] = text.split('.');
  return Number(whole) * 1_000_000 + Number(fraction.padEnd(6, '0'));
}
