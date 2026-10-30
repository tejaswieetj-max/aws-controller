"""Persist final settled ratios into the CostSamples calibration table.

``ReservationSettled`` is emitted by :class:`spendctl.billing.BillingReconciler`
only after a billed amount has been attributed to a reservation.  This handler
converts that event into one canonical sample row so ``MarginPolicies`` can use
it on the next admission.  It never runs on provisional telemetry and never
invents a missing amount.

Revision handling
-----------------
A revised bill produces a new ``settlement_id`` and a new ``settled_at``, so it
is written as a new row.  ``spendctl.api._load_margin_policies`` keeps only the
highest ``settlement_version`` per ``reservation_id``, so a revision replaces
the previous label instead of counting as a second job.

Calibration partition
---------------------
``split_conformal`` needs labels that were not used to fit the same policy.
Each row carries ``sample_partition``.  An upstream producer may set it
explicitly; otherwise it is assigned deterministically from the settlement ID
(``CALIBRATION`` or ``EVALUATION``).  The hash is independent of the observed
cost, so it does not select rows by outcome.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

import boto3

from .calibration import resource_class_for
from .ledger import DynamoLedger, ReservationNotFound

PARTITION_CALIBRATION = "CALIBRATION"
PARTITION_EVALUATION = "EVALUATION"
_PARTITIONS = frozenset({PARTITION_CALIBRATION, PARTITION_EVALUATION})


class SampleWriteError(Exception):
    """A settled event could not be converted into a durable sample row."""


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SampleWriteError(f"missing required environment variable {name}")
    return value


def _region() -> str:
    return os.environ.get("AWS_REGION", "ap-south-1")


def _partition_for(settlement_id: str) -> str:
    digest = hashlib.sha256(settlement_id.encode()).digest()
    return PARTITION_CALIBRATION if digest[0] % 2 == 0 else PARTITION_EVALUATION


def build_sample_item(
    detail: dict[str, Any],
    reservation: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Build one CostSamples item, or return ``None`` for a stale event.

    ``None`` means the event does not describe the reservation's current final
    settlement (for example, an out-of-order revision).  Missing or malformed
    fields raise :class:`SampleWriteError`; they are never replaced with zero.
    """
    reservation_id = detail.get("reservation_id")
    settlement_id = detail.get("settlement_id")
    if not isinstance(reservation_id, str) or not reservation_id:
        raise SampleWriteError("event detail is missing reservation_id")
    if not isinstance(settlement_id, str) or not settlement_id:
        raise SampleWriteError("event detail is missing settlement_id")

    if reservation.get("settlement_stage") != "FINAL":
        return None
    final_settlement_id = reservation.get("final_settlement_id")
    if final_settlement_id is not None and final_settlement_id != settlement_id:
        return None

    try:
        actual_micro = int(detail["new_actual_micro"])
        compute_micro = int(reservation["compute_micro"])
        storage_micro = int(reservation["storage_micro"])
        reserved_micro = int(reservation["reserved_micro"])
    except (KeyError, TypeError, ValueError) as error:
        raise SampleWriteError("settled event is missing an integer cost component") from error
    if actual_micro < 0 or compute_micro < 0 or storage_micro < 0 or reserved_micro < 0:
        raise SampleWriteError("settled amounts must be non-negative")
    modeled_runtime_micro = compute_micro + storage_micro
    if modeled_runtime_micro <= 0:
        raise SampleWriteError("modeled runtime cost must be positive")

    resource = reservation.get("resource") or {}
    instance_type = resource.get("instance_type")
    if not isinstance(instance_type, str) or not instance_type:
        raise SampleWriteError("reservation is missing resource.instance_type")

    settled_at = reservation.get("settled_at")
    if not isinstance(settled_at, str) or not settled_at:
        occurred_at = detail.get("occurred_at")
        settled_at = (
            occurred_at
            if isinstance(occurred_at, str) and occurred_at
            else (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
        )

    requested_partition = detail.get("sample_partition")
    partition = (
        requested_partition
        if requested_partition in _PARTITIONS
        else _partition_for(reservation_id)
    )

    return {
        "resource_class": resource_class_for(instance_type),
        "settled_at": settled_at,
        "settlement_id": settlement_id,
        "reservation_id": reservation_id,
        "settlement_version": int(reservation.get("settlement_version") or 0),
        "settlement_stage": "FINAL",
        "attributed": True,
        "sample_partition": partition,
        "source": "billing_reconciliation",
        "actual_micro": actual_micro,
        "reserved_micro": reserved_micro,
        "modeled_runtime_micro": modeled_runtime_micro,
        "compute_micro": compute_micro,
        "storage_micro": storage_micro,
        "instance_type": instance_type,
    }


def reservation_settled_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """EventBridge target for ``spendctl.billing`` / ``ReservationSettled``."""
    if event.get("detail-type") != "ReservationSettled":
        return {"state": "IGNORED", "reason": "unsupported_detail_type"}
    detail = event.get("detail") or {}
    reservation_id = detail.get("reservation_id")
    if not isinstance(reservation_id, str) or not reservation_id:
        raise SampleWriteError("event detail is missing reservation_id")

    ledger = DynamoLedger(
        boto3.client("dynamodb", region_name=_region()),
        _required_env("BUDGETS_TABLE"),
        _required_env("RESERVATIONS_TABLE"),
        _required_env("ALERTS_TABLE"),
    )
    try:
        reservation = ledger.get_reservation(reservation_id)
    except ReservationNotFound:
        return {"state": "IGNORED", "reason": "reservation_not_found"}

    item = build_sample_item(detail, reservation)
    if item is None:
        return {"state": "IGNORED", "reason": "stale_or_non_final_settlement"}

    table = boto3.resource("dynamodb", region_name=_region()).Table(
        _required_env("COST_SAMPLES_TABLE")
    )
    table.put_item(Item=item)
    return {
        "state": "STORED",
        "reservation_id": reservation_id,
        "settlement_id": item["settlement_id"],
        "resource_class": item["resource_class"],
        "sample_partition": item["sample_partition"],
    }
