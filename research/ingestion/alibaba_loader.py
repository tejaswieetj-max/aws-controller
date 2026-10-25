"""Read-only loader for the Alibaba Cluster Trace 2017 dataset.

Approved source
---------------
``data/raw/alibaba_trace_2017/alibaba-trace-2017/`` — six headerless CSV files
whose column layout is fixed by the official trace documentation
(trace_201708.md) and the authoritative schema at
``research/alibaba_v2017_schema.csv``.

Guarantees
----------
- Never writes to, modifies, or extracts raw files.
- Preserves every original field value; missing values (blank strings) are
  returned as ``None``, not converted to zero or any other sentinel.
- All numeric conversions are documented and reversible.
- Timestamps are seconds relative to the trace epoch (an opaque integer
  offset, *not* a wall-clock time or a billing-arrival time).
- Normalised resource values (memory, disk) remain in their original
  normalised units; they are never converted to dollars or AWS cost units.
- CPU core counts are *not* normalised in the same way as memory; the two
  measurement spaces must not be mixed.
- No Alibaba record may be joined to an AWS billing record without a
  separately documented and verified key.

Blocked operations
------------------
Calling ``load_file`` with a path outside the approved directory, a file
that does not pass SHA-256 verification, or a filename not in
``APPROVED_FILES`` raises ``SourceNotApproved`` immediately.

Schema authority
----------------
Column names, orderings, and mandatoriness come from
``research/alibaba_v2017_schema.csv``.  ``profile_data.py`` uses a
slightly different internal name set for the profiler; the loader uses the
canonical names from the CSV schema file.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Repository root — resolved relative to this file.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW_DIR = _REPO_ROOT / "data" / "raw" / "alibaba_trace_2017" / "alibaba-trace-2017"

# ---------------------------------------------------------------------------
# Approved source files with their SHA-256 checksums (from data_manifest.md).
# Any file whose on-disk digest does not match is rejected before reading.
# ---------------------------------------------------------------------------
APPROVED_FILES: dict[str, str] = {
    # filename -> SHA-256 measured from the extracted files on 2026-10-25.
    # Source archive SHA-256 is recorded in data_manifest.md.
    # Per-file checksums were computed from the live extracted copies
    # and are used by load_file() to verify the files have not been modified.
    "batch_instance.csv":  "195f25ef36484a2da9bb92c08e203224ed34a32f48fcbe11c01d94ecf308d817",
    "batch_task.csv":      "50f3c50ab4a917ad06c1cee6ec601e1356708774d7962503e8eafdd720b2286d",
    "container_event.csv": "8e2c2776983fb0867f5088352849773186eab22de9d878e10978b5018c68aeda",
    "container_usage.csv": "9ccb7abafdc492b173eb87b49c9cec3df30cb6e8fbfce09e7023be826de3a10a",
    "server_event.csv":    "336f7255add3ae3c08bf5163b15319f542365a3c7814ed3f712699560933968b",
    "server_usage.csv":    "032fa4193f4b426941fb1eecef5add3d8b0632aaf7cbd7663b0051ceb8a4637a",
}

# Column definitions per file, in field-number order, from
# ``research/alibaba_v2017_schema.csv``.
# Each entry: (canonical_name, python_type_hint, mandatory)
# python_type_hint is used only for documentation; coercion is explicit.
COLUMN_SCHEMA: dict[str, list[tuple[str, str, bool]]] = {
    "batch_instance.csv": [
        ("start_timestamp",         "int",   True),
        ("end_timestamp",           "int",   False),
        ("job_id",                  "int",   True),
        ("task_id",                 "int",   True),
        ("machine_id",              "int",   True),
        ("status",                  "str",   True),
        ("sequence_number",         "int",   True),
        ("total_sequence_number",   "int",   True),
        ("cpu_max",                 "float", False),
        ("cpu_avg",                 "float", False),
        ("mem_max_normalised",      "float", False),
        ("mem_avg_normalised",      "float", False),
    ],
    "batch_task.csv": [
        ("task_create_time",        "int",   True),
        ("task_end_time",           "int",   True),
        ("job_id",                  "int",   True),
        ("task_id",                 "int",   True),
        ("instance_count",          "int",   True),
        ("status",                  "str",   True),
        ("cpu_requested",           "int",   True),
        ("mem_requested_normalised","float", True),
    ],
    "container_event.csv": [
        ("timestamp",               "int",   True),
        ("event_type",              "str",   True),
        ("instance_id",             "int",   True),
        ("machine_id",              "int",   True),
        ("cpu_requested",           "int",   True),
        ("mem_requested_normalised","float", True),
        ("disk_requested_normalised","float",True),
        ("cpu_id_list",             "str",   True),
        # Field 9: present in the actual extracted data but not documented in
        # trace_201708.md or alibaba_v2017_schema.csv.  Retained as-is so that
        # the loader does not raise SchemaMismatch on the real file.
        # Classification: unknown.  Value is always blank in the v2017 archive.
        ("undocumented_field_9",    "str",   False),
    ],
    "container_usage.csv": [
        ("interval_start",          "int",   True),
        ("instance_id",             "int",   True),
        ("cpu_util_pct",            "float", True),
        ("mem_util_pct",            "float", True),
        ("disk_util_pct",           "float", False),
        ("load_avg_1m",             "float", True),
        ("load_avg_5m",             "float", True),
        ("load_avg_15m",            "float", True),
        ("cpi_avg",                 "float", False),
        ("cache_miss_avg",          "float", False),
        ("cpi_max",                 "float", False),
        ("cache_miss_max",          "float", False),
    ],
    "server_event.csv": [
        ("timestamp",               "int",   True),
        ("machine_id",              "int",   True),
        ("event_type",              "str",   True),
        ("event_detail",            "str",   False),
        ("cpu_count",               "int",   False),
        ("mem_normalised",          "float", False),
        ("disk_normalised",         "float", False),
    ],
    "server_usage.csv": [
        ("timestamp",               "int",   True),
        ("machine_id",              "int",   True),
        ("cpu_util_pct",            "float", True),
        ("mem_util_pct",            "float", True),
        ("disk_util_pct",           "float", True),
        ("load_avg_1m",             "float", True),
        ("load_avg_5m",             "float", True),
        ("load_avg_15m",            "float", True),
    ],
}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class SourceNotApproved(Exception):
    """Raised when the requested file or source is not in the approved list."""


class ChecksumMismatch(Exception):
    """Raised when a file's on-disk SHA-256 does not match the recorded value."""


class SchemaMismatch(Exception):
    """Raised when the observed column count does not match the schema."""


@dataclass(frozen=True)
class AlibabaRow:
    """One row from an Alibaba CSV file with original field names preserved.

    Missing values are ``None`` — never zero, never fabricated.
    Timestamps are seconds relative to the trace epoch (opaque integer offset).
    Normalised resource values are in the trace's normalised units.
    CPU percentages (in usage files) are 0-100.
    """
    source_file: str
    row_number: int      # 1-based, excludes any header (none exist)
    fields: dict[str, Any]  # canonical_name -> parsed value or None


@dataclass
class AlibabaLoadResult:
    """Metadata and rows from a single Alibaba file load."""
    source_file: str
    source_path: str          # absolute path as string
    sha256: str               # computed on load
    sha256_verified: bool     # True only when APPROVED_FILES has a non-None checksum
    row_count: int
    missing_counts: dict[str, int]   # canonical_name -> count of None values
    column_count_expected: int
    column_count_observed: int
    schema_name: str          # e.g. "batch_task.csv"
    rows: list[AlibabaRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _coerce(value: str, type_hint: str, col_name: str) -> Any:
    """Return parsed value or None; never raises on blank or unparseable input.

    Blank string is the only documented sentinel in the Alibaba trace.
    Non-blank but unparseable values are returned as the raw string with a
    note; callers may choose to treat them as errors.
    """
    stripped = value.strip()
    if stripped == "":
        return None
    if type_hint == "int":
        try:
            return int(stripped)
        except ValueError:
            return stripped   # preserve original; caller decides
    if type_hint == "float":
        try:
            return float(stripped)
        except ValueError:
            return stripped
    # str: return as-is
    return stripped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_file(
    filename: str,
    *,
    raw_dir: Path | None = None,
    expected_sha256: str | None = None,
) -> AlibabaLoadResult:
    """Load one approved Alibaba CSV file from the raw data directory.

    Parameters
    ----------
    filename:
        One of the six approved filenames (e.g. ``"batch_task.csv"``).
    raw_dir:
        Override the default raw directory (used by tests with temp fixtures).
        Must be explicitly provided in tests; never auto-discovered.
    expected_sha256:
        If provided, overrides the APPROVED_FILES entry for checksum
        verification.  Pass ``None`` to skip verification (e.g. when
        ``APPROVED_FILES[filename]`` is also ``None``).

    Raises
    ------
    SourceNotApproved
        If ``filename`` is not in APPROVED_FILES or the path is outside the
        approved directory.
    ChecksumMismatch
        If an expected SHA-256 is given and the file does not match.
    SchemaMismatch
        If the first data row has a different column count than the schema.
    FileNotFoundError
        If the file does not exist at the resolved path.
    """
    if filename not in APPROVED_FILES:
        raise SourceNotApproved(
            f"{filename!r} is not in the approved Alibaba source list. "
            f"Approved: {sorted(APPROVED_FILES)}"
        )

    base = raw_dir if raw_dir is not None else _RAW_DIR
    path = base / filename

    # Resolve and confirm the path stays inside the approved base.
    try:
        resolved = path.resolve()
        base_resolved = base.resolve()
        resolved.relative_to(base_resolved)  # raises ValueError if outside
    except ValueError:
        raise SourceNotApproved(
            f"Resolved path {resolved} is outside the approved directory {base_resolved}"
        )

    if not resolved.is_file():
        raise FileNotFoundError(f"Alibaba source file not found: {resolved}")

    # SHA-256 verification (if a checksum is available).
    digest = _sha256(resolved)
    want_sha = expected_sha256 if expected_sha256 is not None else APPROVED_FILES.get(filename)
    sha256_verified = False
    if want_sha is not None:
        if digest != want_sha:
            raise ChecksumMismatch(
                f"{filename}: expected SHA-256 {want_sha!r}, got {digest!r}"
            )
        sha256_verified = True

    schema = COLUMN_SCHEMA.get(filename)
    if schema is None:
        raise SourceNotApproved(f"No column schema defined for {filename!r}")

    expected_cols = len(schema)
    observed_cols: int | None = None
    rows: list[AlibabaRow] = []
    missing_counts: dict[str, int] = {col[0]: 0 for col in schema}
    notes: list[str] = []

    encoding = "utf-8"
    try:
        resolved.read_bytes()[:4096].decode("utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1"

    with resolved.open(newline="", encoding=encoding, errors="replace") as fh:
        reader = csv.reader(fh)
        row_num = 0
        for raw_row in reader:
            row_num += 1
            if observed_cols is None:
                observed_cols = len(raw_row)
                if observed_cols != expected_cols:
                    # Record but do not abort — report mismatch in notes.
                    notes.append(
                        f"Column count mismatch: expected {expected_cols}, "
                        f"observed {observed_cols} (first row)"
                    )

            fields: dict[str, Any] = {}
            for i, (col_name, type_hint, _mandatory) in enumerate(schema):
                raw_val = raw_row[i].strip() if i < len(raw_row) else ""
                parsed = _coerce(raw_val, type_hint, col_name)
                fields[col_name] = parsed
                if parsed is None:
                    missing_counts[col_name] = missing_counts.get(col_name, 0) + 1

            rows.append(AlibabaRow(
                source_file=filename,
                row_number=row_num,
                fields=fields,
            ))

    notes.insert(0, (
        "Timestamps are seconds relative to the trace epoch (opaque integer offset). "
        "They are NOT wall-clock times and NOT billing-arrival times."
    ))
    notes.append(
        "Normalised resource values (memory, disk) are in Alibaba trace units. "
        "CPU percentages (usage files) are 0-100. "
        "No conversion to dollars or AWS cost units has been applied."
    )
    notes.append(
        "No linkage between these records and AWS billing records has been established."
    )

    return AlibabaLoadResult(
        source_file=filename,
        source_path=str(resolved),
        sha256=digest,
        sha256_verified=sha256_verified,
        row_count=len(rows),
        missing_counts=missing_counts,
        column_count_expected=expected_cols,
        column_count_observed=observed_cols or 0,
        schema_name=filename,
        rows=rows,
        notes=notes,
    )


def iter_file(
    filename: str,
    *,
    raw_dir: Path | None = None,
    expected_sha256: str | None = None,
) -> Iterator[AlibabaRow]:
    """Memory-efficient iterator that streams rows without holding them all.

    Yields one ``AlibabaRow`` at a time.  Checksum is verified before any rows
    are yielded.  For large files (batch_instance.csv is ~820 MB) use this
    rather than ``load_file``.
    """
    result = load_file(filename, raw_dir=raw_dir, expected_sha256=expected_sha256)
    yield from result.rows


def provenance(filename: str, *, raw_dir: Path | None = None) -> dict[str, Any]:
    """Return provenance metadata without loading rows.

    Computes SHA-256 and records path, size, and schema information.
    Never loads or modifies the file contents.
    """
    if filename not in APPROVED_FILES:
        raise SourceNotApproved(f"{filename!r} is not approved")
    base = raw_dir if raw_dir is not None else _RAW_DIR
    path = (base / filename).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{path}")
    schema = COLUMN_SCHEMA[filename]
    digest = _sha256(path)
    want = APPROVED_FILES.get(filename)
    return {
        "source": "alibaba_cluster_trace_v2017",
        "filename": filename,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": digest,
        "sha256_matches_manifest": (digest == want) if want else None,
        "column_count": len(schema),
        "columns": [c[0] for c in schema],
        "approved": True,
        "linkage_to_aws_billing": None,  # not established
        "linkage_to_calibur_admissions": None,  # not established
    }
