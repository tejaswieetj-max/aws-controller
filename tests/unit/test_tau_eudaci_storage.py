"""Durable τ-EUDACI storage tests with labeled synthetic software fixtures."""

import hashlib
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws
from spendctl.tau_eudaci_storage import list_job_events, record_early_event, record_final_bill

from calibur_engine.tau_eudaci import EarlyUpdateEvent, FinalBillEvent, TauEudaciError

T0 = datetime(2026, 10, 26, tzinfo=UTC)


@pytest.fixture
def table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="ap-south-1")
        yield resource.create_table(
            TableName="Reservations",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


def test_warning_and_late_final_revisions_are_durable_idempotent(table):
    warning = EarlyUpdateEvent("job-1:warning:obs-1", "job-1", T0, 100, 120)
    assert record_early_event(table, warning)
    assert not record_early_event(table, warning)
    first = FinalBillEvent("bill-1", "job-1", T0 + timedelta(days=2), 90, 0)
    assert record_final_bill(table, first)
    assert not record_final_bill(table, first)
    revised = FinalBillEvent("bill-1", "job-1", T0 + timedelta(days=3), 130, 1)
    assert record_final_bill(table, revised)
    state = list_job_events(table, "job-1")
    assert state["warning_event_ids"] == [warning.event_id]
    assert state["pending_final"] is False
    assert len(state["final_bills"]) == 1
    assert state["final_bills"][0]["actual_micro"] == 130
    bill_key = hashlib.sha256(b"bill-1").hexdigest()
    assert table.get_item(Key={"pk": "TAU_JOB#job-1", "sk": f"FINAL#{bill_key}#REV#000000000000"})["Item"]["actual_micro"] == 90


def test_conflicting_duplicate_and_stale_revision_rejected(table):
    event = FinalBillEvent("bill-1", "job-1", T0, 90, 1)
    record_final_bill(table, event)
    with pytest.raises(TauEudaciError, match="conflicting"):
        record_final_bill(table, FinalBillEvent("bill-1", "job-1", T0, 91, 1))
    with pytest.raises(TauEudaciError, match="stale"):
        record_final_bill(table, FinalBillEvent("bill-1", "job-1", T0, 80, 0))
