"""Export DynamoDB reservation and billing_audit records for empirical analysis.

This script is an operator tool that reads from the deployed Spendctl DynamoDB
tables and writes a JSON file in the format expected by:

    research/ingestion/ledger_export_reader.translate(path)
    research/empirical_run.py --ledger-export <path>

Usage
-----
Dry run (no AWS calls except STS identity verification):

    PYTHONPATH=services .venv/bin/python scripts/export_ledger.py \\
        --period 2026-10 --dry-run

Actual export (requires DynamoDB tables to exist and STS confirmation):

    PYTHONPATH=services .venv/bin/python scripts/export_ledger.py \\
        --period 2026-10 \\
        --confirm-account <12-digit-account-id> \\
        --output /path/to/ledger-export-2026-10.json

Safety rules enforced here
--------------------------
- Never writes to, modifies, or deletes any DynamoDB item.
- Never creates or modifies AWS resources.
- Requires explicit --confirm-account to proceed past dry-run.
- Redacts financially sensitive field values in the dry-run summary.
- Never creates, commits, or uploads the output file to any repository.
- Output JSON contains real billing amounts and reservation IDs; treat as private.

Output format
-------------
The exported JSON has the structure:

    {
      "export_at_utc": "<ISO timestamp>",
      "period": "<YYYY-MM>",
      "account": "<12 digits, for cross-check only>",
      "table_prefix": "<prefix>",
      "reservations": [<reservation DynamoDB items, Python-native types>],
      "billing_audits": [<billing_audit items, Python-native types>]
    }

This is the exact format passed to ledger_export_reader.translate() and
evaluate.build_dataset().  No transformation or filtering is applied; all
exclusion logic stays in evaluate.py where it is tested.

Provenance note
---------------
The --provenance flag passed to evaluate.py should be "aws_export".  This is
an operator assertion that the records were exported from the actual DynamoDB
tables.  It does not guarantee final settlement or pricing validity; evaluate.py
will report those exclusions separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow running from repo root with PYTHONPATH=services
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))


class ExportError(Exception):
    """Raised when the export cannot be completed safely."""


# ---------------------------------------------------------------------------
# DynamoDB type-normaliser
# ---------------------------------------------------------------------------

def _dynamo_item_to_python(item: dict) -> dict:
    """Convert DynamoDB wire-format attribute values to plain Python types.

    DynamoDB items from the low-level client use {'S': '...'}, {'N': '...'}, etc.
    The high-level resource client returns Python-native types directly; this
    function handles whichever the caller used, plus already-native dicts.
    """
    def _value(v):
        if not isinstance(v, dict):
            return v  # already native (resource client)
        if 'S' in v:
            return v['S']
        if 'N' in v:
            # Preserve as string to avoid float precision loss; evaluate.py
            # uses Decimal(str(value)) internally.
            raw = v['N']
            # Return int if it looks like a whole number (micro-USD are integers).
            if '.' not in raw and 'e' not in raw.lower():
                try:
                    return int(raw)
                except ValueError:
                    return raw
            return raw
        if 'BOOL' in v:
            return v['BOOL']
        if 'NULL' in v:
            return None
        if 'L' in v:
            return [_value(x) for x in v['L']]
        if 'M' in v:
            return {k: _value(val) for k, val in v['M'].items()}
        if 'SS' in v:
            return list(v['SS'])
        if 'NS' in v:
            return [n for n in v['NS']]
        return v  # unknown type: pass through

    return {k: _value(v) for k, v in item.items()}


# ---------------------------------------------------------------------------
# Table scanning
# ---------------------------------------------------------------------------

def _scan_reservations(dynamodb, table_name: str, period: str) -> list[dict]:
    """Scan the Reservations table for the given billing period.

    Reads:
    - Reservation records (record_type='RESERVATION', budget_period=period)
    - Billing audit records (record_type='BILLING_RECONCILIATION', billing_period=period)

    Uses FilterExpression to avoid returning unrelated records, but the full
    table is scanned because there is no GSI on period for all record types.
    This is safe for the small dev table expected here.
    """

    reservations = []
    billing_audits = []
    paginator = dynamodb.get_paginator('scan')

    # Scan reservation records for the period.
    for page in paginator.paginate(
        TableName=table_name,
        FilterExpression='budget_period = :period AND record_type = :rtype',
        ExpressionAttributeValues={
            ':period': {'S': period},
            ':rtype': {'S': 'RESERVATION'},
        },
    ):
        for item in page.get('Items', []):
            reservations.append(_dynamo_item_to_python(item))

    # Scan billing audit records for the period.
    for page in paginator.paginate(
        TableName=table_name,
        FilterExpression='billing_period = :period AND record_type = :rtype',
        ExpressionAttributeValues={
            ':period': {'S': period},
            ':rtype': {'S': 'BILLING_RECONCILIATION'},
        },
    ):
        for item in page.get('Items', []):
            billing_audits.append(_dynamo_item_to_python(item))

    return reservations, billing_audits


def _verify_identity(sts, expected_account: str) -> str:
    """Verify the caller identity and confirm the account matches. Returns account ID."""
    identity = sts.get_caller_identity()
    account = identity['Account']
    if account != expected_account:
        raise ExportError(
            f"Active AWS account {account} does not match --confirm-account {expected_account}. "
            "Stop. Verify you have the right profile and account before exporting billing data."
        )
    return account


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export Spendctl DynamoDB reservation + billing_audit records for "
            "offline empirical analysis. Read-only. Never modifies AWS resources."
        )
    )
    parser.add_argument(
        '--period', required=True,
        help='Billing period in YYYY-MM format (e.g. 2026-10).',
    )
    parser.add_argument(
        '--output', type=Path, default=None,
        help='Path for the output JSON file. Required unless --dry-run.',
    )
    parser.add_argument(
        '--confirm-account', default=None,
        help='12-digit AWS account ID, required to proceed past dry-run.',
    )
    parser.add_argument(
        '--table-prefix', default='spendctl-dev',
        help='DynamoDB table name prefix (default: spendctl-dev).',
    )
    parser.add_argument(
        '--region', default='ap-south-1',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Describe what would be exported without making DynamoDB calls.',
    )
    args = parser.parse_args()

    import re
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', args.period):
        raise SystemExit("--period must use YYYY-MM format")

    if args.dry_run:
        print(
            f"Dry run: would export reservations and billing_audits for period={args.period} "
            f"from table prefix '{args.table_prefix}' in region '{args.region}'.\n"
            f"Tables queried:\n"
            f"  {args.table_prefix}-reservations (RESERVATION records for period)\n"
            f"  {args.table_prefix}-reservations (BILLING_RECONCILIATION audit records)\n"
            f"Output path: {args.output or '(not specified)'}\n"
            f"No AWS API calls were made in dry-run mode."
        )
        return

    if not args.confirm_account:
        raise SystemExit(
            "--confirm-account is required to run the export. "
            "Verify the active AWS profile and account first, then pass the 12-digit account ID."
        )
    if not (len(args.confirm_account) == 12 and args.confirm_account.isdigit()):
        raise SystemExit("--confirm-account must be a 12-digit numeric account ID")

    if args.output is None:
        raise SystemExit("--output is required when not in --dry-run mode")

    if args.output.exists():
        raise SystemExit(
            f"Output file {args.output} already exists. "
            "Choose a new path to avoid overwriting an existing export."
        )

    try:
        import boto3
    except ImportError as exc:
        raise SystemExit("boto3 is required: pip install boto3") from exc

    sts = boto3.client('sts', region_name=args.region)
    try:
        account = _verify_identity(sts, args.confirm_account)
    except ExportError as exc:
        raise SystemExit(str(exc)) from exc

    dynamodb = boto3.client('dynamodb', region_name=args.region)
    reservations_table = f"{args.table_prefix}-reservations"

    # Verify tables exist before scanning.
    try:
        dynamodb.describe_table(TableName=reservations_table)
    except dynamodb.exceptions.ResourceNotFoundException:
        raise SystemExit(
            f"DynamoDB table '{reservations_table}' does not exist in {args.region}. "
            "The Spendctl application stacks have not been deployed yet. "
            "There are no reservation records to export."
        )

    print(f"Exporting period={args.period} from {reservations_table} in {args.region}...")

    try:
        reservations, billing_audits = _scan_reservations(
            dynamodb, reservations_table, args.period
        )
    except Exception as exc:
        raise SystemExit(f"DynamoDB scan failed: {exc}") from exc

    export_at = datetime.now(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    output = {
        "export_at_utc": export_at,
        "period": args.period,
        "account": account,
        "table_prefix": args.table_prefix,
        "reservations": reservations,
        "billing_audits": billing_audits,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, indent=2, default=str) + "\n",
        encoding='utf-8',
    )

    print(
        f"Exported {len(reservations)} reservation(s) and "
        f"{len(billing_audits)} billing_audit record(s) to {args.output}.\n"
        f"Export time (UTC): {export_at}\n"
        f"\nIMPORTANT: This file contains real billing data. "
        f"Do not commit it to version control."
    )
    if len(reservations) == 0:
        print(
            "\nNo reservation records found for this period. "
            "This means no CALIBUR admissions have been completed in this period. "
            "The empirical analysis will return DATA_INFEASIBILITY."
        )


if __name__ == '__main__':
    main()
