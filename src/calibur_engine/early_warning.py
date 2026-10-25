"""Early-warning state machine over caller-supplied provisional telemetry.

Scope
-----
This module is a pure, in-process monitor.  It accepts real, timestamped
provisional observations that the caller has already computed, and compares
them with a caller-supplied threshold.  It creates no AWS clients, performs no
network or filesystem access, writes no database rows, and never generates a
timestamp or a synthetic observation.

Threshold semantics
-------------------
``research/protocol.md`` specifies an early error signal when "provisional
spend strictly exceeds the reserved upper bound before job completion".  This
module implements exactly that comparison::

    warning  iff  provisional_cost_micro is present
             and  provisional_cost_micro > threshold_micro
             and  the job is not yet complete

The threshold is required at registration.  It is normally the reservation's
``reserved_micro`` (the protocol's reserved upper bound, recorded as
``threshold_source="reserved_upper_bound"``), but the caller may name another
explicit bound.  This module never derives, calibrates, or widens the
threshold.  Equality does **not** trigger a warning.

The warning latches for the life of the job.  After the first strict crossing,
later observations do not emit additional warning events, and a later lower
reading does not clear the warning.  This follows the protocol's one-time
``E_t=1`` early signal and its assumption that cumulative provisional usage is
non-decreasing.  Re-arming is intentionally not supported.

Missing data
------------
``provisional_cost_micro=None`` represents telemetry that was genuinely
observed but whose cost is missing.  Such a record is retained for audit,
counted separately, and never evaluated as zero.  A job with no usable
observation remains ``INSUFFICIENT_DATA``; a job whose observations all lack a
cost remains ``INSUFFICIENT_DATA`` with reason ``NO_USABLE_TELEMETRY``.

Ordering and rejection rules
----------------------------
For each job, accepted observations must have strictly increasing
``observed_at`` timestamps.  The rules are:

* unknown job IDs are rejected;
* missing timestamps and missing source record IDs are rejected;
* negative provisional costs and negative thresholds are rejected;
* an observation whose currency or unit differs from the job is rejected;
* an identical repeated ``source_record_id`` is an idempotent duplicate;
* the same ``source_record_id`` with different content is a conflict;
* an earlier timestamp than the last accepted observation is out of order;
* an equal timestamp from a different source record is ambiguous;
* a new observation after ``mark_completed`` is rejected, and a new warning
  is never emitted retrospectively.

No system clock is consulted, so this module can neither invent a timestamp
nor decide that a caller-supplied timestamp is "in the future".

Separation from conformal calibration
-------------------------------------
This module does not import :mod:`calibur_engine.conformal` and never mutates a
conformal calibrator.  Its events are operational early-warning telemetry, not
final-cost labels.  A caller may separately record an event as provisional
telemetry in a conformal store, but that is an explicit caller action outside
this module; it must not change conformal scores, ``alpha``, thresholds, or
prediction bounds.

Unresolved protocol inputs
--------------------------
``research/protocol.md`` and ``research/event_collector_spec.md`` do not define
how one-minute CloudWatch usage integrals are converted into provisional
currency, nor a polling cadence, threshold provenance, or masking rule for
late CUR labels.  Callers must therefore supply an already-computed
``provisional_cost_micro`` in a single currency/unit; this module does not
invent a price conversion or a telemetry-derived threshold.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

# Reuse the estimator's coercion rules so timestamp and amount semantics stay
# consistent across the package.  These helpers perform no I/O.
from .estimator import _coerce_amount, _coerce_timestamp

__all__ = [
    "DEFAULT_CURRENCY",
    "DEFAULT_THRESHOLD_SOURCE",
    "EARLY_WARNING_LIMITATIONS",
    "MICRO_USD",
    "REASON_ALREADY_BREACHED",
    "REASON_AT_THRESHOLD",
    "REASON_BELOW_THRESHOLD",
    "REASON_COMPLETED_NO_WARNING",
    "REASON_COMPLETED_WITH_WARNING",
    "REASON_DUPLICATE_OBSERVATION",
    "REASON_MISSING_PROVISIONAL_COST",
    "REASON_NO_TELEMETRY",
    "REASON_NO_USABLE_TELEMETRY",
    "REASON_THRESHOLD_EXCEEDED",
    "STATUS_BREACHED",
    "STATUS_COMPLETED",
    "STATUS_INSUFFICIENT_DATA",
    "STATUS_MONITORING",
    "AmbiguousObservationOrderError",
    "CompletionConflictError",
    "ConflictingObservationError",
    "DuplicateJobError",
    "EarlyWarningError",
    "EarlyWarningEvent",
    "EarlyWarningMonitor",
    "EarlyWarningState",
    "InvalidCompletionTimestampError",
    "InvalidTelemetryError",
    "InvalidTelemetryTimestampError",
    "InvalidThresholdError",
    "MissingProvisionalCostError",
    "MissingThresholdError",
    "ObservationAfterCompletionError",
    "ObservationResult",
    "OutOfOrderCompletionError",
    "OutOfOrderObservationError",
    "TelemetryObservation",
    "UnitMismatchError",
    "UnknownJobError",
]

MICRO_USD = "micro_usd"
DEFAULT_CURRENCY = "USD"
DEFAULT_THRESHOLD_SOURCE = "reserved_upper_bound"

STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STATUS_MONITORING = "MONITORING"
STATUS_BREACHED = "BREACHED"
STATUS_COMPLETED = "COMPLETED"

REASON_NO_TELEMETRY = "NO_TELEMETRY"
REASON_NO_USABLE_TELEMETRY = "NO_USABLE_TELEMETRY"
REASON_BELOW_THRESHOLD = "BELOW_THRESHOLD"
REASON_AT_THRESHOLD = "AT_THRESHOLD"
REASON_THRESHOLD_EXCEEDED = "THRESHOLD_EXCEEDED"
REASON_ALREADY_BREACHED = "ALREADY_BREACHED"
REASON_MISSING_PROVISIONAL_COST = "MISSING_PROVISIONAL_COST"
REASON_DUPLICATE_OBSERVATION = "DUPLICATE_OBSERVATION"
REASON_COMPLETED_NO_WARNING = "COMPLETED_NO_WARNING"
REASON_COMPLETED_WITH_WARNING = "COMPLETED_WITH_WARNING"

EARLY_WARNING_LIMITATIONS = (
    (
        "research/protocol.md requires provisional spend to be compared with the "
        "reserved upper bound, but does not define how CloudWatch usage integrals "
        "are converted to currency. Callers must supply an already-computed "
        "provisional cost in one currency and unit."
    ),
    (
        "The protocol does not define a telemetry polling cadence, threshold "
        "provenance, or a masking rule for late-arriving CUR labels. This module "
        "does not implement any of those policies."
    ),
    (
        "The early warning is a one-time latched operational signal per job under "
        "the protocol's monotonic cumulative-usage assumption; it makes no "
        "budget-safety, coverage, or empirical-validity claim."
    ),
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EarlyWarningError(ValueError):
    """Base class for early-warning validation and state errors."""


class InvalidTelemetryError(EarlyWarningError):
    """Raised when a telemetry record has invalid fields."""


class InvalidTelemetryTimestampError(InvalidTelemetryError):
    """Raised when an observation timestamp is missing or unparseable."""


class MissingProvisionalCostError(InvalidTelemetryError):
    """Raised only when a required provisional cost is absent."""


class InvalidThresholdError(EarlyWarningError):
    """Raised when a registration threshold is negative or invalid."""


class MissingThresholdError(InvalidThresholdError):
    """Raised when a job is registered without a threshold."""


class UnknownJobError(EarlyWarningError):
    """Raised when a job ID has not been registered."""


class DuplicateJobError(EarlyWarningError):
    """Raised when a job ID is reused with different registration content."""


class UnitMismatchError(EarlyWarningError):
    """Raised when an observation's currency or unit differs from its job."""


class ConflictingObservationError(EarlyWarningError):
    """Raised when a source record ID is reused with different content."""


class OutOfOrderObservationError(EarlyWarningError):
    """Raised when an observation is older than the last accepted one."""


class AmbiguousObservationOrderError(EarlyWarningError):
    """Raised when two different records share an observation timestamp."""


class ObservationAfterCompletionError(EarlyWarningError):
    """Raised when a new observation arrives after job completion."""


class InvalidCompletionTimestampError(EarlyWarningError):
    """Raised when a completion timestamp is missing or unparseable."""


class OutOfOrderCompletionError(EarlyWarningError):
    """Raised when completion predates the last accepted observation."""


class CompletionConflictError(EarlyWarningError):
    """Raised when a job is completed twice with different timestamps."""


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _timestamp(value: Any, *, field: str) -> datetime:
    try:
        return _coerce_timestamp(value, field=field)
    except (TypeError, ValueError) as exc:
        raise InvalidTelemetryTimestampError(str(exc)) from exc


def _completion_timestamp(value: Any) -> datetime:
    try:
        return _coerce_timestamp(value, field="completed_at")
    except (TypeError, ValueError) as exc:
        raise InvalidCompletionTimestampError(str(exc)) from exc


def _amount(
    value: Any,
    *,
    field: str,
    required: bool = False,
    nonnegative: bool = False,
) -> int | Decimal | None:
    try:
        parsed = _coerce_amount(value, field=field)
    except (TypeError, ValueError) as exc:
        raise InvalidTelemetryError(str(exc)) from exc
    if parsed is None:
        if required:
            raise MissingProvisionalCostError(f"{field} is required")
        return None
    if nonnegative and parsed < 0:
        raise InvalidTelemetryError(f"{field} must not be negative")
    return parsed


def _threshold(value: Any, *, field: str = "threshold_micro") -> int | Decimal:
    if value is None:
        raise MissingThresholdError(f"{field} is required")
    try:
        parsed = _coerce_amount(value, field=field)
    except (TypeError, ValueError) as exc:
        raise InvalidThresholdError(str(exc)) from exc
    if parsed is None:
        raise MissingThresholdError(f"{field} is required")
    if parsed < 0:
        raise InvalidThresholdError(f"{field} must not be negative")
    return parsed


def _required_text(value: Any, *, field: str, error_type: type[EarlyWarningError]) -> str:
    if value is None:
        raise error_type(f"{field} is required")
    text = str(value).strip()
    if not text:
        raise error_type(f"{field} must not be empty")
    return text


def _optional_text(value: Any, *, field: str, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _currency(value: Any) -> str:
    return _required_text(value, field="currency", error_type=InvalidTelemetryError).upper()


def _unit(value: Any) -> str:
    return _required_text(value, field="unit", error_type=InvalidTelemetryError)


def _single(
    data: Mapping[str, Any],
    names: Iterable[str],
    *,
    default: Any = ...,
    field: str,
) -> Any:
    present = [name for name in names if name in data]
    if len(present) > 1:
        raise TypeError(f"{field} received multiple values: {present}")
    if present:
        return data[present[0]]
    if default is ...:
        raise TypeError(f"{field} is required")
    return default


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _json_number(value: int | Decimal | None) -> int | str | None:
    if value is None or isinstance(value, int):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Input observation
# ---------------------------------------------------------------------------


_JOB_KEYS = ("job_id", "prediction_id", "reservation_id", "request_id")
_OBSERVED_KEYS = (
    "observed_at",
    "observation_timestamp",
    "telemetry_timestamp",
    "timestamp",
)
_COST_KEYS = (
    "provisional_cost_micro",
    "provisional_micro",
    "observed_cost_micro",
    "cost_micro",
)
_SOURCE_RECORD_KEYS = (
    "source_record_id",
    "record_id",
    "observation_id",
    "telemetry_event_id",
)
_SOURCE_KEYS = ("source", "provenance")
_CURRENCY_KEYS = ("currency", "currency_code")
_UNIT_KEYS = ("unit", "units")


@dataclass(frozen=True)
class TelemetryObservation:
    """One real, timestamped provisional telemetry record.

    ``provisional_cost_micro`` may be ``None`` when the record was observed but
    its cost is missing.  Missing cost is retained and counted, never treated
    as zero.  ``source_record_id`` is the upstream record identifier and is
    required so every event remains auditable.
    """

    job_id: str
    observed_at: datetime
    provisional_cost_micro: int | Decimal | None
    source_record_id: str
    currency: str = DEFAULT_CURRENCY
    unit: str = MICRO_USD
    source: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "job_id",
            _required_text(self.job_id, field="job_id", error_type=InvalidTelemetryError),
        )
        object.__setattr__(
            self,
            "observed_at",
            _timestamp(self.observed_at, field="observed_at"),
        )
        object.__setattr__(
            self,
            "provisional_cost_micro",
            _amount(
                self.provisional_cost_micro,
                field="provisional_cost_micro",
                nonnegative=True,
            ),
        )
        object.__setattr__(
            self,
            "source_record_id",
            _required_text(
                self.source_record_id,
                field="source_record_id",
                error_type=InvalidTelemetryError,
            ),
        )
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))
        object.__setattr__(
            self,
            "source",
            _optional_text(self.source, field="source", default="unknown"),
        )

    @property
    def observation_id(self) -> str:
        """Alias for the preserved upstream ``source_record_id``."""
        return self.source_record_id

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> TelemetryObservation:
        """Build an observation from common telemetry/collector field names."""
        if not isinstance(data, Mapping):
            raise TypeError("observation must be a mapping")
        return cls(
            job_id=_single(data, _JOB_KEYS, field="job_id"),
            observed_at=_single(data, _OBSERVED_KEYS, field="observed_at"),
            provisional_cost_micro=_single(
                data, _COST_KEYS, default=None, field="provisional_cost_micro"
            ),
            source_record_id=_single(data, _SOURCE_RECORD_KEYS, field="source_record_id"),
            currency=_single(data, _CURRENCY_KEYS, default=DEFAULT_CURRENCY, field="currency"),
            unit=_single(data, _UNIT_KEYS, default=MICRO_USD, field="unit"),
            source=_single(data, _SOURCE_KEYS, default="unknown", field="source"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "observed_at": _iso(self.observed_at),
            "provisional_cost_micro": _json_number(self.provisional_cost_micro),
            "source_record_id": self.source_record_id,
            "currency": self.currency,
            "unit": self.unit,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Warning event and state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EarlyWarningEvent:
    """A latched threshold-crossing event, retained for audit.

    The event copies the triggering observation's source record ID and
    timestamp.  ``event_id`` is deterministic for that job/source record, so a
    replay cannot create a second ID for the same trigger.
    """

    event_id: str
    job_id: str
    source_record_id: str
    observed_at: datetime
    threshold_micro: int | Decimal
    threshold_source: str
    provisional_cost_micro: int | Decimal
    currency: str
    unit: str
    source: str
    reason_code: str = REASON_THRESHOLD_EXCEEDED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_id",
            _required_text(self.event_id, field="event_id", error_type=EarlyWarningError),
        )
        object.__setattr__(
            self,
            "job_id",
            _required_text(self.job_id, field="job_id", error_type=EarlyWarningError),
        )
        object.__setattr__(
            self,
            "source_record_id",
            _required_text(
                self.source_record_id,
                field="source_record_id",
                error_type=EarlyWarningError,
            ),
        )
        object.__setattr__(
            self,
            "observed_at",
            _timestamp(self.observed_at, field="observed_at"),
        )
        object.__setattr__(
            self,
            "threshold_micro",
            _threshold(self.threshold_micro),
        )
        object.__setattr__(
            self,
            "threshold_source",
            _required_text(
                self.threshold_source,
                field="threshold_source",
                error_type=EarlyWarningError,
            ),
        )
        object.__setattr__(
            self,
            "provisional_cost_micro",
            _amount(
                self.provisional_cost_micro,
                field="provisional_cost_micro",
                required=True,
                nonnegative=True,
            ),
        )
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))
        object.__setattr__(
            self,
            "source",
            _optional_text(self.source, field="source", default="unknown"),
        )
        object.__setattr__(
            self,
            "reason_code",
            _required_text(self.reason_code, field="reason_code", error_type=EarlyWarningError),
        )
        if self.provisional_cost_micro <= self.threshold_micro:
            raise EarlyWarningError(
                "an early-warning event requires provisional cost strictly above the threshold"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "job_id": self.job_id,
            "source_record_id": self.source_record_id,
            "observed_at": _iso(self.observed_at),
            "threshold_micro": _json_number(self.threshold_micro),
            "threshold_source": self.threshold_source,
            "provisional_cost_micro": _json_number(self.provisional_cost_micro),
            "currency": self.currency,
            "unit": self.unit,
            "source": self.source,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class EarlyWarningState:
    """Immutable snapshot of one job's early-warning state."""

    job_id: str
    status: str
    reason: str
    threshold_micro: int | Decimal
    threshold_source: str
    currency: str
    unit: str
    observation_count: int
    evaluated_count: int
    missing_cost_count: int
    last_source_record_id: str | None
    last_observed_at: datetime | None
    completed_at: datetime | None
    warning_event: EarlyWarningEvent | None
    limitations: tuple[str, ...] = EARLY_WARNING_LIMITATIONS

    @property
    def breached(self) -> bool:
        return self.warning_event is not None

    @property
    def completed(self) -> bool:
        return self.completed_at is not None

    @property
    def insufficient_data(self) -> bool:
        return self.status == STATUS_INSUFFICIENT_DATA

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "reason": self.reason,
            "threshold_micro": _json_number(self.threshold_micro),
            "threshold_source": self.threshold_source,
            "currency": self.currency,
            "unit": self.unit,
            "observation_count": self.observation_count,
            "evaluated_count": self.evaluated_count,
            "missing_cost_count": self.missing_cost_count,
            "last_source_record_id": self.last_source_record_id,
            "last_observed_at": None
            if self.last_observed_at is None
            else _iso(self.last_observed_at),
            "completed_at": None if self.completed_at is None else _iso(self.completed_at),
            "warning_event": None if self.warning_event is None else self.warning_event.to_dict(),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ObservationResult:
    """Outcome of submitting one telemetry observation."""

    accepted: bool
    reason: str
    observation: TelemetryObservation
    state: EarlyWarningState
    warning: EarlyWarningEvent | None = None

    @property
    def triggered_warning(self) -> bool:
        return self.warning is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "observation": self.observation.to_dict(),
            "state": self.state.to_dict(),
            "warning": None if self.warning is None else self.warning.to_dict(),
        }


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


@dataclass
class _JobRecord:
    job_id: str
    threshold_micro: int | Decimal
    threshold_source: str
    currency: str
    unit: str
    observations: list[TelemetryObservation] = field(default_factory=list)
    by_source_id: dict[str, TelemetryObservation] = field(default_factory=dict)
    last_observed_at: datetime | None = None
    evaluated_count: int = 0
    missing_cost_count: int = 0
    last_evaluated_reason: str | None = None
    warning_event: EarlyWarningEvent | None = None
    completed_at: datetime | None = None


class EarlyWarningMonitor:
    """Track per-job provisional telemetry and latch strict threshold crossings.

    State transitions::

        INSUFFICIENT_DATA --usable observation--> MONITORING
        MONITORING        --strict crossing--->  BREACHED
        any state         --mark_completed---->  COMPLETED

    ``BREACHED`` never returns to ``MONITORING``.  ``COMPLETED`` retains any
    earlier warning event.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _JobRecord] = {}

    # -- registration -------------------------------------------------

    def register_job(
        self,
        job_id: Any,
        threshold_micro: Any,
        *,
        currency: Any = DEFAULT_CURRENCY,
        unit: Any = MICRO_USD,
        threshold_source: Any = DEFAULT_THRESHOLD_SOURCE,
    ) -> bool:
        """Register a job and its explicit warning threshold.

        Returns ``True`` for a new registration and ``False`` for an identical
        re-registration.  A conflicting re-registration raises
        :class:`DuplicateJobError`.  The threshold is required; it is never
        inferred from prior observations.
        """
        job = _required_text(job_id, field="job_id", error_type=EarlyWarningError)
        threshold = _threshold(threshold_micro)
        curr = _currency(currency)
        units = _unit(unit)
        source = _required_text(
            threshold_source,
            field="threshold_source",
            error_type=EarlyWarningError,
        )

        existing = self._jobs.get(job)
        if existing is not None:
            if (
                existing.threshold_micro == threshold
                and existing.threshold_source == source
                and existing.currency == curr
                and existing.unit == units
            ):
                return False
            raise DuplicateJobError(
                f"job {job!r} already exists with a different threshold or unit"
            )

        self._jobs[job] = _JobRecord(
            job_id=job,
            threshold_micro=threshold,
            threshold_source=source,
            currency=curr,
            unit=units,
        )
        return True

    # -- telemetry ----------------------------------------------------

    def observe(
        self,
        observation: TelemetryObservation | Mapping[str, Any],
    ) -> ObservationResult:
        """Record one telemetry observation and evaluate the threshold.

        Duplicate identical source records are idempotent and do not advance
        the job's timestamp.  All other out-of-order observations raise.
        """
        if isinstance(observation, Mapping):
            observation = TelemetryObservation.from_mapping(observation)
        if not isinstance(observation, TelemetryObservation):
            raise TypeError("observation must be a TelemetryObservation or mapping")

        record = self._require_job(observation.job_id)
        if observation.currency != record.currency or observation.unit != record.unit:
            raise UnitMismatchError(
                f"observation unit {observation.currency}/{observation.unit} does not match "
                f"job {record.currency}/{record.unit}"
            )

        existing = record.by_source_id.get(observation.source_record_id)
        if existing is not None:
            if existing == observation:
                return ObservationResult(
                    accepted=False,
                    reason=REASON_DUPLICATE_OBSERVATION,
                    observation=observation,
                    state=self._state(record),
                )
            raise ConflictingObservationError(
                f"source record {observation.source_record_id!r} already exists "
                "with different content"
            )

        if record.completed_at is not None:
            raise ObservationAfterCompletionError(
                f"job {record.job_id!r} completed at {_iso(record.completed_at)}; "
                "late telemetry is not accepted as an early warning"
            )

        if record.last_observed_at is not None:
            if observation.observed_at < record.last_observed_at:
                raise OutOfOrderObservationError(
                    f"observation at {_iso(observation.observed_at)} precedes the last "
                    f"accepted observation at {_iso(record.last_observed_at)}"
                )
            if observation.observed_at == record.last_observed_at:
                raise AmbiguousObservationOrderError(
                    "a different source record uses the same observation timestamp; "
                    "supply distinct timestamps or an explicit ordering policy"
                )

        record.observations.append(observation)
        record.by_source_id[observation.source_record_id] = observation
        record.last_observed_at = observation.observed_at

        cost = observation.provisional_cost_micro
        if cost is None:
            record.missing_cost_count += 1
            return ObservationResult(
                accepted=True,
                reason=REASON_MISSING_PROVISIONAL_COST,
                observation=observation,
                state=self._state(record),
            )

        record.evaluated_count += 1
        if cost > record.threshold_micro:
            record.last_evaluated_reason = REASON_THRESHOLD_EXCEEDED
            if record.warning_event is None:
                event = EarlyWarningEvent(
                    event_id=(f"{record.job_id}:early-warning:{observation.source_record_id}"),
                    job_id=record.job_id,
                    source_record_id=observation.source_record_id,
                    observed_at=observation.observed_at,
                    threshold_micro=record.threshold_micro,
                    threshold_source=record.threshold_source,
                    provisional_cost_micro=cost,
                    currency=record.currency,
                    unit=record.unit,
                    source=observation.source,
                )
                record.warning_event = event
                return ObservationResult(
                    accepted=True,
                    reason=REASON_THRESHOLD_EXCEEDED,
                    observation=observation,
                    state=self._state(record),
                    warning=event,
                )
            return ObservationResult(
                accepted=True,
                reason=REASON_ALREADY_BREACHED,
                observation=observation,
                state=self._state(record),
            )

        if cost == record.threshold_micro:
            record.last_evaluated_reason = REASON_AT_THRESHOLD
            reason = REASON_AT_THRESHOLD
        else:
            record.last_evaluated_reason = REASON_BELOW_THRESHOLD
            reason = REASON_BELOW_THRESHOLD
        return ObservationResult(
            accepted=True,
            reason=reason,
            observation=observation,
            state=self._state(record),
        )

    # -- completion ---------------------------------------------------

    def mark_completed(self, job_id: Any, completed_at: Any) -> bool:
        """Mark a job complete and preserve the completion timestamp.

        Completion may equal the last observation timestamp but cannot precede
        it.  Repeating the same completion is idempotent; a different
        completion timestamp raises :class:`CompletionConflictError`.
        """
        job = _required_text(job_id, field="job_id", error_type=EarlyWarningError)
        record = self._require_job(job)
        completed = _completion_timestamp(completed_at)

        if record.last_observed_at is not None and completed < record.last_observed_at:
            raise OutOfOrderCompletionError(
                f"completion at {_iso(completed)} precedes the last accepted "
                f"observation at {_iso(record.last_observed_at)}"
            )
        if record.completed_at is not None:
            if record.completed_at == completed:
                return False
            raise CompletionConflictError(
                f"job {record.job_id!r} is already completed at {_iso(record.completed_at)}"
            )
        record.completed_at = completed
        return True

    # -- queries ------------------------------------------------------

    @property
    def job_states(self) -> tuple[EarlyWarningState, ...]:
        return tuple(self._state(self._jobs[job]) for job in sorted(self._jobs))

    @property
    def warnings(self) -> tuple[EarlyWarningEvent, ...]:
        events = [
            record.warning_event
            for record in self._jobs.values()
            if record.warning_event is not None
        ]
        return tuple(sorted(events, key=lambda event: (event.observed_at, event.event_id)))

    def state_for(self, job_id: Any) -> EarlyWarningState:
        return self._state(self._require_job(job_id))

    def observations_for(self, job_id: Any) -> tuple[TelemetryObservation, ...]:
        return tuple(self._require_job(job_id).observations)

    def _require_job(self, job_id: Any) -> _JobRecord:
        job = _required_text(job_id, field="job_id", error_type=EarlyWarningError)
        record = self._jobs.get(job)
        if record is None:
            raise UnknownJobError(f"unknown job {job!r}")
        return record

    def _state(self, record: _JobRecord) -> EarlyWarningState:
        if record.completed_at is not None:
            status = STATUS_COMPLETED
            reason = (
                REASON_COMPLETED_WITH_WARNING
                if record.warning_event is not None
                else REASON_COMPLETED_NO_WARNING
            )
        elif record.warning_event is not None:
            status = STATUS_BREACHED
            reason = REASON_THRESHOLD_EXCEEDED
        elif record.evaluated_count:
            status = STATUS_MONITORING
            reason = record.last_evaluated_reason or REASON_BELOW_THRESHOLD
        else:
            status = STATUS_INSUFFICIENT_DATA
            reason = REASON_NO_TELEMETRY if not record.observations else REASON_NO_USABLE_TELEMETRY

        last = record.observations[-1] if record.observations else None
        return EarlyWarningState(
            job_id=record.job_id,
            status=status,
            reason=reason,
            threshold_micro=record.threshold_micro,
            threshold_source=record.threshold_source,
            currency=record.currency,
            unit=record.unit,
            observation_count=len(record.observations),
            evaluated_count=record.evaluated_count,
            missing_cost_count=record.missing_cost_count,
            last_source_record_id=None if last is None else last.source_record_id,
            last_observed_at=None if last is None else last.observed_at,
            completed_at=record.completed_at,
            warning_event=record.warning_event,
        )
