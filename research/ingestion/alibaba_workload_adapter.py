"""Read-only adapter that converts Alibaba batch_task records into a
WorkloadObservation stream for CALIBUR descriptive workload analysis.

The Alibaba Cluster Trace 2017 provides cluster workload observations:
task shapes (cpu requested, memory requested), instance counts, durations
inferred from trace timestamps (where non-null), and status.

WHAT THIS ADAPTER DOES NOT DO:
- Does not assign AWS prices or dollar costs to Alibaba tasks.
- Does not claim Alibaba tasks have matching AWS billing records.
- Does not convert trace normalised-memory units to bytes or GB.
- Does not treat trace timestamps as wall-clock or billing-arrival times.
- Does not join Alibaba records to FOCUS billing records.
- Does not feed observations into evaluate.py (no CALIBUR reservation IDs exist).

WHAT THIS ADAPTER DOES:
- Streams batch_task.csv records via alibaba_loader.iter_file().
- Computes a duration_seconds field = end_timestamp - start_timestamp where
  both are non-None.  Duration is in trace time units (seconds relative to
  the trace epoch).  This is NOT wall-clock duration.
- Preserves all original field names and values.
- Labels each observation with source, schema version, and approval status.
- Reports missing fields explicitly (None, never imputed).
- Produces a WorkloadStream dataclass that can be consumed by descriptive
  analysis scripts once the Alibaba license is confirmed.

APPROVAL GATE:
The Alibaba source is currently UNAPPROVED pending license confirmation.
This adapter raises AlibabaSourceUnapproved unless called with
``allow_unapproved=True``.  Setting allow_unapproved=True is an operator
assertion that they have independently verified the license; the gate
exists to prevent accidental use in empirical results before confirmation.

See research/data_feasibility.md and research/data_card.md for context.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW_DIR = _REPO_ROOT / "data" / "raw" / "alibaba_trace_2017" / "alibaba-trace-2017"

# Approval status — not changed here; change only after independent license review.
_ALIBABA_APPROVED = False
_APPROVAL_REASON = (
    "Alibaba source is UNAPPROVED pending independent confirmation that CC BY 4.0 "
    "applies to the v2017 trace archive specifically.  Do not use in empirical results "
    "until the license is confirmed and this constant is updated."
)


class AlibabaSourceUnapproved(Exception):
    """Raised when the Alibaba source is accessed without operator approval gate."""


@dataclass(frozen=True)
class WorkloadObservation:
    """One batch_task row expressed as a workload observation.

    All fields are in Alibaba trace units.  No AWS costs or billing amounts
    are attached.  Timestamps are opaque integers (trace epoch offset, seconds).

    Fields marked None were blank in the source data; they are never imputed.
    """
    # Provenance
    source: str                         # "alibaba_cluster_trace_v2017"
    source_file: str                    # "batch_task.csv"
    row_number: int                     # 1-based row number in the CSV
    schema_version: str                 # from trace_201708.md

    # Original identifiers (preserved verbatim)
    job_id: int | None
    task_id: int | None

    # Task shape
    instance_count: int | None
    cpu_requested: int | None           # integer CPU units (NOT normalised)
    mem_requested_normalised: float | None  # normalised; NOT bytes or GB

    # Timestamps (seconds relative to trace epoch; NOT wall-clock)
    start_timestamp: int | None
    end_timestamp: int | None

    # Status
    status: str | None

    # Derived field — computed only when both timestamps are non-None
    duration_trace_seconds: int | None  # end - start; in trace time units

    # Quality flags
    missing_fields: list[str] = field(default_factory=list)  # field names that are None


@dataclass
class WorkloadStream:
    """Summary of a streaming pass over batch_task.csv."""
    source: str
    source_file: str
    schema_version: str
    sha256: str
    sha256_verified: bool
    total_rows: int
    rows_with_duration: int             # rows where both timestamps are non-None
    rows_missing_start: int
    rows_missing_end: int
    rows_missing_cpu: int
    rows_missing_mem: int
    status_counts: dict[str, int]
    duration_p50_trace_s: int | None    # median duration in trace units (None if < 10 rows)
    duration_p90_trace_s: int | None
    duration_p95_trace_s: int | None
    duration_max_trace_s: int | None
    approval_gate: str                  # "UNAPPROVED" or "APPROVED_ASSERTED"
    blocked_analyses: list[str]
    notes: list[str] = field(default_factory=list)
    # observations list is NOT included in WorkloadStream to avoid holding
    # the entire dataset in memory.  Use iter_observations() to stream.


def iter_observations(
    *,
    raw_dir: Path | None = None,
    allow_unapproved: bool = False,
) -> Iterator[WorkloadObservation]:
    """Stream WorkloadObservation records from batch_task.csv.

    Parameters
    ----------
    raw_dir:
        Override the raw data directory (for tests with tmp fixtures).
    allow_unapproved:
        If True, bypass the AlibabaSourceUnapproved gate.  This is an
        operator assertion that the license has been verified.

    Raises
    ------
    AlibabaSourceUnapproved
        If the source is not approved and allow_unapproved is False.
    FileNotFoundError
        If batch_task.csv does not exist at the resolved path.
    """
    if not _ALIBABA_APPROVED and not allow_unapproved:
        raise AlibabaSourceUnapproved(_APPROVAL_REASON)

    from .alibaba_loader import load_file

    base = raw_dir if raw_dir is not None else _RAW_DIR
    result = load_file("batch_task.csv", raw_dir=base)

    for row in result.rows:
        f = row.fields
        start = f.get("task_create_time")
        end = f.get("task_end_time")
        duration: int | None = None
        if start is not None and end is not None:
            try:
                duration = int(end) - int(start)
            except (TypeError, ValueError):
                duration = None

        missing: list[str] = [
            name for name in (
                "task_create_time", "task_end_time", "job_id", "task_id",
                "instance_count", "cpu_requested", "mem_requested_normalised", "status",
            )
            if f.get(name) is None
        ]

        yield WorkloadObservation(
            source="alibaba_cluster_trace_v2017",
            source_file="batch_task.csv",
            row_number=row.row_number,
            schema_version="trace_201708.md",
            job_id=f.get("job_id"),
            task_id=f.get("task_id"),
            instance_count=f.get("instance_count"),
            cpu_requested=f.get("cpu_requested"),
            mem_requested_normalised=f.get("mem_requested_normalised"),
            start_timestamp=start,
            end_timestamp=end,
            status=f.get("status"),
            duration_trace_seconds=duration,
            missing_fields=missing,
        )


def build_workload_stream(
    *,
    raw_dir: Path | None = None,
    allow_unapproved: bool = False,
) -> WorkloadStream:
    """Stream batch_task.csv and return a WorkloadStream summary.

    Does not hold all rows in memory.  Computes summary statistics in one pass
    (except percentiles, which require a second sort pass over durations).
    """
    if not _ALIBABA_APPROVED and not allow_unapproved:
        raise AlibabaSourceUnapproved(_APPROVAL_REASON)

    from .alibaba_loader import load_file

    base = raw_dir if raw_dir is not None else _RAW_DIR
    # Load to get sha256 metadata; then stream observations.
    load_result = load_file("batch_task.csv", raw_dir=base)

    total_rows = 0
    rows_with_duration = 0
    missing_start = 0
    missing_end = 0
    missing_cpu = 0
    missing_mem = 0
    status_counts: dict[str, int] = {}
    durations: list[int] = []

    for obs in iter_observations(raw_dir=base, allow_unapproved=True):
        total_rows += 1
        if obs.start_timestamp is None:
            missing_start += 1
        if obs.end_timestamp is None:
            missing_end += 1
        if obs.cpu_requested is None:
            missing_cpu += 1
        if obs.mem_requested_normalised is None:
            missing_mem += 1
        s = obs.status or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1
        if obs.duration_trace_seconds is not None and obs.duration_trace_seconds >= 0:
            rows_with_duration += 1
            durations.append(obs.duration_trace_seconds)

    durations.sort()
    n = len(durations)

    def percentile(p: float) -> int | None:
        if n < 10:
            return None
        idx = max(0, min(n - 1, int(p * n)))
        return durations[idx]

    blocked = [
        ("AWS cost estimation from Alibaba tasks: "
        "no verified mapping from trace cpu_requested/mem_requested_normalised "
        "to AWS instance type or billing amount exists"),
        ("Empirical evaluate.py input: no CALIBUR reservation IDs exist to join "
        "with Alibaba task identifiers"),
        "All workload analyses: source is UNAPPROVED pending license confirmation",
    ] if not _ALIBABA_APPROVED else [
        ("AWS cost estimation from Alibaba tasks: "
        "no verified mapping from trace cpu_requested/mem_requested_normalised "
        "to AWS instance type or billing amount exists"),
        ("Empirical evaluate.py input: no CALIBUR reservation IDs exist to join "
        "with Alibaba task identifiers"),
    ]

    notes = [
        ("duration_trace_seconds = end_timestamp - start_timestamp; "
        "values are in trace time units (seconds relative to an opaque epoch), "
        "NOT wall-clock seconds."),
        ("mem_requested_normalised is in Alibaba trace normalised units; "
        "NOT bytes, GB, or any AWS memory unit."),
        ("cpu_requested is an integer CPU count in the trace; "
        "NOT an AWS vCPU count or instance-type specification."),
        f"Approval gate: {'UNAPPROVED' if not _ALIBABA_APPROVED else 'APPROVED_ASSERTED'}",
        f"Rows with both timestamps: {rows_with_duration}/{total_rows}",
    ]

    return WorkloadStream(
        source="alibaba_cluster_trace_v2017",
        source_file="batch_task.csv",
        schema_version="trace_201708.md",
        sha256=load_result.sha256,
        sha256_verified=load_result.sha256_verified,
        total_rows=total_rows,
        rows_with_duration=rows_with_duration,
        rows_missing_start=missing_start,
        rows_missing_end=missing_end,
        rows_missing_cpu=missing_cpu,
        rows_missing_mem=missing_mem,
        status_counts=status_counts,
        duration_p50_trace_s=percentile(0.50),
        duration_p90_trace_s=percentile(0.90),
        duration_p95_trace_s=percentile(0.95),
        duration_max_trace_s=max(durations) if durations else None,
        approval_gate="UNAPPROVED" if not _ALIBABA_APPROVED else "APPROVED_ASSERTED",
        blocked_analyses=blocked,
        notes=notes,
    )
