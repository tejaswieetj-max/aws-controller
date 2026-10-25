# 1. Executive Overview and Evaluation Objectives

This protocol defines the formal, real-data-only evaluation framework for CALIBUR (Conformalized Asymmetric Ledger & Integrated Budget Uncertainty Reconciliation) integrated with Spendctl. The primary objective is to evaluate whether combining multi-rate state-space filtering, asynchronous early-update conformal inference (`τ`-EUDACI), asymmetric conformal decision theory (SS-CDT), and hazard-rate survival release scheduling can enforce finite-sample budget safety guarantees while minimizing capital lockup and maximizing request approval rates.

In strict compliance with the CALIBUR real-data policy, no synthetic data distributions, mock billing records, or fabricated delay samples may be used to establish empirical claims. All results must be derived from real FOCUS sandbox billing records and time-stamped cloud telemetry.

# 2. Telemetry Architecture and Multi-Stream Ingestion

The evaluation framework ingests cost and resource telemetry across three distinct operational temporal planes:

1. **High-Frequency Usage Stream (Provisional Runtime):** 1-minute CPU and memory utilization integrals ingested into Amazon DynamoDB to provide immediate, fine-grained resource consumption signals.
2. **Provisional Cost Stream (API Estimates):** Hourly cost estimates queried from the AWS Cost Explorer API and stored in Amazon S3, capturing near-term expenditure projections subject to observation noise.
3. **Authoritative Ground-Truth Stream (Reconciliation Ledger):** Daily AWS Cost and Usage Reports (CUR) CSV files stored in Amazon S3, representing official, immutable expenditure records.
4. **Billing Delay Tracker:** AWS EventBridge listeners that log the exact epoch millisecond when every CUR line item is published or revised, enabling empirical tracking of feedback delay (`τ`).

All incoming cost records are standardized via the FinOps Open Cost and Usage Specification (FOCUS) schema prior to processing by the CALIBUR engine core.

# 3. CALIBUR Engine Core Execution Stages

Normalized FOCUS telemetry flows through three sequential processing tiers.

## Layer 1: Multi-Rate State-Space Kalman Filtering

A Kalman filter fuses high-frequency provisional usage metrics with noisy Cost Explorer estimates to estimate the unobserved current spend state \(x_t\) and estimation covariance matrix \(\Sigma_{t\mid t}\):

\[
x_t = A x_{t-1} + w_t,\qquad w_t \sim N(0,Q)
\]

\[
z_t = C x_t + v_t,\qquad v_t \sim N(0,R_t)
\]

where observation variance \(R_t\) dynamically adjusts to reflect API reporting discrepancies.

## Layer 2: `τ`-EUDACI and Asymmetric Conformal Decision Theory

State estimates \((\hat{x}_{t\mid t},\Sigma_{t\mid t})\) are mapped to asymmetric budget reservations \(R_t(x_{\mathrm{req}})\) for incoming workload requests \(x_{\mathrm{req}}\):

\[
R_t(x_{\mathrm{req}})
=
\hat{x}_{t\mid t}
+
x_{\mathrm{req}}
+
\lambda_t \cdot \operatorname{Tr}(\Sigma_{t\mid t})
+
\sigma_{\mathrm{req}}^2
\]

The conservatism parameter \(\lambda_t\) is dynamically calibrated online using an asymmetric loss function \(L_{\mathrm{asym}}\) where budget overruns (`cover`) are penalized significantly more heavily than locked capital (`clock`).

If 1-minute CloudWatch usage integrals indicate that provisional spend strictly exceeds the reserved upper bound before job completion, an early error signal \(E_t=1\) triggers an immediate step update to \(\alpha_{t+1}\), bypassing multi-hour CUR feedback lag. Late-arriving CUR bills for early-triggered workloads are masked to prevent double-counting.

## Layer 3: Hazard-Rate Capital Release and Admission Gate

Upon workload completion, locked reservation buffers are progressively released back to the general budget pool using a hazard-rate survival model \(S(\Delta t\mid W)\) fitted on historical billing delivery logs, replacing static 24-to-48-hour hold periods.

# 4. Quantitative Evaluation Metrics

System performance across all evaluated controllers is measured using four formal mathematical metrics.

## 4.1. Budget Overrun Frequency (BOF)

Quantifies the proportion of execution windows where actual realized costs exceed the allocated budget:

\[
\mathrm{BOF}
=
\frac{1}{N_{\mathrm{total}}}
\sum_{i=1}^{N_{\mathrm{total}}}
\mathbf{1}\{\mathrm{ActualSpend}_i>\mathrm{Budget}_i\}
\]

**Target:** \(\mathrm{BOF}\leq\alpha\), where \(\alpha=0.05\) represents the nominal risk ceiling.

## 4.2. Capital Lockup Integral (LCI)

Measures the cumulative volume of unspent reserved capital held over time:

\[
\mathrm{LCI}
=
\int_0^{T_{\mathrm{eval}}}
\max\left(0,\mathrm{ReservedCapital}(t)-\mathrm{ActiveSpend}(t)\right)\,dt
\]

**Target:** Minimize LCI subject to the safety constraint \(\mathrm{BOF}\leq\alpha\).

## 4.3. Request Approval Rate (RAR)

Evaluates admission throughput across requested workloads:

\[
\mathrm{RAR}=\frac{N_{\mathrm{approved}}}{N_{\mathrm{requested}}}
\]

**Target:** Maximize RAR while maintaining strict budget bounds.

## 4.4. Loss With Asymmetric Risk (LWR)

Evaluates total financial loss penalizing asymmetric risk outcomes:

\[
\mathrm{LWR}
=
\sum_{t=1}^{T} L_{\mathrm{asym}}(\mathrm{ActualSpend}_t,\mathrm{ReservedCapital}_t)
\]

**Target:** Minimize total loss across the execution horizon.

# 5. Comparative Baseline Framework

CALIBUR is evaluated against five standard baselines:

1. **Static Percentage Buffer:** Applies a fixed 20% safety margin to initial cost estimates.
2. **Unadjusted Delayed ACI (`τ`-DACI):** Standard Adaptive Conformal Inference updating parameters purely on delayed CUR feedback.
3. **Pinball Loss Quantile Regression:** Static asymmetric quantile cost estimation trained via LightGBM pinball loss.
4. **ADWIN Drift-Recalibrated Predictor:** Cost estimation model retrained whenever ADWIN detects residual drift.
5. **Raw Cost Explorer Controller:** Real-time admission decision engine operating directly on uncalibrated AWS Cost Explorer API queries.

# 6. Strict Real-Data Compliance Protocol

1. **Zero Synthetic Generation:** All cost line items must originate from verified FOCUS sandbox files or recorded AWS billing logs.
2. **Empirical Delay Traces:** Billing arrival delays (\(\tau\)) must reflect real time-stamped EventBridge arrival logs.
3. **No Retrospective Threshold Selection:** Conformal coverage target \(\alpha\) and step size \(\gamma\) must be selected prior to evaluation execution and maintained across evaluation runs without hyperparameter tuning on test traces.