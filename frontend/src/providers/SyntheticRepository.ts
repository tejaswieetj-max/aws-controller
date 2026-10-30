import budgetRows from '../mocks/budgets.json';
import admissionRows from '../mocks/admissions.json';
import metricFixture from '../mocks/calibur-metrics.json';
import telemetryFixture from '../mocks/telemetry.json';
import type { Alert, Budget, Estimate, HistoricalEstimate, Reservation } from '../types';
import type { AdmissionDecision, AdmissionRequestPayload, CaliburMetrics, ResourceId, SpendctlRepository, TeamId, TelemetryTraceResult } from '../api/repository';

const PROVENANCE = 'synthetic_frontend_fixture';
const withLatency = <T>(value: T): Promise<T> => new Promise((resolve) => setTimeout(() => resolve(value), 200));

/**
 * Browser-only fixture provider. It does not use fetch, AWS, or the production
 * ledger. Each instance owns cloned state so shared JSON source data stays fixed.
 */
export class SyntheticRepository implements SpendctlRepository {
  readonly mode = 'synthetic' as const;
  private budgets = budgetRows.map((row) => ({ ...row }));
  private reservations: Reservation[] = admissionRows
    .filter((row) => 'reservation_id' in row)
    .map((row) => ({
      reservation_id: row.reservation_id!, status: row.status!, created_at: row.created_at,
      reserved_micro: row.reserved_micro!, estimate_micro: row.estimate_micro!,
      resource: row.resource, workflow_start_state: row.workflow_start_state,
    }));
  private readonly decisions = new Map<string, AdmissionDecision>();

  async getBudgets(team?: TeamId): Promise<readonly Budget[]> {
    return withLatency(this.budgets.filter((row) => !team || row.team === team).map((row) => ({ ...row })));
  }

  async getLedgerEntries(): Promise<readonly Reservation[]> {
    return withLatency(this.reservations.map((row) => ({ ...row, resource: row.resource && { ...row.resource } })));
  }

  async estimate(payload: AdmissionRequestPayload): Promise<Estimate> {
    const compute_micro = payload.instance_type === 't3.small' ? 2_400_000 : 1_200_000;
    const storage_micro = payload.ebs_gb * 100_000;
    const total_micro = compute_micro + storage_micro;
    return withLatency({ compute_micro, storage_micro, total_micro, reserve_micro: Math.ceil(total_micro * 1.1), reserve_multiplier: 1.1, source: PROVENANCE });
  }

  async historicalEstimate(payload: AdmissionRequestPayload): Promise<HistoricalEstimate> {
    return withLatency({
      mode: 'HISTORICAL_ESTIMATE', status: 'NOT_ESTIMABLE',
      warning: 'Historical-data estimate — not live billing and not a verified current AWS charge.',
      reason: 'Historical FOCUS data is unavailable in the synthetic browser preview.',
      source: {}, uncertainty: { kind: 'unavailable', confidence_interval: false, interpretation: 'No historical observations are loaded in this preview.' },
      storage: { status: 'NOT_ESTIMABLE', reason: 'No historical storage observations are loaded.', requested_gb: payload.ebs_gb },
      total: { status: 'NOT_ESTIMABLE', amount_micro: null, reason: 'No historical compute or storage data is loaded.' },
      limitations: ['Synthetic preview data is not empirical evidence.'],
    });
  }

  async submitAdmissionRequest(payload: AdmissionRequestPayload, idempotencyKey: string): Promise<AdmissionDecision> {
    const prior = this.decisions.get(idempotencyKey);
    if (prior) return withLatency(prior);
    const estimate = await this.estimate(payload);
    const budget = this.budgets.find((row) => row.team === payload.team);
    if (!budget || (estimate.reserve_micro ?? 0) > budget.available_micro) {
      const denied: AdmissionDecision = { decision: 'Deny', denialCode: 'INSUFFICIENT_BUDGET', explanation: 'Synthetic example: the requested reserve exceeds the available balance.', dataSource: 'synthetic' };
      this.decisions.set(idempotencyKey, denied);
      return withLatency(denied);
    }
    const reservation: Reservation = {
      reservation_id: `synthetic-rsv-${this.reservations.length + 1}`, status: 'HELD',
      created_at: '2026-10-26T09:05:00Z', reserved_micro: estimate.reserve_micro ?? estimate.total_micro,
      estimate_micro: estimate.total_micro, resource: { instance_type: payload.instance_type, ebs_gb: payload.ebs_gb },
      workflow_start_state: 'SIMULATED',
    };
    this.reservations.unshift(reservation);
    budget.reserved_micro += reservation.reserved_micro;
    budget.available_micro -= reservation.reserved_micro;
    const approved: AdmissionDecision = { decision: 'Approve', reservationId: reservation.reservation_id, reservation, reservedCapitalMicro: reservation.reserved_micro, explanation: 'Synthetic browser-only approval; no ledger or cloud resource changed.', dataSource: 'synthetic' };
    this.decisions.set(idempotencyKey, approved);
    return withLatency(approved);
  }

  async getCaliburMetrics(): Promise<CaliburMetrics> {
    return withLatency({
      availability: 'available', calibrationAccess: 'available', sampleCount: 0, provenance: PROVENANCE,
      targetRiskAlpha: { availability: 'available', value: metricFixture.targetRiskAlpha },
      riskParameterLambda: { availability: 'available', value: metricFixture.currentLambda },
      stateSpaceSpendEstimate: { availability: 'available', value: metricFixture.stateSpaceSpendEstimateMicro },
      kalmanEstimate: { availability: 'unavailable', reason: 'Synthetic fixtures are not estimator observations.' },
      releasePlan: null,
      conformalCoverage: { availability: 'unavailable', reason: 'Illustrative data is not empirical coverage.' },
      spendSeries: metricFixture.spendSeries.map((point) => ({ ...point, provenance: PROVENANCE })),
      lambdaHistory: metricFixture.lambdaHistory.map((point) => ({ ...point, provenance: PROVENANCE })),
      releaseCurve: metricFixture.releaseCurve.map((point) => ({ ...point, provenance: PROVENANCE })),
    });
  }

  async getTelemetryTraces(resourceId: ResourceId): Promise<TelemetryTraceResult> {
    const trace = telemetryFixture[resourceId as keyof typeof telemetryFixture];
    if (!trace) return withLatency({ availability: 'unavailable', resourceId, reason: 'No synthetic trace exists for this resource ID.' });
    return withLatency({ availability: 'available', resourceId, points: trace.points.map((point) => ({ sourceTimestamp: point.sourceTimestamp, usageIntegral: point.usageIntegral, reservedUpperBound: point.reservedUpperBound, unit: trace.unit, provenance: PROVENANCE, thresholdCrossed: point.thresholdCrossed, earlyWarning: point.earlyWarning })) });
  }

  async getEvidenceChain(reservationId: ResourceId) {
    return { status: 'INCOMPLETE', missingLinks: ['synthetic data is not evidence'], links: {} };
  }

  async getRequest(id: string): Promise<Reservation> {
    const reservation = this.reservations.find((row) => row.reservation_id === id);
    if (!reservation) throw new Error('Synthetic reservation was not found.');
    return withLatency({ ...reservation });
  }

  async cancelRequest(id: string): Promise<{ request: Reservation; cleanupState: string }> {
    const request = await this.getRequest(id);
    request.status = 'CANCELLED';
    this.reservations = this.reservations.map((row) => row.reservation_id === id ? request : row);
    return withLatency({ request, cleanupState: 'SIMULATED' });
  }

  async createBudget(team: TeamId, _budgetUsd: string): Promise<Budget> {
    const budget = this.budgets.find((row) => row.team === team);
    if (!budget) throw new Error('Synthetic team was not found.');
    return withLatency({ ...budget });
  }

  async getAlerts(): Promise<readonly Alert[]> { return withLatency([]); }
}
