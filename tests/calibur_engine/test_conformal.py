"""Unit tests for the online conformal calibration module.

All fixtures are local and deterministic. They are label-*shaped* test inputs,
not claims of real AWS observations; the module itself never generates data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from calibur_engine.conformal import (
    CALIBRATION_LIMITATIONS,
    COVERAGE_STATEMENT,
    AmbiguousRevisionError,
    ConflictingEarlyWarningError,
    ConflictingLabelError,
    ConformalCalibrator,
    ConformalError,
    ConformalMixedUnitError,
    DuplicatePredictionError,
    EarlyWarning,
    FinalLabel,
    InvalidEarlyWarningError,
    InvalidLabelError,
    InvalidTimestampError,
    MissingFinalCostError,
    MixedUnitError,
    Prediction,
    ProvisionalLabelError,
    RevisionLineageError,
    StaleLabelError,
    UnknownPredictionError,
    calibrate,
    nearest_rank_quantile,
    residual,
    update_alpha,
)

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def prediction(
    prediction_id: str,
    cost: int | Decimal | None,
    *,
    minutes: int = 0,
    currency: str = "USD",
    unit: str = "micro_usd",
) -> Prediction:
    return Prediction(
        prediction_id=prediction_id,
        prediction_timestamp=at(minutes),
        predicted_cost_micro=cost,
        currency=currency,
        unit=unit,
    )


def label(
    prediction_id: str,
    cost: int | Decimal | None,
    source: str,
    *,
    minutes: int = 0,
    first_seen: int | None = None,
    currency: str = "USD",
    unit: str = "micro_usd",
    **kwargs,
) -> FinalLabel:
    return FinalLabel(
        prediction_id=prediction_id,
        final_cost_micro=cost,
        observed_at=at(minutes),
        label_source_id=source,
        first_seen_at=at(minutes if first_seen is None else first_seen),
        currency=currency,
        unit=unit,
        **kwargs,
    )


def warning(
    prediction_id: str,
    event_id: str,
    cost: int,
    *,
    minutes: int = 0,
    reserved: int | None = None,
    currency: str = "USD",
    unit: str = "micro_usd",
) -> EarlyWarning:
    return EarlyWarning(
        prediction_id=prediction_id,
        event_id=event_id,
        observed_at=at(minutes),
        provisional_cost_micro=cost,
        reserved_micro=reserved,
        currency=currency,
        unit=unit,
    )


# ---------------------------------------------------------------------------
# Update rule and arithmetic
# ---------------------------------------------------------------------------


def test_update_alpha_direction_and_magnitude():
    covered = update_alpha("0.05", target_alpha="0.05", gamma="0.02", miss=0)
    missed = update_alpha("0.05", target_alpha="0.05", gamma="0.02", miss=1)
    assert covered == Decimal("0.051")
    assert missed == Decimal("0.031")
    assert covered > Decimal("0.05")
    assert missed < Decimal("0.05")
    # ACI nudges are unconditional: a cover adds gamma*target, a miss
    # subtracts gamma*(1-target), even when alpha is above the target.
    assert update_alpha("0.10", target_alpha="0.05", gamma="0.02", miss=0) == Decimal("0.101")
    assert update_alpha("0.10", target_alpha="0.05", gamma="0.02", miss=1) == Decimal("0.081")


def test_update_alpha_clips_to_bounds():
    assert update_alpha("0.001", target_alpha="0.05", gamma="0.02", miss=1) == Decimal("0.001")
    assert update_alpha("0.5", target_alpha="0.05", gamma="0.02", miss=0) == Decimal("0.5")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"miss": 2}, "miss must be 0 or 1"),
        ({"miss": True}, "miss must be 0 or 1"),
        ({"gamma": "0"}, "gamma must be"),
        ({"gamma": "0.51"}, "gamma must be"),
        ({"target_alpha": "0.6"}, "target_alpha must lie"),
        ({"alpha": "0.0005"}, "alpha must lie"),
        ({"alpha_min": "0.3", "alpha_max": "0.2"}, "alpha bounds"),
    ],
)
def test_update_alpha_rejects_invalid_parameters(kwargs, match):
    call = {"alpha": "0.05", "target_alpha": "0.05", "gamma": "0.02", "miss": 0}
    call.update(kwargs)
    with pytest.raises(ConformalError, match=match):
        update_alpha(**call)


def test_nearest_rank_quantile_arithmetic():
    assert nearest_rank_quantile([], "0.95") is None
    assert nearest_rank_quantile([7], "0.95") == 7
    assert nearest_rank_quantile([1, 2, 3, 4], "0.5") == 2
    assert nearest_rank_quantile([1, 2, 3, 4], "0.75") == 3
    assert nearest_rank_quantile(range(1, 21), "0.95") == 19
    assert nearest_rank_quantile([1, 2, 3], "1") == 3
    with pytest.raises(ConformalError, match="level"):
        nearest_rank_quantile([1], "0")
    with pytest.raises(ConformalError, match="level"):
        nearest_rank_quantile([1], "1.1")


def test_residual_is_exact_and_preserves_sign():
    assert residual(75, 100) == -25
    assert residual(125, 100) == 25
    assert residual(Decimal("0.3"), Decimal("0.1")) == Decimal("0.2")
    assert residual(10**18 + 3, 10**18) == 3
    assert residual(10, 10) == 0
    assert isinstance(residual(10**18 + 3, 10**18), int)


def test_residual_rejects_missing_values():
    with pytest.raises(MissingFinalCostError):
        residual(None, 1)
    with pytest.raises(MissingFinalCostError):
        residual(1, None)


# ---------------------------------------------------------------------------
# Basic state and bounds
# ---------------------------------------------------------------------------


def test_first_label_seeds_state_and_bound():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    update = calibrator.add_final_label(label("p1", 120, "l1", minutes=1))

    assert update.applied is True
    assert update.reason == "new"
    state = calibrator.state
    assert state.score_count == 1
    assert state.current_label_count == 1
    assert state.label_version_count == 1
    assert state.revision_count == 0
    assert state.alpha == Decimal("0.051")
    assert state.threshold_micro == 20
    assert state.miss_count == 0
    assert state.covered_count == 1

    bound = calibrator.bound_for("p1")
    assert bound.status == "OK"
    assert bound.is_sufficient is True
    assert bound.upper_bound_micro == 120
    assert bound.alpha == Decimal("0.051")
    assert bound.quantile_level == Decimal("0.949")
    assert bound.currency == "USD"
    assert bound.unit == "micro_usd"


def test_miss_decreases_alpha_and_widens_bound():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_prediction(prediction("p2", 100))
    calibrator.add_prediction(prediction("p3", 100))
    calibrator.add_final_label(label("p1", 120, "l1", minutes=1))
    calibrator.add_final_label(label("p2", 130, "l2", minutes=2))

    second = calibrator.state.score_observations[1]
    assert second.threshold_micro == 20
    assert second.miss is True
    assert second.alpha_before == Decimal("0.051")
    assert second.alpha_after == Decimal("0.032")
    assert calibrator.state.miss_count == 1
    assert calibrator.state.covered_count == 1

    bound = calibrator.bound_for("p3")
    assert bound.quantile_level == Decimal("0.968")
    assert bound.upper_bound_micro == 130


def test_cover_increases_alpha():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_prediction(prediction("p2", 100))
    calibrator.add_final_label(label("p1", 120, "l1", minutes=1))
    calibrator.add_final_label(label("p2", 110, "l2", minutes=2))

    second = calibrator.state.score_observations[1]
    assert second.threshold_micro == 20
    assert second.miss is False
    assert second.alpha_after == Decimal("0.052")


def test_bound_statuses_and_require_bound():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))

    empty = calibrator.bound_for("p1")
    assert empty.status == "INSUFFICIENT_DATA"
    assert empty.reason == "no_final_labels"
    assert empty.upper_bound_micro is None
    with pytest.raises(ConformalError, match="insufficient data"):
        empty.require_bound()

    calibrator.add_prediction(prediction("no-cost", None))
    missing = calibrator.bound_for("no-cost")
    assert missing.reason == "missing_predicted_cost"

    calibrator.add_final_label(label("p1", 120, "l1", minutes=1))
    strict = ConformalCalibrator(min_scores=2)
    strict.add_prediction(prediction("p1", 100))
    strict.add_final_label(label("p1", 120, "l1", minutes=1))
    below = strict.bound_for("p1")
    assert below.reason == "below_min_scores"
    assert below.score_count == 1


def test_pending_prediction_is_reported_but_does_not_block_a_bound():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("settled", 100))
    calibrator.add_prediction(prediction("pending", 100))
    calibrator.add_final_label(label("settled", 120, "l1", minutes=1))

    assert calibrator.state.pending_prediction_count == 1
    assert calibrator.bound_for("pending").upper_bound_micro == 120


def test_unscorable_prediction_is_reported():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("no-cost", None))

    assert calibrator.add_final_label(label("no-cost", 50, "l1", minutes=1)).applied
    state = calibrator.state
    assert state.pending_prediction_count == 0
    assert state.unscorable_prediction_count == 1
    assert state.score_count == 0
    assert calibrator.bound_for("no-cost").reason == "missing_predicted_cost"


def test_alpha_clips_at_upper_bound_over_many_covers():
    calibrator = ConformalCalibrator()
    for index in range(600):
        calibrator.add_prediction(prediction(f"p{index}", 100))
        calibrator.add_final_label(label(f"p{index}", 100, f"l{index}", minutes=index))
    assert calibrator.state.alpha == Decimal("0.5")
    assert calibrator.state.alpha <= Decimal("0.5")


# ---------------------------------------------------------------------------
# Revisions and out-of-order labels
# ---------------------------------------------------------------------------


def test_revision_replaces_score_and_does_not_add_a_job():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "v1", minutes=1))
    updated = calibrator.add_final_label(
        label("p1", 90, "v2", minutes=2, revision_of="v1", report_version_id="D2")
    )

    assert updated.applied is True
    assert updated.reason == "revision"
    assert updated.replaced_label_source_id == "v1"
    state = calibrator.state
    assert state.current_label_count == 1
    assert state.label_version_count == 2
    assert state.revision_count == 1
    assert state.score_count == 1
    assert state.score_observations[0].score_micro == -10
    assert calibrator.revision_lineage("p1") == ("v1", "v2")
    assert [item.final_cost_micro for item in calibrator.label_history("p1")] == [120, 90]
    assert calibrator.bound_for("p1").upper_bound_micro == 90


def test_explicit_fork_is_rejected():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "v1", minutes=1))
    calibrator.add_final_label(label("p1", 90, "v2", minutes=2, revision_of="v1"))
    with pytest.raises(RevisionLineageError, match="fork"):
        calibrator.add_final_label(label("p1", 80, "v3", minutes=3, revision_of="v1"))


def test_unknown_and_cross_job_revision_are_rejected():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_prediction(prediction("p2", 100))
    calibrator.add_final_label(label("p1", 120, "v1", minutes=1))
    with pytest.raises(RevisionLineageError, match="not a known"):
        calibrator.add_final_label(label("p1", 90, "v2", minutes=2, revision_of="missing"))
    with pytest.raises(RevisionLineageError, match="cross prediction"):
        calibrator.add_final_label(label("p2", 90, "v2", minutes=2, revision_of="v1"))


def test_self_revision_is_rejected_at_construction():
    with pytest.raises(RevisionLineageError, match="cannot revise itself"):
        label("p1", 100, "v1", minutes=1, revision_of="v1")


def test_stale_label_is_rejected():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "v2", minutes=2))
    with pytest.raises(StaleLabelError, match="older"):
        calibrator.add_final_label(label("p1", 80, "v1", minutes=1))


def test_same_first_seen_without_order_is_ambiguous():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "v1", minutes=1))
    with pytest.raises(AmbiguousRevisionError, match="same first_seen_at"):
        calibrator.add_final_label(label("p1", 90, "v2", minutes=1))


def test_same_first_seen_is_resolved_by_report_version_order():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "v1", minutes=1, report_version_order="1"))
    update = calibrator.add_final_label(label("p1", 90, "v2", minutes=1, report_version_order="2"))
    assert update.applied is True
    assert update.reason == "revision"
    assert calibrator.state.revision_count == 1


def test_out_of_order_newer_revision_with_report_order_is_accepted():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "v1", minutes=2, report_version_order="1"))
    update = calibrator.add_final_label(label("p1", 90, "v2", minutes=1, report_version_order="2"))
    assert update.applied is True
    assert update.reason == "out_of_order_revision"
    assert calibrator.revision_lineage("p1") == ("v1", "v2")
    assert calibrator.state.score_observations[0].score_micro == -10


def test_implicit_revision_records_lineage():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "v1", minutes=1))
    calibrator.add_final_label(label("p1", 90, "v2", minutes=2))
    assert calibrator.revision_lineage("p1") == ("v1", "v2")
    assert calibrator.state.revision_count == 1


def test_duplicate_label_is_idempotent_and_conflict_is_rejected():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    first = label("p1", 120, "v1", minutes=1)
    calibrator.add_final_label(first)
    duplicate = calibrator.add_final_label(first)
    assert duplicate.applied is False
    assert duplicate.reason == "duplicate"
    assert calibrator.state.label_version_count == 1
    assert calibrator.state.score_count == 1

    conflicting = label("p1", 90, "v1", minutes=1)
    with pytest.raises(ConflictingLabelError, match="different content"):
        calibrator.add_final_label(conflicting)


def test_arrival_order_does_not_change_replay():
    predictions = [
        prediction("p1", 100, minutes=1),
        prediction("p2", 100, minutes=2),
        prediction("p3", 100, minutes=3),
    ]
    labels = [
        label("p1", 120, "l1", minutes=11, first_seen=11),
        label("p2", 80, "l2", minutes=12, first_seen=12),
        label("p3", 150, "l3", minutes=13, first_seen=13),
    ]
    forward = ConformalCalibrator()
    backward = ConformalCalibrator()
    for item in predictions:
        forward.add_prediction(item)
        backward.add_prediction(item)
    for item in labels:
        forward.add_final_label(item)
    for item in reversed(labels):
        backward.add_final_label(item)

    assert forward.alpha == backward.alpha
    assert forward.state.score_observations == backward.state.score_observations
    assert [item.prediction_id for item in forward.state.score_observations] == [
        "p1",
        "p2",
        "p3",
    ]


def test_replay_orders_by_first_seen_then_prediction_timestamp_then_id():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("later", 100, minutes=5))
    calibrator.add_prediction(prediction("earlier", 100, minutes=1))
    calibrator.add_final_label(label("later", 110, "l1", minutes=9, first_seen=2))
    calibrator.add_final_label(label("earlier", 120, "l2", minutes=8, first_seen=2))

    assert [item.prediction_id for item in calibrator.state.score_observations] == [
        "earlier",
        "later",
    ]


def test_observed_at_may_be_later_than_first_seen():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_prediction(prediction("p2", 100))
    calibrator.add_final_label(label("p1", 120, "l1", minutes=10, first_seen=2))
    calibrator.add_final_label(label("p2", 110, "l2", minutes=3, first_seen=3))

    observations = calibrator.state.score_observations
    assert [item.prediction_id for item in observations] == ["p1", "p2"]
    assert observations[0].first_seen_at == at(2)


# ---------------------------------------------------------------------------
# Missing labels, duplicates, and validation
# ---------------------------------------------------------------------------


def test_unknown_prediction_label_is_rejected():
    calibrator = ConformalCalibrator()
    with pytest.raises(UnknownPredictionError, match="unknown prediction"):
        calibrator.add_final_label(label("missing", 100, "l1", minutes=1))


def test_prediction_re_registration_requires_identical_content():
    calibrator = ConformalCalibrator()
    item = prediction("p1", 100)
    assert calibrator.add_prediction(item) is True
    assert calibrator.add_prediction(item) is False
    with pytest.raises(DuplicatePredictionError, match="different content"):
        calibrator.add_prediction(prediction("p1", 101))


def test_label_requires_an_observed_final_cost():
    with pytest.raises(MissingFinalCostError):
        label("p1", None, "l1", minutes=1)


def test_first_seen_cannot_be_after_observed():
    with pytest.raises(InvalidLabelError, match="first_seen_at cannot be after"):
        label("p1", 100, "l1", minutes=1, first_seen=2)


def test_provisional_record_cannot_be_a_final_label():
    with pytest.raises(ProvisionalLabelError, match="provisional telemetry"):
        label("p1", 100, "l1", minutes=1, is_final=False)


def test_invalid_timestamps_are_rejected():
    with pytest.raises(InvalidTimestampError):
        Prediction(
            prediction_id="p1",
            prediction_timestamp="not-a-timestamp",
            predicted_cost_micro=1,
        )
    with pytest.raises(InvalidTimestampError):
        FinalLabel(
            prediction_id="p1",
            final_cost_micro=1,
            observed_at=object(),
            label_source_id="l1",
        )


# ---------------------------------------------------------------------------
# Provisional telemetry separation
# ---------------------------------------------------------------------------


def test_early_warning_does_not_change_conformal_state_or_bound():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_prediction(prediction("p2", 100))
    calibrator.add_final_label(label("p1", 120, "l1", minutes=1))
    before_state = calibrator.state
    before_bound = calibrator.bound_for("p2")

    added = calibrator.record_early_warning(warning("p2", "w1", 150, minutes=2, reserved=100))

    assert added is True
    assert calibrator.early_warnings[0].breach is True
    after_state = calibrator.state
    assert after_state.alpha == before_state.alpha
    assert after_state.score_count == before_state.score_count
    assert after_state.miss_count == before_state.miss_count
    assert after_state.threshold_micro == before_state.threshold_micro
    assert after_state.early_warning_count == 1
    assert calibrator.bound_for("p2") == before_bound


def test_early_warning_duplicate_and_conflict():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    first = warning("p1", "w1", 150, minutes=2, reserved=100)
    assert calibrator.record_early_warning(first) is True
    assert calibrator.record_early_warning(first) is False
    with pytest.raises(ConflictingEarlyWarningError, match="different content"):
        calibrator.record_early_warning(warning("p1", "w1", 160, minutes=2, reserved=100))
    assert calibrator.state.early_warning_count == 1


def test_early_warning_unknown_prediction_is_rejected():
    calibrator = ConformalCalibrator()
    with pytest.raises(UnknownPredictionError, match="unknown prediction"):
        calibrator.record_early_warning(warning("missing", "w1", 100, minutes=1))


def test_early_warning_without_reservation_has_unknown_breach():
    event = warning("p1", "w1", 100, minutes=1)
    assert event.breach is None
    assert event.provisional_cost_micro == 100


def test_early_warning_unit_must_match_prediction():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    with pytest.raises(MixedUnitError):
        calibrator.record_early_warning(warning("p1", "w1", 100, minutes=1, currency="EUR"))


def test_invalid_early_warning_timestamp_order_is_rejected():
    with pytest.raises(InvalidEarlyWarningError, match="first_seen_at cannot be after"):
        EarlyWarning(
            prediction_id="p1",
            event_id="w1",
            observed_at=at(1),
            provisional_cost_micro=100,
            first_seen_at=at(2),
        )


# ---------------------------------------------------------------------------
# Units, currencies, and timestamps
# ---------------------------------------------------------------------------


def test_mixed_currency_or_unit_is_rejected():
    assert ConformalMixedUnitError is MixedUnitError
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100, currency="USD"))
    with pytest.raises(MixedUnitError, match="cannot add EUR"):
        calibrator.add_prediction(prediction("p2", 100, currency="EUR"))

    units = ConformalCalibrator()
    units.add_prediction(prediction("p1", 100, unit="micro_usd"))
    with pytest.raises(MixedUnitError, match="cannot add"):
        units.add_prediction(prediction("p2", 100, unit="USD"))


def test_label_units_and_currency_are_preserved():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", Decimal("1.5"), currency="eur", unit="USD"))
    calibrator.add_final_label(
        label("p1", Decimal("2.0"), "l1", minutes=1, currency="eur", unit="USD")
    )
    bound = calibrator.bound_for("p1")
    assert bound.currency == "EUR"
    assert bound.unit == "USD"
    assert bound.upper_bound_micro == Decimal("2.0")


def test_label_unit_must_match_its_prediction():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    with pytest.raises(MixedUnitError):
        calibrator.add_final_label(label("p1", 100, "l1", minutes=1, currency="EUR"))


def test_naive_and_iso_timestamps_are_normalized_to_utc():
    item = Prediction(
        prediction_id="p1",
        prediction_timestamp=datetime(2026, 10, 25, 12, 0),
        predicted_cost_micro=100,
    )
    observed = FinalLabel(
        prediction_id="p1",
        final_cost_micro=120,
        observed_at="2026-10-25T12:05:00Z",
        label_source_id="l1",
    )
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(item)
    calibrator.add_final_label(observed)
    assert item.prediction_timestamp == T0
    assert calibrator.current_labels[0].observed_at == at(5)
    assert calibrator.current_labels[0].first_seen_at == at(5)


# ---------------------------------------------------------------------------
# Numerical stability, serialisation, and purity
# ---------------------------------------------------------------------------


def test_large_integer_residuals_and_bounds_are_exact():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 10**18))
    calibrator.add_prediction(prediction("p2", 10**18))
    calibrator.add_final_label(label("p1", 10**18 + 7, "l1", minutes=1))
    bound = calibrator.bound_for("p2")
    assert bound.upper_bound_micro == 10**18 + 7
    assert isinstance(bound.upper_bound_micro, int)


def test_fractional_scores_remain_exact_decimals():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", Decimal("0.1"), unit="USD"))
    calibrator.add_final_label(label("p1", Decimal("0.3"), "l1", minutes=1, unit="USD"))
    assert calibrator.state.score_observations[0].score_micro == Decimal("0.2")
    assert calibrator.bound_for("p1").upper_bound_micro == Decimal("0.3")


def test_state_and_bound_serialisation():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "l1", minutes=1))
    state = calibrator.state.to_dict()
    bound = calibrator.bound_for("p1").to_dict()

    assert state["alpha"] == "0.051"
    assert state["score_count"] == 1
    observation = state["score_observations"][0]
    assert observation["prediction_id"] == "p1"
    assert observation["prediction_timestamp"] == "2026-10-25T12:00:00Z"
    assert observation["label_source_id"] == "l1"
    assert observation["report_version_id"] is None
    assert observation["observed_at"] == "2026-10-25T12:01:00Z"
    assert observation["first_seen_at"] == "2026-10-25T12:01:00Z"
    assert observation["currency"] == "USD"
    assert observation["unit"] == "micro_usd"
    assert observation["score_micro"] == 20
    assert state["coverage_guarantee"] is None
    assert state["limitations"]
    assert bound["status"] == "OK"
    assert bound["upper_bound_micro"] == 120
    assert bound["coverage_guarantee"] is None


def test_no_coverage_guarantee_is_claimed():
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(prediction("p1", 100))
    calibrator.add_final_label(label("p1", 120, "l1", minutes=1))
    assert calibrator.state.coverage_guarantee is None
    assert COVERAGE_STATEMENT in calibrator.state.limitations
    assert calibrator.state.limitations == CALIBRATION_LIMITATIONS
    assert COVERAGE_STATEMENT in calibrator.bound_for("p1").notes


def test_calibrate_builder_accepts_mappings_and_aliases():
    calibrator = calibrate(
        [
            {
                "reservation_id": "p1",
                "decision_timestamp": at(0),
                "modeled_cost_micro": 100,
            }
        ],
        [
            {
                "reservation_id": "p1",
                "actual_micro": 120,
                "observed_at": at(10),
                "reconciliation_id": "r1",
                "source_revision": "D1",
                "source_revision_order": "1",
            }
        ],
    )
    assert calibrator.state.score_count == 1
    tracked = calibrator.current_labels[0]
    assert tracked.label_source_id == "r1"
    assert tracked.report_version_id == "D1"
    assert tracked.report_version_order == "1"
    assert calibrator.bound_for("p1").upper_bound_micro == 120


def test_module_has_no_aws_network_or_filesystem_access():
    import ast

    import calibur_engine.conformal as module

    with open(module.__file__, encoding="utf-8") as source_file:
        source = source_file.read()
    tree = ast.parse(source)
    imported: list[str] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append(node.func.id)
    forbidden = {"boto3", "botocore", "requests", "urllib", "socket", "pathlib", "os", "io"}
    assert not {name.split(".")[0] for name in imported} & forbidden
    assert "open" not in calls
