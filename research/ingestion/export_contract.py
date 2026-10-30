"""Export contract data models for research replay and ledger validation.

These dataclasses define the canonical schema for evidence records that must
survive into the research replay pipeline.  Each field carries explicit
provenance and timestamp information so that the strict evidence validator
can reject any record lacking non-null join keys, explicit UTC timezone
metadata, or provenance classifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class AdmissionEvent:
    """A durable admission decision record emitted by SPENDCTL."""

    request_id: str
    reservation_id: str
    request_received_at_utc: str
    decision_at_utc: str
    decision: str  # APPROVED | DENIED | PENDING_UNKNOWN
    team: str
    reserved_micro: int
    budget_before_available_micro: int
    source: str = "spendctl/admission"

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "reservation_id": self.reservation_id,
            "request_received_at_utc": self.request_received_at_utc,
            "decision_at_utc": self.decision_at_utc,
            "decision": self.decision,
            "team": self.team,
            "reserved_micro": self.reserved_micro,
            "budget_before_available_micro": self.budget_before_available_micro,
            "source": self.source,
            "collected_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class ResourceLifecycleEvent:
    """A provisioning lifecycle event for a reservation."""

    reservation_id: str
    resource_id: str
    resource_type: str
    event_type: str  # STARTED | RUNNING | STOPPED | TERMINATED | FAILED
    event_time_utc: str
    recorded_at_utc: str
    instance_id: str | None = None
    volume_ids: list[str] | None = None
    region: str | None = None
    source: str = "spendctl/provisioner"

    def to_dict(self) -> dict:
        return {
            "reservation_id": self.reservation_id,
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "event_type": self.event_type,
            "event_time_utc": self.event_time_utc,
            "recorded_at_utc": self.recorded_at_utc,
            "instance_id": self.instance_id,
            "volume_ids": self.volume_ids or [],
            "region": self.region,
            "source": self.source,
            "collected_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class TelemetryObservation:
    """A one-minute CloudWatch telemetry sample linked to a reservation."""

    reservation_id: str
    resource_id: str
    metric_name: str
    usage_quantity: float
    usage_unit: str
    source_timestamp_utc: str
    collector_observed_at_utc: str
    provisioning_state: str | None = None  # STARTING | RUNNING | STOPPED | UNKNOWN
    source: str = "spendctl/cloudwatch-evidence"

    def to_dict(self) -> dict:
        return {
            "reservation_id": self.reservation_id,
            "resource_id": self.resource_id,
            "metric_name": self.metric_name,
            "usage_quantity": self.usage_quantity,
            "usage_unit": self.usage_unit,
            "source_timestamp_utc": self.source_timestamp_utc,
            "collector_observed_at_utc": self.collector_observed_at_utc,
            "provisioning_state": self.provisioning_state,
            "source": self.source,
            "collected_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class BillingLabelVersion:
    """A delayed/CUR billing observation linked to a reservation."""

    reservation_id: str
    billing_record_key: str
    label_source_id: str
    revision_of: str | None  # key of the previous revision, if revised
    billing_amount_micro: int
    report_version_order: int
    collector_first_observed_at_utc: str
    attribution_method: str  # PROVIDER_OBSERVED | DERIVED_ESTIMATE | PROVISIONAL_TELEMETRY
    charge_period_start_utc: str | None = None
    charge_period_end_utc: str | None = None
    source: str = "spendctl/billing"

    def to_dict(self) -> dict:
        return {
            "reservation_id": self.reservation_id,
            "billing_record_key": self.billing_record_key,
            "label_source_id": self.label_source_id,
            "revision_of": self.revision_of,
            "billing_amount_micro": self.billing_amount_micro,
            "report_version_order": self.report_version_order,
            "collector_first_observed_at_utc": self.collector_first_observed_at_utc,
            "attribution_method": self.attribution_method,
            "charge_period_start_utc": self.charge_period_start_utc,
            "charge_period_end_utc": self.charge_period_end_utc,
            "source": self.source,
            "collected_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class ReservationReleaseEvent:
    """A hazard-rate survival release event for a reservation."""

    reservation_id: str
    released_at_utc: str
    released_micro: int
    retained_micro: int
    reason: str
    release_policy_version: str = "v1"
    source: str = "spendctl/release-policy"

    def to_dict(self) -> dict:
        return {
            "reservation_id": self.reservation_id,
            "released_at_utc": self.released_at_utc,
            "released_micro": self.released_micro,
            "retained_micro": self.retained_micro,
            "reason": self.reason,
            "release_policy_version": self.release_policy_version,
            "source": self.source,
            "collected_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }


__all__ = [
    "AdmissionEvent",
    "BillingLabelVersion",
    "ReservationReleaseEvent",
    "ResourceLifecycleEvent",
    "TelemetryObservation",
]
