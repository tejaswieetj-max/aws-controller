import type { Reservation } from '../types';
import { formatDate, titleCase, truncateId } from '../lib/format';
import { Icon } from './Icon';
import { Money, StatusBadge } from './ui';

export function RequestTable({
  rows,
  onOpen,
  compact = false,
}: {
  rows: Reservation[];
  onOpen: (reservation: Reservation) => void;
  compact?: boolean;
}) {
  return (
    <div className="table-wrap">
      <table className={`request-table ${compact ? 'compact' : ''}`.trim()}>
        <thead>
          <tr>
            <th>Request</th>
            <th>Configuration</th>
            <th>Reserved</th>
            <th>Status</th>
            <th>Lifecycle</th>
            {!compact && <th aria-label="Actions" />}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr
              key={row.reservation_id}
              className="request-row"
              onClick={() => onOpen(row)}
              tabIndex={0}
              onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onOpen(row);
                }
              }}
              aria-label={`Open request ${row.reservation_id}`}
            >
              <td>
                <strong className="mono">{truncateId(row.reservation_id, 16, 5)}</strong>
                <small>{formatDate(row.created_at)} · {row.purpose || 'No purpose recorded'}</small>
              </td>
              <td>
                <span className="config-main">{row.resource?.instance_type ?? 'EC2'}</span>
                <small>{row.resource?.ebs_gb ?? '—'} GB gp3 · {row.planned_hours ?? '—'}h</small>
              </td>
              <td>
                <Money micro={row.reserved_micro} precise />
                <small>est. <Money micro={row.estimate_micro} precise /></small>
              </td>
              <td><StatusBadge value={row.status} /></td>
              <td>
                <span className="lifecycle-cell">
                  <span className={`lifecycle-dot ${row.status.toLowerCase()}`} />
                  {row.settlement_stage ? titleCase(row.settlement_stage) : titleCase(row.workflow_execution_status ?? row.workflow_start_state)}
                </span>
                {row.instance_id && <small className="mono">{truncateId(row.instance_id, 12, 4)}</small>}
              </td>
              {!compact && (
                <td className="row-action">
                  <span className="row-chevron"><Icon name="chevron" size={16} /></span>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
