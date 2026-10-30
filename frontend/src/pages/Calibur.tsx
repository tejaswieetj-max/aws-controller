import type { CaliburMetrics, CalibrationPolicySummary, MetricValue } from '../api/repository';
import { loadCaliburMetrics, type CaliburnMetrics, type CaliburnPolicy } from '../lib/calibur-data';
import { formatUsd } from '../api.mjs';
import { formatDateTime, titleCase } from '../lib/format';
import { Icon } from '../components/Icon';
import {
  CodeChip,
  EmptyState,
  InlineNotice,
  LoadingPanel,
  Metric,
  Panel,
  Provenance,
  StatusBadge,
} from '../components/ui';

type DataBadge = 'dataset' | 'backend';

export function CaliburPage({
  metrics,
  loading,
  error,
  onRefresh,
  synthetic,
  mockEngine,
}: {
  metrics: CaliburMetrics | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  synthetic: boolean;
  mockEngine: boolean;
}) {
  const dataset = loadCaliburMetrics();
  const apiMetrics = metrics;
  const hasApiMetrics = apiMetrics?.availability === 'available' && apiMetrics.policies && apiMetrics.policies.length > 0;
  const activeMetrics = hasApiMetrics ? apiMetrics : dataset;
  const dataSource: DataBadge = hasApiMetrics ? 'backend' : 'dataset';

  if (loading && !metrics && !dataset) return <LoadingPanel rows={5} label="Loading CALIBUR policy" />;

  if (!metrics && !dataset) {
    return (
      <div className="page-stack">
        <Panel>
          {error ? (
            <InlineNotice tone="danger" title="CALIBUR evidence could not be loaded">
              {error}
            </InlineNotice>
          ) : (
            <EmptyState
              icon="calibur"
              title="No CALIBUR evidence returned"
              body="No CALIBUR metrics are available from the backend or dataset. No fallback policy is fabricated."
            />
          )}
        </Panel>
      </div>
    );
  }

  const policies = (activeMetrics.policies ?? []) as unknown as (CaliburnPolicy & { id?: string })[];
  const restricted = apiMetrics?.calibrationAccess === 'restricted';
  const activePolicy = policies.find(item => item.id === 'rolling_quantile') ?? policies[0];
  const estimator = apiMetrics?.kalmanEstimate;
  const release = apiMetrics?.releasePlan;
  const lambda = metricValue(apiMetrics?.riskParameterLambda);
  const alpha = metricValue(apiMetrics?.targetRiskAlpha);
  const stateSpend = apiMetrics?.stateSpaceSpendEstimate.availability === 'available'
    ? formatUsd(apiMetrics.stateSpaceSpendEstimate.value)
    : dataset.stateSpaceSpendEstimateMicro != null
      ? `${formatUsd(dataset.stateSpaceSpendEstimateMicro)} (dataset)`
      : 'Unavailable';

  const lambdaHistoryItems = apiMetrics?.lambdaHistory?.length ? apiMetrics.lambdaHistory : dataset.lambdaHistory;
  const spendSeriesItems = apiMetrics?.spendSeries?.length ? apiMetrics.spendSeries : dataset.spendSeries;

  return (
    <div className="page-stack">
      {error && <InlineNotice tone="danger" title="CALIBUR evidence could not be refreshed">{error}</InlineNotice>}

      {(synthetic || mockEngine) && (
        <InlineNotice tone="info" title={synthetic ? 'Synthetic demo provider' : 'Offline mock engine'}>
          {synthetic
            ? 'These values come from browser fixtures selected explicitly with VITE_USE_SYNTHETIC_DATA. They are not AWS cost observations and cannot be treated as empirical CALIBUR evidence.'
            : 'These values come from the client-side mock engine. Admission decisions, budget holds, and CALIBUR metrics are simulated in browser memory.'}
        </InlineNotice>
      )}

      <section className="calibur-hero">
        <div>
          <span className="eyebrow light">Reserve policy · {dataSource === 'dataset' ? 'Dataset calibration results' : 'Backend-reported'}</span>
          <h2>CALIBUR sets the hold, the ledger enforces it.</h2>
          <p>
            The engine proposes a reservation from a point estimate, an upper bound, and the current budget
            snapshot. A denial is final for that request; an approval never overrides the atomic ledger check.
          </p>
        </div>
        <div className="calibur-badges">
          <div className={`data-badge ${dataSource}`}>
            <span>{dataSource === 'dataset' ? 'Dataset calibration results' : 'Backend-reported'}</span>
          </div>
          <div><span>Reported policy</span><strong>{restricted ? 'Restricted' : (activePolicy as CaliburnPolicy & { id?: string }).id ?? 'none'}</strong></div>
          <div><span>Final samples</span><strong>{restricted ? 'Restricted' : activeMetrics.sampleCount ?? 0}</strong></div>
          <div><span>Current λ</span><strong>{lambda}</strong></div>
        </div>
      </section>

      <div className="metric-grid four">
        <Metric
          label="Final attributed samples"
          value={restricted ? 'Restricted' : String(activeMetrics.sampleCount ?? 0)}
          detail={restricted ? 'Administrator access required' : dataSource === 'dataset' ? 'Dataset calibration results' : 'Server-reported partition'}
          icon="activity"
        />
        <Metric label="Target α" value={alpha} detail="Risk target" icon="shield" />
        <Metric label="Current λ" value={lambda} detail="Risk parameter" icon="calibur" tone="positive" />
        <Metric label="State-space estimate" value={stateSpend} detail="Configured state components" icon="server" />
      </div>

      {!restricted && apiMetrics?.conformalCoverage.availability !== 'available' && (
        <InlineNotice tone="info" title="Coverage is not asserted">
          {metricReason(apiMetrics?.conformalCoverage)}
        </InlineNotice>
      )}

      {!restricted && (activeMetrics.sampleCount ?? 0) === 0 && (
        <Panel eyebrow="Cold start" title="Why the margin is not a fitted model">
          <div className="cold-start">
            <span className="cold-start-icon"><Icon name="shield" size={24} /></span>
            <div>
              <p>
                There are <b>no final, attributed settlement samples</b> for this resource class. CALIBUR therefore
                reports <CodeChip>NO_CALIBRATION</CodeChip> in decisions rather than fabricating a distribution,
                coverage number, or confidence interval.
              </p>
              <p>
                As settled actuals accumulate, rolling-quantile and split-conformal candidates can be evaluated.
                Until then, switching the active policy would be an unreviewed change, so this view is read-only.
              </p>
            </div>
          </div>
        </Panel>
      )}

      <Panel
        eyebrow="Candidate policies"
        title="Margin policy evidence"
        description="Candidates are displayed for comparison only and cannot change production policy from this view."
        action={<button className="button secondary small" onClick={onRefresh} disabled={loading}><Icon name="refresh" size={15} />Refresh</button>}
      >
        {policies.length ? (
          <div className="policy-grid">
            {policies.map(policy => {
              const active = policy.id === 'rolling_quantile';
              return <PolicyCard key={policy.id ?? policy.name} policy={policy} active={active} dataSource={dataSource} />;
            })}
          </div>
        ) : restricted ? (
          <EmptyState
            icon="lock"
            title="Policy samples restricted"
            body="The calibration summary requires administrator access. No policy rows are inferred in the UI."
          />
        ) : (
          <EmptyState
            icon="calibur"
            title="No policy rows returned"
            body="No policy rows are available from the backend or dataset. No fallback policy is fabricated."
          />
        )}
      </Panel>

      <div className="overview-split">
        <Panel
          eyebrow="State estimator"
          title="Kalman model availability"
          action={<Provenance tone={estimator?.availability === 'available' ? 'info' : 'warning'}>{estimator?.availability ?? 'dataset fallback'}</Provenance>}
        >
          {estimator?.availability === 'available' ? (
            <>
              <div className="release-head">
                <StatusBadge value={estimator.value.status} />
                <span className="badge tone-neutral">{estimator.value.observationCount} observations</span>
              </div>
              {estimator.value.state && estimator.value.state.length > 0 ? (
                <div className="telemetry-list">
                  {estimator.value.state.map((value, index) => (
                    <div className="telemetry-row" key={estimator.value.stateDefinition[index] ?? index}>
                      <span className="telemetry-dot" />
                      <div>
                        <strong>{value}</strong>
                        <small>{estimator.value.stateDefinition[index] ?? `state[${index}]`}</small>
                      </div>
                      <time>variance {estimator.value.covariance?.[index]?.[index] ?? 'unavailable'}</time>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">The estimator returned no state vector.</p>
              )}
              <p className="source-note">
                Decision at {formatDateTime(estimator.value.decisionAt)} · records used: {estimator.value.usedRecordIds.join(', ') || 'none'}.
              </p>
              <details className="limitations">
                <summary>Assumptions and skipped observations</summary>
                <ul>
                  {estimator.value.assumptions.map(item => <li key={item}>{item}</li>)}
                  {estimator.value.skipped.map(item => <li key={`${item.recordId}-${item.reason}`}>{item.recordId}: {item.reason}</li>)}
                </ul>
              </details>
            </>
          ) : (
            <div className="unavailable-state">
              <span><Icon name="lock" size={20} /></span>
              <div>
                <strong>Estimator {dataSource === 'dataset' ? 'from dataset' : 'unavailable'}</strong>
                <p>{dataSource === 'dataset' ? 'Dataset-backed state-space estimate is displayed above.' : estimator?.reason ?? 'Estimator is unavailable.'}</p>
              </div>
            </div>
          )}
        </Panel>

        <Panel eyebrow="Release policy" title="Early release evidence">
          {release ? (
            <>
              <div className="release-head">
                <StatusBadge value={release.status} />
                <span className="badge tone-neutral">mutation {release.mutation}</span>
              </div>
              <p className="panel-copy">{release.reason.replace(/_/g, ' ').toLowerCase()}</p>
              <p className="source-note">
                Eligible observations: {release.eligibleObservationCount}. A release is never planned from an
                unvalidated timestamp.
              </p>
              {release.releaseAt && <p className="source-note">Proposed release: {formatDateTime(release.releaseAt)}</p>}
              {release.limitations.length > 0 && (
                <ul className="limitations-list">
                  {release.limitations.map(item => <li key={item}>{item}</li>)}
                </ul>
              )}
            </>
          ) : (
            <EmptyState
              icon="clock"
              title="No release proposal"
              body="No release plan is available from the backend. No release is inferred in the UI."
            />
          )}
        </Panel>
      </div>

      <Panel eyebrow="Estimate comparison" title="Estimated versus reconciled spend">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Timestamp</th><th>Estimated</th><th>Reconciled</th><th>Raw Cost Explorer</th><th>Provenance</th><th>Source</th></tr>
            </thead>
            <tbody>
              {spendSeriesItems.map(point => (
                <tr key={`${point.timestamp}-${point.provenance}`}>
                  <td>{formatDateTime(point.timestamp)}</td>
                  <td>{formatUsd(point.estimatedMicro)}</td>
                  <td>{point.reconciledMicro == null ? '—' : formatUsd(point.reconciledMicro)}</td>
                  <td>{point.rawCostExplorerMicro == null ? '—' : formatUsd(point.rawCostExplorerMicro)}</td>
                  <td><span className="mono">{point.provenance}</span></td>
                  <td><span className="data-badge {dataSource}">{dataSource === 'dataset' ? 'Dataset' : 'Backend'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="source-note">
          {dataSource === 'dataset'
            ? 'Spend series values are dataset-backed calibration results. Backend-reported reconciled values are shown when available.'
            : 'Spend series values are backend-reported. Dataset-backed calibration results are available as a fallback.'}
        </p>
      </Panel>

      <Panel eyebrow="Lambda history" title="Risk parameter trajectory">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Timestamp</th><th>Lambda</th><th>Provenance</th><th>Source</th></tr>
            </thead>
            <tbody>
              {lambdaHistoryItems.map(entry => (
                <tr key={entry.timestamp}>
                  <td>{formatDateTime(entry.timestamp)}</td>
                  <td>{entry.lambda.toFixed(2)}</td>
                  <td><span className="mono">{entry.provenance}</span></td>
                  <td><span className="data-badge {dataSource}">{dataSource === 'dataset' ? 'Dataset' : 'Backend'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel eyebrow="Audit" title="What this view can and cannot do">
        <dl className="kv-grid compact">
          <div className="kv"><dt>Can change reserve policy</dt><dd>No. Read-only view.</dd></div>
          <div className="kv"><dt>Can mutate AWS</dt><dd>No. The frontend never creates AWS resources.</dd></div>
          <div className="kv"><dt>Can change the ledger</dt><dd>No. Only admission and lifecycle paths do that.</dd></div>
          <div className="kv"><dt>Active policy</dt><dd>{(activePolicy as CaliburnPolicy & { id?: string }).id ?? 'none'}</dd></div>
        </dl>
      </Panel>
    </div>
  );
}

function PolicyCard({ policy, active, dataSource }: { policy: CaliburnPolicy & { id?: string }; active: boolean; dataSource: DataBadge }) {
  const multiplier = policy.multiplier ?? policy.multiplier;
  return (
    <article className={`policy-card ${active ? 'active' : ''}`.trim()}>
      <header>
        <div>
          <span className="eyebrow">{active ? 'Reported active' : 'Candidate'}</span>
          <h3>{titleCase(policy.id ?? policy.name)}</h3>
        </div>
        <span className="policy-multiplier">{multiplier == null ? '—' : `${Number(multiplier).toFixed(2)}×`}</span>
        <span className={`data-badge ${dataSource}`}>{dataSource === 'dataset' ? 'Dataset' : 'Backend'}</span>
      </header>
      <dl>
        <div><dt>Samples</dt><dd>{policy.sampleCount ?? '—'}</dd></div>
        <div><dt>Mean ratio</dt><dd>{policy.meanRatio == null ? '—' : Number(policy.meanRatio).toFixed(3)}</dd></div>
        <div><dt>P95 ratio</dt><dd>{policy.p95Ratio == null ? '—' : Number(policy.p95Ratio).toFixed(3)}</dd></div>
        <div><dt>Coverage</dt><dd>{policy.observedCoverage == null ? '—' : `${(Number(policy.observedCoverage) * 100).toFixed(1)}%`}</dd></div>
      </dl>
    </article>
  );
}

function metricValue<T>(value: MetricValue<T> | undefined): string {
  if (!value) return 'Unavailable';
  return value.availability === 'available' ? String(value.value) : 'Unavailable';
}

function metricReason<T>(value: MetricValue<T> | undefined): string {
  if (!value) return 'This metric is not reported by the connected API.';
  return value.availability === 'available' ? 'Available.' : value.reason;
}
