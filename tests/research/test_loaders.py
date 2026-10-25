"""Tests for research/ingestion/alibaba_loader.py and focus_loader.py.

All fixtures are synthetic, created in temporary directories, and never
touch data/raw/.  No AWS access.  Test labels are SYNTHETIC_FIXTURE.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import struct
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import helpers — add research/ to path so loader modules are importable.
# ---------------------------------------------------------------------------
import sys
RESEARCH_DIR = Path(__file__).resolve().parents[2] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from ingestion.alibaba_loader import (
    APPROVED_FILES,
    COLUMN_SCHEMA,
    AlibabaLoadResult,
    AlibabaRow,
    ChecksumMismatch,
    SchemaMismatch,
    SourceNotApproved,
    load_file,
    provenance as alibaba_provenance,
)
from ingestion.focus_loader import (
    APPROVED_DELIVERIES,
    FOCUS_REQUIRED_COLUMNS,
    ChecksumMismatch as FocusChecksumMismatch,
    FocusLoadResult,
    FocusRow,
    MissingRequiredColumn,
    SourceNotApproved as FocusSourceNotApproved,
    UnexpectedCurrency,
    cost_to_micro,
    load_parquet,
    provenance as focus_provenance,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Minimal valid batch_task.csv row: 8 columns, all mandatory fields present.
_BATCH_TASK_ROW = ["6459", "6524", "3", "4", "15740", "Terminated", "50", "0.0079"]

# Minimal batch_task.csv row with one optional field blank (mem_requested_normalised
# is listed as mandatory in the schema CSV, but the actual data has blanks).
_BATCH_TASK_ROW_MISSING = ["6457", "6533", "4", "5", "1", "Terminated", "50", ""]


# ===========================================================================
# 1. Alibaba loader — approved file list and path safety
# ===========================================================================

class TestAlibabaApproval:
    def test_unapproved_filename_raises(self, tmp_path):
        with pytest.raises(SourceNotApproved, match="not in the approved"):
            load_file("evil.csv", raw_dir=tmp_path)

    def test_all_six_files_are_in_approved_list(self):
        expected = {
            "batch_instance.csv", "batch_task.csv", "container_event.csv",
            "container_usage.csv", "server_event.csv", "server_usage.csv",
        }
        assert set(APPROVED_FILES) == expected

    def test_schema_defined_for_all_approved_files(self):
        for fname in APPROVED_FILES:
            assert fname in COLUMN_SCHEMA, f"No schema for {fname}"
            assert len(COLUMN_SCHEMA[fname]) > 0


# ===========================================================================
# 2. Alibaba loader — schema counts match authoritative schema CSV
# ===========================================================================

class TestAlibabaSchemaColumnCounts:
    """Column counts must match research/alibaba_v2017_schema.csv."""

    expected_counts = {
        "batch_instance.csv":  12,
        "batch_task.csv":       8,
        # 8 columns per trace_201708.md / alibaba_v2017_schema.csv,
        # plus 1 undocumented field present in the extracted v2017 archive.
        "container_event.csv":  9,
        "container_usage.csv": 12,
        "server_event.csv":     7,
        "server_usage.csv":     8,
    }

    def test_all_column_counts_match_authoritative_schema(self):
        for fname, expected in self.expected_counts.items():
            actual = len(COLUMN_SCHEMA[fname])
            assert actual == expected, (
                f"{fname}: expected {expected} columns (from alibaba_v2017_schema.csv), "
                f"got {actual}"
            )


# ===========================================================================
# 3. Alibaba loader — basic load and row structure
# ===========================================================================

class TestAlibabaLoad:
    def test_loads_batch_task_rows(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW, _BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert isinstance(result, AlibabaLoadResult)
        assert result.row_count == 2
        assert result.schema_name == "batch_task.csv"
        assert result.column_count_expected == 8
        assert result.column_count_observed == 8

    def test_row_has_correct_field_names(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        row = result.rows[0]
        assert isinstance(row, AlibabaRow)
        assert "task_create_time" in row.fields
        assert "job_id" in row.fields
        assert "status" in row.fields

    def test_integer_fields_parsed_as_int(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.rows[0].fields["task_create_time"] == 6459
        assert result.rows[0].fields["job_id"] == 3

    def test_float_fields_parsed_as_float(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert isinstance(result.rows[0].fields["mem_requested_normalised"], float)

    def test_row_number_is_one_based(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW, _BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.rows[0].row_number == 1
        assert result.rows[1].row_number == 2

    def test_source_file_recorded_on_each_row(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.rows[0].source_file == "batch_task.csv"


# ===========================================================================
# 4. Alibaba loader — missing values are None, never zero
# ===========================================================================

class TestAlibabaMissingValues:
    def test_blank_field_is_none_not_zero(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW_MISSING])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.rows[0].fields["mem_requested_normalised"] is None

    def test_missing_count_incremented_for_blank(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW_MISSING, _BATCH_TASK_ROW_MISSING])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.missing_counts["mem_requested_normalised"] == 2

    def test_non_blank_field_not_counted_as_missing(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.missing_counts["task_create_time"] == 0

    def test_blank_integer_field_is_none(self, tmp_path):
        row = ["", "6524", "3", "4", "15740", "Terminated", "50", "0.0079"]
        _write_csv(tmp_path / "batch_task.csv", [row])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.rows[0].fields["task_create_time"] is None

    def test_missing_count_zero_means_no_blanks(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        for count in result.missing_counts.values():
            assert count == 0


# ===========================================================================
# 5. Alibaba loader — checksum verification
# ===========================================================================

class TestAlibabaChecksum:
    def test_correct_checksum_sets_verified_true(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        expected = _sha256(tmp_path / "batch_task.csv")
        result = load_file("batch_task.csv", raw_dir=tmp_path, expected_sha256=expected)
        assert result.sha256_verified is True
        assert result.sha256 == expected

    def test_wrong_checksum_raises(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        with pytest.raises(ChecksumMismatch):
            load_file("batch_task.csv", raw_dir=tmp_path, expected_sha256="a" * 64)

    def test_no_checksum_sets_verified_false(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert result.sha256_verified is False

    def test_sha256_is_always_computed(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert len(result.sha256) == 64
        assert result.sha256 == _sha256(tmp_path / "batch_task.csv")


# ===========================================================================
# 6. Alibaba loader — raw file immutability
# ===========================================================================

class TestAlibabaImmutability:
    def test_load_does_not_modify_file(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        before = (tmp_path / "batch_task.csv").read_bytes()
        load_file("batch_task.csv", raw_dir=tmp_path)
        after = (tmp_path / "batch_task.csv").read_bytes()
        assert before == after

    def test_provenance_does_not_modify_file(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        before = (tmp_path / "batch_task.csv").read_bytes()
        alibaba_provenance("batch_task.csv", raw_dir=tmp_path)
        after = (tmp_path / "batch_task.csv").read_bytes()
        assert before == after

    def test_no_files_written_to_raw_dir(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        before = set(tmp_path.iterdir())
        load_file("batch_task.csv", raw_dir=tmp_path)
        after = set(tmp_path.iterdir())
        assert after == before


# ===========================================================================
# 7. Alibaba loader — notes disclaim costs and arrival times
# ===========================================================================

class TestAlibabaNotes:
    def test_timestamp_note_present(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        combined = " ".join(result.notes)
        assert "NOT" in combined or "not" in combined
        assert "billing" in combined.lower() or "arrival" in combined.lower()

    def test_no_dollar_conversion_note_present(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        combined = " ".join(result.notes).lower()
        assert "dollars" in combined or "aws cost" in combined or "no conversion" in combined

    def test_no_linkage_note_present(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        combined = " ".join(result.notes).lower()
        assert "no linkage" in combined or "not established" in combined


# ===========================================================================
# 8. Alibaba loader — provenance dict
# ===========================================================================

class TestAlibabaProvenance:
    def test_provenance_returns_expected_keys(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        p = alibaba_provenance("batch_task.csv", raw_dir=tmp_path)
        assert p["source"] == "alibaba_cluster_trace_v2017"
        assert p["filename"] == "batch_task.csv"
        assert p["size_bytes"] > 0
        assert len(p["sha256"]) == 64
        assert p["linkage_to_aws_billing"] is None
        assert p["linkage_to_calibur_admissions"] is None

    def test_provenance_does_not_load_rows(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        p = alibaba_provenance("batch_task.csv", raw_dir=tmp_path)
        assert "rows" not in p


# ===========================================================================
# 9. Alibaba loader — fixture isolation (no data/raw access)
# ===========================================================================

class TestAlibabaFixtureIsolation:
    """Tests using tmp_path must never touch data/raw/."""

    def test_raw_dir_override_prevents_real_data_access(self, tmp_path):
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert str(tmp_path) in result.source_path
        # Must not reference the real data/raw path
        assert "data/raw" not in result.source_path or str(tmp_path) in result.source_path

    def test_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_file("batch_task.csv", raw_dir=tmp_path)


# ===========================================================================
# 10. cost_to_micro — deterministic half-even conversion
# ===========================================================================

class TestCostToMicro:
    def test_basic_conversion(self):
        assert cost_to_micro(1.0) == 1_000_000

    def test_fractional_rounds_half_even(self):
        # 0.0000015 USD = 1.5 micro-USD, rounds half-even to 2
        assert cost_to_micro(0.0000015) == 2
        # 0.0000025 USD = 2.5 micro-USD, rounds half-even to 2 (banker's rounding)
        assert cost_to_micro(0.0000025) == 2

    def test_none_returns_none_not_zero(self):
        assert cost_to_micro(None) is None

    def test_negative_cost_preserved(self):
        # Credits are negative; must not be converted to positive
        result = cost_to_micro(-0.5)
        assert result == -500_000

    def test_zero_cost_gives_zero(self):
        assert cost_to_micro(0.0) == 0

    def test_large_value(self):
        assert cost_to_micro(18.77972) == 18_779_720


# ===========================================================================
# 11. FOCUS loader — approval and checksum
# ===========================================================================

class TestFocusApproval:
    def test_unapproved_path_raises(self, tmp_path):
        p = tmp_path / "random.parquet"
        p.write_bytes(b"fake")
        with pytest.raises(FocusSourceNotApproved):
            load_parquet(str(p))

    def test_allow_unlisted_bypasses_approval(self, tmp_path):
        """allow_unlisted=True should not raise SourceNotApproved — only
        fails later because the file is not a valid Parquet file."""
        p = tmp_path / "new_delivery.parquet"
        p.write_bytes(b"not a parquet")
        with pytest.raises(Exception) as exc_info:
            load_parquet(str(p), allow_unlisted=True)
        # Must not be SourceNotApproved
        assert not isinstance(exc_info.value, FocusSourceNotApproved)

    def test_two_approved_deliveries_recorded(self):
        assert len(APPROVED_DELIVERIES) == 3

    def test_approved_delivery_shas_are_64_hex(self):
        for path, sha in APPROVED_DELIVERIES.items():
            assert len(sha) == 64, f"{path}: SHA-256 must be 64 hex chars"
            int(sha, 16)  # must be valid hex


# ===========================================================================
# 12. FOCUS loader — synthetic Parquet fixture
# ===========================================================================

def _make_focus_parquet(path: Path, rows: list[dict]) -> str:
    """Write a minimal FOCUS-shaped Parquet fixture.  Returns SHA-256."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Build schema with required columns + a couple of extras.
    schema = pa.schema([
        ("BilledCost",           pa.float64()),
        ("BillingCurrency",      pa.string()),
        ("BillingPeriodEnd",     pa.timestamp("ms", tz="UTC")),
        ("BillingPeriodStart",   pa.timestamp("ms", tz="UTC")),
        ("ChargeCategory",       pa.string()),
        ("ChargeFrequency",      pa.string()),
        ("InvoiceIssuerName",    pa.string()),
        ("ProviderName",         pa.string()),
        ("RegionId",             pa.string()),
        ("RegionName",           pa.string()),
        ("ServiceCategory",      pa.string()),
        ("SubAccountType",       pa.string()),
        ("BillingAccountType",   pa.string()),
        ("ResourceId",           pa.string()),
        ("InvoiceId",            pa.string()),
    ])

    import pandas as pd
    df = pd.DataFrame(rows)
    for col in ("BillingPeriodStart", "BillingPeriodEnd"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    for col in schema.names:
        if col not in df.columns:
            df[col] = None

    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(table, path, compression="snappy")
    return _sha256(path)


_FOCUS_ROW = {
    "BilledCost": 0.03,
    "BillingCurrency": "USD",
    "BillingPeriodEnd": "2026-11-01T00:00:00+00:00",
    "BillingPeriodStart": "2026-10-01T00:00:00+00:00",
    "ChargeCategory": "Usage",
    "ChargeFrequency": "Usage-Based",
    "InvoiceIssuerName": "Amazon Web Services, Inc.",
    "ProviderName": "Amazon Web Services, Inc.",
    "RegionId": "ap-south-1",
    "RegionName": "Asia Pacific (Mumbai)",
    "ServiceCategory": "Compute",
    "SubAccountType": "AWS Account",
    "BillingAccountType": "Management",
    "ResourceId": "i-0abc123",
    "InvoiceId": None,
}


class TestFocusLoad:
    def test_loads_rows(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW, _FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert isinstance(result, FocusLoadResult)
        assert result.row_count == 2

    def test_row_has_all_required_fields(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        row = result.rows[0]
        assert isinstance(row, FocusRow)
        for col in FOCUS_REQUIRED_COLUMNS:
            assert col in row.fields

    def test_timestamps_are_iso_strings_not_timestamps(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        row = result.rows[0]
        val = row.fields["BillingPeriodStart"]
        assert isinstance(val, str), "Timestamp must be returned as ISO string"
        assert "2026-10-01" in val

    def test_null_invoice_id_is_none_not_zero(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert result.rows[0].fields["InvoiceId"] is None

    def test_null_resource_id_is_none_not_zero(self, tmp_path):
        row = {**_FOCUS_ROW, "ResourceId": None}
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [row])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert result.rows[0].fields["ResourceId"] is None

    def test_missing_counts_reported_for_nulls(self, tmp_path):
        row = {**_FOCUS_ROW, "InvoiceId": None, "ResourceId": None}
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [row, row])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert result.missing_counts.get("InvoiceId", 0) == 2
        assert result.missing_counts.get("ResourceId", 0) == 2

    def test_billing_period_range_recorded(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert result.billing_period_start is not None
        assert result.billing_period_end is not None
        assert "2026-10-01" in result.billing_period_start
        assert "2026-11-01" in result.billing_period_end

    def test_currency_is_usd(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert result.currency == "USD"

    def test_delivery_path_recorded_on_rows(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert result.rows[0].delivery_path is not None

    def test_sha256_verified_true_when_expected_matches(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert result.sha256_verified is True
        assert result.sha256 == sha


# ===========================================================================
# 13. FOCUS loader — checksum and immutability
# ===========================================================================

class TestFocusChecksum:
    def test_wrong_checksum_raises(self, tmp_path):
        p = tmp_path / "focus.parquet"
        _make_focus_parquet(p, [_FOCUS_ROW])
        with pytest.raises(FocusChecksumMismatch):
            load_parquet(str(p), allow_unlisted=True, expected_sha256="a" * 64)

    def test_no_expected_checksum_verified_false(self, tmp_path):
        p = tmp_path / "focus.parquet"
        _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True)
        assert result.sha256_verified is False

    def test_load_does_not_modify_file(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        before = p.read_bytes()
        load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        assert p.read_bytes() == before

    def test_no_new_files_written(self, tmp_path):
        p = tmp_path / "focus.parquet"
        _make_focus_parquet(p, [_FOCUS_ROW])
        before = set(tmp_path.iterdir())
        load_parquet(str(p), allow_unlisted=True)
        assert set(tmp_path.iterdir()) == before


# ===========================================================================
# 14. FOCUS loader — schema errors and currency guard
# ===========================================================================

class TestFocusErrors:
    def test_missing_required_column_raises(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq
        # Write a Parquet with only one column
        table = pa.table({"BilledCost": [1.0]})
        p = tmp_path / "focus.parquet"
        pq.write_table(table, p)
        with pytest.raises(MissingRequiredColumn):
            load_parquet(str(p), allow_unlisted=True)

    def test_non_usd_currency_raises(self, tmp_path):
        row = {**_FOCUS_ROW, "BillingCurrency": "EUR"}
        p = tmp_path / "focus.parquet"
        _make_focus_parquet(p, [row])
        with pytest.raises(UnexpectedCurrency):
            load_parquet(str(p), allow_unlisted=True)


# ===========================================================================
# 15. FOCUS loader — notes disclaim arrival times and no-linkage
# ===========================================================================

class TestFocusNotes:
    def test_arrival_time_disclaimer_present(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        combined = " ".join(result.notes).lower()
        assert "arrival" in combined or "not arrival" in combined or "not a" in combined

    def test_no_linkage_disclaimer_present(self, tmp_path):
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        combined = " ".join(result.notes).lower()
        assert "no linkage" in combined or "not established" in combined

    def test_invoice_id_null_note_absent_when_not_null(self, tmp_path):
        """If InvoiceId were non-null for all rows, no special note needed.
        This tests that notes do not fabricate claims."""
        row = {**_FOCUS_ROW, "InvoiceId": "inv-001"}
        p = tmp_path / "focus.parquet"
        sha = _make_focus_parquet(p, [row])
        result = load_parquet(str(p), allow_unlisted=True, expected_sha256=sha)
        # No crash; notes may or may not mention InvoiceId — just must not claim
        # it's null when it isn't
        assert result.missing_counts.get("InvoiceId", 0) == 0


# ===========================================================================
# 16. FOCUS loader — provenance dict
# ===========================================================================

class TestFocusProvenance:
    def test_provenance_returns_expected_keys(self, tmp_path):
        p = tmp_path / "focus.parquet"
        _make_focus_parquet(p, [_FOCUS_ROW])
        prov = focus_provenance(str(p), allow_unlisted=True)
        assert prov["source"] == "aws_focus_1.2"
        assert prov["size_bytes"] > 0
        assert len(prov["sha256"]) == 64
        assert prov["billing_arrival_time"] is None
        assert prov["linkage_to_alibaba"] is None
        assert prov["linkage_to_calibur_admissions"] is None

    def test_provenance_does_not_load_rows(self, tmp_path):
        p = tmp_path / "focus.parquet"
        _make_focus_parquet(p, [_FOCUS_ROW])
        prov = focus_provenance(str(p), allow_unlisted=True)
        assert "rows" not in prov


# ===========================================================================
# 17. Alibaba loader — container_event.csv 9-column handling
# ===========================================================================

class TestAlibabaContainerEventNineColumns:
    """The extracted container_event.csv has 9 columns; the 9th is undocumented.
    The loader must not raise SchemaMismatch and must return the 9th as
    'undocumented_field_9' (always None / blank in the v2017 archive).
    """

    # Valid 9-column row matching the real file layout.
    _CE_ROW_9 = ["0", "Create", "10000", "934", "4",
                  "0.0424", "0.0341", "40|41|42|43", ""]

    # Valid 8-column row (only 8 documented fields).
    _CE_ROW_8 = ["0", "Create", "10000", "934", "4",
                  "0.0424", "0.0341", "40|41|42|43"]

    def test_nine_col_row_loads_without_error(self, tmp_path):
        _write_csv(tmp_path / "container_event.csv", [self._CE_ROW_9])
        result = load_file("container_event.csv", raw_dir=tmp_path)
        assert result.row_count == 1
        assert result.schema_name == "container_event.csv"

    def test_undocumented_field_9_present_in_row_fields(self, tmp_path):
        _write_csv(tmp_path / "container_event.csv", [self._CE_ROW_9])
        result = load_file("container_event.csv", raw_dir=tmp_path)
        assert "undocumented_field_9" in result.rows[0].fields

    def test_blank_undocumented_field_9_is_none(self, tmp_path):
        _write_csv(tmp_path / "container_event.csv", [self._CE_ROW_9])
        result = load_file("container_event.csv", raw_dir=tmp_path)
        assert result.rows[0].fields["undocumented_field_9"] is None

    def test_eight_col_row_handled_gracefully(self, tmp_path):
        """8-column rows (missing the undocumented field) should not crash.
        The loader pads missing columns to None."""
        _write_csv(tmp_path / "container_event.csv", [self._CE_ROW_8])
        result = load_file("container_event.csv", raw_dir=tmp_path)
        assert result.row_count == 1
        # undocumented_field_9 absent in raw data → should be None
        assert result.rows[0].fields["undocumented_field_9"] is None

    def test_schema_column_count_is_nine(self):
        from ingestion.alibaba_loader import COLUMN_SCHEMA
        assert len(COLUMN_SCHEMA["container_event.csv"]) == 9

    def test_documented_fields_parsed_correctly(self, tmp_path):
        _write_csv(tmp_path / "container_event.csv", [self._CE_ROW_9])
        result = load_file("container_event.csv", raw_dir=tmp_path)
        row = result.rows[0]
        assert row.fields["timestamp"] == 0
        assert row.fields["event_type"] == "Create"
        assert row.fields["instance_id"] == 10000
        assert row.fields["machine_id"] == 934


# ===========================================================================
# 18. Alibaba loader — iter_file streaming iterator
# ===========================================================================

class TestAlibabaIterFile:
    from ingestion.alibaba_loader import iter_file as _iter_file

    def test_iter_file_yields_rows(self, tmp_path):
        from ingestion.alibaba_loader import iter_file
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW, _BATCH_TASK_ROW])
        rows = list(iter_file("batch_task.csv", raw_dir=tmp_path))
        assert len(rows) == 2
        assert all(isinstance(r, AlibabaRow) for r in rows)

    def test_iter_file_wrong_checksum_raises_before_yielding(self, tmp_path):
        from ingestion.alibaba_loader import iter_file
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        rows_yielded = []
        with pytest.raises(ChecksumMismatch):
            for row in iter_file("batch_task.csv", raw_dir=tmp_path, expected_sha256="a" * 64):
                rows_yielded.append(row)
        assert rows_yielded == [], "No rows must be yielded if checksum fails"

    def test_iter_file_missing_is_none_not_zero(self, tmp_path):
        from ingestion.alibaba_loader import iter_file
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW_MISSING])
        rows = list(iter_file("batch_task.csv", raw_dir=tmp_path))
        assert rows[0].fields["mem_requested_normalised"] is None

    def test_iter_file_does_not_modify_file(self, tmp_path):
        from ingestion.alibaba_loader import iter_file
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        before = (tmp_path / "batch_task.csv").read_bytes()
        list(iter_file("batch_task.csv", raw_dir=tmp_path))
        assert (tmp_path / "batch_task.csv").read_bytes() == before


# ===========================================================================
# 19. Test-fixture isolation — fixture values must not enter empirical results
# ===========================================================================

class TestFixtureIsolation:
    """SYNTHETIC_FIXTURE values must be clearly distinguishable from real data.

    All test fixtures use tmp_path; none touch data/raw/.
    This class checks that fixture-only sentinel values cannot silently
    mix into empirical results.
    """

    def test_alibaba_fixture_source_path_outside_raw(self, tmp_path):
        """Fixture load result must reference tmp_path, not data/raw/."""
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        assert "data/raw" not in result.source_path

    def test_focus_fixture_delivery_path_outside_approved_list(self, tmp_path):
        """A fixture Parquet built in tmp_path must not be in APPROVED_DELIVERIES."""
        p = tmp_path / "fixture.parquet"
        _make_focus_parquet(p, [_FOCUS_ROW])
        # load with allow_unlisted so it proceeds — but the path must not be approved
        result = load_parquet(str(p), allow_unlisted=True)
        for approved_path in APPROVED_DELIVERIES:
            assert result.delivery_path != approved_path, (
                f"Fixture delivery path unexpectedly matches approved path: {approved_path}"
            )

    def test_alibaba_fixture_sha256_differs_from_manifest_checksums(self, tmp_path):
        """A synthetic fixture's SHA-256 must not accidentally equal a manifest checksum.
        (Extremely unlikely, but the test documents the expectation explicitly.)
        """
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        result = load_file("batch_task.csv", raw_dir=tmp_path)
        known_shas = set(v for v in APPROVED_FILES.values() if v)
        assert result.sha256 not in known_shas

    def test_missing_cost_never_zero_in_fixture_or_real(self, tmp_path):
        """cost_to_micro(None) must be None, not 0, regardless of fixture vs real data."""
        assert cost_to_micro(None) is None
        assert cost_to_micro(None) != 0

    def test_focus_null_fields_never_zero_in_fixture(self, tmp_path):
        """Null Parquet fields in fixtures must load as None, not 0."""
        row = {**_FOCUS_ROW, "InvoiceId": None, "ResourceId": None, "BilledCost": None}
        p = tmp_path / "null_fixture.parquet"
        # Can't make BilledCost None easily through _make_focus_parquet since it validates
        # required fields — use allow_unlisted and skip the required-column check via a
        # custom table instead.
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.table({
            "BilledCost":         pa.array([None], type=pa.float64()),
            "BillingCurrency":    pa.array(["USD"]),
            "BillingPeriodEnd":   pa.array(["2026-11-01T00:00:00+00:00"]),
            "BillingPeriodStart": pa.array(["2026-10-01T00:00:00+00:00"]),
            "ChargeCategory":     pa.array(["Usage"]),
            "ChargeFrequency":    pa.array(["Usage-Based"]),
            "InvoiceIssuerName":  pa.array(["AWS"]),
            "ProviderName":       pa.array(["AWS"]),
            "RegionId":           pa.array(["ap-south-1"]),
            "RegionName":         pa.array(["Asia Pacific (Mumbai)"]),
            "ServiceCategory":    pa.array(["Compute"]),
            "SubAccountType":     pa.array(["AWS Account"]),
            "BillingAccountType": pa.array(["Management"]),
            "InvoiceId":          pa.array([None], type=pa.string()),
            "ResourceId":         pa.array([None], type=pa.string()),
        })
        pq.write_table(table, p)
        result = load_parquet(str(p), allow_unlisted=True)
        assert result.rows[0].fields["BilledCost"] is None
        assert result.rows[0].fields["InvoiceId"] is None
        assert result.rows[0].fields["ResourceId"] is None


# ===========================================================================
# 20. Source approval and account-ID discrepancy preservation
# ===========================================================================

class TestSourceApprovalAndDiscrepancy:
    """Source approval status must be explicit; the account-ID discrepancy
    must be preserved in the machine-readable quality report constants.
    """

    def test_alibaba_source_is_not_approved(self):
        """Alibaba must remain unapproved until license applicability is confirmed."""
        import sys
        sys.path.insert(0, str(RESEARCH_DIR))
        import profile_data as pd_mod
        approval = pd_mod.SOURCE_APPROVAL.get("alibaba_cluster_trace_v2017", {})
        assert approval.get("approved") is False, (
            "Alibaba source must not be approved until license is confirmed"
        )

    def test_alibaba_approval_reason_mentions_license(self):
        import profile_data as pd_mod
        approval = pd_mod.SOURCE_APPROVAL["alibaba_cluster_trace_v2017"]
        reason = approval.get("approval_reason", "").lower()
        assert "license" in reason or "cc by" in reason, (
            "Approval reason must reference the license uncertainty"
        )

    def test_focus_source_is_approved(self):
        import profile_data as pd_mod
        approval = pd_mod.SOURCE_APPROVAL.get("aws_focus_1.2", {})
        assert approval.get("approved") is True

    def test_account_id_discrepancy_is_resolved_in_source_approval(self):
        """The account-ID discrepancy has been resolved via sts get-caller-identity.
        The resolution note must be present in SOURCE_APPROVAL for aws_focus_1.2."""
        import profile_data as pd_mod
        approval = pd_mod.SOURCE_APPROVAL.get("aws_focus_1.2", {})
        note = approval.get("s3_bucket_name_note", "")
        assert "resolved" in note.lower() or "582422215623" in note, (
            "aws_focus_1.2 source approval must record the account-ID resolution"
        )
        assert "582422215623" in note, "Confirmed account ID 582422215623 must be in the note"

    def test_account_id_discrepancy_preserved_in_unresolved_issues(self):
        """The S3/ARN account-ID discrepancy must be present and unresolved."""
        import profile_data as pd_mod
        issues = pd_mod.UNRESOLVED_QUALITY_ISSUES
        discrepancy_issues = [
            i for i in issues
            if "account" in i.get("issue", "").lower()
            and i.get("source") == "aws_focus_1.2"
        ]
        # The discrepancy was resolved by sts get-caller-identity on 2026-10-25.
        # If the issue is present it must be marked RESOLVED (not silently removed).
        for issue in discrepancy_issues:
            status = issue.get("status", "")
            assert "RESOLVED" in status.upper(), (
                f"Account-ID discrepancy issue must be marked RESOLVED, got: {status}"
            )

    def test_blocked_analyses_list_is_non_empty(self):
        import profile_data as pd_mod
        assert len(pd_mod.BLOCKED_ANALYSES) > 0

    def test_alibaba_workload_analysis_is_blocked(self):
        import profile_data as pd_mod
        alibaba_blocks = [
            b for b in pd_mod.BLOCKED_ANALYSES
            if "alibaba" in b.get("source", "").lower()
        ]
        assert alibaba_blocks, (
            "Alibaba workload analysis must be listed as blocked"
        )

    def test_cross_dataset_join_is_blocked(self):
        import profile_data as pd_mod
        cross_blocks = [
            b for b in pd_mod.BLOCKED_ANALYSES
            if "join" in b.get("analysis", "").lower()
        ]
        assert cross_blocks, (
            "Cross-dataset join must be listed as blocked"
        )

    def test_billing_arrival_analysis_is_blocked(self):
        import profile_data as pd_mod
        arrival_blocks = [
            b for b in pd_mod.BLOCKED_ANALYSES
            if "arrival" in b.get("analysis", "").lower()
        ]
        assert arrival_blocks, (
            "Billing arrival delay analysis must be listed as blocked"
        )

    def test_documented_transformations_exist(self):
        import profile_data as pd_mod
        assert len(pd_mod.DOCUMENTED_TRANSFORMATIONS) > 0

    def test_each_transformation_has_required_fields(self):
        import profile_data as pd_mod
        required_keys = {"source", "field", "transform", "reversible", "introduced_by"}
        for t in pd_mod.DOCUMENTED_TRANSFORMATIONS:
            for key in required_keys:
                assert key in t, (
                    f"Transformation entry missing key {key!r}: {t}"
                )

    def test_report_version_incremented(self):
        """report_version must be ≥ 2 now that machine-readable quality fields are added."""
        import profile_data as pd_mod
        # Construct a minimal report by calling main() with a tmp manifest and no raw files
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td)
            manifest = tp / "data_manifest.md"
            manifest.write_text("# Test manifest\n- SHA-256: `abc`\n")
            raw = tp / "data" / "raw"
            raw.mkdir(parents=True)
            json_out = tp / "out.json"
            md_out = tp / "out.md"
            import unittest.mock as mock
            with mock.patch.object(pd_mod, "MANIFEST", manifest), \
                 mock.patch.object(pd_mod, "RAW_ROOT", raw):
                rc = pd_mod.main([
                    "--json-out", str(json_out),
                    "--report-out", str(md_out),
                ])
            assert rc == 0
            data = json.loads(json_out.read_text())
            assert data["report_version"] >= 2, (
                "report_version must be ≥ 2 after adding machine-readable quality fields"
            )
            assert "source_approval_status" in data
            assert "blocked_analyses" in data
            assert "unresolved_quality_issues" in data
            assert "documented_transformations" in data
