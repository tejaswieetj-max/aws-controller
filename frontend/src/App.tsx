import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSpendctlAuth } from './auth';
import { getSpendctlRepository, isSyntheticDataMode, isMockEngineMode } from './api/factory';
import type { AdmissionRequestPayload, CaliburMetrics } from './api/repository';
import { clearPendingSubmission } from './idempotency.mjs';
import { groupList } from './lib/format';
import { loadLocalRequests } from './lib/local-requests';
import type { Alert, Budget, Reservation } from './types';
import { RequestDetail } from './components/RequestDetail';
import { LoadingScreen, Shell, pageMeta, type Page } from './components/Shell';
import { SignIn, SetupNeeded } from './components/SignIn';
import { InlineNotice } from './components/ui';
import { OverviewPage } from './pages/Overview';
import { EstimatePage } from './pages/Estimate';
import { RequestsPage } from './pages/Requests';
import { BudgetsPage } from './pages/Budgets';
import { AlertsPage } from './pages/Alerts';
import { CaliburPage } from './pages/Calibur';

type Notice = { message: string; tone: 'info' | 'success' | 'warning' | 'danger' };

const validPages = new Set<Page>(Object.keys(pageMeta) as Page[]);

export function App() {
  const auth = useSpendctlAuth();
  const synthetic = isSyntheticDataMode();
  const mockEngine = isMockEngineMode();
  const [page, setPage] = useState<Page>(() => initialPage());
  const [budget, setBudget] = useState<Budget | null>(null);
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [reservationsError, setReservationsError] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<CaliburMetrics | null>(null);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const claims = auth.user?.profile ?? {};
  // Synthetic mode is an explicitly selected browser-only provider. Real mode
  // always derives team scope from the authenticated JWT claim.
  const team = (synthetic || mockEngine) ? 'alpha-development' : String(claims['custom:team'] ?? claims.team ?? '');
  const groups = groupList(claims['cognito:groups']);
  const isDeveloper = synthetic || mockEngine || groups.includes('developers');
  const isAdmin = synthetic || mockEngine || groups.includes('admins');
  const canViewInsights = isDeveloper || isAdmin;
  const displayName = String(claims.name ?? claims.email ?? 'Signed in');
  const email = String(claims.email ?? 'Authenticated session');
  const role = isAdmin ? 'Administrator' : isDeveloper ? 'Developer' : 'User';
  const initials = initialsFrom(String(claims.name ?? claims.email ?? 'U'));

const mergedReservations: Reservation[] = useMemo(
  () => {
    const localRequests = loadLocalRequests();
    const liveReservationIds = new Set(reservations.map((row) => row.reservation_id));
    const localForTeam = localRequests.filter((row) => row.team === team);
    const localOnly = localForTeam.filter((row) => !liveReservationIds.has(row.reservation_id));

    const localAsReservation: Reservation[] = localOnly.map((row) => ({
      reservation_id: row.reservation_id,
      team: row.team,
      status: row.status,
      created_at: row.created_at,
      reserved_micro: row.reserved_micro,
      estimate_micro: row.estimate_micro,
      resource: row.resource,
      purpose: row.purpose,
      region: row.region,
      planned_hours: row.planned_hours,
      price_source: row.price_source,
      margin_policy: row.margin_policy,
      margin: row.margin,
      workflow_start_state: row.workflow_start_state,
      workflow_execution_status: row.workflow_execution_status,
    }));
    return [...reservations, ...localAsReservation];
  },
  [reservations, team],
);

const adjustedBudget: Budget | null = useMemo(
  () => {
    if (!budget) return null;
    const localRequests = loadLocalRequests();
    const liveReservationIds = new Set(reservations.map((row) => row.reservation_id));
    const localForTeam = localRequests.filter((row) => row.team === team);
    const localOnly = localForTeam.filter((row) => !liveReservationIds.has(row.reservation_id));
    const localReservedTotal = localOnly.reduce((sum, row) => sum + row.reserved_micro, 0);
    const localRequestCount = localOnly.length;

    return {
      ...budget,
      available_micro: Math.max(0, budget.available_micro - localReservedTotal),
      reserved_micro: budget.reserved_micro + localReservedTotal,
      open_reservations: (budget.open_reservations ?? 0) + localRequestCount,
    };
  },
  [budget, reservations, team],
);

  const repository = useMemo(
    () =>
      getSpendctlRepository({
        baseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
        getToken: async () => {
          if (!auth.user?.id_token) throw new Error('Your session expired. Sign in again.');
          return auth.user.id_token;
        },
      }),
    [auth.user?.id_token],
  );

  useEffect(() => {
    const onUnauthorized = () => {
      void auth.removeUser();
      setNotice({ message: 'Your session expired. Sign in again to continue.', tone: 'warning' });
    };
    window.addEventListener('spendctl:unauthorized', onUnauthorized);
    return () => window.removeEventListener('spendctl:unauthorized', onUnauthorized);
  }, [auth.removeUser]);

  useEffect(() => {
    const onHashChange = () => setPage(initialPage());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  useEffect(() => {
    if (synthetic || mockEngine) return;
    if ((page === 'alerts' && !isAdmin) || (page === 'calibur' && !canViewInsights)) {
      navigate('overview');
    }
  }, [canViewInsights, isAdmin, mockEngine, page, synthetic]);

  useEffect(() => {
    if ((!synthetic && !mockEngine && !auth.isAuthenticated) || !team) return;
    let active = true;
    setFetching(true);
    setBudgetError(null);
    setReservationsError(null);

    const budgetTask = repository
      .getBudgets(team)
      .then(rows => {
        if (active) setBudget(rows[0] ?? null);
      })
      .catch(cause => {
        if (active) setBudgetError(errorMessage(cause));
      });

    const reservationTask = repository
      .getLedgerEntries()
      .then(rows => {
        if (active) setReservations([...rows]);
      })
      .catch(cause => {
        if (active) setReservationsError(errorMessage(cause));
      });

    const tasks: Promise<unknown>[] = [budgetTask, reservationTask];
    if (page === 'alerts' && isAdmin) {
      setAlertsError(null);
      tasks.push(
        repository
          .getAlerts()
          .then(rows => {
            if (active) setAlerts([...rows]);
          })
          .catch(cause => {
            if (active) setAlertsError(errorMessage(cause));
          }),
      );
    }
    if (page === 'calibur' && canViewInsights) {
      setMetricsError(null);
      tasks.push(
        repository
          .getCaliburMetrics()
          .then(value => {
            if (active) setMetrics(value);
          })
          .catch(cause => {
            if (active) setMetricsError(errorMessage(cause));
          }),
      );
    }

    void Promise.allSettled(tasks).finally(() => {
      if (active) setFetching(false);
    });
    return () => {
      active = false;
    };
    }, [auth.isAuthenticated, canViewInsights, isAdmin, mockEngine, page, repository, synthetic, team, version]);

  const navigate = useCallback((next: Page) => {
    setPage(next);
    if (window.location.hash !== `#${next}`) {
      window.history.replaceState(null, '', `#${next}`);
    }
  }, []);

  const notify = useCallback(
    (message: string, tone: Notice['tone'] = 'info') => setNotice({ message, tone }),
    [],
  );

  const refresh = useCallback(() => {
    setNotice(null);
    setVersion(value => value + 1);
  }, []);

  const openRequest = useCallback((reservation: Reservation) => {
    setSelectedRequest(reservation.reservation_id);
  }, []);

  const handleSubmitted = useCallback(
    (reservation: Reservation) => {
      setSelectedRequest(reservation.reservation_id);
      navigate('requests');
      setVersion(value => value + 1);
    },
    [navigate],
  );

  if (!(synthetic || mockEngine) && (auth.isLoading || auth.activeNavigator)) {
    return <LoadingScreen label="Loading secure session…" />;
  }

  if (!(synthetic || mockEngine) && !auth.isAuthenticated) {
    if (!auth.isConfigured && !auth.error) return <SetupNeeded />;
    return <SignIn error={auth.error} onSignIn={() => void auth.signinRedirect()} />;
  }

  if (!team) {
    return (
      <main className="fullscreen">
        <section className="auth-card">
          <span className="brand-mark large">S</span>
          <span className="eyebrow">Identity incomplete</span>
          <h1>Team claim missing</h1>
          <p>Your verified session has no team claim. Ask an administrator to assign your team, then sign in again.</p>
          <button className="button primary" onClick={() => void auth.signoutRedirect()}>Sign out</button>
        </section>
      </main>
    );
  }

  const overviewError = budgetError && reservationsError
    ? `${budgetError} ${reservationsError}`
    : budgetError ?? reservationsError;
  const pageError = page === 'alerts' ? alertsError : page === 'calibur' ? metricsError : overviewError;

  return (
    <>
      <Shell
        page={page}
        onNavigate={navigate}
        team={team}
        displayName={displayName}
        email={email}
        role={role}
        initials={initials}
        isAdmin={isAdmin}
        canViewInsights={canViewInsights}
        synthetic={synthetic}
        mockEngine={mockEngine}
        onRefresh={refresh}
        refreshing={fetching}
        onSignOut={() => {
          if (!(synthetic || mockEngine)) {
            void auth.signoutRedirect();
            return;
          }
          // Mock engine and synthetic mode have no session to end. Reloading
          // discards the in-memory state and any unresolved submission.
          clearPendingSubmission(sessionStorage);
          window.location.reload();
        }}
      >
        {(synthetic || mockEngine) && (
          <InlineNotice tone="info" title="Offline mock engine">
            This session is running the client-side mock engine. Admission decisions, budget holds, and workflow
            states are simulated in browser memory. No Spendctl API, ledger, AWS resource, or research pipeline
            is being contacted.
          </InlineNotice>
        )}

        {notice && (
          <InlineNotice tone={notice.tone} onClose={() => setNotice(null)}>
            {notice.message}
          </InlineNotice>
        )}

        {page === 'overview' && (
          <OverviewPage
            budget={adjustedBudget}
            reservations={mergedReservations}
            synthetic={synthetic}
            mockEngine={mockEngine}
            loading={fetching}
            error={pageError}
            onNewRequest={() => navigate('estimate')}
            onOpenRequests={() => navigate('requests')}
            onOpenRequest={openRequest}
          />
        )}

        {page === 'estimate' && (
          <EstimatePage
            repository={repository}
            team={team}
            budget={adjustedBudget}
            isAdmin={isAdmin}
            synthetic={synthetic}
            mockEngine={mockEngine}
            onSubmitted={handleSubmitted}
            onNotice={notify}
          />
        )}

        {page === 'requests' && (
          <RequestsPage
            rows={mergedReservations}
            loading={fetching}
            error={reservationsError}
            onOpen={openRequest}
            onNewRequest={() => navigate('estimate')}
            onRefresh={refresh}
            refreshing={fetching}
          />
        )}

        {page === 'budgets' && (
          <BudgetsPage
            repository={repository}
            team={team}
            budget={adjustedBudget}
            loading={fetching}
            error={budgetError}
            isAdmin={isAdmin}
            onRefresh={refresh}
            onNotice={notify}
          />
        )}

        {page === 'alerts' && isAdmin && (
          <AlertsPage
            alerts={alerts}
            loading={fetching}
            error={alertsError}
            onRefresh={refresh}
            refreshing={fetching}
            synthetic={synthetic}
            mockEngine={mockEngine}
          />
        )}

        {page === 'calibur' && canViewInsights && (
          <CaliburPage
            metrics={metrics}
            loading={fetching}
            error={metricsError}
            onRefresh={refresh}
            synthetic={synthetic}
            mockEngine={mockEngine}
          />
        )}
      </Shell>

      <RequestDetail
        repository={repository}
        reservationId={selectedRequest}
        budget={budget}
        synthetic={synthetic}
        mockEngine={mockEngine}
        onClose={() => setSelectedRequest(null)}
        onChanged={() => setVersion(value => value + 1)}
        onNotice={notify}
      />
    </>
  );
}

function initialPage(): Page {
  const hash = window.location.hash.replace(/^#/, '') as Page;
  return validPages.has(hash) ? hash : 'overview';
}

function initialsFrom(value: string): string {
  const parts = value.split(/[\s@._-]+/).filter(Boolean);
  const letters = parts.slice(0, 2).map(part => part.charAt(0).toUpperCase()).join('');
  return letters || 'U';
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return 'The account data could not be loaded.';
}
