"""Unit tests for release timing with explicit right-censoring.

The revision histories below are local unit-test inputs only.  They are never
written to the empirical pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from calibur_engine.models import FutureInputError, InvalidModelError, SourceRef
from calibur_engine.release_policy import (
    REASON_HOLD_CAPPED,
    REASON_INSUFFICIENT_OBSERVED_REVISIONS,
    REASON_NO_OBSERVATIONS,
    REASON_NO_OBSERVED_REVISIONS,
    REASON_QUANTILE_NOT_REACHED,
    STATUS_DESCRIPTIVE_ONLY,
    STATUS_ESTIMABLE,
    STATUS_NOT_ESTIMABLE,
    ReleasePolicyConfig,
    RevisionObservation,
    km_quantile_seconds,
    plan_release,
    summarize_revision_history,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
AS_OF = T0 + timedelta(days=365)
DAY = 86_400


def src(name: str) -> SourceRef:
    return SourceRef(source_id=name, observed_at=AS_OF, source_file="unit-test.json")


def observation(
    key: str,
    *,
    revised_after_days: int | None,
    observed_days: int = 300,
) -> RevisionObservation:
    completed = T0
    observed_until = completed + timedelta(days=observed_days)
    revised_at = (
        None
        if revised_after_days is None
        else completed + timedelta(days=revised_after_days)
    )
    return RevisionObservation(
        record_key=key,
        completed_at=completed,
        observed_until=observed_until,
        revised_at=revised_at,
        source=src(key),
    )


def revisions(*delays: int) -> list[RevisionObservation]:
    return [observation(f"job-{delay}", revised_after_days=delay) for delay in delays]


def test_no_observations_is_not_estimable():
    summary = summarize_revision_history([])
    assert summary.status == STATUS_NOT_ESTIMABLE
    assert summary.reason == REASON_NO_OBSERVATIONS
    assert summary.revised_count == 0


def test_censored_only_history_is_not_reclassified_as_unrevised():
    history = [observation("a", revised_after_days=None), observation("b", revised_after_days=None)]
    summary = summarize_revision_history(history)
    assert summary.status == STATUS_NOT_ESTIMABLE
    assert summary.reason == REASON_NO_OBSERVED_REVISIONS
    assert summary.censored_count == 2
    assert summary.revised_count == 0
    assert km_quantile_seconds(history, Decimal("0.9")) is None


def test_mixed_history_reports_delays_and_censoring():
    history = [
        observation("a", revised_after_days=1),
        observation("b", revised_after_days=3),
        observation("c", revised_after_days=None),
    ]
    summary = summarize_revision_history(history)
    assert summary.status == STATUS_DESCRIPTIVE_ONLY
    assert summary.revised_count == 2
    assert summary.censored_count == 1
    assert summary.delays_seconds == (DAY, 3 * DAY)
    assert summary.median_delay_seconds == 2 * DAY
    assert summary.max_delay_seconds == 3 * DAY
    assert summary.to_dict()["censored_count"] == 1


def test_revision_before_completion_is_rejected():
    with pytest.raises(InvalidModelError, match="later than completed_at"):
        RevisionObservation(
            record_key="bad",
            completed_at=T0,
            observed_until=T0 + timedelta(days=1),
            revised_at=T0 - timedelta(seconds=1),
            source=src("bad"),
        )


def test_revision_after_observation_window_is_rejected():
    with pytest.raises(InvalidModelError, match="observed_until"):
        RevisionObservation(
            record_key="bad",
            completed_at=T0,
            observed_until=T0 + timedelta(days=1),
            revised_at=T0 + timedelta(days=2),
            source=src("bad"),
        )


def test_km_quantile_uses_observed_event_times():
    history = revisions(*range(1, 11))
    # Ten events, survival drops to exactly 1/10 at day 9.
    assert km_quantile_seconds(history, Decimal("0.9")) == 9 * DAY


def test_km_quantile_is_none_when_censoring_prevents_the_level():
    history = revisions(1)
    history.extend(
        observation(f"censor-{index}", revised_after_days=None, observed_days=10)
        for index in range(50)
    )
    assert km_quantile_seconds(history, Decimal("0.9")) is None


def test_km_quantile_rejects_invalid_levels():
    history = revisions(1)
    with pytest.raises(InvalidModelError):
        km_quantile_seconds(history, Decimal(0))
    with pytest.raises(InvalidModelError):
        km_quantile_seconds(history, Decimal(1))


def test_plan_release_is_not_estimable_without_revision_events():
    proposal = plan_release(
        completed_at=T0,
        committed_amount_micro=1_000_000,
        history=[observation("a", revised_after_days=None)],
        as_of=AS_OF,
        job_source=src("job-1"),
    )
    assert proposal.status == STATUS_NOT_ESTIMABLE
    assert proposal.reason == REASON_NO_OBSERVED_REVISIONS
    assert proposal.release_at is None
    assert proposal.retained_amount_micro is None


def test_plan_release_requires_enough_observed_revisions():
    proposal = plan_release(
        completed_at=T0,
        committed_amount_micro=1_000_000,
        history=revisions(1, 2),
        as_of=AS_OF,
        job_source=src("job-1"),
        config=ReleasePolicyConfig(min_observed_revisions=30),
    )
    assert proposal.status == STATUS_NOT_ESTIMABLE
    assert proposal.reason == REASON_INSUFFICIENT_OBSERVED_REVISIONS


def test_plan_release_returns_a_time_and_residual_hold():
    proposal = plan_release(
        completed_at=T0,
        committed_amount_micro=1_000_000,
        history=revisions(*range(1, 41)),
        as_of=AS_OF,
        job_source=src("job-1"),
        config=ReleasePolicyConfig(
            min_observed_revisions=30,
            retention_quantile=Decimal("0.9"),
            residual_hold_fraction=Decimal("0.05"),
            max_hold=timedelta(days=60),
        ),
    )
    assert proposal.status == STATUS_ESTIMABLE
    assert proposal.release_at == T0 + timedelta(days=36)
    assert proposal.retained_amount_micro == 50_000
    assert proposal.observed_revisions == 40
    assert proposal.censored_count == 0
    assert proposal.to_dict()["status"] == STATUS_ESTIMABLE


def test_plan_release_caps_the_hold():
    proposal = plan_release(
        completed_at=T0,
        committed_amount_micro=10_000,
        history=revisions(*range(1, 41)),
        as_of=AS_OF,
        job_source=src("job-1"),
        config=ReleasePolicyConfig(
            min_observed_revisions=30,
            max_hold=timedelta(days=5),
        ),
    )
    assert proposal.status == STATUS_ESTIMABLE
    assert proposal.release_at == T0 + timedelta(days=5)
    assert proposal.reason == REASON_HOLD_CAPPED


def test_plan_release_reports_unreachable_quantile():
    history = revisions(1)
    history.extend(
        observation(f"censor-{index}", revised_after_days=None, observed_days=10)
        for index in range(40)
    )
    proposal = plan_release(
        completed_at=T0,
        committed_amount_micro=1_000_000,
        history=history,
        as_of=AS_OF,
        job_source=src("job-1"),
        config=ReleasePolicyConfig(min_observed_revisions=1),
    )
    assert proposal.status == STATUS_NOT_ESTIMABLE
    assert proposal.reason == REASON_QUANTILE_NOT_REACHED


def test_plan_release_rejects_future_completion():
    with pytest.raises(FutureInputError):
        plan_release(
            completed_at=AS_OF + timedelta(seconds=1),
            committed_amount_micro=1,
            history=revisions(1),
            as_of=AS_OF,
            job_source=src("job-1"),
        )


def test_plan_release_rejects_negative_committed_amount():
    with pytest.raises(InvalidModelError):
        plan_release(
            completed_at=T0,
            committed_amount_micro=-1,
            history=revisions(1),
            as_of=AS_OF,
            job_source=src("job-1"),
        )
