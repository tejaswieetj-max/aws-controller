"""Decision-time spend estimation from approved, timestamped observations.

This module deliberately implements only transparent baselines:

* cumulative observed spend (the default), and
* an exponentially weighted moving average (EWMA) of observed spend.

Both operate on observations that the caller has already marked as approved.
Observations are ordered by their observation timestamp (not by billing-period
time), and every input timestamp is checked against the decision timestamp so
that an estimate cannot use future information.

Why there is no Kalman filter
-----------------------------
``research/protocol.md`` gives a generic state-space form
(``x_t = A x_{t-1} + w_t`` and ``z_t = C x_t + v_t``) but does not map those
symbols to actual repository fields, define a defensible observation matrix,
or supply an empirically grounded process/observation covariance.  The same
protocol's limitations also note that observation-noise covariance for
provisional APIs is not established.  Implementing a filter here would require
inventing those values, so this module returns an explicit unsupported-method
error instead.  The baselines are descriptive; they do not claim unbiasedness,
calibration, or coverage.

Units and missing data
----------------------
Amounts are not converted.  Spendctl callers normally provide integer
micro-USD, which is the default unit (``"micro_usd"``), but the unit and
currency are carried through to the result.  An observation with no reported
amount is kept as ``None``.  Missing amounts are never replaced with zero or an
inferred price.  A cumulative result computed while observations are missing is
reported as an observed lower bound rather than as total billed spend.

The module contains no AWS calls, network access, or infrastructure code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
from math import isfinite
from typing import Any

__all__ = [
    "KALMAN_UNAVAILABLE_REASON",
    "BaselineEstimator",
    "CumulativeEstimator",
    "EWMAEstimator",
    "EstimateResult",
    "EstimateStatus",
    "Estimator",
    "EstimatorError",
    "FutureObservationError",
    "InvalidObservationError",
    "MixedUnitError",
    "Observation",
    "SpendEstimator",
    "SpendObservation",
    "UnsupportedEstimatorError",
    "cumulative_observed_spend",
    "estimate",
    "estimate_at",
    "estimate_cumulative",
    "estimate_ewma",
    "estimate_spend",
    "estimate_spend_at",
    "ewma",
    "sum_observed",
]

MICRO_USD = "micro_usd"
METHOD_CUMULATIVE = "cumulative_observed_spend"
METHOD_EWMA = "ewma"
SUPPORTED_METHODS = (METHOD_CUMULATIVE, METHOD_EWMA)

_METHOD_ALIASES = {
    "cumulative": METHOD_CUMULATIVE,
    "cumulative_spend": METHOD_CUMULATIVE,
    "cumulative_observed": METHOD_CUMULATIVE,
    "observed_spend": METHOD_CUMULATIVE,
    "ewma": METHOD_EWMA,
    "exponential": METHOD_EWMA,
    "exponential_weighted": METHOD_EWMA,
    "exponential_weighted_moving_average": METHOD_EWMA,
}

STATUS_OK = "OK"
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

KALMAN_UNAVAILABLE_REASON = (
    "research/protocol.md does not map the generic state-space equations to "
    "actual observation fields or provide defensible process/observation "
    "covariance values; a Kalman estimator would require invented inputs."
)


class EstimateStatus(str, Enum):
    """Status values for an estimate.

    This is a ``str`` enum so comparisons with the plain strings ``"OK"`` and
    ``"INSUFFICIENT_DATA"`` also work.
    """

    OK = STATUS_OK
    INSUFFICIENT_DATA = STATUS_INSUFFICIENT_DATA


class EstimatorError(ValueError):
    """Base class for estimator input/validation errors."""


class InvalidObservationError(EstimatorError):
    """Raised when an observation has an invalid timestamp, amount, or unit."""


class FutureObservationError(EstimatorError):
    """Raised when an observation timestamp is after the decision timestamp."""


class MixedUnitError(EstimatorError):
    """Raised when observations that would be combined use different units."""


class UnsupportedEstimatorError(NotImplementedError, EstimatorError):
    """Raised for estimator methods that lack a defensible model."""


# ---------------------------------------------------------------------------
# Value and timestamp coercion
# ---------------------------------------------------------------------------


def _normalise_number(value: Decimal) -> int | Decimal:
    """Return an int for integral Decimals, otherwise the exact Decimal."""
    if value == value.to_integral_value():
        return int(value)
    return value


def _coerce_amount(value: Any, *, field: str) -> int | Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidObservationError(f"{field} must be numeric or None, not bool")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        if not isfinite(value):
            raise InvalidObservationError(f"{field} must be finite")
        # str() preserves the decimal literal supplied by the caller rather
        # than exposing binary floating-point expansion.
        amount = Decimal(str(value))
    elif isinstance(value, str):
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise InvalidObservationError(f"{field} is not a finite number") from exc
    else:
        raise InvalidObservationError(f"{field} must be numeric or None")
    if not amount.is_finite():
        raise InvalidObservationError(f"{field} must be finite")
    return _normalise_number(amount)


def _coerce_timestamp(value: Any, *, field: str = "timestamp") -> datetime:
    """Return a timezone-aware UTC datetime.

    Accepted forms are ``datetime`` objects, ISO-8601 strings, and numeric
    Unix epoch seconds.  A naive datetime is treated as UTC.  No unit
    conversion (for example milliseconds to seconds) is inferred.
    """
    if isinstance(value, bool):
        raise InvalidObservationError(f"{field} must be a timestamp, not bool")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise InvalidObservationError(f"{field} must not be empty")
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise InvalidObservationError(f"{field} is not a valid ISO-8601 timestamp") from exc
    elif isinstance(value, (int, float, Decimal)):
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise InvalidObservationError(f"{field} must be finite")
            seconds = float(value)
        else:
            seconds = float(value)
        if not isfinite(seconds):
            raise InvalidObservationError(f"{field} must be finite")
        try:
            parsed = datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise InvalidObservationError(f"{field} is outside the supported range") from exc
    else:
        raise InvalidObservationError(f"{field} must be a datetime, ISO string, or epoch seconds")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _looks_like_timestamp(value: Any) -> bool:
    try:
        _coerce_timestamp(value)
    except (EstimatorError, TypeError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


_TIMESTAMP_ALIASES = ("observed_at", "observation_timestamp", "ts", "time")
_AMOUNT_ALIASES = ("cost_micro", "value_micro", "observed_micro", "amount", "cost")
_ID_ALIASES = ("id", "record_id", "event_id")
_SOURCE_ALIASES = ("provenance",)
_CURRENCY_ALIASES = ("currency_code",)
_UNIT_ALIASES = ("units",)
_APPROVED_ALIASES = ("is_approved",)


def _take_alias(
    values: dict[str, Any],
    canonical: str,
    aliases: Sequence[str],
    *,
    default: Any,
) -> Any:
    present = [key for key in (canonical, *aliases) if key in values]
    if len(present) > 1:
        raise TypeError(f"observation received multiple values for {canonical!r}: {present}")
    if not present:
        return default
    return values[present[0]]


@dataclass(frozen=True, init=False)
class Observation:
    """One approved-or-not, timestamped spend observation.

    Parameters
    ----------
    timestamp:
        Time the observation became available (observation time), not the
        billing-period date.  Normalised to timezone-aware UTC.
    amount_micro:
        Reported amount, or ``None`` when the amount is missing.  The amount is
        not converted; ``unit`` describes it and defaults to integer micro-USD.
    approved:
        Whether the observation is approved for estimation.  The default is
        ``True`` because callers pass their approved input set; set it to
        ``False`` to make the estimator explicitly exclude a row.
    observation_id:
        Stable identifier used only as a deterministic tie-breaker.
    source:
        Free-form provenance label carried through for transparency.
    currency / unit:
        Unit metadata.  Arithmetic refuses to mix units or currencies.

    The constructor also accepts common keyword aliases (``observed_at``,
    ``cost_micro``, ``id``, ``provenance``, ``is_approved``, etc.).  Two
    positional forms are supported: timestamp-first and observation-id-first.
    """

    timestamp: datetime
    amount_micro: int | Decimal | None = None
    approved: bool = True
    observation_id: str = ""
    source: str = "unknown"
    currency: str = "USD"
    unit: str = MICRO_USD

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        values = dict(kwargs)

        if args:
            first = args[0]
            if _looks_like_timestamp(first):
                names = (
                    "timestamp",
                    "amount_micro",
                    "approved",
                    "observation_id",
                    "source",
                    "currency",
                    "unit",
                )
            elif isinstance(first, str) and len(args) >= 2 and _looks_like_timestamp(args[1]):
                names = (
                    "observation_id",
                    "timestamp",
                    "amount_micro",
                    "approved",
                    "source",
                    "currency",
                    "unit",
                )
            else:
                names = (
                    "timestamp",
                    "amount_micro",
                    "approved",
                    "observation_id",
                    "source",
                    "currency",
                    "unit",
                )
            if len(args) > len(names):
                raise TypeError(f"Observation accepts at most {len(names)} positional arguments")
            for name, value in zip(names, args):
                if name in values:
                    raise TypeError(f"Observation received multiple values for {name!r}")
                values[name] = value

        timestamp = _take_alias(values, "timestamp", _TIMESTAMP_ALIASES, default=_MISSING)
        amount = _take_alias(values, "amount_micro", _AMOUNT_ALIASES, default=None)
        approved = _take_alias(values, "approved", _APPROVED_ALIASES, default=True)
        observation_id = _take_alias(values, "observation_id", _ID_ALIASES, default="")
        source = _take_alias(values, "source", _SOURCE_ALIASES, default="unknown")
        currency = _take_alias(values, "currency", _CURRENCY_ALIASES, default="USD")
        unit = _take_alias(values, "unit", _UNIT_ALIASES, default=MICRO_USD)

        if timestamp is _MISSING:
            raise TypeError("Observation requires a timestamp")
        if not isinstance(approved, bool):
            raise TypeError("approved must be a bool")

        object.__setattr__(self, "timestamp", _coerce_timestamp(timestamp))
        object.__setattr__(self, "amount_micro", _coerce_amount(amount, field="amount_micro"))
        object.__setattr__(self, "approved", approved)
        object.__setattr__(
            self, "observation_id", "" if observation_id is None else str(observation_id)
        )
        object.__setattr__(self, "source", "unknown" if source is None else str(source))
        object.__setattr__(self, "currency", str(currency or "").strip().upper())
        object.__setattr__(self, "unit", str(unit or "").strip())
        if not self.currency:
            raise InvalidObservationError("currency must not be empty")
        if not self.unit:
            raise InvalidObservationError("unit must not be empty")

    @property
    def amount(self) -> int | Decimal | None:
        """Alias for :attr:`amount_micro`."""
        return self.amount_micro

    @property
    def cost_micro(self) -> int | Decimal | None:
        """Alias for :attr:`amount_micro`."""
        return self.amount_micro

    @property
    def value_micro(self) -> int | Decimal | None:
        """Alias for :attr:`amount_micro`."""
        return self.amount_micro

    @property
    def observed_at(self) -> datetime:
        """Alias for :attr:`timestamp`."""
        return self.timestamp


class _Missing:
    pass


_MISSING = _Missing()


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimateResult:
    """Explicit result of a decision-time estimate.

    ``value_micro`` is ``None`` exactly when ``status`` is
    ``"INSUFFICIENT_DATA"``; callers must not substitute zero.
    """

    status: str
    method: str
    decision_timestamp: datetime
    value_micro: int | Decimal | None
    unit: str | None
    currency: str | None
    observations: tuple[Observation, ...] = ()
    input_count: int = 0
    eligible_count: int = 0
    used_count: int = 0
    missing_cost_count: int = 0
    unapproved_count: int = 0
    reason: str | None = None
    complete: bool = False
    lower_bound: bool = False
    notes: tuple[str, ...] = ()

    @property
    def value(self) -> int | Decimal | None:
        """Alias for :attr:`value_micro`."""
        return self.value_micro

    @property
    def estimate_micro(self) -> int | Decimal | None:
        """Alias for :attr:`value_micro`."""
        return self.value_micro

    @property
    def is_sufficient(self) -> bool:
        return self.status == STATUS_OK

    @property
    def sufficient(self) -> bool:
        return self.is_sufficient

    @property
    def insufficient(self) -> bool:
        return not self.is_sufficient

    @property
    def insufficient_data(self) -> bool:
        return self.status == STATUS_INSUFFICIENT_DATA

    @property
    def used_observation_count(self) -> int:
        return self.used_count

    def require_value(self) -> int | Decimal:
        """Return the value or raise an explicit insufficient-data error."""
        if self.value_micro is None:
            raise EstimatorError(f"insufficient data: {self.reason or 'no estimate available'}")
        return self.value_micro

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view; timestamps are ISO-8601 strings."""
        value = self.value_micro
        serialised_value: int | str | None
        if value is None or isinstance(value, int):
            serialised_value = value
        else:
            serialised_value = str(value)
        return {
            "status": self.status,
            "method": self.method,
            "decision_timestamp": self.decision_timestamp.isoformat().replace("+00:00", "Z"),
            "value": serialised_value,
            "value_micro": serialised_value,
            "unit": self.unit,
            "currency": self.currency,
            "input_count": self.input_count,
            "eligible_count": self.eligible_count,
            "used_count": self.used_count,
            "missing_cost_count": self.missing_cost_count,
            "unapproved_count": self.unapproved_count,
            "reason": self.reason,
            "complete": self.complete,
            "lower_bound": self.lower_bound,
            "notes": list(self.notes),
        }

    as_dict = to_dict

    def __getitem__(self, key: str) -> Any:
        """Allow read-only dict-style access for report builders."""
        try:
            return self.to_dict()[key]
        except KeyError as exc:
            raise KeyError(key) from exc


# ---------------------------------------------------------------------------
# Pure baseline arithmetic
# ---------------------------------------------------------------------------


def _decimal_alpha(alpha: Any) -> Decimal:
    if isinstance(alpha, bool):
        raise EstimatorError("alpha must be a number in (0, 1]")
    try:
        value = Decimal(str(alpha))
    except (InvalidOperation, ValueError) as exc:
        raise EstimatorError("alpha must be a number in (0, 1]") from exc
    if not value.is_finite() or not (Decimal(0) < value <= Decimal(1)):
        raise EstimatorError("alpha must be a number in (0, 1]")
    return value


def _ewma_decimal(values: Iterable[Any], alpha: Decimal) -> Decimal | None:
    clean: list[Decimal] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            raise EstimatorError("EWMA values must be numeric or None")
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if not isfinite(value):
                raise EstimatorError("EWMA values must be finite")
            number = Decimal(str(value))
        else:
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise EstimatorError("EWMA values must be numeric or None") from exc
        if not number.is_finite():
            raise EstimatorError("EWMA values must be finite")
        clean.append(number)

    if not clean:
        return None

    largest_digits = max(len(str(abs(number))) for number in clean)
    with localcontext() as context:
        context.prec = max(50, largest_digits + 20)
        result = clean[0]
        one_minus_alpha = Decimal(1) - alpha
        for number in clean[1:]:
            result = alpha * number + one_minus_alpha * result
    return result


def ewma(values: Iterable[Any], alpha: Any = 0.2) -> int | Decimal | None:
    """Return a seeded EWMA over numeric values, skipping ``None``.

    ``None`` values are skipped without being replaced by zero.  The first
    numeric value seeds the recursion; subsequent values use
    ``alpha * value + (1 - alpha) * previous``.  Returns ``None`` if no numeric
    values are present.
    """
    result = _ewma_decimal(values, _decimal_alpha(alpha))
    return None if result is None else _normalise_number(result)


def _scaled_integer(number: Decimal, exponent: int) -> int:
    """Return ``number * 10**-exponent`` as an exact integer.

    ``exponent`` must be the smallest exponent across the values being summed,
    so that the shift is never negative.
    """
    sign, digits, number_exponent = number.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    coefficient *= 10 ** (number_exponent - exponent)
    return -coefficient if sign else coefficient


def _from_scaled_integer(coefficient: int, exponent: int) -> Decimal:
    """Build a Decimal exactly, without using the ambient context precision."""
    if coefficient == 0:
        return Decimal(0)
    sign = 1 if coefficient < 0 else 0
    digits = tuple(int(character) for character in str(abs(coefficient)))
    return Decimal((sign, digits, exponent))


def sum_observed(values: Iterable[Any]) -> int | Decimal | None:
    """Return the exact sum of numeric values, skipping ``None``.

    Returns ``None`` when no numeric value exists.  Missing values are never
    coerced to zero.  Addition is performed on scaled integers so that the
    result does not depend on the ambient :mod:`decimal` precision.
    """
    numbers: list[Decimal] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            raise EstimatorError("observed values must be numeric or None")
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if not isfinite(value):
                raise EstimatorError("observed values must be finite")
            number = Decimal(str(value))
        else:
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise EstimatorError("observed values must be numeric or None") from exc
        if not number.is_finite():
            raise EstimatorError("observed values must be finite")
        numbers.append(number)

    if not numbers:
        return None

    # Zeros add nothing, and including a contrived zero with a very small
    # exponent would force needless scaling of every other value.
    nonzero = [number for number in numbers if number]
    if not nonzero:
        return 0

    smallest_exponent = min(number.as_tuple().exponent for number in nonzero)
    total = sum(_scaled_integer(number, smallest_exponent) for number in nonzero)
    return _normalise_number(_from_scaled_integer(total, smallest_exponent))


# ---------------------------------------------------------------------------
# Estimator
# ---------------------------------------------------------------------------


def _as_observation(value: Any) -> Observation:
    if isinstance(value, Observation):
        return value
    if isinstance(value, Mapping):
        return Observation(**value)
    raise TypeError("observations must be Observation objects or mappings")


def _prepare(
    observations: Iterable[Observation | Mapping[str, Any]] | Observation,
    decision_timestamp: Any,
) -> tuple[
    datetime,
    tuple[Observation, ...],
    tuple[Observation, ...],
    tuple[Observation, ...],
    int,
    int,
]:
    decision = _coerce_timestamp(decision_timestamp, field="decision_timestamp")

    if isinstance(observations, Observation):
        raw: list[Any] = [observations]
    elif observations is None:
        raw = []
    else:
        raw = list(observations)
    parsed = tuple(_as_observation(item) for item in raw)

    # Validate every input, including ones that will later be excluded for
    # approval or missingness.  This prevents accidental look-ahead input.
    for index, observation in enumerate(parsed):
        if observation.timestamp > decision:
            raise FutureObservationError(
                "observation timestamp "
                f"{observation.timestamp.isoformat()} at input index {index}"
                f" (id={observation.observation_id!r}) is after decision timestamp "
                f"{decision.isoformat()}"
            )

    eligible = tuple(observation for observation in parsed if observation.approved)
    unapproved_count = len(parsed) - len(eligible)
    missing_cost_count = sum(observation.amount_micro is None for observation in eligible)
    valid = tuple(
        sorted(
            (observation for observation in eligible if observation.amount_micro is not None),
            key=lambda observation: (observation.timestamp, observation.observation_id),
        )
    )

    units = {observation.unit for observation in valid}
    currencies = {observation.currency for observation in valid}
    if len(units) > 1:
        raise MixedUnitError(f"cannot combine observations with units {sorted(units)}")
    if len(currencies) > 1:
        raise MixedUnitError(f"cannot combine observations with currencies {sorted(currencies)}")

    return decision, parsed, eligible, valid, missing_cost_count, unapproved_count


class BaselineEstimator:
    """Transparent cumulative or EWMA spend estimator.

    Parameters
    ----------
    method:
        ``"cumulative_observed_spend"`` (default) or ``"ewma"``.
    alpha:
        EWMA smoothing factor in ``(0, 1]``.  Unused by the cumulative method.
    min_observations:
        Minimum number of approved observations with a reported amount required
        before a value is returned.  The default is one; zero would turn the
        absence of data into a fabricated estimate, so it is rejected.
    """

    def __init__(
        self,
        method: str = METHOD_CUMULATIVE,
        *,
        alpha: Any = 0.2,
        min_observations: int = 1,
    ) -> None:
        normalised_method = str(method).strip().lower()
        normalised_method = _METHOD_ALIASES.get(normalised_method, normalised_method)
        if normalised_method in {"kalman", "kalman_filter", "state_space"}:
            raise UnsupportedEstimatorError(KALMAN_UNAVAILABLE_REASON)
        if normalised_method not in SUPPORTED_METHODS:
            raise ValueError(
                f"unknown estimator method {method!r}; supported: {', '.join(SUPPORTED_METHODS)}"
            )
        if isinstance(min_observations, bool) or not isinstance(min_observations, int):
            raise TypeError("min_observations must be an integer")
        if min_observations < 1:
            raise ValueError("min_observations must be at least 1")
        self.method = normalised_method
        self.alpha = _decimal_alpha(alpha) if normalised_method == METHOD_EWMA else str(alpha)
        self.min_observations = min_observations

    def estimate(
        self,
        observations: Iterable[Observation | Mapping[str, Any]] | Observation,
        decision_timestamp: Any,
    ) -> EstimateResult:
        """Estimate spend using only observations available at the decision."""
        decision, parsed, eligible, valid, missing, unapproved = _prepare(
            observations, decision_timestamp
        )

        unit = valid[0].unit if valid else (eligible[0].unit if eligible else None)
        currency = valid[0].currency if valid else (eligible[0].currency if eligible else None)

        if not parsed:
            return EstimateResult(
                status=STATUS_INSUFFICIENT_DATA,
                method=self.method,
                decision_timestamp=decision,
                value_micro=None,
                unit=None,
                currency=None,
                reason="no_observations",
                notes=("No observations were supplied.",),
            )
        if not eligible:
            return EstimateResult(
                status=STATUS_INSUFFICIENT_DATA,
                method=self.method,
                decision_timestamp=decision,
                value_micro=None,
                unit=unit,
                currency=currency,
                observations=(),
                input_count=len(parsed),
                eligible_count=0,
                used_count=0,
                missing_cost_count=0,
                unapproved_count=unapproved,
                reason="no_approved_observations",
                notes=("All supplied observations were marked unapproved.",),
            )
        if not valid:
            return EstimateResult(
                status=STATUS_INSUFFICIENT_DATA,
                method=self.method,
                decision_timestamp=decision,
                value_micro=None,
                unit=unit,
                currency=currency,
                observations=(),
                input_count=len(parsed),
                eligible_count=len(eligible),
                used_count=0,
                missing_cost_count=missing,
                unapproved_count=unapproved,
                reason="no_cost_observations",
                notes=(
                    "No approved observation reported an amount; missing values were not filled.",
                ),
            )
        if len(valid) < self.min_observations:
            return EstimateResult(
                status=STATUS_INSUFFICIENT_DATA,
                method=self.method,
                decision_timestamp=decision,
                value_micro=None,
                unit=unit,
                currency=currency,
                observations=(),
                input_count=len(parsed),
                eligible_count=len(eligible),
                used_count=len(valid),
                missing_cost_count=missing,
                unapproved_count=unapproved,
                reason="below_min_observations",
                notes=(
                    (
                        f"{len(valid)} observation(s) with an amount are available; "
                        f"{self.min_observations} required."
                    ),
                ),
            )

        amounts = [observation.amount_micro for observation in valid]
        if self.method == METHOD_CUMULATIVE:
            value = sum_observed(amounts)
            notes = [
                "Cumulative observed spend over approved observations known at the decision time.",
                "This is a descriptive baseline, not total billed spend and not a calibrated forecast.",
            ]
            lower_bound = missing > 0
            if lower_bound:
                notes.append(
                    f"{missing} approved observation(s) have no reported amount; "
                    "the result is an observed lower bound."
                )
            method = METHOD_CUMULATIVE
        else:
            value = ewma(amounts, self.alpha)
            notes = [
                "Seeded EWMA of approved observations known at the decision time.",
                "This is a descriptive baseline, not a calibrated forecast.",
            ]
            if missing:
                notes.append(
                    f"{missing} approved observation(s) have no reported amount and were skipped."
                )
            lower_bound = False
            method = METHOD_EWMA

        assert value is not None  # valid is non-empty
        return EstimateResult(
            status=STATUS_OK,
            method=method,
            decision_timestamp=decision,
            value_micro=value,
            unit=unit,
            currency=currency,
            observations=valid,
            input_count=len(parsed),
            eligible_count=len(eligible),
            used_count=len(valid),
            missing_cost_count=missing,
            unapproved_count=unapproved,
            reason=None,
            complete=missing == 0,
            lower_bound=lower_bound,
            notes=tuple(notes),
        )


class CumulativeEstimator(BaselineEstimator):
    """Cumulative observed-spend baseline."""

    def __init__(self, *, min_observations: int = 1) -> None:
        super().__init__(METHOD_CUMULATIVE, min_observations=min_observations)


class EWMAEstimator(BaselineEstimator):
    """Exponentially weighted moving-average baseline."""

    def __init__(self, alpha: Any = 0.2, *, min_observations: int = 1) -> None:
        super().__init__(METHOD_EWMA, alpha=alpha, min_observations=min_observations)


# Friendly aliases for callers that used other natural names.
Estimator = BaselineEstimator
SpendEstimator = BaselineEstimator
SpendObservation = Observation


# ---------------------------------------------------------------------------
# Functional entry points
# ---------------------------------------------------------------------------


def estimate(
    observations: Iterable[Observation | Mapping[str, Any]] | Observation,
    decision_timestamp: Any,
    *,
    method: str = METHOD_CUMULATIVE,
    alpha: Any = 0.2,
    min_observations: int = 1,
) -> EstimateResult:
    """Estimate spend at *decision_timestamp* using the selected baseline."""
    return BaselineEstimator(
        method=method,
        alpha=alpha,
        min_observations=min_observations,
    ).estimate(observations, decision_timestamp)


def estimate_cumulative(
    observations: Iterable[Observation | Mapping[str, Any]] | Observation,
    decision_timestamp: Any,
    *,
    min_observations: int = 1,
) -> EstimateResult:
    """Cumulative observed-spend baseline."""
    return CumulativeEstimator(min_observations=min_observations).estimate(
        observations, decision_timestamp
    )


def estimate_ewma(
    observations: Iterable[Observation | Mapping[str, Any]] | Observation,
    decision_timestamp: Any,
    *,
    alpha: Any = 0.2,
    min_observations: int = 1,
) -> EstimateResult:
    """EWMA baseline."""
    return EWMAEstimator(alpha=alpha, min_observations=min_observations).estimate(
        observations, decision_timestamp
    )


# Common functional aliases.
estimate_spend = estimate
estimate_at = estimate
estimate_spend_at = estimate
cumulative_observed_spend = estimate_cumulative
