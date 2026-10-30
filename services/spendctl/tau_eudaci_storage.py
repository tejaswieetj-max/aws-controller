"""Durable adapter for operational warning/final-bill state.

Events are stored separately from conformal samples and do not mutate budget
balances. Conditional writes make duplicate delivery safe across restarts.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from calibur_engine.tau_eudaci import EarlyUpdateEvent, FinalBillEvent, TauEudaciError

_SER = TypeSerializer()
_DES = TypeDeserializer()


def record_early_event(table: Any, event: EarlyUpdateEvent) -> bool:
    item = {
        "pk": f"TAU_JOB#{event.job_id}", "sk": f"EARLY#{event.event_id}",
        "record_type": "TAU_EARLY_WARNING", "event_id": event.event_id,
        "job_id": event.job_id, "observed_at": _iso(event.observed_at),
        "threshold_micro": event.threshold_micro, "provisional_micro": event.provisional_micro,
    }
    return _put_immutable(table, item, "early event")


def list_job_events_handler(event: dict, context: Any) -> dict:
    """Authenticated API target; the API dispatcher enforces team identity."""
    job_id = (event.get("pathParameters") or {}).get("id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job id is required")
    return list_job_events(table_from_env(), job_id)


def record_final_bill(table: Any, event: FinalBillEvent) -> bool:
    bill_key = hashlib.sha256(event.bill_id.encode()).hexdigest()
    key = {"pk": f"TAU_JOB#{event.job_id}", "sk": f"FINAL#{bill_key}#CURRENT"}
    item = {
        **key, "record_type": "TAU_FINAL_BILL", "bill_id": event.bill_id,
        "job_id": event.job_id, "observed_at": _iso(event.observed_at),
        "actual_micro": event.actual_micro, "revision": event.revision,
    }
    prior = _get(table, key)
    if prior and event.revision < int(prior["revision"]):
        raise TauEudaciError("stale final bill revision")
    if prior and event.revision == int(prior["revision"]):
        if _content(prior) != _content(item):
            raise TauEudaciError("conflicting final bill identity")
        return False
    revision = {**item, "sk": f"FINAL#{bill_key}#REV#{event.revision:012d}"}
    try:
        table.meta.client.transact_write_items(TransactItems=[
            {"Put": {"TableName": table.name, "Item": revision, "ConditionExpression": "attribute_not_exists(pk)"}},
            {"Put": {"TableName": table.name, "Item": item,
             "ConditionExpression": "attribute_not_exists(pk)" if prior is None else "revision < :revision",
             **({"ExpressionAttributeValues": {":revision": event.revision}} if prior else {})}},
        ])
    except ClientError as error:
        latest = _get(table, key)
        if latest and int(latest["revision"]) == event.revision:
            if _content(latest) == _content(item):
                return False
            raise TauEudaciError("conflicting final bill identity") from error
        raise TauEudaciError("final bill changed concurrently; retry same revision") from error
    return True


def record_warning_handler(event: dict, context: Any) -> dict:
    """Persist an already validated service-side threshold crossing."""
    if event.get("source") != "spendctl.telemetry" or event.get("detail-type") != "EarlyWarning":
        raise ValueError("unsupported early-warning event")
    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise TypeError("event detail must be an object")
    warning = EarlyUpdateEvent(
        event_id=_required_text(detail, "event_id"), job_id=_required_text(detail, "job_id"),
        observed_at=_time(_required_text(detail, "observed_at")),
        threshold_micro=_nonnegative_int(detail, "threshold_micro"),
        provisional_micro=_nonnegative_int(detail, "provisional_micro"),
    )
    stored = record_early_event(table_from_env(), warning)
    return {"state": "STORED", "event_id": warning.event_id, "replayed": not stored, "evidence_class": "PROVISIONAL_SIGNAL"}


def record_final_bill_handler(event: dict, context: Any) -> dict:
    """Project a committed, attributed CUR settlement into the τ event ledger."""
    if event.get("source") != "spendctl.billing" or event.get("detail-type") != "ReservationSettled":
        raise ValueError("unsupported final-bill event")
    detail = event.get("detail")
    if not isinstance(detail, dict) or not detail.get("reservation_id") or not detail.get("settlement_id"):
        raise ValueError("billing detail requires reservation and settlement identifiers")
    revision = detail.get("settlement_version")
    amount = detail.get("new_actual_micro")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("billing settlement version must be positive")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValueError("billing actual amount must be a non-negative integer")
    bill = FinalBillEvent(
        bill_id=str(detail["settlement_id"]), job_id=str(detail["reservation_id"]),
        observed_at=_time(_required_text(detail, "occurred_at")), actual_micro=amount,
        revision=revision,
    )
    stored = record_final_bill(table_from_env(), bill)
    return {"state": "STORED", "bill_id": bill.bill_id, "revision": revision, "replayed": not stored, "evidence_class": "FINAL_BILL"}


def _required_text(data, name):
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _nonnegative_int(data, name):
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _time(value):
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include timezone")
    return parsed.astimezone(UTC)


def list_job_events(table: Any, job_id: str) -> dict:
    response = table.query(KeyConditionExpression="pk = :pk", ExpressionAttributeValues={":pk": f"TAU_JOB#{job_id}"}, ConsistentRead=True)
    items = response.get("Items", [])
    early = [item for item in items if item["record_type"] == "TAU_EARLY_WARNING"]
    bills = [item for item in items if item["record_type"] == "TAU_FINAL_BILL" and item["sk"].endswith("#CURRENT")]
    return {"job_id": job_id, "warning_event_ids": sorted(item["event_id"] for item in early), "warnings": sorted(early, key=lambda row: (row["observed_at"], row["event_id"])), "final_bills": sorted(bills, key=lambda row: (row["observed_at"], row["bill_id"])), "pending_final": not bool(bills)}


def _put_immutable(table, item, label):
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
        return True
    except ClientError as error:
        prior = _get(table, {"pk": item["pk"], "sk": item["sk"]})
        if prior and _content(prior) == _content(item):
            return False
        raise TauEudaciError(f"conflicting {label} identity") from error


def _get(table, key):
    return table.get_item(Key=key, ConsistentRead=True).get("Item")


def _content(item):
    return {key: item.get(key) for key in ("record_type", "event_id", "bill_id", "job_id", "observed_at", "threshold_micro", "provisional_micro", "actual_micro", "revision")}


def _encode(value): return _SER.serialize(value)
def _encode_item(item): return {key: _encode(value) for key, value in item.items()}
def _decode_item(item): return {key: _DES.deserialize(value) for key, value in item.items()} if item else None
def _iso(value): return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def table_from_env():
    name = os.environ.get("RESERVATIONS_TABLE")
    if not name:
        raise RuntimeError("RESERVATIONS_TABLE is required")
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1")).Table(name)
