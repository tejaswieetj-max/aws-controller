"""Unit tests for the asymmetric reservation risk policy.

Every value is a local unit-test input and is never written to research output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from calibur_engine.models import (
    BUDGET_CLOSED,
    BudgetState,
    CalibrationSnapshot,
    EstimateSnapshot,
    FutureInputError,
    InvalidModelError,
    RequestFeatures,
    RiskPolicyConfig,
    SourceRef,
    TelemetrySnapshot,
)
from calibur_engine.risk_policy import (
    REASON_BUDGET_EXACT,
    REASON_BUDGET_INSUFFICIENT,
    REASON_BUDGET_SUFFICIENT,
    REASON_EVIDENCE_STALE,
    REASON_MINIMUM_FLOOR_APPLIED,
    REASON_RESERVE_UPPER_BOUND,
    REASON_TELEMETRY_ADDED,
    REASON_TELEMETRY_STALE,
    REASON_UPPER_BELOW_STATIC_FLOOR,
    decide_reservation,
)

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)


def src(name: str, *, at: datetime | None = None) -> SourceRef:
    return SourceRef(source_id=name, observed_at=at or NOW, source_file="unit-test.json")


def make_request(**overrides) -> RequestFeatures:
    values = {
        "request_id": "req-1",
        "team": "team-a",
        "requested_at": T0,
        "instance_type": "t3.micro",
        "duration_hours": Decimal(2),
        "ebs_gb": 8,
        "source": src("req-1", at=T0),
        "region": "us-east-1",
    }
    values.update(overrides)
    return RequestFeatures(**values)


def make_budget(**overrides) -> BudgetState:
    values = {
        "team": "team-a",
        "period": "2026-10",
        "cap_micro": 10_000_000,
        "available_micro": 5_000_000,
        "reserved_micro": 2_000_000,
        "spent_micro": 3_000_000,
        "as_of": NOW,
        "source": src("ledger-1"),
    }
    values.update(overrides)
    return BudgetState(**values)


def make_estimate(**overrides) -> EstimateSnapshot:
    values = {
        "point_cost_micro": 1_000_000,
        "upper_cost_micro": 1_500_000,
        "method": "cumulative_observed_spend",
        "as_of": NOW,
        "source_refs": [src("obs-1", at=T0)],
    }
    values.update(overrides)
    return EstimateSnapshot(**values)


def decide(**overrides):
    values = {
        "request": make_request(),
        "estimate": make_estimate(),
        "budget": make_budget(),
        "decision_at": NOW,
    }
    values.update(overrides)
    return decide_reservation(**values)


def test_approves_and_reserves_the_upper_bound():
    proposal = decide()
    assert proposal.decision == "APPROVE"
    assert proposal.reservation_amount_micro == 1_500_000
    assert proposal.available_budget_before_micro == 5_000_000
    assert proposal.available_budget_after_micro == 3_500_000
    assert REASON_RESERVE_UPPER_BOUND in proposal.reason_codes
    assert REASON_BUDGET_SUFFICIENT in proposal.reason_codes
    assert proposal.under_reserve_loss_micro == 0
    assert proposal.over_reserve_loss_micro == 500_000
    assert proposal.state_version == "estimate/cumulative_observed_spend"
    assert proposal.currency == "USD"
    assert proposal.unit == "micro_usd"
    assert proposal.limitations


def test_exact_available_budget_is_accepted():
    proposal = decide(budget=make_budget(available_micro=1_500_000, reserved_micro=2_000_000,
                                          spent_micro=6_500_000))
    assert proposal.decision == "APPROVE"
    assert REASON_BUDGET_EXACT in proposal.reason_codes
    assert proposal.available_budget_after_micro == 0


def test_budget_below_reserve_is_denied_without_a_commitment():
    proposal = decide(
        budget=make_budget(available_micro=1_400_000, reserved_micro=2_000_000,
                           spent_micro=6_600_000)
    )
    assert proposal.decision == "DENY"
    assert proposal.reservation_amount_micro == 0
    assert proposal.available_budget_before_micro == 1_400_000
    assert proposal.available_budget_after_micro == 1_400_000
    assert REASON_BUDGET_INSUFFICIENT in proposal.reason_codes
    assert proposal.under_reserve_loss_micro == 0
    assert proposal.over_reserve_loss_micro == 0


def test_heavier_over_reserve_weight_uses_the_static_floor():
    config = RiskPolicyConfig(
        under_reserve_weight=Decimal(1),
        over_reserve_weight=Decimal(4),
    )
    proposal = decide(config=config)
    assert proposal.decision == "APPROVE"
    # ceil(1_000_000 * 1.10)
    assert proposal.reservation_amount_micro == 1_100_000


def test_equal_weights_choose_the_smaller_amount():
    config = RiskPolicyConfig(
        under_reserve_weight=Decimal(1),
        over_reserve_weight=Decimal(1),
    )
    proposal = decide(config=config)
    assert proposal.reservation_amount_micro == 1_100_000


def test_missing_point_estimate_denies():
    proposal = decide(estimate=make_estimate(point_cost_micro=None))
    assert proposal.decision == "DENY"
    assert proposal.reservation_amount_micro == 0
    assert "ESTIMATE_MISSING" in proposal.reason_codes


def test_missing_upper_bound_denies_by_default():
    proposal = decide(estimate=make_estimate(upper_cost_micro=None))
    assert proposal.decision == "DENY"
    assert "UPPER_BOUND_MISSING" in proposal.reason_codes


def test_missing_upper_bound_can_use_the_static_floor_when_configured():
    proposal = decide(
        estimate=make_estimate(upper_cost_micro=None),
        config=RiskPolicyConfig(require_upper_bound=False),
    )
    assert proposal.decision == "APPROVE"
    assert proposal.reservation_amount_micro == 1_100_000


def test_upper_bound_below_static_floor_is_inconsistent():
    proposal = decide(estimate=make_estimate(upper_cost_micro=1_050_000))
    assert proposal.decision == "DENY"
    assert proposal.reservation_amount_micro == 0
    assert REASON_UPPER_BELOW_STATIC_FLOOR in proposal.reason_codes


def test_minimum_reserve_floor_is_applied():
    proposal = decide(
        estimate=make_estimate(point_cost_micro=10_000, upper_cost_micro=500_000),
        config=RiskPolicyConfig(minimum_reserve_micro=600_000),
    )
    assert proposal.decision == "APPROVE"
    assert proposal.reservation_amount_micro == 600_000
    assert REASON_MINIMUM_FLOOR_APPLIED in proposal.reason_codes


def test_stale_evidence_denies():
    later = NOW + timedelta(hours=7)
    proposal = decide(decision_at=later)
    assert proposal.decision == "DENY"
    assert REASON_EVIDENCE_STALE in proposal.reason_codes


def test_closed_budget_denies():
    proposal = decide(budget=make_budget(status=BUDGET_CLOSED))
    assert proposal.decision == "DENY"
    assert "BUDGET_CLOSED" in proposal.reason_codes


def test_team_mismatch_is_rejected():
    with pytest.raises(InvalidModelError, match="team"):
        decide(budget=make_budget(team="team-b"))


def test_future_evidence_is_rejected():
    with pytest.raises(FutureInputError):
        decide(estimate=make_estimate(as_of=NOW + timedelta(seconds=1)))
    with pytest.raises(FutureInputError):
        decide(
            telemetry=TelemetrySnapshot(
                job_id="job-1",
                as_of=NOW + timedelta(seconds=1),
                source_refs=[src("tel-1")],
            )
        )


def test_fresh_telemetry_exposure_is_added_not_interpreted():
    telemetry = TelemetrySnapshot(
        job_id="job-1",
        as_of=NOW,
        source_refs=[src("tel-1")],
        provisional_cost_micro=900_000,
        additional_exposure_micro=200_000,
    )
    proposal = decide(telemetry=telemetry)
    assert proposal.decision == "APPROVE"
    assert proposal.telemetry_exposure_micro == 200_000
    assert proposal.reservation_amount_micro == 1_700_000
    assert REASON_TELEMETRY_ADDED in proposal.reason_codes


def test_stale_telemetry_is_not_used_and_not_treated_as_zero():
    telemetry = TelemetrySnapshot(
        job_id="job-1",
        as_of=NOW - timedelta(hours=7),
        source_refs=[src("tel-1", at=NOW - timedelta(hours=7))],
        additional_exposure_micro=200_000,
    )
    proposal = decide(telemetry=telemetry)
    assert proposal.decision == "DENY"
    assert proposal.telemetry_exposure_micro == 0
    assert REASON_TELEMETRY_STALE in proposal.reason_codes
    assert any("not assumed to be zero" in note for note in proposal.limitations)


def test_calibration_upper_bound_is_used_when_it_is_wider():
    calibration = CalibrationSnapshot(
        upper_cost_micro=2_000_000,
        alpha=Decimal("0.1"),
        score_count=100,
        as_of=NOW,
        source_refs=[src("label-1")],
        state_version="cal-42",
    )
    proposal = decide(calibration=calibration)
    assert proposal.reservation_amount_micro == 2_000_000
    assert proposal.state_version == "cal-42"
    assert "CALIBRATION_USED" in proposal.reason_codes


def test_policy_is_deterministic_and_returns_equal_results():
    first = decide()
    second = decide()
    assert first == second
    assert first.audit_reference == second.audit_reference
    assert first.to_dict() == second.to_dict()
    changed = decide(request=make_request(request_id="req-2"))
    assert changed.audit_reference != first.audit_reference


def test_reason_code_sequence_is_stable():
    proposal = decide()
    assert proposal.reason_codes == (
        "EVIDENCE_FRESH",
        "ESTIMATE_PRESENT",
        "UPPER_BOUND_PRESENT",
        "NO_CALIBRATION",
        "BUDGET_OPEN",
        "RESERVE_UPPER_BOUND",
        "NO_TELEMETRY",
        "BUDGET_SUFFICIENT",
        "POLICY_APPROVED",
    )
