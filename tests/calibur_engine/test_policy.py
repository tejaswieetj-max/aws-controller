"""Unit tests for the public compute_reservation boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from calibur_engine.models import (
    BudgetState,
    EstimateSnapshot,
    FutureInputError,
    InvalidModelError,
    RequestFeatures,
    SourceRef,
)
from calibur_engine.policy import compute_reservation

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def src(name: str) -> SourceRef:
    return SourceRef(source_id=name, observed_at=T0, source_file="unit-test.json")


def request(team: str = "team-a", *, requested_at: datetime = T0) -> RequestFeatures:
    return RequestFeatures(
        request_id="req-1",
        team=team,
        requested_at=requested_at,
        instance_type="t3.micro",
        duration_hours=Decimal(2),
        ebs_gb=8,
        source=src("req-1"),
    )


def budget(*, team: str = "team-a", period: str = "2026-10") -> BudgetState:
    return BudgetState(
        team=team,
        period=period,
        cap_micro=10_000_000,
        available_micro=5_000_000,
        reserved_micro=2_000_000,
        spent_micro=3_000_000,
        as_of=T0,
        source=src("ledger-1"),
    )


def estimate(**overrides) -> EstimateSnapshot:
    values = {
        "point_cost_micro": 1_000_000,
        "upper_cost_micro": 1_500_000,
        "method": "cumulative_observed_spend",
        "as_of": T0,
        "source_refs": [src("obs-1")],
    }
    values.update(overrides)
    return EstimateSnapshot(**values)


def test_compute_reservation_returns_a_proposal():
    proposal = compute_reservation(request(), estimate(), budget(), decision_at=T0)
    assert proposal.decision == "APPROVE"
    assert proposal.reservation_amount_micro == 1_500_000
    assert proposal.policy_version == "calibur-policy/0.1"


def test_compute_reservation_accepts_iso_decision_time():
    proposal = compute_reservation(
        request(),
        estimate(),
        budget(),
        decision_at="2026-10-25T12:00:00Z",
    )
    assert proposal.decision == "APPROVE"


def test_budget_period_must_match_the_decision_month():
    with pytest.raises(InvalidModelError, match="budget period"):
        compute_reservation(
            request(),
            estimate(),
            budget(period="2026-09"),
            decision_at=T0,
        )


def test_team_mismatch_is_rejected():
    with pytest.raises(InvalidModelError, match="team"):
        compute_reservation(request(team="team-b"), estimate(), budget(), decision_at=T0)


def test_future_request_is_rejected():
    with pytest.raises(FutureInputError):
        compute_reservation(
            request(requested_at=T0.replace(hour=13)),
            estimate(),
            budget(),
            decision_at=T0,
        )


def test_naive_decision_time_is_rejected():
    with pytest.raises(InvalidModelError, match="timezone-aware"):
        compute_reservation(
            request(),
            estimate(),
            budget(),
            decision_at=datetime(2026, 10, 25, 12, 0),
        )
