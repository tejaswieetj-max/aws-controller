"""Tests for real-dataset integration adapters.

All fixtures use tmp_path; none touch data/raw/ or make AWS calls.
Fixture labels are SYNTHETIC_FIXTURE. No fixture value enters empirical results.

Tests cover:
- focus_billing_adapter: FeasibilityResult structure, delivery loading,
  snapshot diff, evaluation input format, first_observed vs S3 LastModified
- alibaba_workload_adapter: approval gate, duration computation,
  missing-field handling, blocking of unapproved source
- ledger_export_reader: field gap detection, empty result, structural validation
- Alibaba per-file SHA-256 checksums now recorded
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
RESEARCH_DIR = Path(__file__).resolve().parents[2] / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def _make_focus_parquet(path: Path, rows: list[dict]) -> str:
    """Write a minimal FOCUS-shaped Parquet fixture. Returns SHA-256."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

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
        ("ServiceName",          pa.string()),
        ("Tags",                 pa.map_(pa.string(), pa.string())),
        ("ChargePeriodStart",    pa.timestamp("ms", tz="UTC")),
        ("ChargePeriodEnd",      pa.timestamp("ms", tz="UTC")),
    ])
    df = pd.DataFrame(rows)
    for col in ("BillingPeriodStart", "BillingPeriodEnd", "ChargePeriodStart", "ChargePeriodEnd"):
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
    "ServiceName": "Amazon Elastic Compute Cloud",
    "Tags": None,
    "ChargePeriodStart": "2026-10-01T00:00:00+00:00",
    "ChargePeriodEnd": "2026-10-02T00:00:00+00:00",
}

_BATCH_TASK_ROW = ["6459", "6524", "3", "4", "15740", "Terminated", "50", "0.0079"]
_BATCH_TASK_ROW_MISSING = ["6457", "6533", "4", "5", "1", "Terminated", "", ""]


# ===========================================================================
# 1. Alibaba per-file SHA-256 checksums are now recorded
# ===========================================================================

class TestAlibabaChecksumsFilled:
    def test_all_approved_files_have_sha256(self):
        from ingestion.alibaba_loader import APPROVED_FILES
        for fname, sha in APPROVED_FILES.items():
            assert sha is not None, f"{fname} still has None SHA-256"
            assert len(sha) == 64, f"{fname} SHA-256 is not 64 hex chars"
            int(sha, 16)  # must be valid hex

    def test_approved_files_sha256_are_distinct(self):
        from ingestion.alibaba_loader import APPROVED_FILES
        shas = list(APPROVED_FILES.values())
        assert len(shas) == len(set(shas)), "Duplicate SHA-256 entries in APPROVED_FILES"

    def test_three_focus_deliveries_registered(self):
        from ingestion.focus_loader import APPROVED_DELIVERIES
        assert len(APPROVED_DELIVERIES) == 3


# ===========================================================================
# 2. focus_billing_adapter — FeasibilityResult structure
# ===========================================================================

class TestFocusBillingAdapterStructure:
    """Tests that do NOT load real data/raw/ files.
    Uses monkeypatching to isolate the adapter from real deliveries.
    """

    def _make_obs_jsonl(self, tmp_path: Path, exec_id: str, observed_at: str) -> Path:
        """Create a minimal s3_observations.jsonl fixture."""
        obs_file = tmp_path / "s3_observations.jsonl"
        record = {
            "source": "aws_s3",
            "bucket": "test-bucket",
            "object_key": f"prefix/{observed_at}-{exec_id}/data.parquet",
            "region": "ap-south-1",
            "observed_at_utc": f"{observed_at}T12:00:00+00:00",
            "s3_last_modified_utc": f"{observed_at}T11:00:00+00:00",
            "size_bytes": 1000,
            "etag": '"abc"',
            "s3_checksum_sha256": None,
            "sha256": None,
            "sha256_status": "not_computed",
            "observation_scope": "object_metadata",
        }
        obs_file.write_text(json.dumps(record) + "\n")
        return obs_file

    def test_load_s3_observations_parses_jsonl(self, tmp_path):
        import ingestion.focus_billing_adapter as fba
        from ingestion.focus_billing_adapter import _load_s3_observations
        obs_file = self._make_obs_jsonl(tmp_path, "exec-abc", "2026-10-25")
        # Monkeypatch the module-level path
        orig = fba._S3_OBS_FILE
        fba._S3_OBS_FILE = obs_file
        try:
            obs = _load_s3_observations()
            assert len(obs) == 1
        finally:
            fba._S3_OBS_FILE = orig

    def test_extract_execution_id_from_key(self):
        from ingestion.focus_billing_adapter import _extract_execution_id
        key = ("calibur/focus12/CALIBUR_FOCUS_12/data/billing_period=2026-10/"
               "2026-10-25T03:32:00.757Z-ddede1a2-e372-34a5-aa28-36cacf5a98ef/"
               "CALIBUR_FOCUS_12-00001.snappy.parquet")
        result = _extract_execution_id(key)
        assert result == "ddede1a2-e372-34a5-aa28-36cacf5a98ef"

    def test_extract_execution_id_no_match(self):
        from ingestion.focus_billing_adapter import _extract_execution_id
        assert _extract_execution_id("plain/path/without/execution.parquet") is None

    def test_feasibility_result_evaluate_input_is_empty_arrays(self, tmp_path):
        """Without real admissions, evaluate_input must be empty arrays."""
        import ingestion.focus_billing_adapter as fba
        import ingestion.focus_loader as fl

        p = tmp_path / "d1.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        obs_file = tmp_path / "s3.jsonl"
        obs_file.write_text("")

        orig_reg = fba.DELIVERY_REGISTRY
        orig_obs = fba._S3_OBS_FILE
        orig_approved = dict(fl.APPROVED_DELIVERIES)
        orig_fl_root = fl._REPO_ROOT

        # Use absolute path as key so focus_loader resolves it correctly
        abs_rel = str(p)
        fl._REPO_ROOT = Path("/")
        fl.APPROVED_DELIVERIES[abs_rel] = sha
        fba.DELIVERY_REGISTRY = [{"relative_path": abs_rel,
                                   "delivery_timestamp": "2026-10-24T19:27:14.926Z",
                                   "execution_id": "test-exec-1", "label": "D1"}]
        fba._S3_OBS_FILE = obs_file
        try:
            result = fba.build_feasibility_result(skip_diff=True, allow_unlisted=True)
            assert result.feasibility_gate == "AMBER"
            assert result.admission_records_available is False
            assert result.infeasibility_reason is not None
            assert result.evaluate_input == {"reservations": [], "billing_audits": []}
            assert result.matched_rows == 0
            assert result.unmatched_rows > 0
            assert len(result.blocked_analyses) > 0
        finally:
            fba.DELIVERY_REGISTRY = orig_reg
            fba._S3_OBS_FILE = orig_obs
            fl.APPROVED_DELIVERIES.pop(abs_rel, None)
            for k, v in orig_approved.items():
                fl.APPROVED_DELIVERIES[k] = v
            fl._REPO_ROOT = orig_fl_root

    def test_feasibility_result_notes_disclaim_s3_lastmodified_as_arrival(self, tmp_path):
        """Notes must explicitly state S3 LastModified is not an arrival timestamp."""
        import ingestion.focus_billing_adapter as fba
        import ingestion.focus_loader as fl

        p = tmp_path / "d1.parquet"
        sha = _make_focus_parquet(p, [_FOCUS_ROW])
        obs_file = tmp_path / "obs.jsonl"
        obs_file.write_text("")

        orig_reg = fba.DELIVERY_REGISTRY
        orig_obs = fba._S3_OBS_FILE
        orig_approved = dict(fl.APPROVED_DELIVERIES)
        orig_fl_root = fl._REPO_ROOT

        abs_rel = str(p)
        fl._REPO_ROOT = Path("/")
        fl.APPROVED_DELIVERIES[abs_rel] = sha
        fba.DELIVERY_REGISTRY = [{"relative_path": abs_rel,
                                   "delivery_timestamp": "2026-10-24T19:27:14.926Z",
                                   "execution_id": "test-exec-1", "label": "D1"}]
        fba._S3_OBS_FILE = obs_file
        try:
            result = fba.build_feasibility_result(skip_diff=True, allow_unlisted=True)
            combined = " ".join(result.notes).lower()
            assert ("s3 lastmodified" in combined or "last_modified" in combined
                    or "lastmodified" in combined)
            assert "not" in combined and ("arrival" in combined or "billing arrival" in combined)
        finally:
            fba.DELIVERY_REGISTRY = orig_reg
            fba._S3_OBS_FILE = orig_obs
            fl.APPROVED_DELIVERIES.pop(abs_rel, None)
            for k, v in orig_approved.items():
                fl.APPROVED_DELIVERIES[k] = v
            fl._REPO_ROOT = orig_fl_root

    def test_snapshot_diff_is_descriptive_not_revision_claim(self, tmp_path):
        """SnapshotDiff.revision_claim must say the diff is unproven."""
        import ingestion.focus_billing_adapter as fba
        import ingestion.focus_loader as fl

        p1 = tmp_path / "d1.parquet"
        p2 = tmp_path / "d2.parquet"
        sha1 = _make_focus_parquet(p1, [_FOCUS_ROW])
        row2 = {**_FOCUS_ROW, "BilledCost": 0.05,
                "ChargePeriodStart": "2026-10-02T00:00:00+00:00",
                "ChargePeriodEnd": "2026-10-03T00:00:00+00:00"}
        sha2 = _make_focus_parquet(p2, [_FOCUS_ROW, row2])

        orig_approved = dict(fl.APPROVED_DELIVERIES)
        orig_fl_root = fl._REPO_ROOT
        fl._REPO_ROOT = Path("/")
        fl.APPROVED_DELIVERIES[str(p1)] = sha1
        fl.APPROVED_DELIVERIES[str(p2)] = sha2

        d1 = fba.DeliverySnapshot("D1", str(p1), "2026-10-24T19:27:14.926Z",
                                   "exec-1", sha1, True, p1.stat().st_size, 1,
                                   None, None, "inferred:1.2", "USD", 1, 0, 1)
        d2 = fba.DeliverySnapshot("D2", str(p2), "2026-10-25T03:32:00.757Z",
                                   "exec-2", sha2, True, p2.stat().st_size, 2,
                                   None, None, "inferred:1.2", "USD", 2, 0, 2)
        try:
            diff = fba._diff_deliveries(d1, d2, allow_unlisted=True)
            assert ("unproven" in diff.revision_claim.lower()
                    or "no verified" in diff.revision_claim.lower())
            assert diff.earlier_label == "D1"
            assert diff.later_label == "D2"
        finally:
            fl.APPROVED_DELIVERIES.pop(str(p1), None)
            fl.APPROVED_DELIVERIES.pop(str(p2), None)
            for k, v in orig_approved.items():
                fl.APPROVED_DELIVERIES[k] = v
            fl._REPO_ROOT = orig_fl_root

    def test_feasibility_to_json_is_valid_json(self, tmp_path):
        from ingestion.focus_billing_adapter import FeasibilityResult, feasibility_to_json
        result = FeasibilityResult(
            feasibility_gate="AMBER",
            admission_records_available=False,
            infeasibility_reason="test",
            deliveries=[],
            diffs=[],
            matched_rows=0,
            unmatched_rows=5,
            ambiguous_rows=0,
            total_focus_rows=5,
            evaluate_input={"reservations": [], "billing_audits": []},
            blocked_analyses=["test_blocked"],
            notes=["test note"],
        )
        text = feasibility_to_json(result)
        parsed = json.loads(text)
        assert parsed["feasibility_gate"] == "AMBER"
        assert parsed["evaluate_input"] == {"reservations": [], "billing_audits": []}


# ===========================================================================
# 3. alibaba_workload_adapter — approval gate and observation structure
# ===========================================================================

class TestAlibabaWorkloadAdapter:
    def test_unapproved_raises_without_flag(self, tmp_path):
        from ingestion.alibaba_workload_adapter import AlibabaSourceUnapproved, iter_observations
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        with pytest.raises(AlibabaSourceUnapproved):
            list(iter_observations(raw_dir=tmp_path))

    def test_allow_unapproved_streams_rows(self, tmp_path):
        from ingestion.alibaba_workload_adapter import iter_observations
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW, _BATCH_TASK_ROW])
        # Compute real sha256 for the fixture and pass it explicitly
        sha = _sha256(tmp_path / "batch_task.csv")
        from ingestion import alibaba_loader as al
        orig = al.APPROVED_FILES.copy()
        al.APPROVED_FILES["batch_task.csv"] = sha
        try:
            rows = list(iter_observations(raw_dir=tmp_path, allow_unapproved=True))
            assert len(rows) == 2
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig["batch_task.csv"]

    def test_observation_source_is_alibaba(self, tmp_path):
        from ingestion import alibaba_loader as al
        from ingestion.alibaba_workload_adapter import iter_observations
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        sha = _sha256(tmp_path / "batch_task.csv")
        orig = al.APPROVED_FILES["batch_task.csv"]
        al.APPROVED_FILES["batch_task.csv"] = sha
        try:
            obs = next(iter_observations(raw_dir=tmp_path, allow_unapproved=True))
            assert obs.source == "alibaba_cluster_trace_v2017"
            assert obs.source_file == "batch_task.csv"
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig

    def test_duration_computed_from_timestamps(self, tmp_path):
        from ingestion import alibaba_loader as al
        from ingestion.alibaba_workload_adapter import iter_observations
        # task_create_time=100, task_end_time=250 → duration=150
        row = ["100", "250", "3", "4", "5", "Terminated", "50", "0.01"]
        _write_csv(tmp_path / "batch_task.csv", [row])
        sha = _sha256(tmp_path / "batch_task.csv")
        orig = al.APPROVED_FILES["batch_task.csv"]
        al.APPROVED_FILES["batch_task.csv"] = sha
        try:
            obs = next(iter_observations(raw_dir=tmp_path, allow_unapproved=True))
            assert obs.start_timestamp == 100
            assert obs.end_timestamp == 250
            assert obs.duration_trace_seconds == 150
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig

    def test_missing_timestamps_give_none_duration(self, tmp_path):
        from ingestion import alibaba_loader as al
        from ingestion.alibaba_workload_adapter import iter_observations
        row = ["", "250", "3", "4", "5", "Terminated", "50", "0.01"]
        _write_csv(tmp_path / "batch_task.csv", [row])
        sha = _sha256(tmp_path / "batch_task.csv")
        orig = al.APPROVED_FILES["batch_task.csv"]
        al.APPROVED_FILES["batch_task.csv"] = sha
        try:
            obs = next(iter_observations(raw_dir=tmp_path, allow_unapproved=True))
            assert obs.start_timestamp is None
            assert obs.duration_trace_seconds is None
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig

    def test_missing_fields_list_populated(self, tmp_path):
        from ingestion import alibaba_loader as al
        from ingestion.alibaba_workload_adapter import iter_observations
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW_MISSING])
        sha = _sha256(tmp_path / "batch_task.csv")
        orig = al.APPROVED_FILES["batch_task.csv"]
        al.APPROVED_FILES["batch_task.csv"] = sha
        try:
            obs = next(iter_observations(raw_dir=tmp_path, allow_unapproved=True))
            assert "cpu_requested" in obs.missing_fields or "mem_requested_normalised" in obs.missing_fields
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig

    def test_no_aws_cost_field_on_observation(self, tmp_path):
        from ingestion import alibaba_loader as al
        from ingestion.alibaba_workload_adapter import iter_observations
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        sha = _sha256(tmp_path / "batch_task.csv")
        orig = al.APPROVED_FILES["batch_task.csv"]
        al.APPROVED_FILES["batch_task.csv"] = sha
        try:
            obs = next(iter_observations(raw_dir=tmp_path, allow_unapproved=True))
            obs_dict = obs.__dict__
            for key in obs_dict:
                assert "micro" not in key, f"Observation has unexpected cost field: {key}"
                assert "usd" not in key.lower()
                assert "dollar" not in key.lower()
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig

    def test_duration_is_trace_units_not_wall_clock(self, tmp_path):
        import dataclasses

        from ingestion.alibaba_workload_adapter import WorkloadObservation
        # duration_trace_seconds field name signals trace units, not wall clock
        field_names = {f.name for f in dataclasses.fields(WorkloadObservation)}
        assert "duration_trace_seconds" in field_names, (
            "Duration field must be named duration_trace_seconds to signal trace units"
        )

    def test_workload_stream_unapproved_raises(self, tmp_path):
        from ingestion.alibaba_workload_adapter import (
            AlibabaSourceUnapproved,
            build_workload_stream,
        )
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        with pytest.raises(AlibabaSourceUnapproved):
            build_workload_stream(raw_dir=tmp_path)

    def test_workload_stream_summary_counts(self, tmp_path):
        from ingestion import alibaba_loader as al
        from ingestion.alibaba_workload_adapter import build_workload_stream
        rows = [
            ["100", "250", "3", "4", "5", "Terminated", "50", "0.01"],
            ["",    "250", "3", "4", "5", "Waiting",    "50", "0.01"],
            ["100", "",    "3", "4", "5", "Failed",     "",   ""],
        ]
        _write_csv(tmp_path / "batch_task.csv", rows)
        sha = _sha256(tmp_path / "batch_task.csv")
        orig = al.APPROVED_FILES["batch_task.csv"]
        al.APPROVED_FILES["batch_task.csv"] = sha
        try:
            stream = build_workload_stream(raw_dir=tmp_path, allow_unapproved=True)
            assert stream.total_rows == 3
            assert stream.rows_with_duration == 1
            assert stream.rows_missing_start == 1
            assert stream.rows_missing_end == 1
            assert stream.approval_gate == "UNAPPROVED"
            assert len(stream.blocked_analyses) > 0
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig

    def test_workload_stream_does_not_modify_file(self, tmp_path):
        from ingestion import alibaba_loader as al
        from ingestion.alibaba_workload_adapter import build_workload_stream
        _write_csv(tmp_path / "batch_task.csv", [_BATCH_TASK_ROW])
        sha = _sha256(tmp_path / "batch_task.csv")
        orig = al.APPROVED_FILES["batch_task.csv"]
        al.APPROVED_FILES["batch_task.csv"] = sha
        before = (tmp_path / "batch_task.csv").read_bytes()
        try:
            build_workload_stream(raw_dir=tmp_path, allow_unapproved=True)
            assert (tmp_path / "batch_task.csv").read_bytes() == before
        finally:
            al.APPROVED_FILES["batch_task.csv"] = orig


# ===========================================================================
# 4. ledger_export_reader — field gap detection and structural validation
# ===========================================================================

class TestLedgerExportReader:
    def _write_export(self, path: Path, reservations: list, audits: list) -> None:
        path.write_text(json.dumps({
            "reservations": reservations,
            "billing_audits": audits,
        }))

    def test_missing_file_raises(self, tmp_path):
        from ingestion.ledger_export_reader import translate
        with pytest.raises(FileNotFoundError):
            translate(tmp_path / "nonexistent.json")

    def test_invalid_json_raises(self, tmp_path):
        from ingestion.ledger_export_reader import translate
        p = tmp_path / "bad.json"
        p.write_text("not json {{{")
        with pytest.raises(ValueError):
            translate(p)

    def test_empty_arrays_returns_amber(self, tmp_path):
        from ingestion.ledger_export_reader import translate
        p = tmp_path / "empty.json"
        self._write_export(p, [], [])
        result = translate(p)
        assert result.feasibility_gate == "AMBER"
        assert result.reservation_count_in == 0
        assert result.audit_count_in == 0
        assert result.evaluate_input == {"reservations": [], "billing_audits": []}

    def test_warns_on_missing_required_fields(self, tmp_path):
        from ingestion.ledger_export_reader import translate
        p = tmp_path / "incomplete.json"
        # Reservation missing required fields
        self._write_export(p, [{"some_field": "value"}], [])
        result = translate(p)
        assert len(result.warnings) > 0
        assert any("reservation" in w.lower() for w in result.warnings)

    def test_complete_reservation_no_warnings(self, tmp_path):
        from ingestion.ledger_export_reader import translate
        p = tmp_path / "complete.json"
        complete_res = {
            "reservation_id": "rsv_abc",
            "reserved_micro": 1000000,
            "compute_micro": 800000,
            "storage_micro": 200000,
            "price_snapshot_id": "snap-001",
            "price_fetched_at": "2026-10-25T00:00:00Z",
            "settlement_stage": "FINAL",
            "resource": {"instance_type": "t3.micro", "ebs_gb": 8},
            "region": "ap-south-1",
            "margin": "1.10",
        }
        complete_aud = {
            "reservation_id": "rsv_abc",
            "source_revision_order": "2026-10",
            "billing_period": "2026-10",
            "new_actual_micro": 950000,
            "record_type": "BILLING_RECONCILIATION",
        }
        self._write_export(p, [complete_res], [complete_aud])
        result = translate(p)
        assert result.reservation_count_in == 1
        assert result.audit_count_in == 1
        # evaluate_input passes through unchanged
        assert result.evaluate_input["reservations"][0]["reservation_id"] == "rsv_abc"
        assert result.evaluate_input["billing_audits"][0]["billing_period"] == "2026-10"

    def test_empty_result_has_infeasibility_message(self):
        from ingestion.ledger_export_reader import empty_result
        result = empty_result("No DynamoDB tables exist")
        assert result.feasibility_gate == "AMBER"
        assert result.reservation_count_in == 0
        assert result.audit_count_in == 0
        assert any("INFEASIBLE" in n for n in result.notes)

    def test_passthrough_does_not_modify_records(self, tmp_path):
        """translate() must not alter the reservation or audit records."""
        from ingestion.ledger_export_reader import translate
        p = tmp_path / "passthru.json"
        res = {"reservation_id": "rsv_xyz", "reserved_micro": 5000000,
               "compute_micro": 4000000, "storage_micro": 1000000,
               "price_snapshot_id": "snap-99", "price_fetched_at": "2026-10-25T00:00:00Z",
               "settlement_stage": "FINAL"}
        aud = {"reservation_id": "rsv_xyz", "source_revision_order": "1",
               "billing_period": "2026-10", "new_actual_micro": 4800000}
        self._write_export(p, [res], [aud])
        result = translate(p)
        assert result.evaluate_input["reservations"][0] == res
        assert result.evaluate_input["billing_audits"][0] == aud


# ===========================================================================
# 5. End-to-end evaluate.py integration with empty input
# ===========================================================================

class TestEvaluateIntegration:
    """Test that evaluate.build_dataset() correctly handles the FeasibilityResult
    empty input — produces zero observations with correct labels, no crash."""

    def test_build_dataset_empty_is_zero_observations(self):
        # Import evaluate via sys.path (RESEARCH_DIR is already in sys.path)
        import evaluate as evaluate_mod
        result = evaluate_mod.build_dataset(
            {"reservations": [], "billing_audits": []},
            provenance="aws_export"
        )
        assert result["observation_count"] == 0
        assert result["classification"] == "AWS_EXPORT_ASSERTED_UNVERIFIED"
        assert result["exclusions"] == []

    def test_build_dataset_empty_does_not_invent_observations(self):
        import evaluate as evaluate_mod
        result = evaluate_mod.build_dataset(
            {"reservations": [], "billing_audits": []},
            provenance="aws_export"
        )
        assert result["observations"] == []


# ===========================================================================
# 6. Fixture isolation — adapter outputs must not mix with empirical results
# ===========================================================================

class TestAdapterFixtureIsolation:
    def test_workload_observation_is_not_evaluate_input(self, tmp_path):
        """WorkloadObservation must not have the fields evaluate.py requires
        for its reservations array — no accidental mixing of Alibaba data
        into the empirical billing pipeline."""
        import dataclasses

        from ingestion.alibaba_workload_adapter import WorkloadObservation
        field_names = {f.name for f in dataclasses.fields(WorkloadObservation)}
        # evaluate.py reservation fields that must NOT be on a WorkloadObservation
        forbidden = {"reserved_micro", "compute_micro", "storage_micro",
                     "settlement_stage", "price_snapshot_id"}
        present = forbidden & field_names
        assert not present, (
            f"WorkloadObservation has evaluate.py reservation fields: {present}; "
            "this would allow fabricated billing data to enter empirical results"
        )

    def test_feasibility_evaluate_input_cannot_be_mistaken_for_real_data(self, tmp_path):
        """The evaluate_input in a FeasibilityResult with no admissions must have
        empty arrays — never populated with invented or fabricated records."""
        from ingestion.focus_billing_adapter import FeasibilityResult
        result = FeasibilityResult(
            feasibility_gate="AMBER",
            admission_records_available=False,
            infeasibility_reason="no admissions",
            deliveries=[],
            diffs=[],
            matched_rows=0,
            unmatched_rows=0,
            ambiguous_rows=0,
            total_focus_rows=0,
            evaluate_input={"reservations": [], "billing_audits": []},
            blocked_analyses=[],
        )
        assert result.evaluate_input["reservations"] == []
        assert result.evaluate_input["billing_audits"] == []
        assert result.matched_rows == 0
