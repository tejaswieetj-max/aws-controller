"""Typed contracts for the pure CALIBUR policy engine.

Every input carries a :class:`SourceRef` with a source record ID and the time the
record was observed.  Amounts are integer micro-USD (or an exact ``Decimal``
that is integral) and one currency per decision.  The models perform structural
validation only; they never read files, call AWS, or consult a clock.

No coverage, safety, or calibration guarantee is implied by any model here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .estimator import _coerce_amount, _coerce_timestamp

__all__ = [
    "APPROVE",
    "BUDGET_CLOSED",
    "BUDGET_OPEN",
    "CURRENCY_USD",
    "DENY",
    "MICRO_USD",
    "POLICY_VERSION",
    "BudgetState",
    "CalibrationSnapshot",
    "CurrencyMismatchError",
    "Decision",
    "EstimateSnapshot",
    "FutureInputError",
    "InvalidModelError",
    "ModelError",
    "RequestFeatures",
    "ReservationProposal",
    "RiskPolicyConfig",
    "SourceRef",
    "TelemetrySnapshot",
    "UnsupportedUnitError",
]

CURRENCY_USD = "USD"
MICRO_USD = "micro_usd"
SUPPORTED_CURRENCIES = frozenset({CURRENCY_USD})
SUPPORTED_UNITS = frozenset({MICRO_USD})
POLICY_VERSION = "calibur-policy/0.1"

APPROVE = "APPROVE"
DENY = "DENY"

BUDGET_OPEN = "OPEN"
BUDGET_CLOSED = "CLOSED"

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ModelError(ValueError):
    """Base class for invalid policy inputs."""


class InvalidModelError(ModelError):
    """A field is missing, malformed, negative, or internally inconsistent."""


class FutureInputError(ModelError):
    """An input timestamp is later than the decision time."""


class CurrencyMismatchError(ModelError):
    """Two inputs use different currencies."""


class UnsupportedUnitError(ModelError):
    """An input uses an unsupported currency or unit."""


def _text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise InvalidModelError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise InvalidModelError(f"{field_name} must not be empty")
    return text


def _timestamp(value: Any, *, field_name: str) -> datetime:
    try:
        return _coerce_timestamp(value, field=field_name)
    except (TypeError, ValueError) as exc:
        raise InvalidModelError(str(exc)) from exc


def _amount(
    value: Any,
    *,
    field_name: str,
    nonnegative: bool = True,
) -> int:
    if value is None:
        raise InvalidModelError(f"{field_name} is required")
    try:
        parsed = _coerce_amount(value, field=field_name)
    except (TypeError, ValueError) as exc:
        raise InvalidModelError(str(exc)) from exc
    if parsed is None:
        raise InvalidModelError(f"{field_name} is required")
    if isinstance(parsed, Decimal):
        if not parsed.is_finite() or parsed != parsed.to_integral_value():
            raise InvalidModelError(f"{field_name} must be an integral micro-amount")
        integer = int(parsed)
    else:
        integer = parsed
    if nonnegative and integer < 0:
        raise InvalidModelError(f"{field_name} must not be negative")
    return integer


def _decimal(
    value: Any,
    *,
    field_name: str,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> Decimal:
    if value is None or isinstance(value, bool):
        raise InvalidModelError(f"{field_name} must be a number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidModelError(f"{field_name} must be a number") from exc
    if not parsed.is_finite():
        raise InvalidModelError(f"{field_name} must be finite")
    if minimum is not None and parsed < minimum:
        raise InvalidModelError(f"{field_name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise InvalidModelError(f"{field_name} must be <= {maximum}")
    return parsed


def _currency(value: Any) -> str:
    text = _text(value, field_name="currency").upper()
    if text not in SUPPORTED_CURRENCIES:
        raise UnsupportedUnitError(
            f"unsupported currency {text!r}; supported: {sorted(SUPPORTED_CURRENCIES)}"
        )
    return text


def _unit(value: Any) -> str:
    text = _text(value, field_name="unit")
    if text not in SUPPORTED_UNITS:
        raise UnsupportedUnitError(
            f"unsupported unit {text!r}; supported: {sorted(SUPPORTED_UNITS)}"
        )
    return text


def _sources(value: Iterable[SourceRef]) -> tuple[SourceRef, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise InvalidModelError("source_refs must be an iterable of SourceRef")
    refs = tuple(value)
    if not refs:
        raise InvalidModelError("at least one SourceRef is required")
    if any(not isinstance(ref, SourceRef) for ref in refs):
        raise InvalidModelError("source_refs must contain SourceRef instances")
    return refs


@dataclass(frozen=True)
class SourceRef:
    """Provenance for one observed input record."""

    source_id: str
    observed_at: datetime
    source_file: str = "unknown"
    semantics: str = "observed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, field_name="source_id"))
        object.__setattr__(
            self, "observed_at", _timestamp(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(
            self,
            "source_file",
            _text(self.source_file, field_name="source_file") if self.source_file else "unknown",
        )
        object.__setattr__(
            self,
            "semantics",
            _text(self.semantics, field_name="semantics") if self.semantics else "observed",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
            "source_file": self.source_file,
            "semantics": self.semantics,
        }


@dataclass(frozen=True)
class RequestFeatures:
    """A real request as it was known at the decision time."""

    request_id: str
    team: str
    requested_at: datetime
    instance_type: str
    duration_hours: Decimal
    ebs_gb: int
    source: SourceRef
    region: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "request_id", _text(self.request_id, field_name="request_id")
        )
        object.__setattr__(self, "team", _text(self.team, field_name="team"))
        object.__setattr__(
            self, "requested_at", _timestamp(self.requested_at, field_name="requested_at")
        )
        object.__setattr__(
            self,
            "instance_type",
            _text(self.instance_type, field_name="instance_type"),
        )
        object.__setattr__(
            self,
            "duration_hours",
            _decimal(self.duration_hours, field_name="duration_hours", minimum=Decimal(0)),
        )
        if not isinstance(self.ebs_gb, int) or isinstance(self.ebs_gb, bool):
            raise InvalidModelError("ebs_gb must be an integer")
        if self.ebs_gb < 0:
            raise InvalidModelError("ebs_gb must not be negative")
        if not isinstance(self.source, SourceRef):
            raise InvalidModelError("request.source must be a SourceRef")
        object.__setattr__(self, "region", _text(self.region, field_name="region"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "team": self.team,
            "requested_at": self.requested_at.isoformat().replace("+00:00", "Z"),
            "instance_type": self.instance_type,
            "duration_hours": str(self.duration_hours),
            "ebs_gb": self.ebs_gb,
            "region": self.region,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class BudgetState:
    """The authoritative ledger snapshot supplied by the caller.

    The model enforces the production ledger invariant
    ``available + reserved + spent == cap``.  It does not write or lock the
    ledger; the existing atomic transaction remains authoritative.
    """

    team: str
    period: str
    cap_micro: int
    available_micro: int
    reserved_micro: int
    spent_micro: int
    as_of: datetime
    source: SourceRef
    status: str = BUDGET_OPEN
    currency: str = CURRENCY_USD
    unit: str = MICRO_USD

    def __post_init__(self) -> None:
        object.__setattr__(self, "team", _text(self.team, field_name="team"))
        period = _text(self.period, field_name="period")
        if not _PERIOD_RE.match(period):
            raise InvalidModelError("period must be YYYY-MM")
        object.__setattr__(self, "period", period)
        for field_name in ("cap_micro", "available_micro", "reserved_micro", "spent_micro"):
            object.__setattr__(
                self,
                field_name,
                _amount(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(self, "as_of", _timestamp(self.as_of, field_name="as_of"))
        if not isinstance(self.source, SourceRef):
            raise InvalidModelError("budget.source must be a SourceRef")
        status = _text(self.status, field_name="status").upper()
        if status not in {BUDGET_OPEN, BUDGET_CLOSED}:
            raise InvalidModelError("status must be OPEN or CLOSED")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))
        if self.available_micro + self.reserved_micro + self.spent_micro != self.cap_micro:
            raise InvalidModelError(
                "budget invariant failed: available + reserved + spent must equal cap"
            )

    @property
    def is_open(self) -> bool:
        return self.status == BUDGET_OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "period": self.period,
            "cap_micro": self.cap_micro,
            "available_micro": self.available_micro,
            "reserved_micro": self.reserved_micro,
            "spent_micro": self.spent_micro,
            "as_of": self.as_of.isoformat().replace("+00:00", "Z"),
            "status": self.status,
            "currency": self.currency,
            "unit": self.unit,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class EstimateSnapshot:
    """A point estimate and optional upper bound known at decision time."""

    point_cost_micro: int | None
    upper_cost_micro: int | None
    method: str
    as_of: datetime
    source_refs: tuple[SourceRef, ...]
    currency: str = CURRENCY_USD
    unit: str = MICRO_USD
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.point_cost_micro is not None:
            object.__setattr__(
                self,
                "point_cost_micro",
                _amount(self.point_cost_micro, field_name="point_cost_micro"),
            )
        if self.upper_cost_micro is not None:
            object.__setattr__(
                self,
                "upper_cost_micro",
                _amount(self.upper_cost_micro, field_name="upper_cost_micro"),
            )
            if self.point_cost_micro is not None and self.upper_cost_micro < self.point_cost_micro:
                raise InvalidModelError("upper_cost_micro must be >= point_cost_micro")
        object.__setattr__(self, "method", _text(self.method, field_name="method"))
        object.__setattr__(self, "as_of", _timestamp(self.as_of, field_name="as_of"))
        object.__setattr__(self, "source_refs", _sources(self.source_refs))
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_cost_micro": self.point_cost_micro,
            "upper_cost_micro": self.upper_cost_micro,
            "method": self.method,
            "as_of": self.as_of.isoformat().replace("+00:00", "Z"),
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "currency": self.currency,
            "unit": self.unit,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class CalibrationSnapshot:
    """A frozen final-label calibration state known at decision time.

    ``coverage_guarantee`` must remain ``None``.  The engine does not establish
    the assumptions required for a finite-sample coverage claim.
    """

    upper_cost_micro: int | None
    alpha: Decimal
    score_count: int
    as_of: datetime
    source_refs: tuple[SourceRef, ...]
    state_version: str = "unknown"
    currency: str = CURRENCY_USD
    unit: str = MICRO_USD
    coverage_guarantee: None = None

    def __post_init__(self) -> None:
        if self.upper_cost_micro is not None:
            object.__setattr__(
                self,
                "upper_cost_micro",
                _amount(self.upper_cost_micro, field_name="upper_cost_micro"),
            )
        object.__setattr__(
            self,
            "alpha",
            _decimal(self.alpha, field_name="alpha", minimum=Decimal(0), maximum=Decimal(1)),
        )
        if self.alpha == 0:
            raise InvalidModelError("alpha must be greater than zero")
        if not isinstance(self.score_count, int) or isinstance(self.score_count, bool):
            raise InvalidModelError("score_count must be an integer")
        if self.score_count < 0:
            raise InvalidModelError("score_count must not be negative")
        object.__setattr__(self, "as_of", _timestamp(self.as_of, field_name="as_of"))
        object.__setattr__(self, "source_refs", _sources(self.source_refs))
        object.__setattr__(self, "state_version", _text(self.state_version, field_name="state_version"))
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))
        if self.coverage_guarantee is not None:
            raise InvalidModelError(
                "coverage_guarantee must be None; no theorem-backed guarantee is established"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "upper_cost_micro": self.upper_cost_micro,
            "alpha": str(self.alpha),
            "score_count": self.score_count,
            "as_of": self.as_of.isoformat().replace("+00:00", "Z"),
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "state_version": self.state_version,
            "currency": self.currency,
            "unit": self.unit,
            "coverage_guarantee": self.coverage_guarantee,
        }


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Observed provisional telemetry supplied by the caller.

    The module does not convert usage units to currency.  If
    ``additional_exposure_micro`` is supplied, the caller must have derived it
    from real observed telemetry under a predeclared rule; the engine only adds
    the explicit amount and records its source.
    """

    job_id: str
    as_of: datetime
    source_refs: tuple[SourceRef, ...]
    provisional_cost_micro: int | None = None
    additional_exposure_micro: int | None = None
    currency: str = CURRENCY_USD
    unit: str = MICRO_USD

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _text(self.job_id, field_name="job_id"))
        object.__setattr__(self, "as_of", _timestamp(self.as_of, field_name="as_of"))
        object.__setattr__(self, "source_refs", _sources(self.source_refs))
        if self.provisional_cost_micro is not None:
            object.__setattr__(
                self,
                "provisional_cost_micro",
                _amount(self.provisional_cost_micro, field_name="provisional_cost_micro"),
            )
        if self.additional_exposure_micro is not None:
            object.__setattr__(
                self,
                "additional_exposure_micro",
                _amount(
                    self.additional_exposure_micro,
                    field_name="additional_exposure_micro",
                ),
            )
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "as_of": self.as_of.isoformat().replace("+00:00", "Z"),
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "provisional_cost_micro": self.provisional_cost_micro,
            "additional_exposure_micro": self.additional_exposure_micro,
            "currency": self.currency,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class RiskPolicyConfig:
    """Fixed policy configuration.  It is never tuned on evaluation data."""

    static_margin: Decimal = Decimal("1.10")
    under_reserve_weight: Decimal = Decimal(4)
    over_reserve_weight: Decimal = Decimal(1)
    require_upper_bound: bool = True
    max_evidence_age: timedelta = timedelta(hours=6)
    minimum_reserve_micro: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "static_margin",
            _decimal(self.static_margin, field_name="static_margin", minimum=Decimal(1)),
        )
        object.__setattr__(
            self,
            "under_reserve_weight",
            _decimal(
                self.under_reserve_weight,
                field_name="under_reserve_weight",
                minimum=Decimal(0),
            ),
        )
        object.__setattr__(
            self,
            "over_reserve_weight",
            _decimal(
                self.over_reserve_weight,
                field_name="over_reserve_weight",
                minimum=Decimal(0),
            ),
        )
        if self.under_reserve_weight <= 0 or self.over_reserve_weight <= 0:
            raise InvalidModelError("loss weights must be greater than zero")
        object.__setattr__(
            self,
            "max_evidence_age",
            self._age(self.max_evidence_age),
        )
        if not isinstance(self.minimum_reserve_micro, int) or isinstance(
            self.minimum_reserve_micro, bool
        ):
            raise InvalidModelError("minimum_reserve_micro must be an integer")
        if self.minimum_reserve_micro < 0:
            raise InvalidModelError("minimum_reserve_micro must not be negative")

    @staticmethod
    def _age(value: Any) -> timedelta:
        if not isinstance(value, timedelta):
            raise InvalidModelError("max_evidence_age must be a timedelta")
        if value <= timedelta(0):
            raise InvalidModelError("max_evidence_age must be positive")
        return value


class Decision(str, Enum):
    """Admission proposal, not a durable reservation."""

    APPROVE = APPROVE
    DENY = DENY


@dataclass(frozen=True)
class ReservationProposal:
    """Pure proposal returned by :func:`calibur_engine.policy.compute_reservation`."""

    decision: Decision
    reason_codes: tuple[str, ...]
    reservation_amount_micro: int
    point_cost_estimate_micro: int | None
    upper_cost_estimate_micro: int | None
    available_budget_before_micro: int
    available_budget_after_micro: int
    under_reserve_loss_micro: int
    over_reserve_loss_micro: int
    policy_version: str
    state_version: str
    decision_at: datetime
    input_refs: tuple[SourceRef, ...]
    audit_reference: str
    currency: str = CURRENCY_USD
    unit: str = MICRO_USD
    telemetry_exposure_micro: int = 0
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.decision, Decision):
            object.__setattr__(self, "decision", Decision(self.decision))
        object.__setattr__(
            self, "reason_codes", tuple(_text(code, field_name="reason_code") for code in self.reason_codes)
        )
        if not self.reason_codes:
            raise InvalidModelError("at least one reason code is required")
        for field_name in (
            "reservation_amount_micro",
            "available_budget_before_micro",
            "available_budget_after_micro",
            "under_reserve_loss_micro",
            "over_reserve_loss_micro",
            "telemetry_exposure_micro",
        ):
            object.__setattr__(
                self,
                field_name,
                _amount(getattr(self, field_name), field_name=field_name),
            )
        for field_name in ("point_cost_estimate_micro", "upper_cost_estimate_micro"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _amount(value, field_name=field_name))
        object.__setattr__(
            self, "policy_version", _text(self.policy_version, field_name="policy_version")
        )
        object.__setattr__(self, "state_version", _text(self.state_version, field_name="state_version"))
        object.__setattr__(self, "decision_at", _timestamp(self.decision_at, field_name="decision_at"))
        object.__setattr__(self, "input_refs", _sources(self.input_refs))
        object.__setattr__(self, "audit_reference", _text(self.audit_reference, field_name="audit_reference"))
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason_codes": list(self.reason_codes),
            "reservation_amount_micro": self.reservation_amount_micro,
            "point_cost_estimate_micro": self.point_cost_estimate_micro,
            "upper_cost_estimate_micro": self.upper_cost_estimate_micro,
            "available_budget_before_micro": self.available_budget_before_micro,
            "available_budget_after_micro": self.available_budget_after_micro,
            "under_reserve_loss_micro": self.under_reserve_loss_micro,
            "over_reserve_loss_micro": self.over_reserve_loss_micro,
            "policy_version": self.policy_version,
            "state_version": self.state_version,
            "decision_at": self.decision_at.isoformat().replace("+00:00", "Z"),
            "input_refs": [ref.to_dict() for ref in self.input_refs],
            "audit_reference": self.audit_reference,
            "currency": self.currency,
            "unit": self.unit,
            "telemetry_exposure_micro": self.telemetry_exposure_micro,
            "limitations": list(self.limitations),
        }


def assert_known_by(
    timestamp: datetime,
    *,
    now: datetime,
    field_name: str,
) -> None:
    """Reject an input observed after the decision time."""
    if timestamp > now:
        raise FutureInputError(
            f"{field_name} ({timestamp.isoformat()}) is later than decision time "
            f"({now.isoformat()})"
        )


def validate_mapping_keys(data: Mapping[str, Any], allowed: Iterable[str]) -> None:
    """Reject unknown mapping keys for callers that deserialize contract JSON."""
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise InvalidModelError(f"unknown fields: {unknown}")
