# Related Work and Prior Art Assessment

## 1. Domain Taxonomy and Scientific Context

The design of Spendctl and the CALIBUR framework sits at the intersection of five distinct research domains: adaptive conformal inference under delayed feedback, conformal risk control, state-space cloud telemetry fusion, asymmetric loss optimization in resource management, and cloud budget admission control.

## 2. Detailed Literature Synthesis

### Domain A: Adaptive Conformal Inference under Delayed Feedback

Standard split and online conformal prediction provide distribution-free prediction intervals with finite-sample coverage guarantees. In non-stationary online settings, Adaptive Conformal Inference (ACI) dynamically updates quantile thresholds based on sequential miscoverage events.

However, standard ACI assumes immediate feedback. Recent theoretical extensions introduce `τ`-delayed ACI (`τ`-DACI), decomposing the delayed update recursion into `τ` interleaved phase subsequences to establish long-run coverage bounds. A key theoretical diagnostic is the delay-to-memory ratio (`r=τ/L`), which demonstrates that the practical degradation of delayed conformal prediction depends on the relationship between feedback latency `τ` and the temporal memory scale `L` of residual non-exchangeability.

Complementary frameworks address non-standard feedback structures: Online Conformal Prediction with Queries (OCPQ) explores active label querying under partial feedback, while group-weighted and announced-break conformal prediction handle known regime transitions. CALIBUR's design direction combines delayed final-label calibration with a *separate* operational early-warning signal. This repository does **not** implement or validate an early update to conformal coverage, and it makes no claim that telemetry can substitute for a final label.

### Domain B: Conformal Risk Control and Conformal Decision Theory

While conformal prediction traditionally controls scalar prediction set error, Conformal Risk Control (CRC) extends distribution-free calibration to arbitrary monotonic loss functions, guaranteeing that expected loss remains below a specified risk target (`E[L]≤α`). Required-Level Risk Control (ReqRC) further adapts risk control to rolling windows under delayed feedback.

Conformal Decision Theory (CDT) bridges conformal inference and action selection by mapping calibrated confidence sets directly to decision rules with bounded-risk results under stated assumptions. System implementations such as Calibrated Collective Oversight (CCO) and RACE (Risk-Budgeted Online Scheduling) apply CDT and CRC to edge-cloud offloading and LLM routing. CALIBUR's local policy module borrows the *idea* of asymmetric reservation but does not establish a CDT/CRC guarantee for this setting.

### Domain C: State Estimation and Multi-Source Telemetry Fusion

Cloud management platforms generate multi-rate, heterogeneous telemetry streams ranging from high-frequency metric integrals (CloudWatch) to delayed, revised billing summaries (CUR). Kalman filtering, Extended Kalman Filtering (EKF), and Iterative Extended Kalman Filtering (IEKF) provide classical paradigms for multi-sensor state estimation under non-uniform measurement latencies and observation noise. A state-space estimator is a possible future design, but this repository does not implement one: the available fields do not define a defensible observation model or covariance, and no Kalman estimate is claimed to be unbiased.

### Domain D: Asymmetric Loss Optimization and Quantile Regression

Resource allocation problems inherently exhibit asymmetric loss profiles: under-reservation leads to service level objective (SLO) breaches or job terminations, whereas over-reservation causes capital lockup. Quantile regression via pinball loss is widely used in cloud capacity planning, container oversubscription (e.g., Stratus), and predictive CPU isolation (e.g., Netflix production container management).

While pinball loss optimizes expected asymmetric outcomes, it does not by itself provide finite-sample coverage during sudden distribution shifts. CALIBUR explores asymmetric reservation decisions, but this repository does not combine them with a proven distribution-free safety bound and does not claim one.

### Domain E: Cloud Budget Admission Control and Enforcement

Cloud governance systems enforce policy constraints through static rules, quota management, and admission control. In Kubernetes environments, Open Policy Agent (OPA) Gatekeeper blocks pods that violate static resource requests at deploy time. At the application runtime layer, real-time inference systems employ admission control, queue depth limits, and priority downgrades to protect tail latency and cost ceilings.

In cloud workflow scheduling, classic algorithms focus on budget-driven scheduling under fixed billing windows. However, existing admission frameworks rely on static, deterministic cost estimates. Spendctl and CALIBUR bridge this operational gap by introducing dynamic, conformal uncertainty margins into the admission decision loop.

## 3. Identified Research Gaps Addressed by CALIBUR

| Existing Research Domain | Fundamental Limitation | CALIBUR Resolution Mechanism |
|---|---|---|
| **Delayed conformal inference** | Coverage behavior degrades under long feedback delay and drift. | A separate early-warning channel is a research direction; it is not a proven coverage mechanism. |
| **Quantile regression (pinball loss)** | No finite-sample safety guarantee under sudden non-stationarity. | Asymmetric reservation is implemented as a heuristic, not a proven risk bound. |
| **Cloud telemetry APIs** | Multi-rate reporting and reconciliation lag. | A state-space estimator is not implemented without a defensible model. |
| **FinOps capital reservation** | Static hold periods can lock excess capital. | A censoring-aware release interface is implemented; it is not estimated without real revision/arrival histories. |