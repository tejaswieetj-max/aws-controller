import type { ReactNode } from 'react';
import { Icon, type IconName } from './Icon';
import { Spinner } from './ui';

export type Page = 'overview' | 'estimate' | 'requests' | 'budgets' | 'alerts' | 'calibur';

export const pageMeta: Record<Page, { label: string; eyebrow: string; title: string; subtitle: string; icon: IconName }> = {
  overview: {
    label: 'Overview',
    eyebrow: 'Control centre',
    title: 'Budget overview',
    subtitle: 'Reserve capacity before infrastructure starts, then follow every decision to settlement.',
    icon: 'overview',
  },
  estimate: {
    label: 'New request',
    eyebrow: 'Plan a resource',
    title: 'Estimate a request',
    subtitle: 'Price a managed EC2 workload and review CALIBUR’s reservation before anything starts.',
    icon: 'estimate',
  },
  requests: {
    label: 'Requests',
    eyebrow: 'Activity',
    title: 'Requests & reservations',
    subtitle: 'Every hold, workflow step, and settlement in one auditable timeline.',
    icon: 'requests',
  },
  budgets: {
    label: 'Budgets',
    eyebrow: 'Monthly control',
    title: 'Team budget',
    subtitle: 'The application ledger is authoritative. Available, reserved, and spent always reconcile.',
    icon: 'budgets',
  },
  alerts: {
    label: 'Alerts',
    eyebrow: 'Administrator',
    title: 'Alerts',
    subtitle: 'Budget and lifecycle signals raised by the Spendctl control plane.',
    icon: 'alerts',
  },
  calibur: {
    label: 'CALIBUR',
    eyebrow: 'Policy evidence',
    title: 'CALIBUR reserve policy',
    subtitle: 'How the reservation margin is chosen, with cold-start evidence shown honestly.',
    icon: 'calibur',
  },
};

const navOrder: Page[] = ['overview', 'estimate', 'requests', 'budgets', 'alerts', 'calibur'];

export function Shell({
  page,
  onNavigate,
  team,
  displayName,
  email,
  role,
  initials,
  isAdmin,
  canViewInsights,
  synthetic,
  mockEngine,
  onRefresh,
  refreshing,
  onSignOut,
  children,
}: {
  page: Page;
  onNavigate: (page: Page) => void;
  team: string;
  displayName: string;
  email: string;
  role: string;
  initials: string;
  isAdmin: boolean;
  canViewInsights: boolean;
  synthetic: boolean;
  mockEngine: boolean;
  onRefresh: () => void;
  refreshing: boolean;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const meta = pageMeta[page];
  const visible = navOrder.filter(item => {
    if (item === 'alerts') return isAdmin;
    if (item === 'calibur') return canViewInsights;
    return true;
  });
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span className="brand-text">
            spendctl
            <small>financial control</small>
          </span>
        </div>

        <div className="workspace-card">
          <div className="workspace-head">
            <span className="avatar tiny">{initials}</span>
            <div>
              <strong>{displayName}</strong>
              <small>{role}</small>
            </div>
          </div>
          <div className="workspace-meta">
            <span className="team-chip"><span className="status-dot" />{team}</span>
            {synthetic && <span className="env-chip" title="Browser-only fixture provider. No AWS account is contacted."><Icon name="spark" size={12} />Demo</span>}
            {mockEngine && <span className="env-chip" title="Client-side mock engine. No AWS account is contacted."><Icon name="server" size={12} />Offline</span>}
          </div>
        </div>

        <nav className="nav" aria-label="Main navigation">
          <span className="nav-label">Workspace</span>
          {visible.map(item => (
            <button
              key={item}
              className={`nav-item ${page === item ? 'active' : ''}`.trim()}
              onClick={() => onNavigate(item)}
              aria-current={page === item ? 'page' : undefined}
            >
              <Icon name={pageMeta[item].icon} size={17} />
              <span>{pageMeta[item].label}</span>
              {item === 'alerts' && <span className="nav-admin">admin</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-note">
          <Icon name="shield" size={16} />
          <div>
            <strong>{synthetic ? 'Demo provider' : mockEngine ? 'Mock engine' : 'Secure workspace'}</strong>
            <span>{synthetic ? 'Browser fixtures only.' : mockEngine ? 'Client-side simulation.' : 'Cognito-verified session.'}</span>
          </div>
        </div>

        <div className="sidebar-foot">
          <div className="user-block">
            <span className="avatar">{initials}</span>
            <div className="user-copy">
              <strong>{displayName}</strong>
              <small>{email}</small>
            </div>
          </div>
          <button
            className="icon-button quiet"
            aria-label={synthetic ? 'Reset demo data' : mockEngine ? 'Reset mock state' : 'Sign out'}
            title={synthetic ? 'Reset demo data' : mockEngine ? 'Reset mock state' : 'Sign out'}
            onClick={onSignOut}
          >
            <Icon name={synthetic ? 'refresh' : mockEngine ? 'refresh' : 'logout'} size={17} />
          </button>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="page-heading">
            <span className="eyebrow">Spendctl / {meta.eyebrow}</span>
            <h1>{meta.title}</h1>
            <p>{meta.subtitle}</p>
          </div>
          <div className="top-actions">
            {synthetic || mockEngine ? (
              <span className="environment-pill showcase" title={synthetic ? 'Synthetic fixture provider. No Spendctl API, ledger, or AWS resource is used.' : 'Client-side mock engine. No AWS account is contacted.'}>
                <span className="pulse-dot" />
                Mode: {synthetic ? 'Synthetic Preview' : 'Mock Engine'}
              </span>
            ) : (
              <span className="environment-pill"><span className="status-dot" />Secure API</span>
            )}
            <button className="button secondary icon-only" onClick={onRefresh} disabled={refreshing} aria-label="Refresh data" title="Refresh data">
              {refreshing ? <span className="spinner small" aria-hidden="true" /> : <Icon name="refresh" size={17} />}
            </button>
          </div>
        </header>
        <main className="main" id="main-content">
          {children}
        </main>
        <footer className="footer">
          <span>Integer micro-USD ledger · {synthetic ? 'Browser-only fixtures' : mockEngine ? 'Client-side mock state' : 'Application budget, not an AWS billing cap'}</span>
          <span className="footer-sep" aria-hidden="true">·</span>
          <span>{synthetic ? 'Synthetic data is not empirical evidence.' : mockEngine ? 'Mock engine state is not empirical evidence.' : 'Server authorization remains authoritative.'}</span>
        </footer>
      </div>
    </div>
  );
}

export function LoadingScreen({ label }: { label: string }) {
  return (
    <main className="fullscreen">
      <div className="loading-card">
        <span className="brand-mark large" aria-hidden="true">S</span>
        <Spinner label={label} />
      </div>
    </main>
  );
}
