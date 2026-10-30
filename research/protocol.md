# CALIBUR Evaluation Protocol (real-data only)

**Protocol version:** 2.0 — 2026-10-25

**Status:** frozen before any comparative result run.

**Current data-feasibility gate:** **AMBER**. No empirical CALIBUR claim is
supported by the currently available data.

This document replaces the earlier protocol. It removes unsupported safety
guarantees, an incorrect variance-to-currency dimension, and any implication
that the available FOCUS or Alibaba files establish admission-to-billing
linkage. It is the authoritative definition for the replay and metric code in
`research/replay/`.

---

## 1. Research question and scope

**Question.** Can a telemetry-assisted, delayed-feedback admission policy reduce
aggregate budget risk and unnecessary reserved-capital lockup while preserving
workload acceptance when final billed costs arrive late or are revised?

**Unit of analysis.** One real, eligible request/job, its admission decision, its
actual reservation and resource lifecycle, and the actual provider billing
record(s) linked to the same job. Aggregate analysis uses fully observed team
budget windows.

**What this project builds.** A deterministic policy engine and a strict replay
of recorded real observations. The engine can propose an admission decision and a
reservation; the existing Spendctl handler and atomic ledger remain authoritative.

**What this project does not establish.** The current data cannot support an
empirical answer. The FOCUS export has no admission, reservation, request, or
runtime linkage, no verified stable billing-record identity, and no
billing-arrival or revision timestamp. Alibaba records are unlinked workload
traces. The gate therefore stays AMBER and the empirical hypothesis remains
untested.

## 2. Contributions and non-claims

Contributions, conditional on data feasibility:

1. A provenance-preserving real-data ingestion and chronological replay contract
   that refuses synthetic substitutions.
2. A pure policy engine that separates estimation, final-label calibration,
   early-warning telemetry, asymmetric risk, and release logic.
3. A metric contract that reports denominators, exclusions, missing labels,
   censoring, and feasibility status alongside every value.
4. An empirical evaluation of the hypotheses below **only if** the linked
   evidence described in §8 exists.

Not claimed:

- finite-sample, long-run, conformal, statistical, or aggregate budget-safety
  guarantees;
- that a Kalman/state-space model, survival release policy, or early warning is
  empirically valid;
- universal superiority over baselines;
- production readiness;
- that FOCUS rows are CALIBUR admissions or that Alibaba records are AWS bills.

## 3. Hypotheses and falsification criteria

All hypotheses are pre-registered. If the real history is insufficient, the
result is **not testable with the current dataset**, never “supported”.

- **H1 — risk/acceptance frontier.** CALIBUR achieves a better aggregate
  budget-risk/acceptance frontier than a static margin and delayed-label-only
  calibration on the same eligible real sequence.
  **Reject/fail to support** if BOF or ABR is not lower at an equal or better
  RAR on the chronological evaluation window, or if the difference is not
  distinguishable given the reported sample size.
- **H2 — less over-reservation.** At comparable measured risk, CALIBUR reduces
  measured reserved currency-hours versus fixed reservation/release policies.
  **Reject/fail to support** if LCI is not lower at comparable BOF/ABR and RAR.
- **H3 — telemetry signal value.** The observed early-warning channel improves
  the risk/acceptance frontier versus the same policy with early warning ablated.
  **Reject/fail to support** if no measurable improvement is observed on real
  telemetry timestamps, or telemetry coverage is insufficient.
- **H4 — release-policy value.** A release policy informed by observed
  revision/arrival histories reduces measured locked capital without raising risk
  above the predeclared threshold.
  **Reject/fail to support** if no real revision/arrival histories exist, or if
  risk rises above the predeclared threshold.
- **H5 — robustness.** Advantages persist across observed, separately held-out
  time windows or resource groups.
  **Reject/fail to support** if the direction reverses or the held-out sample is
  too small to evaluate.

A low BOF alone does not demonstrate aggregate budget safety. A low LCI achieved
by denying almost everything is not a win; RAR and the denial breakdown must be
reported with LCI.

## 4. Required evidence and eligibility

### 4.1 Required linked evidence

The following must exist for the same request/resource or be joined by a
documented, verified key:

| Evidence | Minimum content |
|---|---|
| Request | stable request/job ID, request timestamp, requested resource shape |
| Decision | decision timestamp, approved/denied, reserved amount, currency, policy version |
| Budget context | team cap, available/reserved/spent state at decision time, period |
| Runtime | resource ID, start/end or usage timestamps |
| Billed outcome | actual final cost and currency linked to the same job |
| First availability | report/version ID and the time the cost record became observable |
| Revision history | dated versions of the same validated billing key |
| Release | reservation release event and time, if release metrics are claimed |

### 4.2 Job eligibility

A job enters the evaluation population only if:

- it is a real request recorded before its decision, with a stable ID;
- its decision, reservation (or explicit denial), and budget context are recorded;
- its timestamps are timezone-aware and chronologically valid;
- its billing label is linked by a verified key, not by coincidence of time or
  amount;
- its final label is designated under the revision rule in §5.

Jobs with unknown outcome, missing required timestamps, unresolved duplicate IDs,
or ambiguous billing matches are excluded and counted with a reason. They are
never imputed.

### 4.3 Request and denial eligibility

RAR uses all eligible real requests, including denied requests. Denials are
reported separately as budget denials, safety denials, malformed requests, and
unknown outcomes. A missing decision is not a denial.

### 4.4 Observation eligibility

A billing observation is eligible only when its source file, source record ID,
version, and first-observed timestamp are present. Billing-period boundaries and
S3 `LastModified` are not arrival times.

## 5. Time, delayed labels, and revisions

### 5.1 Chronological boundaries

If and only if sufficient real observations exist, use event time:

1. **Training:** earliest period, for any model fitting.
2. **Calibration:** next period, for threshold/alpha selection.
3. **Evaluation:** latest held-out period, untouched during fitting.

Boundaries are fixed from the available time range before viewing evaluation
results. No random row split is used. At decision time `t`, only records whose
first-observed time is `<= t` may be used. The eventual final cost may be used
later to score that decision, never as a decision input.

Because the current data has no linked admissions, no boundaries are currently
applicable. A future run must record its boundaries in the run manifest.

### 5.2 Label identity and first arrival

A label version is identified by `(job_id, label_source_id)`. `first_seen_at` is
the earliest time a collector observed that version. `observed_at` is the time of
the particular sighting. Charge-period time is never substituted for either.

### 5.3 Revisions and out-of-order labels

- A revision must name its predecessor (`revision_of`) or otherwise provide a
  deterministic version order.
- A revision replaces the current label for the same job; it is **not** a new
  independent job and must not add a second score.
- A label older than the current version without newer lineage/order is rejected
  as stale.
- Two versions with the same first-seen time and no order evidence are treated as
  ambiguous and rejected.
- Forked or cross-job revision lineage is rejected.
- Calibration state is replayed deterministically from the current label of each
  job, ordered by `(first_seen_at, prediction_timestamp, prediction_id)`.
  Arrival order therefore cannot change the result.
- Late-arriving revisions are processed when observed; they never change the
  historical decision inputs. A true correction may change the reconstructed
  current calibration state, and this must be disclosed.

### 5.4 Right-censoring and incomplete jobs

A job whose observation window ends before a revision or final label is
**right-censored**, not “never revised”. Censoring time and status must be stored.
Release/survival estimates must account for censoring. A job with no final label
at the evaluation cutoff is excluded from BOF/coverage denominators and counted
as missing; it is not treated as covered or as zero cost.

## 6. Metric definitions

All monetary values use one declared currency (USD for the current FOCUS export)
and integer micro-USD internally. Every metric reports numerator, denominator,
observation count, window, missing/excluded counts, currency, and aggregation
rule. A metric with a zero denominator is **not computed**, not zero.

### 6.1 BOF — job reservation-overrun frequency

Let `A` be accepted jobs with a valid final billed label and a committed
reservation. Then

```text
BOF = #{i in A : actual_billed_cost_i > committed_reservation_i} / |A|
```

This is **marginal, job-level risk**. Jobs with missing/unmatched labels are
excluded from both numerator and denominator and reported separately. BOF does
not imply ABR.

### 6.2 ABR — aggregate budget breach rate

Let `W` be fully observed budget windows. A window is fully observed only when
all linked final labels for accepted jobs in the window are available, the
observation period has closed, and the ledger state is complete. Then

```text
ABR = #{w in W : actual_cumulative_spend_w + enforceable_obligations_w > team_cap_w} / |W|
```

Window boundaries are the team budget period (calendar month, UTC) unless a
different pre-registered boundary is recorded. Enforceable obligations include
reservations that can still become spend. Incomplete or reconstructed ledger
windows are excluded. **ABR is not computable with the current data.**

### 6.3 LCI — locked-capital integral

Using only actual observed intervals `[start, end)`:

```text
LCI = integral over observed time of max(0, reserved_capital(t) - active_spend(t)) dt
```

Reported in currency-hours (USD-hours) and micro-USD-hours. Times must be real
observation/lifecycle timestamps, never artificial event times. Gaps and
unobserved intervals are reported; they are not interpolated. **LCI is not
computable without linked reservation and spend histories.**

### 6.4 RAR — request acceptance rate

```text
RAR = approved eligible requests / all eligible real requests
```

Budget denials, safety denials, malformed requests, and unknown outcomes are
counted separately. A request with no recorded decision is unknown, not denied.
RAR must be reported with BOF/ABR and LCI; a high RAR alone is not success.

### 6.5 Upper-bound coverage (marginal job-level)

For jobs with a bound frozen at decision time from labels observable at that time,
and a valid final label:

```text
coverage = #{i : actual_final_cost_i <= frozen_upper_bound_i} / #{i : bound_i and final_label_i}
```

The bound timestamp and source must be recorded. A bound produced with future
labels is ineligible. This metric is **marginal job-level coverage**; it is not a
finite-sample guarantee and does not establish aggregate budget safety.

### 6.6 Feedback delay

Predeclared start: the job's observed usage end (or resource stop). End: the first
time the associated final billing amount was observable in an export or collector
log. Report median, p90, count, censored/missing count, and the exact start/end
definitions. If usage end or first availability is absent, delay is not computed.
Billing-period timestamps and S3 `LastModified` are not substitutes.

### 6.7 Revision magnitude and frequency

Only validated, stable billing keys matched across separately observed snapshots
are compared. For each matched key with a changed amount:

```text
revision_magnitude = later_comparable_amount - earlier_comparable_amount
revision_frequency = revised_matched_keys / matched_keys_with_two_versions
```

Ambiguous matches are excluded and reported. A row-count change or a new export
delivery is not a revision.

### 6.8 Release delay and retained reserve

Only if historical release records exist: `released_at - completed_at`, plus the
amount retained after release. Unobserved releases are not inferred.

### 6.9 Decision latency

Measured locally as actual policy compute time. It is an engineering metric, not
an empirical billing observation.

### 6.10 Undefined or ambiguous metric: LWR

The earlier protocol defined “Loss With Asymmetric Risk” (LWR) without a
defensible loss function, units, or aggregation. The project guide does not
define it. **LWR is therefore undefined and is not evaluated.** Any future use
must first specify the loss, weights, units, and decision population in a
protocol amendment.

## 7. Baselines, ablations, and comparisons

All policies must share the same eligible request population, event-time
visibility, labels, and evaluation window.

1. Raw point estimate with no uncertainty margin.
2. Static 20% reserve margin (a selected rule, not a universal standard).
3. Delayed-label ACI-style calibration, updating only when a label becomes
   observable; record the formula variant and bounds.
4. Quantile regression trained only on historical data with genuine labels.
5. Drift-triggered recalibration using a transparent residual detector on ordered
   real residuals; record real trigger times.
6. CALIBUR without the early-warning channel (ablation).
7. CALIBUR without the state-space estimator, using a simpler estimator
   (ablation).
8. CALIBUR with a fixed release rule instead of a survival-informed release
   (ablation, only where real revision histories exist).
9. Oracle reference only as a clearly labelled unattainable upper bound.

A baseline whose required features or labels are absent is marked **unavailable
with a reason**; no substitute labels are generated.

## 8. Provenance, licensing, and feasibility gates

### 8.1 Sources

- **AWS FOCUS 1.2 export.** Local deliveries D1–D3 under `data/raw/focus12/`,
  with hashes in `research/data_manifest.md`. These are genuine provider billing
  rows, but no admission/reservation/runtime linkage, stable row key, arrival
  time, or revision key exists. `InvoiceId` is null; `Tags` are empty;
  `ResourceId` is often null.
- **Alibaba Cluster Trace v2017.** Genuine workload observations, unlinked to AWS
  billing or admissions. May support a separately labelled descriptive workload
  analysis only. License/terms remain unverified and redistribution is not
  approved.
- **Spendctl ledger export.** No live DynamoDB tables or admission records exist.
  An operator-provided export is required.

### 8.2 Gates

- **GREEN:** all required fields in §4.1 exist with stable keys, verified
  provenance, and valid chronology. Comparative evaluation may proceed.
- **AMBER:** genuine cost or workload data exists, but linkage, arrival, revision,
  or budget context is incomplete. Descriptive analysis and software tests only.
- **RED:** provenance is uncertain, generated, or the file is a sample/demo
  dataset. It is not used.

The current gate is **AMBER**. No dataset may be promoted to GREEN by inventing
delays, joining unrelated rows, deriving fake resource IDs, filling missing charges
with zero, generating telemetry, or converting normalized CPU units to dollars.

### 8.3 Licensing and privacy

Raw FOCUS files contain account-specific identifiers and are not to be committed
to a public repository or shared. Only reviewed aggregate or schema information
may be published. The Alibaba license must be verified before redistribution.
Raw data, manifests, and retrieval timestamps are preserved unchanged.

### 8.4 Missing fields

An absent field stays missing/unknown. It is never zero, an inferred value, or a
generated value. Missingness is reported with every metric.

## 9. Counterfactual limitations

The observed final cost is only a valid counterfactual outcome for a policy
decision if the workload would have cost the same under the alternative
admission/denial and scheduling decision. Shared hosts, capacity, network,
discounts, runtime interference, and changed execution time can violate this.

Therefore:

- Retrospective replay is **descriptive** unless a stated assumption justifies
  off-policy evaluation.
- If a policy would deny a request that was executed historically, its observed
  cost is not automatically the cost of the denial.
- No causal improvement, validated policy superiority, or production
  recommendation follows from observational traces alone.
- The protocol does not assume “same historical request sequence” makes every
  counterfactual cost observable.

## 10. Conditions preventing a valid empirical evaluation

The current evaluation is invalid and must not be run when any of the following
holds:

1. No real request → decision → reservation linkage.
2. No real resource/runtime linkage to those requests.
3. No billed outcome joined by a verified key.
4. No first-availability timestamp for the billing amount.
5. No stable billing key for revision comparison.
6. No historical budget/ledger context at decision time.
7. Insufficient final labels or a single incomparable report version for the
   proposed comparison.
8. Chronology or label lineage cannot be established without guessing.

All eight currently hold in whole or part. The expected replay result is
`NOT_RUN_DATA_GAP`, with the gate left at **AMBER**.

## 11. Reporting and reproducibility

A valid run must write a manifest containing the code commit, Python and
dependency versions, command and configuration, source hashes and versions, data
time span, eligible/excluded/missing counts, report versions and timestamps,
metric denominators, output hashes, limitations, and untested claims. It must not
contain secrets, full account IDs, private S3 URIs, or personal data.

A run that cannot satisfy §4 and §5 must exit nonzero and write
`research/results/NOT_RUN_DATA_GAP.md` with the exact missing evidence and the
additional real observations required. It must not write comparative metrics or
fall back to synthetic data.

## 12. Interpretation rule

The research succeeds only if the pre-registered hypotheses can be evaluated on
eligible real data and survive the stated falsification criteria. Code passing
tests, a low synthetic overrun rate, or a plausible narrative is not empirical
evidence. If the evidence is insufficient, the correct conclusion is that the
hypothesis remains **untested**.
