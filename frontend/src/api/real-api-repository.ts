import { SpendctlApi } from '../api.mjs';
import type { Alert, Budget, Estimate, HistoricalEstimate, Reservation } from '../types';
import type {
  AdmissionDecision,
  AdmissionRequestPayload,
  CaliburMetrics,
  CalibrationPolicySummary,
  ResourceId,
  SpendctlRepository,
  TeamId,
  TelemetryTraceResult,
} from './repository';

export type RealApiRepositoryOptions = {
  baseUrl: string;
  getToken: () => Promise<string>;
};

/** Raised locally before a network request when required public configuration is absent. */
export class RepositoryConfigurationError extends Error {}
export class UnsupportedRepositoryOperationError extends Error {}

export class RealApiRepository implements SpendctlRepository {
  readonly mode = 'real-api' as const;
  private readonly api: SpendctlApi;
  private readonly baseUrl: string;

  constructor(options: RealApiRepositoryOptions) {
    this.baseUrl = options.baseUrl.trim();
    this.api = new SpendctlApi(this.baseUrl, options.getToken);
  }

  async getBudgets(team?: TeamId): Promise<readonly Budget[]> {
    this.requireBaseUrl();
    if (!team) throw new RepositoryConfigurationError('A team is required by the current budget API.');
    try {
      return [(await this.api.budget(team)).budget];
    } catch (error) {
      if (errorCode(error) === 'NOT_FOUND') return [];
      throw error;
    }
  }

  async getLedgerEntries(): Promise<readonly Reservation[]> {
    this.requireBaseUrl();
    const entries: Reservation[] = [];
    let nextToken: string | null | undefined;
    do {
      const page = await this.api.reservations(nextToken ?? undefined);
      entries.push(...page.items);
      nextToken = page.nextToken;
    } while (nextToken);
    return entries;
  }

  async submitAdmissionRequest(payload: AdmissionRequestPayload, idempotencyKey: string): Promise<AdmissionDecision> {
    this.requireBaseUrl();
    try {
      const result = await this.api.submit(payload, idempotencyKey);
      return {
        decision: 'Approve',
        reservationId: result.reservation_id,
        reservation: result.reservation,
        reservedCapitalMicro: result.reservation.reserved_micro,
        explanation: proposalExplanation(result.reservation.calibur_proposal),
        dataSource: 'real-api',
      };
    } catch (error) {
      const code = errorCode(error);
      if (code === 'INSUFFICIENT_BUDGET' || code === 'CALIBUR_POLICY_DENIED') {
        const message = errorMessage(error);
        const detail = errorDetail(error);
        const proposal = detail && typeof detail === 'object' && 'calibur_proposal' in detail
          ? detail.calibur_proposal as { reason_codes?: unknown }
          : undefined;
        const reasonCodes = Array.isArray(proposal?.reason_codes)
          ? proposal.reason_codes.filter((value): value is string => typeof value === 'string')
          : [];
        return {
          decision: 'Deny',
          denialCode: code,
          explanation: reasonCodes.length ? `${message} (${reasonCodes.join(', ')})` : message,
          dataSource: 'real-api',
        };
      }
      throw error;
    }
  }

  async getCaliburMetrics(): Promise<CaliburMetrics> {
    this.requireBaseUrl();
    const summary = await this.api.calibration().catch(error => {
      const code = errorCode(error);
      if (code === 'POLICY_DENIED' || code === 'INSUFFICIENT_PERMISSIONS' || code === 'FORBIDDEN') return null;
      throw error;
    });
    const stateEstimate = await this.api.stateEstimate().catch(error => {
      const code = errorCode(error);
      if (code === 'CALIBUR_ESTIMATOR_UNCONFIGURED') return null;
      if (code === 'INTERNAL_ERROR') return null;
      throw error;
    });
    const releasePlan = await this.api.releasePlan();
    const policies = summary ? summary.policies.filter(isPolicySummary) : [];
    return {
      availability: 'available',
      calibrationAccess: summary ? 'available' : 'restricted',
      sampleCount: summary ? summary.sample_count : 0,
      policies,
      conformalCoverage: summary ? { availability: 'unsupported', reason: 'The calibration summary endpoint does not expose a CALIBUR conformal-coverage metric.' } : { availability: 'unavailable', reason: 'Calibration summary is not accessible at this permission level.' },
      riskParameterLambda: summary ? { availability: 'unsupported', reason: 'The calibration summary endpoint does not expose CALIBUR lambda.' } : { availability: 'unavailable', reason: 'Calibration summary is not accessible at this permission level.' },
      stateSpaceSpendEstimate: summary ? { availability: 'unsupported', reason: 'The estimator exposes configured state components; no single component is asserted to be total spend.' } : { availability: 'unavailable', reason: 'Calibration summary is not accessible at this permission level.' },
      kalmanEstimate: stateEstimate?.estimate.status === 'OK'
        ? { availability: 'available', value: {
            status: stateEstimate!.estimate.status,
            state: stateEstimate!.estimate.state,
            covariance: stateEstimate!.estimate.covariance,
            stateDefinition: stateEstimate!.estimate.state_definition,
            observationCount: stateEstimate!.observation_count,
            usedRecordIds: stateEstimate!.estimate.used_record_ids,
            skipped: stateEstimate!.estimate.skipped.map(item => ({ recordId: item.record_id, reason: item.reason })),
            assumptions: stateEstimate!.estimate.assumptions,
            decisionAt: stateEstimate!.estimate.decision_at,
          }, observedAt: stateEstimate!.estimate.decision_at }
        : { availability: 'unavailable', reason: !stateEstimate ? 'The deployed estimator has no owner-reviewed CALIBUR_KALMAN_CONFIG or returned an unexpected server error.' : stateEstimate.estimate.status === 'INSUFFICIENT_DATA'
            ? 'No eligible, fresh observations are available for estimation.'
            : `Estimator status: ${stateEstimate.estimate.status}.` },
      earlyUpdates: null,
      evidenceByReservation: {},
      releasePlan: releasePlan ? {
        status: releasePlan.proposal.status,
        reason: releasePlan.proposal.reason,
        releaseAt: releasePlan.proposal.release_at,
        retainedAmountMicro: releasePlan.proposal.retained_amount_micro,
        limitations: releasePlan.proposal.limitations,
        eligibleObservationCount: releasePlan.eligible_observation_count,
        mutation: releasePlan.mutation,
      } : null,
    };
  }

  async getTelemetryTraces(resourceId: ResourceId): Promise<TelemetryTraceResult> {
    this.requireBaseUrl();
    const result = await this.api.telemetryTrace(resourceId);
    if (result.availability === 'available') {
      return { availability: 'available', resourceId, points: result.points };
    }
    return { availability: 'unavailable', resourceId, reason: result.reason ?? 'No trusted telemetry is available.' };
  }

  async getEvidenceChain(reservationId: ResourceId) {
    this.requireBaseUrl();
    const result = await this.api.evidenceChain(reservationId);
    return { status: result.status, missingLinks: result.missing_links, links: result.links };
  }

  async estimate(payload: AdmissionRequestPayload): Promise<Estimate> { this.requireBaseUrl(); return this.api.estimate(payload); }
  async historicalEstimate(payload: AdmissionRequestPayload): Promise<HistoricalEstimate> { this.requireBaseUrl(); return this.api.historicalEstimate(payload); }
  async getRequest(reservationId: string): Promise<Reservation> { this.requireBaseUrl(); return (await this.api.requestById(reservationId)).request; }
  async cancelRequest(reservationId: string): Promise<{ request: Reservation; cleanupState: string }> {
    this.requireBaseUrl();
    const result = await this.api.cancel(reservationId);
    return { request: result.request, cleanupState: result.cleanup_state };
  }
  async createBudget(team: TeamId, budgetUsd: string): Promise<Budget> {
    this.requireBaseUrl();
    const result = await this.api.updateBudget(team, budgetUsd) as { budget?: Budget };
    if (!result.budget) throw new Error('The budget API returned no budget record.');
    return result.budget;
  }
  async getAlerts(): Promise<readonly Alert[]> {
    this.requireBaseUrl();
    const alerts: Alert[] = [];
    let nextToken: string | null | undefined;
    do {
      const page = await this.api.alerts(nextToken);
      alerts.push(...page.items);
      nextToken = page.nextToken;
    } while (nextToken);
    return alerts;
  }

  private requireBaseUrl(): void {
    if (!this.baseUrl) throw new RepositoryConfigurationError('Set VITE_API_BASE_URL to use the real Spendctl API. Synthetic mode is only enabled by VITE_USE_SYNTHETIC_DATA=true.');
  }
}

function errorCode(error: unknown): string | undefined {
  return typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string' ? error.code : undefined;
}
function proposalExplanation(proposal: Reservation['calibur_proposal']): string | undefined {
  if (!proposal) return undefined;
  return `CALIBUR ${proposal.policy_version}: ${proposal.reason_codes.join(', ')}`;
}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : 'The API denied this request.'; }
function errorDetail(error: unknown): unknown {
  return typeof error === 'object' && error !== null && 'detail' in error ? error.detail : undefined;
}
function isPolicySummary(value: unknown): value is CalibrationPolicySummary {
  return typeof value === 'object' && value !== null && 'policy' in value && typeof value.policy === 'string';
}
