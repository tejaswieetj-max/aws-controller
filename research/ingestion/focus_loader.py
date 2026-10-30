"""Read-only loader for AWS FOCUS 1.2 billing export Parquet files.

Approved sources
----------------
``data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet`` (Delivery 1)
``data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet`` (Delivery 2)

Guarantees
----------
- Never writes to, modifies, or downloads raw files.
- Every original FOCUS field is preserved in ``FocusRow.fields``.
- Timestamps (BillingPeriodStart/End, ChargePeriodStart/End) are returned as
  UTC ISO-8601 strings.  They represent billing-period boundaries, NOT
  billing-record arrival times.  No arrival time exists in the FOCUS schema.
- Cost fields (BilledCost, EffectiveCost, ListCost) are kept as Python
  ``float`` (matching Parquet ``double``). Conversion to micro-USD is
  performed on explicit request via ``cost_to_micro()``, which rounds
  half-even, preserving sign.
- Missing values (Parquet nulls) are returned as ``None`` — never zero,
  never fabricated.
- ``InvoiceId`` is fully null in both known deliveries; this is recorded
  but no substitute key is invented.
- No Alibaba record may be joined to a FOCUS record without a separately
  documented and verified key.

Blocked operations
------------------
Calling ``load_parquet`` with a path whose SHA-256 does not match the
recorded value raises ``ChecksumMismatch``.  Calling with a path outside
the approved list raises ``SourceNotApproved`` unless ``allow_unlisted=True``
is passed (for research scripts comparing future deliveries).

Units
-----
All cost amounts are in USD (ISO 4217), as declared by the ``BillingCurrency``
field in the data.  The loader verifies this for every row; rows with a
non-USD currency raise ``UnexpectedCurrency``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repository root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FOCUS_DIR = _REPO_ROOT / "data" / "raw" / "focus12"

# ---------------------------------------------------------------------------
# Approved deliveries: path-relative-to-repo -> recorded SHA-256
# Values come from research/data_manifest.md (verified 2026-10-25).
# ---------------------------------------------------------------------------
APPROVED_DELIVERIES: dict[str, str] = {
    "data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet":
        "e829bfa81b344c07e7803d52bd7e9863eba2240e37cea4a701c2dc48f5220597",
    "data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet":
        "df688f94a119d2cc92b0347dc86b7ee4b3ec383a58671de2ce216347f375378b",
    # Delivery 3 — execution ID 2719ee81-66bd-346a-93bc-ec5e99dc1747
    # S3 delivery timestamp: 2026-10-25T11:18:08.607Z
    # First observed by s3_evidence_collector: 2026-10-25T11:32:29.273473Z
    # 12,579 rows, 60 columns. 140 rows present in D3 but not D2, spanning
    # ChargePeriodStart dates 2026-10-24 (3 rows) and 2026-10-25 (137 rows).
    # D3 is a strictly additive snapshot: all D2 descriptive groups are present.
    # InvoiceId fully null. Tags fully empty. No verified row-level unique key.
    "data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-00001.snappy.parquet":
        "a6c184a5cae50b43d0a7d0e46c5987ff8852cd8aeb4799b4cfa7bb3e1e1c08f0",
}

# Required FOCUS 1.2 columns that must be present and non-null.
# Source: CALIBUR_FOCUS_12-Manifest-FOCUS.json (FocusVersion 1.2).
FOCUS_REQUIRED_COLUMNS: frozenset[str] = frozenset({
    "BilledCost",
    "BillingCurrency",
    "BillingPeriodEnd",
    "BillingPeriodStart",
    "ChargeCategory",
    "ChargeFrequency",
    "InvoiceIssuerName",
    "ProviderName",
    "RegionId",
    "RegionName",
    "ServiceCategory",
    "SubAccountType",
    "BillingAccountType",
})

# Timestamp columns whose values are billing-period boundaries,
# NOT billing-record arrival times.
PERIOD_TIMESTAMP_COLUMNS: frozenset[str] = frozenset({
    "BillingPeriodStart",
    "BillingPeriodEnd",
    "ChargePeriodStart",
    "ChargePeriodEnd",
})

MICRO_USD = Decimal(1000000)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SourceNotApproved(Exception):
    """Path not in the approved delivery list."""


class ChecksumMismatch(Exception):
    """On-disk SHA-256 does not match the recorded value."""


class MissingRequiredColumn(Exception):
    """A FOCUS 1.2 required column is absent from the Parquet schema."""


class UnexpectedCurrency(Exception):
    """A row contains a non-USD currency; multi-currency is not supported."""


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FocusRow:
    """One row from a FOCUS 1.2 Parquet file.

    ``fields`` contains every column present in the file.  Missing values
    (Parquet nulls) are ``None``.  Timestamps are UTC ISO-8601 strings.
    Cost values are Python ``float`` (Parquet ``double``).

    Provenance fields record which delivery file this row came from.
    """
    delivery_path: str       # relative path from repo root
    row_index: int           # 0-based
    fields: dict[str, Any]


@dataclass
class FocusLoadResult:
    """Metadata and rows from a single FOCUS delivery file."""
    delivery_path: str           # relative path from repo root
    absolute_path: str
    sha256: str
    sha256_verified: bool
    focus_version: str           # from manifest if available, else "inferred:1.2"
    row_count: int
    column_count: int
    billing_period_start: str | None   # min across rows (boundary, NOT arrival)
    billing_period_end: str | None     # max across rows (boundary, NOT arrival)
    currency: str | None               # "USD" or None if mixed/unknown
    missing_counts: dict[str, int]     # column -> null count
    missing_required: list[str]        # required columns that are absent
    rows: list[FocusRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ts_to_iso(value: Any) -> str | None:
    """Convert a pyarrow timestamp scalar or pandas Timestamp to UTC ISO-8601.

    Returns None if the value is None/NaT.
    Raises TypeError if the value cannot be interpreted as a timestamp.
    """
    if value is None:
        return None
    # pandas Timestamp (from pyarrow .to_pydict())
    try:
        import pandas as pd
        if isinstance(value, pd.Timestamp):
            if value is pd.NaT:
                return None
            return value.tz_convert("UTC").isoformat()
    except ImportError:
        pass
    # datetime
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    return str(value)


def cost_to_micro(usd_value: float | None) -> int | None:
    """Convert a USD float to micro-USD using half-even rounding.

    Returns ``None`` if ``usd_value`` is ``None``.
    Never converts None to zero.
    """
    if usd_value is None:
        return None
    try:
        d = Decimal(str(usd_value))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return int((d * MICRO_USD).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_parquet(
    path: str | Path,
    *,
    allow_unlisted: bool = False,
    expected_sha256: str | None = None,
) -> FocusLoadResult:
    """Load one approved FOCUS 1.2 Parquet file.

    Parameters
    ----------
    path:
        Absolute path or path relative to the repository root.
    allow_unlisted:
        If ``True``, accept files not in ``APPROVED_DELIVERIES`` (e.g. a
        future delivery being compared for the first time).  Checksum
        verification is skipped in this case unless ``expected_sha256`` is
        provided.
    expected_sha256:
        Override the checksum from ``APPROVED_DELIVERIES``.

    Raises
    ------
    SourceNotApproved
    ChecksumMismatch
    MissingRequiredColumn
    FileNotFoundError
    ImportError if pyarrow is not installed
    """
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError("pyarrow is required to load FOCUS Parquet files") from exc

    abs_path = Path(path) if Path(path).is_absolute() else (_REPO_ROOT / path)
    abs_path = abs_path.resolve()

    # Build relative path for approval lookup.
    try:
        rel_str = str(abs_path.relative_to(_REPO_ROOT.resolve()))
    except ValueError:
        rel_str = str(abs_path)

    approved_sha = APPROVED_DELIVERIES.get(rel_str)
    if approved_sha is None and not allow_unlisted:
        raise SourceNotApproved(
            f"{rel_str!r} is not in the approved FOCUS delivery list. "
            f"Pass allow_unlisted=True for new deliveries."
        )

    if not abs_path.is_file():
        raise FileNotFoundError(f"FOCUS Parquet file not found: {abs_path}")

    digest = _sha256_file(abs_path)
    want_sha = expected_sha256 or approved_sha
    sha256_verified = False
    if want_sha is not None:
        if digest != want_sha:
            raise ChecksumMismatch(
                f"{rel_str}: expected SHA-256 {want_sha!r}, got {digest!r}"
            )
        sha256_verified = True

    table = pq.read_table(abs_path)
    column_names: list[str] = table.schema.names
    row_count = len(table)

    # Check required columns.
    present = set(column_names)
    missing_required = sorted(FOCUS_REQUIRED_COLUMNS - present)
    if missing_required:
        raise MissingRequiredColumn(
            f"FOCUS 1.2 required columns absent: {missing_required}"
        )

    # Build column -> null count.
    missing_counts: dict[str, int] = {
        name: table.column(name).null_count for name in column_names
    }

    # Convert to Python dicts for row-level access.
    py_table = table.to_pydict()

    rows: list[FocusRow] = []
    billing_starts: list[str] = []
    billing_ends: list[str] = []
    currencies: set[str] = set()

    for i in range(row_count):
        row_fields: dict[str, Any] = {}
        for col in column_names:
            val = py_table[col][i]
            # Convert timestamps to UTC ISO-8601 strings.
            if col in PERIOD_TIMESTAMP_COLUMNS:
                val = _ts_to_iso(val)
            # None stays None — never convert to zero.
            row_fields[col] = val

        # Track billing period range.
        bs = row_fields.get("BillingPeriodStart")
        be = row_fields.get("BillingPeriodEnd")
        if bs:
            billing_starts.append(bs)
        if be:
            billing_ends.append(be)

        # Currency check.
        curr = row_fields.get("BillingCurrency")
        if curr is not None:
            currencies.add(str(curr))

        rows.append(FocusRow(
            delivery_path=rel_str,
            row_index=i,
            fields=row_fields,
        ))

    # Validate currency.
    non_usd = currencies - {"USD"}
    if non_usd:
        raise UnexpectedCurrency(
            f"Non-USD currencies found: {sorted(non_usd)}. "
            "Multi-currency is not supported by the current loader."
        )

    currency = "USD" if currencies == {"USD"} else (next(iter(currencies)) if len(currencies) == 1 else None)

    notes = [
        ("BillingPeriodStart/End and ChargePeriodStart/End are billing-period boundary "
        "timestamps. They are NOT billing-record arrival times. No arrival time exists "
        "in the FOCUS 1.2 schema."),
        ("InvoiceId is fully null in known deliveries. No stable row-level unique key "
        "has been established. No key will be invented."),
        ("No linkage between these records and CALIBUR admission decisions or Alibaba "
        "workload records has been established."),
        f"Delivery: {rel_str}",
        f"SHA-256 verified: {sha256_verified}",
    ]
    if missing_counts.get("ResourceId", 0) > 0:
        notes.append(
            f"ResourceId is null for {missing_counts['ResourceId']} of {row_count} rows. "
            "Linkage via ResourceId alone is not possible for those rows."
        )

    return FocusLoadResult(
        delivery_path=rel_str,
        absolute_path=str(abs_path),
        sha256=digest,
        sha256_verified=sha256_verified,
        focus_version="inferred:1.2",   # from manifest; no field in data
        row_count=row_count,
        column_count=len(column_names),
        billing_period_start=min(billing_starts) if billing_starts else None,
        billing_period_end=max(billing_ends) if billing_ends else None,
        currency=currency,
        missing_counts=missing_counts,
        missing_required=missing_required,
        rows=rows,
        notes=notes,
    )


def provenance(path: str | Path, *, allow_unlisted: bool = False) -> dict[str, Any]:
    """Return provenance metadata without loading rows.

    Computes SHA-256 and records path, size, and approval status.
    Does not read any row data.
    """
    abs_path = Path(path) if Path(path).is_absolute() else (_REPO_ROOT / path)
    abs_path = abs_path.resolve()
    try:
        rel_str = str(abs_path.relative_to(_REPO_ROOT.resolve()))
    except ValueError:
        rel_str = str(abs_path)

    approved_sha = APPROVED_DELIVERIES.get(rel_str)
    if approved_sha is None and not allow_unlisted:
        raise SourceNotApproved(f"{rel_str!r} is not in the approved list")
    if not abs_path.is_file():
        raise FileNotFoundError(f"{abs_path}")

    digest = _sha256_file(abs_path)
    return {
        "source": "aws_focus_1.2",
        "delivery_path": rel_str,
        "absolute_path": str(abs_path),
        "size_bytes": abs_path.stat().st_size,
        "sha256": digest,
        "sha256_matches_manifest": (digest == approved_sha) if approved_sha else None,
        "approved": approved_sha is not None,
        "focus_version": "inferred:1.2",
        "linkage_to_alibaba": None,        # not established
        "linkage_to_calibur_admissions": None,  # not established
        "billing_arrival_time": None,      # not available in FOCUS schema
    }
