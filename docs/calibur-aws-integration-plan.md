# CALIBUR AWS integration and evidence-capture plan

**Status: NOT DEPLOYED — planning only.**
No AWS resource was created, modified, or deleted while preparing this plan. No
deployment command was run. The feasibility gate remains **AMBER**.

This plan predates the current read-only AWS validation. The current account
has a Spendctl baseline deployment, while the CALIBUR additions in the local
templates are not deployed. See [`calibur-live-validation.md`](calibur-live-validation.md)
for the 2026-10-26 resource comparison and current blockers.

This document describes the smallest sequence that could produce real,
provenance-backed evidence for CALIBUR. It does not authorize any step. Every
phase below requires separate, explicit human approval, a reviewed change set,
and a confirmed AWS identity and region. Until then, the repository stays in its
current local-only state.

Related documents: `docs/aws-setup.md`, `docs/aws-deployment-readiness.md`,
`docs/calibur-connection-audit.md`, `research/protocol.md`,
`research/data_feasibility.md`, and `research/README.md`.

## 1. Purpose and non-goals

The goal is to capture the evidence the replay requires:

```
request -> admission decision -> reservation -> resource -> runtime telemetry
        -> settled billing label -> first-observability time -> revision history
        -> budget/ledger state at decision
```

Non-goals:

- no claim that CALIBUR is safe, conformal, or budget-guaranteeing;
- no joining of FOCUS bills to Alibaba trace records;
- no inferred arrival timestamps from billing-period fields or S3 `LastModified`;
- no synthetic or simulated replacement for a missing observation;
- no automatic progression from AMBER to GREEN.

## 2. Current boundary

What exists in the repository and is locally tested:

- the pure decision engine under `src/calibur_engine/`;
- the Spendctl admission, idempotent ledger, lifecycle, billing, and API code
  under `services/spendctl/`;
- CDK stack definitions under `infra/` that synthesize locally;
- read-only research loaders, the strict replay runner, and the ledger export
  tool;
- real FOCUS 1.2 billing deliveries retained locally and verified by checksum.

What is not established (confirmed against deployed state on 2026-10-26):

- the deployed Spendctl baseline does not contain the local CALIBUR API,
  estimator, τ-EUDACI, or event-ingestion additions;
- no real Spendctl admission, reservation, or settlement records exist in the
  inspected application tables to export;
- no request-to-resource-to-billing join has been verified;
- no first-observability or revision timestamps exist in the FOCUS data;
- no stable row-level FOCUS key has been confirmed;
- no telemetry is linked to a reservation;
- `research/replay/run.py` therefore exits with status 2 and writes
  `NOT_RUN_DATA_GAP`.

The plan below must not be treated as evidence that any of these gaps are
closed.

## 3. Phase 0 — local review only (safe now)

Everything in this phase is offline or read-only. No AWS credentials are needed
for the offline commands.

1. Run the repository gates and record actual output:

   ```sh
   .venv/bin/python -m pytest -q
   .venv/bin/ruff check services infra tests scripts research
   ```

2. Synthesize the CDK templates into a temporary directory and review them
   locally. Synthesis must not deploy:

   ```sh
   make synth CDK_OUTDIR=/private/tmp/spendctl-review
   ```

3. Review the exact stack list, IAM policies, DynamoDB tables, network
   parameters, and billing defaults against `docs/aws-deployment-readiness.md`.
4. Confirm that billing reconciliation remains disabled by default and that no
   Spendctl resource is currently assumed to exist.

**Exit criteria:** all local gates pass, the synthesized templates are reviewed,
and the operator has a written, redacted summary of the intended changes. No AWS
mutation has occurred.

**Stop if:** synthesis fails, a template contains an unreviewed wildcard
permission, a public-network default, or a billing path that would be enabled
without explicit approval.

## 4. Phase 1 — approved data layer (requires approval)

This phase would create only the persistence layer needed to accept one real
admission. It creates no EC2 instance and performs no billing reconciliation.

Preconditions:

- a non-root, least-privilege AWS identity with MFA, verified against the
  intended account and region;
- reviewed budget notifications (notifications are not a spending cap);
- the exact synthesized change set, including all IAM policies;
- explicit approval limited to the named data stack and no other stack.

Planned outputs:

- the DynamoDB budget, reservation, price-cache, sample, and alert tables;
- retention and point-in-time-recovery settings as reviewed;
- no public endpoint, no EC2 workload, no CUR reconciliation.

**Evidence captured:** none yet. Table creation is infrastructure, not a
research observation.

**Exit criteria:** the approved stacks reach a stable state, outputs are
recorded without secrets, and the operator confirms the tables are empty.

## 5. Phase 2 — first bounded admission (requires separate approval)

This phase would accept exactly one human-approved request and record the
decision before provisioning.

Preconditions:

- Phase 1 complete;
- a reviewed private subnet with no public route and no NAT route;
- a reviewed no-ingress security group;
- an approved AMI, instance type, volume size, and maximum runtime;
- an explicit maximum expected cost and a named approver;
- API/orchestration stacks reviewed and approved separately from Phase 1;
- billing reconciliation still disabled.

Required records for the single request:

- request identity and team, with an idempotency key;
- decision timestamp and decision outcome;
- estimate, currency, unit convention, and price snapshot reference;
- budget period and the authoritative budget state observed at decision time;
- reservation ID and reserved amount;
- resource ID and resource-creation timestamp after provisioning;
- tags that permit ownership-scoped inventory.

**Evidence captured:** a real admission decision and reservation, plus a resource
link. This is necessary but not sufficient for replay.

**Exit criteria:** exactly one reservation exists, its resource is owned and
tagged, the approved runtime is respected, and cleanup is verified. Any
unexpected resource, public address, key pair, or ingress rule is a stop
condition.

## 6. Phase 3 — runtime and billing observations (requires approval)

This phase must preserve observation time separately from event time. A later
export does not retroactively create an arrival timestamp.

### 6.1 Runtime telemetry

Capture, with source record IDs and source timestamps:

- resource start/stop and lifecycle transitions;
- observed usage relevant to the estimate;
- the observation time at which each reading became available to the collector;
- the reservation association for every observation.

Telemetry is an early-warning channel only. It must not be written into the
final billing label or double-counted as a settlement.

### 6.2 Billing label

The settled amount must be joined to the reservation by a documented,
verified key. Preserve:

- currency and integer micro-USD amount;
- the billing-period boundaries as boundaries, not arrival times;
- the first time the amount became observable;
- the export delivery identity and checksum;
- a stable billing-record key, if and only if one is confirmed.

FOCUS 1.2 currently provides no billing-record arrival timestamp and no verified
row-level stable key. Until those exist, a billing row can support a descriptive
amount but not a revision-delay or coverage evaluation.

### 6.3 Revisions and censoring

A revision exists only when the same validated stable key is observed again and
its value changed. A row appearing in a later snapshot is not a revision. A
revision replaces the current label for that job; it is not a new independent
job. Unsettled jobs remain right-censored and must be reported as such.

**Evidence captured:** genuine telemetry and, if the joins hold, a first final
label. The AMBER gate remains in force until all required channels are present.

## 7. Phase 4 — export and strict replay (local analysis)

The operator exports real records without modifying DynamoDB:

```sh
PYTHONPATH=services .venv/bin/python scripts/export_ledger.py \
    --period <YYYY-MM> \
    --confirm-account <confirmed-account> \
    --output /path/to/ledger-export.json
```

The output is private operator data. It must not be committed. It contains real
amounts and reservation identifiers.

Run the strict replay locally:

```sh
.venv/bin/python -m research.replay.run \
    --protocol research/protocol.md \
    --manifest research/data_manifest.md \
    --ledger-export /path/to/ledger-export.json
```

Important: supplying an export is not enough to make the replay run. The current
runner checks for required evidence and still stops with
`NOT_RUN_DATA_GAP`/exit 2. Before any empirical metrics can be produced, the
matching, chronology, leakage, first-availability, stable-key, revision, and
censoring checks in `research/protocol.md` must be implemented and reviewed.

**Evidence captured:** a manifest of what is present, what is missing, and the
checksums of the inputs. No metric table is written while evidence is missing.

## 8. Phase 5 — gate review

The gate may move from AMBER toward GREEN only after independent review confirms:

- every linked record has a source file, source record ID, observed timestamp,
  timestamp semantics, and provenance class;
- request, admission, reservation, resource, runtime, billing, and budget
  context are linked by documented keys;
- chronology is valid: no label or telemetry is used before it was observable;
- revisions use a validated stable key and replace, rather than duplicate,
  labels;
- censoring and missing data are explicit and counted;
- no synthetic or inferred value entered the evaluation;
- any metric is reported as descriptive unless a theorem, matching assumptions,
  and independent review support a stronger claim.

The gate does not move automatically because a deployment succeeded or because
one request completed.

## 9. Data contract additions still missing

The current ledger export format provides reservations and billing audits. For
CALIBUR replay, each exported record must additionally carry or be accompanied
by:

| Field | Why it is required |
|---|---|
| `decided_at` | chronological boundary for the decision |
| `budget_period`, `available_micro` | budget state at decision |
| `resource_id`, `resource_created_at` | request-to-resource linkage |
| `first_observed_at` | first time the billing amount was observable |
| `billing_record_key` | stable key for revision comparison |
| `revision_of` or equivalent lineage | distinguishes a revision from a new job |
| `telemetry_observed_at`, `telemetry_source_id` | early-warning provenance |
| final amount and currency | settled label with unit preservation |

Absent fields must remain absent. They must not be filled with zero, an inferred
price, an interpolated time, `LastModified`, or a generated row.

## 10. Security and cost controls

- Use a non-root, least-privilege identity with MFA; never use root credentials.
- Review every IAM policy; no unexplained wildcard permissions.
- Keep billing reconciliation disabled until the billing schema, attribution,
  and completeness have been verified.
- Treat budget alerts as notifications, not spending caps.
- Keep provisioned instances private: no public IPv4, no key pair, no ingress,
  IMDSv2, encrypted EBS, delete-on-termination, and ownership tags.
- Bound the first request by instance type, volume size, and runtime.
- Do not commit account IDs, bucket names, ARNs, raw billing rows, credentials,
  tokens, or exported ledger files.
- Do not use `make deploy`, `cdk deploy`, `cdk bootstrap`, or `make seed` without
  a separate, current approval.
- `make teardown` is a read-only inventory/planner; it is not a deletion tool.

## 11. Stop conditions

Stop and return to planning if any of the following occurs:

- the active account, region, or identity differs from the approved one;
- synthesis or review reveals an unapproved permission or public-network path;
- a deployment would change IAM, budgets, billing exports, or reconciliation
  without explicit approval;
- a required record would have to be invented, inferred, interpolated, or
  synthesized;
- billing first-availability, stable keys, or revision lineage are unavailable;
- a test or lint gate fails and the proposed fix would weaken the check;
- anyone asks for a coverage, conformal, statistical, or budget-safety guarantee
  without a stated theorem and independent review.

## 12. Approval checklist

Before any AWS mutation, the approver should confirm in writing:

- [ ] the exact account, region, identity, and role;
- [ ] the exact stacks and resources in the reviewed change set;
- [ ] one-time and ongoing cost drivers, with a maximum acceptable exposure;
- [ ] private subnet and no-ingress security group identifiers;
- [ ] billing reconciliation remains disabled;
- [ ] data retention, redaction, and export handling;
- [ ] the single bounded test request and its cleanup procedure;
- [ ] the evidence records that will be captured and where they will be stored;
- [ ] that success will not be reported as a safety guarantee.

Until this checklist is completed and approved, the correct state is:
**NOT DEPLOYED, AMBER, no empirical metrics.**
