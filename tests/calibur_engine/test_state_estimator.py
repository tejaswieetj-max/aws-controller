"""Deterministic software tests; fixtures are not operational observations."""

from datetime import UTC, datetime, timedelta

import pytest

from calibur_engine.models import SourceRef
from calibur_engine.state_estimator import (
    LinearModel,
    StateEstimatorError,
    StateObservation,
    estimate_state,
)

T0 = datetime(2026, 10, 26, tzinfo=UTC)
MODEL = LinearModel(((1.0, 0.0), (0.0, 1.0)), ((0.0, 0.0), (0.0, 0.0)), (0.0, 0.0), ((100.0, 0.0), (0.0, 100.0)))


def observation(source, record_id, value, row, *, revision=0, minutes=0, observed_minutes=None):
    source_at = T0 + timedelta(minutes=minutes)
    return StateObservation(source, record_id, revision, source_at, source_at + timedelta(minutes=observed_minutes or 0), value, row, 1.0, SourceRef(f"{source}:{record_id}", source_at, "test", "fixture"))


def test_kalman_uses_explicit_source_rows_and_uncertainty():
    result = estimate_state(MODEL, [observation("cloudwatch", "cw", 10.0, (0.0, 1.0)), observation("cur", "cur", 20.0, (1.0, 0.0), minutes=1)], decision_at=T0 + timedelta(minutes=2), max_age=timedelta(hours=1))
    assert result.status == "OK"
    assert result.state == pytest.approx((19.801980198, 9.900990099))
    assert result.used_record_ids == ("cw", "cur")
    assert "caller-supplied" in result.assumptions[0]


def test_missing_stale_and_delayed_observations_are_never_zero_filled():
    stale = observation("cost_explorer", "stale", 99, (1.0, 0.0), minutes=-120)
    delayed = observation("cur", "late", 11, (1.0, 0.0), observed_minutes=5)
    result = estimate_state(MODEL, [stale, delayed], decision_at=T0 + timedelta(minutes=2), max_age=timedelta(hours=1))
    assert result.status == "INSUFFICIENT_DATA"
    assert result.state is None
    assert result.skipped == (("late", "OBSERVED_AFTER_DECISION"), ("stale", "STALE_SOURCE_OBSERVATION"))


def test_later_revision_replaces_previous_measurement_once():
    old = observation("cur", "line", 10, (1.0, 0.0), revision=0)
    revised = observation("cur", "line", 30, (1.0, 0.0), revision=1)
    result = estimate_state(MODEL, [old, revised], decision_at=T0 + timedelta(minutes=1), max_age=timedelta(hours=1))
    assert result.used_record_ids == ("line",)
    assert result.state[0] == pytest.approx(29.702970297)


def test_conflicting_same_revision_and_invalid_covariance_fail_closed():
    one = observation("cur", "line", 10, (1.0, 0.0))
    two = observation("cur", "line", 11, (1.0, 0.0))
    with pytest.raises(StateEstimatorError, match="conflicting"):
        estimate_state(MODEL, [one, two], decision_at=T0, max_age=timedelta(hours=1))
    with pytest.raises(StateEstimatorError, match="positive semidefinite"):
        LinearModel(((1.0,),), ((-1.0,),), (0.0,), ((1.0,),))


def test_filter_uses_source_time_not_arrival_count_for_prediction():
    model = LinearModel(
        ((2.0,),), ((1.0,),), (1.0,), ((1.0,),), transition_interval_seconds=60,
    )
    first = observation("cloudwatch", "first", 1.0, (1.0,), minutes=0)
    later = observation("cur", "later", 4.0, (1.0,), minutes=120)
    result = estimate_state(model, [first, later], decision_at=T0 + timedelta(minutes=121), max_age=timedelta(hours=3))
    # The second update is predicted through two configured minute intervals
    # (1 -> 4) before incorporating its source measurement.
    assert result.status == "OK"
    assert result.used_record_ids == ("first", "later")


def test_filter_rejects_unconfigured_fractional_source_interval():
    model = LinearModel(((1.0,),), ((1.0,),), (0.0,), ((1.0,),), transition_interval_seconds=60)
    half_minute = observation("cur", "half", 1.0, (1.0,), minutes=0)
    fractional = StateObservation("cur", "fractional", 0, T0 + timedelta(seconds=30), T0 + timedelta(seconds=30), 1.0, (1.0,), 1.0, SourceRef("fractional", T0, "test", "fixture"))
    with pytest.raises(StateEstimatorError, match="align"):
        estimate_state(model, [half_minute, fractional], decision_at=T0 + timedelta(minutes=1), max_age=timedelta(hours=1))
