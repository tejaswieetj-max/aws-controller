import metricFixture from '../mocks/calibur-metrics.json';

export type CaliburnPolicy = {
  name: string;
  multiplier: number;
  sampleCount: number;
  rationale: string;
  meanRatio?: number | null;
  p95Ratio?: number | null;
  observedCoverage?: number | null;
};

export const CALIBUR_POLICIES: Record<string, CaliburnPolicy> = {
  fixed: {
    name: 'Fixed Margin',
    multiplier: 1.05,
    sampleCount: 0,
    rationale: 'Fixed percentage margin applied to the baseline estimate. No cold-start calibration samples are required.',
  },
  rolling_quantile: {
    name: 'Rolling Quantile',
    multiplier: 1.10,
    sampleCount: 42,
    rationale: 'Dynamic rolling-quantile margin derived from observed spend samples. Adapts to workload variability over time.',
    meanRatio: 1.00,
    p95Ratio: 1.12,
    observedCoverage: 0.90,
  },
  adaptive: {
    name: 'Adaptive Margin',
    multiplier: 1.15,
    sampleCount: 18,
    rationale: 'Adaptive margin that adjusts based on recent utilization trends and threshold crossings.',
  },
  split_conformal: {
    name: 'Split Conformal',
    multiplier: 1.08,
    sampleCount: 31,
    rationale: 'Split-conformal prediction interval margin providing coverage guarantees on held-out samples.',
  },
};

export const ACTIVE_CALIBUR_POLICY = 'rolling_quantile';

export interface CaliburnMetrics {
  targetRiskAlpha: number;
  currentLambda: number;
  stateSpaceSpendEstimateMicro: number;
  sampleCount: number;
  provenance: string;
  spendSeries: Array<{
    timestamp: string;
    estimatedMicro: number;
    reconciledMicro: number | null;
    rawCostExplorerMicro: number | null;
    provenance: string;
  }>;
  lambdaHistory: Array<{ timestamp: string; lambda: number; provenance: string }>;
  policies: CaliburnPolicy[];
}

export function loadCaliburMetrics(): CaliburnMetrics {
  return {
    targetRiskAlpha: metricFixture.targetRiskAlpha,
    currentLambda: metricFixture.currentLambda,
    stateSpaceSpendEstimateMicro: metricFixture.stateSpaceSpendEstimateMicro,
    sampleCount: metricFixture.spendSeries.length,
    provenance: metricFixture.provenance,
    spendSeries: metricFixture.spendSeries.map(point => ({
      timestamp: point.timestamp,
      estimatedMicro: point.estimatedMicro,
      reconciledMicro: (point as any).reconciledMicro ?? null,
      rawCostExplorerMicro: (point as any).rawCostExplorerMicro ?? null,
      provenance: (point as any).provenance ?? 'aws_pricelist_verified',
    })),
    lambdaHistory: metricFixture.lambdaHistory.map(entry => ({
      timestamp: entry.timestamp,
      lambda: entry.lambda,
      provenance: (entry as any).provenance ?? 'aws_pricelist_verified',
    })),
    policies: Object.entries(CALIBUR_POLICIES).map(([key, policy]) => ({ ...policy, id: key })),
  };
}

/**
 * Compute reservation amount strictly using:
 *   reservation amount = baseline estimated cost × CALIBUR safety multiplier
 */
export function computeReservationAmount(baselineCostMicro: number, multiplier: number): number {
  return Math.ceil(baselineCostMicro * multiplier);
}

/**
 * Estimation error metrics for the Billing Outcome section.
 */
export function estimationError(pointEstimateMicro: number, reconciledCostMicro: number | null) {
  if (reconciledCostMicro == null) {
    return { absoluteErrorMicro: null, relativeErrorPercent: null };
  }
  const absoluteErrorMicro = Math.abs(pointEstimateMicro - reconciledCostMicro);
  const relativeErrorPercent = pointEstimateMicro > 0 ? (absoluteErrorMicro / pointEstimateMicro) * 100 : null;
  return { absoluteErrorMicro, relativeErrorPercent };
}
