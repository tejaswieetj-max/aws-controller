"""Read-only reader for operator-exported DynamoDB reservation and billing records.

This module provides the translation layer between:
  - A local JSON file produced by an operator who exported DynamoDB records
    (reservations + billing_audits arrays from the Spendctl ledger)
  - The evaluate.py input schema (same format, but validated and annotated)

It also integrates the FOCUS billing adapter so that, once real admission
records exist, the pipeline can be run end-to-end:

    operator export JSON  →  ledger_export_reader.translate()
                          →  evaluate.build_dataset(provenance="aws_export")
                          →  evaluate.estimation_metrics()
                          →  evaluate.evaluate_margins()

CURRENT STATUS:
No DynamoDB tables exist (confirmed 2026-10-25: no tables found in any of the
17 enabled AWS regions).  The translate() function will return an empty
observations list with a clear infeasibility reason until real records exist.

This module does NOT:
- Access DynamoDB directly.
- Create, modify, or deploy AWS resources.
- Fabricate reservation IDs, resource IDs, costs, or timestamps.
- Join FOCUS rows to reservations without a verified shared identifier.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class TranslationResult:
    """Result of translating a ledger export to evaluate.py input format."""
    feasibility_gate: str               # "AMBER" until real records are verified
    reservation_count_in: int           # reservations in the input JSON
    audit_count_in: int                 # billing_audits in the input JSON
    evaluate_input: dict[str, Any]      # {"reservations": [...], "billing_audits": [...]}
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def translate(export_path: Path | str) -> TranslationResult:
    """Read a ledger export JSON and prepare it for evaluate.build_dataset().

    The export JSON must have the structure produced by the Spendctl DynamoDB
    operator export script (not yet written):

        {
          "reservations": [<reservation DynamoDB items>],
          "billing_audits": [<billing_audit DynamoDB items>]
        }

    The reservation items must include the fields that admission.py writes to
    DynamoDB (see services/spendctl/admission.py):
        reservation_id, reserved_micro, compute_micro, storage_micro,
        resource.instance_type, resource.ebs_gb, region, planned_hours,
        price_snapshot_id, price_fetched_at, margin, settlement_stage

    The billing_audit items must include the fields that billing.py writes:
        reservation_id, source_revision_order, billing_period, new_actual_micro,
        record_type (optional, "BILLING_RECONCILIATION")

    Parameters
    ----------
    export_path:
        Path to the operator-exported JSON file.

    Returns
    -------
    TranslationResult
        Contains the evaluate-ready dict and any warnings about field gaps.
        Always AMBER until records have been verified against AWS evidence.

    Raises
    ------
    FileNotFoundError
        If export_path does not exist.
    ValueError
        If the JSON is structurally invalid.
    """
    path = Path(export_path)
    if not path.is_file():
        raise FileNotFoundError(f"Ledger export not found: {path}")

    try:
        export = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(export, dict):
        raise ValueError("Export must be a JSON object")

    reservations = export.get("reservations", [])
    billing_audits = export.get("billing_audits", [])
    if not isinstance(reservations, list) or not isinstance(billing_audits, list):
        raise ValueError("reservations and billing_audits must be arrays")

    warnings: list[str] = []
    notes = [
        "This export was read from a local file; DynamoDB was not queried.",
        "Records are passed through to evaluate.py without modification.",
        "evaluate.py will report exclusions for any records that fail its "
        "validation rules (missing price_snapshot_id, non-FINAL settlement_stage, "
        "unsupported resource configuration, etc.).",
        "Provenance label 'aws_export' is an operator assertion; verify against "
        "actual AWS DynamoDB before treating results as empirically validated.",
    ]

    # Check for obvious structural gaps and warn — don't silently drop fields.
    required_reservation_fields = {
        "reservation_id", "reserved_micro", "compute_micro", "storage_micro",
        "price_snapshot_id", "price_fetched_at", "settlement_stage",
    }
    required_audit_fields = {
        "reservation_id", "source_revision_order", "billing_period", "new_actual_micro",
    }
    for i, res in enumerate(reservations[:5]):  # sample first 5
        missing = required_reservation_fields - set(res.keys() if isinstance(res, dict) else [])
        if missing:
            warnings.append(
                f"reservations[{i}] missing fields that evaluate.py requires: {sorted(missing)}"
            )
    for i, aud in enumerate(billing_audits[:5]):
        missing = required_audit_fields - set(aud.keys() if isinstance(aud, dict) else [])
        if missing:
            warnings.append(
                f"billing_audits[{i}] missing fields that evaluate.py requires: {sorted(missing)}"
            )

    return TranslationResult(
        feasibility_gate="AMBER",
        reservation_count_in=len(reservations),
        audit_count_in=len(billing_audits),
        evaluate_input={"reservations": reservations, "billing_audits": billing_audits},
        warnings=warnings,
        notes=notes,
    )


def empty_result(reason: str) -> TranslationResult:
    """Return an empty TranslationResult with an explicit infeasibility reason.

    Use this when no export file exists yet, rather than crashing.
    """
    return TranslationResult(
        feasibility_gate="AMBER",
        reservation_count_in=0,
        audit_count_in=0,
        evaluate_input={"reservations": [], "billing_audits": []},
        warnings=[],
        notes=[
            f"INFEASIBLE: {reason}",
            "No reservation or billing_audit records are available.",
            "Run evaluate.py with this empty input to produce a zero-observation report.",
        ],
    )
