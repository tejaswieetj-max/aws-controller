"""Read-only adapter connecting FOCUS 1.2 deliveries to the evaluate.py input schema.

This adapter does NOT create a join between FOCUS billing records and CALIBUR
reservation records.  No such join is possible with the current data: all Tags
maps are empty, no reservation-id tag has ever appeared in any delivery, and
DynamoDB contains no CALIBUR-managed reservations (confirmed 2026-10-25).

What this module does:
- Loads each approved FOCUS delivery via focus_loader.load_parquet().
- Computes per-delivery snapshot metadata: delivery timestamp, execution ID,
  first-observed time from s3_observations.jsonl, row counts, and checksums.
- Performs a delivery-to-delivery descriptive comparison (D1→D2→D3) using
  descriptive grouping keys (ServiceName + ChargeCategory + ChargePeriodStart
  + ResourceId).  Groups that appear only in a later delivery are recorded as
  "new_groups"; groups that changed count are "changed_groups".  Neither
  constitutes a proven billing revision without a verified row-level key.
- Classifies rows into matched, unmatched, and ambiguous sets with explicit
  reasons.
- Produces a FeasibilityResult that can be passed directly to evaluate.py's
  build_dataset() when real admission records are available, or consumed
  independently for descriptive analysis.

Guarantees:
- Never invents reservation IDs, resource mappings, costs, or arrival times.
- Never fills missing costs with zero.
- Never treats S3 LastModified as the exact first-available billing timestamp.
- Tags the first_observed_utc field from the s3_evidence_collector record
  (observation time), not from S3 LastModified or ChargePeriodStart.
- All results are explicitly labelled with their provenance.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repository root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_S3_OBS_FILE = _REPO_ROOT / "data" / "raw" / "collector_observations" / "s3_observations.jsonl"

# Ordered list of approved deliveries: (relative_path, delivery_timestamp, execution_id)
# Ordered chronologically by delivery_timestamp.
DELIVERY_REGISTRY: list[dict[str, str]] = [
    {
        "relative_path": "data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet",
        "delivery_timestamp": "2026-10-24T19:27:14.926Z",
        "execution_id": "19206226-e1be-320d-84a6-833b2f8a9b81",
        "label": "D1",
    },
    {
        "relative_path": "data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet",
        "delivery_timestamp": "2026-10-25T03:32:00.757Z",
        "execution_id": "ddede1a2-e372-34a5-aa28-36cacf5a98ef",
        "label": "D2",
    },
    {
        "relative_path": "data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-00001.snappy.parquet",
        "delivery_timestamp": "2026-10-25T11:18:08.607Z",
        "execution_id": "2719ee81-66bd-346a-93bc-ec5e99dc1747",
        "label": "D3",
    },
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class DeliverySnapshot:
    """Metadata for one FOCUS delivery, with its first-observed time."""
    label: str                          # "D1", "D2", "D3"
    relative_path: str
    delivery_timestamp: str             # UTC ISO-8601 from S3 path
    execution_id: str
    sha256: str
    sha256_verified: bool
    size_bytes: int
    row_count: int
    first_observed_utc: str | None      # from s3_evidence_collector, not S3 LastModified
    s3_last_modified_utc: str | None    # S3 object LastModified; NOT a billing arrival time
    focus_version: str
    currency: str | None
    missing_invoice_id_count: int
    missing_resource_id_count: int
    empty_tags_count: int
    notes: list[str] = field(default_factory=list)


@dataclass
class SnapshotDiff:
    """Descriptive difference between two consecutive FOCUS deliveries.

    Uses grouping key (ServiceName, ChargeCategory, ChargePeriodStart[:10], ResourceId).
    Results are descriptive only; they are NOT proven billing revisions.
    """
    earlier_label: str
    later_label: str
    earlier_row_count: int
    later_row_count: int
    groups_in_earlier: int
    groups_in_later: int
    groups_in_both: int
    new_groups_in_later: int            # groups present in later but not earlier
    removed_groups_in_earlier: int      # groups present in earlier but not later
    changed_count_groups: int           # groups present in both where row count changed
    new_rows_in_later: int              # rows in new_groups only
    new_rows_sum_billed_usd: float | None  # sum of BilledCost for new_groups rows
    is_strictly_additive: bool          # True if no earlier groups were removed
    revision_claim: str                 # always "unproven — no verified row-level key"
    notes: list[str] = field(default_factory=list)


@dataclass
class FeasibilityResult:
    """Summarises what can and cannot be connected to evaluate.py.

    The ``evaluate_input`` field contains the evaluate.py-compatible JSON
    structure.  When ``admission_records_available`` is False, both arrays
    will be empty and the ``infeasibility_reason`` explains why.
    """
    feasibility_gate: str               # "AMBER" always until real admissions exist
    admission_records_available: bool
    infeasibility_reason: str | None
    deliveries: list[DeliverySnapshot]
    diffs: list[SnapshotDiff]
    matched_rows: int                   # 0 until real admission-FOCUS joins exist
    unmatched_rows: int                 # all FOCUS rows are currently unmatched
    ambiguous_rows: int                 # 0
    total_focus_rows: int               # across all deliveries (latest snapshot)
    evaluate_input: dict[str, Any]      # {"reservations": [], "billing_audits": []}
    blocked_analyses: list[str]
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_s3_observations() -> dict[str, dict]:
    """Return object_key -> observation record from s3_observations.jsonl."""
    if not _S3_OBS_FILE.is_file():
        return {}
    result: dict[str, dict] = {}
    with _S3_OBS_FILE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                result[rec["object_key"]] = rec
            except (json.JSONDecodeError, KeyError):
                pass
    return result


def _extract_execution_id(object_key: str) -> str | None:
    """Extract execution ID from S3 object key path segment."""
    # Keys look like: .../billing_period=2026-10/2026-10-25T03:32:00.757Z-<exec_id>/...
    parts = object_key.split("/")
    for part in parts:
        # segment like "2026-10-25T03:32:00.757Z-ddede1a2-e372-34a5-aa28-36cacf5a98ef"
        if "Z-" in part and len(part) > 40:
            idx = part.index("Z-")
            return part[idx + 2:]  # everything after "Z-"
    return None


def _grouping_key(row: dict) -> tuple:
    """Descriptive grouping key for snapshot comparison. NOT a billing identity key."""
    cp_start = str(row.get("ChargePeriodStart", ""))[:10]  # date only
    return (
        row.get("ServiceName"),
        row.get("ChargeCategory"),
        cp_start,
        row.get("ResourceId"),
    )


def _load_delivery(registry_entry: dict, s3_obs: dict[str, dict],
                   *, allow_unlisted: bool = False) -> DeliverySnapshot:
    """Load one delivery from the approved registry, attach S3 observation metadata."""
    from .focus_loader import load_parquet

    rel = registry_entry["relative_path"]
    abs_path = Path(rel) if Path(rel).is_absolute() else (_REPO_ROOT / rel)
    abs_path = abs_path.resolve()

    if not abs_path.is_file():
        raise FileNotFoundError(f"FOCUS delivery not found locally: {abs_path}")

    result = load_parquet(abs_path, allow_unlisted=allow_unlisted)

    # Attach first_observed_utc from s3_evidence_collector (not S3 LastModified).
    # Match by execution_id embedded in the object key.
    exec_id = registry_entry["execution_id"]
    first_observed: str | None = None
    s3_last_modified: str | None = None
    for key, obs in s3_obs.items():
        key_exec = _extract_execution_id(key)
        if key_exec == exec_id and key.endswith(".parquet"):
            first_observed = obs.get("observed_at_utc")
            s3_last_modified = obs.get("s3_last_modified_utc")
            break

    rows_dict = {col: [] for col in ["Tags", "ResourceId"]}
    for row in result.rows:
        rows_dict["Tags"].append(row.fields.get("Tags"))
        rows_dict["ResourceId"].append(row.fields.get("ResourceId"))

    empty_tags = sum(1 for t in rows_dict["Tags"] if not t)
    missing_rid = sum(1 for r in rows_dict["ResourceId"] if r is None)

    notes = list(result.notes)
    if missing_rid:
        notes.append(
            f"ResourceId is null in {missing_rid} row(s); no resource-level join "
            "is possible for those rows."
        )
    if first_observed is None:
        notes.append(
            "first_observed_utc is None: s3_evidence_collector observation not found "
            f"for execution_id={exec_id}. Do not substitute S3 LastModified."
        )

    return DeliverySnapshot(
        label=registry_entry["label"],
        relative_path=rel,
        delivery_timestamp=registry_entry["delivery_timestamp"],
        execution_id=exec_id,
        sha256=result.sha256,
        sha256_verified=result.sha256_verified,
        size_bytes=abs_path.stat().st_size,
        row_count=result.row_count,
        first_observed_utc=first_observed,
        s3_last_modified_utc=s3_last_modified,
        focus_version=result.focus_version,
        currency=result.currency,
        missing_invoice_id_count=result.missing_counts.get("InvoiceId", 0),
        missing_resource_id_count=result.missing_counts.get("ResourceId", 0),
        empty_tags_count=empty_tags,
        notes=notes,
    )


def _diff_deliveries(earlier: DeliverySnapshot, later: DeliverySnapshot,
                     *, allow_unlisted: bool = False) -> SnapshotDiff:
    """Build a descriptive diff between two deliveries. No revision claim is made."""
    from .focus_loader import load_parquet

    def load_groups(snapshot: DeliverySnapshot) -> dict[tuple, list[dict]]:
        path = Path(snapshot.relative_path) if Path(snapshot.relative_path).is_absolute() \
               else _REPO_ROOT / snapshot.relative_path
        result = load_parquet(path, allow_unlisted=allow_unlisted)
        groups: dict[tuple, list] = {}
        for row in result.rows:
            key = _grouping_key(row.fields)
            groups.setdefault(key, []).append(row.fields)
        return groups

    g_e = load_groups(earlier)
    g_l = load_groups(later)

    keys_e = set(g_e)
    keys_l = set(g_l)
    keys_both = keys_e & keys_l
    new_in_later = keys_l - keys_e
    removed_in_earlier = keys_e - keys_l

    changed_count = sum(
        1 for k in keys_both
        if len(g_e[k]) != len(g_l[k])
    )

    new_rows = sum(len(g_l[k]) for k in new_in_later)
    new_rows_cost: float | None = None
    try:
        costs = [
            row.get("BilledCost") or 0
            for k in new_in_later
            for row in g_l[k]
            if row.get("BilledCost") is not None
        ]
        new_rows_cost = round(sum(costs), 8) if costs else 0.0
    except (TypeError, ValueError):
        new_rows_cost = None

    notes = [
        "Grouping key: (ServiceName, ChargeCategory, ChargePeriodStart[:10], ResourceId).",
        "This is a descriptive comparison only. Groups are not billing identity keys.",
        "A group appearing in a later delivery does not prove a billing revision.",
        ("A group with changed row count does not prove a billing revision without a "
        "verified stable row-level key."),
    ]

    return SnapshotDiff(
        earlier_label=earlier.label,
        later_label=later.label,
        earlier_row_count=earlier.row_count,
        later_row_count=later.row_count,
        groups_in_earlier=len(keys_e),
        groups_in_later=len(keys_l),
        groups_in_both=len(keys_both),
        new_groups_in_later=len(new_in_later),
        removed_groups_in_earlier=len(removed_in_earlier),
        changed_count_groups=changed_count,
        new_rows_in_later=new_rows,
        new_rows_sum_billed_usd=new_rows_cost,
        is_strictly_additive=len(removed_in_earlier) == 0,
        revision_claim="unproven — no verified row-level key exists",
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_feasibility_result(*, skip_diff: bool = False,
                              allow_unlisted: bool = False) -> FeasibilityResult:
    """Load all approved FOCUS deliveries and build a FeasibilityResult.

    Parameters
    ----------
    skip_diff:
        If True, skip the O(n²) delivery comparison step.  Useful for fast
        unit tests that do not need diff output.

    Returns a FeasibilityResult with:
    - All three deliveries loaded and checksummed.
    - Delivery-to-delivery descriptive diffs (D1→D2, D2→D3) unless skip_diff.
    - An empty evaluate_input (both arrays empty) with a clear infeasibility reason,
      because no CALIBUR admission records exist to join against.
    - All FOCUS rows classified as unmatched with explicit reason.
    """
    s3_obs = _load_s3_observations()
    deliveries: list[DeliverySnapshot] = []
    for entry in DELIVERY_REGISTRY:
        snap = _load_delivery(entry, s3_obs, allow_unlisted=allow_unlisted)
        deliveries.append(snap)

    diffs: list[SnapshotDiff] = []
    if not skip_diff and len(deliveries) >= 2:
        for i in range(len(deliveries) - 1):
            diffs.append(_diff_deliveries(deliveries[i], deliveries[i + 1]))

    # Latest delivery row count for unmatched tally.
    latest = deliveries[-1] if deliveries else None
    total_rows = latest.row_count if latest else 0

    infeasibility_reason = (
        "No CALIBUR-managed admission records exist to join against FOCUS billing rows.  "
        "All 12 CloudTrail RunInstances events in October 2026 were initiated by root "
        "or AutoScaling for unrelated workloads.  DynamoDB contains no Spendctl "
        "reservation records.  Tags maps are empty in all three deliveries.  "
        "The end-to-end join (reservation_id → resource_id → FOCUS row) cannot be "
        "constructed without at least one human-approved CALIBUR admission."
    )

    blocked = [
        ("evaluate.py empirical analysis: no FINAL-settled reservations with "
        "price_snapshot_id and billing_audit to join"),
        "billing revision detection: no verified row-level unique key (InvoiceId fully null)",
        ("billing arrival delay measurement: no arrival timestamp in FOCUS 1.2 schema; "
        "first_observed_utc records collector observation, not billing record availability"),
        "per-request cost attribution: Tags fully empty, no reservation-id tag active",
    ]

    notes = [
        "All FOCUS rows are unmatched because no CALIBUR admission records exist.",
        "Unmatched rows are retained with explicit reason; they are not discarded.",
        ("first_observed_utc is the s3_evidence_collector observation timestamp, "
        "not the time a billing record first became available to CALIBUR."),
        "S3 LastModified is recorded separately and must not be used as arrival time.",
        ("To enable the empirical evaluate.py run: export DynamoDB reservation and "
        "billing_audit records after at least one FINAL-settled CALIBUR admission."),
    ]

    return FeasibilityResult(
        feasibility_gate="AMBER",
        admission_records_available=False,
        infeasibility_reason=infeasibility_reason,
        deliveries=deliveries,
        diffs=diffs,
        matched_rows=0,
        unmatched_rows=total_rows,
        ambiguous_rows=0,
        total_focus_rows=total_rows,
        evaluate_input={"reservations": [], "billing_audits": []},
        blocked_analyses=blocked,
        notes=notes,
    )


def feasibility_to_json(result: FeasibilityResult) -> str:
    """Serialize a FeasibilityResult to a JSON string."""
    d = asdict(result)
    return json.dumps(d, indent=2, default=str)


def load_deliveries_only() -> list[DeliverySnapshot]:
    """Load and verify all approved deliveries without computing diffs.

    Suitable for lightweight probes (e.g., count rows, verify checksums)
    without loading all rows twice for diff computation.
    """
    s3_obs = _load_s3_observations()
    return [_load_delivery(entry, s3_obs) for entry in DELIVERY_REGISTRY]
