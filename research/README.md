# CALIBUR research workspace

This directory holds the real-data research pipeline for CALIBUR. It is
deliberately conservative: the feasibility gate is **AMBER**, and no empirical
comparison is reported until the evidence required by `protocol.md` exists and
has been reviewed.

Read these first:

| Document | Purpose |
|---|---|
| `protocol.md` | v2.0 metric definitions, chronology, revisions, censoring, and the blocking conditions for a valid evaluation |
| `data_manifest.md` | Approved sources, SHA-256 values, provenance, and unresolved gaps |
| `data_card.md` | Per-dataset measurement limits and permitted use |
| `data_feasibility.md` | Current AMBER decision and what would upgrade it |
| `limitations.md` | What the current work does and does not establish |
| `../AGENTS.md` | Repository rules for safety, provenance, and change discipline |

## Evidence classes

- **Real observations only** are evidence. Approved inputs are the FOCUS billing
  deliveries and their manifests recorded in `data_manifest.md`, plus
  operator-exported Spendctl admission/reservation/billing records.
- **Synthetic artifacts are not evidence.** Everything under
  `results/synthetic/` and the legacy generator `run.py` are
  `SYNTHETIC_ONLY_NOT_EVIDENCE`. They exist only for software reproducibility,
  must stay inside a directory named `synthetic`, and must never feed the
  empirical replay or be cited as a result.
- **Alibaba Cluster Trace** records are real workload observations, not AWS
  bills. No verified key connects them to FOCUS billing or CALIBUR admissions,
  so they are not joined to either.

## Strict replay

The accepted empirical command is:

```sh
.venv/bin/python -m research.replay.run \
    --protocol research/protocol.md \
    --manifest research/data_manifest.md
```

`make research` runs the same command. From the repository root the module is
importable without installation.

The runner:

1. Verifies every approved source file against its recorded SHA-256 value and
   checks the FOCUS schema.
2. Optionally reads a local `--ledger-export` JSON file. No AWS service is
   contacted and no credential is read.
3. Evaluates the evidence checklist from `protocol.md` section 10.
4. Writes `results/run_manifest.json` and, when evidence is missing,
   `results/NOT_RUN_DATA_GAP.md`.

It exits with status **2** while the evidence is incomplete. That is the correct
outcome today, not a test failure. It never substitutes synthetic data, never
interpolates timestamps, and never writes metric tables for a run that did not
happen.

### Optional ledger export

`--ledger-export path/to/export.json` accepts a JSON object with `reservations`
and `billing_audits` arrays exported from the authoritative Spendctl ledger.
The runner treats absent fields as absent and reports record counts; it does not
infer a field from another. An export is local operator data, not committed
research input, and must not contain secrets.

### Outputs

| File | Written when | Contents |
|---|---|---|
| `results/run_manifest.json` | every run | status, gate, input hashes, code state, environment, source checks, evidence checklist, baseline availability |
| `results/NOT_RUN_DATA_GAP.md` | evidence missing | missing preconditions in plain language, source verification table, unblock requirements |

`generated_at` is the wall-clock time of the run. It is not a billing-period
timestamp, an arrival time, or a revision time.

## Metrics and engine code

- `replay/metrics.py` defines BOF, ABR, LCI, RAR, coverage, feedback delay, and
  the static/raw reservation baselines. Functions are pure and return
  `NOT_COMPUTED`/`UNAVAILABLE` instead of inventing a value.
- `replay/run.py` is the strict runner described above.
- `../src/calibur_engine/` is the pure decision engine (frozen contracts,
  integer micro-USD, explicit `SourceRef` provenance, no I/O).
- `empirical_run.py` is the earlier feasibility pipeline. It stops at AMBER when
  admission records are absent and writes only descriptive delivery summaries.
- `run.py` is the quarantined synthetic generator. Use `make research-synthetic`
  only to reproduce old software artifacts.

No module in this directory claims coverage, conformal, finite-sample,
statistical, or aggregate budget-safety guarantees. Descriptive metrics are not
guarantees.

## Tests and quality gates

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check services infra tests scripts research
```

Run both from the repository root. Report actual output; do not weaken checks to
make a gate pass.

## Current blockers

The AMBER gate stays until real records can be linked with verified provenance
and valid chronology:

- Spendctl admission/reservation exports with decision timestamps and resource
  identity;
- settled billing labels joined by a documented key;
- first-observability timestamps for those labels;
- a validated stable billing key and separately observed revisions;
- budget/ledger state at each decision;
- real telemetry linked to reservations;
- resolution of the FOCUS account discrepancy and the Alibaba license question.

Billing-period boundaries and S3 `LastModified` are not arrival times. A
snapshot difference is not a revision unless a stable key matches and the value
changed. Missing data stays missing.

For the phased, approval-gated AWS path that could produce this evidence, see
`../docs/calibur-aws-integration-plan.md`. It is **NOT DEPLOYED** and does not
authorize any AWS change.
