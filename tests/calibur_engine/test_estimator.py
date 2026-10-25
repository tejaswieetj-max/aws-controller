"""Unit tests for the decision-time spend estimator.

Fixtures are local, deterministic, and provide no empirical AWS evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from calibur_engine.estimator import (
    BaselineEstimator,
    CumulativeEstimator,
    EstimateStatus,
    EstimatorError,
    EWMAEstimator,
    FutureObservationError,
    InvalidObservationError,
    MixedUnitError,
    Observation,
    UnsupportedEstimatorError,
    cumulative_observed_spend,
    estimate,
    estimate_ewma,
    ewma,
    sum_observed,
)

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
DECISION = T0 + timedelta(hours=30)


def obs(minutes: int, amount: int | None, *, obs_id: str = "", **kwargs) -> Observation:
    return Observation(
        timestamp=T0 + timedelta(minutes=minutes),
        amount_micro=amount,
        observation_id=obs_id or f"obs-{minutes}",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def test_cumulative_arithmetic_and_transparency_fields():
    result = CumulativeEstimator().estimate(
        [obs(1, 100_000), obs(2, 250_000), obs(3, 650_000)],
        DECISION,
    )
    assert result.status == EstimateStatus.OK
    assert result.status == "OK"
    assert result.value_micro == 1_000_000
    assert result.method == "cumulative_observed_spend"
    assert result.used_count == 3
    assert result.input_count == 3
    assert result.unit == "micro_usd"
    assert result.currency == "USD"
    assert result.complete is True
    assert result.lower_bound is False
    assert result.is_sufficient is True
    assert result.insufficient_data is False


def test_cumulative_keeps_genuine_negative_corrections():
    result = estimate([obs(1, 100), obs(2, -25)], DECISION)
    assert result.value_micro == 75


def test_ewma_arithmetic_is_seeded_and_exact():
    result = EWMAEstimator(alpha="0.5").estimate(
        [obs(1, 100), obs(2, 200), obs(3, 300)],
        DECISION,
    )
    assert result.status == "OK"
    assert result.value_micro == 225
    assert result.method == "ewma"

    alpha_two_tenths = estimate_ewma(
        [obs(1, 100), obs(2, 200), obs(3, 300)],
        DECISION,
        alpha="0.2",
    )
    assert alpha_two_tenths.value_micro == 156


def test_ewma_helper_skips_missing_without_zero_filling():
    assert ewma([100, None, 200, None], 0.5) == 150
    assert ewma([None], 0.5) is None
    assert sum_observed([100, None, 200]) == 300
    assert sum_observed([None, None]) is None


def test_cumulative_wrapper_matches_estimator():
    rows = [obs(1, 10), obs(2, 20)]
    assert cumulative_observed_spend(rows, DECISION).value_micro == 30


# ---------------------------------------------------------------------------
# Units and missing data
# ---------------------------------------------------------------------------


def test_units_are_preserved_and_not_reinterpreted():
    rows = [
        Observation(timestamp=T0, amount_micro=Decimal("1.5"), unit="USD"),
        Observation(timestamp=T0 + timedelta(minutes=1), amount_micro=Decimal("2.5"), unit="USD"),
    ]
    result = estimate(rows, DECISION)
    assert result.value_micro == 4
    assert result.unit == "USD"
    assert result.currency == "USD"


def test_mixing_units_or_currencies_is_rejected():
    with pytest.raises(MixedUnitError, match="units"):
        estimate(
            [
                Observation(timestamp=T0, amount_micro=1, unit="micro_usd"),
                Observation(timestamp=T0 + timedelta(minutes=1), amount_micro=1, unit="USD"),
            ],
            DECISION,
        )
    with pytest.raises(MixedUnitError, match="currencies"):
        estimate(
            [
                Observation(timestamp=T0, amount_micro=1, currency="USD"),
                Observation(timestamp=T0 + timedelta(minutes=1), amount_micro=1, currency="EUR"),
            ],
            DECISION,
        )


def test_missing_cost_is_preserved_and_cumulative_is_a_lower_bound():
    result = estimate([obs(1, 100), obs(2, None), obs(3, 200)], DECISION)
    assert result.status == "OK"
    assert result.value_micro == 300
    assert result.used_count == 2
    assert result.missing_cost_count == 1
    assert result.complete is False
    assert result.lower_bound is True
    assert any("lower bound" in note for note in result.notes)


def test_all_missing_costs_return_insufficient_data_not_zero():
    result = estimate([obs(1, None), obs(2, None)], DECISION)
    assert result.status == "INSUFFICIENT_DATA"
    assert result.value_micro is None
    assert result.reason == "no_cost_observations"
    assert result.missing_cost_count == 2
    assert result.used_count == 0


def test_unapproved_observations_are_excluded_but_counted():
    result = estimate(
        [obs(1, 100, approved=True), obs(2, 900, approved=False)],
        DECISION,
    )
    assert result.value_micro == 100
    assert result.unapproved_count == 1
    assert all(row.approved for row in result.observations)


def test_only_unapproved_observations_return_insufficient_data():
    result = estimate([obs(1, 100, approved=False)], DECISION)
    assert result.status == "INSUFFICIENT_DATA"
    assert result.value_micro is None
    assert result.reason == "no_approved_observations"


def test_empty_input_returns_explicit_insufficient_data():
    result = estimate([], DECISION)
    assert result.status == EstimateStatus.INSUFFICIENT_DATA
    assert result.value_micro is None
    assert result.reason == "no_observations"
    with pytest.raises(EstimatorError, match="insufficient data"):
        result.require_value()


def test_minimum_observation_gate_is_explicit():
    result = CumulativeEstimator(min_observations=3).estimate(
        [obs(1, 100), obs(2, 200)],
        DECISION,
    )
    assert result.status == "INSUFFICIENT_DATA"
    assert result.reason == "below_min_observations"
    assert result.value_micro is None


# ---------------------------------------------------------------------------
# Ordering and decision-time validation
# ---------------------------------------------------------------------------


def test_input_order_does_not_change_cumulative_or_ewma():
    rows = [obs(3, 300), obs(1, 100), obs(2, 200)]

    assert estimate(rows, DECISION).value_micro == 600
    assert estimate_ewma(rows, DECISION, alpha="0.5").value_micro == 225
    assert estimate_ewma(list(reversed(rows)), DECISION, alpha="0.5").value_micro == 225


def test_same_timestamp_ties_are_deterministic_by_observation_id():
    first = obs(1, 100, obs_id="a")
    second = obs(1, 200, obs_id="b")
    forward = estimate_ewma([first, second], DECISION, alpha="0.5")
    backward = estimate_ewma([second, first], DECISION, alpha="0.5")
    assert forward.value_micro == backward.value_micro == 150
    assert [row.observation_id for row in forward.observations] == ["a", "b"]


def test_future_observation_is_rejected_even_when_unapproved_or_missing():
    future = obs(30 * 60 + 1, 100)
    with pytest.raises(FutureObservationError, match="after decision timestamp"):
        estimate([future], DECISION)
    with pytest.raises(FutureObservationError):
        estimate([obs(30 * 60 + 1, None, approved=False)], DECISION)


def test_observation_exactly_at_decision_time_is_allowed():
    result = estimate([obs(30 * 60, 100)], DECISION)
    assert result.status == "OK"
    assert result.value_micro == 100


def test_only_observations_available_at_decision_are_used():
    result = estimate([obs(1, 100), obs(2, 200), obs(3, 900, approved=False)], DECISION)
    assert result.value_micro == 300


def test_naive_and_iso_timestamps_are_normalised_to_utc():
    naive = Observation(timestamp=datetime(2026, 10, 25, 12, 0), amount_micro=50)
    iso = Observation(timestamp="2026-10-25T12:05:00Z", amount_micro=50)
    assert naive.timestamp.tzinfo is not None
    assert iso.timestamp == datetime(2026, 10, 25, 12, 5, tzinfo=UTC)
    assert estimate([naive, iso], DECISION).value_micro == 100


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [True, False])
def test_bool_amount_is_rejected(amount):
    with pytest.raises(InvalidObservationError):
        Observation(timestamp=T0, amount_micro=amount)


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), Decimal("NaN")])
def test_non_finite_amount_is_rejected(amount):
    with pytest.raises(InvalidObservationError):
        Observation(timestamp=T0, amount_micro=amount)


def test_invalid_timestamp_is_rejected():
    with pytest.raises(InvalidObservationError):
        Observation(timestamp="not-a-timestamp", amount_micro=1)
    with pytest.raises(InvalidObservationError):
        estimate([obs(1, 1)], object())


@pytest.mark.parametrize("alpha", [0, -0.1, 1.1, float("nan"), "abc"])
def test_invalid_ewma_alpha_is_rejected(alpha):
    with pytest.raises(EstimatorError):
        EWMAEstimator(alpha=alpha)


def test_min_observations_must_be_positive():
    with pytest.raises(ValueError):
        CumulativeEstimator(min_observations=0)


# ---------------------------------------------------------------------------
# Model selection and compatibility aliases
# ---------------------------------------------------------------------------


def test_kalman_is_explicitly_unsupported_without_a_defensible_model():
    with pytest.raises(UnsupportedEstimatorError, match="does not map"):
        BaselineEstimator("kalman")


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError, match="unknown estimator method"):
        BaselineEstimator("magic")


def test_method_aliases_select_the_same_baseline():
    assert BaselineEstimator("cumulative").method == "cumulative_observed_spend"
    assert BaselineEstimator("exponential").method == "ewma"
    assert estimate([obs(1, 100)], DECISION, method="cumulative").value_micro == 100


def test_observation_keyword_aliases_and_mapping_inputs():
    row = Observation(observed_at=T0, cost_micro=25, id="alias-row", provenance="fixture")
    assert row.observation_id == "alias-row"
    assert row.source == "fixture"
    assert row.amount_micro == 25

    result = estimate(
        [{"timestamp": T0, "amount_micro": 25, "observation_id": "mapped"}],
        DECISION,
    )
    assert result.value_micro == 25


def test_observation_id_first_positional_form_is_supported():
    row = Observation("id-first", T0, 40, True, "billing")
    assert row.observation_id == "id-first"
    assert row.timestamp == T0
    assert row.amount_micro == 40
    assert row.approved is True
    assert row.source == "billing"


def test_result_dict_view_does_not_expose_sensitive_state():
    result = estimate([obs(1, 100)], DECISION)
    as_dict = result.to_dict()
    assert as_dict["status"] == "OK"
    assert as_dict["value"] == 100
    assert as_dict["value_micro"] == 100
    assert as_dict["unit"] == "micro_usd"
    assert "observations" not in as_dict


# ---------------------------------------------------------------------------
# Numerical stability
# ---------------------------------------------------------------------------


def test_ewma_is_stable_for_large_constant_values_over_many_steps():
    value = 10**18
    rows = [obs(index, value) for index in range(1, 1001)]
    result = estimate_ewma(rows, DECISION, alpha="0.1")
    assert result.value_micro == value


def test_cumulative_uses_exact_integer_arithmetic_for_large_values():
    rows = [obs(index, 10**18) for index in range(1, 1001)]
    result = estimate(rows, DECISION)
    assert result.value_micro == 1000 * 10**18
    assert isinstance(result.value_micro, int)


def test_sum_is_exact_beyond_the_default_decimal_precision():
    wide = Decimal("1E+30")
    assert sum_observed([wide, Decimal(1)]) == 10**30 + 1
    assert sum_observed([Decimal("0.1"), Decimal("0.2")]) == Decimal("0.3")
    assert sum_observed([Decimal("0.00")]) == 0


def test_cumulative_with_widely_separated_decimal_magnitudes_is_exact():
    rows = [
        Observation(timestamp=T0, amount_micro=Decimal("1E+30"), unit="USD"),
        Observation(timestamp=T0 + timedelta(minutes=1), amount_micro=Decimal("0.5"), unit="USD"),
    ]
    result = estimate(rows, DECISION)
    assert result.value_micro == Decimal("1000000000000000000000000000000.5")


def test_ewma_large_values_with_none_gaps_remain_exact():
    rows = [obs(1, 10**18), obs(2, None), obs(3, 10**18)]
    result = estimate_ewma(rows, DECISION, alpha="0.25")
    assert result.value_micro == 10**18
    assert result.missing_cost_count == 1


def test_module_has_no_aws_or_network_imports():
    import calibur_engine.estimator as module

    with open(module.__file__, encoding="utf-8") as source_file:
        source = source_file.read()
    for forbidden in ("boto3", "botocore", "requests", "urllib.request", "socket"):
        assert forbidden not in source
