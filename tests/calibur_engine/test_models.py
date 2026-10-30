"""Unit tests for the pure CALIBUR contract models.

All values here are local test inputs, not empirical observations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from calibur_engine.models import (
    BUDGET_CLOSED,
    BUDGET_OPEN,
    BudgetState,
    CalibrationSnapshot,
    CurrencyMismatchError,
    Decision,
    EstimateSnapshot,
    FutureInputError,
    InvalidModelError,
    ModelError,
    RequestFeatures,
    ReservationProposal,
    SourceRef,
    TelemetrySnapshot,
    UnsupportedUnitError,
    assert_known_by,
)

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def src(name: str = "record-1", *, observed_at: datetime = T0) -> SourceRef:
    return SourceRef(source_id=name, observed_at=observed_at, source_file="unit-test.json")


@pytest.fixture()
def request_features() -> RequestFeatures:
    return RequestFeatures(
        request_id="req-1",
        team="team-a",
        requested_at=T0,
        instance_type="t3.micro",
        duration_hours=Decimal(2),
        ebs_gb=8,
        source=src("req-1"),
        region="us-east-1",
    )


@pytest.fixture()
def budget_state() -> BudgetState:
    return BudgetState(
        team="team-a",
        period="2026-10",
        cap_micro=10_000_000,
        available_micro=5_000_000,
        reserved_micro=2_000_000,
        spent_micro=3_000_000,
        as_of=T0,
        source=src("ledger-1"),
    )


def test_source_ref_normalises_and_serialises():
    ref = SourceRef(
        source_id=" id-1 ",
        observed_at="2026-10-25T12:00:00Z",
        source_file="",
        semantics="observed",
    )
    assert ref.source_id == "id-1"
    assert ref.observed_at == T0
    assert ref.to_dict()["observed_at"] == "2026-10-25T12:00:00Z"


def test_source_ref_rejects_missing_identity():
    with pytest.raises(InvalidModelError):
        SourceRef(source_id="  ", observed_at=T0)


def test_request_features_rejects_negative_values():
    with pytest.raises(InvalidModelError):
        RequestFeatures(
            request_id="req-1",
            team="team-a",
            requested_at=T0,
            instance_type="t3.micro",
            duration_hours=Decimal(-1),
            ebs_gb=0,
            source=src(),
        )
    with pytest.raises(InvalidModelError):
        RequestFeatures(
            request_id="req-1",
            team="team-a",
            requested_at=T0,
            instance_type="t3.micro",
            duration_hours=Decimal(1),
            ebs_gb=-1,
            source=src(),
        )


def test_request_features_rejects_bool_ebs():
    with pytest.raises(InvalidModelError):
        RequestFeatures(
            request_id="req-1",
            team="team-a",
            requested_at=T0,
            instance_type="t3.micro",
            duration_hours=Decimal(1),
            ebs_gb=True,
            source=src(),
        )


def test_budget_state_normalises_status_and_serialises(budget_state):
    assert budget_state.status == BUDGET_OPEN
    assert budget_state.is_open
    assert budget_state.to_dict()["cap_micro"] == 10_000_000
    assert budget_state.to_dict()["unit"] == "micro_usd"

    closed = BudgetState(
        team="team-a",
        period="2026-10",
        cap_micro=10,
        available_micro=10,
        reserved_micro=0,
        spent_micro=0,
        as_of=T0,
        source=src("ledger"),
        status=BUDGET_CLOSED.lower(),
    )
    assert closed.status == BUDGET_CLOSED
    assert not closed.is_open


def test_budget_state_rejects_broken_ledger_invariant(budget_state):
    with pytest.raises(InvalidModelError, match="invariant"):
        BudgetState(
            team="team-a",
            period="2026-10",
            cap_micro=10_000_000,
            available_micro=5_000_001,
            reserved_micro=2_000_000,
            spent_micro=3_000_000,
            as_of=T0,
            source=src("ledger"),
        )


def test_budget_state_rejects_bad_period_and_status():
    with pytest.raises(InvalidModelError, match="YYYY-MM"):
        BudgetState(
            team="team-a",
            period="2026-13",
            cap_micro=1,
            available_micro=1,
            reserved_micro=0,
            spent_micro=0,
            as_of=T0,
            source=src("ledger"),
        )
    with pytest.raises(InvalidModelError, match="OPEN or CLOSED"):
        BudgetState(
            team="team-a",
            period="2026-10",
            cap_micro=1,
            available_micro=1,
            reserved_micro=0,
            spent_micro=0,
            as_of=T0,
            source=src("ledger"),
            status="FROZEN",
        )


def test_estimate_snapshot_validates_bounds_units_and_provenance():
    estimate = EstimateSnapshot(
        point_cost_micro=1_000,
        upper_cost_micro=1_500,
        method="cumulative_observed_spend",
        as_of=T0,
        source_refs=[src("obs-1"), src("obs-2")],
    )
    assert estimate.to_dict()["upper_cost_micro"] == 1_500

    with pytest.raises(InvalidModelError, match="upper_cost_micro"):
        EstimateSnapshot(
            point_cost_micro=2_000,
            upper_cost_micro=1_500,
            method="cumulative_observed_spend",
            as_of=T0,
            source_refs=[src()],
        )
    with pytest.raises(UnsupportedUnitError):
        EstimateSnapshot(
            point_cost_micro=1,
            upper_cost_micro=None,
            method="m",
            as_of=T0,
            source_refs=[src()],
            currency="EUR",
        )
    with pytest.raises(UnsupportedUnitError):
        EstimateSnapshot(
            point_cost_micro=1,
            upper_cost_micro=None,
            method="m",
            as_of=T0,
            source_refs=[src()],
            unit="usd",
        )
    with pytest.raises(InvalidModelError, match="SourceRef"):
        EstimateSnapshot(
            point_cost_micro=1,
            upper_cost_micro=None,
            method="m",
            as_of=T0,
            source_refs=[],
        )
    with pytest.raises(InvalidModelError, match="integral"):
        EstimateSnapshot(
            point_cost_micro=Decimal("1.5"),
            upper_cost_micro=None,
            method="m",
            as_of=T0,
            source_refs=[src()],
        )


def test_calibration_snapshot_rejects_a_coverage_claim():
    with pytest.raises(InvalidModelError, match="coverage_guarantee"):
        CalibrationSnapshot(
            upper_cost_micro=1_000,
            alpha=Decimal("0.1"),
            score_count=20,
            as_of=T0,
            source_refs=[src()],
            coverage_guarantee=True,  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidModelError, match="alpha"):
        CalibrationSnapshot(
            upper_cost_micro=1_000,
            alpha=Decimal(0),
            score_count=20,
            as_of=T0,
            source_refs=[src()],
        )
    with pytest.raises(InvalidModelError, match="score_count"):
        CalibrationSnapshot(
            upper_cost_micro=1_000,
            alpha=Decimal("0.1"),
            score_count=-1,
            as_of=T0,
            source_refs=[src()],
        )


def test_telemetry_snapshot_keeps_missing_values_missing():
    telemetry = TelemetrySnapshot(job_id="job-1", as_of=T0, source_refs=[src("tel-1")])
    assert telemetry.provisional_cost_micro is None
    assert telemetry.additional_exposure_micro is None
    with pytest.raises(InvalidModelError):
        TelemetrySnapshot(
            job_id="job-1",
            as_of=T0,
            source_refs=[src("tel-1")],
            additional_exposure_micro=-1,
        )


def test_reservation_proposal_requires_reason_codes():
    with pytest.raises(InvalidModelError, match="reason code"):
        ReservationProposal(
            decision=Decision.APPROVE,
            reason_codes=(),
            reservation_amount_micro=1,
            point_cost_estimate_micro=1,
            upper_cost_estimate_micro=1,
            available_budget_before_micro=10,
            available_budget_after_micro=9,
            under_reserve_loss_micro=0,
            over_reserve_loss_micro=0,
            policy_version="calibur-policy/0.1",
            state_version="s",
            decision_at=T0,
            input_refs=[src()],
            audit_reference="audit",
        )


def test_future_input_helper_rejects_later_timestamps():
    assert_known_by(T0, now=T0, field_name="x")
    with pytest.raises(FutureInputError):
        assert_known_by(T0 + timedelta(seconds=1), now=T0, field_name="x")


def test_currency_mismatch_error_is_a_model_error():
    assert issubclass(CurrencyMismatchError, ModelError)
