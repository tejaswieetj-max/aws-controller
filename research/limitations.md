# Research Limitations and Feasibility Analysis

## 1. Verification status vocabulary

- **Implemented and locally tested:** exercised by repository unit tests. This is
  software evidence, not empirical validity.
- **Implemented, not empirically validated:** code exists but no linked real
  observations support its use in a research claim.
- **Planned:** designed but not implemented.
- **Blocked:** depends on unavailable data, approval, or infrastructure.
- **Not testable with current data:** the required real observations do not exist.

## 2. Current status by component

### Data and infrastructure

| Component | Status | Evidence |
|---|---|---|
| FOCUS 1.2 ingestion | Implemented and locally tested | `research/ingestion/focus_loader.py`; D1–D3 local Parquet files |
| FOCUS snapshot comparison | Descriptive only | No verified stable row key; `InvoiceId` null |
| Alibaba workload profiling | Optional descriptive only | Real workload trace, no billing link, license unverified |
| Admission → resource → FOCUS linkage | **Blocked** | No Spendctl admission/reservation records exist |
| Billing first-availability timestamp | **Blocked** | FOCUS 1.2 has no arrival timestamp |
| Billing revision history | **Blocked** | No validated stable key across snapshots |
| Live EventBridge/S3 arrival logger | Planned | No AWS deployment |
| Private subnet / dedicated security group | Blocked | Requires owner decision; no AWS mutation authorized |

### Engine and analysis

| Component | Status | Evidence |
|---|---|---|
| Observed-spend estimator | Implemented and locally tested | Cumulative and EWMA baselines; Kalman deliberately unsupported |
| Final-label conformal/ACI calibration | Implemented and locally tested | Revisions replace scores; no coverage guarantee claimed |
| Early-warning monitor | Implemented and locally tested | Separate state; never creates a final label |
| State-space Kalman filter | **Not implemented** | Protocol does not define defensible state/observation covariance |
| `τ`-EUDACI early update of final-label coverage | **Not implemented** | No proof or empirical basis for an early-label update; early warnings remain operational only |
| Asymmetric risk policy | Implemented and locally tested | Pure proposal; ledger remains authoritative |
| Survival/hazard release policy | Implemented as an explicitly not-estimable interface with censoring support | No real revision/arrival history to fit |
| `research/replay` metrics and runner | Implemented and locally tested | Stops at AMBER with `NOT_RUN_DATA_GAP` |
| Comparative BOF/ABR/LCI/RAR evaluation | **Not testable with current data** | No linked labels, ledger state, or release records |
| End-to-end live AWS verification | Not performed | No deployment authorized |

## 3. Methodological limitations

### 3.1 No observable billing arrival or revision time

The FOCUS 1.2 schema contains billing-period and charge-period boundaries, not
the time a record became available to a controller. S3 `LastModified` is an object
timestamp and the collector's observation time is not the provider's publication
time. One or more snapshots with different row counts do not prove that a given
billing record was revised.

**Impact:** feedback delay and revision magnitude cannot be measured from the
current export. A release policy cannot be fitted. Any claim about delayed
feedback remains untested.

### 3.2 No admission-to-billing linkage

No Spendctl admission, reservation, resource, or runtime records exist to join to
FOCUS rows. `Tags` are empty, `InvoiceId` is null, and `ResourceId` is frequently
null. No key is invented.

**Impact:** BOF, RAR, LCI, ABR, and upper-bound coverage cannot be computed for
CALIBUR decisions. Aggregate budget safety is not evaluable.

### 3.3 Alibaba traces are not bills

Alibaba Cluster Trace v2017 provides real workload observations with relative
timestamps and normalized/obfuscated resource values. It has no AWS costs,
admission decisions, or billing arrival times, and no verified join key.

**Impact:** only a separately labelled descriptive workload study is possible.
It cannot support dollar-cost calibration or budget-safety claims.

### 3.4 Counterfactual policy outcomes are not identified

Historical jobs were executed under the historical policy. Their observed costs
are not automatically the costs that would occur if a different policy admitted
or denied other jobs. Shared capacity, interference, discounts, and changed
runtime violate simple off-policy assumptions.

**Impact:** replay is descriptive unless a defensible identification assumption is
stated. No causal superiority claim is made.

### 3.5 Revisions and censoring

A job with no observed revision by the collection cutoff is right-censored, not
“never revised”. A late label changes the current reconstructed calibration state
but must not be double-counted as a new job. Missing labels are excluded and
reported, never treated as zero.

### 3.6 No coverage or safety guarantee

The implemented calibration is a sequential heuristic. No finite-sample
split-conformal guarantee applies without a frozen exchangeable calibration
partition and prespecified quantile. No theorem in this repository establishes
long-run coverage under delayed, revised, non-exchangeable feedback. Early
warnings do not alter that conclusion and are not final-cost misses.

### 3.7 Kalman filtering and survival release are not empirically supported

No defensible state/observation model or covariance is defined from the available
fields, so a Kalman filter is not implemented. No real revision/arrival history
exists, so a survival release curve is not fitted. The release module returns an
explicit not-estimable status rather than inventing a distribution.

## 4. Claims that must not be made

- CALIBUR has been empirically validated for budget safety.
- FOCUS or Alibaba rows establish admission-to-billing linkage.
- The current data measures billing delay or revision behavior.
- The engine provides finite-sample, conformal, statistical, or aggregate
  budget-safety guarantees.
- A low synthetic overrun rate is evidence of real-world performance.
- The project is production-ready or AWS-verified.

## 5. Conditions for upgrading the gate

Upgrade from AMBER to GREEN only after real request, admission, reservation,
resource, runtime, billed-outcome, first-availability, revision, and
budget-context records are linked with documented keys and timestamps, and the
chronology passes the leakage checks in `research/protocol.md`.

## 6. References

- Gibbs and Candès (2021), Adaptive Conformal Inference Under Distribution Shift.
- Gibbs and Candès (2024), Conformal Inference for Online Prediction with
  Arbitrary Distribution Shifts.
- El Halabi and Brandt (2026 preprint), Adaptive Conformal Inference Under
  Delayed Feedback.
- Angelopoulos et al., Conformal Risk Control and Conformal Decision Theory.
- Alibaba Cluster Trace v2017 documentation.
- FinOps FOCUS specification and AWS Data Exports documentation.

These references motivate the design. They do not supply empirical evidence for
this repository's claims.
