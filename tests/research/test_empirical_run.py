"""Tests for research/empirical_run.py.

These tests verify the pipeline wiring: FOCUS adapter → evaluate.py,
the correct AMBER stop behaviour, and output file structure.

No AWS access. No real data/raw/ files are required; monkeypatching
substitutes the real data loaders with controlled in-process fixtures.
All fixture values are labelled SYNTHETIC_FIXTURE and must never be
presented as empirical results.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = REPO_ROOT / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))
if str(REPO_ROOT / "services") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "services"))


# ---------------------------------------------------------------------------
# Minimal FeasibilityResult fixture
# ---------------------------------------------------------------------------

def _make_feasibility_dict(
    *,
    admission_records_available: bool = False,
    total_focus_rows: int = 5,
    matched_rows: int = 0,
    unmatched_rows: int = 5,
) -> dict:
    return {
        "feasibility_gate": "AMBER",
        "admission_records_available": admission_records_available,
        "infeasibility_reason": (
            None if admission_records_available else "No CALIBUR admissions exist."
        ),
        "deliveries": [],
        "diffs": [],
        "matched_rows": matched_rows,
        "unmatched_rows": unmatched_rows,
        "ambiguous_rows": 0,
        "total_focus_rows": total_focus_rows,
        "evaluate_input": {"reservations": [], "billing_audits": []},
        "blocked_analyses": ["test"],
        "notes": ["fixture"],
    }


# ===========================================================================
# 1. _run_evaluate with empty input produces zero observations
# ===========================================================================

class TestRunEvaluateEmpty:
    def test_empty_input_zero_observations(self):
        from empirical_run import _run_evaluate
        result = _run_evaluate({"reservations": [], "billing_audits": []})
        assert result["dataset"]["observation_count"] == 0

    def test_empty_input_correct_classification(self):
        from empirical_run import _run_evaluate
        result = _run_evaluate({"reservations": [], "billing_audits": []})
        assert result["dataset"]["classification"] == "AWS_EXPORT_ASSERTED_UNVERIFIED"

    def test_empty_input_no_invented_observations(self):
        from empirical_run import _run_evaluate
        result = _run_evaluate({"reservations": [], "billing_audits": []})
        assert result["dataset"]["observations"] == []

    def test_empty_input_metrics_are_zero_observations(self):
        from empirical_run import _run_evaluate
        result = _run_evaluate({"reservations": [], "billing_audits": []})
        assert result["metrics"]["observation_count"] == 0

    def test_empty_input_margin_evaluation_returns_candidates(self):
        from empirical_run import _run_evaluate
        result = _run_evaluate({"reservations": [], "billing_audits": []})
        assert len(result["margin_evaluation"]["candidate_margins"]) > 0


# ===========================================================================
# 2. _write_json output format
# ===========================================================================

class TestWriteJson:
    def test_writes_valid_json(self, tmp_path):
        from empirical_run import _write_json
        p = tmp_path / "test.json"
        _write_json(p, {"key": "value", "number": 42})
        parsed = json.loads(p.read_text())
        assert parsed["key"] == "value"
        assert parsed["number"] == 42

    def test_handles_decimal_values(self, tmp_path):
        from empirical_run import _write_json
        from decimal import Decimal
        p = tmp_path / "test.json"
        _write_json(p, {"amount": Decimal("1.10")})
        parsed = json.loads(p.read_text())
        assert parsed["amount"] == "1.10"

    def test_file_is_newline_terminated(self, tmp_path):
        from empirical_run import _write_json
        p = tmp_path / "test.json"
        _write_json(p, {})
        assert p.read_text(encoding="utf-8").endswith("\n")


# ===========================================================================
# 3. main() — output files written correctly
# ===========================================================================

class TestMainOutputFiles:
    """Monkeypatch build_feasibility_result to avoid loading real data."""

    def _run_main(self, tmp_path, *, extra_args: list[str] | None = None) -> None:
        """Run empirical_run.main() with a monkeypatched feasibility result."""
        import dataclasses
        import ingestion.focus_billing_adapter as fba
        import empirical_run as er

        # Build a minimal FeasibilityResult dataclass
        feasibility = fba.FeasibilityResult(
            feasibility_gate="AMBER",
            admission_records_available=False,
            infeasibility_reason="test: no admissions",
            deliveries=[],
            diffs=[],
            matched_rows=0,
            unmatched_rows=0,
            ambiguous_rows=0,
            total_focus_rows=0,
            evaluate_input={"reservations": [], "billing_audits": []},
            blocked_analyses=["test"],
            notes=["test"],
        )

        original_bfr = fba.build_feasibility_result

        def fake_bfr(**kwargs):
            return feasibility

        fba.build_feasibility_result = fake_bfr
        try:
            orig_argv = sys.argv
            sys.argv = ["empirical_run.py", "--output-dir", str(tmp_path), "--skip-alibaba"]
            if extra_args:
                sys.argv += extra_args
            with pytest.raises(SystemExit) as exc_info:
                er.main()
            assert exc_info.value.code == 0
        finally:
            fba.build_feasibility_result = original_bfr
            sys.argv = orig_argv

    def test_feasibility_json_written(self, tmp_path):
        self._run_main(tmp_path)
        assert (tmp_path / "empirical_feasibility.json").is_file()

    def test_evaluate_input_json_written(self, tmp_path):
        self._run_main(tmp_path)
        assert (tmp_path / "empirical_evaluate_input.json").is_file()

    def test_evaluate_result_json_written(self, tmp_path):
        self._run_main(tmp_path)
        assert (tmp_path / "empirical_evaluate_result.json").is_file()

    def test_evaluate_result_has_zero_observations(self, tmp_path):
        self._run_main(tmp_path)
        result = json.loads((tmp_path / "empirical_evaluate_result.json").read_text())
        assert result["dataset"]["observation_count"] == 0

    def test_feasibility_json_is_valid_json(self, tmp_path):
        self._run_main(tmp_path)
        parsed = json.loads((tmp_path / "empirical_feasibility.json").read_text())
        assert "feasibility_gate" in parsed
        assert parsed["feasibility_gate"] == "AMBER"

    def test_evaluate_input_is_empty_arrays(self, tmp_path):
        self._run_main(tmp_path)
        parsed = json.loads((tmp_path / "empirical_evaluate_input.json").read_text())
        assert parsed == {"reservations": [], "billing_audits": []}

    def test_no_workload_stream_file_when_alibaba_skipped(self, tmp_path):
        self._run_main(tmp_path)
        assert not (tmp_path / "workload_stream.json").is_file()

    def test_data_infeasibility_file_written_when_amber(self, tmp_path):
        self._run_main(tmp_path)
        assert (tmp_path / "empirical_data_infeasibility.json").is_file()

    def test_data_infeasibility_has_correct_result_key(self, tmp_path):
        self._run_main(tmp_path)
        parsed = json.loads((tmp_path / "empirical_data_infeasibility.json").read_text())
        assert parsed["result"] == "DATA_INFEASIBILITY"
        assert len(parsed["missing_evidence"]) > 0
        assert parsed["next_action"]


# ===========================================================================
# 4. Ledger export integration in main()
# ===========================================================================

class TestLedgerExportIntegration:
    def test_ledger_export_empty_still_zero_observations(self, tmp_path):
        """Even with a real ledger export file, empty arrays → 0 observations."""
        import ingestion.focus_billing_adapter as fba
        import empirical_run as er

        # Write a minimal ledger export
        export_file = tmp_path / "export.json"
        export_file.write_text(json.dumps({"reservations": [], "billing_audits": []}))

        feasibility = fba.FeasibilityResult(
            feasibility_gate="AMBER",
            admission_records_available=False,
            infeasibility_reason="test",
            deliveries=[],
            diffs=[],
            matched_rows=0,
            unmatched_rows=0,
            ambiguous_rows=0,
            total_focus_rows=0,
            evaluate_input={"reservations": [], "billing_audits": []},
            blocked_analyses=[],
            notes=[],
        )

        original_bfr = fba.build_feasibility_result

        def fake_bfr(**kwargs):
            return feasibility

        fba.build_feasibility_result = fake_bfr
        try:
            orig_argv = sys.argv
            sys.argv = [
                "empirical_run.py",
                "--output-dir", str(tmp_path / "out"),
                "--ledger-export", str(export_file),
                "--skip-alibaba",
            ]
            with pytest.raises(SystemExit) as exc_info:
                er.main()
            assert exc_info.value.code == 0
        finally:
            fba.build_feasibility_result = original_bfr
            sys.argv = orig_argv

        result = json.loads((tmp_path / "out" / "empirical_evaluate_result.json").read_text())
        assert result["dataset"]["observation_count"] == 0

    def test_ledger_export_nonexistent_raises(self, tmp_path):
        """A missing ledger export file must raise FileNotFoundError inside main()."""
        from ingestion.ledger_export_reader import translate
        with pytest.raises(FileNotFoundError):
            translate(tmp_path / "nonexistent.json")


# ===========================================================================
# 5. Pipeline isolation — Alibaba data must not reach evaluate_input
# ===========================================================================

class TestStatusMode:
    """--status flag prints a readiness report and exits 0 without loading data."""

    def test_status_flag_exits_zero(self, tmp_path):
        import empirical_run as er
        orig_argv = sys.argv
        sys.argv = [
            "empirical_run.py",
            "--output-dir", str(tmp_path),
            "--status",
        ]
        try:
            with pytest.raises(SystemExit) as exc_info:
                er.main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = orig_argv

    def test_status_flag_writes_no_output_files(self, tmp_path):
        import empirical_run as er
        orig_argv = sys.argv
        sys.argv = [
            "empirical_run.py",
            "--output-dir", str(tmp_path),
            "--status",
        ]
        try:
            with pytest.raises(SystemExit):
                er.main()
        finally:
            sys.argv = orig_argv
        # No output files should be written by --status
        assert not (tmp_path / "empirical_feasibility.json").is_file()
        assert not (tmp_path / "empirical_evaluate_result.json").is_file()

    def test_status_report_mentions_data_infeasibility(self, tmp_path, capsys):
        import empirical_run as er
        orig_argv = sys.argv
        sys.argv = [
            "empirical_run.py",
            "--output-dir", str(tmp_path),
            "--status",
        ]
        try:
            with pytest.raises(SystemExit):
                er.main()
        finally:
            sys.argv = orig_argv
        captured = capsys.readouterr()
        assert "DATA_INFEASIBILITY" in captured.out
        assert "DynamoDB" in captured.out


class TestAlibabaPipelineIsolation:
    def test_alibaba_workload_not_in_evaluate_input(self, tmp_path):
        """The evaluate_input written by main() must have no Alibaba fields."""
        import ingestion.focus_billing_adapter as fba
        import empirical_run as er

        feasibility = fba.FeasibilityResult(
            feasibility_gate="AMBER",
            admission_records_available=False,
            infeasibility_reason="test",
            deliveries=[],
            diffs=[],
            matched_rows=0,
            unmatched_rows=0,
            ambiguous_rows=0,
            total_focus_rows=0,
            evaluate_input={"reservations": [], "billing_audits": []},
            blocked_analyses=[],
            notes=[],
        )

        original_bfr = fba.build_feasibility_result

        def fake_bfr(**kwargs):
            return feasibility

        fba.build_feasibility_result = fake_bfr
        try:
            orig_argv = sys.argv
            sys.argv = ["empirical_run.py", "--output-dir", str(tmp_path), "--skip-alibaba"]
            with pytest.raises(SystemExit):
                er.main()
        finally:
            fba.build_feasibility_result = original_bfr
            sys.argv = orig_argv

        ei = json.loads((tmp_path / "empirical_evaluate_input.json").read_text())
        # evaluate_input must only have reservations and billing_audits
        assert set(ei.keys()) == {"reservations", "billing_audits"}
        # Must not contain any Alibaba-origin fields
        for res in ei["reservations"]:
            for key in res:
                assert "alibaba" not in key.lower()
                assert "trace" not in key.lower()
                assert "job_id" not in key.lower()
                assert "task_id" not in key.lower()
