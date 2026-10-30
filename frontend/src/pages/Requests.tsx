import { useMemo, useState } from 'react';
import type { Reservation } from '../types';
import { pluralize } from '../lib/format';
import { Icon } from '../components/Icon';
import { RequestTable } from '../components/RequestTable';
import { EmptyState, InlineNotice, LoadingPanel, Panel } from '../components/ui';

const filters = ['ALL', 'HELD', 'ACTIVE', 'SETTLED'] as const;

export function RequestsPage({
  rows,
  loading,
  error,
  onOpen,
  onNewRequest,
  onRefresh,
  refreshing,
}: {
  rows: Reservation[];
  loading: boolean;
  error: string | null;
  onOpen: (reservation: Reservation) => void;
  onNewRequest: () => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const [filter, setFilter] = useState<(typeof filters)[number]>('ALL');
  const [query, setQuery] = useState('');

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter(row => {
      if (filter !== 'ALL' && row.status !== filter) return false;
      if (!needle) return true;
      return [row.reservation_id, row.purpose, row.resource?.instance_type, row.instance_id]
        .filter(Boolean)
        .some(value => String(value).toLowerCase().includes(needle));
    });
  }, [filter, query, rows]);

  const counts = useMemo(
    () => ({
      ALL: rows.length,
      HELD: rows.filter(row => row.status === 'HELD').length,
      ACTIVE: rows.filter(row => row.status === 'ACTIVE').length,
      SETTLED: rows.filter(row => row.status === 'SETTLED').length,
    }),
    [rows],
  );

  return (
    <div className="page-stack">
      {error && <InlineNotice tone="danger" title="Requests could not be refreshed">{error}</InlineNotice>}

      <Panel
        eyebrow="Team activity"
        title={pluralize(rows.length, 'request')}
        description="Select a request to inspect the CALIBUR decision, workflow lifecycle, settlement, and evidence chain."
        action={
          <div className="panel-actions">
            <button className="button secondary small" onClick={onRefresh} disabled={refreshing}>
              {refreshing ? <span className="spinner small" aria-hidden="true" /> : <Icon name="refresh" size={15} />}Refresh
            </button>
            <button className="button primary small" onClick={onNewRequest}><Icon name="plus" size={15} />New request</button>
          </div>
        }
      >
        <div className="table-toolbar">
          <div className="filter-tabs" role="tablist" aria-label="Filter requests">
            {filters.map(item => (
              <button
                key={item}
                role="tab"
                aria-selected={filter === item}
                className={filter === item ? 'filter-tab active' : 'filter-tab'}
                onClick={() => setFilter(item)}
              >
                {item === 'ALL' ? 'All' : item.charAt(0) + item.slice(1).toLowerCase()}
                <span>{counts[item]}</span>
              </button>
            ))}
          </div>
          <label className="search-box">
            <Icon name="search" size={15} />
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Search requests"
              aria-label="Search requests"
            />
          </label>
        </div>

        {loading && rows.length === 0 ? (
          <LoadingPanel rows={5} label="Loading requests" />
        ) : visible.length ? (
          <RequestTable rows={visible} onOpen={onOpen} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon="requests"
            title="No requests yet"
            body="Create an estimate to reserve budget and open a managed lifecycle."
            action={<button className="button primary" onClick={onNewRequest}>Start an estimate <Icon name="arrow" size={15} /></button>}
          />
        ) : (
          <EmptyState
            icon="search"
            title="No matching requests"
            body="Try a different status filter or clear the search text."
          />
        )}
      </Panel>
    </div>
  );
}
