"""Generate a redacted, deterministic Moto-backed admission replay artifact."""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

import boto3
from moto import mock_aws
from spendctl.admission import AdmissionService, VerifiedIdentity
from spendctl.ledger import DynamoLedger
from spendctl.pricing import FixturePricingProvider

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "review" / "evidence" / "idempotency-demo.json"
PERIOD = "2026-10"
TEAM = "alpha"
IDEMPOTENCY_KEY = "evidence-demo-key-2026-10"
BODY = {
    "team": TEAM,
    "resource": {"type": "ec2", "instance_type": "t3.micro", "ebs_gb": 8},
    "duration_hours": 1,
    "purpose": "local evidence scenario",
}


def _make_ledger(db) -> DynamoLedger:
    db.create_table(
        TableName="Budgets",
        KeySchema=[
            {"AttributeName": "team", "KeyType": "HASH"},
            {"AttributeName": "period", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "team", "AttributeType": "S"},
            {"AttributeName": "period", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    db.create_table(
        TableName="Reservations",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return DynamoLedger(db, "Budgets", "Reservations")


def main() -> None:
    assertions = []
    with mock_aws():
        db = boto3.client("dynamodb", region_name="ap-south-1")
        ledger = _make_ledger(db)
        initial_budget_micro = 50_000_000
        ledger.create_budget(team=TEAM, period=PERIOD, budget_micro=initial_budget_micro)
        admission = AdmissionService(ledger, FixturePricingProvider())
        identity = VerifiedIdentity(team=TEAM, subject="local-demo-user")

        with redirect_stdout(io.StringIO()):
            first = admission.admit(
                BODY, identity=identity, period=PERIOD, idempotency_key=IDEMPOTENCY_KEY
            )
            replay = admission.admit(
                BODY, identity=identity, period=PERIOD, idempotency_key=IDEMPOTENCY_KEY
            )
        reservation_rows = db.scan(
            TableName="Reservations",
            FilterExpression="#kind = :reservation",
            ExpressionAttributeNames={"#kind": "record_type"},
            ExpressionAttributeValues={":reservation": {"S": "RESERVATION"}},
        )["Items"]
        budget = ledger.get_budget(TEAM, PERIOD)

        assertions.extend(
            [
                {"name": "first request admitted", "passed": first["state"] == "ADMITTED"},
                {
                    "name": "replay returns same reservation",
                    "passed": replay["reservation_id"] == first["reservation_id"],
                },
                {"name": "replay marked idempotent", "passed": replay["replayed"] is True},
                {"name": "one durable reservation row", "passed": len(reservation_rows) == 1},
                {
                    "name": "budget held once",
                    "passed": budget["reserved_micro"] == first["reserved_micro"],
                },
                {
                    "name": "budget invariant holds",
                    "passed": budget["budget_micro"]
                    == budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"],
                },
                {
                    "name": "admission layer did not start provisioning",
                    "passed": first["provisioning_started"] is False,
                },
            ]
        )
        failures = [item["name"] for item in assertions if not item["passed"]]
        if failures:
            raise AssertionError(f"evidence scenario failed assertions: {failures}")

        result = {
            "scenario": "retry-safe-admission",
            "evidence_type": "local deterministic demonstration using Moto DynamoDB mocks",
            "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "command": "PYTHONPATH=services .venv/bin/python scripts/evidence_idempotency_demo.py",
            "result": "PASS",
            "input_summary": {
                "team": TEAM,
                "period": PERIOD,
                "instance_type": BODY["resource"]["instance_type"],
                "gp3_gb": BODY["resource"]["ebs_gb"],
                "planned_hours": BODY["duration_hours"],
                "pricing_source": first["estimate"]["source"],
                "idempotency_key_sha256": hashlib.sha256(IDEMPOTENCY_KEY.encode()).hexdigest(),
                "requester": "redacted",
            },
            "observed": {
                "reservation_count": len(reservation_rows),
                "budget_hold_micro_usd": budget["reserved_micro"],
                "budget_available_micro_usd": budget["available_micro"],
                "budget_spent_micro_usd": budget["spent_micro"],
                "budget_total_micro_usd": budget["budget_micro"],
                "first_admission_status": first["state"],
                "replay_http_status": replay["http_status"],
                "replay_same_reservation": replay["reservation_id"] == first["reservation_id"],
                "workflow_identity": None,
                "workflow_count": None,
                "workflow_observation": "Not exercised; AdmissionService does not start Step Functions.",
            },
            "assertions": {"passed": assertions, "failed": []},
            "limitations": [
                "Moto is a local AWS-service mock; this is not a live DynamoDB or AWS verification.",
                "The scenario proves the AdmissionService ledger path, not API Gateway authentication or workflow execution.",
                "Fixture pricing is illustrative and unverified against AWS prices.",
            ],
        }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
