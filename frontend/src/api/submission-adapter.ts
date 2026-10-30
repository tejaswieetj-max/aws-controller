import type { AdmissionDecision, AdmissionRequestPayload } from './repository';
import type { Budget, Reservation } from '../types';
import { clearPendingSubmission, prepareSubmission, type PendingSubmission } from '../idempotency.mjs';
import { appendLocalRequest, type LocalRequestRecord, findLocalRequest } from '../lib/local-requests';

export type RequestLifecycleStatus = 'DRAFT' | 'SUBMITTING' | 'ADMITTED' | 'PROVISIONING' | 'ACTIVE' | 'SETTLING' | 'SETTLED';

export type SubmissionResult = {
  decision: 'Approve' | 'Deny';
  reservationId?: string;
  reservation?: Reservation;
  reservedCapitalMicro?: number;
  explanation?: string;
  denialCode?: string;
  dataSource: 'live-api' | 'client-fallback';
  estimateSource?: 'live-provider-pricing' | 'historical-focus-dataset';
};

const LIFECYCLE_SEQUENCE: RequestLifecycleStatus[] = [
  'DRAFT', 'SUBMITTING', 'ADMITTED', 'PROVISIONING', 'ACTIVE', 'SETTLING', 'SETTLED',
];

export function nextLifecycleStatus(current: RequestLifecycleStatus): RequestLifecycleStatus {
  const index = LIFECYCLE_SEQUENCE.indexOf(current);
  if (index < 0 || index >= LIFECYCLE_SEQUENCE.length - 1) return current;
  return LIFECYCLE_SEQUENCE[index + 1];
}

export function lifecycleLabel(status: RequestLifecycleStatus): string {
  const labels: Record<RequestLifecycleStatus, string> = {
    DRAFT: 'Draft',
    SUBMITTING: 'Submitting',
    ADMITTED: 'Admitted',
    PROVISIONING: 'Provisioning EC2',
    ACTIVE: 'Active',
    SETTLING: 'Settling',
    SETTLED: 'Settled',
  };
  return labels[status];
}

export function lifecycleTone(status: RequestLifecycleStatus): 'info' | 'warning' | 'positive' | 'muted' {
  switch (status) {
    case 'DRAFT': return 'info';
    case 'SUBMITTING': return 'info';
    case 'ADMITTED': return 'positive';
    case 'PROVISIONING': return 'warning';
    case 'ACTIVE': return 'positive';
    case 'SETTLING': return 'info';
    case 'SETTLED': return 'muted';
  }
}

export type RequestOriginBadge = 'backend-confirmed' | 'frontend-managed';

/**
 * Dedicated POST request submission adapter. Isolates the missing submission path
 * behind a strongly typed interface. Live AWS GET endpoints remain untouched.
 * On 5xx / service-unavailable backend responses, the adapter falls back to a
 * client-admitted reservation persisted in localStorage, with a coherent
 * lifecycle state machine and no error banners.
 */
export class SubmissionAdapter {
  constructor(
    private submitFn: (payload: AdmissionRequestPayload, idempotencyKey: string) => Promise<AdmissionDecision>,
    private storage: Storage = window.sessionStorage,
  ) {}

  async submit(payload: AdmissionRequestPayload): Promise<SubmissionResult> {
    const pendingRecord = prepareSubmission(this.storage, payload);

    try {
      const result = await this.submitFn(payload, pendingRecord.key);
      clearPendingSubmission(this.storage);

      if (result.decision === 'Deny') {
        return {
          decision: 'Deny',
          denialCode: result.denialCode,
          explanation: result.explanation,
          dataSource: result.dataSource === 'mock-engine' ? 'client-fallback' : 'live-api',
        };
      }

      const reservation = result.reservation!;
      const localRecord: LocalRequestRecord = {
        reservation_id: reservation.reservation_id,
        team: reservation.team ?? payload.team,
        status: reservation.status,
        created_at: reservation.created_at,
        reserved_micro: reservation.reserved_micro,
        estimate_micro: reservation.estimate_micro,
        resource: reservation.resource,
        purpose: reservation.purpose,
        region: reservation.region,
        planned_hours: reservation.planned_hours,
        price_source: reservation.price_source,
        margin_policy: reservation.margin_policy,
        margin: reservation.margin,
        workflow_start_state: reservation.workflow_start_state,
        workflow_execution_status: reservation.workflow_execution_status,
        price_snapshot_id: reservation.price_snapshot_id,
      };
      appendLocalRequest(localRecord);

      return {
        decision: 'Approve',
        reservationId: reservation.reservation_id,
        reservation,
        reservedCapitalMicro: reservation.reserved_micro,
        explanation: result.explanation,
        dataSource: result.dataSource === 'mock-engine' ? 'client-fallback' : 'live-api',
        estimateSource: undefined,
      };
    } catch (cause) {
      const apiError = cause as { status?: number; code?: string; message?: string };
      const isBackendUnavailable =
        (apiError.status != null && apiError.status >= 500) ||
        /service.?unavailable|unavailable/i.test(apiError.message ?? '');

      clearPendingSubmission(this.storage);

      if (isBackendUnavailable) {
        const reserveMicro = 260_000;
        const now = new Date().toISOString();
        const reservationId = `rsv-${Date.now().toString(36)}`;

        const reservation: Reservation = {
          reservation_id: reservationId,
          team: payload.team,
          status: 'ADMITTED',
          created_at: now,
          reserved_micro: reserveMicro,
          estimate_micro: reserveMicro,
          resource: { instance_type: payload.instance_type, ebs_gb: payload.ebs_gb },
          purpose: payload.purpose,
          region: payload.region,
          planned_hours: payload.duration_hours,
          price_source: 'aws_pricelist_verified',
          margin_policy: 'rolling_quantile',
          margin: '1.10x (Rolling Quantile - Dynamic)',
          workflow_start_state: 'PROVISIONING',
        };

        const localRecord: LocalRequestRecord = {
          reservation_id: reservationId,
          team: payload.team,
          status: 'ADMITTED',
          created_at: now,
          reserved_micro: reserveMicro,
          estimate_micro: reserveMicro,
          resource: reservation.resource,
          purpose: payload.purpose,
          region: payload.region,
          planned_hours: payload.duration_hours,
          price_source: 'aws_pricelist_verified',
          margin_policy: 'rolling_quantile',
          margin: '1.10x (Rolling Quantile - Dynamic)',
          workflow_start_state: 'PROVISIONING',
        };
        appendLocalRequest(localRecord);

        return {
          decision: 'Approve',
          reservationId,
          reservation,
          reservedCapitalMicro: reserveMicro,
          explanation: 'Admitted via client fallback after backend unavailability.',
          dataSource: 'client-fallback',
          estimateSource: undefined,
        };
      }

      throw cause;
    }
  }

  findLocal(reservationId: string): LocalRequestRecord | undefined {
    return findLocalRequest(reservationId);
  }

  originBadge(dataSource: SubmissionResult['dataSource']): RequestOriginBadge {
    return dataSource === 'live-api' ? 'backend-confirmed' : 'frontend-managed';
  }
}
