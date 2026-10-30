export type Budget = { budget_micro: number; available_micro: number; reserved_micro: number; spent_micro: number; period: string };
export type CaliburProposal = { decision: 'APPROVE' | 'DENY'; reason_codes: string[]; reservation_amount_micro: number | null; point_cost_estimate_micro?: number | null; upper_cost_estimate_micro?: number | null; policy_version: string; state_version: string; decision_at: string; limitations?: string[] };
export type Reservation = { reservation_id: string; status: string; created_at?: string; reserved_micro: number; estimate_micro: number; resource?: { instance_type?: string; ebs_gb?: number }; workflow_start_state?: string; workflow_execution_status?: string; calibur_proposal?: CaliburProposal; margin_policy?: string; decision_budget_snapshot?: Record<string, number> };
export type Estimate = { compute_micro: number; storage_micro: number; total_micro: number; reserve_micro?: number; reserve_multiplier?: number; source?: string; price_source?: string; price_snapshot_id?: string; breakdown?: Record<string, number> };
export type HistoricalEstimate = { mode: 'HISTORICAL_ESTIMATE'; status: 'HISTORICAL_ESTIMATE' | 'NOT_ESTIMABLE'; warning: string; reason?: string; source: Record<string, unknown>; historical_period?: Record<string, unknown>; method?: string; uncertainty: { kind: string; confidence_interval: false; interpretation: string }; compute?: { status: 'HISTORICAL_ESTIMATE'; amount_micro: number; lower_observed_amount_micro: number; upper_observed_amount_micro: number; unit: string; unit_rate_micro_per_hour: string; observed_rate_range_micro_per_hour: string[]; observation_rows: number; resource_count: number; operating_system?: string; charge_period_start?: string | null; charge_period_end?: string | null }; storage: { status: 'NOT_ESTIMABLE'; reason: string; requested_gb: number }; total: { status: 'NOT_ESTIMABLE'; amount_micro: null; reason: string }; limitations: string[] };
export type Alert = { alert_id: string; alert_type: string; created_at?: string; details?: Record<string, unknown> };
export class SpendctlApi {
  constructor(baseUrl: string, token: () => Promise<string>);
  request<T>(path: string, init?: RequestInit, idempotencyKey?: string): Promise<T>;
  estimate(payload: object): Promise<Estimate>;
  historicalEstimate(payload: object): Promise<HistoricalEstimate>;
  submit(payload: object, key: string): Promise<{ reservation: Reservation; reservation_id: string; workflow?: unknown }>;
  budget(team: string): Promise<{ budget: Budget }>;
  reservations(nextToken?: string): Promise<{ items: Reservation[]; nextToken?: string | null }>;
  requestById(id: string): Promise<{ request: Reservation }>;
  cancel(id: string): Promise<{ request: Reservation; cleanup_state: string }>;
  updateBudget(team: string, budget_usd: string): Promise<unknown>;
  calibration(): Promise<{ sample_count: number; policies: unknown[] }>;
  stateEstimate(): Promise<{ estimate: { status: string; state: number[] | null; covariance: number[][] | null; used_record_ids: string[]; skipped: { record_id: string; reason: string }[]; assumptions: string[]; decision_at: string; state_definition: string[] }; observation_count: number; sources: string[] }>;
  tauState(jobId: string): Promise<{ job_id: string; warning_event_ids: string[]; warnings: unknown[]; final_bills: unknown[]; pending_final: boolean }>;
  evidenceChain(requestId: string): Promise<{ reservation_id: string; status: string; links: Record<string, { available: boolean; requirement: string; record_id?: string; record_ids?: string[]; reconciliation_ids?: string[] }>; missing_links: string[] }>;
  releasePlan(): Promise<{ proposal: { status: string; reason: string; release_at: string | null; retained_amount_micro: number | null; limitations: string[] }; mutation: 'NONE'; eligible_observation_count: number }>;
  telemetryTrace(resourceId: string): Promise<{ availability: 'available' | 'unavailable'; resourceId: string; points: Array<{ sourceTimestamp: string; observedAt?: string; usageIntegral?: number; unit?: string; provenance?: string; earlyWarning?: boolean; thresholdCrossed?: boolean; sourceRecordId?: string }>; reason?: string | null }>;
  alerts(nextToken?: string | null): Promise<{ items: Alert[]; nextToken?: string | null }>;
}
export function formatUsd(micro: number | null | undefined): string;
export function formatUsdPrecise(micro: number | null | undefined): string;
export function usdToMicro(value: string | number): number;
