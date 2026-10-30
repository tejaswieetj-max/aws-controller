"""Fixture-testable CUR 2.0 / FOCUS 1.2 normalization and ledger reconciliation.

This module deliberately does not guess at missing resources or incomplete
periods. FOCUS 1.2 has no verified row-level key, so a delivery is treated as a
complete snapshot: rows are aggregated per reservation and the new total
replaces the prior target instead of being appended.

Timestamps in both schemas are billing-period boundaries, never billing-record
arrival times. FOCUS revision order comes from the S3 delivery-key timestamp.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import urlsplit

from .ledger import (
    LedgerError,
    ReservationConflict,
    ReservationNotFound,
    SettlementConflict,
)

MICRO_USD = Decimal(1_000_000)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,256}$")
_PERIOD = re.compile(r"^\d{4}-\d{2}$")
_DELIVERY_DIRECTORY = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)-([A-Za-z0-9_.:/-]{1,256})$"
)
_EC2_ARN = re.compile(r"^arn:[^:]+:ec2:[^:]*:[^:]*:(?:instance|volume)/(.+)$")
_SUPPORTED_PRODUCTS = {"AmazonEC2", "AmazonEBS"}
_FOCUS_SUPPORTED_FILE_SUFFIXES = (".parquet", ".csv", ".csv.gz")


class BillingError(Exception):
    """Base class for malformed or unsafe billing input."""


class InvalidBillingRecord(BillingError):
    pass


class IncompleteBillingSnapshot(BillingError):
    pass


class AmbiguousAttribution(BillingError):
    pass


class BillingLedgerConflict(BillingError):
    pass


@dataclass(frozen=True)
class NormalizedBillingRecord:
    record_id: str
    billing_period: str
    usage_start: str
    usage_end: str
    account_id: str | None
    service_code: str
    usage_type: str | None
    operation: str | None
    resource_id: str | None
    reservation_tag: str | None
    team_tag: str | None
    line_item_type: str
    currency: str
    amount_micro: int
    cost_field: str
    classification: str
    raw_fingerprint: str


def _required(row: dict, key: str) -> str:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        raise InvalidBillingRecord(f"missing required CUR field: {key}")
    return str(value).strip()


def _micro_usd(value: object, field: str) -> int:
    try:
        usd = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise InvalidBillingRecord(f"invalid decimal in {field}") from error
    if not usd.is_finite():
        raise InvalidBillingRecord(f"non-finite decimal in {field}")
    # One USD is 1,000,000 micro-USD. Half-even is deterministic and unbiased;
    # signed credits/refunds use the same rule but are never applied as usage.
    return int((usd * MICRO_USD).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def normalize_record(row: dict, *, object_id: str) -> NormalizedBillingRecord:
    """Normalize documented CUR 2.0 aliases; fail closed on required fields."""
    if not isinstance(row, dict):
        raise InvalidBillingRecord("CUR row must be an object")
    record_id = row.get("identity_line_item_id") or row.get("record_id")
    if not record_id:
        raise InvalidBillingRecord(
            "identity_line_item_id is required for stable record deduplication"
        )
    period = _required(row, "bill_billing_period_start_date")[:7]
    service = _required(row, "product_product_code")
    line_type = _required(row, "line_item_line_item_type")
    currency = str(row.get("line_item_currency_code") or "USD").upper()
    if currency != "USD":
        raise InvalidBillingRecord(f"unsupported CUR currency: {currency}")
    cost_field = (
        "line_item_net_unblended_cost"
        if row.get("line_item_net_unblended_cost") not in (None, "")
        else "line_item_unblended_cost"
    )
    if row.get(cost_field) in (None, ""):
        raise InvalidBillingRecord("CUR row has neither net nor unblended cost")
    amount = _micro_usd(row[cost_field], cost_field)
    classification = _classify(service, line_type, amount)
    raw_fingerprint = hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    reservation_tag = _nonempty(row.get("resource_tags_user_reservation_id"))
    team_tag = _nonempty(row.get("resource_tags_user_team"))
    return NormalizedBillingRecord(
        record_id=f"{object_id}:{record_id}",
        billing_period=period,
        usage_start=_required(row, "line_item_usage_start_date"),
        usage_end=_required(row, "line_item_usage_end_date"),
        account_id=_nonempty(row.get("line_item_usage_account_id")),
        service_code=service,
        usage_type=_nonempty(row.get("line_item_usage_type")),
        operation=_nonempty(row.get("line_item_operation")),
        resource_id=_nonempty(row.get("line_item_resource_id")),
        reservation_tag=reservation_tag,
        team_tag=team_tag,
        line_item_type=line_type,
        currency=currency,
        amount_micro=amount,
        cost_field=cost_field,
        classification=classification,
        raw_fingerprint=raw_fingerprint,
    )


def _nonempty(value) -> str | None:
    return str(value).strip() if value is not None and str(value).strip() else None


def _focus_value(row: dict, key: str):
    """Read a FOCUS field case-insensitively (Athena may lower-case columns)."""
    if key in row:
        return row[key]
    folded = key.casefold()
    for candidate, value in row.items():
        if str(candidate).casefold() == folded:
            return value
    return None


def _focus_required(row: dict, key: str) -> str:
    value = _focus_value(row, key)
    if value is None or str(value).strip() == "":
        raise InvalidBillingRecord(f"missing required FOCUS field: {key}")
    return str(value).strip()


def _focus_tags(value) -> dict[str, str]:
    """Parse FOCUS Tags from a map or Athena's ``{key=value}`` rendering."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {
            str(key): str(item)
            for key, item in value.items()
            if key is not None and item is not None
        }
    if isinstance(value, (list, tuple)):
        if not value:
            return {}
        tags = {}
        for entry in value:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise InvalidBillingRecord("FOCUS Tags entry is malformed")
            key, item = entry
            if key is None or item is None:
                continue
            tags[str(key)] = str(item)
        return tags
    text = str(value).strip()
    if text in {"", "{}"}:
        return {}
    if not (text.startswith("{") and text.endswith("}")):
        raise InvalidBillingRecord("FOCUS Tags must be a map")
    if '"' in text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return _focus_tags(parsed)
        if parsed is not None:
            raise InvalidBillingRecord("FOCUS Tags must be a map")
    body = text[1:-1].strip()
    if not body:
        return {}
    parts = body.split(", ")
    if any("=" not in part for part in parts):
        parts = body.split(",")
    tags = {}
    for part in parts:
        key, separator, item = part.partition("=")
        if not separator or not key.strip():
            raise InvalidBillingRecord("FOCUS Tags entry is malformed")
        tags[key.strip()] = item.strip()
    return tags


def _focus_resource_id(value) -> str | None:
    """Reduce an EC2/volume ARN to the bare resource id recorded by the ledger."""
    resource_id = _nonempty(value)
    if not resource_id:
        return None
    match = _EC2_ARN.fullmatch(resource_id)
    return match.group(1) if match else resource_id


def _focus_period(value: str) -> str:
    period = value[:7]
    if not _PERIOD.fullmatch(period):
        raise InvalidBillingRecord("FOCUS BillingPeriodStart is not an ISO timestamp")
    return period


def normalize_focus_record(
    row: dict, *, snapshot_id: str, row_index: int
) -> NormalizedBillingRecord:
    """Normalize one FOCUS 1.2 row into the internal reconciliation model.

    FOCUS 1.2 has no verified row-level unique key. ``row_index`` provides a
    stable ordinal within one validated snapshot so that legitimate duplicate
    rows are aggregated rather than silently discarded.
    """
    if not isinstance(row, dict):
        raise InvalidBillingRecord("FOCUS row must be an object")
    snapshot = _nonempty(snapshot_id)
    if not snapshot:
        raise InvalidBillingRecord("FOCUS snapshot id is required")
    if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0:
        raise InvalidBillingRecord("FOCUS row index must be a non-negative integer")
    billing_period = _focus_period(_focus_required(row, "BillingPeriodStart"))
    service = _focus_required(row, "x_ServiceCode")
    charge_category = _focus_required(row, "ChargeCategory")
    currency = _focus_required(row, "BillingCurrency").upper()
    if currency != "USD":
        raise InvalidBillingRecord(f"unsupported FOCUS currency: {currency}")
    amount = _micro_usd(_focus_required(row, "BilledCost"), "BilledCost")
    tags = _focus_tags(_focus_value(row, "Tags"))
    raw_fingerprint = hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return NormalizedBillingRecord(
        record_id=f"{snapshot}:row:{row_index}",
        billing_period=billing_period,
        usage_start=_focus_required(row, "ChargePeriodStart"),
        usage_end=_focus_required(row, "ChargePeriodEnd"),
        account_id=_nonempty(_focus_value(row, "BillingAccountId"))
        or _nonempty(_focus_value(row, "SubAccountId")),
        service_code=service,
        usage_type=_nonempty(_focus_value(row, "SkuMeter")),
        operation=_nonempty(_focus_value(row, "x_Operation")),
        resource_id=_focus_resource_id(_focus_value(row, "ResourceId")),
        reservation_tag=_nonempty(tags.get("reservation-id")),
        team_tag=_nonempty(tags.get("team")),
        line_item_type=charge_category,
        currency=currency,
        amount_micro=amount,
        cost_field="BilledCost",
        classification=_classify(service, charge_category, amount),
        raw_fingerprint=raw_fingerprint,
    )


def _classify(service: str, line_type: str, amount: int) -> str:
    if amount < 0 or line_type.lower() in {"credit", "refund", "discount", "edp discount"}:
        return "ADJUSTMENT_EXCLUDED"
    if line_type != "Usage":
        return "NON_USAGE_EXCLUDED"
    if service not in _SUPPORTED_PRODUCTS:
        return "OUT_OF_SCOPE"
    return "USAGE_CHARGE"


class BillingLedger(Protocol):
    def get_reservation(self, reservation_id: str) -> dict: ...
    def get_reservation_by_resource(self, resource_id: str) -> dict: ...
    def reconcile_billing(self, reservation_id: str, actual_micro: int, **kwargs) -> dict: ...


@dataclass
class ReconciliationResult:
    processed: int = 0
    unattributed: int = 0
    excluded: int = 0
    duplicates: int = 0
    skipped: int = 0
    adjustments: list[dict] | None = None
    reasons: list[dict] | None = None

    def __post_init__(self):
        self.adjustments = [] if self.adjustments is None else self.adjustments
        self.reasons = [] if self.reasons is None else self.reasons


class BillingReconciler:
    """Apply only complete, identified snapshots and exact resource attribution."""

    def __init__(self, ledger: BillingLedger, *, emit=None):
        self.ledger = ledger
        self.emit = emit or (lambda _name, _detail: None)

    def reconcile(
        self,
        records: list[NormalizedBillingRecord],
        *,
        billing_period: str,
        source_revision: str,
        source_revision_order: str,
        complete: bool,
        stale_revision_policy: str = "raise",
    ) -> ReconciliationResult:
        if not complete:
            raise IncompleteBillingSnapshot("incomplete billing snapshot cannot change the ledger")
        if stale_revision_policy not in {"raise", "skip"}:
            raise ValueError("stale_revision_policy must be 'raise' or 'skip'")
        if (
            not _IDENTIFIER.fullmatch(billing_period)
            or not _IDENTIFIER.fullmatch(source_revision)
            or not _IDENTIFIER.fullmatch(source_revision_order)
        ):
            raise InvalidBillingRecord("invalid billing period or source revision")
        totals: dict[str, dict] = {}
        seen: dict[str, str] = {}
        result = ReconciliationResult()
        for record in records:
            if record.billing_period != billing_period:
                raise InvalidBillingRecord("snapshot includes a row from another billing period")
            prior_fingerprint = seen.get(record.record_id)
            if prior_fingerprint is not None:
                if prior_fingerprint != record.raw_fingerprint:
                    raise InvalidBillingRecord(
                        "duplicate CUR identity has conflicting amounts or fields"
                    )
                result.duplicates += 1
                continue
            seen[record.record_id] = record.raw_fingerprint
            if record.classification != "USAGE_CHARGE":
                result.excluded += 1
                continue
            reservation = self._attribute(record, result)
            if reservation is None:
                continue
            if not self._single_period_reservation(reservation, billing_period):
                self._unattributed(
                    result,
                    record,
                    "reservation runtime period is incomplete or spans billing periods",
                )
                continue
            entry = totals.setdefault(reservation["reservation_id"], {"micro": 0, "ids": []})
            entry["micro"] += record.amount_micro
            entry["ids"].append((record.record_id, record.raw_fingerprint))
        for reservation_id, aggregate in sorted(totals.items()):
            digest = hashlib.sha256(
                "\n".join(
                    f"{record_id}:{fingerprint}"
                    for record_id, fingerprint in sorted(aggregate["ids"])
                ).encode()
                + b"\0"
                + str(aggregate["micro"]).encode()
            ).hexdigest()
            reconciliation_id = hashlib.sha256(
                f"{reservation_id}\0{billing_period}\0{source_revision}\0{digest}".encode()
            ).hexdigest()
            try:
                settled = self.ledger.reconcile_billing(
                    reservation_id,
                    aggregate["micro"],
                    reconciliation_id=reconciliation_id,
                    billing_period=billing_period,
                    source_revision=source_revision,
                    source_revision_order=source_revision_order,
                    input_digest=digest,
                    attributed_record_ids=[record_id for record_id, _ in aggregate["ids"]],
                )
            except LedgerError as error:
                # Never convert ledger/transport uncertainty into a retry with a
                # new identity or into a refund. Surface the error to the caller.
                self.emit(
                    "BillingLedgerConflict",
                    {"reservation_id": reservation_id, "reason": type(error).__name__},
                )
                stale = isinstance(error, SettlementConflict) and (
                    "stale" in str(error).lower() or "out of order" in str(error).lower()
                )
                if stale:
                    self.emit(
                        "BillingStaleRevision",
                        {"reservation_id": reservation_id, "reason": type(error).__name__},
                    )
                    if stale_revision_policy == "skip":
                        result.skipped += 1
                        continue
                raise BillingLedgerConflict(reservation_id) from error
            result.processed += 1
            result.adjustments.append(
                {
                    "reservation_id": reservation_id,
                    "reconciliation_id": reconciliation_id,
                    "previous_actual_micro": settled["audit"].get("previous_actual_micro"),
                    "new_actual_micro": settled["audit"].get("new_actual_micro"),
                    "adjustment_micro": settled["audit"].get("adjustment_micro"),
                    "replayed": settled["replayed"],
                    "team": settled["reservation"].get("team"),
                    "settlement_id": settled["reservation"].get("final_settlement_id"),
                    "settlement_version": settled["reservation"].get("settlement_version"),
                }
            )
            self.emit("BillingReconciled", result.adjustments[-1])
            self.emit("ReservationSettled", result.adjustments[-1])
        return result

    def _attribute(
        self, record: NormalizedBillingRecord, result: ReconciliationResult
    ) -> dict | None:
        by_resource = None
        if record.resource_id:
            try:
                by_resource = self.ledger.get_reservation_by_resource(record.resource_id)
            except ReservationNotFound:
                pass
            except ReservationConflict:
                return self._unattributed(
                    result, record, "resource mapping conflicts with reservation history"
                )
        tagged = None
        if record.reservation_tag:
            try:
                tagged = self.ledger.get_reservation(record.reservation_tag)
            except ReservationNotFound:
                tagged = None
        if by_resource and tagged and by_resource["reservation_id"] != tagged["reservation_id"]:
            return self._unattributed(
                result, record, "reservation tag conflicts with resource mapping"
            )
        reservation = tagged or by_resource
        if not reservation:
            return self._unattributed(
                result, record, "no exact reservation tag or resource mapping"
            )
        if record.team_tag and record.team_tag != reservation.get("team"):
            return self._unattributed(result, record, "team tag does not match reservation")
        return reservation

    def _unattributed(self, result, record, reason):
        result.unattributed += 1
        detail = {
            "record_id": record.record_id,
            "resource_id": record.resource_id,
            "reason": reason,
        }
        result.reasons.append(detail)
        self.emit("BillingAttributionAmbiguous", detail)

    @staticmethod
    def _single_period_reservation(reservation: dict, billing_period: str) -> bool:
        start = reservation.get("launched_at")
        end = reservation.get("terminated_at") or reservation.get("expires_at")
        if not start or not end:
            return False
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
        except ValueError:
            return False
        return (
            start_dt.strftime("%Y-%m") == billing_period
            and end_dt.strftime("%Y-%m") == billing_period
        )


def validate_manifest(manifest: dict) -> tuple[str, str, str]:
    """Validate the minimal manifest contract before querying Athena.

    Data Exports manifest field names are an explicit VERIFY assumption until
    confirmed from an account export; unknown shapes fail closed.
    """
    if not isinstance(manifest, dict):
        raise InvalidBillingRecord("manifest must be a JSON object")
    revision = manifest.get("assemblyId") or manifest.get("assemblyVersion")
    period = manifest.get("billingPeriod")
    created = manifest.get("createTimestamp")
    files = manifest.get("dataFilePaths")
    if not revision or not period or not created or not isinstance(files, list) or not files:
        raise InvalidBillingRecord(
            "manifest missing revision, createTimestamp, billingPeriod, or dataFilePaths"
        )
    period = str(period)
    if len(period) == 6 and period.isdigit():
        period = f"{period[:4]}-{period[4:]}"
    if not _IDENTIFIER.fullmatch(str(revision)) or not re.fullmatch(r"\d{4}-\d{2}", period):
        raise InvalidBillingRecord("manifest revision or billing period has an invalid format")
    try:
        timestamp = datetime.fromisoformat(str(created))
    except ValueError as error:
        raise InvalidBillingRecord("manifest createTimestamp must be an ISO timestamp") from error
    if timestamp.tzinfo is None:
        raise InvalidBillingRecord("manifest createTimestamp must include a timezone")
    order = timestamp.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return period, str(revision), order


@dataclass(frozen=True)
class FocusSnapshot:
    billing_period: str
    revision: str
    revision_order: str
    data_files: tuple[str, ...]


def validate_focus_manifest(manifest: dict, *, object_key: str) -> FocusSnapshot:
    """Validate a FOCUS 1.2 Data Exports manifest and derive its delivery order.

    The manifest carries no billing period or creation timestamp. The period
    and the observed delivery timestamp come from the S3 key written by Data
    Exports (``billing_period=YYYY-MM/<timestamp>-<executionId>/...``).
    """
    if not isinstance(manifest, dict):
        raise InvalidBillingRecord("manifest must be a JSON object")
    revision = manifest.get("executionId")
    data_files = manifest.get("dataFiles")
    additional = manifest.get("additionalOutputFiles", [])
    if not isinstance(revision, str) or not _IDENTIFIER.fullmatch(revision):
        raise InvalidBillingRecord("FOCUS manifest executionId is missing or invalid")
    if not isinstance(data_files, list) or not data_files:
        raise IncompleteBillingSnapshot("FOCUS manifest has no data files")
    if not isinstance(additional, list):
        raise InvalidBillingRecord("FOCUS additionalOutputFiles must be a list")
    if additional:
        raise IncompleteBillingSnapshot(
            "FOCUS additional output files require manual review before reconciliation"
        )
    key = _validated_object_key(object_key)
    parts = key.split("/")
    if len(parts) < 3:
        raise InvalidBillingRecord("FOCUS manifest key is not inside a delivery directory")
    delivery_directory = parts[-2]
    match = _DELIVERY_DIRECTORY.fullmatch(delivery_directory)
    if not match:
        raise InvalidBillingRecord(
            "FOCUS manifest key is missing the delivery timestamp and execution id"
        )
    if match.group(2) != revision:
        raise InvalidBillingRecord("FOCUS manifest executionId does not match its S3 key")
    try:
        timestamp = datetime.fromisoformat(match.group(1))
    except ValueError as error:
        raise InvalidBillingRecord("FOCUS delivery timestamp is not ISO-8601") from error
    order = timestamp.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    period = None
    for part in parts:
        period_match = re.fullmatch(r"billing_period=(\d{4}-\d{2})", part)
        if period_match:
            period = period_match.group(1)
            break
    if period is None:
        raise InvalidBillingRecord("FOCUS manifest key has no billing_period=YYYY-MM segment")
    delivery_prefix = "/".join(parts[:-1]) + "/"
    files = []
    for uri in data_files:
        if not isinstance(uri, str):
            raise InvalidBillingRecord("FOCUS data file must be an S3 URI")
        if "'" in uri or "\\" in uri:
            raise InvalidBillingRecord("FOCUS data file contains an unsafe character")
        parsed = urlsplit(uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
            raise InvalidBillingRecord("FOCUS data file must be an S3 URI")
        file_key = parsed.path.lstrip("/")
        if not file_key.startswith(delivery_prefix):
            raise InvalidBillingRecord("FOCUS data file is outside its delivery directory")
        if ".." in file_key.split("/"):
            raise InvalidBillingRecord("FOCUS data file path traversal is not allowed")
        if not file_key.lower().endswith(_FOCUS_SUPPORTED_FILE_SUFFIXES):
            raise InvalidBillingRecord("FOCUS data file is not Parquet or CSV")
        files.append(uri)
    return FocusSnapshot(period, revision, order, tuple(files))


def _validated_object_key(object_key: str) -> str:
    if not isinstance(object_key, str) or not object_key:
        raise InvalidBillingRecord("manifest object key is required")
    key = object_key.lstrip("/")
    if "\\" in key or ".." in key.split("/"):
        raise InvalidBillingRecord("invalid S3 manifest key")
    return key
