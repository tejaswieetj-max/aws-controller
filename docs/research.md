# M9 research methodology and outputs

## Reconciled observation pipeline

`research/evaluate.py` is an offline CLI. Its input JSON has two arrays exported by an authorized operator from the existing Reservations table: `reservations` and `billing_audits`. The analyzer joins by stable `reservation_id`, chooses the unique highest `source_revision_order`, requires `settlement_stage=FINAL`, validates resource and money fields, and requires `price_snapshot_id` plus `price_fetched_at`. Missing, duplicate/conflicting, unsupported, provisional, invalid, or unaudited rows are listed in the exclusion report. It never initializes boto3 or writes production ledger data.

The `--provenance` flag labels data as `AWS_EXPORT_ASSERTED_UNVERIFIED`, `SYNTHETIC_FIXTURE`, or `UNKNOWN`. Selecting `aws_export` is an operator assertion; this project has not verified any AWS export. No actual AWS observations are currently evidenced. An empty export yields zero observations and exclusions, not synthetic substitutes.

Financial fields remain integer micro-USD. Error, MAE, RMSE, MAPE, bias, ratio, reserve deficit and unused reserve metrics use Decimal arithmetic for aggregation and serialize Decimal results as strings. MAPE excludes zero or negative actual values and reports the count. Negative billing adjustments are not usage observations and are excluded by M8 before reconciliation; if supplied directly, invalid negative settled actuals are reported as an exclusion.

Candidate margins default to 1.00, 1.05, **1.10**, 1.15, 1.25 and 1.50. Reserves use the production upward integer rounding rule. These are research recommendations only and cannot change production admission. A chronological last-20% holdout is reported only with at least 20 observations across at least two billing periods; otherwise output is explicitly exploratory. The holdout split's representativeness is not guaranteed.

## Accepted research command (real data only)

`python -m research.replay.run --protocol research/protocol.md --manifest research/data_manifest.md` is the accepted research entry point. It validates provenance and event-time ordering, refuses to substitute synthetic data, and writes a truthful data-gap report when linked real observations are absent. See [research/README.md](../research/README.md).

## Quarantined synthetic demonstration

`research/run.py` is **SYNTHETIC-ONLY / NOT EVIDENCE**. It uses a fixed seed and emits raw synthetic requests, policy summaries and an SVG plot into `research/results/synthetic/`. It compares fixed 1.10, rolling empirical quantile, frozen split calibration and adaptive conformal-style calibration, plus a local schedule model and feedback-lag sensitivity. These artifacts are retained only for software reproducibility. Latency and cleanup/attribution values requiring AWS instrumentation remain null. Local schedule results do not prove DynamoDB contention or timing, and none of these files may be cited as empirical CALIBUR evidence.

The adaptive calibration rule and gamma are documented in `services/spendctl/calibration.py`; deterministic ordering and deduplication make processing delayed settlements reproducible. The adaptive heuristic makes no unconditional coverage claim. Split conformal's finite-sample coverage claim requires exchangeability, a prespecified split/quantile and representative future data; those assumptions are not established here.

## Commands

```sh
make research                 # strict real-data replay; exits nonzero on a data gap
make research-synthetic       # quarantined synthetic demonstration; not evidence
PYTHONPATH=services .venv/bin/python research/evaluate.py \
  --input /path/to/operator-export.json --provenance aws_export \
  --output /path/to/report.json --experiment
```

The second command reads only a local file and writes a deterministic Markdown summary next to the JSON report (`report.md`). Do not place customer identifiers in exports that are not required for analysis. Keep research output separate from production CostSamples and operational EMF namespaces.

## Limitations and falsification

- There are no verified live prices, CUR observations, or published research claims in this repository.
- Reservation latency, DynamoDB transaction conflicts, orphaned holds and cleanup success require instrumented execution; the local model does not invent those values.
- Reject an overrun-policy claim if a preregistered, sufficiently sized, attributed chronological holdout exceeds the allowed undercoverage rate, if excluded observations materially differ from included rows, or if duplicate settlement replay changes any metric.
- Reject an atomic-safety claim if the M2 invariant fails under any tested race or if a live integration test demonstrates duplicate reservation or overspending.
