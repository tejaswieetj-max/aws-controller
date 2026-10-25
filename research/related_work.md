# Related Work and Prior Art Assessment

## 1. Domain Taxonomy and Scientific Context

The design of Spendctl and the CALIBUR framework sits at the intersection of five distinct research domains: adaptive conformal inference under delayed feedback, conformal risk control, state-space cloud telemetry fusion, asymmetric loss optimization in resource management, and cloud budget admission control.

## 2. Detailed Literature Synthesis

### Domain A: Adaptive Conformal Inference under Delayed Feedback

Standard split and online conformal prediction provide distribution-free prediction intervals with finite-sample coverage guarantees. In non-stationary online settings, Adaptive Conformal Inference (ACI) dynamically updates quantile thresholds based on sequential miscoverage events.

However, standard ACI assumes immediate feedback. Recent theoretical extensions introduce `τ`-delayed ACI (`τ`-DACI), decomposing the delayed update recursion into `τ` interleaved phase subsequences to establish long-run coverage bounds. A key theoretical diagnostic is the delay-to-memory ratio (`r=τ/L`), which demonstrates that the practical degradation of delayed conformal prediction depends on the relationship between feedback latency `τ` and the temporal memory scale `L` of residual non-exchangeability.

Complementary frameworks address non-standard feedback structures: Online Conformal Prediction with Queries (OCPQ) explores active label querying under partial feedback, while group-weighted and announced-break conformal prediction handle known regime transitions. CALIBUR extends this body of work by introducing `τ`-EUDACI, which leverages monotonic provisional runtime telemetry to execute early miscoverage updates before final delayed labels arrive.

### Domain B: Conformal Risk Control and Conformal Decision Theory

While conformal prediction traditionally controls scalar prediction set error, Conformal Risk Control (CRC) extends distribution-free calibration to arbitrary monotonic loss functions, guaranteeing that expected loss remains below a specified risk target (`E[L]≤α`). Required-Level Risk Control (ReqRC) further adapts risk control to rolling windows under delayed feedback.

Conformal Decision Theory (CDT) bridges conformal inference and action selection by mapping calibrated confidence sets directly to decision rules with bounded risk guarantees. System implementations such as Calibrated Collective Oversight (CCO) and RACE (Risk-Budgeted Online Scheduling) apply CDT and CRC to edge-cloud offloading and LLM routing. CALIBUR adapts CDT to financial cloud ledgers, formulating budget reservations as asymmetric decision rules under non-stationary expenditure drift.

### Domain C: State Estimation and Multi-Source Telemetry Fusion

Cloud management platforms generate multi-rate, heterogeneous telemetry streams ranging from high-frequency metric integrals (CloudWatch) to delayed, revised billing summaries (CUR). Kalman filtering, Extended Kalman Filtering (EKF), and Iterative Extended Kalman Filtering (IEKF) provide classical paradigms for multi-sensor state estimation under non-uniform measurement latencies and observation noise. CALIBUR applies state-space Kalman filtering to cloud cost estimation, fusing provisional API signals and delayed billing ledgers to produce unbiased current spend estimates and variance bounds.

### Domain D: Asymmetric Loss Optimization and Quantile Regression

Resource allocation problems inherently exhibit asymmetric loss profiles: under-reservation leads to service level objective (SLO) breaches or job terminations, whereas over-reservation causes capital lockup. Quantile regression via pinball loss is widely used in cloud capacity planning, container oversubscription (e.g., Stratus), and predictive CPU isolation (e.g., Netflix production container management).

While pinball loss optimizes expected asymmetric outcomes, it lacks finite-sample coverage guarantees during sudden distribution shifts. CALIBUR integrates asymmetric loss functions directly within a Conformal Decision Theory framework, combining asymmetric risk optimization with distribution-free safety bounds.

### Domain E: Cloud Budget Admission Control and Enforcement

Cloud governance systems enforce policy constraints through static rules, quota management, and admission control. In Kubernetes environments, Open Policy Agent (OPA) Gatekeeper blocks pods that violate static resource requests at deploy time. At the application runtime layer, real-time inference systems employ admission control, queue depth limits, and priority downgrades to protect tail latency and cost ceilings.

In cloud workflow scheduling, classic algorithms focus on budget-driven scheduling under fixed billing windows. However, existing admission frameworks rely on static, deterministic cost estimates. Spendctl and CALIBUR bridge this operational gap by introducing dynamic, conformal uncertainty margins into the admission decision loop.

## 3. Identified Research Gaps Addressed by CALIBUR

| Existing Research Domain | Fundamental Limitation | CALIBUR Resolution Mechanism |
|---|---|---|
| **Delayed Conformal Inference (-DACI)** | Suffers coverage breakdown during feedback delay when cost drift occurs rapidly. | **-EUDACI**: Uses fast provisional usage signals to execute immediate, early updates before CUR arrival. |
| **Quantile Regression (Pinball Loss)** | Provides no finite-sample safety guarantees under sudden non-stationary pricing shifts. | **SS-CDT**: Embedded within Conformal Decision Theory to guarantee distribution-free risk bounds. |
| **Cloud Telemetry APIs** | Multi-rate reporting discrepancies introduce noise and ledger reconciliation lag. | **Kalman Filter Fusion**: State-space filtering fuses CloudWatch, Cost Explorer, and CUR into a single state. |
| **FinOps Capital Reservation** | Employs static 24-48 hour hold periods for unspent buffers, locking excess capital. | **Hazard-Rate Survival Release**: Dynamically decays safety buffers based on empirical billing arrival functions. |