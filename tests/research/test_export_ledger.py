"""Tests for scripts/export_ledger.py.

All tests use Moto-backed DynamoDB or tmp_path; no real AWS calls.
No fixture values enter empirical research results.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so we can import export_ledger directly.
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
SERVICES_DIR = Path(__file__).resolve().parents[2] / "services"
for d in (SCRIPTS_DIR, SERVICES_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from export_ledger import (
    ExportError,
    _dynamo_item_to_python,
    _verify_identity,
)

# ===========================================================================
# 1. DynamoDB wire-format normaliser
# ===========================================================================

class TestDynamoItemToPython:
    def test_string_attribute(self):
        assert _dynamo_item_to_python({"k": {"S": "hello"}}) == {"k": "hello"}

    def test_integer_numeric(self):
        assert _dynamo_item_to_python({"n": {"N": "12345"}}) == {"n": 12345}

    def test_decimal_numeric_stays_string(self):
        result = _dynamo_item_to_python({"c": {"N": "18.77"}})
        # Decimal values returned as strings to avoid float precision loss.
        assert result["c"] == "18.77"

    def test_bool_true(self):
        assert _dynamo_item_to_python({"b": {"BOOL": True}}) == {"b": True}

    def test_null_is_none(self):
        assert _dynamo_item_to_python({"x": {"NULL": True}}) == {"x": None}

    def test_list_attribute(self):
        result = _dynamo_item_to_python({"l": {"L": [{"S": "a"}, {"N": "1"}]}})
        assert result["l"] == ["a", 1]

    def test_map_attribute(self):
        result = _dynamo_item_to_python({"m": {"M": {"k": {"S": "v"}}}})
        assert result["m"] == {"k": "v"}

    def test_already_native_dict_passthrough(self):
        """Resource client returns native Python; normaliser must not corrupt it."""
        native = {"team": "alpha", "reserved_micro": 14000}
        assert _dynamo_item_to_python(native) == native

    def test_string_set(self):
        result = _dynamo_item_to_python({"ss": {"SS": ["a", "b"]}})
        assert set(result["ss"]) == {"a", "b"}

    def test_nested_map_with_list(self):
        item = {"resource": {"M": {"type": {"S": "ec2"}, "ebs_gb": {"N": "8"}}}}
        result = _dynamo_item_to_python(item)
        assert result["resource"] == {"type": "ec2", "ebs_gb": 8}

    def test_micro_usd_integer_not_float(self):
        """reserved_micro must be an int, not a float or Decimal string."""
        result = _dynamo_item_to_python({"reserved_micro": {"N": "1100000"}})
        assert result["reserved_micro"] == 1100000
        assert isinstance(result["reserved_micro"], int)


# ===========================================================================
# 2. verify_identity guard
# ===========================================================================

class TestVerifyIdentity:
    def test_matching_account_returns_account(self):
        class FakeSts:
            def get_caller_identity(self):
                return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/test"}
        account = _verify_identity(FakeSts(), "123456789012")
        assert account == "123456789012"

    def test_mismatched_account_raises_export_error(self):
        class FakeSts:
            def get_caller_identity(self):
                return {"Account": "999999999999", "Arn": "arn:aws:iam::999999999999:user/test"}
        with pytest.raises(ExportError, match="does not match"):
            _verify_identity(FakeSts(), "123456789012")


# ===========================================================================
# 3. CLI argument validation (dry-run, no AWS calls)
# ===========================================================================

class TestCliArguments:
    def _run(self, args: list[str]) -> int:
        """Run export_ledger.main() with given argv. Returns exit code."""
        import export_ledger
        orig = sys.argv
        sys.argv = ["export_ledger.py"] + args
        try:
            with pytest.raises(SystemExit) as exc:
                export_ledger.main()
            return exc.value.code
        finally:
            sys.argv = orig

    def test_dry_run_exits_zero(self):
        # dry-run returns normally (no sys.exit), so we just call main() directly.
        import export_ledger
        orig = sys.argv
        sys.argv = ["export_ledger.py", "--period", "2026-10", "--dry-run"]
        try:
            export_ledger.main()  # must return normally, no exception
        finally:
            sys.argv = orig

    def test_invalid_period_format_exits(self):
        code = self._run(["--period", "202610", "--dry-run"])
        assert code != 0 or code is None  # SystemExit with error message

    def test_missing_confirm_account_exits(self, tmp_path):
        code = self._run([
            "--period", "2026-10",
            "--output", str(tmp_path / "out.json"),
        ])
        assert code != 0

    def test_confirm_account_wrong_length_exits(self, tmp_path):
        code = self._run([
            "--period", "2026-10",
            "--confirm-account", "12345",
            "--output", str(tmp_path / "out.json"),
        ])
        assert code != 0

    def test_missing_output_without_dry_run_exits(self):
        code = self._run([
            "--period", "2026-10",
            "--confirm-account", "123456789012",
        ])
        assert code != 0

    def test_existing_output_file_exits(self, tmp_path):
        existing = tmp_path / "out.json"
        existing.write_text("{}")
        code = self._run([
            "--period", "2026-10",
            "--confirm-account", "123456789012",
            "--output", str(existing),
        ])
        assert code != 0


# ===========================================================================
# 4. Moto-backed DynamoDB export
# ===========================================================================

try:
    HAS_MOTO = importlib.util.find_spec("moto") is not None
except ImportError:
    HAS_MOTO = False


@pytest.mark.skipif(not HAS_MOTO, reason="moto not installed")
class TestMotoExport:
    """End-to-end export test with Moto-backed DynamoDB."""

    def test_export_empty_table_writes_empty_arrays(self, tmp_path):
        import boto3
        import export_ledger
        from moto import mock_aws

        with mock_aws():
            client = boto3.client("dynamodb", region_name="ap-south-1")
            client.create_table(
                TableName="spendctl-dev-reservations",
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            sts = boto3.client("sts", region_name="ap-south-1")
            account = sts.get_caller_identity()["Account"]
            out = tmp_path / "export.json"
            orig = sys.argv
            sys.argv = [
                "export_ledger.py",
                "--period", "2026-10",
                "--confirm-account", account,
                "--output", str(out),
                "--region", "ap-south-1",
            ]
            try:
                export_ledger.main()  # success path: returns normally
            finally:
                sys.argv = orig

        assert out.is_file()
        data = json.loads(out.read_text())
        assert data["period"] == "2026-10"
        assert data["reservations"] == []
        assert data["billing_audits"] == []
        assert "export_at_utc" in data

    def test_export_with_reservation_record(self, tmp_path):
        """One RESERVATION record in DynamoDB must appear in the export."""
        import boto3
        import export_ledger
        from moto import mock_aws

        with mock_aws():
            client = boto3.client("dynamodb", region_name="ap-south-1")
            client.create_table(
                TableName="spendctl-dev-reservations",
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            # Insert a minimal reservation record.
            client.put_item(
                TableName="spendctl-dev-reservations",
                Item={
                    "pk": {"S": "RESERVATION#rsv_abc123"},
                    "sk": {"S": "RESERVATION#rsv_abc123"},
                    "record_type": {"S": "RESERVATION"},
                    "reservation_id": {"S": "rsv_abc123"},
                    "budget_period": {"S": "2026-10"},
                    "team": {"S": "alpha"},
                    "reserved_micro": {"N": "14000"},
                    "settlement_stage": {"S": "FINAL"},
                    "price_snapshot_id": {"S": "snap-001"},
                    "price_fetched_at": {"S": "2026-10-25T00:00:00Z"},
                    "compute_micro": {"N": "12000"},
                    "storage_micro": {"N": "2000"},
                },
            )
            sts = boto3.client("sts", region_name="ap-south-1")
            account = sts.get_caller_identity()["Account"]

            out = tmp_path / "export.json"
            orig = sys.argv
            sys.argv = [
                "export_ledger.py",
                "--period", "2026-10",
                "--confirm-account", account,
                "--output", str(out),
                "--region", "ap-south-1",
            ]
            try:
                export_ledger.main()  # success path: returns normally
            finally:
                sys.argv = orig

        assert out.is_file()
        data = json.loads(out.read_text())
        assert len(data["reservations"]) == 1
        res = data["reservations"][0]
        assert res["reservation_id"] == "rsv_abc123"
        assert res["reserved_micro"] == 14000

    def test_export_excludes_records_from_other_periods(self, tmp_path):
        """Records for a different period must not appear in the export."""
        import boto3
        import export_ledger
        from moto import mock_aws

        with mock_aws():
            client = boto3.client("dynamodb", region_name="ap-south-1")
            client.create_table(
                TableName="spendctl-dev-reservations",
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            client.put_item(
                TableName="spendctl-dev-reservations",
                Item={
                    "pk": {"S": "RESERVATION#rsv_other"},
                    "sk": {"S": "RESERVATION#rsv_other"},
                    "record_type": {"S": "RESERVATION"},
                    "reservation_id": {"S": "rsv_other"},
                    "budget_period": {"S": "2026-09"},  # different period
                    "team": {"S": "alpha"},
                    "reserved_micro": {"N": "99000"},
                    "settlement_stage": {"S": "FINAL"},
                    "price_snapshot_id": {"S": "snap-old"},
                    "price_fetched_at": {"S": "2026-09-01T00:00:00Z"},
                    "compute_micro": {"N": "80000"},
                    "storage_micro": {"N": "19000"},
                },
            )
            sts = boto3.client("sts", region_name="ap-south-1")
            account = sts.get_caller_identity()["Account"]

            out = tmp_path / "export.json"
            orig = sys.argv
            sys.argv = [
                "export_ledger.py",
                "--period", "2026-10",  # requesting 2026-10
                "--confirm-account", account,
                "--output", str(out),
                "--region", "ap-south-1",
            ]
            try:
                export_ledger.main()  # success path: returns normally
            finally:
                sys.argv = orig

        assert out.is_file()
        data = json.loads(out.read_text())
        assert data["reservations"] == []  # 2026-09 record excluded

    def test_export_no_overwrite_existing(self, tmp_path):
        """Export must refuse to overwrite an existing output file."""
        import export_ledger

        existing = tmp_path / "existing.json"
        existing.write_text('{"old": true}')

        orig = sys.argv
        sys.argv = [
            "export_ledger.py",
            "--period", "2026-10",
            "--confirm-account", "123456789012",
            "--output", str(existing),
        ]
        try:
            with pytest.raises(SystemExit) as exc:
                export_ledger.main()
            assert exc.value.code != 0
        finally:
            sys.argv = orig
        # File must be unchanged.
        assert json.loads(existing.read_text()) == {"old": True}

    def test_export_missing_table_exits(self, tmp_path):
        """When the DynamoDB table does not exist, the export exits gracefully."""
        import boto3
        import export_ledger
        from moto import mock_aws

        with mock_aws():
            sts = boto3.client("sts", region_name="ap-south-1")
            account = sts.get_caller_identity()["Account"]
            out = tmp_path / "export.json"

            orig = sys.argv
            sys.argv = [
                "export_ledger.py",
                "--period", "2026-10",
                "--confirm-account", account,
                "--output", str(out),
                "--region", "ap-south-1",
            ]
            try:
                with pytest.raises(SystemExit) as exc:
                    export_ledger.main()
                assert exc.value.code != 0
            finally:
                sys.argv = orig
