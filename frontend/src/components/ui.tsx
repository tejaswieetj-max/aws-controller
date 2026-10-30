import type { CSSProperties, ReactNode } from 'react';
import { formatUsd, formatUsdPrecise } from '../api.mjs';
import { titleCase } from '../lib/format';
import { Icon, type IconName } from './Icon';

export function Money({
  micro,
  precise = false,
  className = '',
}: {
  micro: number | null | undefined;
  precise?: boolean;
  className?: string;
}) {
  return <span className={`money ${className}`.trim()}>{precise ? formatUsdPrecise(micro) : formatUsd(micro)}</span>;
}

export function Panel({
  eyebrow,
  title,
  description,
  action,
  children,
  className = '',
}: {
  eyebrow?: string;
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`.trim()}>
      {(eyebrow || title || action) && (
        <header className="panel-head">
          <div>
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
            {description && <p className="panel-description">{description}</p>}
          </div>
          {action && <div className="panel-action">{action}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Metric({
  label,
  value,
  detail,
  icon,
  tone = 'default',
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  icon?: IconName;
  tone?: 'default' | 'positive' | 'warning' | 'muted';
}) {
  return (
    <article className={`metric tone-${tone}`}>
      <div className="metric-label">
        {icon && <Icon name={icon} size={15} />}
        <span>{label}</span>
      </div>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </article>
  );
}

const statusTone: Record<string, string> = {
  HELD: 'warning',
  ACTIVE: 'positive',
  SETTLED: 'info',
  CANCELLED: 'muted',
  EXPIRED: 'muted',
  RELEASED: 'muted',
  APPROVE: 'positive',
  APPROVED: 'positive',
  DENY: 'danger',
  DENIED: 'danger',
  FINAL: 'info',
  PROVISIONAL: 'warning',
  COMPLETE: 'positive',
  SUCCEEDED: 'positive',
  RUNNING: 'info',
  SUBMITTING: 'info',
  PROVISIONING: 'warning',
  UNKNOWN: 'warning',
  INCOMPLETE: 'warning',
  OPEN: 'positive',
  CLOSED: 'muted',
  NOT_ESTIMABLE: 'warning',
  FAILED: 'danger',
  TIMED_OUT: 'danger',
  ABORTED: 'danger',
};

export function StatusBadge({ value, label }: { value?: string | null; label?: string }) {
  const tone = statusTone[String(value ?? '').toUpperCase()] ?? 'neutral';
  return (
    <span className={`badge tone-${tone}`}>
      <span className="badge-dot" aria-hidden="true" />
      {label ?? titleCase(value)}
    </span>
  );
}

export function DecisionBadge({ decision }: { decision?: string | null }) {
  const approved = String(decision).toUpperCase() === 'APPROVE';
  return (
    <span className={`decision ${approved ? 'approved' : 'denied'}`}>
      <Icon name={approved ? 'check' : 'close'} size={15} />
      {approved ? 'Approved by CALIBUR' : 'Denied by CALIBUR'}
    </span>
  );
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

export function Skeleton({ height = 18, width = '100%' }: { height?: number | string; width?: number | string }) {
  return <span className="skeleton" style={{ height, width }} aria-hidden="true" />;
}

export function LoadingPanel({ rows = 3, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="loading-panel" role="status" aria-label={label}>
      <Skeleton height={12} width="22%" />
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} height={16} width={`${88 - index * 9}%`} />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function EmptyState({
  icon = 'search',
  title,
  body,
  action,
}: {
  icon?: IconName;
  title: string;
  body: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Icon name={icon} size={22} />
      </span>
      <h3>{title}</h3>
      <p>{body}</p>
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function InlineNotice({
  tone = 'info',
  title,
  children,
  action,
  onClose,
}: {
  tone?: 'info' | 'success' | 'warning' | 'danger';
  title?: string;
  children: ReactNode;
  action?: ReactNode;
  onClose?: () => void;
}) {
  const icon: IconName = tone === 'success' ? 'check' : tone === 'danger' ? 'warning' : tone === 'warning' ? 'warning' : 'info';
  return (
    <div className={`inline-notice tone-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <span className="notice-icon"><Icon name={icon} size={17} /></span>
      <div className="notice-copy">
        {title && <strong>{title}</strong>}
        <div>{children}</div>
      </div>
      {action && <div className="notice-action">{action}</div>}
      {onClose && (
        <button className="icon-button quiet" aria-label="Dismiss message" onClick={onClose}>
          <Icon name="close" size={16} />
        </button>
      )}
    </div>
  );
}

export function UtilizationRing({ percent, label }: { percent: number; label?: string }) {
  const safe = Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0));
  return (
    <div className="ring-block">
      <div
        className="ring"
        style={{ '--progress': `${safe * 3.6}deg` } as CSSProperties}
        role="img"
        aria-label={`${Math.round(safe)} percent utilized`}
      >
        <div className="ring-inner">
          <strong>{Math.round(safe)}<small>%</small></strong>
        </div>
      </div>
      {label && <span>{label}</span>}
    </div>
  );
}

export function ProgressBar({ percent, tone = 'positive' }: { percent: number; tone?: 'positive' | 'warning' | 'danger' }) {
  const safe = Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0));
  return (
    <div className={`progress tone-${tone}`} aria-hidden="true">
      <span style={{ width: `${safe}%` }} />
    </div>
  );
}

export function KeyValue({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="kv">
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : undefined}>{value}</dd>
    </div>
  );
}

export function CodeChip({ children, title }: { children: ReactNode; title?: string }) {
  return <code className="code-chip" title={title}>{children}</code>;
}

export function ReasonCodes({ codes }: { codes: string[] }) {
  return (
    <div className="reason-codes" aria-label="CALIBUR reason codes">
      {codes.map(code => <span key={code} className="reason-chip">{code.replace(/_/g, ' ').toLowerCase()}</span>)}
    </div>
  );
}

export function Provenance({ children, tone = 'muted' }: { children: ReactNode; tone?: 'muted' | 'warning' | 'info' }) {
  return <span className={`provenance tone-${tone}`}>{children}</span>;
}

export function Divider() {
  return <div className="divider" role="presentation" />;
}
