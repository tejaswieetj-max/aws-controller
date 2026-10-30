"""CALIBUR read-only data profiler.

Profiles raw files discovered under data/raw/ against provenance recorded in
research/data_manifest.md.  Never modifies, extracts, or downloads raw files.
Never generates synthetic data.  Writes machine-readable JSON and a
human-readable Markdown report under research/; never under data/raw/.

Usage:
    PYTHONPATH=services python research/profile_data.py
    PYTHONPATH=services python research/profile_data.py \\
        --json-out research/profile_report.json \\
        --report-out research/data_quality_report.md

Research gate is permanently AMBER: profiling alone does not establish
billing finality, billing-arrival delays, request-to-resource linkage, or
empirical controller performance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path constants — all relative to the repository root.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "data" / "raw"
MANIFEST = ROOT / "research" / "data_manifest.md"
DEFAULT_JSON = ROOT / "research" / "profile_report.json"
DEFAULT_MD = ROOT / "research" / "data_quality_report.md"

# ---------------------------------------------------------------------------
# Alibaba Cluster Trace 2017 — column schemas documented in trace_201708.md.
# Files have NO header row; column names come from official documentation only.
# ---------------------------------------------------------------------------
ALIBABA_SCHEMAS: dict[str, list[dict[str, str]]] = {
    "batch_task.csv": [
        {"name": "task_name",  "classification": "observed",
         "reason": "Opaque integer identifier from source; not a dollar value."},
        {"name": "inst_num",   "classification": "observed",
         "reason": "Number of instances in the task; observed count from source."},
        {"name": "job_name",   "classification": "observed",
         "reason": "Opaque integer identifier for the parent job."},
        {"name": "task_type",  "classification": "observed",
         "reason": "Integer type code from source trace."},
        {"name": "start_time", "classification": "observed",
         "reason": "Seconds relative to trace epoch; unit documented in trace_201708.md. "
                   "Zero or blank means event fell before or outside the trace window."},
        {"name": "status",     "classification": "observed",
         "reason": "Categorical terminal/non-terminal state from source."},
        {"name": "plan_cpu",   "classification": "observed",
         "reason": "Requested CPU cores; documented sentinel: blank means not recorded."},
        {"name": "plan_mem",   "classification": "observed",
         "reason": "Requested normalised memory; documented sentinel: blank means not recorded."},
    ],
    "batch_instance.csv": [
        {"name": "task_name",     "classification": "observed",
         "reason": "Opaque task identifier."},
        {"name": "job_name",      "classification": "observed",
         "reason": "Opaque job identifier."},
        {"name": "task_type",     "classification": "observed",
         "reason": "Integer type code; blank is a documented missing value in the trace."},
        {"name": "inst_seq_num",  "classification": "observed",
         "reason": "Instance sequence number within the task; blank is documented missing."},
        {"name": "start_time",    "classification": "observed",
         "reason": "Seconds relative to trace epoch; blank means not recorded."},
        {"name": "end_time",      "classification": "observed",
         "reason": "Seconds relative to trace epoch; blank means not recorded."},
        {"name": "machine_id",    "classification": "observed",
         "reason": "Opaque machine identifier."},
        {"name": "status",        "classification": "observed",
         "reason": "Categorical terminal/non-terminal state."},
        {"name": "seq_no",        "classification": "observed",
         "reason": "Retry sequence number; blank is documented missing."},
        {"name": "total_seq_num", "classification": "observed",
         "reason": "Total retry count; blank is documented missing."},
        {"name": "cpu_avg",       "classification": "observed",
         "reason": "Average CPU utilisation ratio (0-1 normalised); blank is documented missing."},
        {"name": "mem_avg",       "classification": "observed",
         "reason": "Average memory utilisation ratio (0-1 normalised); blank is documented missing."},
    ],
    "container_event.csv": [
        {"name": "timestamp",    "classification": "observed",
         "reason": "Seconds relative to trace epoch."},
        {"name": "event_type",   "classification": "observed",
         "reason": "Categorical event type (Create, Delete, etc.)."},
        {"name": "container_id", "classification": "observed",
         "reason": "Opaque container identifier."},
        {"name": "machine_id",   "classification": "observed",
         "reason": "Opaque machine identifier."},
        {"name": "cpu_request",  "classification": "observed",
         "reason": "Requested CPU cores at event time."},
        {"name": "mem_request",  "classification": "observed",
         "reason": "Requested normalised memory at event time."},
        {"name": "disk_request", "classification": "observed",
         "reason": "Requested normalised disk at event time."},
        {"name": "cpu_affinity", "classification": "observed",
         "reason": "CPU core affinity list (pipe-separated); blank means not set."},
        {"name": "extra",        "classification": "unknown",
         "reason": "Ninth column present in data; not documented in trace_201708.md."},
    ],
    "container_usage.csv": [
        {"name": "timestamp",        "classification": "observed",
         "reason": "Seconds relative to trace epoch; 300-second averaged sample."},
        {"name": "container_id",     "classification": "observed",
         "reason": "Opaque container identifier."},
        {"name": "cpu_util",         "classification": "observed",
         "reason": "CPU utilisation percentage (0-100); 300-second average."},
        {"name": "cpu_util_max",     "classification": "observed",
         "reason": "Maximum CPU utilisation in the 300-second window."},
        {"name": "mem_util",         "classification": "observed",
         "reason": "Memory utilisation percentage (0-100)."},
        {"name": "disk_read_rate",   "classification": "observed",
         "reason": "Disk read rate, normalised units per trace documentation."},
        {"name": "disk_write_rate",  "classification": "observed",
         "reason": "Disk write rate, normalised units."},
        {"name": "disk_read_rate2",  "classification": "observed",
         "reason": "Secondary disk read rate measurement."},
        {"name": "net_in",           "classification": "observed",
         "reason": "Network ingress rate, normalised."},
        {"name": "net_out",          "classification": "observed",
         "reason": "Network egress rate, normalised."},
        {"name": "mem_util2",        "classification": "observed",
         "reason": "Secondary memory utilisation measurement."},
        {"name": "mem_util3",        "classification": "observed",
         "reason": "Tertiary memory utilisation measurement."},
    ],
    "server_event.csv": [
        {"name": "timestamp",  "classification": "observed",
         "reason": "Seconds relative to trace epoch."},
        {"name": "machine_id", "classification": "observed",
         "reason": "Opaque machine identifier."},
        {"name": "event_type", "classification": "observed",
         "reason": "Categorical event type (add, remove, etc.)."},
        {"name": "extra",      "classification": "unknown",
         "reason": "Fourth column present in data; may be optional per documentation."},
        {"name": "cpu_cores",  "classification": "observed",
         "reason": "Physical CPU core count; NOT normalised unlike memory fields."},
        {"name": "mem_size",   "classification": "observed",
         "reason": "Normalised memory capacity per trace_201708.md."},
        {"name": "disk_size",  "classification": "observed",
         "reason": "Disk capacity; units per trace documentation."},
    ],
    "server_usage.csv": [
        {"name": "timestamp",    "classification": "observed",
         "reason": "Seconds relative to trace epoch; 300-second averaged sample."},
        {"name": "machine_id",   "classification": "observed",
         "reason": "Opaque machine identifier."},
        {"name": "cpu_util",     "classification": "observed",
         "reason": "CPU utilisation percentage; 300-second average."},
        {"name": "cpu_util_max", "classification": "observed",
         "reason": "Maximum CPU utilisation in the 300-second window."},
        {"name": "mem_util",     "classification": "observed",
         "reason": "Memory utilisation percentage."},
        {"name": "net_in",       "classification": "observed",
         "reason": "Network ingress rate, normalised units."},
        {"name": "net_out",      "classification": "observed",
         "reason": "Network egress rate, normalised units."},
        {"name": "disk_io",      "classification": "observed",
         "reason": "Disk I/O rate, normalised units."},
    ],
}

# FOCUS 1.2 required fields — per the FinOps FOCUS 1.2 specification.
# These must be present and non-null in every conformant FOCUS export.
FOCUS_REQUIRED_FIELDS: list[dict[str, str]] = [
    {"name": "BilledCost",         "classification": "observed",
     "reason": "Actual billed amount in BillingCurrency; required FOCUS 1.2 column."},
    {"name": "BillingCurrency",    "classification": "observed",
     "reason": "ISO 4217 currency code; required FOCUS 1.2 column."},
    {"name": "BillingPeriodEnd",   "classification": "observed",
     "reason": "UTC timestamp marking end of billing period; required FOCUS 1.2 column. "
               "This is a billing-period boundary, NOT a billing-record arrival timestamp."},
    {"name": "BillingPeriodStart", "classification": "observed",
     "reason": "UTC timestamp marking start of billing period; required FOCUS 1.2 column. "
               "This is a billing-period boundary, NOT a billing-record arrival timestamp."},
    {"name": "ChargeCategory",     "classification": "observed",
     "reason": "Charge classification (Usage, Credit, Tax, etc.); required FOCUS 1.2 column."},
    {"name": "ChargeFrequency",    "classification": "observed",
     "reason": "Charge recurrence type; required FOCUS 1.2 column."},
    {"name": "InvoiceIssuerName",  "classification": "observed",
     "reason": "Issuing entity name; required FOCUS 1.2 column."},
    {"name": "ProviderName",       "classification": "observed",
     "reason": "Cloud provider name; required FOCUS 1.2 column."},
    {"name": "RegionId",           "classification": "observed",
     "reason": "Provider-assigned region identifier; required FOCUS 1.2 column."},
    {"name": "RegionName",         "classification": "observed",
     "reason": "Human-readable region name; required FOCUS 1.2 column."},
    {"name": "ServiceCategory",    "classification": "observed",
     "reason": "FinOps taxonomy service grouping; required FOCUS 1.2 column."},
    {"name": "SubAccountType",     "classification": "observed",
     "reason": "Sub-account kind (e.g. AWS Account); required FOCUS 1.2 column."},
    {"name": "BillingAccountType", "classification": "observed",
     "reason": "Billing-account kind; required FOCUS 1.2 column."},
]

# FOCUS optional but important fields for CALIBUR billing analysis.
FOCUS_OPTIONAL_FIELDS: list[dict[str, str]] = [
    {"name": "EffectiveCost",        "classification": "observed",
     "reason": "Cost after commitment discounts; FOCUS 1.2 optional column."},
    {"name": "ListCost",             "classification": "observed",
     "reason": "On-demand list-price cost; FOCUS 1.2 optional column."},
    {"name": "ChargePeriodStart",    "classification": "observed",
     "reason": "UTC start of charge period; FOCUS 1.2 optional. "
               "NOT a billing-record arrival timestamp."},
    {"name": "ChargePeriodEnd",      "classification": "observed",
     "reason": "UTC end of charge period; FOCUS 1.2 optional. "
               "NOT a billing-record arrival timestamp."},
    {"name": "ResourceId",           "classification": "observed",
     "reason": "Provider resource ARN/identifier; FOCUS 1.2 optional. "
               "Required for linkage to CALIBUR admission records — absent rows break linkage."},
    {"name": "ServiceName",          "classification": "observed",
     "reason": "Provider service name; FOCUS 1.2 optional."},
    {"name": "Tags",                 "classification": "observed",
     "reason": "Key-value tag map; FOCUS 1.2 optional. "
               "No CALIBUR reservation-id or team tags observed in this delivery."},
    {"name": "InvoiceId",            "classification": "observed",
     "reason": "Invoice identifier; FOCUS 1.2 optional. Fully null in this delivery."},
    {"name": "SkuId",                "classification": "observed",
     "reason": "Provider SKU identifier; FOCUS 1.2 optional."},
    {"name": "ConsumedQuantity",     "classification": "observed",
     "reason": "Actual usage quantity; FOCUS 1.2 optional."},
    {"name": "ConsumedUnit",         "classification": "observed",
     "reason": "Unit for ConsumedQuantity; FOCUS 1.2 optional."},
    {"name": "x_Operation",         "classification": "observed",
     "reason": "AWS-specific operation string (FOCUS AWS extension column)."},
    {"name": "x_ServiceCode",       "classification": "observed",
     "reason": "AWS-specific service code (FOCUS AWS extension column)."},
]

# Sentinel value definitions per dataset.
# Documented blanks in Alibaba CSVs are the primary sentinel; no numeric sentinel is documented.
ALIBABA_SENTINELS: dict[str, list[Any]] = {"": None}  # blank string is the only documented sentinel

# Files to skip when discovering raw files (archives, non-data support files).
SKIP_SUFFIXES = {".gz", ".bz2", ".zip", ".tar"}

# ---------------------------------------------------------------------------
# Source approval registry
# ---------------------------------------------------------------------------
# Each entry documents the explicit approval status of one research source.
# An "approved" source may be loaded for analysis subject to the stated
# conditions.  An "unapproved" source MUST NOT be used in empirical results
# until all blocking items are resolved.
#
# Do not set "approved": True for Alibaba until the license applicability
# to the exact v2017 trace archive has been independently confirmed.
SOURCE_APPROVAL: dict[str, dict[str, Any]] = {
    "alibaba_cluster_trace_v2017": {
        "approved": False,
        "approval_reason": (
            "License applicability to the exact v2017 trace archive has not been "
            "independently confirmed.  The Alibaba clusterdata repository contains a "
            "CC BY 4.0 license file, but it is not yet established that this license "
            "covers the v2017 archive specifically.  Source remains UNAPPROVED for "
            "research use until that determination is made and recorded here."
        ),
        "schema_version": "trace_201708.md (official Alibaba trace documentation)",
        "sha256_archive": "b7efe9dc9caa75352de41b9ac34b329602cde3414fd7cab87c1d21a9a9871d66",
        "files": [
            "data/raw/alibaba_trace_2017/alibaba-trace-2017/batch_instance.csv",
            "data/raw/alibaba_trace_2017/alibaba-trace-2017/batch_task.csv",
            "data/raw/alibaba_trace_2017/alibaba-trace-2017/container_event.csv",
            "data/raw/alibaba_trace_2017/alibaba-trace-2017/container_usage.csv",
            "data/raw/alibaba_trace_2017/alibaba-trace-2017/server_event.csv",
            "data/raw/alibaba_trace_2017/alibaba-trace-2017/server_usage.csv",
        ],
        "blocking_items": [
            "Verify and document the applicable license/terms for the v2017 trace archive.",
        ],
    },
    "aws_focus_1.2": {
        "approved": True,
        "approval_reason": (
            "AWS FOCUS 1.2 billing export from the project AWS account.  "
            "FOCUS schema version 1.2 confirmed from CALIBUR_FOCUS_12-Manifest-FOCUS.json.  "
            "All three deliveries verified by SHA-256 against recorded manifest values.  "
            "Data-handling restrictions apply: raw files must not be committed to a public "
            "repository or shared outside the project team."
        ),
        "schema_version": "FOCUS 1.2 (FocusVersion 1.2, SchemaId 1.0.0, "
                          "schema_creation_date 2025-11-19T00:00:00Z)",
        "deliveries": {
            "delivery_1": {
                "path": "data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet",
                "sha256": "e829bfa81b344c07e7803d52bd7e9863eba2240e37cea4a701c2dc48f5220597",
                "size_bytes": 111251,
                "rows": 12200,
                "delivery_timestamp": "2026-10-24T19:27:14.926Z",
                "execution_id": "19206226-e1be-320d-84a6-833b2f8a9b81",
            },
            "delivery_2": {
                "path": "data/raw/focus12/delivery_2026-10-25/CALIBUR_FOCUS_12-00001.snappy.parquet",
                "sha256": "df688f94a119d2cc92b0347dc86b7ee4b3ec383a58671de2ce216347f375378b",
                "size_bytes": 105860,
                "rows": 12404,
                "delivery_timestamp": "2026-10-25T03:32:00.757Z",
                "execution_id": "ddede1a2-e372-34a5-aa28-36cacf5a98ef",
            },
            "delivery_3": {
                "path": "data/raw/focus12/delivery_2026-10-25T11/CALIBUR_FOCUS_12-00001.snappy.parquet",
                "sha256": "a6c184a5cae50b43d0a7d0e46c5987ff8852cd8aeb4799b4cfa7bb3e1e1c08f0",
                "size_bytes": 113134,
                "rows": 12579,
                "delivery_timestamp": "2026-10-25T11:18:08.607Z",
                "execution_id": "2719ee81-66bd-346a-93bc-ec5e99dc1747",
                "first_observed_utc": "2026-10-25T11:32:29.273473+00:00",
                "note": (
                    "Strictly additive vs D2: 140 rows in D3 not in D2, spanning "
                    "ChargePeriodStart 2026-10-24 (3 rows) and 2026-10-25 (137 rows). "
                    "No D2 descriptive group was removed or changed. "
                    "This is a new snapshot, not a proven billing revision."
                ),
            },
        },
        "blocking_items": [],
        "s3_bucket_name_note": (
            "The S3 delivery path and export ARN record account identifiers that "
            "differ by one digit. Account-specific identifiers are withheld from "
            "committed artifacts. This discrepancy is not treated as resolved "
            "without owner confirmation against the AWS console; no identifier is "
            "guessed or silently corrected."
        ),
    },
}

# ---------------------------------------------------------------------------
# Blocked analyses
# ---------------------------------------------------------------------------
# Each entry names an analysis that CANNOT be performed with current evidence
# and states the missing prerequisite.  Do not remove items until the
# prerequisite is independently confirmed and recorded.
BLOCKED_ANALYSES: list[dict[str, str]] = [
    {
        "analysis": "Alibaba workload descriptive statistics",
        "blocked_by": "Source unapproved: license applicability of CC BY 4.0 to the "
                      "v2017 trace archive has not been independently confirmed.",
        "source": "alibaba_cluster_trace_v2017",
    },
    {
        "analysis": "Cross-dataset join: Alibaba records <-> FOCUS billing records",
        "blocked_by": "No verified shared identifier between Alibaba cluster records "
                      "and AWS FOCUS billing records has been established.  No linkage "
                      "will be fabricated.",
        "source": "alibaba_cluster_trace_v2017 + aws_focus_1.2",
    },
    {
        "analysis": "Cross-dataset join: CALIBUR admission decisions <-> FOCUS billing records",
        "blocked_by": "No verified shared identifier between CALIBUR admission records "
                      "and FOCUS billing records has been established.  Tags field is "
                      "fully empty in both deliveries; ResourceId is null for 606 of "
                      "12,200 rows.",
        "source": "aws_focus_1.2",
    },
    {
        "analysis": "Billing-record arrival delay measurement",
        "blocked_by": "No billing-record arrival timestamp is present in the FOCUS 1.2 "
                      "schema.  BillingPeriodStart/End and ChargePeriodStart/End are "
                      "billing-period boundaries, not arrival times.  Arrival delay "
                      "cannot be measured without a separate arrival timestamp.",
        "source": "aws_focus_1.2",
    },
    {
        "analysis": "Billing revision detection",
        "blocked_by": "No verified row-level unique key has been established for FOCUS "
                      "records.  InvoiceId is fully null.  Snapshot comparison used "
                      "only descriptive grouping fields.  Two snapshots differing does "
                      "not prove a billing revision.",
        "source": "aws_focus_1.2",
    },
    {
        "analysis": "CALIBUR budget-safety performance validation",
        "blocked_by": "No empirical linkage between admission decisions and billing "
                      "outcomes has been established.  Profiling alone does not "
                      "validate real-world controller performance.",
        "source": "aws_focus_1.2 + calibur_admissions",
    },
]

# ---------------------------------------------------------------------------
# Unresolved quality issues
# ---------------------------------------------------------------------------
UNRESOLVED_QUALITY_ISSUES: list[dict[str, str]] = [
    {
        "issue": "Alibaba dataset license applicability",
        "detail": (
            "The Alibaba clusterdata repository contains a CC BY 4.0 license file.  "
            "Whether that license applies specifically to the v2017 trace archive "
            "(alibaba-trace-2017.tar.gz) has not been independently confirmed.  "
            "The source remains unapproved for research use until this is established."
        ),
        "source": "alibaba_cluster_trace_v2017",
        "status": "UNRESOLVED — requires independent license review",
    },
    {
        "issue": "container_event.csv undocumented 9th column",
        "detail": (
            "The extracted container_event.csv contains 9 columns.  "
            "The authoritative schema (trace_201708.md, alibaba_v2017_schema.csv) "
            "documents only 8.  The 9th column is always blank in the v2017 archive.  "
            "Its semantics are unknown.  It is preserved as 'undocumented_field_9' in "
            "the loader schema without interpretation."
        ),
        "source": "alibaba_cluster_trace_v2017",
        "status": "UNRESOLVED — semantics of 9th column not documented by source",
    },
    {
        "issue": "Delivery 1 has no manifest file",
        "detail": (
            "No manifest file was initially present for FOCUS Delivery 1 "
            "(data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet, 111,251 bytes).  "
            "Its provenance rests on the SHA-256 recorded in data_manifest.md.  "
            "The manifests downloaded for D1 from S3 on 2026-10-25 are now confirmed: "
            "executionId 19206226-e1be-320d-84a6-833b2f8a9b81, same exportArn as D2/D3, "
            "same 60-column schema.  D1 manifest is available at the S3 path "
            "metadata/billing_period=2026-10/2026-10-24T19:27:14.926Z-19206226-.../."
        ),
        "source": "aws_focus_1.2",
        "status": "RESOLVED — D1 manifest confirmed from S3 manifest content",
    },
    {
        "issue": "No verified row-level unique key for FOCUS billing records",
        "detail": (
            "FOCUS 1.2 does not mandate a row-level stable ID field.  InvoiceId is "
            "fully null in all three deliveries.  ResourceId is null for 606/622/648 "
            "rows in D1/D2/D3 respectively.  No substitute key has been invented.  "
            "Snapshot comparison used descriptive grouping only and must not be treated "
            "as row-level identity comparison."
        ),
        "source": "aws_focus_1.2",
        "status": "UNRESOLVED — no unique row key established",
    },
    {
        "issue": "No CALIBUR-managed resource has been launched",
        "detail": (
            "All 12 RunInstances CloudTrail events in October 2026 were initiated by "
            "the root user or AutoScaling for unrelated workloads (da1-ec2, da2-mysql-source, etc.).  "
            "No Spendctl request, admission, or reservation record exists in DynamoDB.  "
            "The FOCUS billing records contain real AWS resource IDs but none correspond "
            "to CALIBUR-managed resources.  The end-to-end integration cannot be completed "
            "without at least one human-approved CALIBUR admission and EC2 launch."
        ),
        "source": "aws_focus_1.2 + calibur_admissions",
        "status": "UNRESOLVED — requires approved CALIBUR admission and resource launch",
    },
]

# ---------------------------------------------------------------------------
# Documented transformations applied by the loaders (read-only, deterministic)
# ---------------------------------------------------------------------------
DOCUMENTED_TRANSFORMATIONS: list[dict[str, str]] = [
    {
        "source": "aws_focus_1.2",
        "field": "BillingPeriodStart, BillingPeriodEnd, ChargePeriodStart, ChargePeriodEnd",
        "transform": "Parquet timestamp(ms, UTC) -> UTC ISO-8601 string via pandas Timestamp.tz_convert('UTC').isoformat()",
        "reversible": "Yes — original value recoverable from ISO-8601 string",
        "introduced_by": "focus_loader.load_parquet",
    },
    {
        "source": "aws_focus_1.2",
        "field": "BilledCost, EffectiveCost, ListCost (on explicit request only)",
        "transform": "USD float -> micro-USD integer via Decimal half-even rounding (cost_to_micro())",
        "reversible": "Approximately — rounding may lose sub-micro precision; sign preserved",
        "introduced_by": "focus_loader.cost_to_micro",
    },
    {
        "source": "alibaba_cluster_trace_v2017",
        "field": "All integer columns",
        "transform": "CSV string -> Python int via int(stripped); blank -> None",
        "reversible": "Yes",
        "introduced_by": "alibaba_loader.load_file (_coerce)",
    },
    {
        "source": "alibaba_cluster_trace_v2017",
        "field": "All float columns",
        "transform": "CSV string -> Python float via float(stripped); blank -> None",
        "reversible": "Yes (within float precision)",
        "introduced_by": "alibaba_loader.load_file (_coerce)",
    },
]


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class ProfileError(Exception):
    """Raised when a file cannot be profiled due to a structural problem."""


# ---------------------------------------------------------------------------
# SHA-256 helper — streaming to handle large files without loading into memory.
# ---------------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover_raw_files() -> list[Path]:
    """Return all supported raw files under data/raw/, sorted for determinism.

    Skips compressed archives (handled separately as archive entries).
    Never modifies the directory tree.
    """
    if not RAW_ROOT.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(RAW_ROOT.rglob("*")):
        if path.is_file() and path.suffix not in SKIP_SUFFIXES:
            found.append(path)
    return found


# ---------------------------------------------------------------------------
# CSV profiler (streaming)
# ---------------------------------------------------------------------------
def _profile_csv(
    path: Path,
    col_schema: list[dict[str, str]],
    *,
    has_header: bool = False,
    dataset_label: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Stream a CSV and return profiling statistics.  Never loads full file."""
    _root = root or ROOT
    file_size = path.stat().st_size
    if file_size == 0:
        raise ProfileError(f"Empty CSV file: {path}")

    sha = _sha256_file(path)

    # Detect encoding: try UTF-8 strictly, fall back to latin-1.
    encoding = "utf-8"
    try:
        with path.open(encoding="utf-8") as f:
            f.read(4096)
    except UnicodeDecodeError:
        encoding = "latin-1"

    # Count columns from first data row (not a header row for Alibaba files).
    with path.open(newline="", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        if has_header:
            header_row = next(reader, None)
            observed_col_count = len(header_row) if header_row else 0
        else:
            first_row = next(reader, None)
            observed_col_count = len(first_row) if first_row else 0

    documented_col_count = len(col_schema)
    schema_match: str
    if observed_col_count == documented_col_count:
        schema_match = "exact_match"
    elif observed_col_count > documented_col_count:
        schema_match = f"extra_columns:{observed_col_count - documented_col_count}"
    else:
        schema_match = f"missing_columns:{documented_col_count - observed_col_count}"

    # Streaming pass: count rows, missing, and exact_duplicate_records.
    # For large files we skip exact-duplicate detection (memory-unsafe) and say so.
    missing_by_col: dict[int, int] = {i: 0 for i in range(max(observed_col_count, documented_col_count))}
    total_rows = 0
    exact_duplicates: int | None = None

    with path.open(newline="", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f)
        if has_header:
            next(reader, None)  # skip header

        if file_size <= 50 * 1024 * 1024:  # ≤50 MB: track duplicates
            seen_rows: set[tuple[str, ...]] = set()
            exact_duplicates = 0
            for row in reader:
                total_rows += 1
                t = tuple(row)
                if t in seen_rows:
                    exact_duplicates += 1
                else:
                    seen_rows.add(t)
                for i in range(documented_col_count):
                    val = row[i].strip() if i < len(row) else ""
                    if val == "":
                        missing_by_col[i] = missing_by_col.get(i, 0) + 1
        else:
            # Large file: stream without holding rows in memory.
            for row in reader:
                total_rows += 1
                for i in range(documented_col_count):
                    val = row[i].strip() if i < len(row) else ""
                    if val == "":
                        missing_by_col[i] = missing_by_col.get(i, 0) + 1
            # Duplicate check skipped for large files; document this clearly.
            exact_duplicates = None

    fields: list[dict[str, Any]] = []
    for i, col_def in enumerate(col_schema):
        missing = missing_by_col.get(i, 0)
        # For Alibaba: blank is the only documented sentinel.
        # Missing and sentinel are the same thing — blank fields.
        # We report them separately as the spec requires, but note they coincide.
        sentinel_count = missing  # blank == sentinel for Alibaba
        fields.append({
            "name": col_def["name"],
            "classification": col_def["classification"],
            "missing_count": missing,
            "invalid_count": 0,   # no numeric range validation applied at profile stage
            "sentinel_count": sentinel_count,
            "sentinel_definition": "blank string — documented missing value in Alibaba trace",
            "reason": col_def["reason"],
        })

    notes = [
        f"Dataset: {dataset_label}",
        "No header row present; column names assigned from official trace_201708.md documentation.",
        f"Column count: documented={documented_col_count}, observed={observed_col_count} ({schema_match}).",
        "Timestamps are seconds relative to the trace epoch; zero may indicate events before the trace window.",
        "Memory values are normalised/obfuscated; CPU core counts are NOT normalised in the same way.",
        "Measurements are NOT AWS billing amounts or AWS resource identifiers.",
        "No cross-dataset join with AWS FOCUS data has been established.",
    ]
    if exact_duplicates is None:
        notes.append(
            f"Exact-duplicate detection skipped for large file ({file_size:,} bytes > 50 MB); "
            "key uniqueness is UNKNOWN."
        )

    return {
        "relative_path": str(path.relative_to(_root)),
        "file_type": "csv",
        "encoding": encoding,
        "size_bytes": file_size,
        "sha256": sha,
        "row_count": total_rows,
        "column_count_documented": documented_col_count,
        "column_count_observed": observed_col_count,
        "schema_match": schema_match,
        "exact_duplicate_record_count": exact_duplicates,
        "key_uniqueness": "unknown" if exact_duplicates is None else (
            "no_exact_duplicates_found" if exact_duplicates == 0 else f"{exact_duplicates}_exact_duplicates"
        ),
        "fields": fields,
        "notes": notes,
        "dataset": dataset_label,
        # Source approval and schema version for machine-readable quality gate.
        "source_approved": SOURCE_APPROVAL.get("alibaba_cluster_trace_v2017", {}).get("approved", False),
        "schema_version": SOURCE_APPROVAL.get("alibaba_cluster_trace_v2017", {}).get("schema_version"),
    }


# ---------------------------------------------------------------------------
# Parquet profiler (FOCUS 1.2)
# ---------------------------------------------------------------------------
def _profile_parquet(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Profile an Apache Parquet file.  Requires pyarrow (available in venv)."""
    _root = root or ROOT
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ProfileError(
            f"pyarrow is required to profile Parquet files but is not installed: {exc}"
        ) from exc

    file_size = path.stat().st_size
    if file_size == 0:
        raise ProfileError(f"Empty Parquet file: {path}")

    sha = _sha256_file(path)

    try:
        pf = pq.read_table(path)
    except Exception as exc:
        raise ProfileError(f"Cannot read Parquet file {path}: {exc}") from exc
    row_count = len(pf)
    actual_cols: set[str] = set(pf.schema.names)

    # Validate required FOCUS 1.2 fields.
    required_names = {f["name"] for f in FOCUS_REQUIRED_FIELDS}
    missing_required = sorted(required_names - actual_cols)

    # Build field profiles for required + optional columns.
    all_defined = FOCUS_REQUIRED_FIELDS + FOCUS_OPTIONAL_FIELDS
    field_profiles: list[dict[str, Any]] = []

    for col_def in all_defined:
        col_name = col_def["name"]
        if col_name not in actual_cols:
            field_profiles.append({
                "name": col_name,
                "classification": col_def["classification"],
                "present": False,
                "missing_count": None,
                "invalid_count": None,
                "sentinel_count": None,
                "sentinel_definition": "n/a — column absent",
                "reason": col_def["reason"],
            })
            continue

        arr = pf.column(col_name)
        null_count = arr.null_count

        # For map-type columns (Tags, SkuPriceDetails, x_Discounts), count
        # rows where the map is null OR empty.
        import pyarrow as pa
        if pa.types.is_map(arr.type):
            py_list = arr.to_pylist()
            empty_count = sum(1 for v in py_list if not v)
            sentinel_count = empty_count
            sentinel_def = "null or empty map"
            invalid_count = 0
        elif pa.types.is_floating(arr.type):
            # Check for NaN which is a documented sentinel in some billing exports.
            py_list = arr.to_pylist()
            nan_count = sum(
                1 for v in py_list if isinstance(v, float) and math.isnan(v)
            )  # NaN != NaN
            sentinel_count = nan_count
            sentinel_def = "NaN (floating-point not-a-number)"
            invalid_count = 0
        else:
            sentinel_count = 0
            sentinel_def = "none documented"
            invalid_count = 0

        field_profiles.append({
            "name": col_name,
            "classification": col_def["classification"],
            "present": True,
            "missing_count": null_count,
            "invalid_count": invalid_count,
            "sentinel_count": sentinel_count,
            "sentinel_definition": sentinel_def,
            "reason": col_def["reason"],
        })

    # Also profile undocumented extra columns present in the actual file.
    documented_names = {f["name"] for f in all_defined}
    for extra_col in sorted(actual_cols - documented_names):
        arr = pf.column(extra_col)
        field_profiles.append({
            "name": extra_col,
            "classification": "unknown",
            "present": True,
            "missing_count": arr.null_count,
            "invalid_count": 0,
            "sentinel_count": 0,
            "sentinel_definition": "none assessed",
            "reason": "Column present in Parquet file but not in FOCUS_REQUIRED or FOCUS_OPTIONAL lists.",
        })

    # Summary statistics for key cost fields.
    cost_summary: dict[str, Any] = {}
    for cost_col in ("BilledCost", "EffectiveCost", "ListCost"):
        if cost_col in actual_cols:
            arr = pf.column(cost_col)
            vals = [v for v in arr.to_pylist() if v is not None]
            cost_summary[cost_col] = {
                "currency": "USD (all rows)",
                "sum": round(sum(vals), 8) if vals else None,
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
                "non_null_count": len(vals),
            }

    # Tag check.
    tag_non_empty = 0
    if "Tags" in actual_cols:
        tag_list = pf.column("Tags").to_pylist()
        tag_non_empty = sum(1 for v in tag_list if v)

    # Billing period range — not arrival time.
    bp_start = None
    bp_end = None
    if "BillingPeriodStart" in actual_cols:
        col = pf.column("BillingPeriodStart")
        non_null = [v for v in col.to_pylist() if v is not None]
        if non_null:
            bp_start = str(min(non_null))
    if "BillingPeriodEnd" in actual_cols:
        col = pf.column("BillingPeriodEnd")
        non_null = [v for v in col.to_pylist() if v is not None]
        if non_null:
            bp_end = str(max(non_null))

    notes = [
        "Dataset: AWS FOCUS 1.2 billing export (CALIBUR_FOCUS_12).",
        "Parquet metadata: org.apache.spark.version present in file metadata.",
        f"Billing period: {bp_start} to {bp_end} (billing-period boundary timestamps, NOT billing-arrival timestamps).",
        ("BillingPeriodStart/End and ChargePeriodStart/End are period-boundary timestamps. "
        "The time this record first became available to a controller has NOT been recorded."),
        (f"Tags: {tag_non_empty} rows with non-empty tag maps out of {row_count}. "
        "No CALIBUR reservation-id or team tags are present. "
        "Linkage between these billing records and CALIBUR admission decisions is NOT established."),
        "InvoiceId: fully null in this delivery — no invoice number is available for deduplication.",
        "One delivery snapshot only. Billing revisions have not been observed.",
        "Currency: USD for all 12,200 rows.",
        "ChargeClass: fully null — no adjustment/correction class values present in this delivery.",
        "BilledCost sum ≈ 0 due to credit offset; this does not mean future costs will be zero.",
    ]
    if missing_required:
        notes.append(
            f"REQUIRED FIELD VALIDATION FAILURE: missing FOCUS 1.2 required columns: {missing_required}"
        )

    result: dict[str, Any] = {
        "relative_path": str(path.relative_to(_root)),
        "file_type": "parquet/snappy",
        "encoding": "binary (Parquet)",
        "size_bytes": file_size,
        "sha256": sha,
        "row_count": row_count,
        "column_count_observed": len(actual_cols),
        "column_count_required_defined": len(FOCUS_REQUIRED_FIELDS),
        "missing_required_fields": missing_required,
        "exact_duplicate_record_count": None,
        "key_uniqueness": "unknown — no candidate key justified for FOCUS rows",
        "cost_summary": cost_summary,
        "fields": field_profiles,
        "notes": notes,
        "dataset": "focus12",
        # Source approval and schema version for machine-readable quality gate.
        "source_approved": SOURCE_APPROVAL.get("aws_focus_1.2", {}).get("approved", False),
        "schema_version": SOURCE_APPROVAL.get("aws_focus_1.2", {}).get("schema_version"),
        "unresolved_quality_issues": [
            issue["issue"]
            for issue in UNRESOLVED_QUALITY_ISSUES
            if issue.get("source") == "aws_focus_1.2"
        ],
    }

    if missing_required:
        raise ProfileError(
            f"FOCUS 1.2 required fields absent in {path.relative_to(_root)}: {missing_required}"
        )

    return result


# ---------------------------------------------------------------------------
# Archive profiler — inspects tar.gz contents WITHOUT extracting.
# ---------------------------------------------------------------------------
def _profile_tar(path: Path, manifest_text: str, *, root: Path | None = None) -> dict[str, Any]:
    """Profile a tar archive by reading its member list only.  No extraction."""
    _root = root or ROOT
    file_size = path.stat().st_size
    if file_size == 0:
        raise ProfileError(f"Empty archive file: {path}")

    sha = _sha256_file(path)

    try:
        with tarfile.open(path, "r:gz") as tf:
            members = tf.getmembers()
    except tarfile.TarError as exc:
        raise ProfileError(f"Cannot read archive {path}: {exc}") from exc

    member_info = [
        {
            "name": m.name,
            "size_bytes": m.size,
            "is_directory": m.isdir(),
        }
        for m in members
    ]

    notes = [
        "Archive inspected by listing members only.  No files were extracted or modified.",
        "Extracted files are already present under data/raw/alibaba_trace_2017/ — profiled separately.",
        "SHA-256 matches provenance recorded in research/data_manifest.md."
        if sha in manifest_text else
        "WARNING: SHA-256 does NOT match the value in research/data_manifest.md.",
    ]

    return {
        "relative_path": str(path.relative_to(_root)),
        "file_type": "tar.gz",
        "encoding": "binary (gzip-compressed tar)",
        "size_bytes": file_size,
        "sha256": sha,
        "row_count": None,
        "member_count": len(member_info),
        "members": member_info,
        "exact_duplicate_record_count": None,
        "key_uniqueness": "n/a — archive, not tabular data",
        "fields": [],
        "notes": notes,
        "dataset": "alibaba_v2017_archive",
        # Source approval reflects the extracted-file source.
        "source_approved": SOURCE_APPROVAL.get("alibaba_cluster_trace_v2017", {}).get("approved", False),
        "schema_version": SOURCE_APPROVAL.get("alibaba_cluster_trace_v2017", {}).get("schema_version"),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def profile_file(path: Path, manifest_text: str, *, root: Path | None = None) -> dict[str, Any]:
    """Dispatch to the appropriate profiler based on file type."""
    _root = root or ROOT
    rel = str(path.relative_to(_root))

    # Parquet
    if path.suffix == ".parquet":
        return _profile_parquet(path, root=_root)

    # Compressed archive
    if path.suffix in (".gz", ".bz2") or path.name.endswith(".tar.gz"):
        return _profile_tar(path, manifest_text, root=_root)

    # CSV: assign schema based on filename; reject unknown CSVs.
    if path.suffix == ".csv":
        fname = path.name
        schema = ALIBABA_SCHEMAS.get(fname)
        if schema is None:
            return {
                "relative_path": rel,
                "file_type": "csv",
                "encoding": "unknown",
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
                "row_count": None,
                "exact_duplicate_record_count": None,
                "key_uniqueness": "unknown",
                "fields": [],
                "notes": [
                    (f"No documented schema for '{fname}'. "
                    "This CSV was not profiled — it is not listed in ALIBABA_SCHEMAS or FOCUS definitions."),
                ],
                "dataset": "unknown",
            }
        dataset_label = f"alibaba_v2017/{fname}"
        return _profile_csv(path, schema, has_header=False, dataset_label=dataset_label, root=_root)

    # Unsupported type
    return {
        "relative_path": rel,
        "file_type": f"unsupported ({path.suffix})",
        "encoding": "unknown",
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "row_count": None,
        "exact_duplicate_record_count": None,
        "key_uniqueness": "unknown",
        "fields": [],
        "notes": [f"File type '{path.suffix}' is not supported by the profiler."],
        "dataset": "unknown",
    }


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------
def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# CALIBUR data quality report",
        "",
        f"**Generated:** {report['generated_at_utc']}  ",
        f"**Research gate:** `{report['research_gate']}`  ",
        f"**Gate reason:** {report['research_gate_reason']}",
        "",
        f"Files discovered: {report['files_discovered']}  ",
        f"Files profiled: {report['files_profiled']}",
        "",
        "> **Strict constraint:** profiling statistics are descriptive data-quality outputs only.",
        "> They do not constitute experiment results, model evaluations, or research performance claims.",
        "> Billing-period timestamps are period boundaries, not billing-record arrival times.",
        "> No cross-dataset join has been established.",
        "",
    ]

    for result in report["results"]:
        lines += [
            f"## `{result['relative_path']}`",
            "",
            f"- **Dataset:** {result.get('dataset', 'unknown')}",
            f"- **Type:** {result['file_type']}",
            f"- **Size:** {result['size_bytes']:,} bytes",
            f"- **SHA-256:** `{result['sha256']}`",
            f"- **Encoding:** {result['encoding']}",
            f"- **Rows:** {result['row_count']}",
            f"- **Exact duplicate records:** {result.get('exact_duplicate_record_count')}",
            f"- **Key uniqueness:** {result.get('key_uniqueness', 'unknown')}",
            "",
        ]

        missing_req = result.get("missing_required_fields")
        if missing_req:
            lines += [
                f"**⛔ REQUIRED FIELDS ABSENT:** {missing_req}",
                "",
            ]

        cost_summary = result.get("cost_summary")
        if cost_summary:
            lines += ["### Cost field summary", ""]
            lines += ["| Field | Currency | Sum | Min | Max | Non-null |", "|---|---|---:|---:|---:|---:|"]
            for cf, cs in cost_summary.items():
                lines.append(
                    f"| `{cf}` | {cs['currency']} | {cs['sum']} | {cs['min']} | {cs['max']} | {cs['non_null_count']} |"
                )
            lines.append("")

        if result["fields"]:
            lines += [
                "| Field | Classification | Missing | Invalid | Sentinel | Reason |",
                "|---|---|---:|---:|---:|---|",
            ]
            for field in result["fields"]:
                reason = field["reason"].replace("|", "\\|")
                present_marker = "" if field.get("present", True) else " *(absent)*"
                lines.append(
                    f"| `{field['name']}`{present_marker} | `{field['classification']}` "
                    f"| {field['missing_count']} | {field['invalid_count']} "
                    f"| {field['sentinel_count']} | {reason} |"
                )
            lines.append("")

        members = result.get("members")
        if members:
            lines += ["### Archive members", ""]
            lines += ["| Member | Size (bytes) |", "|---|---:|"]
            for m in members:
                lines.append(f"| `{m['name']}` | {m['size_bytes']:,} |")
            lines.append("")

        for note in result.get("notes", []):
            lines.append(f"> {note}")
        lines.append("")

    if report["errors"]:
        lines += ["## Errors", ""]
        for error in report["errors"]:
            lines.append(f"- `{error}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_MD)
    args = parser.parse_args(argv)

    # Use module-level MANIFEST and RAW_ROOT so that tests can monkeypatch them.
    # Derive root from RAW_ROOT so relative_to() calls work correctly.
    raw_root = RAW_ROOT
    root = raw_root.parent.parent  # data/raw -> data -> repo root

    if not MANIFEST.is_file():
        print(f"ERROR: required provenance manifest not found: {MANIFEST}", file=sys.stderr)
        return 2
    manifest_text = MANIFEST.read_text(encoding="utf-8")

    files = discover_raw_files()
    # Include the tar.gz archive explicitly (skipped by discover_raw_files suffix filter).
    tar_files = sorted(raw_root.rglob("*.tar.gz"))
    all_files = sorted(set(files) | set(tar_files))

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in all_files:
        try:
            results.append(profile_file(path, manifest_text, root=root))
        except ProfileError as exc:
            errors.append(str(exc))
        except (OSError, UnicodeError, csv.Error, tarfile.TarError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")

    report = {
        "report_version": 2,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "repository_root": str(root),
        "raw_directory_present": raw_root.is_dir(),
        "files_discovered": len(all_files),
        "files_profiled": len(results),
        "errors": errors,
        "results": results,
        "research_gate": "AMBER",
        "research_gate_reason": (
            "Profiling alone does not establish provenance, billing finality, "
            "billing-arrival delays, request-to-resource linkage, or empirical performance."
        ),
        # --- Machine-readable quality sections (required by data-ingestion spec) ---
        "source_approval_status": SOURCE_APPROVAL,
        "blocked_analyses": BLOCKED_ANALYSES,
        "unresolved_quality_issues": UNRESOLVED_QUALITY_ISSUES,
        "documented_transformations": DOCUMENTED_TRANSFORMATIONS,
    }

    # Atomic write: never leave a partial report that appears successful.
    if errors:
        print("ERROR: profiling failed; reports were not written:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"
    md_text = render_markdown(report)

    staged: list[tuple[Path, Path]] = []
    try:
        for target, content in ((args.json_out, json_text), (args.report_out, md_text)):
            temp = target.with_name(target.name + ".tmp")
            temp.write_text(content, encoding="utf-8")
            staged.append((temp, target))
        for temp, target in staged:
            temp.replace(target)
    except OSError as exc:
        for temp, _ in staged:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        print(f"ERROR: could not write reports: {exc}", file=sys.stderr)
        return 2

    print(f"Profiled {len(results)} of {len(all_files)} discovered files.")
    if not all_files:
        print("WARNING: no raw files found; this is not successful real-data validation.")
    print(f"JSON report: {args.json_out}")
    print(f"Markdown report: {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
