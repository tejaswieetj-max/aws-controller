"""Online conformal calibration over observed final-cost labels.

Scope
-----
This module is a pure, in-process state machine.  It accepts:

* a registered prediction/job (the modeled or reserved cost and the time the
  prediction was made), and
* final-cost labels that were actually observed, with their observation
  timestamps and label-version identity.

It does not create AWS clients, read files, write databases, or generate
labels, delays, prices, or synthetic residuals.  Callers must supply real
final-cost observations.

What is calibrated
------------------
The nonconformity score is the signed residual::

    score = final_cost - predicted_cost

Both sides use the prediction's unit (normally integer micro-USD) and
currency.  The one-sided upper conformal bound for a new prediction is::

    upper_bound = predicted_cost + quantile(scores, 1 - alpha)

Adaptive conformal inference (ACI) updates the risk parameter after each
label that becomes available::

    miss = 1 if score > previous_upper_quantile else 0
    alpha_next = clip(alpha + gamma * (target_alpha - miss), alpha_min, alpha_max)

A miss (``miss=1``) decreases ``alpha`` by ``gamma * (1 - target_alpha)``,
which raises the ``1 - alpha`` quantile and widens the bound.  A covered
residual increases ``alpha`` by ``gamma * target_alpha``, which tightens the
bound.  Both moves are unconditional nudges, not projections onto
``target_alpha``: the expected increment is zero when the observed miss rate
equals ``target_alpha``, even if ``alpha`` itself differs from the target.
Equality counts as covered because the bound is one-sided.
The first label has no prior bound; it is treated as covered by the vacuous
interval and therefore applies the cover-direction increment.

Delayed, out-of-order, and revised labels
-----------------------------------------
A label version is identified by ``label_source_id`` (for example a
reconciliation or settlement ID).  A prediction contributes at most one
current score.  Adding a revision replaces the prior score rather than adding
an independent job.  Explicit ``revision_of`` lineage is preferred.  When
lineage is absent, a later ``first_seen_at`` supersedes the current version;
``report_version_order`` can disambiguate labels first seen at the same time
or arriving out of order.  Ambiguous, stale, forked, or unknown lineage is
rejected explicitly instead of being guessed.

Conformal state is replayed deterministically from the *current* label of each
prediction, ordered by ``(first_seen_at, prediction_timestamp,
prediction_id)``.  Arrival order therefore does not affect the result, and a
revised record never becomes a second sample.  Because a revision triggers a
full replay, the historical ``alpha`` sequence can change once a corrected
label is known; the state is a current best reconstruction, not an immutable
append-only log.

Timestamps
----------
``first_seen_at`` is the real time a label version was first observed by the
collector.  It defaults to ``observed_at`` but may be earlier when a label was
first seen in an earlier snapshot and processed later.  ``observed_at`` records
when this particular sighting happened.  Charge-period timestamps are not
observation timestamps and must not be passed here as ``observed_at``.

Provisional telemetry
---------------------
``EarlyWarning`` events are kept in a separate log.  They never add scores,
change ``alpha``, or affect a bound.  They are operational early-warning
telemetry, not final provider-billed cost.

No coverage guarantee
---------------------
ACI is a sequential heuristic under non-stationary, delayed, and revised
feedback.  This module makes **no finite-sample or long-run coverage
guarantee**.  A finite-sample split-conformal guarantee would require a frozen
exchangeable calibration partition and a prespecified quantile; those
assumptions are neither established nor checked here.  ``coverage_guarantee``
is therefore ``None`` and the reported miss rate is descriptive only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation, localcontext
from enum import Enum
from math import isfinite
from typing import Any

# Reuse the estimator's coercion rules so timestamp and money semantics stay
# consistent across the package.  These helpers perform no I/O.
from .estimator import _coerce_amount, _coerce_timestamp, _normalise_number

__all__ = [
    "CALIBRATION_LIMITATIONS",
    "COVERAGE_STATEMENT",
    "DEFAULT_ALPHA_MAX",
    "DEFAULT_ALPHA_MIN",
    "DEFAULT_CURRENCY",
    "DEFAULT_GAMMA",
    "DEFAULT_TARGET_ALPHA",
    "MICRO_USD",
    "STATUS_INSUFFICIENT_DATA",
    "STATUS_OK",
    "AmbiguousRevisionError",
    "BoundStatus",
    "ConflictingEarlyWarningError",
    "ConflictingLabelError",
    "ConformalBound",
    "ConformalCalibrator",
    "ConformalError",
    "ConformalMixedUnitError",
    "ConformalState",
    "DuplicatePredictionError",
    "EarlyWarning",
    "FinalLabel",
    "InvalidEarlyWarningError",
    "InvalidLabelError",
    "InvalidPredictionError",
    "InvalidTimestampError",
    "LabelUpdate",
    "MissingFinalCostError",
    "MixedUnitError",
    "Prediction",
    "ProvisionalLabelError",
    "RevisionLineageError",
    "ScoreObservation",
    "StaleLabelError",
    "UnknownPredictionError",
    "calibrate",
    "nearest_rank_quantile",
    "residual",
    "update_alpha",
]

MICRO_USD = "micro_usd"
DEFAULT_CURRENCY = "USD"
DEFAULT_TARGET_ALPHA = Decimal("0.05")
DEFAULT_GAMMA = Decimal("0.02")
DEFAULT_ALPHA_MIN = Decimal("0.001")
DEFAULT_ALPHA_MAX = Decimal("0.5")
GAMMA_MAX = Decimal("0.5")

STATUS_OK = "OK"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

COVERAGE_STATEMENT = (
    "No finite-sample or long-run coverage guarantee is claimed. Adaptive "
    "conformal inference is a sequential heuristic under delayed, revised, "
    "and non-exchangeable feedback."
)

CALIBRATION_LIMITATIONS = (
    COVERAGE_STATEMENT,
    (
        "A split-conformal finite-sample guarantee requires a frozen calibration "
        "partition and exchangeability; neither is established by this module."
    ),
    (
        "Only real observed final-cost labels with actual observation timestamps "
        "may be supplied; missing labels are reported, never imputed."
    ),
    (
        "Provisional telemetry early warnings are separate operational signals and "
        "never update the conformal scores, alpha, or bound."
    ),
    (
        "A revision replaces the current score for its prediction; it is not a new "
        "independent job. Historical alpha values can change after a replay."
    ),
)

_MISSING = object()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConformalError(ValueError):
    """Base class for conformal calibration errors."""


class InvalidPredictionError(ConformalError):
    """Raised when a prediction has invalid fields."""


class InvalidLabelError(ConformalError):
    """Raised when a final label has invalid fields."""


class InvalidTimestampError(ConformalError):
    """Raised when a timestamp cannot be interpreted."""


class MissingFinalCostError(InvalidLabelError):
    """Raised when a final label has no observed final cost."""


class ProvisionalLabelError(InvalidLabelError):
    """Raised when provisional data is submitted as a final label."""


class UnknownPredictionError(ConformalError):
    """Raised when a label references a prediction that was never registered."""


class DuplicatePredictionError(ConformalError):
    """Raised when a prediction ID is reused with different content."""


class ConflictingLabelError(ConformalError):
    """Raised when a label source ID is reused with different content."""


class StaleLabelError(ConformalError):
    """Raised when a label is older than the current version for its job."""


class AmbiguousRevisionError(ConformalError):
    """Raised when two label versions cannot be ordered without guessing."""


class RevisionLineageError(ConformalError):
    """Raised for unknown, forked, cyclic, or cross-job revision lineage."""


class MixedUnitError(ConformalError):
    """Raised when a calibrator would combine different units or currencies."""


# The package-level ``MixedUnitError`` is the estimator's class.  This alias
# lets callers name the conformal error unambiguously.
ConformalMixedUnitError = MixedUnitError


class InvalidEarlyWarningError(ConformalError):
    """Raised when a provisional early-warning event is invalid."""


class ConflictingEarlyWarningError(ConformalError):
    """Raised when an early-warning event ID is reused with different content."""


class BoundStatus(str, Enum):
    """Status of a requested conformal bound."""

    OK = STATUS_OK
    INSUFFICIENT_DATA = STATUS_INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------


def _timestamp(value: Any, *, field: str) -> datetime:
    try:
        return _coerce_timestamp(value, field=field)
    except (TypeError, ValueError) as exc:
        raise InvalidTimestampError(str(exc)) from exc


def _amount(value: Any, *, field: str, required: bool = False) -> int | Decimal | None:
    try:
        parsed = _coerce_amount(value, field=field)
    except (TypeError, ValueError) as exc:
        raise InvalidLabelError(str(exc)) from exc
    if required and parsed is None:
        raise MissingFinalCostError(f"{field} is required")
    return parsed


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ConformalError(f"{field} must be a finite number")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not isfinite(value):
            raise ConformalError(f"{field} must be finite")
        number = Decimal(str(value))
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ConformalError(f"{field} must be a finite number") from exc
    else:
        raise ConformalError(f"{field} must be a finite number")
    if not number.is_finite():
        raise ConformalError(f"{field} must be finite")
    return number


def _text(value: Any, *, field: str, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ConformalError(f"{field} is required")
        return None
    text = str(value).strip()
    if required and not text:
        raise ConformalError(f"{field} must not be empty")
    return text or None


def _identifier(value: Any, *, field: str) -> str:
    text = _text(value, field=field, required=True)
    assert text is not None
    return text


def _currency(value: Any) -> str:
    text = _text(value, field="currency", required=True)
    assert text is not None
    return text.upper()


def _unit(value: Any) -> str:
    text = _text(value, field="unit", required=True)
    assert text is not None
    return text


def _single(
    data: Mapping[str, Any],
    names: Iterable[str],
    *,
    default: Any = _MISSING,
    field: str,
) -> Any:
    present = [name for name in names if name in data]
    if len(present) > 1:
        raise TypeError(f"{field} received multiple values: {present}")
    if present:
        return data[present[0]]
    if default is _MISSING:
        raise TypeError(f"{field} is required")
    return default


def _digit_span(value: int | Decimal) -> int:
    return len(str(abs(value)))


def _add(left: int | Decimal, right: int | Decimal) -> int | Decimal:
    if isinstance(left, int) and isinstance(right, int):
        return left + right
    with localcontext() as context:
        context.prec = max(50, _digit_span(left) + _digit_span(right) + 10)
        result = Decimal(left) + Decimal(right)
    return _normalise_number(result)


def _subtract(left: int | Decimal, right: int | Decimal) -> int | Decimal:
    if isinstance(left, int) and isinstance(right, int):
        return left - right
    with localcontext() as context:
        context.prec = max(50, _digit_span(left) + _digit_span(right) + 10)
        result = Decimal(left) - Decimal(right)
    return _normalise_number(result)


# ---------------------------------------------------------------------------
# Public score arithmetic
# ---------------------------------------------------------------------------


def residual(
    final_cost: Any,
    predicted_cost: Any,
) -> int | Decimal:
    """Return ``final_cost - predicted_cost`` without unit conversion.

    Both values must be finite numbers.  Integral results are returned as
    ``int``; fractional results remain exact ``Decimal`` values.
    """
    actual = _amount(final_cost, field="final_cost", required=True)
    predicted = _amount(predicted_cost, field="predicted_cost", required=True)
    assert actual is not None and predicted is not None
    return _subtract(actual, predicted)


def nearest_rank_quantile(
    values: Iterable[int | Decimal],
    level: Any,
) -> int | Decimal | None:
    """Return the nearest-rank empirical quantile, or ``None`` when empty.

    With ``n`` values the rank is ``ceil(level * n)`` clamped to ``[1, n]``.
    This is the same empirical rule used by the existing Spendctl calibration
    helper.  No distributional or coverage claim follows from this statistic.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    probability = _decimal(level, field="level")
    if not (Decimal(0) < probability <= Decimal(1)):
        raise ConformalError("level must be in (0, 1]")
    rank = int((probability * len(ordered)).to_integral_value(rounding=ROUND_CEILING))
    rank = max(1, min(len(ordered), rank))
    return ordered[rank - 1]


def update_alpha(
    alpha: Any,
    *,
    target_alpha: Any,
    gamma: Any,
    miss: int | bool,
    alpha_min: Any = DEFAULT_ALPHA_MIN,
    alpha_max: Any = DEFAULT_ALPHA_MAX,
) -> Decimal:
    """Apply one adaptive-conformal update and clip ``alpha`` to its bounds.

    ``alpha_next = clip(alpha + gamma * (target_alpha - miss))``.

    A miss (``miss=1``) subtracts ``gamma * (1 - target_alpha)`` from ``alpha``
    (more conservative, wider bound); a cover (``miss=0``) adds
    ``gamma * target_alpha`` (tighter bound).  These are unconditional ACI
    nudges, so a cover raises ``alpha`` even when it is already above the
    target; clipping at ``alpha_max`` limits that drift.  ``alpha`` must
    already lie inside ``[alpha_min, alpha_max]``; ``target_alpha`` must lie
    inside the same interval.
    """
    low = _decimal(alpha_min, field="alpha_min")
    high = _decimal(alpha_max, field="alpha_max")
    if not (Decimal(0) < low <= high < Decimal(1)):
        raise ConformalError("alpha bounds must satisfy 0 < alpha_min <= alpha_max < 1")
    target = _decimal(target_alpha, field="target_alpha")
    if not (low <= target <= high):
        raise ConformalError("target_alpha must lie within [alpha_min, alpha_max]")
    step = _decimal(gamma, field="gamma")
    if not (Decimal(0) < step <= GAMMA_MAX):
        raise ConformalError(f"gamma must be in (0, {GAMMA_MAX}]")
    current = _decimal(alpha, field="alpha")
    if not (low <= current <= high):
        raise ConformalError("alpha must lie within [alpha_min, alpha_max]")
    if isinstance(miss, bool) or miss not in (0, 1):
        raise ConformalError("miss must be 0 or 1")
    with localcontext() as context:
        context.prec = 50
        candidate = current + step * (target - Decimal(int(miss)))
    if candidate < low:
        return low
    if candidate > high:
        return high
    return candidate.normalize()


# ---------------------------------------------------------------------------
# Observations and results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreObservation:
    """One conformal update, retained for transparent replay and auditing."""

    prediction_id: str
    prediction_timestamp: datetime
    label_source_id: str
    report_version_id: str | None
    observed_at: datetime
    first_seen_at: datetime
    currency: str
    unit: str
    score_micro: int | Decimal
    threshold_micro: int | Decimal | None
    miss: bool
    alpha_before: Decimal
    alpha_after: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "prediction_timestamp": _iso(self.prediction_timestamp),
            "label_source_id": self.label_source_id,
            "report_version_id": self.report_version_id,
            "observed_at": _iso(self.observed_at),
            "first_seen_at": _iso(self.first_seen_at),
            "currency": self.currency,
            "unit": self.unit,
            "score_micro": _json_number(self.score_micro),
            "threshold_micro": _json_number(self.threshold_micro),
            "miss": self.miss,
            "alpha_before": str(self.alpha_before),
            "alpha_after": str(self.alpha_after),
        }


@dataclass(frozen=True)
class ConformalState:
    """Immutable snapshot of the calibrator's conformal state."""

    alpha: Decimal
    target_alpha: Decimal
    gamma: Decimal
    quantile_level: Decimal
    threshold_micro: int | Decimal | None
    score_count: int
    miss_count: int
    covered_count: int
    score_observations: tuple[ScoreObservation, ...]
    current_label_count: int
    label_version_count: int
    revision_count: int
    pending_prediction_count: int
    unscorable_prediction_count: int
    early_warning_count: int
    coverage_guarantee: str | None
    limitations: tuple[str, ...]

    @property
    def observed_miss_rate(self) -> float | None:
        """Descriptive in-sample miss frequency; never a coverage guarantee."""
        if not self.score_count:
            return None
        return self.miss_count / self.score_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": str(self.alpha),
            "target_alpha": str(self.target_alpha),
            "gamma": str(self.gamma),
            "quantile_level": str(self.quantile_level),
            "threshold_micro": _json_number(self.threshold_micro),
            "score_count": self.score_count,
            "miss_count": self.miss_count,
            "covered_count": self.covered_count,
            "observed_miss_rate": self.observed_miss_rate,
            "score_observations": [item.to_dict() for item in self.score_observations],
            "current_label_count": self.current_label_count,
            "label_version_count": self.label_version_count,
            "revision_count": self.revision_count,
            "pending_prediction_count": self.pending_prediction_count,
            "unscorable_prediction_count": self.unscorable_prediction_count,
            "early_warning_count": self.early_warning_count,
            "coverage_guarantee": self.coverage_guarantee,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class ConformalBound:
    """Explicit result of requesting an upper conformal bound."""

    status: str
    prediction_id: str
    predicted_cost_micro: int | Decimal | None
    upper_bound_micro: int | Decimal | None
    alpha: Decimal
    quantile_level: Decimal
    score_count: int
    currency: str | None
    unit: str | None
    reason: str | None = None
    notes: tuple[str, ...] = ()
    coverage_guarantee: str | None = None

    @property
    def is_sufficient(self) -> bool:
        return self.status == STATUS_OK

    @property
    def insufficient_data(self) -> bool:
        return self.status == STATUS_INSUFFICIENT_DATA

    def require_bound(self) -> int | Decimal:
        """Return the bound or raise if no bound can be computed."""
        if self.upper_bound_micro is None:
            raise ConformalError(
                f"insufficient data: {self.reason or 'no conformal bound available'}"
            )
        return self.upper_bound_micro

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "prediction_id": self.prediction_id,
            "predicted_cost_micro": _json_number(self.predicted_cost_micro),
            "upper_bound_micro": _json_number(self.upper_bound_micro),
            "alpha": str(self.alpha),
            "quantile_level": str(self.quantile_level),
            "score_count": self.score_count,
            "currency": self.currency,
            "unit": self.unit,
            "reason": self.reason,
            "notes": list(self.notes),
            "coverage_guarantee": self.coverage_guarantee,
        }


@dataclass(frozen=True)
class LabelUpdate:
    """Outcome of adding one final-label version."""

    applied: bool
    reason: str
    prediction_id: str
    label_source_id: str
    replaced_label_source_id: str | None = None


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _json_number(value: int | Decimal | None) -> int | str | None:
    if value is None or isinstance(value, int):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Input records
# ---------------------------------------------------------------------------


_PREDICTION_ID_KEYS = ("prediction_id", "job_id", "reservation_id", "request_id")
_PREDICTION_TIME_KEYS = (
    "prediction_timestamp",
    "prediction_time",
    "decision_timestamp",
    "created_at",
)
_PREDICTED_COST_KEYS = (
    "predicted_cost_micro",
    "predicted_micro",
    "modeled_cost_micro",
    "estimated_cost_micro",
)
_CURRENCY_KEYS = ("currency", "currency_code")
_UNIT_KEYS = ("unit", "units")


@dataclass(frozen=True)
class Prediction:
    """A registered job prediction.

    ``prediction_timestamp`` is when the prediction was made, not when its
    label arrived.  ``predicted_cost_micro`` may be ``None`` when the model
    cost is genuinely missing; such a prediction remains visible as pending or
    unscorable, but cannot contribute a residual score.
    """

    prediction_id: str
    prediction_timestamp: datetime
    predicted_cost_micro: int | Decimal | None = None
    currency: str = DEFAULT_CURRENCY
    unit: str = MICRO_USD

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "prediction_id", _identifier(self.prediction_id, field="prediction_id")
        )
        object.__setattr__(
            self,
            "prediction_timestamp",
            _timestamp(self.prediction_timestamp, field="prediction_timestamp"),
        )
        object.__setattr__(
            self,
            "predicted_cost_micro",
            _amount(self.predicted_cost_micro, field="predicted_cost_micro"),
        )
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Prediction:
        """Build a prediction from common Spendctl/research field names."""
        if not isinstance(data, Mapping):
            raise TypeError("prediction must be a mapping")
        return cls(
            prediction_id=_single(data, _PREDICTION_ID_KEYS, field="prediction_id"),
            prediction_timestamp=_single(data, _PREDICTION_TIME_KEYS, field="prediction_timestamp"),
            predicted_cost_micro=_single(
                data, _PREDICTED_COST_KEYS, default=None, field="predicted_cost_micro"
            ),
            currency=_single(data, _CURRENCY_KEYS, default=DEFAULT_CURRENCY, field="currency"),
            unit=_single(data, _UNIT_KEYS, default=MICRO_USD, field="unit"),
        )


_FINAL_PREDICTION_KEYS = ("prediction_id", "job_id", "reservation_id", "request_id")
_FINAL_COST_KEYS = (
    "final_cost_micro",
    "actual_micro",
    "final_cost",
    "actual_cost_micro",
    "cost_micro",
)
_LABEL_OBSERVED_KEYS = ("observed_at", "observation_timestamp", "received_at")
_LABEL_FIRST_SEEN_KEYS = (
    "first_seen_at",
    "first_observed_at",
    "first_observed_timestamp",
)
_LABEL_SOURCE_KEYS = (
    "label_source_id",
    "reconciliation_id",
    "settlement_id",
    "label_id",
    "source_id",
)
_REPORT_VERSION_KEYS = ("report_version_id", "source_revision", "report_id", "report_version")
_REPORT_ORDER_KEYS = ("report_version_order", "source_revision_order")
_REVISION_OF_KEYS = ("revision_of", "previous_label_id", "supersedes")
_IS_FINAL_KEYS = ("is_final", "final")


@dataclass(frozen=True)
class FinalLabel:
    """One observed final-cost label version for a prediction.

    ``label_source_id`` identifies this version and is the duplicate key.
    ``revision_of`` explicitly names the version this label supersedes;
    ``report_version_id`` identifies the export/report snapshot and
    ``report_version_order`` its caller-supplied order within that report.
    """

    prediction_id: str
    final_cost_micro: int | Decimal
    observed_at: datetime
    label_source_id: str
    first_seen_at: datetime | None = None
    report_version_id: str | None = None
    report_version_order: str | None = None
    revision_of: str | None = None
    currency: str = DEFAULT_CURRENCY
    unit: str = MICRO_USD
    is_final: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "prediction_id", _identifier(self.prediction_id, field="prediction_id")
        )
        object.__setattr__(
            self,
            "final_cost_micro",
            _amount(self.final_cost_micro, field="final_cost_micro", required=True),
        )
        observed = _timestamp(self.observed_at, field="observed_at")
        object.__setattr__(self, "observed_at", observed)
        if self.first_seen_at is None:
            first_seen = observed
        else:
            first_seen = _timestamp(self.first_seen_at, field="first_seen_at")
        if first_seen > observed:
            raise InvalidLabelError("first_seen_at cannot be after observed_at")
        object.__setattr__(self, "first_seen_at", first_seen)
        object.__setattr__(
            self, "label_source_id", _identifier(self.label_source_id, field="label_source_id")
        )
        object.__setattr__(
            self,
            "report_version_id",
            _text(self.report_version_id, field="report_version_id", required=False),
        )
        object.__setattr__(
            self,
            "report_version_order",
            _text(self.report_version_order, field="report_version_order", required=False),
        )
        revision_of = _text(self.revision_of, field="revision_of", required=False)
        if revision_of is not None and revision_of == self.label_source_id:
            raise RevisionLineageError("a label cannot revise itself")
        object.__setattr__(self, "revision_of", revision_of)
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))
        if not isinstance(self.is_final, bool):
            raise InvalidLabelError("is_final must be a bool")
        if not self.is_final:
            raise ProvisionalLabelError(
                "provisional telemetry cannot be added as a final label; use record_early_warning"
            )

    @property
    def available_at(self) -> datetime:
        """Real first-observation time used for deterministic replay."""
        assert self.first_seen_at is not None
        return self.first_seen_at

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> FinalLabel:
        """Build a label from common billing/ledger field names."""
        if not isinstance(data, Mapping):
            raise TypeError("label must be a mapping")
        return cls(
            prediction_id=_single(data, _FINAL_PREDICTION_KEYS, field="prediction_id"),
            final_cost_micro=_single(data, _FINAL_COST_KEYS, field="final_cost_micro"),
            observed_at=_single(data, _LABEL_OBSERVED_KEYS, field="observed_at"),
            label_source_id=_single(data, _LABEL_SOURCE_KEYS, field="label_source_id"),
            first_seen_at=_single(
                data, _LABEL_FIRST_SEEN_KEYS, default=None, field="first_seen_at"
            ),
            report_version_id=_single(
                data, _REPORT_VERSION_KEYS, default=None, field="report_version_id"
            ),
            report_version_order=_single(
                data, _REPORT_ORDER_KEYS, default=None, field="report_version_order"
            ),
            revision_of=_single(data, _REVISION_OF_KEYS, default=None, field="revision_of"),
            currency=_single(data, _CURRENCY_KEYS, default=DEFAULT_CURRENCY, field="currency"),
            unit=_single(data, _UNIT_KEYS, default=MICRO_USD, field="unit"),
            is_final=_single(data, _IS_FINAL_KEYS, default=True, field="is_final"),
        )


_EARLY_PREDICTION_KEYS = ("prediction_id", "job_id", "reservation_id", "request_id")
_EARLY_EVENT_KEYS = ("event_id", "warning_id", "telemetry_event_id")
_EARLY_OBSERVED_KEYS = ("observed_at", "observation_timestamp", "telemetry_timestamp")
_EARLY_COST_KEYS = (
    "provisional_cost_micro",
    "provisional_micro",
    "observed_cost_micro",
)
_EARLY_RESERVED_KEYS = ("reserved_micro", "upper_bound_micro", "bound_micro")


@dataclass(frozen=True)
class EarlyWarning:
    """Provisional telemetry event kept outside the conformal update path."""

    prediction_id: str
    event_id: str
    observed_at: datetime
    provisional_cost_micro: int | Decimal
    reserved_micro: int | Decimal | None = None
    currency: str = DEFAULT_CURRENCY
    unit: str = MICRO_USD
    first_seen_at: datetime | None = None
    source: str = "provisional_telemetry"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "prediction_id", _identifier(self.prediction_id, field="prediction_id")
        )
        object.__setattr__(self, "event_id", _identifier(self.event_id, field="event_id"))
        observed = _timestamp(self.observed_at, field="observed_at")
        object.__setattr__(self, "observed_at", observed)
        if self.first_seen_at is None:
            first_seen = observed
        else:
            first_seen = _timestamp(self.first_seen_at, field="first_seen_at")
        if first_seen > observed:
            raise InvalidEarlyWarningError("first_seen_at cannot be after observed_at")
        object.__setattr__(self, "first_seen_at", first_seen)
        object.__setattr__(
            self,
            "provisional_cost_micro",
            _amount(
                self.provisional_cost_micro,
                field="provisional_cost_micro",
                required=True,
            ),
        )
        object.__setattr__(
            self,
            "reserved_micro",
            _amount(self.reserved_micro, field="reserved_micro"),
        )
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))
        object.__setattr__(self, "source", _text(self.source, field="source", required=True))

    @property
    def breach(self) -> bool | None:
        """Whether provisional cost exceeds the reservation, if one was given."""
        if self.reserved_micro is None:
            return None
        assert isinstance(self.provisional_cost_micro, (int, Decimal))
        assert isinstance(self.reserved_micro, (int, Decimal))
        return self.provisional_cost_micro > self.reserved_micro

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> EarlyWarning:
        if not isinstance(data, Mapping):
            raise TypeError("early warning must be a mapping")
        return cls(
            prediction_id=_single(data, _EARLY_PREDICTION_KEYS, field="prediction_id"),
            event_id=_single(data, _EARLY_EVENT_KEYS, field="event_id"),
            observed_at=_single(data, _EARLY_OBSERVED_KEYS, field="observed_at"),
            provisional_cost_micro=_single(data, _EARLY_COST_KEYS, field="provisional_cost_micro"),
            reserved_micro=_single(
                data, _EARLY_RESERVED_KEYS, default=None, field="reserved_micro"
            ),
            currency=_single(data, _CURRENCY_KEYS, default=DEFAULT_CURRENCY, field="currency"),
            unit=_single(data, _UNIT_KEYS, default=MICRO_USD, field="unit"),
            first_seen_at=_single(
                data, _LABEL_FIRST_SEEN_KEYS, default=None, field="first_seen_at"
            ),
            source=_single(
                data, ("source", "provenance"), default="provisional_telemetry", field="source"
            ),
        )


# ---------------------------------------------------------------------------
# Calibrator
# ---------------------------------------------------------------------------


def _version_order(label: FinalLabel) -> tuple[int, int, str] | None:
    """Return a comparable caller-supplied version order, or ``None``.

    Digit-only orders compare numerically so ``"10"`` follows ``"9"``.  Other
    orders compare as text.  This is only a tie-breaker; explicit
    ``revision_of`` lineage always takes precedence.
    """
    if label.report_version_order is None:
        return None
    text = label.report_version_order
    if text.isdigit():
        return (0, int(text), "")
    return (1, 0, text)


class ConformalCalibrator:
    """Track final labels and replay adaptive conformal inference.

    Parameters
    ----------
    target_alpha:
        Long-run risk target in ``(0, 1)``; default ``0.05``.
    gamma:
        ACI step size in ``(0, 0.5]``; default ``0.02``.
    alpha_min / alpha_max:
        Projection bounds, default ``[0.001, 0.5]``.  ``target_alpha`` must lie
        inside this interval.
    initial_alpha:
        Alpha at the start of every replay; defaults to ``target_alpha``.
    min_scores:
        Minimum number of scored jobs required before ``bound_for`` returns a
        value.  Never fabricates a fallback when there are too few labels.
    """

    def __init__(
        self,
        *,
        target_alpha: Any = DEFAULT_TARGET_ALPHA,
        gamma: Any = DEFAULT_GAMMA,
        alpha_min: Any = DEFAULT_ALPHA_MIN,
        alpha_max: Any = DEFAULT_ALPHA_MAX,
        initial_alpha: Any = None,
        min_scores: int = 1,
    ) -> None:
        low = _decimal(alpha_min, field="alpha_min")
        high = _decimal(alpha_max, field="alpha_max")
        if not (Decimal(0) < low <= high < Decimal(1)):
            raise ConformalError("alpha bounds must satisfy 0 < alpha_min <= alpha_max < 1")
        target = _decimal(target_alpha, field="target_alpha")
        if not (low <= target <= high):
            raise ConformalError("target_alpha must lie within [alpha_min, alpha_max]")
        step = _decimal(gamma, field="gamma")
        if not (Decimal(0) < step <= GAMMA_MAX):
            raise ConformalError(f"gamma must be in (0, {GAMMA_MAX}]")
        start = target if initial_alpha is None else _decimal(initial_alpha, field="initial_alpha")
        if not (low <= start <= high):
            raise ConformalError("initial_alpha must lie within [alpha_min, alpha_max]")
        if isinstance(min_scores, bool) or not isinstance(min_scores, int):
            raise TypeError("min_scores must be an integer")
        if min_scores < 1:
            raise ValueError("min_scores must be at least 1")

        self.target_alpha = target
        self.gamma = step
        self.alpha_min = low
        self.alpha_max = high
        self.initial_alpha = start
        self.min_scores = min_scores

        self._predictions: dict[str, Prediction] = {}
        self._labels: dict[str, FinalLabel] = {}
        self._current: dict[str, FinalLabel] = {}
        self._parent: dict[str, str | None] = {}
        self._superseded_by: dict[str, str] = {}
        self._warnings: dict[str, EarlyWarning] = {}
        self._scores: tuple[int | Decimal, ...] = ()
        self._score_observations: tuple[ScoreObservation, ...] = ()
        self._alpha = start
        self._miss_count = 0
        self._covered_count = 0
        self._rebuild()

    # -- registry -----------------------------------------------------

    @property
    def predictions(self) -> tuple[Prediction, ...]:
        return tuple(self._predictions.values())

    @property
    def current_labels(self) -> tuple[FinalLabel, ...]:
        return tuple(self._current.values())

    @property
    def label_versions(self) -> tuple[FinalLabel, ...]:
        return tuple(self._labels.values())

    @property
    def early_warnings(self) -> tuple[EarlyWarning, ...]:
        return tuple(
            sorted(
                self._warnings.values(), key=lambda warning: (warning.observed_at, warning.event_id)
            )
        )

    @property
    def alpha(self) -> Decimal:
        return self._alpha

    def _assert_compatible(self, currency: str, unit: str) -> None:
        if not self._predictions:
            return
        reference = next(iter(self._predictions.values()))
        if reference.currency != currency or reference.unit != unit:
            raise MixedUnitError(
                "calibrator already has predictions in "
                f"{reference.currency}/{reference.unit}; cannot add {currency}/{unit}"
            )

    def add_prediction(
        self,
        prediction: Prediction | Mapping[str, Any],
    ) -> bool:
        """Register a prediction.  Identical re-registration is a no-op."""
        if isinstance(prediction, Mapping):
            prediction = Prediction.from_mapping(prediction)
        if not isinstance(prediction, Prediction):
            raise TypeError("prediction must be a Prediction or mapping")
        self._assert_compatible(prediction.currency, prediction.unit)
        existing = self._predictions.get(prediction.prediction_id)
        if existing is not None:
            if existing == prediction:
                return False
            raise DuplicatePredictionError(
                f"prediction {prediction.prediction_id!r} already exists with different content"
            )
        self._predictions[prediction.prediction_id] = prediction
        self._rebuild()
        return True

    # -- final labels -------------------------------------------------

    def add_final_label(
        self,
        label: FinalLabel | Mapping[str, Any],
    ) -> LabelUpdate:
        """Add one observed final-label version and replay the state.

        A duplicate ``label_source_id`` with identical content is ignored.  A
        revision replaces the current score for its prediction; it does not
        create another job or score.
        """
        if isinstance(label, Mapping):
            label = FinalLabel.from_mapping(label)
        if not isinstance(label, FinalLabel):
            raise TypeError("label must be a FinalLabel or mapping")

        prediction = self._predictions.get(label.prediction_id)
        if prediction is None:
            raise UnknownPredictionError(
                f"label references unknown prediction {label.prediction_id!r}"
            )
        self._assert_compatible(label.currency, label.unit)
        if label.currency != prediction.currency or label.unit != prediction.unit:
            raise MixedUnitError(
                f"label unit {label.currency}/{label.unit} does not match prediction "
                f"{prediction.currency}/{prediction.unit}"
            )

        existing = self._labels.get(label.label_source_id)
        if existing is not None:
            if existing == label:
                return LabelUpdate(
                    applied=False,
                    reason="duplicate",
                    prediction_id=label.prediction_id,
                    label_source_id=label.label_source_id,
                )
            raise ConflictingLabelError(
                f"label source {label.label_source_id!r} already exists with different content"
            )

        current = self._current.get(label.prediction_id)
        replaced: FinalLabel | None = None
        reason = "new"

        if label.revision_of is not None:
            predecessor = self._labels.get(label.revision_of)
            if predecessor is None:
                raise RevisionLineageError(
                    f"revision_of {label.revision_of!r} is not a known label version"
                )
            if predecessor.prediction_id != label.prediction_id:
                raise RevisionLineageError(
                    "revision lineage cannot cross prediction/job identities"
                )
            if predecessor.label_source_id in self._superseded_by:
                raise RevisionLineageError(
                    f"label {predecessor.label_source_id!r} already has a successor; "
                    "refusing to create a fork"
                )
            if current is None or predecessor.label_source_id != current.label_source_id:
                raise RevisionLineageError(
                    "an explicit revision must supersede the current label version"
                )
            if label.available_at < predecessor.available_at:
                raise StaleLabelError(
                    "revision first_seen_at is earlier than the version it supersedes"
                )
            replaced = predecessor
            reason = "revision"
        elif current is not None:
            if label.available_at > current.available_at:
                replaced = current
                reason = "revision"
            elif label.available_at == current.available_at:
                new_order = _version_order(label)
                old_order = _version_order(current)
                if new_order is not None and old_order is not None and new_order > old_order:
                    replaced = current
                    reason = "revision"
                else:
                    raise AmbiguousRevisionError(
                        "two labels for the same prediction have the same first_seen_at "
                        "and no ordering evidence; supply revision_of or report_version_order"
                    )
            else:
                new_order = _version_order(label)
                old_order = _version_order(current)
                if new_order is not None and old_order is not None and new_order > old_order:
                    replaced = current
                    reason = "out_of_order_revision"
                else:
                    raise StaleLabelError(
                        "label is older than the current version and has no newer "
                        "revision lineage or report order"
                    )

        self._labels[label.label_source_id] = label
        self._parent[label.label_source_id] = None if replaced is None else replaced.label_source_id
        if replaced is not None:
            self._superseded_by[replaced.label_source_id] = label.label_source_id
        self._current[label.prediction_id] = label
        self._rebuild()
        return LabelUpdate(
            applied=True,
            reason=reason,
            prediction_id=label.prediction_id,
            label_source_id=label.label_source_id,
            replaced_label_source_id=None if replaced is None else replaced.label_source_id,
        )

    def revision_lineage(self, prediction_id: str) -> tuple[str, ...]:
        """Return label source IDs from first version to current, inclusive."""
        label = self._current.get(str(prediction_id))
        if label is None:
            return ()
        chain: list[str] = []
        source: str | None = label.label_source_id
        while source is not None:
            chain.append(source)
            source = self._parent.get(source)
        return tuple(reversed(chain))

    def label_history(self, prediction_id: str) -> tuple[FinalLabel, ...]:
        """Return label versions in lineage order."""
        return tuple(self._labels[source] for source in self.revision_lineage(prediction_id))

    # -- provisional telemetry ---------------------------------------

    def record_early_warning(
        self,
        warning: EarlyWarning | Mapping[str, Any],
    ) -> bool:
        """Record provisional telemetry without touching conformal state."""
        if isinstance(warning, Mapping):
            warning = EarlyWarning.from_mapping(warning)
        if not isinstance(warning, EarlyWarning):
            raise TypeError("warning must be an EarlyWarning or mapping")
        prediction = self._predictions.get(warning.prediction_id)
        if prediction is None:
            raise UnknownPredictionError(
                f"early warning references unknown prediction {warning.prediction_id!r}"
            )
        self._assert_compatible(warning.currency, warning.unit)
        if warning.currency != prediction.currency or warning.unit != prediction.unit:
            raise MixedUnitError(
                f"early-warning unit {warning.currency}/{warning.unit} does not match "
                f"prediction {prediction.currency}/{prediction.unit}"
            )
        existing = self._warnings.get(warning.event_id)
        if existing is not None:
            if existing == warning:
                return False
            raise ConflictingEarlyWarningError(
                f"early-warning event {warning.event_id!r} already exists with different content"
            )
        self._warnings[warning.event_id] = warning
        return True

    # -- replay and queries -------------------------------------------

    def _rebuild(self) -> None:
        ordered: list[tuple[datetime, datetime, str, FinalLabel, Prediction]] = []
        for prediction_id, label in self._current.items():
            prediction = self._predictions[prediction_id]
            if prediction.predicted_cost_micro is None:
                continue
            ordered.append(
                (
                    label.available_at,
                    prediction.prediction_timestamp,
                    prediction_id,
                    label,
                    prediction,
                )
            )
        ordered.sort(key=lambda item: item[:3])

        alpha = self.initial_alpha
        scores: list[int | Decimal] = []
        observations: list[ScoreObservation] = []
        miss_count = 0
        covered_count = 0
        for _, _, _, label, prediction in ordered:
            threshold = nearest_rank_quantile(scores, Decimal(1) - alpha) if scores else None
            score = residual(label.final_cost_micro, prediction.predicted_cost_micro)
            miss = threshold is not None and score > threshold
            before = alpha
            alpha = update_alpha(
                alpha,
                target_alpha=self.target_alpha,
                gamma=self.gamma,
                miss=1 if miss else 0,
                alpha_min=self.alpha_min,
                alpha_max=self.alpha_max,
            )
            observations.append(
                ScoreObservation(
                    prediction_id=label.prediction_id,
                    prediction_timestamp=prediction.prediction_timestamp,
                    label_source_id=label.label_source_id,
                    report_version_id=label.report_version_id,
                    observed_at=label.observed_at,
                    first_seen_at=label.available_at,
                    currency=label.currency,
                    unit=label.unit,
                    score_micro=score,
                    threshold_micro=threshold,
                    miss=miss,
                    alpha_before=before,
                    alpha_after=alpha,
                )
            )
            scores.append(score)
            if miss:
                miss_count += 1
            else:
                covered_count += 1

        self._alpha = alpha
        self._scores = tuple(scores)
        self._score_observations = tuple(observations)
        self._miss_count = miss_count
        self._covered_count = covered_count

    @property
    def state(self) -> ConformalState:
        threshold = (
            nearest_rank_quantile(self._scores, Decimal(1) - self._alpha) if self._scores else None
        )
        pending = sum(
            1 for prediction_id in self._predictions if prediction_id not in self._current
        )
        unscorable = sum(
            1
            for prediction_id in self._current
            if self._predictions[prediction_id].predicted_cost_micro is None
        )
        return ConformalState(
            alpha=self._alpha,
            target_alpha=self.target_alpha,
            gamma=self.gamma,
            quantile_level=Decimal(1) - self._alpha,
            threshold_micro=threshold,
            score_count=len(self._scores),
            miss_count=self._miss_count,
            covered_count=self._covered_count,
            score_observations=self._score_observations,
            current_label_count=len(self._current),
            label_version_count=len(self._labels),
            revision_count=len(self._superseded_by),
            pending_prediction_count=pending,
            unscorable_prediction_count=unscorable,
            early_warning_count=len(self._warnings),
            coverage_guarantee=None,
            limitations=CALIBRATION_LIMITATIONS,
        )

    def bound_for(self, prediction_id: str) -> ConformalBound:
        """Return the current upper bound for a registered prediction."""
        prediction = self._predictions.get(str(prediction_id))
        if prediction is None:
            raise UnknownPredictionError(f"unknown prediction {prediction_id!r}")
        return self._make_bound(
            prediction.prediction_id,
            prediction.predicted_cost_micro,
            prediction.currency,
            prediction.unit,
        )

    def bound_for_cost(
        self,
        predicted_cost_micro: Any,
        *,
        currency: Any = DEFAULT_CURRENCY,
        unit: Any = MICRO_USD,
        prediction_id: str = "",
    ) -> ConformalBound:
        """Return a bound for an unregistered modeled cost."""
        predicted = _amount(predicted_cost_micro, field="predicted_cost_micro")
        return self._make_bound(
            str(prediction_id),
            predicted,
            _currency(currency),
            _unit(unit),
        )

    def _make_bound(
        self,
        prediction_id: str,
        predicted: int | Decimal | None,
        currency: str,
        unit: str,
    ) -> ConformalBound:
        if self._predictions:
            reference = next(iter(self._predictions.values()))
            if reference.currency != currency or reference.unit != unit:
                raise MixedUnitError(
                    f"cannot bound {currency}/{unit} with calibration data in "
                    f"{reference.currency}/{reference.unit}"
                )
        base = {
            "prediction_id": prediction_id,
            "predicted_cost_micro": predicted,
            "alpha": self._alpha,
            "quantile_level": Decimal(1) - self._alpha,
            "score_count": len(self._scores),
            "currency": currency,
            "unit": unit,
            "coverage_guarantee": None,
        }
        if predicted is None:
            return ConformalBound(
                status=STATUS_INSUFFICIENT_DATA,
                upper_bound_micro=None,
                reason="missing_predicted_cost",
                notes=(
                    "A predicted cost is required to convert a residual quantile into a bound.",
                ),
                **base,
            )
        if not self._scores:
            return ConformalBound(
                status=STATUS_INSUFFICIENT_DATA,
                upper_bound_micro=None,
                reason="no_final_labels",
                notes=("No observed final-cost label has produced a residual score.",),
                **base,
            )
        if len(self._scores) < self.min_scores:
            return ConformalBound(
                status=STATUS_INSUFFICIENT_DATA,
                upper_bound_micro=None,
                reason="below_min_scores",
                notes=(f"{len(self._scores)} score(s) available; {self.min_scores} required.",),
                **base,
            )
        quantile = nearest_rank_quantile(self._scores, Decimal(1) - self._alpha)
        assert quantile is not None
        return ConformalBound(
            status=STATUS_OK,
            upper_bound_micro=_add(predicted, quantile),
            reason=None,
            notes=(
                COVERAGE_STATEMENT,
                "The bound is the modeled cost plus the current residual quantile.",
            ),
            **base,
        )


def calibrate(
    predictions: Iterable[Prediction | Mapping[str, Any]],
    labels: Iterable[FinalLabel | Mapping[str, Any]] = (),
    *,
    target_alpha: Any = DEFAULT_TARGET_ALPHA,
    gamma: Any = DEFAULT_GAMMA,
    alpha_min: Any = DEFAULT_ALPHA_MIN,
    alpha_max: Any = DEFAULT_ALPHA_MAX,
    initial_alpha: Any = None,
    min_scores: int = 1,
) -> ConformalCalibrator:
    """Convenience builder: register predictions, then add final labels."""
    calibrator = ConformalCalibrator(
        target_alpha=target_alpha,
        gamma=gamma,
        alpha_min=alpha_min,
        alpha_max=alpha_max,
        initial_alpha=initial_alpha,
        min_scores=min_scores,
    )
    for prediction in predictions:
        calibrator.add_prediction(prediction)
    for label in labels:
        calibrator.add_final_label(label)
    return calibrator
