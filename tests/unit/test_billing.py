import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from spendctl import billing_handlers
from spendctl.billing import (
    BillingLedgerConflict,
    BillingReconciler,
    IncompleteBillingSnapshot,
    InvalidBillingRecord,
    normalize_focus_record,
    normalize_record,
    validate_focus_manifest,
    validate_manifest,
)
from spendctl.ledger import DynamoLedger

FOCUS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "focus12"
FOCUS_COLUMNS = [name.lower() for name in (
    *billing_handlers._REQUIRED_FOCUS_COLUMNS,
    "ResourceId",
    "Tags",
)]


def focus_csv(name):
    with (FOCUS_FIXTURES / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def focus_parquet(tmp_path, name):
    pyarrow = pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")
    path = tmp_path / name.replace(".csv", ".parquet")
    parquet.write_table(pyarrow.Table.from_pylist(focus_csv(name)), path)
    return parquet.read_table(path).to_pylist()


def focus_records(rows, snapshot_id):
    return [
        normalize_focus_record(row, snapshot_id=snapshot_id, row_index=index)
        for index, row in enumerate(rows)
    ]


def focus_athena_rows(rows, columns):
    result = []
    for row in rows:
        folded = {key.casefold(): value for key, value in row.items()}
        result.append([folded.get(column.casefold(), "") for column in columns])
    return result


@pytest.fixture
def ledger():
    with mock_aws():
        db = boto3.client("dynamodb", region_name="us-east-1")
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
        db.create_table(
            TableName="Alerts",
            KeySchema=[{"AttributeName": "alert_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "alert_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        obj = DynamoLedger(db, "Budgets", "Reservations", "Alerts")
        obj.create_budget(team="alpha", period="2026-07", budget_micro=20_000_000)
        obj.reserve(
            team="alpha",
            period="2026-07",
            reservation_id="r-1",
            idempotency_key="key-1",
            requester="user-1",
            reserved_micro=5_000_000,
            record={
                "resource": {"type": "ec2", "instance_type": "t3.micro", "ebs_gb": 8},
                "planned_hours": 3,
            },
        )["reservation"]
        obj.set_workflow_start_state("r-1", "STARTING", execution_name="start-r-1")
        obj.record_workflow_execution(
            "r-1",
            execution_status="SUCCEEDED",
            instance_id="i-123",
            volume_ids=["vol-123"],
            launch_state="CREATED",
            launched_at="2026-07-01T00:00:00Z",
        )
        obj.activate(
            "r-1", launched_at="2026-07-01T00:00:00Z", instance_id="i-123", volume_ids=["vol-123"]
        )
        obj.mark_expired("r-1", expired_at="2026-07-02T00:00:00Z")
        obj.provisional_settle("r-1", 2_000_000)
        yield obj


def row(**changes):
    value = {
        "identity_line_item_id": "line-1",
        "bill_billing_period_start_date": "2026-07-01 00:00:00",
        "line_item_usage_start_date": "2026-07-01 00:00:00",
        "line_item_usage_end_date": "2026-07-01 01:00:00",
        "line_item_usage_account_id": "123456789012",
        "product_product_code": "AmazonEC2",
        "line_item_usage_type": "BoxUsage:t3.micro",
        "line_item_operation": "RunInstances",
        "line_item_resource_id": "i-123",
        "line_item_line_item_type": "Usage",
        "line_item_currency_code": "USD",
        "line_item_unblended_cost": "0.0000015",
        "resource_tags_user_reservation_id": None,
        "resource_tags_user_team": "alpha",
    }
    value.update(changes)
    return value


def test_normalize_uses_decimal_half_even_and_adjustments_are_classified():
    record = normalize_record(row(), object_id="export/manifest#rev-1")
    assert record.amount_micro == 2  # 1.5 micro-USD rounds half-even to 2.
    credit = normalize_record(
        row(
            identity_line_item_id="line-credit",
            line_item_unblended_cost="-0.05",
            line_item_line_item_type="Credit",
        ),
        object_id="manifest",
    )
    assert credit.amount_micro == -50_000
    assert credit.classification == "ADJUSTMENT_EXCLUDED"


def test_schema_validation_and_manifest_fail_closed():
    with pytest.raises(InvalidBillingRecord, match="identity_line_item_id"):
        normalize_record(row(identity_line_item_id=None), object_id="manifest")
    assert validate_manifest(
        {
            "assemblyId": "rev-1",
            "createTimestamp": "2026-08-01T00:00:00Z",
            "billingPeriod": "202607",
            "dataFilePaths": ["part.parquet"],
        }
    ) == ("2026-07", "rev-1", "2026-08-01T00:00:00.000000Z")
    with pytest.raises(InvalidBillingRecord):
        validate_manifest({"billingPeriod": "202607", "dataFilePaths": []})


def test_reconcile_attribution_revision_and_duplicate_are_atomic(ledger):
    reconciler = BillingReconciler(ledger)
    records = [normalize_record(row(line_item_unblended_cost="1.25"), object_id="rev-1")]
    first = reconciler.reconcile(
        records,
        billing_period="2026-07",
        source_revision="rev-1",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    assert first.adjustments[0]["adjustment_micro"] == -750_000
    replay = reconciler.reconcile(
        records,
        billing_period="2026-07",
        source_revision="rev-1",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    assert replay.adjustments[0]["replayed"] is True
    revised = [normalize_record(row(line_item_unblended_cost="3.00"), object_id="rev-2")]
    second = reconciler.reconcile(
        revised,
        billing_period="2026-07",
        source_revision="rev-2",
        source_revision_order="2026-08-02T00:00:00Z",
        complete=True,
    )
    assert second.adjustments[0]["previous_actual_micro"] == 1_250_000
    assert second.adjustments[0]["adjustment_micro"] == 1_750_000
    current = ledger.get_reservation("r-1")
    assert current["final_actual_micro"] == 3_000_000
    budget = ledger.get_budget("alpha", "2026-07")
    assert (
        budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"]
        == budget["budget_micro"]
    )
    assert budget["spent_micro"] == 3_000_000
    assert (
        reconciler.reconcile(
            records,
            billing_period="2026-07",
            source_revision="rev-1",
            source_revision_order="2026-08-01T00:00:00Z",
            complete=True,
        ).adjustments[0]["replayed"]
        is True
    )
    assert ledger.get_reservation("r-1")["final_actual_micro"] == 3_000_000
    with pytest.raises(BillingLedgerConflict, match="r-1"):
        reconciler.reconcile(
            [normalize_record(row(line_item_unblended_cost="1.00"), object_id="late-old")],
            billing_period="2026-07",
            source_revision="late-old",
            source_revision_order="2026-07-31T23:59:00Z",
            complete=True,
        )


def test_exact_resource_mapping_team_conflict_and_unknown_stay_unattributed(ledger):
    reconciler = BillingReconciler(ledger)
    found = reconciler.reconcile(
        [normalize_record(row(line_item_unblended_cost="1.50"), object_id="rev")],
        billing_period="2026-07",
        source_revision="rev",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    assert found.processed == 1
    wrong_team = normalize_record(
        row(
            identity_line_item_id="line-2",
            resource_tags_user_reservation_id="r-1",
            resource_tags_user_team="beta",
        ),
        object_id="rev",
    )
    unknown = normalize_record(
        row(
            identity_line_item_id="line-3",
            line_item_resource_id="i-unknown",
            resource_tags_user_reservation_id=None,
        ),
        object_id="rev",
    )
    result = reconciler.reconcile(
        [wrong_team, unknown],
        billing_period="2026-07",
        source_revision="rev-2",
        source_revision_order="2026-08-02T00:00:00Z",
        complete=True,
    )
    assert result.unattributed == 2
    assert ledger.get_reservation("r-1")["final_actual_micro"] == 1_500_000


def test_exact_reservation_tag_is_attributed_and_unknown_tag_is_not(ledger):
    reconciler = BillingReconciler(ledger)
    tagged = normalize_record(
        row(
            identity_line_item_id="tagged",
            line_item_resource_id=None,
            resource_tags_user_reservation_id="r-1",
        ),
        object_id="manifest-tag",
    )
    result = reconciler.reconcile(
        [tagged],
        billing_period="2026-07",
        source_revision="rev-tag",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    assert result.processed == 1
    unknown_tag = normalize_record(
        row(
            identity_line_item_id="bad-tag",
            line_item_resource_id=None,
            resource_tags_user_reservation_id="missing-reservation",
        ),
        object_id="manifest-tag",
    )
    result = reconciler.reconcile(
        [unknown_tag],
        billing_period="2026-07",
        source_revision="rev-tag-2",
        source_revision_order="2026-08-02T00:00:00Z",
        complete=True,
    )
    assert result.unattributed == 1


def test_cross_period_runtime_remains_unattributed_without_partial_refund(ledger):
    ledger.db.update_item(
        TableName="Reservations",
        Key={"pk": {"S": "RES#r-1"}, "sk": {"S": "META"}},
        UpdateExpression="SET launched_at = :start, expires_at = :end",
        ExpressionAttributeValues={
            ":start": {"S": "2026-07-31T23:30:00Z"},
            ":end": {"S": "2026-08-01T02:30:00Z"},
        },
    )
    record = normalize_record(row(line_item_unblended_cost="1.00"), object_id="cross-period")
    result = BillingReconciler(ledger).reconcile(
        [record],
        billing_period="2026-07",
        source_revision="cross-period",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    assert result.unattributed == 1
    assert "spans billing periods" in result.reasons[0]["reason"]
    reservation = ledger.get_reservation("r-1")
    assert reservation["settlement_stage"] == "PROVISIONAL"
    budget = ledger.get_budget("alpha", "2026-07")
    assert (
        budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"]
        == budget["budget_micro"]
    )


def test_explicit_attributed_zero_usage_is_valid_but_absence_is_not(ledger):
    record = normalize_record(row(line_item_unblended_cost="0.00"), object_id="zero-row")
    result = BillingReconciler(ledger).reconcile(
        [record],
        billing_period="2026-07",
        source_revision="zero-row-rev",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    assert result.processed == 1
    reservation = ledger.get_reservation("r-1")
    assert reservation["final_actual_micro"] == 0
    budget = ledger.get_budget("alpha", "2026-07")
    assert budget["spent_micro"] == 0
    assert budget["available_micro"] == budget["budget_micro"]
    assert (
        budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"]
        == budget["budget_micro"]
    )


def test_incomplete_snapshot_never_changes_ledger(ledger):
    with pytest.raises(IncompleteBillingSnapshot):
        BillingReconciler(ledger).reconcile(
            [],
            billing_period="2026-07",
            source_revision="rev",
            source_revision_order="2026-08-01T00:00:00Z",
            complete=False,
        )
    assert ledger.get_reservation("r-1")["settlement_stage"] == "PROVISIONAL"
    no_rows = BillingReconciler(ledger).reconcile(
        [],
        billing_period="2026-07",
        source_revision="empty-but-complete",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    assert no_rows.processed == 0
    assert ledger.get_reservation("r-1")["settlement_stage"] == "PROVISIONAL"


def test_final_correction_on_closed_budget_preserves_terminal_status_and_invariant(ledger):
    reconciler = BillingReconciler(ledger)
    first = normalize_record(row(line_item_unblended_cost="1.00"), object_id="close-1")
    reconciler.reconcile(
        [first],
        billing_period="2026-07",
        source_revision="close-1",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    ledger.close_budget(team="alpha", period="2026-07")
    revised = normalize_record(row(line_item_unblended_cost="1.25"), object_id="close-2")
    reconciler.reconcile(
        [revised],
        billing_period="2026-07",
        source_revision="close-2",
        source_revision_order="2026-08-02T00:00:00Z",
        complete=True,
    )
    budget = ledger.get_budget("alpha", "2026-07")
    assert budget["status"] == "CLOSED"
    assert budget["spent_micro"] == 1_250_000
    assert (
        budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"]
        == budget["budget_micro"]
    )


def test_m6_cleanup_release_can_receive_late_billing_correction_on_closed_budget(ledger):
    # Settle the fixture reservation first so the period can be safely closed.
    BillingReconciler(ledger).reconcile(
        [normalize_record(row(line_item_unblended_cost="1.00"), object_id="r1")],
        billing_period="2026-07",
        source_revision="r1",
        source_revision_order="2026-08-01T00:00:00Z",
        complete=True,
    )
    ledger.reserve(
        team="alpha",
        period="2026-07",
        reservation_id="r-2",
        idempotency_key="key-2",
        requester="user-1",
        reserved_micro=1_000_000,
        record={
            "resource": {"type": "ec2", "instance_type": "t3.micro", "ebs_gb": 8},
            "planned_hours": 2,
        },
    )
    ledger.set_workflow_start_state("r-2", "STARTING", execution_name="start-r-2")
    ledger.set_workflow_start_state("r-2", "STARTED")
    ledger.record_workflow_execution(
        "r-2",
        execution_status="FAILED",
        cleanup_state="COMPLETE",
        instance_id="i-2",
        volume_ids=["vol-2"],
        launch_state="CREATED",
        launched_at="2026-07-03T00:00:00Z",
        terminated_at="2026-07-03T01:00:00Z",
    )
    ledger.release_after_confirmed_cleanup("r-2", 200_000)
    ledger.close_budget(team="alpha", period="2026-07")
    record = normalize_record(
        row(
            identity_line_item_id="r2-line",
            line_item_resource_id="i-2",
            line_item_unblended_cost="0.30",
        ),
        object_id="r2-billing",
    )
    BillingReconciler(ledger).reconcile(
        [record],
        billing_period="2026-07",
        source_revision="r2-rev",
        source_revision_order="2026-08-02T00:00:00Z",
        complete=True,
    )
    reservation = ledger.get_reservation("r-2")
    assert reservation["status"] == "RELEASED"
    assert reservation["final_actual_micro"] == 300_000
    budget = ledger.get_budget("alpha", "2026-07")
    assert budget["status"] == "CLOSED"
    assert budget["spent_micro"] == 1_300_000
    assert (
        budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"]
        == budget["budget_micro"]
    )


def test_failed_financial_transaction_leaves_budget_and_audit_unchanged(ledger, monkeypatch):
    before = ledger.get_budget("alpha", "2026-07")

    def fail_transaction(**_kwargs):
        raise ClientError(
            {"Error": {"Code": "TransactionCanceledException", "Message": "injected failure"}},
            "TransactWriteItems",
        )

    monkeypatch.setattr(ledger.db, "transact_write_items", fail_transaction)
    record = normalize_record(row(line_item_unblended_cost="1.00"), object_id="failure")
    with pytest.raises(BillingLedgerConflict):
        BillingReconciler(ledger).reconcile(
            [record],
            billing_period="2026-07",
            source_revision="failure-rev",
            source_revision_order="2026-08-01T00:00:00Z",
            complete=True,
        )
    after = ledger.get_budget("alpha", "2026-07")
    assert after["available_micro"] == before["available_micro"]
    assert after["reserved_micro"] == before["reserved_micro"]
    assert after["spent_micro"] == before["spent_micro"]
    assert (
        after["available_micro"] + after["reserved_micro"] + after["spent_micro"]
        == after["budget_micro"]
    )
    assert ledger.get_reservation("r-1")["settlement_stage"] == "PROVISIONAL"


def test_concurrent_same_snapshot_is_applied_once(ledger):
    records = [normalize_record(row(line_item_unblended_cost="1.00"), object_id="same")]

    def reconcile():
        return BillingReconciler(ledger).reconcile(
            records,
            billing_period="2026-07",
            source_revision="same-rev",
            source_revision_order="2026-08-01T00:00:00Z",
            complete=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reconcile(), range(2)))
    assert sum(result.adjustments[0]["replayed"] is False for result in results) == 1
    assert ledger.get_reservation("r-1")["final_actual_micro"] == 1_000_000
    budget = ledger.get_budget("alpha", "2026-07")
    assert budget["spent_micro"] == 1_000_000
    assert (
        budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"]
        == budget["budget_micro"]
    )


class _Paginator:
    def __init__(self, result):
        self.result = result

    def paginate(self, **_kwargs):
        return iter([self.result])


class _Athena:
    def __init__(self, status="SUCCEEDED", columns=None, rows=None):
        self.status = status
        self.columns = columns or [
            *billing_handlers._REQUIRED_CUR_COLUMNS,
            "line_item_unblended_cost",
        ]
        self.rows = rows or []
        self.sql = None

    def start_query_execution(self, **kwargs):
        self.sql = kwargs["QueryString"]
        return {"QueryExecutionId": "query-1"}

    def get_query_execution(self, **_kwargs):
        return {
            "QueryExecution": {
                "Status": {"State": self.status, "StateChangeReason": "test failure"}
            }
        }

    def get_paginator(self, _name):
        columns = [{"Name": name} for name in self.columns]
        data = [{"VarCharValue": name} for name in self.columns]
        result_rows = [{"Data": data}]
        result_rows.extend(
            {"Data": [{"VarCharValue": value} for value in row]} for row in self.rows
        )
        return _Paginator(
            {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": columns}, "Rows": result_rows}}
        )


class _Glue:
    def __init__(self, columns, partitions=None):
        self.columns = columns
        self.partitions = partitions or []

    def get_table(self, **_kwargs):
        return {
            "Table": {
                "StorageDescriptor": {"Columns": [{"Name": name} for name in self.columns]},
                "PartitionKeys": [{"Name": name} for name in self.partitions],
            }
        }


def test_athena_query_is_period_filtered_and_uses_actual_glue_columns(monkeypatch):
    athena = _Athena()
    glue_columns = [*billing_handlers._REQUIRED_CUR_COLUMNS, "line_item_unblended_cost"]
    glue = _Glue(glue_columns, partitions=["bill_billing_period"])
    monkeypatch.setenv("CUR_TABLE", "fixture_table")
    monkeypatch.setenv("ATHENA_WORKGROUP", "test-workgroup")
    monkeypatch.setattr(billing_handlers, "_emit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        billing_handlers.boto3, "client", lambda name, **_kwargs: glue if name == "glue" else athena
    )
    assert billing_handlers._query_rows(athena, "2026-07") == []
    assert '"spendctl_billing"."fixture_table"' in athena.sql
    assert "2026-07" in athena.sql
    assert "\"bill_billing_period\" = '2026-07'" in athena.sql


def test_athena_schema_mismatch_and_query_failure_are_visible(monkeypatch):
    monkeypatch.setenv("CUR_TABLE", "fixture_table")
    monkeypatch.setenv("ATHENA_WORKGROUP", "test-workgroup")
    monkeypatch.setattr(billing_handlers, "_emit_event", lambda *_args, **_kwargs: None)
    athena = _Athena()
    monkeypatch.setattr(
        billing_handlers.boto3,
        "client",
        lambda name, **_kwargs: _Glue(["wrong_column"]) if name == "glue" else athena,
    )
    with pytest.raises(InvalidBillingRecord, match="missing required columns"):
        billing_handlers._query_rows(athena, "2026-07")
    monkeypatch.setattr(
        billing_handlers.boto3,
        "client",
        lambda name, **_kwargs: (
            _Glue([*billing_handlers._REQUIRED_CUR_COLUMNS, "line_item_unblended_cost"])
            if name == "glue"
            else _Athena("FAILED")
        ),
    )
    with pytest.raises(RuntimeError, match="Athena query FAILED"):
        billing_handlers._query_rows(_Athena("FAILED"), "2026-07")


def test_focus_normalizer_maps_billed_cost_arns_tags_and_charges():
    rows = focus_csv("snapshot-v1.csv")
    usage = normalize_focus_record(rows[0], snapshot_id="exec-v1", row_index=0)
    assert usage.amount_micro == 1_250_000
    assert usage.cost_field == "BilledCost"
    assert usage.billing_period == "2026-07"
    assert usage.service_code == "AmazonEC2"
    assert usage.classification == "USAGE_CHARGE"
    assert usage.reservation_tag is None
    arn = normalize_focus_record(rows[7], snapshot_id="exec-v1", row_index=7)
    assert arn.resource_id == "i-123"
    tagged = normalize_focus_record(
        {**rows[0], "Tags": "{reservation-id=r-1, team=alpha}"},
        snapshot_id="exec-v1",
        row_index=8,
    )
    assert tagged.reservation_tag == "r-1"
    assert tagged.team_tag == "alpha"
    arrow_tags = normalize_focus_record(
        {**rows[0], "Tags": [("reservation-id", "r-1"), ("team", "alpha")]},
        snapshot_id="exec-v1",
        row_index=10,
    )
    assert arrow_tags.reservation_tag == "r-1"
    empty_arrow_tags = normalize_focus_record(
        {**rows[0], "Tags": []}, snapshot_id="exec-v1", row_index=11
    )
    assert empty_arrow_tags.reservation_tag is None
    lowered = {key.lower(): value for key, value in rows[0].items()}
    assert normalize_focus_record(lowered, snapshot_id="exec-v1", row_index=9).amount_micro == 1_250_000
    credit = normalize_focus_record(rows[3], snapshot_id="exec-v1", row_index=3)
    assert credit.classification == "ADJUSTMENT_EXCLUDED"
    tax = normalize_focus_record(rows[4], snapshot_id="exec-v1", row_index=4)
    assert tax.classification == "NON_USAGE_EXCLUDED"
    rds = normalize_focus_record(rows[5], snapshot_id="exec-v1", row_index=5)
    assert rds.classification == "OUT_OF_SCOPE"


def test_focus_manifest_requires_delivery_timestamp_and_scoped_files():
    prefix = "focus/billing_period=2026-07/2026-08-01T03:32:00.757Z-exec-1/"
    key = prefix + "CALIBUR_FOCUS_12-Manifest.json"
    manifest = {
        "executionId": "exec-1",
        "dataFiles": [f"s3://test-bucket/{prefix}CALIBUR_FOCUS_12-00001.snappy.parquet"],
        "additionalOutputFiles": [],
    }
    snapshot = validate_focus_manifest(manifest, object_key=key)
    assert snapshot.billing_period == "2026-07"
    assert snapshot.revision == "exec-1"
    assert snapshot.revision_order == "2026-08-01T03:32:00.757000Z"
    assert snapshot.data_files == (
        f"s3://test-bucket/{prefix}CALIBUR_FOCUS_12-00001.snappy.parquet",
    )
    with pytest.raises(InvalidBillingRecord, match="does not match"):
        validate_focus_manifest({**manifest, "executionId": "exec-2"}, object_key=key)
    with pytest.raises(InvalidBillingRecord, match="delivery timestamp"):
        validate_focus_manifest(manifest, object_key=prefix + "nested/Manifest.json")
    with pytest.raises(InvalidBillingRecord, match="outside its delivery"):
        validate_focus_manifest(
            {
                **manifest,
                "dataFiles": [
                    "s3://test-bucket/focus/billing_period=2026-07/other/part.parquet"
                ],
            },
            object_key=key,
        )
    with pytest.raises(IncompleteBillingSnapshot):
        validate_focus_manifest({**manifest, "additionalOutputFiles": ["part.csv"]}, object_key=key)
    with pytest.raises(IncompleteBillingSnapshot):
        validate_focus_manifest({**manifest, "dataFiles": []}, object_key=key)


def test_focus_snapshot_revisions_replace_prior_billed_amount(ledger, tmp_path):
    reconciler = BillingReconciler(ledger)
    first_rows = focus_csv("snapshot-v1.csv")
    first = reconciler.reconcile(
        focus_records(first_rows, "exec-v1"),
        billing_period="2026-07",
        source_revision="exec-v1",
        source_revision_order="2026-08-01T03:32:00.757000Z",
        complete=True,
        stale_revision_policy="skip",
    )
    assert first.processed == 1
    assert first.duplicates == 0  # FOCUS duplicate rows are valid, distinct line items.
    assert first.excluded == 3
    assert first.unattributed == 1
    assert first.adjustments[0]["new_actual_micro"] == 3_250_000
    budget = ledger.get_budget("alpha", "2026-07")
    assert budget["spent_micro"] == 3_250_000
    assert ledger.get_reservation("r-1")["final_actual_micro"] == 3_250_000

    revised_rows = focus_parquet(tmp_path, "snapshot-v2.csv")
    revised = reconciler.reconcile(
        focus_records(revised_rows, "exec-v2"),
        billing_period="2026-07",
        source_revision="exec-v2",
        source_revision_order="2026-08-02T03:32:00.757000Z",
        complete=True,
        stale_revision_policy="skip",
    )
    assert revised.adjustments[0]["previous_actual_micro"] == 3_250_000
    assert revised.adjustments[0]["new_actual_micro"] == 2_750_000
    assert revised.adjustments[0]["adjustment_micro"] == -500_000
    budget = ledger.get_budget("alpha", "2026-07")
    assert budget["spent_micro"] == 2_750_000
    assert budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"] == (
        budget["budget_micro"]
    )
    assert ledger.get_reservation("r-1")["final_actual_micro"] == 2_750_000

    stale = reconciler.reconcile(
        focus_records(focus_csv("snapshot-v0.csv"), "exec-v0"),
        billing_period="2026-07",
        source_revision="exec-v0",
        source_revision_order="2026-07-31T00:00:00.000000Z",
        complete=True,
        stale_revision_policy="skip",
    )
    assert stale.processed == 0
    assert stale.skipped == 1
    assert ledger.get_budget("alpha", "2026-07")["spent_micro"] == 2_750_000


def test_focus_athena_query_is_scoped_to_manifest_files(monkeypatch):
    monkeypatch.setenv("BILLING_TABLE", "focus_table")
    monkeypatch.setenv("ATHENA_WORKGROUP", "test-workgroup")
    monkeypatch.setattr(billing_handlers, "_emit_event", lambda *_args, **_kwargs: None)
    data_file = (
        "s3://test-bucket/focus/billing_period=2026-07/"
        "2026-08-01T03:32:00.757Z-exec-1/CALIBUR_FOCUS_12-00001.snappy.parquet"
    )
    athena = _Athena(
        columns=FOCUS_COLUMNS,
        rows=focus_athena_rows(focus_csv("snapshot-v1.csv"), FOCUS_COLUMNS),
    )
    glue = _Glue(FOCUS_COLUMNS, partitions=["billing_period"])
    monkeypatch.setattr(
        billing_handlers.boto3,
        "client",
        lambda name, **_kwargs: glue if name == "glue" else athena,
    )
    rows = billing_handlers._query_rows(
        athena, "2026-07", schema="focus", data_files=[data_file]
    )
    assert len(rows) == 8
    assert '"spendctl_billing"."focus_table"' in athena.sql
    assert "\"billing_period\" = '2026-07'" in athena.sql
    assert f'"$path" IN (\'{data_file}\')' in athena.sql
    assert '"billedcost"' in athena.sql
    assert '"resourceid"' in athena.sql


def test_focus_manifest_handler_updates_dynamodb_budget_on_revision(ledger, monkeypatch):
    bucket = "focus-test-bucket"
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("CUR_EXPORT_BUCKET", bucket)
    monkeypatch.setenv("CUR_EXPORT_PREFIX", "focus/")
    monkeypatch.setenv("BILLING_TABLE", "focus_table")
    monkeypatch.setenv("BILLING_SCHEMA", "focus")
    monkeypatch.setenv("ATHENA_WORKGROUP", "test-workgroup")
    monkeypatch.setenv("BILLING_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("BILLING_FINALIZATION_DELAY_DAYS", "0")
    monkeypatch.setenv("BUDGETS_TABLE", "Budgets")
    monkeypatch.setenv("RESERVATIONS_TABLE", "Reservations")
    monkeypatch.setenv("ALERTS_TABLE", "Alerts")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=bucket)
    athena = _Athena(
        columns=FOCUS_COLUMNS,
        rows=focus_athena_rows(focus_csv("snapshot-v1.csv"), FOCUS_COLUMNS),
    )
    glue = _Glue(FOCUS_COLUMNS, partitions=["billing_period"])
    original_client = billing_handlers.boto3.client

    def client(name, **kwargs):
        if name == "glue":
            return glue
        if name == "athena":
            return athena
        return original_client(name, **kwargs)

    monkeypatch.setattr(billing_handlers.boto3, "client", client)
    events = []
    monkeypatch.setattr(
        billing_handlers,
        "_emit_event",
        lambda name, _detail, **_kwargs: events.append(name),
    )

    def manifest(delivery_stamp, execution_id):
        delivery = (
            "focus/billing_period=2026-07/"
            f"{delivery_stamp}-{execution_id}/"
        )
        key = delivery + "CALIBUR_FOCUS_12-Manifest.json"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(
                {
                    "executionId": execution_id,
                    "dataFiles": [
                        f"s3://{bucket}/{delivery}CALIBUR_FOCUS_12-00001.snappy.parquet"
                    ],
                    "additionalOutputFiles": [],
                }
            ).encode(),
        )
        return key

    def ingest(key):
        return billing_handlers.billing_manifest_handler(
            {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]},
            None,
        )

    first = ingest(manifest("2026-08-01T03:32:00.757Z", "exec-v1"))
    assert first["manifests"][0]["state"] == "RECONCILED"
    assert first["manifests"][0]["schema"] == "focus"
    assert first["manifests"][0]["adjustments"][0]["new_actual_micro"] == 3_250_000
    assert ledger.get_budget("alpha", "2026-07")["spent_micro"] == 3_250_000
    assert "BillingReconciled" in events
    assert "ReservationSettled" in events

    athena.rows = focus_athena_rows(focus_csv("snapshot-v2.csv"), FOCUS_COLUMNS)
    revised = ingest(manifest("2026-08-02T03:32:00.757Z", "exec-v2"))
    adjustment = revised["manifests"][0]["adjustments"][0]
    assert adjustment["previous_actual_micro"] == 3_250_000
    assert adjustment["new_actual_micro"] == 2_750_000
    assert adjustment["adjustment_micro"] == -500_000
    budget = ledger.get_budget("alpha", "2026-07")
    assert budget["spent_micro"] == 2_750_000
    assert budget["available_micro"] + budget["reserved_micro"] + budget["spent_micro"] == (
        budget["budget_micro"]
    )
    assert ledger.get_reservation("r-1")["final_actual_micro"] == 2_750_000
