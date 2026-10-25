"""Focused tests for the pure early-warning state machine.

Every timestamp and telemetry value below is a local, deterministic fixture.
They are shaped like real observations but make no empirical claim; the module
itself never creates observations, reads a clock, or performs I/O.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import calibur_engine
from calibur_engine import early_warning as ew
from calibur_engine.conformal import (
    ConformalCalibrator,
    FinalLabel,
    Prediction,
)
from calibur_engine.conformal import (
    EarlyWarning as ConformalEarlyWarning,
)
from calibur_engine.early_warning import (
    DEFAULT_THRESHOLD_SOURCE,
    EARLY_WARNING_LIMITATIONS,
    MICRO_USD,
    REASON_ALREADY_BREACHED,
    REASON_AT_THRESHOLD,
    REASON_BELOW_THRESHOLD,
    REASON_COMPLETED_NO_WARNING,
    REASON_COMPLETED_WITH_WARNING,
    REASON_DUPLICATE_OBSERVATION,
    REASON_MISSING_PROVISIONAL_COST,
    REASON_NO_TELEMETRY,
    REASON_NO_USABLE_TELEMETRY,
    REASON_THRESHOLD_EXCEEDED,
    STATUS_BREACHED,
    STATUS_COMPLETED,
    STATUS_INSUFFICIENT_DATA,
    STATUS_MONITORING,
    AmbiguousObservationOrderError,
    CompletionConflictError,
    ConflictingObservationError,
    DuplicateJobError,
    EarlyWarningError,
    EarlyWarningEvent,
    EarlyWarningMonitor,
    InvalidCompletionTimestampError,
    InvalidTelemetryError,
    InvalidTelemetryTimestampError,
    InvalidThresholdError,
    MissingThresholdError,
    ObservationAfterCompletionError,
    OutOfOrderCompletionError,
    OutOfOrderObservationError,
    TelemetryObservation,
    UnitMismatchError,
    UnknownJobError,
)

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def observation(
    *,
    job_id: str = "job-1",
    minutes: int = 0,
    cost: int | Decimal | None = 1_000,
    record: str = "record-1",
    **kwargs,
) -> TelemetryObservation:
    return TelemetryObservation(
        job_id=job_id,
        observed_at=kwargs.pop("observed_at", at(minutes)),
        provisional_cost_micro=cost,
        source_record_id=record,
        **kwargs,
    )


def make_monitor(threshold: int | Decimal = 10_000, *, job_id: str = "job-1", **kwargs):
    monitor = EarlyWarningMonitor()
    monitor.register_job(job_id, threshold, **kwargs)
    return monitor


# ---------------------------------------------------------------------------
# Constants and package surface
# ---------------------------------------------------------------------------


def test_constants_are_stable_and_documented() -> None:
    assert MICRO_USD == "micro_usd"
    assert DEFAULT_THRESHOLD_SOURCE == "reserved_upper_bound"
    assert (STATUS_INSUFFICIENT_DATA, STATUS_MONITORING, STATUS_BREACHED, STATUS_COMPLETED) == (
        "INSUFFICIENT_DATA",
        "MONITORING",
        "BREACHED",
        "COMPLETED",
    )
    assert {
        REASON_NO_TELEMETRY,
        REASON_NO_USABLE_TELEMETRY,
        REASON_BELOW_THRESHOLD,
        REASON_AT_THRESHOLD,
        REASON_THRESHOLD_EXCEEDED,
        REASON_ALREADY_BREACHED,
        REASON_MISSING_PROVISIONAL_COST,
        REASON_DUPLICATE_OBSERVATION,
        REASON_COMPLETED_NO_WARNING,
        REASON_COMPLETED_WITH_WARNING,
    } == {
        "NO_TELEMETRY",
        "NO_USABLE_TELEMETRY",
        "BELOW_THRESHOLD",
        "AT_THRESHOLD",
        "THRESHOLD_EXCEEDED",
        "ALREADY_BREACHED",
        "MISSING_PROVISIONAL_COST",
        "DUPLICATE_OBSERVATION",
        "COMPLETED_NO_WARNING",
        "COMPLETED_WITH_WARNING",
    }
    assert EARLY_WARNING_LIMITATIONS
    assert all(isinstance(item, str) and item for item in EARLY_WARNING_LIMITATIONS)


def test_package_reexports_early_warning_surface() -> None:
    assert len(calibur_engine.__all__) == len(set(calibur_engine.__all__))
    assert all(hasattr(calibur_engine, name) for name in calibur_engine.__all__)
    for name in (
        "EarlyWarningMonitor",
        "TelemetryObservation",
        "EarlyWarningEvent",
        "STATUS_MONITORING",
        "REASON_THRESHOLD_EXCEEDED",
        "EARLY_WARNING_LIMITATIONS",
    ):
        assert name in calibur_engine.__all__


# ---------------------------------------------------------------------------
# Observation validation and audit fields
# ---------------------------------------------------------------------------


def test_observation_preserves_audit_fields() -> None:
    item = observation(minutes=7, cost=12_345, record="telemetry-abc", source="cloudwatch")
    assert item.source_record_id == "telemetry-abc"
    assert item.observation_id == "telemetry-abc"
    assert item.observed_at == at(7)
    assert item.provisional_cost_micro == 12_345
    assert item.source == "cloudwatch"
    assert item.currency == "USD"
    assert item.unit == MICRO_USD


def test_observation_normalises_timestamp_and_currency() -> None:
    item = TelemetryObservation(
        job_id="job-1",
        observed_at="2026-10-25T12:30:00Z",
        provisional_cost_micro="1500",
        source_record_id="record-1",
        currency="usd",
    )
    assert item.observed_at == at(30)
    assert item.observed_at.tzinfo is UTC
    assert item.currency == "USD"
    assert item.provisional_cost_micro == 1500


def test_observation_missing_cost_is_retained() -> None:
    item = observation(cost=None)
    assert item.provisional_cost_micro is None


def test_observation_from_mapping_accepts_collector_aliases() -> None:
    item = TelemetryObservation.from_mapping(
        {
            "request_id": "job-1",
            "observation_timestamp": "2026-10-25T12:00:00Z",
            "provisional_micro": 2500,
            "telemetry_event_id": "record-9",
            "provenance": "cloudwatch",
        }
    )
    assert item.job_id == "job-1"
    assert item.observed_at == at(0)
    assert item.provisional_cost_micro == 2500
    assert item.source_record_id == "record-9"
    assert item.source == "cloudwatch"


def test_observation_from_mapping_rejects_ambiguous_field() -> None:
    with pytest.raises(TypeError, match="multiple values"):
        TelemetryObservation.from_mapping(
            {
                "job_id": "job-1",
                "observed_at": at(0),
                "provisional_cost_micro": 1,
                "source_record_id": "record-1",
                "timestamp": at(1),
            }
        )


def test_observation_rejects_missing_source_record() -> None:
    with pytest.raises(InvalidTelemetryError, match="source_record_id"):
        observation(record="   ")


def test_observation_rejects_missing_timestamp() -> None:
    with pytest.raises(InvalidTelemetryTimestampError):
        observation(observed_at=None)


def test_observation_rejects_negative_cost() -> None:
    with pytest.raises(InvalidTelemetryError, match="negative"):
        observation(cost=-1)


def test_observation_rejects_bool_cost() -> None:
    with pytest.raises(InvalidTelemetryError):
        observation(cost=True)


def test_observation_to_dict_is_json_friendly() -> None:
    payload = observation(cost=Decimal("100.50")).to_dict()
    assert payload == {
        "job_id": "job-1",
        "observed_at": "2026-10-25T12:00:00Z",
        "provisional_cost_micro": "100.50",
        "source_record_id": "record-1",
        "currency": "USD",
        "unit": MICRO_USD,
        "source": "unknown",
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_job_new_and_identical() -> None:
    monitor = EarlyWarningMonitor()
    assert monitor.register_job("job-1", 10_000) is True
    assert monitor.register_job("job-1", 10_000) is False


def test_register_job_conflict_raises() -> None:
    monitor = make_monitor()
    with pytest.raises(DuplicateJobError):
        monitor.register_job("job-1", 10_001)


def test_register_job_requires_threshold() -> None:
    monitor = EarlyWarningMonitor()
    with pytest.raises(MissingThresholdError):
        monitor.register_job("job-1", None)
    with pytest.raises(TypeError):
        monitor.register_job("job-1")


def test_register_job_rejects_negative_threshold() -> None:
    with pytest.raises(InvalidThresholdError, match="negative"):
        EarlyWarningMonitor().register_job("job-1", -1)


def test_register_job_rejects_blank_job_id() -> None:
    with pytest.raises(EarlyWarningError):
        EarlyWarningMonitor().register_job("   ", 1)


def test_initial_state_is_explicitly_insufficient() -> None:
    state = make_monitor().state_for("job-1")
    assert state.status == STATUS_INSUFFICIENT_DATA
    assert state.reason == REASON_NO_TELEMETRY
    assert state.insufficient_data is True
    assert state.breached is False
    assert state.completed is False
    assert state.observation_count == 0
    assert state.evaluated_count == 0
    assert state.missing_cost_count == 0
    assert state.last_source_record_id is None
    assert state.last_observed_at is None
    assert state.warning_event is None


# ---------------------------------------------------------------------------
# Threshold crossing and latching
# ---------------------------------------------------------------------------


def test_below_threshold_monitors_without_warning() -> None:
    monitor = make_monitor(10_000)
    result = monitor.observe(observation(cost=9_999))
    assert result.accepted is True
    assert result.reason == REASON_BELOW_THRESHOLD
    assert result.warning is None
    assert result.triggered_warning is False
    state = result.state
    assert state.status == STATUS_MONITORING
    assert state.reason == REASON_BELOW_THRESHOLD
    assert state.evaluated_count == 1
    assert monitor.warnings == ()


def test_equal_threshold_does_not_warn() -> None:
    monitor = make_monitor(10_000)
    result = monitor.observe(observation(cost=10_000))
    assert result.reason == REASON_AT_THRESHOLD
    assert result.state.status == STATUS_MONITORING
    assert result.state.reason == REASON_AT_THRESHOLD
    assert result.warning is None
    assert monitor.warnings == ()


def test_strict_crossing_warns_and_copies_provenance() -> None:
    monitor = make_monitor(10_000)
    item = observation(cost=10_001, record="record-7", source="provisional_telemetry")
    result = monitor.observe(item)
    assert result.accepted is True
    assert result.reason == REASON_THRESHOLD_EXCEEDED
    assert result.triggered_warning is True
    warning = result.warning
    assert isinstance(warning, EarlyWarningEvent)
    assert warning.event_id == "job-1:early-warning:record-7"
    assert warning.job_id == "job-1"
    assert warning.source_record_id == "record-7"
    assert warning.observed_at == at(0)
    assert warning.threshold_micro == 10_000
    assert warning.threshold_source == DEFAULT_THRESHOLD_SOURCE
    assert warning.provisional_cost_micro == 10_001
    assert warning.currency == "USD"
    assert warning.unit == MICRO_USD
    assert warning.source == "provisional_telemetry"
    assert warning.reason_code == REASON_THRESHOLD_EXCEEDED
    state = result.state
    assert state.status == STATUS_BREACHED
    assert state.reason == REASON_THRESHOLD_EXCEEDED
    assert state.breached is True
    assert state.warning_event == warning
    assert monitor.warnings == (warning,)


def test_crossing_with_exact_decimal_cost() -> None:
    item = observation(cost=Decimal("10000.0001"))
    result = make_monitor(10_000).observe(item)
    assert result.warning is not None
    assert result.warning.provisional_cost_micro == Decimal("10000.0001")


def test_zero_threshold_only_warns_above_zero() -> None:
    monitor = make_monitor(0)
    assert monitor.observe(observation(cost=0)).reason == REASON_AT_THRESHOLD
    assert monitor.warnings == ()
    result = monitor.observe(observation(cost=1, minutes=1, record="record-2"))
    assert result.warning is not None


def test_warning_latches_and_does_not_repeat() -> None:
    monitor = make_monitor(10_000)
    first = monitor.observe(observation(cost=12_000))
    assert first.warning is not None
    second = monitor.observe(observation(cost=13_000, minutes=1, record="record-2"))
    assert second.accepted is True
    assert second.reason == REASON_ALREADY_BREACHED
    assert second.warning is None
    assert second.state.status == STATUS_BREACHED
    assert second.state.warning_event == first.warning
    assert monitor.warnings == (first.warning,)


def test_later_lower_reading_does_not_clear_warning() -> None:
    monitor = make_monitor(10_000)
    first = monitor.observe(observation(cost=12_000))
    later = monitor.observe(observation(cost=1, minutes=1, record="record-2"))
    assert later.reason == REASON_BELOW_THRESHOLD
    assert later.state.status == STATUS_BREACHED
    assert later.state.reason == REASON_THRESHOLD_EXCEEDED
    assert later.state.warning_event == first.warning
    assert monitor.warnings == (first.warning,)


def test_warnings_are_sorted_across_jobs() -> None:
    monitor = EarlyWarningMonitor()
    monitor.register_job("job-b", 100)
    monitor.register_job("job-a", 100)
    monitor.observe(observation(job_id="job-b", cost=200, minutes=5, record="b-1"))
    monitor.observe(observation(job_id="job-a", cost=200, minutes=1, record="a-1"))
    assert [warning.job_id for warning in monitor.warnings] == ["job-a", "job-b"]
    assert [state.job_id for state in monitor.job_states] == ["job-a", "job-b"]


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------


def test_missing_cost_is_not_treated_as_zero() -> None:
    monitor = make_monitor(0)
    result = monitor.observe(observation(cost=None))
    assert result.accepted is True
    assert result.reason == REASON_MISSING_PROVISIONAL_COST
    assert result.state.status == STATUS_INSUFFICIENT_DATA
    assert result.state.reason == REASON_NO_USABLE_TELEMETRY
    assert result.state.observation_count == 1
    assert result.state.evaluated_count == 0
    assert result.state.missing_cost_count == 1
    assert result.state.last_source_record_id == "record-1"
    assert monitor.warnings == ()


def test_missing_cost_then_usable_observation() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=None))
    result = monitor.observe(observation(cost=500, minutes=1, record="record-2"))
    assert result.reason == REASON_BELOW_THRESHOLD
    assert result.state.observation_count == 2
    assert result.state.evaluated_count == 1
    assert result.state.missing_cost_count == 1
    assert result.state.status == STATUS_MONITORING


def test_observations_for_preserves_records_and_order() -> None:
    monitor = make_monitor(10_000)
    first = observation(cost=None, minutes=0, record="record-1")
    second = observation(cost=500, minutes=1, record="record-2")
    monitor.observe(first)
    monitor.observe(second)
    assert monitor.observations_for("job-1") == (first, second)


# ---------------------------------------------------------------------------
# Duplicates, conflicts, and ordering
# ---------------------------------------------------------------------------


def test_duplicate_observation_is_idempotent() -> None:
    monitor = make_monitor(10_000)
    item = observation(cost=500)
    monitor.observe(item)
    result = monitor.observe(item)
    assert result.accepted is False
    assert result.reason == REASON_DUPLICATE_OBSERVATION
    assert result.state.observation_count == 1
    assert result.state.evaluated_count == 1
    assert monitor.observations_for("job-1") == (item,)


def test_conflicting_source_record_raises() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=500))
    with pytest.raises(ConflictingObservationError):
        monitor.observe(observation(cost=600))
    assert monitor.state_for("job-1").observation_count == 1


def test_duplicate_after_completion_is_idempotent() -> None:
    monitor = make_monitor(10_000)
    item = observation(cost=500)
    monitor.observe(item)
    monitor.mark_completed("job-1", at(5))
    result = monitor.observe(item)
    assert result.accepted is False
    assert result.reason == REASON_DUPLICATE_OBSERVATION
    assert result.state.status == STATUS_COMPLETED


def test_unknown_job_raises() -> None:
    with pytest.raises(UnknownJobError):
        EarlyWarningMonitor().observe(observation())


def test_out_of_order_observation_raises() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=500, minutes=5))
    with pytest.raises(OutOfOrderObservationError):
        monitor.observe(observation(cost=600, minutes=4, record="record-2"))
    assert monitor.state_for("job-1").observation_count == 1


def test_equal_timestamp_from_different_record_is_ambiguous() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=500, minutes=5))
    with pytest.raises(AmbiguousObservationOrderError):
        monitor.observe(observation(cost=600, minutes=5, record="record-2"))
    assert monitor.state_for("job-1").observation_count == 1


def test_observation_after_completion_is_rejected() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=500, minutes=0))
    monitor.mark_completed("job-1", at(50))
    with pytest.raises(ObservationAfterCompletionError):
        monitor.observe(observation(cost=20_000, minutes=10, record="record-2"))
    assert monitor.state_for("job-1").observation_count == 1
    assert monitor.warnings == ()


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


def test_completion_without_warning() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=500))
    assert monitor.mark_completed("job-1", at(5)) is True
    state = monitor.state_for("job-1")
    assert state.status == STATUS_COMPLETED
    assert state.reason == REASON_COMPLETED_NO_WARNING
    assert state.completed is True
    assert state.breached is False
    assert state.completed_at == at(5)
    assert monitor.mark_completed("job-1", at(5)) is False


def test_completion_preserves_warning() -> None:
    monitor = make_monitor(10_000)
    warning = monitor.observe(observation(cost=12_000)).warning
    monitor.mark_completed("job-1", at(5))
    state = monitor.state_for("job-1")
    assert state.status == STATUS_COMPLETED
    assert state.reason == REASON_COMPLETED_WITH_WARNING
    assert state.breached is True
    assert state.warning_event == warning


def test_completion_may_equal_last_observation() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=500, minutes=5))
    assert monitor.mark_completed("job-1", at(5)) is True


def test_completion_before_last_observation_raises() -> None:
    monitor = make_monitor(10_000)
    monitor.observe(observation(cost=500, minutes=5))
    with pytest.raises(OutOfOrderCompletionError):
        monitor.mark_completed("job-1", at(4))


def test_conflicting_completion_raises() -> None:
    monitor = make_monitor(10_000)
    monitor.mark_completed("job-1", at(5))
    with pytest.raises(CompletionConflictError):
        monitor.mark_completed("job-1", at(6))


def test_completion_without_observations_is_allowed() -> None:
    monitor = make_monitor(10_000)
    assert monitor.mark_completed("job-1", at(5)) is True
    state = monitor.state_for("job-1")
    assert state.status == STATUS_COMPLETED
    assert state.reason == REASON_COMPLETED_NO_WARNING
    assert state.observation_count == 0


def test_invalid_completion_timestamp_raises() -> None:
    monitor = make_monitor(10_000)
    with pytest.raises(InvalidCompletionTimestampError):
        monitor.mark_completed("job-1", "not-a-timestamp")


def test_completion_for_unknown_job_raises() -> None:
    with pytest.raises(UnknownJobError):
        EarlyWarningMonitor().mark_completed("job-1", at(0))


# ---------------------------------------------------------------------------
# Units, thresholds, and serialisation
# ---------------------------------------------------------------------------


def test_currency_mismatch_raises() -> None:
    monitor = make_monitor(10_000, currency="USD")
    with pytest.raises(UnitMismatchError):
        monitor.observe(observation(currency="EUR"))


def test_unit_mismatch_raises() -> None:
    monitor = make_monitor(10_000, unit=MICRO_USD)
    with pytest.raises(UnitMismatchError):
        monitor.observe(observation(unit="cents"))


def test_currency_is_normalised_before_comparison() -> None:
    monitor = make_monitor(10_000, currency="usd")
    result = monitor.observe(observation(currency="Usd"))
    assert result.state.currency == "USD"


def test_custom_threshold_source_is_preserved() -> None:
    monitor = make_monitor(10_000, threshold_source="caller_supplied_bound")
    warning = monitor.observe(observation(cost=20_000)).warning
    assert warning is not None
    assert warning.threshold_source == "caller_supplied_bound"
    assert monitor.state_for("job-1").threshold_source == "caller_supplied_bound"


def test_state_and_event_serialisation() -> None:
    monitor = make_monitor(10_000)
    warning = monitor.observe(observation(cost=Decimal("12500.5"), record="record-3")).warning
    assert warning is not None
    state_payload = monitor.state_for("job-1").to_dict()
    event_payload = warning.to_dict()
    assert state_payload["status"] == STATUS_BREACHED
    assert state_payload["threshold_micro"] == 10_000
    assert state_payload["last_source_record_id"] == "record-3"
    assert state_payload["last_observed_at"] == "2026-10-25T12:00:00Z"
    assert state_payload["warning_event"] == event_payload
    assert state_payload["limitations"] == list(EARLY_WARNING_LIMITATIONS)
    assert event_payload["provisional_cost_micro"] == "12500.5"
    assert event_payload["observed_at"] == "2026-10-25T12:00:00Z"


# ---------------------------------------------------------------------------
# Purity and determinism
# ---------------------------------------------------------------------------


def test_future_timestamp_is_accepted_because_no_clock_is_read() -> None:
    monitor = make_monitor(10_000)
    item = observation(observed_at=datetime(2999, 1, 1, tzinfo=UTC), cost=500)
    assert monitor.observe(item).accepted is True
    assert monitor.state_for("job-1").last_observed_at == datetime(2999, 1, 1, tzinfo=UTC)


def test_module_imports_only_pure_standard_library_and_estimator() -> None:
    tree = ast.parse(Path(ew.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    assert imported <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "decimal",
        "typing",
        ".estimator",
    }


def test_monitors_do_not_share_state() -> None:
    first = make_monitor(10_000)
    second = make_monitor(20_000)
    assert first.observe(observation(cost=15_000)).warning is not None
    assert second.observe(observation(cost=15_000)).warning is None
    assert second.state_for("job-1").status == STATUS_MONITORING


def test_event_requires_strict_excess() -> None:
    with pytest.raises(EarlyWarningError, match="strictly above"):
        EarlyWarningEvent(
            event_id="event-1",
            job_id="job-1",
            source_record_id="record-1",
            observed_at=at(0),
            threshold_micro=100,
            threshold_source=DEFAULT_THRESHOLD_SOURCE,
            provisional_cost_micro=100,
            currency="USD",
            unit=MICRO_USD,
            source="provisional_telemetry",
        )


# ---------------------------------------------------------------------------
# Separation from conformal calibration
# ---------------------------------------------------------------------------


def _conformal_snapshot(calibrator: ConformalCalibrator) -> tuple:
    state = calibrator.state
    return (
        state.alpha,
        state.threshold_micro,
        state.score_observations,
        state.miss_count,
        state.covered_count,
        state.early_warning_count,
        calibrator.bound_for("job-1"),
    )


def _calibrator() -> ConformalCalibrator:
    calibrator = ConformalCalibrator()
    calibrator.add_prediction(Prediction("job-1", at(0), 1_000_000))
    calibrator.add_final_label(FinalLabel("job-1", 1_200_000, at(30), "label-1"))
    return calibrator


def test_warning_does_not_mutate_conformal_state() -> None:
    calibrator = _calibrator()
    before = _conformal_snapshot(calibrator)

    monitor = make_monitor(1_100_000)
    result = monitor.observe(observation(cost=1_150_000, minutes=10))
    assert result.triggered_warning is True

    assert _conformal_snapshot(calibrator) == before
    assert calibrator.state.alpha == before[0]
    assert calibrator.state.threshold_micro == before[1]
    assert calibrator.bound_for("job-1") == before[6]
    assert calibrator.state.early_warning_count == 0


def test_recording_warning_in_conformal_changes_only_telemetry_count() -> None:
    calibrator = _calibrator()
    monitor = make_monitor(1_100_000)
    event = monitor.observe(observation(cost=1_150_000, minutes=10)).warning
    assert event is not None

    before = _conformal_snapshot(calibrator)
    applied = calibrator.record_early_warning(
        ConformalEarlyWarning(
            prediction_id=event.job_id,
            event_id=event.event_id,
            observed_at=event.observed_at,
            provisional_cost_micro=event.provisional_cost_micro,
            reserved_micro=event.threshold_micro,
            currency=event.currency,
            unit=event.unit,
            source=event.source,
        )
    )
    assert applied is True

    after = _conformal_snapshot(calibrator)
    assert after[:5] == before[:5]
    assert after[6] == before[6]
    assert calibrator.state.early_warning_count == before[5] + 1
    assert len(calibrator.early_warnings) == 1
