import { useState, type FormEvent } from 'react';
import type { SpendctlRepository } from '../api/repository';
import type { Budget } from '../types';
import { formatDate } from '../lib/format';
import { Icon } from '../components/Icon';
import {
  EmptyState,
  InlineNotice,
  KeyValue,
  LoadingPanel,
  Metric,
  Money,
  Panel,
  StatusBadge,
} from '../components/ui';

export function BudgetsPage({
  repository,
  team,
  budget,
  loading,
  error,
  isAdmin,
  onRefresh,
  onNotice,
}: {
  repository: SpendctlRepository;
  team: string;
  budget: Budget | null;
  loading: boolean;
  error: string | null;
  isAdmin: boolean;
  onRefresh: () => void;
  onNotice: (message: string, tone?: 'info' | 'success' | 'warning' | 'danger') => void;
}) {
  const [amount, setAmount] = useState('250.00');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const cap = budget?.budget_micro ?? 0;
  const available = budget?.available_micro ?? 0;
  const reserved = budget?.reserved_micro ?? 0;
  const spent = budget?.spent_micro ?? 0;
  const percent = (value: number) => (cap > 0 ? Math.max(0, Math.min(100, (value / cap) * 100)) : 0);

  const createBudget = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setFormError(null);
    try {
      const created = await repository.createBudget(team, amount.trim());
      onNotice(`Budget for ${team} is now ${created.period}. Existing budgets are never reset by the client.`, 'success');
      onRefresh();
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : 'The budget could not be created.');
    } finally {
      setCreating(false);
    }
  };

  if (loading && !budget) return <LoadingPanel rows={5} label="Loading team budget" />;

  return (
    <div className="page-stack">
      {error && <InlineNotice tone="danger" title="Budget could not be refreshed">{error}</InlineNotice>}

      {budget ? (
        <>
          <section className="budget-hero">
            <div>
              <span className="eyebrow light">Monthly application budget · {budget.period}</span>
              <strong><Money micro={cap} /></strong>
              <p>
                Team <b>{team}</b> · version {budget.version ?? '—'} · status{' '}
                {budget.status ? <StatusBadge value={budget.status} /> : '—'}
              </p>
            </div>
            <div className="budget-hero-note">
              <Icon name="shield" size={22} />
              <div>
                <strong>Ledger invariant holds</strong>
                <span>Available + reserved + spent equals the cap in integer micro-USD.</span>
              </div>
            </div>
          </section>

          <Panel eyebrow="Allocation" title="Where the budget sits">
            <div className="allocation-bar" role="img" aria-label="Budget allocation">
              <span className="available" style={{ width: `${percent(available)}%` }} title={`Available ${formatPercent(percent(available))}`} />
              <span className="reserved" style={{ width: `${percent(reserved)}%` }} title={`Reserved ${formatPercent(percent(reserved))}`} />
              <span className="spent" style={{ width: `${percent(spent)}%` }} title={`Spent ${formatPercent(percent(spent))}`} />
            </div>
            <div className="allocation-legend">
              <span><i className="legend-dot available" />Available <b><Money micro={available} /></b><small>{formatPercent(percent(available))}</small></span>
              <span><i className="legend-dot reserved" />Reserved <b><Money micro={reserved} /></b><small>{formatPercent(percent(reserved))}</small></span>
              <span><i className="legend-dot spent" />Spent <b><Money micro={spent} /></b><small>{formatPercent(percent(spent))}</small></span>
            </div>

            <div className="invariant">
              <div><span>Available</span><b><Money micro={available} /></b></div>
              <span className="operator">+</span>
              <div><span>Reserved</span><b><Money micro={reserved} /></b></div>
              <span className="operator">+</span>
              <div><span>Spent</span><b><Money micro={spent} /></b></div>
              <span className="operator">=</span>
              <div className="total"><span>Cap</span><b><Money micro={cap} /></b></div>
            </div>
          </Panel>

          <div className="metric-grid four">
            <Metric label="Available" value={<Money micro={available} />} detail="Ready to reserve" icon="unlock" tone="positive" />
            <Metric label="Reserved" value={<Money micro={reserved} />} detail={budget.open_reservations == null ? 'Open holds' : `${budget.open_reservations} open holds`} icon="lock" tone="warning" />
            <Metric label="Spent" value={<Money micro={spent} />} detail="Final attributed actuals" icon="activity" />
            <Metric label="Open reservations" value={budget.open_reservations == null ? '—' : String(budget.open_reservations)} detail="In the ledger" icon="requests" />
          </div>

          <div className="overview-split">
            <Panel eyebrow="Ledger record" title="Budget metadata">
              <dl className="kv-grid compact">
                <KeyValue label="Team" value={team} />
                <KeyValue label="Period" value={budget.period} />
                <KeyValue label="Status" value={budget.status ?? '—'} />
                <KeyValue label="Version" value={String(budget.version ?? '—')} />
                <KeyValue label="Created" value={formatDate(budget.created_at)} />
                <KeyValue label="Last updated" value={formatDate(budget.updated_at)} />
              </dl>
              <p className="source-note">
                This is the application admission ledger, not an AWS billing cap. Spendctl never resets an existing
                balance from this screen.
              </p>
            </Panel>

            <Panel eyebrow="Administrator" title="Budget changes">
              {isAdmin ? (
                <>
                  <InlineNotice tone="info" title="Existing budgets are protected">
                    A budget already exists for {budget.period}. To protect the ledger, this screen will not reset it.
                    Changes require a separate, reviewed administrative workflow.
                  </InlineNotice>
                  <button className="button secondary full" onClick={onRefresh}>
                    <Icon name="refresh" size={15} />Reload ledger balance
                  </button>
                </>
              ) : (
                <EmptyState
                  icon="lock"
                  title="Administrator access required"
                  body="Only an administrator can create or change a monthly budget."
                />
              )}
            </Panel>
          </div>
        </>
      ) : (
        <div className="overview-split">
          <Panel>
            <EmptyState
              icon="budgets"
              title="No budget for this period"
              body="A monthly budget must exist before Spendctl can reserve funds. Creating one does not overwrite any existing period."
            />
          </Panel>
          {isAdmin ? (
            <Panel eyebrow="Administrator action" title="Create this month's budget">
              <form className="form-stack" onSubmit={createBudget}>
                <label className="field">
                  <span>Budget in USD</span>
                  <input
                    value={amount}
                    onChange={event => setAmount(event.target.value)}
                    inputMode="decimal"
                    pattern="[0-9]+(\.[0-9]{1,6})?"
                    placeholder="250.00"
                    required
                  />
                  <small>Positive amount, up to six decimal places. Stored as integer micro-USD.</small>
                </label>
                {formError && <InlineNotice tone="danger" title="Budget not created">{formError}</InlineNotice>}
                <button className="button primary full" disabled={creating}>
                  {creating ? 'Creating…' : 'Create monthly budget'}
                </button>
              </form>
            </Panel>
          ) : (
            <Panel>
              <EmptyState
                icon="lock"
                title="Ask an administrator"
                body="Only an administrator can create the monthly budget for this team."
              />
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}

function formatPercent(value: number): string {
  return `${Math.round(value)}%`;
}
