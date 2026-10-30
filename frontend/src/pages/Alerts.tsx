import type { Alert } from '../types';
import { formatDateTime, titleCase } from '../lib/format';
import { Icon } from '../components/Icon';
import { EmptyState, InlineNotice, LoadingPanel, Panel } from '../components/ui';

export function AlertsPage({
  alerts,
  loading,
  error,
  onRefresh,
  refreshing,
  synthetic,
  mockEngine,
}: {
  alerts: Alert[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  refreshing: boolean;
  synthetic: boolean;
  mockEngine: boolean;
}) {
  return (
    <div className="page-stack">
      {error && <InlineNotice tone="danger" title="Alerts could not be refreshed">{error}</InlineNotice>}
      <Panel
        eyebrow="Administrator view"
        title={alerts.length ? `${alerts.length} alert${alerts.length === 1 ? '' : 's'}` : 'Alert feed'}
        description="Budget and lifecycle signals written by the control plane."
        action={
          <button className="button secondary small" onClick={onRefresh} disabled={refreshing}>
            {refreshing ? <span className="spinner small" aria-hidden="true" /> : <Icon name="refresh" size={15} />}Refresh
          </button>
        }
      >
        {loading && alerts.length === 0 ? (
          <LoadingPanel rows={4} label="Loading alerts" />
        ) : alerts.length ? (
          <div className="alert-list">
            {alerts.map(alert => (
              <article className="alert-row" key={alert.alert_id}>
                <span className="alert-icon"><Icon name="warning" size={18} /></span>
                <div className="alert-copy">
                  <div className="alert-title">
                    <strong>{titleCase(alert.alert_type ?? 'Alert')}</strong>
                    <span className="badge tone-neutral">recorded</span>
                  </div>
                  <p>{describeAlert(alert)}</p>
                  {alert.details && Object.keys(alert.details).length > 0 && (
                    <div className="alert-details">
                      {Object.entries(alert.details).map(([key, value]) => (
                        <span key={key}><b>{titleCase(key)}</b>{formatValue(value)}</span>
                      ))}
                    </div>
                  )}
                </div>
                <time dateTime={alert.created_at}>{formatDateTime(alert.created_at)}</time>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon="alerts"
            title="No alerts recorded"
            body={
              synthetic
                ? 'The synthetic provider has no alert records. This is an empty in-memory table, not a live AWS alarm state.'
                : mockEngine
                  ? 'The mock engine has no alert records. This is client-side simulated state.'
                  : 'The API returned no alert records for this account.'
            }
          />
        )}
      </Panel>
    </div>
  );
}

function describeAlert(alert: Alert): string {
  if (typeof alert.details?.message === 'string') return alert.details.message;
  if (typeof alert.details?.reason === 'string') return alert.details.reason;
  return 'An alert record was written by the control plane. Review the structured details below.';
}

function formatValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return '—';
  }
}
