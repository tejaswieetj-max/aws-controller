"""Unit tests for replay metric formulas.

All records are local unit-test inputs.  Nothing here is written to empirical
results or treated as observed research evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from research.replay import metrics

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def outcome(
    job: str,
    *,
    reservation: int | None,
    actual: int | None,
    observed_at: datetime | None = None,
    upper: int | None = None,
    frozen_at: datetime | None = None,
    decision_at: datetime | None = None,
) -> metrics.JobOutcome:
    return metrics.JobOutcome(
        job_id=job,
        committed_reservation_micro=reservation,
        actual_billed_micro=actual,
        final_label_observed_at=observed_at,
        upper_bound_micro=upper,
        bound_frozen_at=frozen_at,
        decision_at=decision_at,
    )


def test_bof_counts_overruns_and_excludes_missing_labels():
    result = metrics.reservation_overrun_frequency(
        [
            outcome("a", reservation=100, actual=150, observed_at=T0),
            outcome("b", reservation=100, actual=100, observed_at=T0),
            outcome("c", reservation=100, actual=90, observed_at=T0),
            outcome("d", reservation=100, actual=None),
            outcome("e", reservation=None, actual=100, observed_at=T0),
        ]
    )
    assert result.status == metrics.STATUS_COMPUTED
    assert result.numerator == 1
    assert result.denominator == 3
    assert result.value == Decimal(1) / Decimal(3)
    assert result.missing == 1
    assert result.excluded == 1


def test_zero_denominator_is_not_computed_not_zero():
    result = metrics.reservation_overrun_frequency([outcome("a", reservation=None, actual=None)])
    assert result.status == metrics.STATUS_NOT_COMPUTED
    assert result.value is None
    assert result.denominator == 0


def test_abr_uses_only_fully_observed_windows():
    result = metrics.aggregate_budget_breach_rate(
        [
            metrics.BudgetWindow("w1", "team", "2026-01", 1000, 800, 300, True),
            metrics.BudgetWindow("w2", "team", "2026-02", 1000, 700, 200, True),
            metrics.BudgetWindow("w3", "team", "2026-03", 1000, 900, 500, False),
            metrics.BudgetWindow("w4", "team", "2026-04", 1000, None, 100, True),
        ]
    )
    assert result.numerator == 1
    assert result.denominator == 2
    assert result.excluded == 2


def interval(start_hour: int, end_hour: int, reserved: int, active: int) -> metrics.ReservationInterval:
    return metrics.ReservationInterval(
        start=T0 + timedelta(hours=start_hour),
        end=T0 + timedelta(hours=end_hour),
        reserved_micro=reserved,
        active_spend_micro=active,
        source_id=f"interval-{start_hour}-{end_hour}",
    )


def test_lci_integrates_only_observed_intervals_and_reports_gaps():
    result = metrics.locked_capital_integral(
        [interval(0, 1, 100, 20), interval(1, 2, 50, 50)],
        window_start=T0,
        window_end=T0 + timedelta(hours=4),
    )
    assert result.status == metrics.STATUS_COMPUTED
    assert result.numerator == 80 * 3600
    assert result.denominator == 3600
    assert result.value == Decimal(80)
    assert result.unit == "micro_usd_hours"
    assert any("unobserved_seconds=7200" in note for note in result.notes)


def test_lci_rejects_overlapping_intervals():
    with pytest.raises(ValueError, match="overlap"):
        metrics.locked_capital_integral([interval(0, 2, 100, 0), interval(1, 3, 100, 0)])


def test_lci_without_intervals_is_not_computed():
    result = metrics.locked_capital_integral([])
    assert result.status == metrics.STATUS_NOT_COMPUTED
    assert result.value is None


def test_rar_keeps_unknown_separate_from_denied():
    result = metrics.request_acceptance_rate(
        [
            metrics.RequestDecision("a", True, "APPROVE"),
            metrics.RequestDecision("b", True, "DENY"),
            metrics.RequestDecision("c", True, None),
            metrics.RequestDecision("d", False, "APPROVE"),
        ]
    )
    assert result.numerator == 1
    assert result.denominator == 3
    assert result.missing == 1
    assert result.excluded == 1


def test_coverage_requires_a_decision_time_bound_and_label():
    result = metrics.coverage(
        [
            # eligible: bound frozen at decision, label later
            outcome(
                "a",
                reservation=100,
                actual=90,
                observed_at=T0 + timedelta(hours=2),
                upper=100,
                frozen_at=T0,
                decision_at=T0,
            ),
            # eligible but not covered
            outcome(
                "b",
                reservation=100,
                actual=120,
                observed_at=T0 + timedelta(hours=2),
                upper=100,
                frozen_at=T0,
                decision_at=T0,
            ),
            # no decision timestamp: excluded
            outcome("c", reservation=100, actual=90, observed_at=T0, upper=100, frozen_at=T0),
        ]
    )
    assert result.numerator == 1
    assert result.denominator == 2
    assert result.excluded == 1


def test_coverage_rejects_a_bound_frozen_after_the_decision():
    with pytest.raises(ValueError, match="bound_frozen_at"):
        outcome(
            "a",
            reservation=100,
            actual=90,
            observed_at=T0 + timedelta(hours=2),
            upper=100,
            frozen_at=T0 + timedelta(hours=1),
            decision_at=T0,
        )


def test_matching_rate_counts_all_outcomes():
    result = metrics.matching_rate(matched=8, unmatched=1, ambiguous=1)
    assert result.numerator == 8
    assert result.denominator == 10
    assert result.missing == 1
    assert result.excluded == 1


def test_feedback_delay_summary_uses_nearest_rank():
    delays = [
        metrics.FeedbackDelay(f"job-{days}", T0, T0 + timedelta(days=days))
        for days in range(1, 11)
    ]
    summary = metrics.describe_feedback_delay(delays, censored_count=2)
    assert summary["status"] == metrics.STATUS_COMPUTED
    assert summary["count"] == 10
    assert summary["censored_count"] == 2
    assert summary["median_seconds"] == int(timedelta(days=5.5).total_seconds())
    assert summary["p90_seconds"] == int(timedelta(days=9).total_seconds())


def test_feedback_delay_without_observations_is_not_computed():
    summary = metrics.describe_feedback_delay([], censored_count=3)
    assert summary["status"] == metrics.STATUS_NOT_COMPUTED
    assert summary["median_seconds"] is None
    assert summary["censored_count"] == 3


def test_static_margin_rounds_up_and_preserves_missing():
    assert metrics.reserve_raw_point(100) == 100
    assert metrics.reserve_raw_point(None) is None
    assert metrics.reserve_static_margin(100) == 120
    assert metrics.reserve_static_margin(101) == 122
    assert metrics.reserve_static_margin(None) is None
    with pytest.raises(ValueError):
        metrics.reserve_static_margin(100, Decimal("0.5"))


def test_baseline_availability_reports_reasons_not_substitutes():
    availability = metrics.baseline_availability(
        has_point_estimates=False,
        has_final_labels=False,
        has_telemetry=False,
        has_ledger_context=False,
        has_calibration_history=False,
        has_revision_history=False,
    )
    by_policy = {item.policy: item for item in availability}
    assert set(by_policy) == set(metrics.BASELINE_POLICIES)
    assert not any(item.available for item in availability)
    assert all(item.reason for item in availability)
    assert by_policy[metrics.POLICY_ORACLE].status == metrics.STATUS_UNAVAILABLE
