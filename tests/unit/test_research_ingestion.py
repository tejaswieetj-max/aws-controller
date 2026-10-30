"""Tests for research ingestion export contract and strict evidence validator."""

from __future__ import annotations

import pytest

from research.ingestion.export_contract import (
    AdmissionEvent,
    BillingLabelVersion,
    ReservationReleaseEvent,
    ResourceLifecycleEvent,
    TelemetryObservation,
)
from research.ingestion.ledger_validator import (
    ValidatorError,
    validate_admission_event,
    validate_billing_label_version,
    validate_record,
    validate_reservation_release_event,
    validate_resource_lifecycle_event,
    validate_revision_ordering,
    validate_telemetry_observation,
)

# ---------------------------------------------------------------------------
# Export contract: round-trip serialization
# ---------------------------------------------------------------------------


def test_admission_event_to_dict():
    event = AdmissionEvent(
        request_id="req_abc",
        reservation_id="rsv_abc",
        request_received_at_utc="2026-10-01T10:00:00Z",
        decision_at_utc="2026-10-01T10:00:05Z",
        decision="APPROVED",
        team="alpha",
        reserved_micro=500_000,
        budget_before_available_micro=1_000_000,
    )
    d = event.to_dict()
    assert d["request_id"] == "req_abc"
    assert d["reservation_id"] == "rsv_abc"
    assert d["decision"] == "APPROVED"
    assert d["team"] == "alpha"
    assert d["reserved_micro"] == 500_000
    assert d["budget_before_available_micro"] == 1_000_000
    assert "collected_at_utc" in d


def test_resource_lifecycle_event_to_dict():
    event = ResourceLifecycleEvent(
        reservation_id="rsv_abc",
        resource_id="i-123456",
        resource_type="ec2",
        event_type="STARTED",
        event_time_utc="2026-10-01T10:05:00Z",
        recorded_at_utc="2026-10-01T10:05:01Z",
    )
    d = event.to_dict()
    assert d["event_type"] == "STARTED"
    assert d["resource_id"] == "i-123456"
    assert d["collected_at_utc"] is not None


def test_telemetry_observation_to_dict():
    event = TelemetryObservation(
        reservation_id="rsv_abc",
        resource_id="i-123456",
        metric_name="CPUUtilization",
        usage_quantity=42.5,
        usage_unit="Percent",
        source_timestamp_utc="2026-10-01T10:00:00Z",
        collector_observed_at_utc="2026-10-01T10:00:05Z",
    )
    d = event.to_dict()
    assert d["usage_quantity"] == 42.5
    assert d["usage_unit"] == "Percent"


def test_billing_label_version_to_dict():
    event = BillingLabelVersion(
        reservation_id="rsv_abc",
        billing_record_key="key-abc",
        label_source_id="cur-2026-10",
        revision_of=None,
        billing_amount_micro=105_000,
        report_version_order=1,
        collector_first_observed_at_utc="2026-10-02T00:00:00Z",
        attribution_method="PROVIDER_OBSERVED",
    )
    d = event.to_dict()
    assert d["attribution_method"] == "PROVIDER_OBSERVED"
    assert d["billing_amount_micro"] == 105_000


def test_reservation_release_event_to_dict():
    event = ReservationReleaseEvent(
        reservation_id="rsv_abc",
        released_at_utc="2026-10-05T00:00:00Z",
        released_micro=95_000,
        retained_micro=10_000,
        reason="billing_reconciliation_complete",
    )
    d = event.to_dict()
    assert d["released_micro"] == 95_000
    assert d["retained_micro"] == 10_000


# ---------------------------------------------------------------------------
# Validator: admission events
# ---------------------------------------------------------------------------


def test_admission_event_valid():
    validate_admission_event({
        "request_id": "req_1",
        "reservation_id": "rsv_1",
        "request_received_at_utc": "2026-10-01T10:00:00Z",
        "decision_at_utc": "2026-10-01T10:00:05Z",
        "decision": "APPROVED",
        "team": "alpha",
        "reserved_micro": 500_000,
        "budget_before_available_micro": 1_000_000,
        "source": "spendctl/admission",
    })


def test_admission_event_rejects_missing_request_id():
    with pytest.raises(ValidatorError):
        validate_admission_event({
            "reservation_id": "rsv_1",
            "request_received_at_utc": "2026-10-01T10:00:00Z",
            "decision_at_utc": "2026-10-01T10:00:05Z",
            "decision": "APPROVED",
            "team": "alpha",
            "reserved_micro": 500_000,
            "budget_before_available_micro": 1_000_000,
        })


def test_admission_event_rejects_naive_timestamp():
    with pytest.raises(ValidatorError):
        validate_admission_event({
            "request_id": "req_1",
            "reservation_id": "rsv_1",
            "request_received_at_utc": "2026-10-01T10:00:00",  # no Z suffix
            "decision_at_utc": "2026-10-01T10:00:05Z",
            "decision": "APPROVED",
            "team": "alpha",
            "reserved_micro": 500_000,
            "budget_before_available_micro": 1_000_000,
        })


def test_admission_event_rejects_out_of_order_timestamps():
    with pytest.raises(ValidatorError):
        validate_admission_event({
            "request_id": "req_1",
            "reservation_id": "rsv_1",
            "request_received_at_utc": "2026-10-01T10:00:10Z",
            "decision_at_utc": "2026-10-01T10:00:05Z",
            "decision": "APPROVED",
            "team": "alpha",
            "reserved_micro": 500_000,
            "budget_before_available_micro": 1_000_000,
        })


# ---------------------------------------------------------------------------
# Validator: resource lifecycle events
# ---------------------------------------------------------------------------


def test_resource_lifecycle_event_valid():
    validate_resource_lifecycle_event({
        "reservation_id": "rsv_1",
        "resource_id": "i-123",
        "resource_type": "ec2",
        "event_type": "STARTED",
        "event_time_utc": "2026-10-01T10:05:00Z",
        "recorded_at_utc": "2026-10-01T10:05:01Z",
    })


def test_resource_lifecycle_event_rejects_invalid_event_type():
    with pytest.raises(ValidatorError):
        validate_resource_lifecycle_event({
            "reservation_id": "rsv_1",
            "resource_id": "i-123",
            "resource_type": "ec2",
            "event_type": "INVALID_TYPE",
            "event_time_utc": "2026-10-01T10:05:00Z",
            "recorded_at_utc": "2026-10-01T10:05:01Z",
        })


# ---------------------------------------------------------------------------
# Validator: telemetry observations
# ---------------------------------------------------------------------------


def test_telemetry_observation_valid():
    validate_telemetry_observation({
        "reservation_id": "rsv_1",
        "resource_id": "i-123",
        "metric_name": "CPUUtilization",
        "usage_quantity": 42.5,
        "usage_unit": "Percent",
        "source_timestamp_utc": "2026-10-01T10:00:00Z",
        "collector_observed_at_utc": "2026-10-01T10:00:05Z",
    })


# ---------------------------------------------------------------------------
# Validator: billing label versions
# ---------------------------------------------------------------------------


def test_billing_label_version_valid():
    validate_billing_label_version({
        "reservation_id": "rsv_1",
        "billing_record_key": "cur-1",
        "label_source_id": "cur-2026-10",
        "revision_of": None,
        "billing_amount_micro": 105_000,
        "report_version_order": 1,
        "collector_first_observed_at_utc": "2026-10-02T00:00:00Z",
        "attribution_method": "PROVIDER_OBSERVED",
    })


def test_billing_label_version_rejects_bad_provenance():
    with pytest.raises(ValidatorError):
        validate_billing_label_version({
            "reservation_id": "rsv_1",
            "billing_record_key": "cur-1",
            "label_source_id": "cur-2026-10",
            "revision_of": None,
            "billing_amount_micro": 105_000,
            "report_version_order": 1,
            "collector_first_observed_at_utc": "2026-10-02T00:00:00Z",
            "attribution_method": "UNKNOWN",
        })


# ---------------------------------------------------------------------------
# Validator: revision ordering
# ---------------------------------------------------------------------------


def test_revision_ordering_accepts_newer():
    validate_revision_ordering(
        {"report_version_order": 1},
        {"report_version_order": 2},
    )


def test_revision_ordering_rejects_stale():
    with pytest.raises(ValidatorError):
        validate_revision_ordering(
            {"report_version_order": 2},
            {"report_version_order": 1},
        )


def test_revision_ordering_rejects_duplicate():
    with pytest.raises(ValidatorError):
        validate_revision_ordering(
            {"report_version_order": 2},
            {"report_version_order": 2},
        )


# ---------------------------------------------------------------------------
# Validator: record dispatcher
# ---------------------------------------------------------------------------


def test_validate_record_dispatches_by_type():
    validate_record("AdmissionEvent", {
        "request_id": "req_1",
        "reservation_id": "rsv_1",
        "request_received_at_utc": "2026-10-01T10:00:00Z",
        "decision_at_utc": "2026-10-01T10:00:05Z",
        "decision": "APPROVED",
        "team": "alpha",
        "reserved_micro": 500_000,
        "budget_before_available_micro": 1_000_000,
    })
    validate_record("BillingLabelVersion", {
        "reservation_id": "rsv_1",
        "billing_record_key": "cur-1",
        "label_source_id": "cur-2026-10",
        "revision_of": None,
        "billing_amount_micro": 105_000,
        "report_version_order": 1,
        "collector_first_observed_at_utc": "2026-10-02T00:00:00Z",
        "attribution_method": "PROVIDER_OBSERVED",
    })


def test_validate_record_rejects_unknown_type():
    with pytest.raises(ValidatorError):
        validate_record("UnknownType", {"reservation_id": "rsv_1"})


# ---------------------------------------------------------------------------
# Validator: reservation release events
# ---------------------------------------------------------------------------


def test_reservation_release_event_valid():
    validate_reservation_release_event({
        "reservation_id": "rsv_1",
        "released_at_utc": "2026-10-05T00:00:00Z",
        "released_micro": 95_000,
        "retained_micro": 10_000,
        "reason": "billing_reconciliation_complete",
    })


def test_reservation_release_event_rejects_missing_timestamp():
    with pytest.raises(ValidatorError):
        validate_reservation_release_event({
            "reservation_id": "rsv_1",
            "released_micro": 95_000,
            "retained_micro": 10_000,
            "reason": "billing_reconciliation_complete",
        })
