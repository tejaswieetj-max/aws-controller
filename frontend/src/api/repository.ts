import type { Alert, Budget, Estimate, HistoricalEstimate, Reservation } from '../types';

/** ISO-8601 timestamp supplied by a repository; its precision and semantics are
 * documented by the backing service. */
export type Timestamp = string;
export type TeamId = string;
export type ResourceId = string;
export type MicroUsd = number;

export type AdmissionRequestPayload = {
  team: TeamId;
  instance_type: 't3.micro' | 't3.small';
  region: string;
  ebs_gb: number;
  duration_hours: number;
  purpose: string;
};

export type AdmissionDecision =
  | {
      decision: 'Approve';
      reservationId: string;
      reservation: Reservation;
      /** Rt: capital held by the authoritative Spendctl ledger. */
      reservedCapitalMicro: MicroUsd;
      explanation?: string;
      dataSource: RepositoryMode;
    }
  | {
      decision: 'Deny';
      denialCode: string;
      explanation: string;
      dataSource: RepositoryMode;
      reservedCapitalMicro?: undefined;
    };

export type MetricValue<T> =
  | { availability: 'available'; value: T; observedAt?: Timestamp }
  | { availability: 'unavailable' | 'unsupported'; reason: string };

export type CalibrationPolicySummary = {
  policy: string;
  resource_class?: string;
  sample_count?: number;
  mean_ratio?: number | null;
  p95_ratio?: number | null;
  observed_coverage?: number | null;
  multiplier?: number;
  active_multiplier?: number;
  alpha?: number;
  limitations?: readonly string[];
  [field: string]: unknown;
};

export type CaliburMetrics = {
  availability: 'available' | 'unavailable';
  /** Whether the calibration sample summary was readable by this role. */
  calibrationAccess?: 'available' | 'restricted';
  sampleCount?: number;
  policies?: readonly CalibrationPolicySummary[];
  conformalCoverage: MetricValue<number>;
  riskParameterLambda: MetricValue<number>;
  stateSpaceSpendEstimate: MetricValue<MicroUsd>;
  kalmanEstimate: MetricValue<KalmanEstimate>;
  earlyUpdates?: { jobId: string; warningEventIds: readonly string[]; warnings: readonly unknown[]; finalBills: readonly unknown[]; pendingFinal: boolean } | null;
  evidenceByReservation?: Readonly<Record<string, { status: string; missingLinks: readonly string[]; links: Readonly<Record<string, { available: boolean; requirement: string }>> }>>;
  selectedEvidence?: { status: string; missingLinks: readonly string[]; links: Readonly<Record<string, { available: boolean; requirement: string }>> } | null;
  releasePlan?: { status: string; reason: string; releaseAt: string | null; retainedAmountMicro: number | null; limitations: readonly string[]; eligibleObservationCount: number; mutation: 'NONE' } | null;
  targetRiskAlpha?: MetricValue<number>;
  spendSeries?: readonly SpendComparisonPoint[];
  lambdaHistory?: readonly LambdaPoint[];
  releaseCurve?: readonly ReleaseCurvePoint[];
  provenance?: string;
};

export type KalmanEstimate = {
  status: string;
  state: readonly number[] | null;
  covariance: readonly (readonly number[])[] | null;
  stateDefinition: readonly string[];
  observationCount: number;
  usedRecordIds: readonly string[];
  skipped: readonly { recordId: string; reason: string }[];
  assumptions: readonly string[];
  decisionAt: Timestamp;
};

export type SpendComparisonPoint = { timestamp: Timestamp; estimatedMicro: MicroUsd; reconciledMicro?: MicroUsd; rawCostExplorerMicro?: MicroUsd; provenance: string };
export type LambdaPoint = { timestamp: Timestamp; lambda: number; provenance: string };
export type ReleaseCurvePoint = { elapsedMinutes: number; retainedCapitalFraction: number; provenance: string };

export type TelemetryPoint = {
  sourceTimestamp: Timestamp;
  observedAt?: Timestamp;
  usageIntegral?: number;
  reservedUpperBound?: number;
  unit?: string;
  provenance?: string;
  thresholdCrossed?: boolean;
  earlyWarning?: boolean;
};

export type TelemetryTraceResult =
  | {
      availability: 'available';
      resourceId: ResourceId;
      points: readonly TelemetryPoint[];
    }
  | {
      availability: 'unavailable' | 'unsupported';
      resourceId: ResourceId;
      reason: string;
    };

export type RepositoryMode = 'real-api' | 'synthetic' | 'mock-engine';

/**
 * Frontend data boundary. It intentionally represents only documented API
 * operations; it never turns unavailable backend data into synthetic values.
 */
export interface SpendctlRepository {
  readonly mode: RepositoryMode;

  getBudgets(team?: TeamId): Promise<readonly Budget[]>;
  getLedgerEntries(): Promise<readonly Reservation[]>;
  submitAdmissionRequest(payload: AdmissionRequestPayload, idempotencyKey: string): Promise<AdmissionDecision>;
  getCaliburMetrics(): Promise<CaliburMetrics>;
  getTelemetryTraces(resourceId: ResourceId): Promise<TelemetryTraceResult>;
  getEvidenceChain(reservationId: ResourceId): Promise<{ status: string; missingLinks: readonly string[]; links: Readonly<Record<string, { available: boolean; requirement: string }>> }>;

  // Existing UI operations retained while the views migrate to the contract.
  estimate(payload: AdmissionRequestPayload): Promise<Estimate>;
  historicalEstimate(payload: AdmissionRequestPayload): Promise<HistoricalEstimate>;
  getRequest(reservationId: string): Promise<Reservation>;
  cancelRequest(reservationId: string): Promise<{ request: Reservation; cleanupState: string }>;
  createBudget(team: TeamId, budgetUsd: string): Promise<Budget>;
  getAlerts(): Promise<readonly Alert[]>;
}
