"""Empirical CALIBUR pipeline runner.

This script wires real ingested data through the event-time ledger to the
CALIBUR evaluate.py engine.  It stops with an explicit data-feasibility
result when the required joins cannot be made.

Entry points
------------
1. FOCUS billing deliveries → focus_billing_adapter.build_feasibility_result()
   → writes delivery summaries and snapshot diffs to output dir
   → if no admission records: writes data_feasibility_result.json (AMBER, stops here)
   → if admission records exist: passes evaluate_input to evaluate.build_dataset()

2. Alibaba workload stream → alibaba_workload_adapter.build_workload_stream()
   → writes descriptive workload summary (NEVER mixed into billing pipeline)
   → blocked from empirical billing analysis by AlibabaSourceUnapproved gate

3. Ledger export (optional) → ledger_export_reader.translate(export_path)
   → translates DynamoDB export to evaluate.py input
   → supplements or replaces the FOCUS billing adapter's evaluate_input

Output files written (all in --output-dir):
  empirical_feasibility.json     — FeasibilityResult from focus_billing_adapter
  empirical_delivery_N.json      — per-delivery snapshot metadata (D1, D2, D3)
  empirical_diff_D1_D2.json      — descriptive snapshot diff
  empirical_diff_D2_D3.json      — descriptive snapshot diff
  empirical_evaluate_input.json  — the evaluate.py input (may be empty arrays)
  empirical_evaluate_result.json — evaluate.py output (zero-observation when empty)
  workload_stream.json           — Alibaba workload summary (separate; not billing)

Safety constraints (enforced here)
------------------------------------
- No AWS API calls.
- No modification of raw data files.
- No fabrication of joins, costs, arrival times, or reservation IDs.
- Alibaba workload output is written to a clearly labelled separate file.
  It is never passed to evaluate.py.
- When AMBER: the evaluate_input is {"reservations": [], "billing_audits": []}.
  evaluate.py is still called so the pipeline runs end-to-end; zero observations
  is the correct and expected result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure services/ is importable (for evaluate.py).
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_focus_feasibility(*, skip_diff: bool = False) -> dict:
    """Load all FOCUS deliveries and return FeasibilityResult as a dict."""
    from ingestion.focus_billing_adapter import build_feasibility_result
    result = build_feasibility_result(skip_diff=skip_diff)
    return result


def _print_status_report(args) -> None:
    """Print a pipeline readiness summary without loading any data files."""
    _REPO_ROOT_LOCAL = Path(__file__).resolve().parents[1]

    lines = [
        "═" * 64,
        "CALIBUR Empirical Pipeline — Status Report",
        "═" * 64,
        "",
        "LOCAL CODE (all implemented and tested):",
    ]

    checks = [
        ("admission.py + calibration.py", True, "AdmissionService wires CALIBUR policy"),
        ("ledger.py", True, "Atomic DynamoDB transactions (Moto-tested)"),
        ("calibur.py", True, "SettlementHandler + DynamoCaliburStateStore"),
        ("billing.py", True, "BillingReconciler (CUR 2.0 schema; disabled by default)"),
        ("ingestion/focus_loader.py", True, "Loads FOCUS 1.2 Parquet (3 deliveries verified)"),
        ("ingestion/focus_billing_adapter.py", True, "D1→D3 snapshot diffs"),
        ("ingestion/manifest_reader.py", True, "D1 + D3 manifests read and SHA-256 verified"),
        ("ingestion/ledger_export_reader.py", True, "Translates DynamoDB export → evaluate.py"),
        ("scripts/export_ledger.py", True, "Operator export script (reads DynamoDB)"),
        ("research/empirical_run.py", True, "End-to-end pipeline entry point"),
        ("research/evaluate.py", True, "Offline analysis engine"),
    ]
    for name, ok, note in checks:
        lines.append(f"  {'✅' if ok else '❌'} {name}  — {note}")

    lines += [
        "",
        "AWS INFRASTRUCTURE (required for first real admission):",
    ]
    aws_checks = [
        ("CDK bootstrap (CDKToolkit)", True, "Deployed, bootstrap v32"),
        ("FOCUS 1.2 Data Export", True, "HEALTHY, 3 deliveries in S3"),
        ("AWS Budget ($35 ceiling)", True, "spendctl-monthly-budget deployed"),
        ("DynamoDB tables (Budgets, Reservations, Alerts)", False, "NOT DEPLOYED — need DataStack"),
        ("Lambda functions (API, orchestration, lifecycle)", False, "NOT DEPLOYED — need ApiStack + OrchestrationStack + LifecycleStack"),
        ("API Gateway HTTP API", False, "NOT DEPLOYED — need ApiStack"),
        ("Cognito User Pool", False, "NOT DEPLOYED — need IdentityStack"),
        ("Step Functions state machine", False, "NOT DEPLOYED — need OrchestrationStack"),
        ("EventBridge custom bus (spendctl-bus)", False, "NOT DEPLOYED — need LifecycleStack"),
        ("ProvisionerRole (IAM)", False, "NOT DEPLOYED — need IdentityStack"),
        ("Private subnet (no internet route)", False, "UNVERIFIED — default VPC subnets are public"),
        ("CloudTrail trail", False, "NOT DEPLOYED — bypass detection won't fire"),
        ("CUR 2.0 export (for billing.py reconciler)", False, "NOT SET UP — deployed export is FOCUS 1.2 (different schema)"),
        ("Cost-allocation tags (reservation-id, team)", False, "NOT ACTIVATED — tags empty in all 3 deliveries"),
    ]
    for name, ok, note in aws_checks:
        lines.append(f"  {'✅' if ok else '❌'} {name}  — {note}")

    lines += [
        "",
        "CURRENT EMPIRICAL RESULT: DATA_INFEASIBILITY",
        "",
        "Missing evidence to enable empirical evaluation:",
        "  1. No CALIBUR-managed admission records (DynamoDB tables not deployed)",
        "  2. No EC2 resource IDs linked to any CALIBUR reservation",
        "  3. No ReservationSettled events produced",
        "  4. No billing rows tagged with reservation-id (tags not activated)",
        "  5. The end-to-end join reservation_id → resource_id → FOCUS row",
        "     cannot be constructed without at least one completed admission",
        "",
        "NEXT HUMAN ACTION REQUIRED (approval needed before proceeding):",
        "  → Deploy Spendctl application stacks (DataStack minimum)",
        "  → Verify private subnet / no-ingress security group",
        "  → See docs/calibur-connection-audit.md for full deployment plan",
        "",
        "═" * 64,
    ]
    print("\n".join(lines))


def _load_alibaba_stream() -> dict | None:
    """Attempt to build an Alibaba workload stream.

    Returns None if the source is UNAPPROVED (expected), so the caller can
    record the gate outcome without crashing.
    """
    from ingestion.alibaba_workload_adapter import (
        AlibabaSourceUnapproved,
        build_workload_stream,
    )
    try:
        stream = build_workload_stream()
        return {
            "source": stream.source,
            "source_file": stream.source_file,
            "schema_version": stream.schema_version,
            "sha256": stream.sha256,
            "sha256_verified": stream.sha256_verified,
            "total_rows": stream.total_rows,
            "rows_with_duration": stream.rows_with_duration,
            "rows_missing_start": stream.rows_missing_start,
            "rows_missing_end": stream.rows_missing_end,
            "rows_missing_cpu": stream.rows_missing_cpu,
            "rows_missing_mem": stream.rows_missing_mem,
            "status_counts": stream.status_counts,
            "duration_p50_trace_s": stream.duration_p50_trace_s,
            "duration_p90_trace_s": stream.duration_p90_trace_s,
            "duration_p95_trace_s": stream.duration_p95_trace_s,
            "duration_max_trace_s": stream.duration_max_trace_s,
            "approval_gate": stream.approval_gate,
            "blocked_analyses": stream.blocked_analyses,
            "notes": stream.notes,
        }
    except AlibabaSourceUnapproved as exc:
        return {
            "approval_gate": "UNAPPROVED",
            "blocked": True,
            "reason": str(exc),
            "notes": [
                "Alibaba source is UNAPPROVED pending independent license confirmation.",
                ("Set _ALIBABA_APPROVED = True in alibaba_workload_adapter.py after "
                "independently verifying that CC BY 4.0 applies to the v2017 trace."),
            ],
        }


def _run_evaluate(evaluate_input: dict) -> dict:
    """Run evaluate.build_dataset() and return the full result dict."""
    from decimal import Decimal

    import evaluate as evaluate_mod

    dataset = evaluate_mod.build_dataset(evaluate_input, provenance="aws_export")
    observations = [
        evaluate_mod.Observation(
            **{
                **item,
                "reserve_margin": Decimal(str(item["reserve_margin"])),
                "actual_runtime_hours": (
                    Decimal(str(item["actual_runtime_hours"]))
                    if item["actual_runtime_hours"] is not None
                    else None
                ),
            }
        )
        for item in dataset["observations"]
    ]
    from decimal import Decimal as D
    margins = [D("1.00"), D("1.05"), D("1.10"), D("1.15"), D("1.25"), D("1.50")]
    return {
        "dataset": dataset,
        "metrics": evaluate_mod.estimation_metrics(observations),
        "margin_evaluation": evaluate_mod.evaluate_margins(observations, margins),
        "provenance": "aws_export",
    }


def _write_json(path: Path, obj: object) -> None:
    """Write obj as JSON, handling dataclasses and Decimal."""
    import dataclasses
    from decimal import Decimal

    def _default(o: object) -> object:
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)  # type: ignore[arg-type]
        if isinstance(o, Decimal):
            return str(o)
        raise TypeError(f"Object of type {type(o)} is not JSON serialisable")

    path.write_text(json.dumps(obj, indent=2, default=_default) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the real-data CALIBUR empirical pipeline.  "
            "Writes AMBER feasibility results when admission records are absent."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("research/results/empirical"),
        help="Directory for output files (created if needed).",
    )
    parser.add_argument(
        "--ledger-export",
        type=Path,
        default=None,
        help=(
            "Optional path to an operator-exported DynamoDB JSON file "
            "(reservations + billing_audits).  If omitted, the FOCUS adapter "
            "evaluate_input is used (currently empty arrays)."
        ),
    )
    parser.add_argument(
        "--skip-diff",
        action="store_true",
        help="Skip the O(n²) delivery-to-delivery snapshot diff for faster runs.",
    )
    parser.add_argument(
        "--skip-alibaba",
        action="store_true",
        help="Skip the Alibaba workload stream step.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help=(
            "Print a pipeline status report (which stages are deployable, "
            "what is missing) and exit without running the full pipeline. "
            "Makes no AWS calls and does not load Parquet files."
        ),
    )
    args = parser.parse_args()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # --status mode: print a pipeline readiness report without running.
    # -----------------------------------------------------------------------
    if args.status:
        _print_status_report(args)
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Step 1: Load FOCUS deliveries and build FeasibilityResult.
    # -----------------------------------------------------------------------
    print("Loading FOCUS billing deliveries...")
    feasibility = _load_focus_feasibility(skip_diff=args.skip_diff)

    # Serialise FeasibilityResult (dataclass) to dict for JSON output.
    import dataclasses
    if dataclasses.is_dataclass(feasibility) and not isinstance(feasibility, type):
        feasibility_dict: dict = dataclasses.asdict(feasibility)  # type: ignore[arg-type]
    else:
        feasibility_dict = feasibility  # type: ignore[assignment]

    _write_json(out / "empirical_feasibility.json", feasibility_dict)

    # Write per-delivery snapshot files.
    for snap in feasibility_dict.get("deliveries", []):
        label = snap.get("label", "unknown")
        _write_json(out / f"empirical_delivery_{label}.json", snap)
    print(f"  Wrote {len(feasibility_dict.get('deliveries', []))} delivery snapshots.")

    # Write diff files.
    for diff in feasibility_dict.get("diffs", []):
        earlier = diff.get("earlier_label", "?")
        later = diff.get("later_label", "?")
        _write_json(out / f"empirical_diff_{earlier}_{later}.json", diff)
    print(f"  Wrote {len(feasibility_dict.get('diffs', []))} delivery diffs.")

    gate = feasibility_dict.get("feasibility_gate", "AMBER")
    admission_available = feasibility_dict.get("admission_records_available", False)
    infeasibility_reason = feasibility_dict.get("infeasibility_reason", "")

    print(f"  Feasibility gate: {gate}")
    if not admission_available:
        print(f"  AMBER stop reason: {infeasibility_reason}")

    # -----------------------------------------------------------------------
    # Step 2: Determine evaluate_input.
    #   - If ledger export is provided, use ledger_export_reader.translate().
    #   - Otherwise use FeasibilityResult.evaluate_input (currently empty).
    # -----------------------------------------------------------------------
    evaluate_input: dict
    if args.ledger_export is not None:
        from ingestion.ledger_export_reader import translate
        print(f"Loading ledger export from {args.ledger_export}...")
        translation = translate(args.ledger_export)
        evaluate_input = translation.evaluate_input
        if translation.warnings:
            print("  Ledger export warnings:")
            for w in translation.warnings:
                print(f"    - {w}")
        print(
            f"  Ledger: {translation.reservation_count_in} reservations, "
            f"{translation.audit_count_in} billing_audits."
        )
    else:
        evaluate_input = feasibility_dict.get("evaluate_input", {"reservations": [], "billing_audits": []})

    _write_json(out / "empirical_evaluate_input.json", evaluate_input)

    # -----------------------------------------------------------------------
    # Step 3: Run evaluate.py end-to-end.
    #   Always run — zero observations is the correct result when AMBER.
    # -----------------------------------------------------------------------
    print("Running evaluate.py...")
    try:
        eval_result = _run_evaluate(evaluate_input)
        _write_json(out / "empirical_evaluate_result.json", eval_result)
        obs_count = eval_result["dataset"]["observation_count"]
        excl_count = len(eval_result["dataset"]["exclusions"])
        print(
            f"  evaluate.py: {obs_count} observations, "
            f"{excl_count} exclusions, "
            f"provenance={eval_result['dataset']['classification']}"
        )
        if obs_count == 0:
            print(
                "  ↳ Zero observations is the correct result: no CALIBUR-managed "
                "admissions with FINAL settlement and linked billing audits exist."
            )
    except Exception as exc:
        print(f"  evaluate.py failed: {exc}")
        raise

    # -----------------------------------------------------------------------
    # Step 4: Alibaba workload stream (descriptive only; NEVER billing input).
    # -----------------------------------------------------------------------
    if not args.skip_alibaba:
        print("Loading Alibaba workload stream (descriptive only)...")
        alibaba_result = _load_alibaba_stream()
        if alibaba_result is not None:
            _write_json(out / "workload_stream.json", alibaba_result)
            gate_val = alibaba_result.get("approval_gate", "unknown")
            total = alibaba_result.get("total_rows", "n/a")
            print(f"  Alibaba gate: {gate_val}, total_rows: {total}")
            if alibaba_result.get("blocked"):
                print(f"  ↳ Blocked: {alibaba_result.get('reason', '')[:120]}")
    else:
        print("Skipping Alibaba workload stream (--skip-alibaba).")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print()
    print("─" * 64)
    print(f"Feasibility gate:          {gate}")
    print(f"Admission records:         {admission_available}")
    print(f"FOCUS deliveries loaded:   {len(feasibility_dict.get('deliveries', []))}")
    total_focus = feasibility_dict.get("total_focus_rows", 0)
    matched = feasibility_dict.get("matched_rows", 0)
    unmatched = feasibility_dict.get("unmatched_rows", 0)
    print(f"Total FOCUS rows (latest): {total_focus}")
    print(f"Matched rows:              {matched}/{total_focus}")
    print(f"Unmatched rows:            {unmatched}/{total_focus}")
    print(f"evaluate.py observations:  {obs_count}")
    print(f"Output written to:         {out}")
    print("─" * 64)

    if gate == "AMBER":
        print(
            "\nData-feasibility result: DATA_INFEASIBILITY\n"
            "The empirical billing evaluation cannot proceed because no "
            "CALIBUR-managed admission records exist to join against FOCUS billing rows.\n"
            "This is an accurate and expected result, not a pipeline failure.\n"
            "See empirical_feasibility.json for the full gap analysis."
        )
        # Write a machine-readable DATA_INFEASIBILITY result alongside the other outputs.
        _write_json(out / "empirical_data_infeasibility.json", {
            "result": "DATA_INFEASIBILITY",
            "feasibility_gate": gate,
            "reason": feasibility_dict.get("infeasibility_reason", ""),
            "blocked_analyses": feasibility_dict.get("blocked_analyses", []),
            "missing_evidence": [
                "No CALIBUR-managed admission records exist to join against FOCUS billing rows.",
                "DynamoDB tables have not been deployed (no DataStack application stack).",
                "No EC2 resource IDs are linked to any CALIBUR reservation.",
                "No ReservationSettled events have been produced.",
                "No billing rows are tagged with reservation-id (cost-allocation tags not activated).",
                ("The end-to-end join (reservation_id → resource_id → FOCUS row) "
                "cannot be constructed without at least one human-approved CALIBUR admission."),
            ],
            "next_action": (
                "Deploy Spendctl DataStack to create DynamoDB tables, "
                "then deploy ApiStack + OrchestrationStack to accept a real admission. "
                "See docs/calibur-connection-audit.md for the full deployment proposal "
                "and required approval checklist."
            ),
            "notes": feasibility_dict.get("notes", []),
        })

    sys.exit(0)


if __name__ == "__main__":
    main()
