export type Budget = {
  team?: string;
  period: string;
  budget_micro: number;
  available_micro: number;
  reserved_micro: number;
  spent_micro: number;
  open_reservations?: number;
  version?: number;
  status?: string;
  created_at?: string;
  updated_at?: string;
};

export type EstimateBreakdown = {
  compute_usd_per_hour?: string;
  gp3_usd_per_gb_month?: string;
  hours_per_month?: number;
  compute_micro?: number;
  storage_micro?: number;
};

export type Estimate = {
  compute_micro: number;
  storage_micro: number;
  total_micro: number;
  reserve_micro?: number;
  reserve_multiplier?: number;
  source?: string;
  price_source?: string;
  price_snapshot_id?: string;
  breakdown?: EstimateBreakdown;
  region?: string;
  configuration?: { instance_type?: string; ebs_gb?: number; duration_hours?: number };
};

export type CaliburSourceRef = {
  source_id: string;
  observed_at: string;
  source_file: string;
  semantics: string;
};

export type CaliburProposal = {
  decision: 'APPROVE' | 'DENY' | string;
  reason_codes: string[];
  reservation_amount_micro: number | null;
  point_cost_estimate_micro?: number | null;
  upper_cost_estimate_micro?: number | null;
  available_budget_before_micro?: number | null;
  available_budget_after_micro?: number | null;
  under_reserve_loss_micro?: number | null;
  over_reserve_loss_micro?: number | null;
  policy_version: string;
  state_version: string;
  decision_at: string;
  input_refs?: CaliburSourceRef[];
  audit_reference?: string;
  currency?: string;
  unit?: string;
  telemetry_exposure_micro?: number;
  limitations?: string[];
};

export type Reservation = {
  reservation_id: string;
  request_id?: string;
  team?: string;
  status: string;
  requester?: string;
  budget_period?: string;
  created_at?: string;
  updated_at?: string;
  decision_at?: string;
  purpose?: string;
  region?: string;
  planned_hours?: number;
  estimate_micro?: number;
  compute_micro?: number;
  storage_micro?: number;
  reserved_micro: number;
  margin?: string;
  margin_policy?: string;
  policy_version?: string;
  price_source?: string;
  price_snapshot_id?: string;
  price_fetched_at?: string;
  estimate_breakdown?: EstimateBreakdown;
  calibur_proposal?: CaliburProposal;
  resource?: {
    type?: string;
    instance_type?: string;
    ebs_gb?: number;
    resource_class?: string;
  };
  workflow_start_state?: string;
  workflow_execution_name?: string;
  workflow_execution_arn?: string;
  workflow_execution_status?: string;
  workflow_launch_state?: string;
  workflow_cleanup_state?: string;
  instance_id?: string;
  volume_ids?: string[];
  launched_at?: string;
  expires_at?: string;
  expired_at?: string;
  terminated_at?: string;
  settlement_stage?: 'PROVISIONAL' | 'FINAL' | null;
  settlement_version?: number;
  provisional_micro?: number;
  provisional_settlement_id?: string;
  final_actual_micro?: number;
  final_settlement_id?: string;
  settled_at?: string;
  release_reason?: string;
  budget_before_available_micro?: number;
  budget_before_reserved_micro?: number;
  budget_before_spent_micro?: number;
  idempotency_key?: string;
  cancelled_at?: string;
  decision_budget_snapshot?: Record<string, number>;
};

export type HistoricalEstimate = {
  mode: 'HISTORICAL_ESTIMATE';
  status: 'HISTORICAL_ESTIMATE' | 'NOT_ESTIMABLE';
  warning: string;
  reason?: string;
  source: {
    dataset?: string;
    delivery_label?: string;
    delivery_path?: string;
    delivery_timestamp?: string;
    delivery_timestamp_semantics?: string;
    sha256_verified?: boolean;
    row_count?: number;
    currency?: string;
    billing_period_start?: string;
    billing_period_end?: string;
  };
  historical_period?: {
    billing_period_start?: string;
    billing_period_end?: string;
    charge_period_start?: string | null;
    charge_period_end?: string | null;
    charge_period_semantics?: string;
  };
  method?: string;
  uncertainty: { kind: string; confidence_interval: false; interpretation: string };
  compute?: {
    status: 'HISTORICAL_ESTIMATE';
    amount_micro: number;
    lower_observed_amount_micro: number;
    upper_observed_amount_micro: number;
    unit: string;
    unit_rate_micro_per_hour: string;
    observed_rate_range_micro_per_hour: string[];
    observation_rows: number;
    resource_count: number;
    operating_system?: string;
    charge_period_start?: string | null;
    charge_period_end?: string | null;
  };
  storage: { status: 'NOT_ESTIMABLE'; reason: string; requested_gb: number };
  total: { status: 'NOT_ESTIMABLE'; amount_micro: null; reason: string };
  limitations: string[];
};

export type Alert = {
  alert_id: string;
  alert_type?: string;
  created_at?: string;
  details?: Record<string, unknown>;
  [key: string]: unknown;
};

export type TelemetryPoint = {
  sourceTimestamp: string;
  observedAt?: string;
  usageIntegral?: number;
  reservedUpperBound?: number;
  unit?: string;
  provenance?: string;
  earlyWarning?: boolean;
  thresholdCrossed?: boolean;
  sourceRecordId?: string;
  simulated?: boolean;
};

export type EvidenceLink = {
  available: boolean;
  requirement: string;
  record_id?: string | null;
  record_ids?: string[];
  reconciliation_ids?: string[];
};

export type EvidenceChain = {
  reservation_id?: string;
  status: string;
  links: Record<string, EvidenceLink>;
  missing_links: string[];
};

export type ApiError = Error & {
  code?: string;
  status?: number;
  detail?: unknown;
  details?: unknown;
  pendingUnknown?: boolean;
  pending?: unknown;
};

export type RequestInput = {
  team: string;
  instance_type: string;
  ebs_gb: number;
  duration_hours: number;
  purpose: string;
};
