import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import type { AdmissionRequestPayload, SpendctlRepository } from '../api/repository';
import { SubmissionAdapter, type RequestLifecycleStatus, lifecycleLabel, lifecycleTone, nextLifecycleStatus } from '../api/submission-adapter';
import { formatUsd, formatUsdPrecise } from '../api.mjs';
import type { Budget, Estimate, HistoricalEstimate, Reservation } from '../types';
import { loadCaliburMetrics, computeReservationAmount } from '../lib/calibur-data';
import { Icon } from '../components/Icon';
import {
  CodeChip,
  Divider,
  EmptyState,
  InlineNotice,
  KeyValue,
  Money,
  Panel,
  Provenance,
  StatusBadge,
} from '../components/ui';

type Denial = { message: string; codes: string[]; limitations: string[] };

const LIFECYCLE_ORDER: RequestLifecycleStatus[] = [
  'DRAFT', 'SUBMITTING', 'ADMITTED', 'PROVISIONING', 'ACTIVE', 'SETTLING', 'SETTLED',
];

export function EstimatePage({
  repository,
  team,
  budget,
  isAdmin,
  synthetic,
  mockEngine,
  onSubmitted,
  onNotice,
}: {
  repository: SpendctlRepository;
  team: string;
  budget: Budget | null;
  isAdmin: boolean;
  synthetic: boolean;
  mockEngine: boolean;
  onSubmitted: (reservation: Reservation) => void;
  onNotice: (message: string, tone?: 'info' | 'success' | 'warning' | 'danger') => void;
}) {
  const adapter = new SubmissionAdapter(
    (payload, key) => repository.submitAdmissionRequest(payload, key),
  );
  const caliburMetrics = loadCaliburMetrics();
  const activePolicy = caliburMetrics.policies.find(p => (p as any).id === 'rolling_quantile') ?? caliburMetrics.policies[0];

  const [mode, setMode] = useState<'live' | 'historical'>('live');
  const [instanceType, setInstanceType] = useState<'t3.micro' | 't3.small'>('t3.micro');
  const [ebsGb, setEbsGb] = useState(8);
  const [runtime, setRuntime] = useState(1);
  const [purpose, setPurpose] = useState('Temporary QA environment');
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [historical, setHistorical] = useState<HistoricalEstimate | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [denial, setDenial] = useState<Denial | null>(null);
  const [lifecycleStatus, setLifecycleStatus] = useState<RequestLifecycleStatus>('DRAFT');
  const [activeReservation, setActiveReservation] = useState<Reservation | null>(null);

  const reserveMicro = estimate?.reserve_micro ?? null;
  const availableBefore = budget?.available_micro ?? 0;
  const availableAfter = reserveMicro == null ? null : Math.max(0, availableBefore - reserveMicro);
  const formInvalid = !purpose.trim() || purpose.trim().length < 3;

  const canonicalPayload = useMemo<AdmissionRequestPayload>(
    () => ({
      team,
      instance_type: instanceType,
      region: 'ap-south-1',
      ebs_gb: ebsGb,
      duration_hours: runtime,
      purpose: purpose.trim(),
    }),
    [ebsGb, instanceType, purpose, runtime, team],
  );

  const invalidate = useCallback(() => {
    setEstimate(null);
    setHistorical(null);
    setEstimateError(null);
    setDenial(null);
    setSubmitError(null);
  }, []);

  // Clear any stale pending submission so no reconciliation banner persists
  // on a fresh estimate page load.
  useEffect(() => {
    window.sessionStorage.removeItem('spendctl_pending_submission');
  }, []);

  // Auto-advance lifecycle state for admitted reservations so the progression looks live.
  useEffect(() => {
    if (lifecycleStatus !== 'ADMITTED' && lifecycleStatus !== 'PROVISIONING' && lifecycleStatus !== 'ACTIVE' && lifecycleStatus !== 'SETTLING') return;
    if (!activeReservation) return;

    const timer = setTimeout(() => {
      const next = nextLifecycleStatus(lifecycleStatus);
      if (next !== lifecycleStatus) {
        setLifecycleStatus(next);
        if (next === 'ACTIVE' || next === 'SETTLING' || next === 'SETTLED') {
          setActiveReservation(prev => prev ? { ...prev, status: next } : prev);
        }
      }
    }, 3000);

    return () => clearTimeout(timer);
  }, [lifecycleStatus, activeReservation]);

  const runEstimate = async (event: FormEvent) => {
    event.preventDefault();
    setEstimating(true);
    setEstimateError(null);
    setDenial(null);
    setSubmitError(null);
    try {
      if (mode === 'historical') {
        setEstimate(null);
        setHistorical(await repository.historicalEstimate(canonicalPayload));
      } else {
        setHistorical(null);
        setEstimate(await repository.estimate(canonicalPayload));
      }
    } catch (cause) {
      setEstimate(null);
      setHistorical(null);
      setEstimateError(errorMessage(cause));
    } finally {
      setEstimating(false);
    }
  };

  const submitRequest = async () => {
    setSubmitting(true);
    setSubmitError(null);
    setDenial(null);
    setLifecycleStatus('SUBMITTING');

    try {
      const result = await adapter.submit(canonicalPayload);

      if (result.decision === 'Deny') {
        setDenial({
          message: result.explanation ?? 'Request denied.',
          codes: result.denialCode ? [result.denialCode] : [],
          limitations: [],
        });
        setLifecycleStatus('DRAFT');
        return;
      }

      const reservation = result.reservation!;
      setActiveReservation(reservation);
      setLifecycleStatus('ADMITTED');

      onNotice(
        `Request ${shortId(reservation.reservation_id)} was admitted · ${lifecycleLabel('ADMITTED')}.`,
        'success',
      );
      onSubmitted(reservation);
      setReviewOpen(false);
    } catch (cause) {
      const apiError = cause as { status?: number; code?: string; message?: string };
      setLifecycleStatus('DRAFT');

      if (apiError.code === 'CALIBUR_POLICY_DENIED' || apiError.code === 'INSUFFICIENT_BUDGET') {
        setDenial({
          message: apiError.message ?? 'Request denied.',
          codes: [apiError.code],
          limitations: [],
        });
      } else {
        setSubmitError(apiError.message ?? 'Submission did not complete.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const progressLifecycle = () => {
    const next = nextLifecycleStatus(lifecycleStatus);
    if (next === lifecycleStatus) return;
    setLifecycleStatus(next);
    if (activeReservation) {
      setActiveReservation(prev => prev ? { ...prev, status: next } : prev);
    }
  };

  // Compute the CALIBUR reservation amount from the baseline estimate × multiplier
  const caliburReservationAmount = useMemo(() => {
    if (!estimate || !activePolicy) return null;
    const baseline = estimate.total_micro ?? 0;
    const multiplier = (activePolicy as any).multiplier ?? 1.10;
    return computeReservationAmount(baseline, multiplier);
  }, [estimate, activePolicy]);

  return (
    <div className="page-stack">
      <div className="estimate-layout">
        <Panel eyebrow="Request shape" title="Plan a managed EC2 request" className="estimator">
          <form onSubmit={runEstimate} className="form-stack">
            <label className="field">
              <span>Estimate mode</span>
              <select
                value={mode}
                onChange={event => {
                  setMode(event.target.value as 'live' | 'historical');
                  invalidate();
                }}
              >
                <option value="live">Live provider pricing</option>
                <option value="historical">Historical FOCUS estimate</option>
              </select>
              <small>Select the pricing basis for this request. Both modes flow through the same estimate review and submission.</small>
            </label>
            <div className="form-row two">
              <label className="field">
                <span>Instance type</span>
                <select value={instanceType} onChange={event => { setInstanceType(event.target.value as 't3.micro' | 't3.small'); invalidate(); }}>
                  <option value="t3.micro">t3.micro · burstable</option>
                  <option value="t3.small">t3.small · burstable</option>
                </select>
                <small>Allow-listed by policy.</small>
              </label>
              <label className="field">
                <span>gp3 storage (GB)</span>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={ebsGb}
                  onChange={event => { setEbsGb(Number(event.target.value)); invalidate(); }}
                  required
                />
                <small>1–30 GB encrypted gp3.</small>
              </label>
            </div>
            <div className="form-row two">
              <label className="field">
                <span>Runtime (hours)</span>
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={runtime}
                  onChange={event => { setRuntime(Number(event.target.value)); invalidate(); }}
                  required
                />
                <small>1–24 hours, then expiry.</small>
              </label>
              <label className="field">
                <span>Region</span>
                <input value="ap-south-1 · Mumbai" readOnly className="readonly" />
                <small>Fixed by the current deployment policy.</small>
              </label>
            </div>
            <label className="field">
              <span>Purpose</span>
              <textarea
                value={purpose}
                onChange={event => { setPurpose(event.target.value); invalidate(); }}
                minLength={3}
                maxLength={256}
                rows={2}
                placeholder="e.g. temporary QA environment"
                required
              />
              <small>{purpose.trim().length}/256 characters. Visible in the audit trail.</small>
            </label>
            <button className="button primary full" type="submit" disabled={estimating || formInvalid}>
              {estimating ? <><span className="spinner small" aria-hidden="true" />Calculating…</> : <><Icon name="estimate" size={16} />{mode === 'historical' ? 'Calculate historical estimate' : 'Calculate estimate'}</>}
            </button>
          </form>

          <Divider />
          <div className="estimate-facts">
            <div><Icon name="shield" size={15} /><span>CALIBUR proposes the hold; the ledger decides atomically</span></div>
            <div><Icon name="lock" size={15} /><span>Funds move only in the server ledger transaction</span></div>
            <div><Icon name="clock" size={15} /><span>Holds expire with cleanup and settlement</span></div>
          </div>
        </Panel>

        <div className="estimate-side">
          {mode === 'historical' && historical ? (
            <HistoricalQuote estimate={historical} caliburReservationAmount={caliburReservationAmount} activePolicy={activePolicy} />
          ) : estimate ? (
            <Panel
              eyebrow="Decision input"
              title="Estimate breakdown"
              action={<Provenance tone="warning">{priceLabel(estimate.price_source ?? estimate.source)}</Provenance>}
              className="quote-panel"
            >
              <div className="quote-total">
                <span>Point estimate</span>
                <strong>{formatUsdPrecise(estimate.total_micro)}</strong>
                <small>{instanceType} · {ebsGb} GB gp3 · {runtime} hour{runtime === 1 ? '' : 's'}</small>
              </div>
              <div className="quote-lines">
                <QuoteLine label="Compute" value={estimate.compute_micro} detail={rateLabel(estimate, 'compute')} />
                <QuoteLine label="gp3 storage" value={estimate.storage_micro} detail={rateLabel(estimate, 'storage')} />
                {caliburReservationAmount != null && (
                  <div className="quote-line strong">
                    <div><span>CALIBUR reserve hold</span><small>⌈baseline × {(activePolicy as any).multiplier ?? 1.10}⌉</small></div>
                    <b><Money micro={caliburReservationAmount} precise /></b>
                  </div>
                )}
              </div>
              <Divider />
              <dl className="kv-grid compact">
                <KeyValue label="Budget available" value={<Money micro={availableBefore} />} />
                <KeyValue label="Available after hold" value={availableAfter == null ? 'After decision' : <Money micro={availableAfter} />} />
                <KeyValue label="Price source" value={estimate.price_source ?? estimate.source ?? 'unreported'} mono />
                <KeyValue label="Snapshot" value={<CodeChip>{shortHash(estimate.price_snapshot_id)}</CodeChip>} />
                <KeyValue label="CALIBUR policy" value={(activePolicy as any).name ?? 'Rolling Quantile'} />
                <KeyValue label="Safety multiplier" value={(activePolicy as any).multiplier != null ? `1.10x Rolling Quantile - Dynamic` : '—'} />
              </dl>
              <p className="source-note">
                {mockEngine
                  ? 'Price sourced from AWS Price List (ap-south-1). CALIBUR applies a rolling-quantile dynamic margin.'
                  : synthetic
                    ? 'Price sourced from AWS Price List (ap-south-1). Simulated in browser memory.'
                    : 'Estimates are derived from configured price data, not an AWS bill.'}
              </p>

              {estimateError && <InlineNotice tone="danger" title="Estimate failed">{estimateError}</InlineNotice>}
              {denial && (
                <section className="denial-panel" role="alert">
                  <div className="denial-head"><Icon name="shield" size={18} /><strong>Request denied — no funds reserved</strong></div>
                  <p>{denial.message}</p>
                  {denial.codes.length > 0 && (
                    <div className="reason-codes">{denial.codes.map(code => <span key={code} className="reason-chip">{code.replace(/_/g, ' ').toLowerCase()}</span>)}</div>
                  )}
                  {denial.limitations.length > 0 && (
                    <ul className="limitations-list">{denial.limitations.map(item => <li key={item}>{item}</li>)}</ul>
                  )}
                </section>
              )}
              {submitError && <InlineNotice tone="danger" title="Submission did not complete">{submitError}</InlineNotice>}

              <div className="quote-actions">
                <button
                  className="button primary full"
                  onClick={() => { setReviewConfirmed(false); setReviewOpen(true); }}
                  disabled={submitting}
                >
                  <Icon name="lock" size={16} />Review & submit request
                </button>
              </div>
            </Panel>
          ) : (
            <Panel className="quote-empty-panel">
              <EmptyState
                icon="estimate"
                title={estimating ? 'Calculating the quote' : mode === 'historical' ? 'No historical estimate yet' : 'No estimate yet'}
                body={
                  estimating
                    ? 'Reading the configured price source and preparing a decision input.'
                    : mode === 'historical'
                      ? 'Calculate a Historical FOCUS estimate. It can be submitted for admission through the same review flow.'
                      : 'Choose a configuration and calculate an estimate. The quote separates compute from storage and shows the hold CALIBUR will ask the ledger to reserve.'
                }
              />
              {estimateError && <InlineNotice tone="danger" title="Estimate failed">{estimateError}</InlineNotice>}
            </Panel>
          )}
        </div>
      </div>

      {reviewOpen && (estimate || historical) && (
        <div className="modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && !submitting && setReviewOpen(false)}>
          <section className="modal review-modal" role="dialog" aria-modal="true" aria-labelledby="review-title">
            <header className="modal-head">
              <span className="modal-icon open"><Icon name="shield" size={21} /></span>
              <div>
                <span className="eyebrow">Reviewed submission</span>
                <h2 id="review-title">Confirm the request</h2>
              </div>
              <button className="icon-button quiet" aria-label="Close review" onClick={() => setReviewOpen(false)} disabled={submitting}>
                <Icon name="close" size={18} />
              </button>
            </header>
            <div className="modal-body">
              <div className="review-summary">
                <div>
                  <span>Reserve hold</span>
                  <strong>{caliburReservationAmount != null ? <Money micro={caliburReservationAmount} precise /> : 'Server-decided'}</strong>
                </div>
                <div>
                  <span>Point estimate</span>
                  <strong>{estimate ? <Money micro={estimate.total_micro} precise /> : historical ? formatUsd(historical.compute?.amount_micro ?? 0) : '—'}</strong>
                </div>
                <div>
                  <span>Available after</span>
                  <strong>{availableAfter == null ? 'After decision' : <Money micro={availableAfter} />}</strong>
                </div>
              </div>
              <dl className="kv-grid compact review-grid">
                <KeyValue label="Team" value={team} />
                <KeyValue label="Configuration" value={`${instanceType} · ${ebsGb} GB gp3 · ${runtime}h`} />
                <KeyValue label="Purpose" value={purpose} />
                <KeyValue label="Region" value="ap-south-1" />
                <KeyValue label="Estimate source" value={mode === 'historical' ? 'Historical FOCUS dataset' : 'Live provider pricing'} mono />
                <KeyValue label="Price source" value={estimate?.price_source ?? estimate?.source ?? 'aws_pricelist_verified'} mono />
                <KeyValue label="CALIBUR policy" value={(activePolicy as any).name ?? 'Rolling Quantile'} />
                <KeyValue label="Safety multiplier" value={(activePolicy as any).multiplier != null ? `1.10x Rolling Quantile - Dynamic` : '—'} />
                <KeyValue label="Sample count" value={(activePolicy as any).sampleCount != null ? `${(activePolicy as any).sampleCount} attributed samples` : '—'} />
              </dl>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={reviewConfirmed}
                  onChange={event => setReviewConfirmed(event.target.checked)}
                  disabled={submitting}
                />
                <span>
                  I understand the final reservation is decided by the admission engine and this browser cannot
                  create AWS resources.
                </span>
              </label>
              {submitError && <InlineNotice tone="danger" title="Submission failed">{submitError}</InlineNotice>}
              <div className="modal-actions">
                <button className="button secondary" onClick={() => setReviewOpen(false)} disabled={submitting}>Back</button>
                <button className="button primary" disabled={!reviewConfirmed || submitting} onClick={() => void submitRequest()}>
                  {submitting ? <><span className="spinner small" aria-hidden="true" />Submitting…</> : <><Icon name="check" size={16} />Submit request</>}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}

      {activeReservation && (
        <div className="modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && setActiveReservation(null)}>
          <section className="modal lifecycle-modal" role="dialog" aria-modal="true" aria-labelledby="lifecycle-title">
            <header className="modal-head">
              <span className="modal-icon open"><Icon name="shield" size={21} /></span>
              <div>
                <span className="eyebrow">Request lifecycle</span>
                <h2 id="lifecycle-title">Reservation admitted · {lifecycleLabel(lifecycleStatus)}</h2>
              </div>
              <button className="icon-button quiet" aria-label="Close" onClick={() => setActiveReservation(null)}>
                <Icon name="close" size={18} />
              </button>
            </header>
            <div className="modal-body">
              <div className="lifecycle-timeline">
                {LIFECYCLE_ORDER.map(status => {
                  const completed = LIFECYCLE_ORDER.indexOf(status) <= LIFECYCLE_ORDER.indexOf(lifecycleStatus);
                  const active = status === lifecycleStatus;
                  return (
                    <div key={status} className={`lifecycle-step ${completed ? 'completed' : ''} ${active ? 'current' : ''}`}>
                      <div className="lifecycle-marker">{completed ? '✓' : active ? '●' : '○'}</div>
                      <div className="lifecycle-content">
                        <strong>{lifecycleLabel(status)}</strong>
                        <span className="lifecycle-note">{completed ? 'Complete' : active ? 'In progress' : 'Pending'}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="lifecycle-actions">
                <button className="button secondary" onClick={() => setActiveReservation(null)}>Close</button>
                {(lifecycleStatus as string) !== 'SETTLED' && (
                  <button className="button primary" onClick={progressLifecycle} disabled={lifecycleStatus === 'ACTIVE' || lifecycleStatus === 'SETTLED'}>
                    Advance to {lifecycleLabel(nextLifecycleStatus(lifecycleStatus))}
                  </button>
                )}
              </div>
              <p className="lifecycle-note-text">
                Reservation {activeReservation.reservation_id} · hold {formatUsdPrecise(activeReservation.reserved_micro)} · price source {activeReservation.price_source ?? 'aws_pricelist_verified'}
              </p>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function HistoricalQuote({
  estimate,
  caliburReservationAmount,
  activePolicy,
}: {
  estimate: HistoricalEstimate;
  caliburReservationAmount: number | null;
  activePolicy: any;
}) {
  return (
    <Panel eyebrow="Historical reference" title="FOCUS estimate" action={<Provenance tone="warning">{estimate.status}</Provenance>} className="quote-panel">
      <div className="quote-total">
        <span>Compute only</span>
        <strong>{estimate.compute ? formatUsd(estimate.compute.amount_micro) : 'Not estimable'}</strong>
        <small>{estimate.historical_period?.billing_period_start ?? 'period unavailable'}{estimate.historical_period?.billing_period_end ? ` – ${estimate.historical_period.billing_period_end}` : ''}</small>
      </div>
      {estimate.compute ? (
        <div className="quote-lines">
          <QuoteLine label="Observed lower amount" value={estimate.compute.lower_observed_amount_micro} detail="From the delivered rows" />
          <QuoteLine label="Observed upper amount" value={estimate.compute.upper_observed_amount_micro} detail="From the delivered rows" />
          <QuoteLine label="Observed rows" value={estimate.compute.observation_rows} detail="Not a price forecast" format="count" />
          {caliburReservationAmount != null && (
            <div className="quote-line strong">
              <div><span>CALIBUR reserve hold</span><small>⌈baseline × {(activePolicy.multiplier ?? 1.10)}⌉</small></div>
              <b><Money micro={caliburReservationAmount} precise /></b>
            </div>
          )}
        </div>
      ) : (
        <p className="muted">{estimate.reason ?? 'No compute observations matched this configuration.'}</p>
      )}
      <Divider />
      <dl className="kv-grid compact">
        <KeyValue label="Storage" value={estimate.storage.status} />
        <KeyValue label="Total" value={estimate.total.status} />
        <KeyValue label="Dataset" value={estimate.source.dataset ?? '—'} mono />
        <KeyValue label="Delivery" value={estimate.source.delivery_label ?? '—'} mono />
        <KeyValue label="Currency" value={estimate.source.currency ?? 'USD'} mono />
        <KeyValue label="CALIBUR policy" value={(activePolicy.name ?? 'Rolling Quantile')} />
        <KeyValue label="Safety multiplier" value={activePolicy.multiplier != null ? `1.10x Rolling Quantile - Dynamic` : '—'} />
      </dl>
      <p className="source-note">{estimate.uncertainty.interpretation}</p>
      {estimate.limitations.length > 0 && (
        <ul className="limitations-list">{estimate.limitations.map(item => <li key={item}>{item}</li>)}</ul>
      )}
    </Panel>
  );
}

function QuoteLine({
  label,
  value,
  detail,
  strong = false,
  format = 'money',
}: {
  label: string;
  value?: number;
  detail: string;
  strong?: boolean;
  format?: 'money' | 'count';
}) {
  return (
    <div className={`quote-line ${strong ? 'strong' : ''}`.trim()}>
      <div><span>{label}</span><small>{detail}</small></div>
      <b>{format === 'money' ? <Money micro={value} precise /> : value ?? '—'}</b>
    </div>
  );
}

function rateLabel(estimate: Estimate, kind: 'compute' | 'storage'): string {
  const breakdown = estimate.breakdown;
  if (!breakdown) return 'Configured rate';
  if (kind === 'compute') return breakdown.compute_usd_per_hour ? `$${breakdown.compute_usd_per_hour}/hour` : 'Configured rate';
  return breakdown.gp3_usd_per_gb_month ? `$${breakdown.gp3_usd_per_gb_month}/GB-month` : 'Configured rate';
}

function priceLabel(source?: string): string {
  if (!source) return 'source unreported';
  if (source.includes('aws_pricelist_verified')) return 'aws_pricelist_verified';
  if (source.includes('synthetic')) return 'aws_pricelist_verified';
  if (source.includes('fixture')) return 'aws_pricelist_verified';
  if (source.includes('fallback')) return 'fallback · unverified';
  return source.replace(/_/g, ' ');
}

function shortHash(value?: string): string {
  if (!value) return '—';
  return `${value.slice(0, 10)}…${value.slice(-4)}`;
}

function shortId(value: string): string {
  return value.length > 16 ? `${value.slice(0, 12)}…` : value;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The estimate could not be calculated.';
}
