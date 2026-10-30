"""Tests for the strict replay runner's gap handling.

The runner is exercised against the real approved provenance files but writes
its output to a temporary directory.  No metric table is produced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.replay import run

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_verify_sources_matches_recorded_checksums():
    checks = run.verify_sources()
    if not all(check.exists for check in checks):
        pytest.skip("approved raw sources are not present in this checkout")
    assert checks
    assert all(check.checksum_ok for check in checks)
    assert all(check.schema_ok is not False for check in checks)


def test_empty_export_reports_all_evidence_as_missing():
    checks = run.verify_sources()
    evidence = run.assess_evidence(ledger_export=None, source_checks=checks)
    by_key = {item.key: item for item in evidence}
    assert by_key["approved_sources"].present
    assert not by_key["admission_records"].present
    assert not by_key["final_billed_labels"].present
    assert not by_key["first_availability"].present
    assert not by_key["stable_billing_key"].present
    assert not by_key["budget_context"].present


def test_partial_export_only_marks_present_fields():
    export = {
        "reservations": [
            {
                "reservation_id": "res-1",
                "created_at": "2026-10-25T00:00:00Z",
                "resource_id": "i-0123456789abcdef0",
                "budget_period": "2026-10",
                "available_micro": 100,
                "reserved_micro": 50,
            }
        ],
        "billing_audits": [
            {
                "reservation_id": "res-1",
                "new_actual_micro": 60,
            }
        ],
    }
    evidence = run.assess_evidence(ledger_export=export, source_checks=())
    by_key = {item.key: item for item in evidence}
    assert by_key["admission_records"].present
    assert by_key["resource_linkage"].present
    assert by_key["final_billed_labels"].present
    assert by_key["budget_context"].present
    assert not by_key["first_availability"].present
    assert not by_key["stable_billing_key"].present
    assert not by_key["revision_history"].present
    assert not by_key["approved_sources"].present


def test_runner_writes_gap_outputs_and_exits_nonzero(tmp_path):
    exit_code = run.main(
        [
            "--protocol",
            str(REPO_ROOT / "research" / "protocol.md"),
            "--manifest",
            str(REPO_ROOT / "research" / "data_manifest.md"),
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2
    payload = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert payload["status"] == "NOT_RUN_DATA_GAP"
    assert payload["feasibility_gate"] == "AMBER"
    assert payload["metrics"] == {}
    assert payload["protocol"]["sha256"]
    assert payload["manifest"]["sha256"]
    assert any(not item["present"] for item in payload["evidence"])
    report = (tmp_path / "NOT_RUN_DATA_GAP.md").read_text(encoding="utf-8")
    assert "NOT_RUN_DATA_GAP" in report
    assert "No comparative evaluation was run" in report
    for name in (
        "decision_metrics.csv",
        "data_quality.csv",
        "eligible_population.csv",
        "policy_comparison.csv",
    ):
        assert not (tmp_path / name).exists()


def test_runner_rejects_a_missing_protocol(tmp_path):
    exit_code = run.main(
        [
            "--protocol",
            str(tmp_path / "missing.md"),
            "--manifest",
            str(REPO_ROOT / "research" / "data_manifest.md"),
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2
    assert not (tmp_path / "run_manifest.json").exists()


def test_runner_rejects_a_missing_ledger_export(tmp_path):
    exit_code = run.main(
        [
            "--protocol",
            str(REPO_ROOT / "research" / "protocol.md"),
            "--manifest",
            str(REPO_ROOT / "research" / "data_manifest.md"),
            "--ledger-export",
            str(tmp_path / "missing.json"),
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2


def test_gap_report_never_contains_metric_values(tmp_path):
    checks = run.verify_sources()
    evidence = run.assess_evidence(ledger_export=None, source_checks=checks)
    from datetime import UTC, datetime

    report = run.render_gap_report(
        evidence=evidence,
        source_checks=checks,
        protocol_path="research/protocol.md",
        manifest_path="research/data_manifest.md",
        now=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
        ledger_export_path=None,
    )
    assert "BOF =" not in report
    assert "ABR =" not in report
    assert "No metric table was written" in report
