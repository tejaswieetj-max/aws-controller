import budgetRows from '../mocks/budgets.json';
import type { Alert, Budget, Estimate, HistoricalEstimate, Reservation } from '../types';
import type { AdmissionDecision, AdmissionRequestPayload, CaliburMetrics, ResourceId, SpendctlRepository, TeamId, TelemetryTraceResult } from '../api/repository';

const PROVENANCE = 'aws_pricelist_verified';
const RESERVE_MULTIPLIER = 1.10;
const RESERVE_MICRO = 260_000;
const ESTIMATE_MICRO = 236_364;

type TrackedReservation = Reservation & { _submittedAt?: number };

const initialBudgets: Budget[] = [
  {
    team: 'alpha',
    period: '2026-10',
    budget_micro: 10_000_000,
    available_micro: 10_000_000,
    reserved_micro: 0,
    spent_micro: 0,
    open_reservations: 0,
    version: 1,
    status: 'OPEN',
    created_at: new Date().toISOString(),
  },
  {
    team: 'beta',
    period: '2026-10',
    budget_micro: 10_000_000,
    available_micro: 10_000_000,
    reserved_micro: 0,
    spent_micro: 0,
    open_reservations: 0,
    version: 1,
    status: 'OPEN',
    created_at: new Date().toISOString(),
  },
];

/**
 * Client-side mock engine. It never calls fetch, AWS, or the production ledger.
 * All admission decisions, budget mutations, and workflow states live in browser
 * memory. Live AWS API handlers remain intact; switching the flag restores them.
 */
export class MockEngineRepository implements SpendctlRepository {
  readonly mode = 'mock-engine' as const;
  private budgets: Map<string, Budget> = new Map();
  private reservations: Map<string, TrackedReservation> = new Map();
  private readonly decisions = new Map<string, AdmissionDecision>();

  constructor() {
    for (const row of initialBudgets) {
      if (row.team) this.budgets.set(row.team, { ...row });
    }
  }

  async getBudgets(team?: TeamId): Promise<readonly Budget[]> {
    return [...this.budgets.values()]
      .filter((row) => !team || row.team === team)
      .map((row) => ({ ...row }));
  }

  async getLedgerEntries(): Promise<readonly Reservation[]> {
    return [...this.reservations.values()].map((row) => ({ ...row }));
  }

  async estimate(payload: AdmissionRequestPayload): Promise<Estimate> {
    const compute_micro = payload.instance_type === 't3.small' ? 472_727 : 236_364;
    const storage_micro = payload.ebs_gb * 29_545;
    const total_micro = compute_micro + storage_micro;
    const reserve_micro = Math.ceil(total_micro * RESERVE_MULTIPLIER);
    return {
      compute_micro,
      storage_micro,
      total_micro,
      reserve_micro,
      reserve_multiplier: RESERVE_MULTIPLIER,
      source: 'aws_pricelist_verified',
      price_source: 'aws_pricelist_verified',
      price_snapshot_id: `ps-${Date.now().toString(36)}`,
      region: payload.region,
      configuration: { instance_type: payload.instance_type, ebs_gb: payload.ebs_gb, duration_hours: payload.duration_hours },
    };
  }

  async historicalEstimate(payload: AdmissionRequestPayload): Promise<HistoricalEstimate> {
    return {
      mode: 'HISTORICAL_ESTIMATE',
      status: 'NOT_ESTIMABLE',
      warning: 'Historical FOCUS estimate is not available in offline mode.',
      reason: 'No historical billing rows are loaded in this session.',
      source: {},
      uncertainty: { kind: 'unavailable', confidence_interval: false, interpretation: 'No historical observations are available.' },
      storage: { status: 'NOT_ESTIMABLE', reason: 'No historical storage observations are available.', requested_gb: payload.ebs_gb },
      total: { status: 'NOT_ESTIMABLE', amount_micro: null, reason: 'No historical compute or storage data is loaded.' },
      limitations: ['Historical estimate requires live billing data.'],
    };
  }

  async submitAdmissionRequest(payload: AdmissionRequestPayload, idempotencyKey: string): Promise<AdmissionDecision> {
    const prior = this.decisions.get(idempotencyKey);
    if (prior) return prior;

    const estimate = await this.estimate(payload);
    const budget = this.budgets.get(payload.team);
    const reserveMicro = estimate.reserve_micro ?? RESERVE_MICRO;
    const budgetTeam = budget?.team ?? payload.team;

    if (!budget || reserveMicro > budget.available_micro) {
      const denied: AdmissionDecision = {
        decision: 'Deny',
        denialCode: 'INSUFFICIENT_BUDGET',
        explanation: 'The requested reserve exceeds the available budget balance.',
        dataSource: 'mock-engine',
      };
      this.decisions.set(idempotencyKey, denied);
      return denied;
    }

    budget.available_micro -= reserveMicro;
    budget.reserved_micro += reserveMicro;
    budget.open_reservations = (budget.open_reservations ?? 0) + 1;

    const now = Date.now();
    const reservationId = `rsv-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
    const reservation: TrackedReservation = {
      reservation_id: reservationId,
      team: payload.team,
      status: 'SUBMITTING',
      created_at: new Date(now).toISOString(),
      reserved_micro: reserveMicro,
      estimate_micro: total_micro(estimate),
      resource: { instance_type: payload.instance_type, ebs_gb: payload.ebs_gb },
      workflow_start_state: 'RESERVED',
      purpose: payload.purpose,
      region: payload.region,
      planned_hours: payload.duration_hours,
      price_source: 'aws_pricelist_verified',
      price_snapshot_id: estimate.price_snapshot_id,
      margin_policy: 'rolling_quantile',
      margin: '1.10x (Rolling Quantile - Dynamic)',
      calibur_proposal: {
        decision: 'APPROVE',
        reason_codes: ['WITHIN_BUDGET'],
        reservation_amount_micro: reserveMicro,
        point_cost_estimate_micro: total_micro(estimate),
        upper_cost_estimate_micro: Math.ceil(total_micro(estimate) * RESERVE_MULTIPLIER),
        available_budget_before_micro: budget.available_micro + reserveMicro,
        available_budget_after_micro: budget.available_micro,
        policy_version: 'rolling_quantile-v1',
        state_version: '1',
        decision_at: new Date(now).toISOString(),
        input_refs: [
          { source_id: 'aws-pricelist-ap-south-1', observed_at: new Date(now).toISOString(), source_file: 'aws_pricelist_2026-10.csv', semantics: 'On-Demand Linux t3 pricing, ap-south-1' },
        ],
        audit_reference: `audit-${reservationId}`,
      },
      budget_before_available_micro: budget.available_micro + reserveMicro,
      budget_before_reserved_micro: budget.reserved_micro - reserveMicro,
      budget_before_spent_micro: budget.spent_micro,
      _submittedAt: now,
    };

    this.reservations.set(reservationId, reservation);

    const approved: AdmissionDecision = {
      decision: 'Approve',
      reservationId,
      reservation: { ...reservation },
      reservedCapitalMicro: reserveMicro,
      explanation: 'CALIBUR approved the hold; the ledger recorded the reservation atomically.',
      dataSource: 'mock-engine',
    };
    this.decisions.set(idempotencyKey, approved);
    return approved;
  }

  async getCaliburMetrics(): Promise<CaliburMetrics> {
    return {
      availability: 'available',
      calibrationAccess: 'available',
      sampleCount: 42,
      provenance: PROVENANCE,
      targetRiskAlpha: { availability: 'available', value: 0.05 },
      riskParameterLambda: { availability: 'available', value: 1.10 },
      stateSpaceSpendEstimate: { availability: 'available', value: 31000000 },
      kalmanEstimate: {
        availability: 'available',
        value: {
          status: 'OK',
          state: [31000000, 1.10],
          covariance: [[900000000, 0], [0, 0.01]],
          stateDefinition: ['estimated_spend_micro', 'reserve_multiplier'],
          observationCount: 42,
          usedRecordIds: ['rec-2026-10-01', 'rec-2026-10-02', 'rec-2026-10-03'],
          skipped: [],
          assumptions: ['Rolling quantile margin with cold-start calibration.'],
          decisionAt: new Date().toISOString(),
        },
        observedAt: new Date().toISOString(),
      },
      earlyUpdates: null,
      evidenceByReservation: {},
      releasePlan: {
        status: 'HELD',
        reason: 'No early release criteria met.',
        releaseAt: null,
        retainedAmountMicro: null,
        limitations: ['Release requires final settlement evidence.'],
        eligibleObservationCount: 0,
        mutation: 'NONE',
      },
      conformalCoverage: { availability: 'available', value: 0.90 },
      spendSeries: [
        { timestamp: '2026-10-01T00:00:00Z', estimatedMicro: 12000000, reconciledMicro: 11000000, rawCostExplorerMicro: 12500000, provenance: PROVENANCE },
        { timestamp: '2026-10-15T00:00:00Z', estimatedMicro: 23000000, reconciledMicro: 21000000, rawCostExplorerMicro: 24500000, provenance: PROVENANCE },
        { timestamp: '2026-10-30T00:00:00Z', estimatedMicro: 31000000, reconciledMicro: 28000000, rawCostExplorerMicro: 33400000, provenance: PROVENANCE },
      ],
      lambdaHistory: [
        { timestamp: '2026-10-01T00:00:00Z', lambda: 1.08, provenance: PROVENANCE },
        { timestamp: '2026-10-15T00:00:00Z', lambda: 1.10, provenance: PROVENANCE },
        { timestamp: '2026-10-30T00:00:00Z', lambda: 1.12, provenance: PROVENANCE },
      ],
      releaseCurve: [
        { elapsedMinutes: 0, retainedCapitalFraction: 1, provenance: PROVENANCE },
        { elapsedMinutes: 30, retainedCapitalFraction: 0.8, provenance: PROVENANCE },
        { elapsedMinutes: 60, retainedCapitalFraction: 0.55, provenance: PROVENANCE },
      ],
    };
  }

  async getTelemetryTraces(resourceId: ResourceId): Promise<TelemetryTraceResult> {
    return {
      availability: 'available',
      resourceId,
      points: [
        { sourceTimestamp: new Date(Date.now() - 300_000).toISOString(), usageIntegral: 0.18, reservedUpperBound: 0.3, unit: 'vCPU-minutes', thresholdCrossed: false, earlyWarning: false, provenance: PROVENANCE },
        { sourceTimestamp: new Date(Date.now() - 240_000).toISOString(), usageIntegral: 0.24, reservedUpperBound: 0.3, unit: 'vCPU-minutes', thresholdCrossed: false, earlyWarning: false, provenance: PROVENANCE },
        { sourceTimestamp: new Date(Date.now() - 180_000).toISOString(), usageIntegral: 0.36, reservedUpperBound: 0.3, unit: 'vCPU-minutes', thresholdCrossed: true, earlyWarning: true, provenance: PROVENANCE },
      ],
    };
  }

  async getEvidenceChain(reservationId: ResourceId) {
    return {
      status: 'COMPLETE',
      missingLinks: [],
      links: {
        request: { available: true, requirement: 'Admission request record' },
        decision: { available: true, requirement: 'CALIBUR decision record' },
        ledger: { available: true, requirement: 'Budget ledger transaction' },
        provisioning: { available: true, requirement: 'Workflow provisioning state' },
      },
    };
  }

  async getRequest(id: string): Promise<Reservation> {
    const entry = this.reservations.get(id);
    if (!entry) throw new Error('Reservation was not found.');

    const elapsed = Date.now() - (entry._submittedAt ?? Date.now());
    let status = entry.status;
    if (elapsed < 1_000) status = 'SUBMITTING';
    else if (elapsed < 2_000) status = 'APPROVED';
    else if (elapsed < 4_000) status = 'PROVISIONING';
    else status = 'ACTIVE';

    return { ...entry, status };
  }

  async cancelRequest(id: string): Promise<{ request: Reservation; cleanupState: string }> {
    const request = await this.getRequest(id);
    const updated: TrackedReservation = { ...request, status: 'CANCELLED', cancelled_at: new Date().toISOString(), _submittedAt: (request as TrackedReservation)._submittedAt };
    this.reservations.set(id, updated);
    return { request: updated, cleanupState: 'RELEASED' };
  }

  async createBudget(team: TeamId, _budgetUsd: string): Promise<Budget> {
    const budget = this.budgets.get(team);
    if (!budget) throw new Error('Team was not found.');
    return { ...budget };
  }

  async getAlerts(): Promise<readonly Alert[]> { return []; }
}

function total_micro(estimate: Estimate): number {
  return estimate.total_micro ?? estimate.compute_micro + estimate.storage_micro;
}
