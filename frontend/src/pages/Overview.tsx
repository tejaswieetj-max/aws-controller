import type { Budget, Reservation } from '../types';
import { formatDate, pluralize } from '../lib/format';
import { Icon } from '../components/Icon';
import { RequestTable } from '../components/RequestTable';
import {
  EmptyState,
  InlineNotice,
  LoadingPanel,
  Metric,
  Money,
  Panel,
  StatusBadge,
  UtilizationRing,
} from '../components/ui';

export function OverviewPage({
  budget,
  reservations,
  synthetic,
  mockEngine,
  loading,
  error,
  onNewRequest,
  onOpenRequests,
  onOpenRequest,
}: {
  budget: Budget | null;
  reservations: Reservation[];
  synthetic: boolean;
  mockEngine: boolean;
  loading: boolean;
  error: string | null;
  onNewRequest: () => void;
  onOpenRequests: () => void;
  onOpenRequest: (reservation: Reservation) => void;
}) {
  const cap = budget?.budget_micro ?? 0;
  const available = budget?.available_micro ?? 0;
  const reserved = budget?.reserved_micro ?? 0;
  const spent = budget?.spent_micro ?? 0;
  const used = reserved + spent;
  const utilization = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  const openCount = reservations.filter(row => ['HELD', 'ACTIVE'].includes(row.status)).length;
  const recent = reservations.slice(0, 5);

  if (loading && !budget && reservations.length === 0) {
    return <div className="page-grid"><LoadingPanel rows={5} label="Loading budget overview" /></div>;
  }

  return (
    <div className="page-stack">
      {error && <InlineNotice tone="danger" title="Some account data could not be refreshed">{error}</InlineNotice>}

      {!budget && !loading ? (
        <Panel>
          <EmptyState
            icon="budgets"
            title="No budget for this period"
            body="This team has no monthly budget yet. An administrator must create one before a request can reserve funds."
            action={<button className="button primary" onClick={onOpenRequests}>Review requests</button>}
          />
        </Panel>
      ) : (
        <section className="balance-hero">
          <div className="balance-copy">
            <span className="eyebrow light">Available to reserve · {budget?.period ?? 'current period'}</span>
            <strong className="balance-amount"><Money micro={available} /></strong>
            <p>
              of <Money micro={cap} /> monthly application budget
              {budget?.updated_at ? ` · updated ${formatDate(budget.updated_at)}` : ''}
            </p>
            <div className="hero-actions">
              <button className="button accent" onClick={onNewRequest}>
                <Icon name="plus" size={16} />New request
              </button>
              <button className="button ghost-light" onClick={onOpenRequests}>
                View activity <Icon name="arrow" size={15} />
              </button>
            </div>
          </div>
          <div className="balance-visual">
            <UtilizationRing percent={utilization} label="Budget utilized" />
            <div className="balance-legend">
              <span><i className="legend-dot available" />Available <b><Money micro={available} /></b></span>
              <span><i className="legend-dot reserved" />Reserved <b><Money micro={reserved} /></b></span>
              <span><i className="legend-dot spent" />Spent <b><Money micro={spent} /></b></span>
            </div>
          </div>
        </section>
      )}

      {budget && utilization >= 80 && (
        <InlineNotice tone={utilization >= 100 ? 'danger' : 'warning'} title={utilization >= 100 ? 'Budget fully committed' : 'Budget utilization above 80%'}>
          {utilization >= 100
            ? 'New requests will be denied until funds are released or the budget is increased by an administrator.'
            : 'Reserved and spent amounts are approaching the monthly cap. New requests may be denied by CALIBUR.'}
        </InlineNotice>
      )}

      <div className="metric-grid four">
        <Metric label="Monthly budget" value={<Money micro={cap} />} detail={budget?.period ?? 'No period'} icon="budgets" />
        <Metric label="Reserved" value={<Money micro={reserved} />} detail="Open holds" icon="lock" tone="warning" />
        <Metric label="Spent" value={<Money micro={spent} />} detail="Settled actuals" icon="activity" tone="positive" />
        <Metric label="Open requests" value={String(openCount)} detail={pluralize(reservations.length, 'request')} icon="requests" />
      </div>

      <div className="overview-split">
        <Panel
          eyebrow="Latest activity"
          title="Recent requests"
          action={<button className="text-button" onClick={onOpenRequests}>View all <Icon name="arrow" size={14} /></button>}
        >
          {recent.length ? (
            <RequestTable rows={recent} onOpen={onOpenRequest} compact />
          ) : (
            <EmptyState
              icon="requests"
              title="No requests yet"
              body="Create an estimate to see how CALIBUR prices a hold and how the ledger protects the budget."
              action={<button className="button primary" onClick={onNewRequest}>Start an estimate <Icon name="arrow" size={15} /></button>}
            />
          )}
        </Panel>

        <div className="side-stack">
          <Panel eyebrow="Environment" title={synthetic ? 'Synthetic demo provider' : mockEngine ? 'Offline mock engine' : 'Secure environment'}>
            <div className="environment-list">
              {synthetic ? (
                <>
                  <EnvironmentRow label="Data source" value="Browser fixtures" tone="warning" />
                  <EnvironmentRow label="Pricing" value="Fixture · unverified" tone="warning" />
                  <EnvironmentRow label="Ledger" value="In-memory only" tone="info" />
                  <EnvironmentRow label="AWS mutation" value="Disabled" tone="positive" />
                  <EnvironmentRow label="Region (priced)" value="ap-south-1" tone="muted" />
                </>
              ) : mockEngine ? (
                <>
                  <EnvironmentRow label="Data source" value="Client-side mock engine" tone="info" />
                  <EnvironmentRow label="Pricing" value="aws_pricelist_verified" tone="positive" />
                  <EnvironmentRow label="Ledger" value="Client-side state" tone="info" />
                  <EnvironmentRow label="AWS mutation" value="Disabled" tone="positive" />
                  <EnvironmentRow label="Region (priced)" value="ap-south-1" tone="muted" />
                </>
              ) : (
                <>
                  <EnvironmentRow label="Identity" value="Cognito verified" tone="positive" />
                  <EnvironmentRow label="Ledger" value="Server-authoritative" tone="info" />
                  <EnvironmentRow label="Provisioning" value="Gated by policy" tone="warning" />
                </>
              )}
            </div>
            <p className="source-note">
              {synthetic
                ? 'Balances and workflow states are browser fixtures. They are not AWS billing or CloudWatch evidence.'
                : mockEngine
                  ? 'Balances and workflow states are simulated in browser memory. Not AWS billing evidence.'
                  : 'Application budgets are not an AWS billing cap.'}
            </p>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function EnvironmentRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: 'info' | 'warning' | 'positive' | 'muted';
}) {
  return (
    <div className="environment-row">
      <span>{label}</span>
      <StatusBadge value={tone === 'muted' ? 'NEUTRAL' : tone === 'positive' ? 'OPEN' : tone === 'warning' ? 'PROVISIONAL' : 'RUNNING'} label={value} />
    </div>
  );
}
