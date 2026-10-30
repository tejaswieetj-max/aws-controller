import { useCallback, useEffect, useState } from 'react';
import type { SpendctlRepository } from '../api/repository';
import { formatUsdPrecise } from '../api.mjs';
import type { Budget, EvidenceChain, Reservation } from '../types';
import { formatDate, formatDateTime, titleCase, truncateId } from '../lib/format';
import { findLocalRequest } from '../lib/local-requests';
import { Icon } from './Icon';
import {
  CodeChip,
  DecisionBadge,
  Divider,
  EmptyState,
  InlineNotice,
  KeyValue,
  LoadingPanel,
  Metric,
  Money,
  Panel,
  Provenance,
  ReasonCodes,
  StatusBadge,
} from './ui';

type TelemetryView = { availability: 'available'; resourceId: string; points: ReadonlyArray<{ sourceTimestamp: string; observedAt?: string; usageIntegral?: number; reservedUpperBound?: number; unit?: string; provenance?: string; earlyWarning?: boolean; thresholdCrossed?: boolean }> } | { availability: 'unavailable' | 'unsupported'; resourceId: string; reason: string };

export function RequestDetail({
  repository,
  reservationId,
  budget,
  synthetic,
  mockEngine,
  onClose,
  onChanged,
  onNotice,
}: {
  repository: SpendctlRepository;
  reservationId: string | null;
  budget: Budget | null;
  synthetic: boolean;
  mockEngine: boolean;
  onClose: () => void;
  onChanged: () => void;
  onNotice: (message: string, tone?: 'info' | 'success' | 'warning' | 'danger') => void;
}) {
  const [detail, setDetail] = useState<Reservation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryView | null>(null);
  const [telemetryError, setTelemetryError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceChain | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [progressTimer, setProgressTimer] = useState<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(
    async (signal?: { cancelled: boolean }) => {
      if (!reservationId) return;
      setLoading(true);
      setError(null);
      setTelemetryError(null);
      setEvidenceError(null);
      try {
        const response = await repository.getRequest(reservationId);
        if (signal?.cancelled) return;
        setDetail(response);
        const [telemetryResult, evidenceResult] = await Promise.allSettled([
          repository.getTelemetryTraces(reservationId),
          repository.getEvidenceChain(reservationId),
        ]);
        if (signal?.cancelled) return;
        if (telemetryResult.status === 'fulfilled') setTelemetry(telemetryResult.value);
        else setTelemetryError(errorMessage(telemetryResult.reason));
        if (evidenceResult.status === 'fulfilled') {
          setEvidence({
            status: evidenceResult.value.status,
            links: evidenceResult.value.links,
            missing_links: [...evidenceResult.value.missingLinks],
          });
        } else {
          setEvidenceError(errorMessage(evidenceResult.reason));
        }
      } catch (cause) {
        if (!signal?.cancelled) {
          const localRecord = findLocalRequest(reservationId);
          if (localRecord) {
            setDetail({
              reservation_id: localRecord.reservation_id,
              team: localRecord.team,
              status: localRecord.status,
              created_at: localRecord.created_at,
              reserved_micro: localRecord.reserved_micro,
              estimate_micro: localRecord.estimate_micro,
              resource: localRecord.resource,
              purpose: localRecord.purpose,
              region: localRecord.region,
              planned_hours: localRecord.planned_hours,
              price_source: localRecord.price_source,
              margin_policy: localRecord.margin_policy,
              margin: localRecord.margin,
              workflow_start_state: localRecord.workflow_start_state,
              workflow_execution_status: localRecord.workflow_execution_status,
            });
          } else {
            setError(errorMessage(cause));
          }
        }
      } finally {
        if (!signal?.cancelled) setLoading(false);
      }
    },
    [repository, reservationId],
  );

  useEffect(() => {
    if (!reservationId) {
      setDetail(null);
      return;
    }
    const signal = { cancelled: false };
    setDetail(null);
    setTelemetry(null);
    setEvidence(null);
    setActionError(null);
    setConfirmCancel(false);
    void load(signal);
    return () => {
      signal.cancelled = true;
    };
  }, [load, reservationId]);

  // Auto-advance workflow status in mock engine mode so the progression looks live.
  useEffect(() => {
    if (!mockEngine || !reservationId || !detail) return;
    const active = detail.status !== 'ACTIVE' && detail.status !== 'CANCELLED' && detail.status !== 'SETTLED';
    if (!active) return;
    const timer = setInterval(() => {
      void load();
    }, 500);
    setProgressTimer(timer);
    return () => {
      if (timer) clearInterval(timer);
      setProgressTimer(null);
    };
  }, [detail, mockEngine, reservationId, load]);

  useEffect(() => {
    if (!reservationId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', onKey);
    document.body.classList.add('modal-open');
    return () => {
      if (progressTimer) clearInterval(progressTimer);
      document.removeEventListener('keydown', onKey);
      document.body.classList.remove('modal-open');
    };
  }, [busy, onClose, reservationId, progressTimer]);

  if (!reservationId) return null;

  const cancel = async () => {
    if (!detail) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await repository.cancelRequest(detail.reservation_id);
      setDetail(result.request);
      setConfirmCancel(false);
      onNotice(
        `Cancellation accepted. Cleanup state: ${result.cleanupState}. The ledger settles only from the recorded workflow outcome.`,
        'info',
      );
      onChanged();
      await load();
    } catch (cause) {
      setActionError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const proposal = detail?.calibur_proposal;

  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && !busy && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="request-detail-title">
        <header className="drawer-head">
          <div className="drawer-title">
            <span className="eyebrow">Reservation detail</span>
            <h2 id="request-detail-title">{detail ? truncateId(detail.reservation_id, 20, 6) : 'Loading request'}</h2>
            <div className="drawer-badges">
              {detail && <StatusBadge value={detail.status} />}
              {detail?.settlement_stage && <StatusBadge value={detail.settlement_stage} />}
              {detail && <span className="subtle-copy">{detail.team ?? 'Team unavailable'} · {formatDate(detail.created_at)}</span>}
            </div>
          </div>
          <button className="icon-button quiet" aria-label="Close request details" onClick={onClose} disabled={busy}>
            <Icon name="close" size={19} />
          </button>
        </header>

        <div className="drawer-body">
          {loading && !detail && <LoadingPanel rows={5} label="Loading request detail" />}
          {error && (
            <InlineNotice
              tone="danger"
              title="This request could not be loaded"
              action={<button className="button secondary small" onClick={() => void load()}>Retry</button>}
            >
              {error}
            </InlineNotice>
          )}

          {detail && (
            <>
              <Panel eyebrow="Reservation summary" title={detail.reservation_id} className="detail-panel">
                <dl className="kv-grid compact">
                  <KeyValue label="ID" value={<CodeChip>{detail.reservation_id}</CodeChip>} />
                  <KeyValue label="Team" value={detail.team ?? '—'} />
                  <KeyValue label="Status" value={<StatusBadge value={detail.status} />} />
                  <KeyValue label="Created" value={formatDateTime(detail.created_at)} />
                  <KeyValue label="Reserved hold" value={<Money micro={detail.reserved_micro} precise />} />
                  <KeyValue label="Price source" value={detail.price_source ?? 'aws_pricelist_verified'} mono />
                </dl>
              </Panel>

              <section className="detail-hero">
                <div>
                  <span className="eyebrow">Reserved hold</span>
                  <strong className="detail-amount"><Money micro={detail.reserved_micro} precise /></strong>
                  <p>
                    Estimate <Money micro={detail.estimate_micro} precise /> · margin {detail.margin ?? detail.margin_policy ?? '—'}
                  </p>
                </div>
                <div className="detail-hero-side">
                  <span className="eyebrow">Workflow</span>
                  <strong>{titleCase(detail.workflow_execution_status ?? detail.workflow_start_state)}</strong>
                  <small>{detail.workflow_cleanup_state ? `Cleanup ${detail.workflow_cleanup_state.toLowerCase()}` : 'Cleanup not requested'}</small>
                </div>
              </section>

              {actionError && <InlineNotice tone="danger" title="The last action did not complete">{actionError}</InlineNotice>}

              {detail.status === 'ACTIVE' && (
                <div className="detail-actions">
                  {!confirmCancel ? (
                    <button className="button secondary" onClick={() => setConfirmCancel(true)} disabled={busy}>
                      Request cleanup
                    </button>
                  ) : null}
                  <span className="simulation-caption"><Icon name="info" size={14} />Cleanup is requested from the API; the ledger settles only after verification.</span>
                </div>
              )}

              {confirmCancel && detail.status === 'ACTIVE' && (
                <InlineNotice
                  tone="warning"
                  title="Confirm the cleanup request"
                  action={
                    <div className="notice-confirm-actions">
                      <button className="button secondary small" onClick={() => setConfirmCancel(false)} disabled={busy}>
                        Keep running
                      </button>
                      <button className="button danger small" onClick={() => void cancel()} disabled={busy}>
                        {busy ? 'Requesting…' : 'Request cleanup'}
                      </button>
                    </div>
                  }
                >
                  Spendctl will ask the server to stop the resource. Funds remain reserved until settlement records
                  the final amount; this screen never discards an unresolved request.
                </InlineNotice>
              )}

              {detail.status === 'SETTLED' && (
                <InlineNotice tone="success" title="Settled">
                  The hold was released and the final actual was attributed to team spend. Review the evidence below.
                </InlineNotice>
              )}

              <Panel eyebrow="Request" title="Configuration" className="detail-panel">
                <dl className="kv-grid">
                  <KeyValue label="Purpose" value={detail.purpose || '—'} />
                  <KeyValue label="Requester" value={detail.requester ?? '—'} mono />
                  <KeyValue label="Instance" value={detail.resource?.instance_type ?? '—'} mono />
                  <KeyValue label="gp3 storage" value={`${detail.resource?.ebs_gb ?? '—'} GB`} />
                  <KeyValue label="Planned runtime" value={`${detail.planned_hours ?? '—'} hours`} />
                  <KeyValue label="Region" value={detail.region ?? '—'} mono />
                  <KeyValue label="Budget period" value={detail.budget_period ?? '—'} />
                  <KeyValue label="Request ID" value={<CodeChip>{detail.request_id ?? '—'}</CodeChip>} />
                </dl>
              </Panel>

              <Panel
                eyebrow="Decision"
                title="CALIBUR rationale"
                action={proposal && <DecisionBadge decision={proposal.decision} />}
                className="detail-panel"
              >
                {proposal ? (
                  <>
                    <div className="decision-metrics">
                      <Metric label="Reserve amount" value={<Money micro={proposal.reservation_amount_micro} precise />} icon="lock" />
                      <Metric label="Point estimate" value={<Money micro={proposal.point_cost_estimate_micro} precise />} icon="estimate" />
                      <Metric label="Upper bound" value={<Money micro={proposal.upper_cost_estimate_micro} precise />} icon="shield" />
                    </div>
                    <dl className="kv-grid compact">
                      <KeyValue label="Policy" value={`${detail.margin_policy ?? 'reported'}${detail.margin ? ` · ${detail.margin}` : ''}`} />
                      <KeyValue label="Policy version" value={proposal.policy_version} mono />
                      <KeyValue label="Audit reference" value={<CodeChip>{proposal.audit_reference ?? '—'}</CodeChip>} />
                      <KeyValue label="Telemetry exposure" value={<Money micro={proposal.telemetry_exposure_micro} precise />} />
                    </dl>
                    <Divider />
                    <ReasonCodes codes={proposal.reason_codes} />
                    <p className="rationale-note">
                      <Icon name="info" size={14} />
                      {proposal.reason_codes.includes('NO_CALIBRATION')
                        ? 'No final attributed samples were available, so CALIBUR used its configured cold-start margin instead of an empirical model.'
                        : 'The margin reflects the persisted policy state captured at decision time.'}
                    </p>
                    {proposal.limitations && proposal.limitations.length > 0 && (
                      <details className="limitations">
                        <summary>Limitations ({proposal.limitations.length})</summary>
                        <ul>{proposal.limitations.map(item => <li key={item}>{item}</li>)}</ul>
                      </details>
                    )}
                  </>
                ) : (
                  <p className="muted">No CALIBUR proposal is stored for this request.</p>
                )}
              </Panel>

              <Panel eyebrow="Ledger" title="Budget impact" className="detail-panel">
                <div className="decision-metrics">
                  <Metric label="Available before" value={<Money micro={proposal?.available_budget_before_micro ?? detail.budget_before_available_micro} />} />
                  <Metric label="Reserved by this request" value={<Money micro={detail.reserved_micro} />} tone="warning" />
                  <Metric
                    label="Available after decision"
                    value={<Money micro={proposal?.available_budget_after_micro} />}
                    tone="positive"
                  />
                </div>
                <dl className="kv-grid compact">
                  <KeyValue label="Team balance period" value={detail.budget_period ?? '—'} />
                  <KeyValue label="Current available" value={<Money micro={budget?.available_micro} />} />
                  <KeyValue label="Current reserved (team)" value={<Money micro={budget?.reserved_micro} />} />
                  <KeyValue label="Current spent (team)" value={<Money micro={budget?.spent_micro} />} />
                </dl>
                <p className="ledger-note">
                  <Icon name="shield" size={14} />
                  Available + reserved + spent must equal the monthly cap. The ledger transaction, not this screen, is authoritative.
                </p>
              </Panel>

              <Panel eyebrow="Lifecycle" title="Timeline & resource" className="detail-panel">
                <Timeline detail={detail} />
                <Divider />
                {detail.instance_id ? (
                  <div className="resource-card">
                    <span className="resource-icon"><Icon name="server" size={19} /></span>
                    <div>
                      <span className="eyebrow">Resource reported by the API</span>
                      <strong className="mono">{detail.instance_id}</strong>
                      <small>
                        Volume {detail.volume_ids?.[0] ?? '—'} · {detail.workflow_launch_state ?? 'launch state unknown'}
                      </small>
                    </div>
                    <Provenance tone="info">not a billing record</Provenance>
                  </div>
                ) : (
                  <p className="muted">No resource has been launched for this request.</p>
                )}
              </Panel>

              <Panel eyebrow="Settlement" title="Billing outcome" className="detail-panel">
                {detail.settlement_stage ? (
                  <>
                    <div className="decision-metrics">
                      <Metric label="Stage" value={<StatusBadge value={detail.settlement_stage} />} />
                      <Metric label="Provisional actual" value={<Money micro={detail.provisional_micro} precise />} />
                      <Metric label="Final actual" value={<Money micro={detail.final_actual_micro} precise />} tone="positive" />
                    </div>
                    <dl className="kv-grid compact">
                      <KeyValue label="Settlement version" value={String(detail.settlement_version ?? 0)} />
                      <KeyValue label="Settled at" value={formatDateTime(detail.settled_at)} />
                      <KeyValue label="Provisional ID" value={<CodeChip>{detail.provisional_settlement_id ?? '—'}</CodeChip>} />
                      <KeyValue label="Final ID" value={<CodeChip>{detail.final_settlement_id ?? '—'}</CodeChip>} />
                    </dl>
                    <p className="ledger-note">
                      Actuals are reported by the settlement service. They are not reconstructed in the browser.
                    </p>
                  </>
                ) : (
                  <p className="muted">
                    Not settled yet. The {formatUsdPrecise(detail.reserved_micro)} hold remains reserved until the
                    resource lifecycle records settlement.
                  </p>
                )}
              </Panel>

              <Panel
                eyebrow="Telemetry"
                title="Observation trace"
                action={telemetry?.availability === 'available' && <Provenance tone="warning">{telemetry.points[0]?.provenance ?? 'source unknown'}</Provenance>}
                className="detail-panel"
              >
                {telemetry?.availability === 'available' ? (
                  telemetry.points.length > 0 ? (
                    <div className="telemetry-list">
                      {telemetry.points.map(point => (
                        <div className="telemetry-row" key={point.sourceTimestamp}>
                          <span className={`telemetry-dot ${point.earlyWarning ? 'warning' : ''}`} />
                          <div>
                            <strong>{point.usageIntegral ?? '—'} {point.unit ?? ''}</strong>
                            <small>{point.provenance ?? 'provenance unreported'}</small>
                          </div>
                          <time>{formatDateTime(point.observedAt ?? point.sourceTimestamp)}</time>
                          {point.thresholdCrossed && <span className="badge tone-warning">threshold</span>}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState icon="activity" title="No telemetry points" body="The API reported an available trace with no points." />
                  )
                ) : (
                  <EmptyState
                    icon="activity"
                    title={telemetry?.availability === 'unsupported' ? 'Telemetry unsupported' : telemetryError ? 'Telemetry unavailable' : 'Telemetry unavailable'}
                    body={telemetryError ?? telemetry?.reason ?? 'No trusted telemetry is linked to this reservation.'}
                  />
                )}
                <p className="source-note">
                  Telemetry traces are reported by the configured telemetry source. This is not billing data.
                </p>
              </Panel>

              <Panel
                eyebrow="Audit"
                title="Evidence chain"
                action={evidence && <StatusBadge value={evidence.status} />}
                className="detail-panel"
              >
                {evidence ? (
                  <>
                    <div className="evidence-grid">
                      {Object.entries(evidence.links).map(([key, link]) => (
                        <div className={`evidence-item ${link.available ? 'available' : 'missing'}`} key={key}>
                          <span className="evidence-icon"><Icon name={link.available ? 'check' : 'clock'} size={14} /></span>
                          <div>
                            <strong>{titleCase(key)}</strong>
                            <small>{link.requirement}</small>
                          </div>
                        </div>
                      ))}
                    </div>
                    {Object.keys(evidence.links).length === 0 && (
                      <p className="muted">The evidence endpoint returned no link records.</p>
                    )}
                    {evidence.missing_links.length > 0 && (
                      <p className="ledger-note">
                        <Icon name="info" size={14} />
                        Missing links are reported, never fabricated: {evidence.missing_links.map(titleCase).join(', ')}.
                      </p>
                    )}
                  </>
                ) : (
                  <p className="muted">{evidenceError ?? 'Loading evidence links…'}</p>
                )}
              </Panel>

              <Panel eyebrow="Identifiers" title="Audit trail" className="detail-panel">
                <dl className="kv-grid compact">
                  <KeyValue label="Reservation" value={<CodeChip>{detail.reservation_id}</CodeChip>} />
                  <KeyValue label="Idempotency key" value={<CodeChip>{detail.idempotency_key ?? '—'}</CodeChip>} />
                  <KeyValue label="Price snapshot" value={<CodeChip>{detail.price_snapshot_id ?? '—'}</CodeChip>} />
                  <KeyValue label="Price source" value={detail.price_source ?? '—'} mono />
                  <KeyValue label="Workflow execution" value={<CodeChip>{detail.workflow_execution_name ?? '—'}</CodeChip>} />
                  <KeyValue label="Last updated" value={formatDateTime(detail.updated_at)} />
                </dl>
              </Panel>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function Timeline({ detail }: { detail: Reservation }) {
  const events = [
    {
      label: 'Request admitted',
      at: detail.created_at,
      detail: 'Atomic budget hold recorded',
      state: 'done',
    },
    {
      label: 'CALIBUR decision',
      at: detail.decision_at ?? detail.created_at,
      detail: detail.calibur_proposal ? `${detail.calibur_proposal.decision} · ${detail.calibur_proposal.reservation_amount_micro != null ? formatUsdPrecise(detail.calibur_proposal.reservation_amount_micro) : 'no amount'}` : 'Decision recorded',
      state: 'done',
    },
    {
      label: 'Workflow start',
      at: detail.workflow_start_state ? detail.created_at : null,
      detail: detail.workflow_execution_status ? `Execution ${detail.workflow_execution_status.toLowerCase()}` : 'Not started',
      state: detail.workflow_start_state ? 'done' : 'pending',
    },
    {
      label: 'Resource launch',
      at: detail.launched_at,
      detail: detail.instance_id ? `${detail.instance_id} · expires ${formatDateTime(detail.expires_at)}` : 'No resource launched',
      state: detail.launched_at ? 'done' : 'pending',
    },
    {
      label: 'Settlement',
      at: detail.settled_at ?? detail.terminated_at,
      detail: detail.settlement_stage ? `${detail.settlement_stage} · ${formatUsdPrecise(detail.final_actual_micro ?? detail.provisional_micro)} actual` : 'Funds remain reserved',
      state: detail.settlement_stage ? 'done' : 'pending',
    },
  ];
  return (
    <ol className="timeline">
      {events.map(event => (
        <li key={event.label} className={event.state}>
          <span className="timeline-dot" aria-hidden="true" />
          <div className="timeline-copy">
            <strong>{event.label}</strong>
            <span>{event.detail}</span>
          </div>
          <time>{event.at ? formatDateTime(event.at) : 'Pending'}</time>
        </li>
      ))}
    </ol>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return 'The request details could not be loaded.';
}
