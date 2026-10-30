"""Evidence-chain read model over verified records already in the ledger."""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.types import TypeDeserializer

_DES = TypeDeserializer()


def trace_reservation(db: Any, reservations_table: str, reservation: dict) -> dict:
    reservation_id = reservation["reservation_id"]
    evidence = {
        "request": _link(bool(reservation.get("request_id")), "stable admission request ID"),
        "decision": _link(bool(reservation.get("decision_at") and reservation.get("calibur_proposal")), "durable admission decision details"),
        "reservation": _link(True, "atomic budget reservation"),
        "resource": _link(bool(reservation.get("instance_id") or reservation.get("volume_ids")), "recorded AWS resource identity"),
        "telemetry": _link(False, "verified telemetry records linked by reservation ID"),
        "initial_bill": _link(reservation.get("settlement_stage") in {"PROVISIONAL", "FINAL"}, "provisional or initial final settlement"),
        "billing_revisions": _link(int(reservation.get("settlement_version", 0)) > 1, "subsequent attributed CUR settlement revisions"),
        "release": _link(bool(reservation.get("released_at")), "ledger release record"),
    }
    billing = _query_partition(db, reservations_table, f"SETTLEVER#{reservation_id}")
    tau = _query_partition(db, reservations_table, f"TAU_JOB#{reservation_id}")
    evidence["billing_revisions"]["record_ids"] = [row.get("settlement_id") for row in billing]
    evidence["telemetry"]["record_ids"] = [row.get("event_id") for row in tau if row.get("record_type") == "TAU_EARLY_WARNING"]
    evidence["telemetry"]["available"] = bool(evidence["telemetry"]["record_ids"])
    evidence["initial_bill"]["record_id"] = reservation.get("final_settlement_id") or reservation.get("provisional_settlement_id")
    return {"reservation_id": reservation_id, "status": "COMPLETE" if all(value["available"] for value in evidence.values()) else "INCOMPLETE", "links": evidence, "missing_links": [key for key, value in evidence.items() if not value["available"]]}


def _link(available, requirement):
    return {"available": available, "requirement": requirement}


def _query_partition(db, table, pk):
    values = {":pk": {"S": pk}}
    response = db.query(TableName=table, KeyConditionExpression="pk = :pk", ExpressionAttributeValues=values, ConsistentRead=True)
    rows = [_decode(row) for row in response.get("Items", [])]
    while response.get("LastEvaluatedKey"):
        response = db.query(TableName=table, KeyConditionExpression="pk = :pk", ExpressionAttributeValues=values, ExclusiveStartKey=response["LastEvaluatedKey"], ConsistentRead=True)
        rows.extend(_decode(row) for row in response.get("Items", []))
    return rows


def trace_reservation_events(db, table, reservation):
    """Extend the chain with exact request, telemetry, and billing audit keys."""
    result = trace_reservation(db, table, reservation)
    reservation_id = reservation["reservation_id"]
    request_id = reservation.get("request_id")
    if request_id:
        decision = db.get_item(TableName=table, Key={"pk": {"S": f"REQUEST#{request_id}"}, "sk": {"S": "DECISION"}}, ConsistentRead=True).get("Item")
        if decision:
            result["links"]["decision"]["available"] = True
            result["links"]["decision"]["record_id"] = request_id
    audits = _query_partition(db, table, f"RES#{reservation_id}")
    result["links"]["billing_revisions"]["reconciliation_ids"] = [row.get("reconciliation_id") for row in audits if row.get("record_type") == "BILLING_RECONCILIATION"]
    result["links"]["billing_revisions"]["available"] = int(reservation.get("settlement_version", 0)) > 1
    result["status"] = "COMPLETE" if all(value["available"] for value in result["links"].values()) else "INCOMPLETE"
    result["missing_links"] = [key for key, value in result["links"].items() if not value["available"]]
    return result


def _decode(row): return {key: _DES.deserialize(value) for key, value in row.items()}


def billing_delay_history(db, table):
    """Return only rows with persisted billing-arrival and censoring semantics.

    Current billing audit records retain CUR manifest order and ledger commit
    time, but neither is a validated first-availability timestamp. Therefore
    the present schema yields an empty eligible history.
    """
    return []
