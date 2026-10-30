# Agent Rules for Spendctl / CALIBUR

These rules apply to every change in this repository. Read them before editing,
running commands, or making claims about the research.

## Safety and scope

- **No AWS mutation without explicit, current human approval.** Do not deploy,
  create, modify, or delete AWS resources; do not change IAM, budgets, billing
  exports, or permissions; do not run billable workloads. Read-only inspection is
  allowed only when separately authorized. The commands in `Makefile` named
  `deploy`, `seed`, and `teardown` are out of scope for routine agent work.
- **No secrets or private identifiers in committed artifacts.** Do not commit
  credentials, account IDs, raw billing rows, S3 URIs containing account IDs, ARNs
  tied to an account, or personally identifying data.
- **Preserve raw data and history.** Never delete or rewrite files under
  `data/raw/`, never rewrite Git history, force-push, reset branches, or run
  destructive cleanup. The local repository and the published remote branch are
  distinct: local branch `research/calibur-real-data` is not identical to the
  pushed remote branch (the remote excludes oversized Alibaba files). Do not push
  unless explicitly asked.

## Evidence and provenance

- **No synthetic or simulated observations may be used as empirical evidence.**
  Never fabricate requests, admissions, reservations, resource IDs, telemetry,
  billing events, arrival timestamps, report versions, costs, or results.
- Every research input must retain its source file, source record ID, observed
  timestamp, timestamp semantics, and provenance classification.
- Missing data stays missing. It is never replaced with zero, an inferred price,
  an interpolated timestamp, or a generated row.
- FOCUS billing-period timestamps and S3 `LastModified` are **not** billing
  arrival times. A snapshot difference is not a revision unless a validated,
  stable billing key matches and the value changed.
- Alibaba Cluster Trace records are real workload observations, not AWS bills.
  They must never be joined to FOCUS or admission records without a documented,
  verified key. No such key exists in this repository.
- The CALIBUR data gate remains **AMBER** until real request, admission,
  reservation, resource, runtime, billing, first-availability, revision, and
  budget-context records can be linked with verified provenance and valid
  chronology.
- **No coverage, conformal, finite-sample, statistical, or aggregate
  budget-safety guarantee may be claimed** without a stated theorem, matching
  assumptions, and independent review. Descriptive metrics are not guarantees.

## Research and synthetic material

- `research/run.py` and everything under `research/results/synthetic/` are
  **SYNTHETIC-ONLY / NOT EVIDENCE**. They may be retained for software
  reproducibility, but must never feed the empirical replay or be presented as
  real data.
- The accepted research command is the strict replay under `research/replay/`.
  It must stop with an explicit data-gap result when required real evidence is
  absent.
- Do not create a simulator, random-delay generator, synthetic workload, fake
  billing record, or artificial report version.

## Engineering and testing

- Keep `src/calibur_engine/` pure: no AWS SDK calls, network access, credentials,
  database or filesystem access, or global mutable policy state.
- The existing Spendctl API/ledger remains authoritative for authorization,
  idempotency, atomic reservation, and persistence. The engine only returns a
  proposal.
- Keep early-warning telemetry separate from final-label conformal calibration.
  A warning must never become an extra final-cost label.
- Delayed and revised labels must not be double-counted. A revision replaces the
  current label for the same job; it is not a new independent job.
- Add tests for normal operation, boundaries, invalid inputs, revision ordering,
  replay determinism, censoring, leakage prevention, and failure paths.
- Do not weaken tests or lint rules, disable meaningful checks, or suppress
  errors to make a gate pass.
- Inspect configured tool versions before running quality gates. Use the
  repository commands:
  - `.venv/bin/python -m pytest -q`
  - `.venv/bin/ruff check services infra tests scripts research`
- Report actual results. If a check fails, diagnose it rather than describing the
  expected result.
- Stage files explicitly. Never use `git add .`.

## Change discipline

- Prefer small, reviewable commits. Do not perform unrelated refactors.
- When a requirement conflicts with the project guide or the available real data,
  document the conflict and stop rather than inventing a solution.
