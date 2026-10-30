"""Strict evidence validator for research replay pipelines.

Enforces fail-closed rules before any empirical replay or analysis:

* Rejects records missing non-null join keys
  (``reservation_id``, ``resource_id``, ``billing_record_key``).
* Enforces strict chronological ordering:
  ``request_received_at_utc <= decision_at_utc <= event_time_utc``.
* Rejects out-of-order billing revisions where
  ``report_version_order <= existing.report_version_order``.
* Rejects records lacking explicit UTC timezone metadata or provenance
  classifications.

All validation errors raise ``ValidatorError``; callers must treat any
raised exception as a hard stop — no record may be silently dropped or
repair-inferred.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class ValidatorError(ValueError):
    """Raised when an evidence record fails strict validation."""

    def __init__(self, record_type: str, key: str, reason: str):
        super().__init__(f"{record_type}[{key}]: {reason}")
        self.record_type = record_type
        self.key = key
        self.reason = reason


def _require_utc_timestamp(value: str, field_name: str, record_type: str, key: str) -> None:
    """Validate that ``value`` carries an explicit UTC timezone designator."""
    if not isinstance(value, str) or not value:
        raise ValidatorError(record_type, key, f"{field_name} is missing or empty")
    # Must end with 'Z' (UTC) — offset strings like +00:00 are accepted too
    # but must be explicit; naive timestamps are rejected.
    if not (value.endswith("Z") or "+" in value[10:] or value[10:].startswith("-")):
        raise ValidatorError(
            record_type, key, f"{field_name} lacks explicit UTC timezone metadata: {value!r}"
        )
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValidatorError(
                record_type, key, f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
            ) from exc


def _require_join_key(value: Any, field_name: str, record_type: str, key: str) -> None:
    """Reject missing or null join keys."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidatorError(record_type, key, f"{field_name} is missing or null")


def _validate_provenance(value: Any, record_type: str, key: str) -> None:
    """Reject records without explicit provenance classification."""
    allowed = {"PROVIDER_OBSERVED", "DERIVED_ESTIMATE", "PROVISIONAL_TELEMETRY"}
    if value not in allowed:
        raise ValidatorError(
            record_type,
            key,
            f"attribution_method must be one of {sorted(allowed)}, got {value!r}",
        )


def _parse_utc(value: str) -> datetime:
    """Parse a UTC timestamp string into a timezone-aware datetime."""
    return datetime.fromisoformat(value)


def validate_admission_event(record: dict) -> None:
    """Validate an AdmissionEvent record.

    Required keys: request_id, reservation_id, request_received_at_utc,
    decision_at_utc, decision, team, reserved_micro,
    budget_before_available_micro, source.
    """
    record_type = "AdmissionEvent"
    key = record.get("request_id", "<unknown>")

    _require_join_key(record.get("request_id"), "request_id", record_type, key)
    _require_join_key(record.get("reservation_id"), "reservation_id", record_type, key)
    _require_utc_timestamp(record.get("request_received_at_utc", ""), "request_received_at_utc", record_type, key)
    _require_utc_timestamp(record.get("decision_at_utc", ""), "decision_at_utc", record_type, key)

    decision = record.get("decision")
    if decision not in {"APPROVED", "DENIED", "PENDING_UNKNOWN"}:
        raise ValidatorError(
            record_type, key, f"decision must be one of APPROVED/DENIED/PENDING_UNKNOWN, got {decision!r}"
        )

    # Chronological ordering: request_received_at_utc <= decision_at_utc
    received = _parse_utc(record["request_received_at_utc"])
    decided = _parse_utc(record["decision_at_utc"])
    if received > decided:
        raise ValidatorError(
            record_type, key,
            f"request_received_at_utc ({received}) is after decision_at_utc ({decided})",
        )


def validate_resource_lifecycle_event(record: dict) -> None:
    """Validate a ResourceLifecycleEvent record.

    Required keys: reservation_id, resource_id, resource_type, event_type,
    event_time_utc, recorded_at_utc.
    """
    record_type = "ResourceLifecycleEvent"
    key = record.get("reservation_id", "<unknown>")

    _require_join_key(record.get("reservation_id"), "reservation_id", record_type, key)
    _require_join_key(record.get("resource_id"), "resource_id", record_type, key)
    _require_utc_timestamp(record.get("event_time_utc", ""), "event_time_utc", record_type, key)
    _require_utc_timestamp(record.get("recorded_at_utc", ""), "recorded_at_utc", record_type, key)

    event_type = record.get("event_type")
    allowed_types = {
        "STARTED", "RUNNING", "STOPPED", "TERMINATED", "FAILED",
        "PROVISIONING", "READY", "DELETED",
    }
    if event_type not in allowed_types:
        raise ValidatorError(
            record_type, key,
            f"event_type must be one of {sorted(allowed_types)}, got {event_type!r}",
        )

    event_time = _parse_utc(record["event_time_utc"])
    recorded_time = _parse_utc(record["recorded_at_utc"])
    if event_time > recorded_time:
        raise ValidatorError(
            record_type, key,
            f"event_time_utc ({event_time}) is after recorded_at_utc ({recorded_time})",
        )


def validate_telemetry_observation(record: dict) -> None:
    """Validate a TelemetryObservation record.

    Required keys: reservation_id, resource_id, metric_name, usage_quantity,
    usage_unit, source_timestamp_utc, collector_observed_at_utc.
    """
    record_type = "TelemetryObservation"
    key = record.get("reservation_id", "<unknown>")

    _require_join_key(record.get("reservation_id"), "reservation_id", record_type, key)
    _require_join_key(record.get("resource_id"), "resource_id", record_type, key)
    _require_utc_timestamp(record.get("source_timestamp_utc", ""), "source_timestamp_utc", record_type, key)
    _require_utc_timestamp(record.get("collector_observed_at_utc", ""), "collector_observed_at_utc", record_type, key)

    source_ts = _parse_utc(record["source_timestamp_utc"])
    collected_ts = _parse_utc(record["collector_observed_at_utc"])
    if source_ts > collected_ts:
        raise ValidatorError(
            record_type, key,
            f"source_timestamp_utc ({source_ts}) is after collector_observed_at_utc ({collected_ts})",
        )


def validate_billing_label_version(record: dict) -> None:
    """Validate a BillingLabelVersion record.

    Required keys: reservation_id, billing_record_key, label_source_id,
    billing_amount_micro, report_version_order,
    collector_first_observed_at_utc, attribution_method.
    """
    record_type = "BillingLabelVersion"
    key = record.get("reservation_id", "<unknown>")

    _require_join_key(record.get("reservation_id"), "reservation_id", record_type, key)
    _require_join_key(record.get("billing_record_key"), "billing_record_key", record_type, key)
    _require_join_key(record.get("label_source_id"), "label_source_id", record_type, key)
    _require_utc_timestamp(record.get("collector_first_observed_at_utc", ""), "collector_first_observed_at_utc", record_type, key)

    _validate_provenance(record.get("attribution_method"), record_type, key)

    report_version_order = record.get("report_version_order")
    if not isinstance(report_version_order, int) or report_version_order < 0:
        raise ValidatorError(
            record_type, key,
            f"report_version_order must be a non-negative integer, got {report_version_order!r}",
        )


def validate_reservation_release_event(record: dict) -> None:
    """Validate a ReservationReleaseEvent record.

    Required keys: reservation_id, released_at_utc, released_micro,
    retained_micro, reason.
    """
    record_type = "ReservationReleaseEvent"
    key = record.get("reservation_id", "<unknown>")

    _require_join_key(record.get("reservation_id"), "reservation_id", record_type, key)
    _require_utc_timestamp(record.get("released_at_utc", ""), "released_at_utc", record_type, key)


VALIDATORS = {
    "AdmissionEvent": validate_admission_event,
    "ResourceLifecycleEvent": validate_resource_lifecycle_event,
    "TelemetryObservation": validate_telemetry_observation,
    "BillingLabelVersion": validate_billing_label_version,
    "ReservationReleaseEvent": validate_reservation_release_event,
}


def validate_record(record_type: str, record: dict) -> None:
    """Validate a single evidence record against its type schema.

    Raises ``ValidatorError`` on any violation.  Callers must treat this as a
    hard stop — no record may be silently dropped or repaired.
    """
    validator = VALIDATORS.get(record_type)
    if validator is None:
        raise ValidatorError(record_type, record.get("reservation_id", "<unknown>"),
                             f"unknown record type: {record_type!r}")
    validator(record)


def validate_revision_ordering(
    existing: dict | None,
    candidate: dict,
) -> None:
    """Validate that a billing revision is not out of order.

    ``existing`` is the previously stored version (or ``None`` if first
    observation).  ``candidate`` is the new record.  Rejects when
    ``candidate.report_version_order <= existing.report_version_order``.
    """
    if existing is None:
        return
    record_type = "BillingLabelVersion"
    key = candidate.get("reservation_id", "<unknown>")
    existing_order = existing.get("report_version_order")
    candidate_order = candidate.get("report_version_order")
    if not isinstance(existing_order, int) or not isinstance(candidate_order, int):
        raise ValidatorError(
            record_type, key,
            "both existing and candidate must have integer report_version_order",
        )
    if candidate_order <= existing_order:
        raise ValidatorError(
            record_type, key,
            f"candidate report_version_order ({candidate_order}) is not strictly greater "
            f"than existing ({existing_order}); revision is stale or duplicate",
        )


__all__ = [
    "ValidatorError",
    "validate_admission_event",
    "validate_billing_label_version",
    "validate_record",
    "validate_reservation_release_event",
    "validate_resource_lifecycle_event",
    "validate_revision_ordering",
    "validate_telemetry_observation",
]
