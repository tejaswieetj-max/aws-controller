"""Strict chronological replay over approved real observations only.

This command is deliberately small: it verifies provenance and schema, inspects
an optional operator-exported ledger file, and stops with an explicit
``NOT_RUN_DATA_GAP`` result when the evidence required by
``research/protocol.md`` is absent.  It never creates events, delays, costs,
reservations, telemetry, or billing revisions.

Usage::

    python -m research.replay.run \
        --protocol research/protocol.md \
        --manifest research/data_manifest.md

The optional ``--ledger-export`` accepts a local JSON file with
``reservations`` and ``billing_audits`` arrays.  No AWS service is contacted.

Outputs (all under ``--output-dir``, default ``research/results``):

* ``run_manifest.json`` — hashes, environment, evidence checklist, status.
* ``NOT_RUN_DATA_GAP.md`` — written instead of metrics when evidence is missing.

If the main data preconditions fail, the command exits with status 2.  It never
switches to synthetic data and never writes placeholder metric tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from research.ingestion.focus_loader import (
    APPROVED_DELIVERIES,
    FOCUS_REQUIRED_COLUMNS,
)
from research.ingestion.manifest_reader import APPROVED_MANIFESTS
from research.replay import metrics

__all__ = [
    "EvidenceItem",
    "SourceCheck",
    "assess_evidence",
    "main",
    "render_gap_report",
    "verify_sources",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESULTS_DIR = _REPO_ROOT / "research" / "results"

STATUS_NOT_RUN_DATA_GAP = "NOT_RUN_DATA_GAP"
GATE_AMBER = "AMBER"

_EXIT_DATA_GAP = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


@dataclass(frozen=True)
class SourceCheck:
    """Verification state of one approved source file."""

    path: str
    role: str
    exists: bool
    sha256: str | None
    expected_sha256: str | None
    checksum_ok: bool
    bytes: int | None = None
    rows: int | None = None
    schema_ok: bool | None = None
    missing_columns: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "role": self.role,
            "exists": self.exists,
            "sha256": self.sha256,
            "expected_sha256": self.expected_sha256,
            "checksum_ok": self.checksum_ok,
            "bytes": self.bytes,
            "rows": self.rows,
            "schema_ok": self.schema_ok,
            "missing_columns": list(self.missing_columns),
            "notes": list(self.notes),
        }


def _parquet_metadata(path: Path) -> tuple[int | None, tuple[str, ...], tuple[str, ...]]:
    """Return row count, missing required columns, and notes from Parquet metadata."""
    try:
        from pyarrow import parquet
    except ImportError:
        return None, (), ("pyarrow is unavailable; schema and row count were not read",)
    reader = parquet.ParquetFile(path)
    names = frozenset(reader.schema_arrow.names)
    missing = tuple(sorted(FOCUS_REQUIRED_COLUMNS - names))
    notes: list[str] = []
    if missing:
        notes.append("required FOCUS columns are missing")
    return reader.metadata.num_rows, missing, tuple(notes)


def verify_sources() -> tuple[SourceCheck, ...]:
    """Check every approved delivery and manifest against recorded SHA-256 values."""
    checks: list[SourceCheck] = []
    for relative, expected in sorted(APPROVED_DELIVERIES.items()):
        path = _REPO_ROOT / relative
        if not path.is_file():
            checks.append(
                SourceCheck(
                    path=relative,
                    role="focus_delivery",
                    exists=False,
                    sha256=None,
                    expected_sha256=expected,
                    checksum_ok=False,
                    notes=("approved delivery is absent from this checkout",),
                )
            )
            continue
        digest = _sha256(path)
        rows, missing, notes = _parquet_metadata(path)
        checks.append(
            SourceCheck(
                path=relative,
                role="focus_delivery",
                exists=True,
                sha256=digest,
                expected_sha256=expected,
                checksum_ok=digest == expected,
                bytes=path.stat().st_size,
                rows=rows,
                schema_ok=not missing,
                missing_columns=missing,
                notes=notes,
            )
        )
    for relative, expected in sorted(APPROVED_MANIFESTS.items()):
        path = _REPO_ROOT / relative
        if not path.is_file():
            checks.append(
                SourceCheck(
                    path=relative,
                    role="focus_manifest",
                    exists=False,
                    sha256=None,
                    expected_sha256=expected,
                    checksum_ok=False,
                    notes=("approved manifest is absent from this checkout",),
                )
            )
            continue
        digest = _sha256(path)
        checks.append(
            SourceCheck(
                path=relative,
                role="focus_manifest",
                exists=True,
                sha256=digest,
                expected_sha256=expected,
                checksum_ok=digest == expected,
                bytes=path.stat().st_size,
            )
        )
    return tuple(checks)


@dataclass(frozen=True)
class EvidenceItem:
    """One precondition from research/protocol.md section 10."""

    key: str
    description: str
    present: bool
    detail: str
    needed: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_present(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def assess_evidence(
    *,
    ledger_export: dict[str, Any] | None,
    source_checks: tuple[SourceCheck, ...],
) -> tuple[EvidenceItem, ...]:
    """Build the protocol section-10 checklist from actual supplied records.

    Absence is reported as absence.  No field is inferred from another, and a
    record count is never used as a substitute for a required timestamp or key.
    """
    reservations: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    if ledger_export is not None:
        raw_reservations = ledger_export.get("reservations", [])
        raw_audits = ledger_export.get("billing_audits", [])
        if isinstance(raw_reservations, list):
            reservations = [item for item in raw_reservations if isinstance(item, dict)]
        if isinstance(raw_audits, list):
            audits = [item for item in raw_audits if isinstance(item, dict)]

    approved_sources_ok = bool(source_checks) and all(
        check.checksum_ok and (check.schema_ok is not False) for check in source_checks
    )

    linked = [
        reservation
        for reservation in reservations
        if _first_present(reservation, "resource_id", "resource")
        and _first_present(reservation, "created_at", "decided_at")
    ]
    final_labels = [
        audit
        for audit in audits
        if _first_present(audit, "new_actual_micro", "actual_micro", "billed_cost_micro")
        and _first_present(audit, "reservation_id")
    ]
    first_availability = [
        audit
        for audit in final_labels
        if _first_present(audit, "first_observed_at", "first_available_at")
    ]
    stable_keys = [
        audit
        for audit in audits
        if _first_present(audit, "billing_record_key", "stable_key")
    ]
    budget_context = [
        reservation
        for reservation in reservations
        if _first_present(reservation, "available_micro", "budget_available_micro")
        and _first_present(reservation, "budget_period", "period")
    ]
    telemetry = [
        audit
        for audit in audits
        if _first_present(audit, "telemetry_observed_at")
    ]
    revisions = [
        audit
        for audit in audits
        if _first_present(audit, "revision_of", "previous_version")
    ]

    return (
        EvidenceItem(
            key="approved_sources",
            description="approved FOCUS deliveries and manifests match recorded SHA-256 values",
            present=approved_sources_ok,
            detail=f"{sum(c.checksum_ok for c in source_checks)}/{len(source_checks)} sources verified",
            needed="all approved source files present and verified",
        ),
        EvidenceItem(
            key="admission_records",
            description="real request/decision/reservation records",
            present=bool(reservations),
            detail=f"{len(reservations)} reservation record(s) supplied",
            needed="operator export of Spendctl admission/reservation records",
        ),
        EvidenceItem(
            key="resource_linkage",
            description="request -> resource/runtime linkage with an observed timestamp",
            present=bool(linked),
            detail=f"{len(linked)} reservation(s) carry a resource reference and creation time",
            needed="reservation records containing resource identity and real timestamps",
        ),
        EvidenceItem(
            key="final_billed_labels",
            description="final billed cost joined to the reservation by a verified key",
            present=bool(final_labels),
            detail=f"{len(final_labels)} billing record(s) carry a final amount and reservation_id",
            needed="settled billing records joined by a documented key",
        ),
        EvidenceItem(
            key="first_availability",
            description="first-observability timestamp for each billing amount",
            present=bool(first_availability),
            detail=f"{len(first_availability)} billing record(s) carry a first-observed timestamp",
            needed="collector or export log recording when the amount first became observable",
        ),
        EvidenceItem(
            key="stable_billing_key",
            description="stable billing key for revision comparison",
            present=bool(stable_keys),
            detail=f"{len(stable_keys)} billing record(s) carry a stable key",
            needed="confirmed row-level key across separately observed deliveries",
        ),
        EvidenceItem(
            key="budget_context",
            description="authoritative budget/ledger state at each decision time",
            present=bool(budget_context),
            detail=f"{len(budget_context)} reservation(s) carry budget state at decision",
            needed="exported ledger snapshot per decision",
        ),
        EvidenceItem(
            key="telemetry",
            description="observed usage telemetry linked to the reservation",
            present=bool(telemetry),
            detail=f"{len(telemetry)} record(s) carry telemetry timestamps",
            needed="real CloudWatch/telemetry observations with source timestamps",
        ),
        EvidenceItem(
            key="revision_history",
            description="observed billing revision/arrival history with censoring",
            present=bool(revisions),
            detail=f"{len(revisions)} record(s) carry revision lineage",
            needed="validated revision pairs across separately observed exports",
        ),
    )


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("boto3", "pydantic", "pyarrow", "pytest", "ruff"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _code_state() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "worktree_dirty": bool(status),
    }


def render_gap_report(
    *,
    evidence: tuple[EvidenceItem, ...],
    source_checks: tuple[SourceCheck, ...],
    protocol_path: str,
    manifest_path: str,
    now: datetime,
    ledger_export_path: str | None,
) -> str:
    """Render the data-gap report without invented values."""
    missing = [item for item in evidence if not item.present]
    lines = [
        "# Replay not run: data gap",
        "",
        f"Generated: {now.isoformat().replace('+00:00', 'Z')}",
        f"Protocol: `{protocol_path}`",
        f"Manifest: `{manifest_path}`",
        f"Ledger export: `{ledger_export_path}`" if ledger_export_path else "Ledger export: none supplied",
        "",
        "**Status:** `NOT_RUN_DATA_GAP`",
        f"**Feasibility gate:** `{GATE_AMBER}` (unchanged)",
        "",
        "No comparative evaluation was run. No metric table was written. No request,",
        "reservation, telemetry, billing row, timestamp, or revision was created.",
        "",
        "## Missing preconditions",
        "",
    ]
    if missing:
        for item in missing:
            lines.extend(
                (
                    f"### {item.key}",
                    "",
                    f"- Requirement: {item.description}",
                    f"- Observed: {item.detail}",
                    f"- Needed: {item.needed}",
                    "",
                )
            )
    else:
        lines.extend(
            (
                "All declared evidence items were detected. The runner still does not",
                "claim a valid evaluation: the full matching, chronology, and leakage",
                "checks in `research/protocol.md` must be reviewed before any result is",
                "published.",
                "",
            )
        )
    lines.extend(
        (
            "## Source verification",
            "",
            "| File | Role | SHA-256 verified | Rows | Schema |",
            "|---|---|---|---|---|",
        )
    )
    for check in source_checks:
        lines.append(
            f"| `{check.path}` | {check.role} | {check.checksum_ok} | "
            f"{check.rows if check.rows is not None else 'unknown'} | "
            f"{check.schema_ok if check.schema_ok is not None else 'n/a'} |"
        )
    lines.extend(
        (
            "",
            "## What would unblock the run",
            "",
            "1. One or more real Spendctl admission decisions exported from the",
            "   authoritative ledger, each with decision time, team, budget period,",
            "   available budget, reserved amount, and a resource identifier.",
            "2. A settled billing record joined to that reservation by a documented,",
            "   verified key, with the currency and amount preserved.",
            "3. A first-observability timestamp for each billing amount. Billing-period",
            "   dates and S3 `LastModified` are not arrival times.",
            "4. A validated stable billing key and at least two separately observed",
            "   versions before claiming a revision or a revision delay.",
            "5. Real telemetry with source record IDs and timestamps if the early-warning",
            "   channel is to be evaluated.",
            "",
            "Once these exist, extend the runner with the matching, chronology, and",
            "leakage checks; do not substitute generated values.",
            "",
        )
    )
    return "\n".join(lines)


def _load_export(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"ledger export could not be read as JSON: {exc}"]
    if not isinstance(raw, dict):
        return None, ["ledger export must be a JSON object"]
    notes: list[str] = []
    if not isinstance(raw.get("reservations", []), list):
        notes.append("reservations is not an array")
    if not isinstance(raw.get("billing_audits", []), list):
        notes.append("billing_audits is not an array")
    return raw, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=_REPO_ROOT / "research" / "protocol.md")
    parser.add_argument("--manifest", type=Path, default=_REPO_ROOT / "research" / "data_manifest.md")
    parser.add_argument("--ledger-export", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=_RESULTS_DIR)
    args = parser.parse_args(argv)

    protocol: Path = args.protocol
    manifest: Path = args.manifest
    output_dir: Path = args.output_dir
    if not protocol.is_file():
        print(f"protocol not found: {protocol}", file=sys.stderr)
        return _EXIT_DATA_GAP
    if not manifest.is_file():
        print(f"manifest not found: {manifest}", file=sys.stderr)
        return _EXIT_DATA_GAP

    now = datetime.now(UTC)
    source_checks = verify_sources()
    export: dict[str, Any] | None = None
    export_notes: list[str] = []
    if args.ledger_export is not None:
        if not args.ledger_export.is_file():
            print(f"ledger export not found: {args.ledger_export}", file=sys.stderr)
            return _EXIT_DATA_GAP
        export, export_notes = _load_export(args.ledger_export)

    evidence = assess_evidence(ledger_export=export, source_checks=source_checks)
    unavailable = metrics.baseline_availability(
        has_point_estimates=False,
        has_final_labels=any(item.key == "final_billed_labels" and item.present for item in evidence),
        has_telemetry=any(item.key == "telemetry" and item.present for item in evidence),
        has_ledger_context=any(item.key == "budget_context" and item.present for item in evidence),
        has_calibration_history=False,
        has_revision_history=any(
            item.key == "revision_history" and item.present for item in evidence
        ),
    )

    manifest_payload: dict[str, Any] = {
        "status": STATUS_NOT_RUN_DATA_GAP,
        "feasibility_gate": GATE_AMBER,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "time_semantics": (
            "generated_at is the wall-clock run time, not a billing or report arrival time"
        ),
        "protocol": {
            "path": str(protocol.relative_to(_REPO_ROOT))
            if protocol.is_relative_to(_REPO_ROOT)
            else str(protocol),
            "sha256": _sha256(protocol),
        },
        "manifest": {
            "path": str(manifest.relative_to(_REPO_ROOT))
            if manifest.is_relative_to(_REPO_ROOT)
            else str(manifest),
            "sha256": _sha256(manifest),
        },
        "ledger_export": None
        if args.ledger_export is None
        else {
            "path": str(args.ledger_export),
            "sha256": _sha256(args.ledger_export),
            "notes": export_notes,
        },
        "code": _code_state(),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "packages": _dependency_versions(),
        },
        "sources": [check.to_dict() for check in source_checks],
        "evidence": [item.to_dict() for item in evidence],
        "baselines": [item.to_dict() for item in unavailable],
        "metrics": {},
        "exclusions": [
            "No comparative metrics were computed because required evidence is absent.",
            "All supplied-but-unmatched records, if any, remain in the export and were not deleted.",
        ],
        "limitations": [
            "No synthetic, simulated, interpolated, or generated observation was used.",
            "The feasibility gate remains AMBER.",
            "No coverage, conformal, statistical, or budget-safety guarantee is claimed.",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "run_manifest.json"
    gap_path = output_dir / "NOT_RUN_DATA_GAP.md"
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gap_path.write_text(
        render_gap_report(
            evidence=evidence,
            source_checks=source_checks,
            protocol_path=manifest_payload["protocol"]["path"],
            manifest_path=manifest_payload["manifest"]["path"],
            now=now,
            ledger_export_path=None
            if args.ledger_export is None
            else str(args.ledger_export),
        ),
        encoding="utf-8",
    )

    missing = [item.key for item in evidence if not item.present]
    print(
        json.dumps(
            {
                "status": STATUS_NOT_RUN_DATA_GAP,
                "gate": GATE_AMBER,
                "missing": missing,
                "manifest": str(manifest_path),
                "report": str(gap_path),
            },
            sort_keys=True,
        )
    )
    return _EXIT_DATA_GAP


if __name__ == "__main__":
    raise SystemExit(main())
