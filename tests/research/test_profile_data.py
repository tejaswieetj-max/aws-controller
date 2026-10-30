"""Tests for research/profile_data.py.

All test fixtures are small, synthetic, and explicitly labelled.
They are created in temporary directories and never written to data/raw/.
Tests do not download data, access AWS, or make performance claims.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import tarfile
import textwrap
from pathlib import Path
from typing import ClassVar

import pytest

# ---------------------------------------------------------------------------
# Make the research module importable from the test suite.
# ---------------------------------------------------------------------------
RESEARCH_DIR = Path(__file__).resolve().parents[2] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from datetime import UTC

import profile_data as pd_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_MANIFEST = textwrap.dedent("""\
    # Data Manifest (test fixture copy — not research data)
    - SHA-256: `abc123`
""")


def _write_csv(path: Path, rows: list[list[str]], *, header: bool = False) -> None:
    """Write a CSV test fixture.  Never touches data/raw/."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def _write_tar_gz(archive_path: Path, members: list[tuple[str, bytes]]) -> None:
    """Write a minimal tar.gz fixture.  Never touches data/raw/."""
    with tarfile.open(archive_path, "w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


# ---------------------------------------------------------------------------
# 1. discover_raw_files
# ---------------------------------------------------------------------------
class TestDiscoverRawFiles:
    def test_returns_empty_list_when_directory_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pd_mod, "RAW_ROOT", tmp_path / "nonexistent")
        assert pd_mod.discover_raw_files() == []

    def test_finds_csv_and_parquet_but_not_gz(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "a.csv").write_text("1,2,3\n")
        (raw / "b.parquet").write_bytes(b"\x00")
        (raw / "c.tar.gz").write_bytes(b"\x00")  # should be excluded
        monkeypatch.setattr(pd_mod, "RAW_ROOT", raw)
        found = pd_mod.discover_raw_files()
        names = {p.name for p in found}
        assert "a.csv" in names
        assert "b.parquet" in names
        assert "c.tar.gz" not in names

    def test_recurses_into_subdirectories(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw"
        sub = raw / "sub"
        sub.mkdir(parents=True)
        (sub / "x.csv").write_text("1\n")
        monkeypatch.setattr(pd_mod, "RAW_ROOT", raw)
        found = pd_mod.discover_raw_files()
        assert any(p.name == "x.csv" for p in found)

    def test_sorted_deterministic(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw"
        raw.mkdir()
        for name in ["z.csv", "a.csv", "m.csv"]:
            (raw / name).write_text("1\n")
        monkeypatch.setattr(pd_mod, "RAW_ROOT", raw)
        found = pd_mod.discover_raw_files()
        assert found == sorted(found)


# ---------------------------------------------------------------------------
# 2. _sha256_file
# ---------------------------------------------------------------------------
class TestSha256File:
    def test_known_hash(self, tmp_path):
        import hashlib
        content = b"hello calibur"
        f = tmp_path / "t.bin"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert pd_mod._sha256_file(f) == expected

    def test_empty_file_hash(self, tmp_path):
        import hashlib
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert pd_mod._sha256_file(f) == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# 3. _profile_csv — Alibaba batch_task fixture
# ---------------------------------------------------------------------------
class TestProfileCsvAlibaba:
    def _batch_task_rows(self) -> list[list[str]]:
        # Fixture data — NOT research data; labelled synthetic in test only.
        return [
            ["100", "3", "200", "1", "10", "Terminated", "50", "0.01"],
            ["101", "2", "201", "1", "20", "Terminated", "",   ""],    # plan_cpu/mem blank
            ["102", "1", "202", "1", "30", "Waiting",    "50", "0.02"],
        ]

    def test_row_count_and_schema_match(self, tmp_path):
        p = tmp_path / "batch_task.csv"
        _write_csv(p, self._batch_task_rows())
        schema = pd_mod.ALIBABA_SCHEMAS["batch_task.csv"]
        result = pd_mod._profile_csv(p, schema, has_header=False, dataset_label="test_fixture", root=tmp_path)
        assert result["row_count"] == 3
        assert result["schema_match"] == "exact_match"
        assert result["file_type"] == "csv"

    def test_missing_count_for_documented_blank_sentinels(self, tmp_path):
        p = tmp_path / "batch_task.csv"
        _write_csv(p, self._batch_task_rows())
        schema = pd_mod.ALIBABA_SCHEMAS["batch_task.csv"]
        result = pd_mod._profile_csv(p, schema, has_header=False, dataset_label="test_fixture", root=tmp_path)
        # Row 1 has blank plan_cpu (col 6) and plan_mem (col 7)
        plan_cpu = next(f for f in result["fields"] if f["name"] == "plan_cpu")
        plan_mem = next(f for f in result["fields"] if f["name"] == "plan_mem")
        assert plan_cpu["missing_count"] == 1
        assert plan_mem["missing_count"] == 1
        # missing and sentinel are the same for Alibaba (blank == documented sentinel)
        assert plan_cpu["sentinel_count"] == plan_cpu["missing_count"]

    def test_exact_duplicate_detection_small_file(self, tmp_path):
        p = tmp_path / "batch_task.csv"
        rows = self._batch_task_rows() + [self._batch_task_rows()[0]]  # duplicate row 0
        _write_csv(p, rows)
        schema = pd_mod.ALIBABA_SCHEMAS["batch_task.csv"]
        result = pd_mod._profile_csv(p, schema, has_header=False, dataset_label="test_fixture", root=tmp_path)
        assert result["exact_duplicate_record_count"] == 1

    def test_empty_file_raises_profile_error(self, tmp_path):
        p = tmp_path / "batch_task.csv"
        p.write_text("")
        schema = pd_mod.ALIBABA_SCHEMAS["batch_task.csv"]
        with pytest.raises(pd_mod.ProfileError, match="Empty CSV"):
            pd_mod._profile_csv(p, schema, has_header=False, dataset_label="test_fixture", root=tmp_path)

    def test_schema_mismatch_extra_columns_flagged(self, tmp_path):
        p = tmp_path / "batch_task.csv"
        rows = [r + ["extra_col"] for r in self._batch_task_rows()]
        _write_csv(p, rows)
        schema = pd_mod.ALIBABA_SCHEMAS["batch_task.csv"]
        result = pd_mod._profile_csv(p, schema, has_header=False, dataset_label="test_fixture", root=tmp_path)
        assert "extra_columns" in result["schema_match"]

    def test_schema_mismatch_fewer_columns_flagged(self, tmp_path):
        p = tmp_path / "batch_task.csv"
        rows = [r[:4] for r in self._batch_task_rows()]  # truncated to 4 cols
        _write_csv(p, rows)
        schema = pd_mod.ALIBABA_SCHEMAS["batch_task.csv"]
        result = pd_mod._profile_csv(p, schema, has_header=False, dataset_label="test_fixture", root=tmp_path)
        assert "missing_columns" in result["schema_match"]

    def test_all_six_alibaba_schemas_have_correct_classification(self):
        for fname, schema in pd_mod.ALIBABA_SCHEMAS.items():
            for col in schema:
                assert col["classification"] in (
                    "observed", "derived_deterministic", "estimated", "unknown"
                ), f"{fname}/{col['name']} has invalid classification"

    def test_no_timestamp_inferred_as_arrival_time(self):
        """Billing-arrival time must never be inferred from billing-period timestamps.
        For Alibaba: no column may be labelled as a billing arrival time.
        """
        for fname, schema in pd_mod.ALIBABA_SCHEMAS.items():
            for col in schema:
                reason_lower = col["reason"].lower()
                assert "arrival" not in reason_lower or "not" in reason_lower, (
                    f"{fname}/{col['name']} reason appears to claim billing-arrival semantics"
                )


# ---------------------------------------------------------------------------
# 4. _profile_parquet (FOCUS 1.2) — uses a real minimal Parquet fixture via pyarrow
# ---------------------------------------------------------------------------
class TestProfileParquet:
    def _make_focus_parquet(self, tmp_path: Path, *, omit_field: str | None = None) -> Path:
        """Create a minimal Parquet fixture that conforms to FOCUS 1.2 required columns.
        FIXTURE — not research data.
        """
        from datetime import datetime

        import pyarrow as pa
        import pyarrow.parquet as pq

        ts_end   = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
        ts_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

        required_col_data: dict[str, pa.Array] = {
            "BilledCost":         pa.array([0.01, 0.02], type=pa.float64()),
            "BillingCurrency":    pa.array(["USD", "USD"]),
            "BillingPeriodEnd":   pa.array([ts_end, ts_end],   type=pa.timestamp("ms", tz="UTC")),
            "BillingPeriodStart": pa.array([ts_start, ts_start], type=pa.timestamp("ms", tz="UTC")),
            "ChargeCategory":     pa.array(["Usage", "Tax"],  type=pa.string()),
            "ChargeFrequency":    pa.array(["Usage-Based", "Usage-Based"]),
            "InvoiceIssuerName":  pa.array(["AWS", "AWS"]),
            "ProviderName":       pa.array(["AWS", "AWS"]),
            "RegionId":           pa.array(["ap-south-1", "ap-south-1"]),
            "RegionName":         pa.array(["Asia Pacific (Mumbai)", "Asia Pacific (Mumbai)"]),
            "ServiceCategory":    pa.array(["Compute", "Compute"]),
            "SubAccountType":     pa.array(["AWS Account", "AWS Account"]),
            "BillingAccountType": pa.array(["Management Account", "Management Account"]),
        }
        if omit_field and omit_field in required_col_data:
            del required_col_data[omit_field]

        table = pa.table(required_col_data)
        out = tmp_path / "test_focus.snappy.parquet"
        pq.write_table(table, out, compression="snappy")
        return out

    def test_valid_focus_fixture_profiles_without_error(self, tmp_path):
        p = self._make_focus_parquet(tmp_path)
        result = pd_mod._profile_parquet(p, root=tmp_path)
        assert result["row_count"] == 2
        assert result["missing_required_fields"] == []
        assert result["file_type"] == "parquet/snappy"

    def test_missing_required_field_raises_profile_error(self, tmp_path):
        p = self._make_focus_parquet(tmp_path, omit_field="BilledCost")
        with pytest.raises(pd_mod.ProfileError, match="BilledCost"):
            pd_mod._profile_parquet(p, root=tmp_path)

    def test_field_classifications_are_valid(self, tmp_path):
        p = self._make_focus_parquet(tmp_path)
        result = pd_mod._profile_parquet(p, root=tmp_path)
        valid = {"observed", "derived_deterministic", "estimated", "unknown"}
        for field in result["fields"]:
            assert field["classification"] in valid, (
                f"Field {field['name']} has invalid classification: {field['classification']}"
            )

    def test_billing_period_timestamps_not_labelled_as_arrival_times(self, tmp_path):
        p = self._make_focus_parquet(tmp_path)
        result = pd_mod._profile_parquet(p, root=tmp_path)
        for field in result["fields"]:
            if field["name"] in ("BillingPeriodStart", "BillingPeriodEnd",
                                 "ChargePeriodStart", "ChargePeriodEnd"):
                reason = field["reason"].lower()
                # Must explicitly state this is NOT an arrival timestamp.
                assert "not" in reason and ("arrival" in reason or "boundary" in reason), (
                    f"{field['name']} reason does not disclaim arrival-time interpretation: {field['reason']}"
                )

    def test_empty_parquet_raises_profile_error(self, tmp_path):
        p = tmp_path / "empty.parquet"
        p.write_bytes(b"")
        with pytest.raises(pd_mod.ProfileError, match="Empty Parquet"):
            pd_mod._profile_parquet(p, root=tmp_path)

    def test_null_counts_reported_accurately(self, tmp_path):
        from datetime import datetime

        import pyarrow as pa
        import pyarrow.parquet as pq

        ts_end   = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
        ts_start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

        table = pa.table({
            "BilledCost":         pa.array([0.01, None], type=pa.float64()),
            "BillingCurrency":    pa.array(["USD", "USD"]),
            "BillingPeriodEnd":   pa.array([ts_end, ts_end],     type=pa.timestamp("ms", tz="UTC")),
            "BillingPeriodStart": pa.array([ts_start, ts_start], type=pa.timestamp("ms", tz="UTC")),
            "ChargeCategory":     pa.array(["Usage", "Usage"]),
            "ChargeFrequency":    pa.array(["Usage-Based", "Usage-Based"]),
            "InvoiceIssuerName":  pa.array(["AWS", "AWS"]),
            "ProviderName":       pa.array(["AWS", "AWS"]),
            "RegionId":           pa.array(["ap-south-1", "ap-south-1"]),
            "RegionName":         pa.array(["Asia Pacific (Mumbai)", "Asia Pacific (Mumbai)"]),
            "ServiceCategory":    pa.array(["Compute", "Compute"]),
            "SubAccountType":     pa.array(["AWS Account", "AWS Account"]),
            "BillingAccountType": pa.array(["Management Account", "Management Account"]),
        })
        p = tmp_path / "nullable.snappy.parquet"
        pq.write_table(table, p, compression="snappy")
        result = pd_mod._profile_parquet(p, root=tmp_path)
        billed = next(f for f in result["fields"] if f["name"] == "BilledCost")
        assert billed["missing_count"] == 1


# ---------------------------------------------------------------------------
# 5. _profile_tar — archive-only, no extraction
# ---------------------------------------------------------------------------
class TestProfileTar:
    def test_lists_members_without_extracting(self, tmp_path):
        arc = tmp_path / "test.tar.gz"
        _write_tar_gz(arc, [
            ("trace/batch_task.csv", b"1,2,3\n"),
            ("trace/server_usage.csv", b"4,5,6\n"),
        ])
        result = pd_mod._profile_tar(arc, manifest_text="", root=tmp_path)
        assert result["file_type"] == "tar.gz"
        assert result["member_count"] == 2
        names = {m["name"] for m in result["members"]}
        assert "trace/batch_task.csv" in names
        # Confirm nothing was extracted into the archive directory.
        assert not (tmp_path / "trace").exists()

    def test_sha256_mismatch_noted(self, tmp_path):
        arc = tmp_path / "test.tar.gz"
        _write_tar_gz(arc, [("a.csv", b"data")])
        result = pd_mod._profile_tar(arc, manifest_text="SHA-256: `definitely_wrong_hash`", root=tmp_path)
        warn_notes = [n for n in result["notes"] if "WARNING" in n]
        assert warn_notes, "Expected a SHA-256 mismatch warning in notes"

    def test_sha256_match_noted(self, tmp_path):
        import hashlib
        arc = tmp_path / "test.tar.gz"
        _write_tar_gz(arc, [("a.csv", b"data")])
        actual_sha = hashlib.sha256(arc.read_bytes()).hexdigest()
        result = pd_mod._profile_tar(arc, manifest_text=f"SHA-256: `{actual_sha}`", root=tmp_path)
        warn_notes = [n for n in result["notes"] if "WARNING" in n]
        assert not warn_notes, f"Unexpected SHA-256 mismatch warning: {warn_notes}"

    def test_empty_archive_raises_profile_error(self, tmp_path):
        arc = tmp_path / "empty.tar.gz"
        arc.write_bytes(b"")
        with pytest.raises(pd_mod.ProfileError, match="Empty archive"):
            pd_mod._profile_tar(arc, manifest_text="", root=tmp_path)

    def test_corrupt_archive_raises_profile_error(self, tmp_path):
        arc = tmp_path / "corrupt.tar.gz"
        arc.write_bytes(b"this is not a valid tar.gz file")
        with pytest.raises(pd_mod.ProfileError, match="Cannot read archive"):
            pd_mod._profile_tar(arc, manifest_text="", root=tmp_path)


# ---------------------------------------------------------------------------
# 6. profile_file dispatch
# ---------------------------------------------------------------------------
class TestProfileFileDispatch:
    def test_csv_with_known_name_dispatched_to_alibaba(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pd_mod, "ROOT", tmp_path)
        p = tmp_path / "batch_task.csv"
        _write_csv(p, [["1", "2", "3", "4", "5", "Terminated", "50", "0.01"]])
        result = pd_mod.profile_file(p, MINIMAL_MANIFEST)
        assert result["file_type"] == "csv"
        assert "alibaba" in result["dataset"]

    def test_csv_with_unknown_name_returns_unsupported_note(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pd_mod, "ROOT", tmp_path)
        p = tmp_path / "unknown_schema.csv"
        p.write_text("a,b,c\n1,2,3\n")
        result = pd_mod.profile_file(p, MINIMAL_MANIFEST)
        assert result["dataset"] == "unknown"
        assert any("No documented schema" in n for n in result["notes"])

    def test_unsupported_extension_returns_unsupported_note(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pd_mod, "ROOT", tmp_path)
        p = tmp_path / "data.xml"
        p.write_bytes(b"<root/>")
        result = pd_mod.profile_file(p, MINIMAL_MANIFEST)
        assert "unsupported" in result["file_type"]


# ---------------------------------------------------------------------------
# 7. render_markdown
# ---------------------------------------------------------------------------
class TestRenderMarkdown:
    def _minimal_report(self) -> dict:
        return {
            "report_version": 1,
            "generated_at_utc": "2026-10-25T00:00:00+00:00",
            "repository_root": "/fake/root",
            "raw_directory_present": True,
            "files_discovered": 1,
            "files_profiled": 1,
            "errors": [],
            "results": [{
                "relative_path": "data/raw/test.csv",
                "dataset": "test_fixture",
                "file_type": "csv",
                "encoding": "utf-8",
                "size_bytes": 100,
                "sha256": "abc123",
                "row_count": 5,
                "exact_duplicate_record_count": 0,
                "key_uniqueness": "no_exact_duplicates_found",
                "missing_required_fields": [],
                "fields": [{
                    "name": "col_a",
                    "classification": "observed",
                    "missing_count": 0,
                    "invalid_count": 0,
                    "sentinel_count": 0,
                    "reason": "test reason",
                }],
                "notes": ["Test note."],
            }],
            "research_gate": "AMBER",
            "research_gate_reason": "Test reason.",
        }

    def test_research_gate_always_amber(self):
        md = pd_mod.render_markdown(self._minimal_report())
        assert "AMBER" in md

    def test_strict_constraint_disclaimer_present(self):
        md = pd_mod.render_markdown(self._minimal_report())
        assert "profiling statistics are descriptive" in md.lower() or "strict constraint" in md.lower()

    def test_missing_required_field_warning_rendered(self):
        report = self._minimal_report()
        report["results"][0]["missing_required_fields"] = ["BilledCost"]
        md = pd_mod.render_markdown(report)
        assert "BilledCost" in md
        assert "REQUIRED FIELDS ABSENT" in md

    def test_pipe_in_reason_escaped(self):
        report = self._minimal_report()
        report["results"][0]["fields"][0]["reason"] = "A|B|C"
        md = pd_mod.render_markdown(report)
        assert "A\\|B\\|C" in md

    def test_errors_section_rendered_when_present(self):
        report = self._minimal_report()
        report["errors"] = ["Something went wrong."]
        md = pd_mod.render_markdown(report)
        assert "Errors" in md
        assert "Something went wrong." in md


# ---------------------------------------------------------------------------
# 8. main() — integration via temporary directory
# ---------------------------------------------------------------------------
class TestMain:
    def test_main_missing_manifest_returns_2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pd_mod, "MANIFEST", tmp_path / "nonexistent.md")
        monkeypatch.setattr(pd_mod, "RAW_ROOT", tmp_path / "data" / "raw")
        rc = pd_mod.main([
            "--json-out", str(tmp_path / "out.json"),
            "--report-out", str(tmp_path / "out.md"),
        ])
        assert rc == 2

    def test_main_no_raw_files_writes_empty_report(self, tmp_path, monkeypatch):
        manifest = tmp_path / "research" / "data_manifest.md"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(MINIMAL_MANIFEST)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        monkeypatch.setattr(pd_mod, "MANIFEST", manifest)
        monkeypatch.setattr(pd_mod, "RAW_ROOT", raw)
        json_out = tmp_path / "research" / "out.json"
        md_out = tmp_path / "research" / "out.md"
        rc = pd_mod.main([
            "--json-out", str(json_out),
            "--report-out", str(md_out),
        ])
        assert rc == 0
        data = json.loads(json_out.read_text())
        assert data["files_discovered"] == 0
        assert data["research_gate"] == "AMBER"
        assert md_out.exists()

    def test_main_profiles_small_csv_fixture(self, tmp_path, monkeypatch):
        manifest = tmp_path / "research" / "data_manifest.md"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(MINIMAL_MANIFEST)
        raw = tmp_path / "data" / "raw" / "alibaba_trace_2017" / "alibaba-trace-2017"
        raw.mkdir(parents=True)
        _write_csv(
            raw / "batch_task.csv",
            [["1", "2", "3", "4", "5", "Terminated", "50", "0.01"]],
        )
        monkeypatch.setattr(pd_mod, "MANIFEST", manifest)
        monkeypatch.setattr(pd_mod, "RAW_ROOT", tmp_path / "data" / "raw")
        json_out = tmp_path / "research" / "out.json"
        md_out = tmp_path / "research" / "out.md"
        rc = pd_mod.main([
            "--json-out", str(json_out),
            "--report-out", str(md_out),
        ])
        assert rc == 0
        data = json.loads(json_out.read_text())
        assert data["files_profiled"] >= 1
        assert data["research_gate"] == "AMBER"

    def test_main_atomic_no_partial_report_on_error(self, tmp_path, monkeypatch):
        """A profile failure must not leave a partial report file."""
        manifest = tmp_path / "research" / "data_manifest.md"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(MINIMAL_MANIFEST)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        # Write an empty CSV — will raise ProfileError.
        (raw / "batch_task.csv").write_bytes(b"")
        monkeypatch.setattr(pd_mod, "MANIFEST", manifest)
        monkeypatch.setattr(pd_mod, "RAW_ROOT", raw)
        json_out = tmp_path / "research" / "out.json"
        md_out = tmp_path / "research" / "out.md"
        rc = pd_mod.main([
            "--json-out", str(json_out),
            "--report-out", str(md_out),
        ])
        assert rc == 2
        assert not json_out.exists(), "Partial JSON report must not be written on error"
        assert not md_out.exists(), "Partial Markdown report must not be written on error"
        # Also confirm no .tmp files were left behind.
        assert not (tmp_path / "research" / "out.json.tmp").exists()
        assert not (tmp_path / "research" / "out.md.tmp").exists()

    def test_main_does_not_modify_raw_files(self, tmp_path, monkeypatch):
        """Raw files must be untouched after profiling."""
        manifest = tmp_path / "research" / "data_manifest.md"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(MINIMAL_MANIFEST)
        raw = tmp_path / "data" / "raw" / "alibaba_trace_2017" / "alibaba-trace-2017"
        raw.mkdir(parents=True)
        csv_path = raw / "batch_task.csv"
        original_content = "1,2,3,4,5,Terminated,50,0.01\n"
        csv_path.write_text(original_content)
        import os
        orig_mtime = os.path.getmtime(csv_path)
        monkeypatch.setattr(pd_mod, "MANIFEST", manifest)
        monkeypatch.setattr(pd_mod, "RAW_ROOT", tmp_path / "data" / "raw")
        pd_mod.main([
            "--json-out", str(tmp_path / "research" / "out.json"),
            "--report-out", str(tmp_path / "research" / "out.md"),
        ])
        assert csv_path.read_text() == original_content
        assert os.path.getmtime(csv_path) == orig_mtime

    def test_reports_never_written_under_raw(self, tmp_path, monkeypatch):
        manifest = tmp_path / "research" / "data_manifest.md"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(MINIMAL_MANIFEST)
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True)
        _write_csv(
            raw / "batch_task.csv",
            [["1", "2", "3", "4", "5", "Terminated", "50", "0.01"]],
        )
        monkeypatch.setattr(pd_mod, "MANIFEST", manifest)
        monkeypatch.setattr(pd_mod, "RAW_ROOT", raw)
        # Try to force output into data/raw/ — must be rejected by the design.
        # The implementation writes under research/ by default; here we check
        # the default paths are outside data/raw/.
        assert not str(pd_mod.DEFAULT_JSON).startswith(str(pd_mod.RAW_ROOT))
        assert not str(pd_mod.DEFAULT_MD).startswith(str(pd_mod.RAW_ROOT))


# ---------------------------------------------------------------------------
# 9. Field-classification consistency
# ---------------------------------------------------------------------------
class TestFieldClassificationConsistency:
    VALID_CLASSIFICATIONS: ClassVar[set[str]] = {"observed", "derived_deterministic", "estimated", "unknown"}

    def test_all_focus_required_classifications_valid(self):
        for f in pd_mod.FOCUS_REQUIRED_FIELDS:
            assert f["classification"] in self.VALID_CLASSIFICATIONS

    def test_all_focus_optional_classifications_valid(self):
        for f in pd_mod.FOCUS_OPTIONAL_FIELDS:
            assert f["classification"] in self.VALID_CLASSIFICATIONS

    def test_all_alibaba_column_classifications_valid(self):
        for fname, schema in pd_mod.ALIBABA_SCHEMAS.items():
            for col in schema:
                assert col["classification"] in self.VALID_CLASSIFICATIONS, (
                    f"{fname}/{col['name']}: {col['classification']}"
                )

    def test_billing_period_fields_disclaim_arrival_time(self):
        """BillingPeriodStart/End and ChargePeriodStart/End must not be presented
        as billing-arrival timestamps.  The reason text must contain 'NOT' or 'not'
        near 'arrival'."""
        arrival_candidates = {
            "BillingPeriodStart", "BillingPeriodEnd",
            "ChargePeriodStart", "ChargePeriodEnd",
        }
        for f in pd_mod.FOCUS_REQUIRED_FIELDS + pd_mod.FOCUS_OPTIONAL_FIELDS:
            if f["name"] in arrival_candidates:
                r = f["reason"]
                assert ("NOT" in r or "not" in r) and (
                    "arrival" in r.lower() or "boundary" in r.lower()
                ), (
                    f"{f['name']} reason must disclaim arrival-time interpretation: {r}"
                )
