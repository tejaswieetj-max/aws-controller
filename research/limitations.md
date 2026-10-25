# Research Limitations and Feasibility Analysis

## 1. Feasibility and Verification Status Framework

To maintain scientific integrity, all system claims, implementation components, and data sources within the Spendctl and CALIBUR research repository are categorized under four explicit status designations:

- **Verified:** Supported directly by inspected repository code, executed unit tests, or confirmed empirical data.
- **Planned:** Architecturally designed and specified, but not yet fully implemented or validated.
- **Unknown:** Requires further empirical evidence or telemetry collection before feasibility can be confirmed.
- **Blocked:** Depends on unfulfilled prerequisites or unresolved infrastructure decisions.

## 2. Explicit Itemized Status Taxonomy

### Telemetry and Infrastructure Components

- **FOCUS Data Ingestion Pipeline:** *Verified* — Standardized schema transformation handles multi-resource cost records.
- **CDK Multi-Stack Templates (5 Stacks):** *Verified (Synthesized)* — Local CDK synthesis confirms template validity; live deployment remains unverified.
- **Live AWS Billing Delay Logger (EventBridge + S3):** *Planned* — Architecture specified; live deployment pending AWS environment readiness.
- **Private Subnet & Dedicated No-Ingress Security Group:** *Blocked* — Requires owner decision on VPC topology.

### Mathematical Core and Engine Mechanics

- **State-Space Kalman Filter Module:** *Verified (Local)* — Mathematical implementation verified via local numerical tests.
- **`τ`-EUDACI Asynchronous Update Loop:** *Verified (Local)* — Early breach update logic and double-counting bitmask validated in local simulations.
- **Hazard-Rate Capital Release Scheduler:** *Planned* — Weibull/Gamma hazard fitting formulated; empirical validation awaiting time-stamped AWS delay logs.

### Empirical Validation and Production Safety

- **Unit and Synthesis Test Passing:** *Verified* — Local test suites pass; explicitly **not** claimed as proof of empirical validity or production safety.
- **End-to-End Live AWS Deployment:** *Unverified / Planned* — Full end-to-end integration across live AWS APIs remains unverified.
- **Comparative Performance Benchmarks (BOF, LCI, RAR, LWR):** *Planned* — Execution protocol defined; awaiting full real-data trace runs.

## 3. Methodological and Theoretical Limitations

### 3.1. The Public Dataset Billing Delay Gap

Public cloud billing datasets (including the FOCUS sandbox repository) contain real resource line items, costs, and utilization quantities. However, they do not record the exact epoch timestamps of when individual line items were first published by the cloud provider or subsequently revised.

**Impact:** Fitting exact hazard-rate survival functions \(S(\Delta t\mid W)\) requires collecting time-stamped EventBridge arrival logs from live AWS environments. Public traces allow cost variance estimation but cannot fully model real billing arrival latency without empirical timestamp enrichment.

### 3.2. Theoretical Delay-to-Memory Ratio (`r=τ/L`) Vulnerability

Adaptive conformal inference relies on historical residual autocorrelation. In operational scenarios where feedback delay `τ` is significantly larger than the memory scale `L` of workload cost drift (`r=τ/L≫1`), the responsiveness of the conformal parameter `αt` degrades.

**Impact:** While `τ`-EUDACI mitigates this delay by leveraging early provisional breach signals `Et=1`, early signals are only emitted when gross usage crosses the safety threshold. For subtle, gradual cost shifts that remain below the threshold during job execution, adaptation responsiveness remains bottlenecked by the raw billing delay `τ`.

### 3.3. One-Sided Asymmetry of Provisional Runtime Telemetry

`τ`-EUDACI relies on the physical principle that cumulative resource usage (CPU/memory integrals) is monotonically non-decreasing over time. This creates an asymmetric signal: an early usage breach provides definitive, non-reversible proof that a budget overrun will occur (`Et=1`).

**Impact:** Conversely, provisional telemetry cannot provide early proof of a downward cost correction or unbilled credit revision. As a result, early updates can accelerate conservatism increases during cost surges, but conservatism decreases must still wait for delayed ground-truth CUR reconciliation.

### 3.4. Noise Covariance Estimation in Multi-Rate Filtering

The Layer 1 Kalman filter assumes that observation noise covariance `Rt` can be estimated for provisional APIs (e.g., AWS Cost Explorer).

**Impact:** Cloud provider APIs may exhibit non-linear observation error, throttling behavior, or sudden batch updates during billing cycle closures. If `Rt` is severely misspecified, the state estimate `x^t∣t` may lag behind true expenditure, temporarily miscalibrating the downstream conformal decision boundary.

## 4. Summary of Source Citations for Reference

- **Adaptive Conformal Inference under Delayed Feedback (`τ`-DACI & `r=τ/L`):** El Halabi & Brandt (2026); Gibbs & Candès (2021); OCPQ; Group-Weighted Conformal Prediction.
- **Conformal Risk Control & Conformal Decision Theory:** Angelopoulos et al. (2022/2025); ReqRC; CCO / CDT; RACE.
- **State Estimation & Sensor Fusion:** Multi-sensor Kalman filtering (EKF/IEKF).
- **Asymmetric Loss & Quantile Regression:** Pinball loss & LightGBM quantile regression; Netflix predictive CPU isolation & container oversubscription.
- **Cloud Admission & Policy Enforcement:** OPA Gatekeeper & Policy-as-Code; Real-time ML inference admission control; Fog/Cloud resource allocation.