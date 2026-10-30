"""Metric and baseline definitions for the strict real-data replay.

Every function here is a pure calculation over already-validated records.  The
module does not read files, invent observations, fill missing values, or write
results.  A metric whose denominator is zero is returned with status
``NOT_COMPUTED`` and ``value=None``; it is never reported as zero.

Definitions follow ``research/protocol.md`` sections 6 and 7.  The runner passes
actual records only; nothing in this module decides whether an empirical run is
justified.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from itertools import pairwise
from typing import Any

__all__ = [
    "BASELINE_POLICIES",
    "CURRENCY_USD",
    "POLICY_CALIBUR",
    "POLICY_DELAYED_ACI",
    "POLICY_ORACLE",
    "POLICY_QUANTILE_REGRESSION",
    "POLICY_RAW_POINT",
    "POLICY_STATIC_MARGIN",
    "STATUS_AVAILABLE",
    "STATUS_COMPUTED",
    "STATUS_NOT_COMPUTED",
    "STATUS_REFERENCE_ONLY",
    "STATUS_UNAVAILABLE",
    "BaselineAvailability",
    "BudgetWindow",
    "FeedbackDelay",
    "JobOutcome",
    "MetricResult",
    "RequestDecision",
    "ReservationInterval",
    "aggregate_budget_breach_rate",
    "baseline_availability",
    "coverage",
    "describe_feedback_delay",
    "locked_capital_integral",
    "matching_rate",
    "request_acceptance_rate",
    "reservation_overrun_frequency",
    "reserve_raw_point",
    "reserve_static_margin",
]

CURRENCY_USD = "USD"

STATUS_COMPUTED = "COMPUTED"
STATUS_NOT_COMPUTED = "NOT_COMPUTED"
STATUS_AVAILABLE = "AVAILABLE"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_REFERENCE_ONLY = "REFERENCE_ONLY"

POLICY_RAW_POINT = "raw_point_estimate"
POLICY_STATIC_MARGIN = "static_margin"
POLICY_DELAYED_ACI = "delayed_label_aci"
POLICY_QUANTILE_REGRESSION = "quantile_regression"
POLICY_DRIFT = "drift_recalibration"
POLICY_CALIBUR = "calibur"
POLICY_CALIBUR_NO_EARLY_WARNING = "calibur_without_early_warning"
POLICY_CALIBUR_SIMPLE_ESTIMATOR = "calibur_without_state_estimator"
POLICY_CALIBUR_FIXED_RELEASE = "calibur_with_fixed_release"
POLICY_ORACLE = "oracle_upper_bound"

BASELINE_POLICIES = (
    POLICY_RAW_POINT,
    POLICY_STATIC_MARGIN,
    POLICY_DELAYED_ACI,
    POLICY_QUANTILE_REGRESSION,
    POLICY_DRIFT,
    POLICY_CALIBUR,
    POLICY_CALIBUR_NO_EARLY_WARNING,
    POLICY_CALIBUR_SIMPLE_ESTIMATOR,
    POLICY_CALIBUR_FIXED_RELEASE,
    POLICY_ORACLE,
)


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must not be negative")
    return value


def _aware(value: Any, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


@dataclass(frozen=True)
class MetricResult:
    """One metric with an explicit denominator and missing/excluded counts."""

    name: str
    status: str
    numerator: int | None
    denominator: int | None
    value: Decimal | None
    unit: str
    currency: str | None = CURRENCY_USD
    missing: int = 0
    excluded: int = 0
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {STATUS_COMPUTED, STATUS_NOT_COMPUTED}:
            raise ValueError(f"invalid metric status {self.status!r}")
        if self.numerator is not None:
            _nonnegative_int(self.numerator, field="numerator")
        if self.denominator is not None:
            _nonnegative_int(self.denominator, field="denominator")
        _nonnegative_int(self.missing, field="missing")
        _nonnegative_int(self.excluded, field="excluded")
        if self.status == STATUS_COMPUTED:
            if self.value is None or not self.denominator:
                raise ValueError("a computed metric needs a value and a positive denominator")
        elif self.value is not None:
            raise ValueError("a not-computed metric must not carry a value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": None if self.value is None else str(self.value),
            "unit": self.unit,
            "currency": self.currency,
            "missing": self.missing,
            "excluded": self.excluded,
            "notes": list(self.notes),
        }


def _ratio(
    name: str,
    numerator: int,
    denominator: int,
    *,
    unit: str = "ratio",
    missing: int = 0,
    excluded: int = 0,
    notes: tuple[str, ...] = (),
) -> MetricResult:
    if denominator == 0:
        return MetricResult(
            name=name,
            status=STATUS_NOT_COMPUTED,
            numerator=0,
            denominator=0,
            value=None,
            unit=unit,
            missing=missing,
            excluded=excluded,
            notes=(*notes, "zero denominator; metric is not computed, not zero"),
        )
    with localcontext() as context:
        context.prec = 28
        value = Decimal(numerator) / Decimal(denominator)
    return MetricResult(
        name=name,
        status=STATUS_COMPUTED,
        numerator=numerator,
        denominator=denominator,
        value=value,
        unit=unit,
        missing=missing,
        excluded=excluded,
        notes=notes,
    )


@dataclass(frozen=True)
class JobOutcome:
    """A job's committed reservation and final billed label, if either exists.

    ``actual_billed_micro`` and ``final_label_observed_at`` must be supplied
    together.  A missing label stays missing.
    """

    job_id: str
    committed_reservation_micro: int | None
    actual_billed_micro: int | None
    final_label_observed_at: datetime | None = None
    decision_at: datetime | None = None
    upper_bound_micro: int | None = None
    bound_frozen_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        for name in (
            "committed_reservation_micro",
            "actual_billed_micro",
            "upper_bound_micro",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_int(value, field=name)
        if (self.actual_billed_micro is None) != (self.final_label_observed_at is None):
            raise ValueError(
                "actual_billed_micro and final_label_observed_at must be supplied together"
            )
        if self.final_label_observed_at is not None:
            _aware(self.final_label_observed_at, field="final_label_observed_at")
        for name in ("decision_at", "bound_frozen_at"):
            value = getattr(self, name)
            if value is not None:
                _aware(value, field=name)
        if (
            self.bound_frozen_at is not None
            and self.decision_at is not None
            and self.bound_frozen_at > self.decision_at
        ):
            raise ValueError("bound_frozen_at must not be later than decision_at")
        if (
            self.decision_at is not None
            and self.final_label_observed_at is not None
            and self.final_label_observed_at < self.decision_at
        ):
            raise ValueError("final_label_observed_at must not precede decision_at")

    @property
    def has_final_label(self) -> bool:
        return self.actual_billed_micro is not None


def reservation_overrun_frequency(outcomes: Iterable[JobOutcome]) -> MetricResult:
    """BOF: accepted jobs whose actual billed cost exceeds the reservation."""
    items = tuple(outcomes)
    evaluable = [
        item
        for item in items
        if item.has_final_label and item.committed_reservation_micro is not None
    ]
    overruns = sum(
        1
        for item in evaluable
        if item.actual_billed_micro > item.committed_reservation_micro
    )
    missing_labels = sum(1 for item in items if not item.has_final_label)
    missing_reservations = sum(
        1 for item in items if item.has_final_label and item.committed_reservation_micro is None
    )
    return _ratio(
        "BOF",
        overruns,
        len(evaluable),
        unit="ratio",
        missing=missing_labels,
        excluded=missing_reservations,
        notes=(
            "Marginal job-level reservation-overrun frequency; not a budget-safety guarantee.",
            "Jobs without a final label or committed reservation are excluded and counted.",
        ),
    )


@dataclass(frozen=True)
class BudgetWindow:
    """One budget period.  Only fully observed windows are evaluable for ABR."""

    window_id: str
    team: str
    period: str
    cap_micro: int
    actual_cumulative_micro: int | None
    enforceable_obligations_micro: int | None
    fully_observed: bool

    def __post_init__(self) -> None:
        for name in ("window_id", "team", "period"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        _nonnegative_int(self.cap_micro, field="cap_micro")
        for name in ("actual_cumulative_micro", "enforceable_obligations_micro"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative_int(value, field=name)
        if not isinstance(self.fully_observed, bool):
            raise TypeError("fully_observed must be a bool")


def aggregate_budget_breach_rate(windows: Iterable[BudgetWindow]) -> MetricResult:
    """ABR over fully observed windows with complete spend and obligation data."""
    items = tuple(windows)
    evaluable = [
        window
        for window in items
        if window.fully_observed
        and window.actual_cumulative_micro is not None
        and window.enforceable_obligations_micro is not None
    ]
    breaches = sum(
        1
        for window in evaluable
        if window.actual_cumulative_micro + window.enforceable_obligations_micro
        > window.cap_micro
    )
    excluded = len(items) - len(evaluable)
    return _ratio(
        "ABR",
        breaches,
        len(evaluable),
        unit="ratio",
        excluded=excluded,
        notes=(
            "Aggregate budget breach rate over fully observed windows only.",
            "Incomplete or reconstructed ledger windows are excluded, never filled.",
        ),
    )


@dataclass(frozen=True)
class ReservationInterval:
    """One observed, non-overlapping interval of reserved capital and active spend."""

    start: datetime
    end: datetime
    reserved_micro: int
    active_spend_micro: int
    source_id: str

    def __post_init__(self) -> None:
        start = _aware(self.start, field="start")
        end = _aware(self.end, field="end")
        if end < start:
            raise ValueError("interval end must not precede start")
        _nonnegative_int(self.reserved_micro, field="reserved_micro")
        _nonnegative_int(self.active_spend_micro, field="active_spend_micro")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string")

    @property
    def seconds(self) -> int:
        return int((self.end - self.start).total_seconds())


def _validate_non_overlapping(intervals: tuple[ReservationInterval, ...]) -> None:
    ordered = sorted(intervals, key=lambda item: (item.start, item.end))
    for earlier, later in pairwise(ordered):
        if later.start < earlier.end:
            raise ValueError(
                f"observed intervals overlap: {earlier.source_id!r} and {later.source_id!r}"
            )


def locked_capital_integral(
    intervals: Iterable[ReservationInterval],
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> MetricResult:
    """LCI in micro-USD-hours over actual observed intervals only.

    Overlapping intervals and negative excess are rejected or clipped
    respectively; gaps are not interpolated and are reported when a window is
    supplied.
    """
    items = tuple(intervals)
    _validate_non_overlapping(items)
    if (window_start is None) != (window_end is None):
        raise ValueError("window_start and window_end must be supplied together")
    observed_seconds = sum(item.seconds for item in items)
    excess_micro_seconds = sum(
        max(0, item.reserved_micro - item.active_spend_micro) * item.seconds
        for item in items
    )
    unobserved_seconds = 0
    if window_start is not None and window_end is not None:
        window_start = _aware(window_start, field="window_start")
        window_end = _aware(window_end, field="window_end")
        if window_end < window_start:
            raise ValueError("window_end must not precede window_start")
        for item in items:
            if item.start < window_start or item.end > window_end:
                raise ValueError("observed interval lies outside the declared window")
        unobserved_seconds = max(
            0,
            int((window_end - window_start).total_seconds()) - observed_seconds,
        )
    if not items:
        return MetricResult(
            name="LCI",
            status=STATUS_NOT_COMPUTED,
            numerator=None,
            denominator=None,
            value=None,
            unit="micro_usd_hours",
            missing=0,
            notes=("no observed reservation intervals",),
        )
    with localcontext() as context:
        context.prec = 28
        value = Decimal(excess_micro_seconds) / Decimal(3600)
    return MetricResult(
        name="LCI",
        status=STATUS_COMPUTED,
        numerator=excess_micro_seconds,
        denominator=3600,
        value=value,
        unit="micro_usd_hours",
        notes=(
            f"observed_seconds={observed_seconds}",
            f"unobserved_seconds={unobserved_seconds}",
            "Gaps are reported, not interpolated.",
            "Locked capital is descriptive; it is not a budget-safety guarantee.",
        ),
    )


@dataclass(frozen=True)
class RequestDecision:
    """One eligible real request and its recorded decision, if any."""

    request_id: str
    eligible: bool
    decision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(self.eligible, bool):
            raise TypeError("eligible must be a bool")
        if self.decision is not None and self.decision not in {"APPROVE", "DENY"}:
            raise ValueError("decision must be APPROVE, DENY, or None")


def request_acceptance_rate(requests: Iterable[RequestDecision]) -> MetricResult:
    """RAR: approved eligible requests divided by all eligible real requests."""
    items = tuple(requests)
    eligible = [item for item in items if item.eligible]
    approved = sum(1 for item in eligible if item.decision == "APPROVE")
    unknown = sum(1 for item in eligible if item.decision is None)
    excluded = sum(1 for item in items if not item.eligible)
    return _ratio(
        "RAR",
        approved,
        len(eligible),
        unit="ratio",
        missing=unknown,
        excluded=excluded,
        notes=(
            "A request with no recorded decision is unknown, not denied.",
            "Report alongside BOF/ABR and LCI; a high RAR alone is not success.",
        ),
    )


def coverage(outcomes: Iterable[JobOutcome]) -> MetricResult:
    """Marginal job-level upper-bound coverage with a decision-time bound."""
    items = tuple(outcomes)
    evaluable = [
        item
        for item in items
        if item.has_final_label
        and item.upper_bound_micro is not None
        and item.bound_frozen_at is not None
        and item.decision_at is not None
    ]
    covered = sum(
        1 for item in evaluable if item.actual_billed_micro <= item.upper_bound_micro
    )
    excluded = len(items) - len(evaluable)
    return _ratio(
        "coverage",
        covered,
        len(evaluable),
        unit="ratio",
        excluded=excluded,
        notes=(
            "Marginal job-level coverage, not a finite-sample or aggregate guarantee.",
            "A bound computed with future labels or without a decision timestamp is ineligible.",
        ),
    )


def matching_rate(
    *,
    matched: int,
    unmatched: int,
    ambiguous: int,
) -> MetricResult:
    """Matching rate with unmatched and ambiguous rows kept visible."""
    matched = _nonnegative_int(matched, field="matched")
    unmatched = _nonnegative_int(unmatched, field="unmatched")
    ambiguous = _nonnegative_int(ambiguous, field="ambiguous")
    return _ratio(
        "matching_rate",
        matched,
        matched + unmatched + ambiguous,
        unit="ratio",
        missing=unmatched,
        excluded=ambiguous,
        notes=("Unmatched and ambiguous records are excluded, not silently dropped.",),
    )


@dataclass(frozen=True)
class FeedbackDelay:
    """Observed job completion and the first time its final bill was observable."""

    job_id: str
    completed_at: datetime
    first_available_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("job_id must be a non-empty string")
        completed = _aware(self.completed_at, field="completed_at")
        available = _aware(self.first_available_at, field="first_available_at")
        if available < completed:
            raise ValueError("first_available_at must not precede completed_at")


def describe_feedback_delay(
    delays: Iterable[FeedbackDelay],
    *,
    censored_count: int = 0,
) -> dict[str, Any]:
    """Median/p90 observed feedback delay, or an explicit not-computed result."""
    items = tuple(delays)
    censored_count = _nonnegative_int(censored_count, field="censored_count")
    seconds = sorted(
        int((item.first_available_at - item.completed_at).total_seconds()) for item in items
    )
    if not seconds:
        return {
            "status": STATUS_NOT_COMPUTED,
            "count": 0,
            "censored_count": censored_count,
            "median_seconds": None,
            "p90_seconds": None,
            "reason": "no_observed_feedback_delays",
        }
    middle = len(seconds) // 2
    median = (
        seconds[middle]
        if len(seconds) % 2
        else (seconds[middle - 1] + seconds[middle]) // 2
    )
    rank = max(0, (9 * len(seconds) + 9) // 10 - 1)
    return {
        "status": STATUS_COMPUTED,
        "count": len(seconds),
        "censored_count": censored_count,
        "median_seconds": median,
        "p90_seconds": seconds[rank],
    }


def _ceil_micro(value: Decimal) -> int:
    from decimal import ROUND_CEILING

    return int(value.to_integral_value(rounding=ROUND_CEILING))


def reserve_raw_point(point_micro: int | None) -> int | None:
    """The raw point-estimate policy; ``None`` stays ``None``."""
    if point_micro is None:
        return None
    return _nonnegative_int(point_micro, field="point_micro")


def reserve_static_margin(
    point_micro: int | None,
    multiplier: Decimal | str | int = Decimal("1.20"),
) -> int | None:
    """Static-margin baseline (protocol default: a 20% margin, a selected rule)."""
    if point_micro is None:
        return None
    point = _nonnegative_int(point_micro, field="point_micro")
    if isinstance(multiplier, bool):
        raise TypeError("multiplier must be a number")
    try:
        factor = Decimal(str(multiplier))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("multiplier must be a number") from exc
    if not factor.is_finite() or factor < 1:
        raise ValueError("multiplier must be a finite number >= 1")
    return _ceil_micro(Decimal(point) * factor)


@dataclass(frozen=True)
class BaselineAvailability:
    """Whether a baseline can be evaluated on the actual available records."""

    policy: str
    status: str
    reason: str
    required_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {
            STATUS_AVAILABLE,
            STATUS_UNAVAILABLE,
            STATUS_REFERENCE_ONLY,
        }:
            raise ValueError(f"invalid availability status {self.status!r}")

    @property
    def available(self) -> bool:
        return self.status in {STATUS_AVAILABLE, STATUS_REFERENCE_ONLY}

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "status": self.status,
            "reason": self.reason,
            "required_fields": list(self.required_fields),
        }


def baseline_availability(
    *,
    has_point_estimates: bool,
    has_final_labels: bool,
    has_telemetry: bool,
    has_ledger_context: bool,
    has_calibration_history: bool,
    has_revision_history: bool,
    training_label_count: int = 0,
) -> tuple[BaselineAvailability, ...]:
    """Return an explicit availability record for every protocol baseline.

    Unavailable baselines are never replaced with substitute labels or generated
    features; the runner reports the reason instead.
    """
    _nonnegative_int(training_label_count, field="training_label_count")
    results: list[BaselineAvailability] = []

    def add(
        policy: str,
        available: bool,
        reason: str,
        required: tuple[str, ...],
        *,
        reference: bool = False,
    ) -> None:
        if available:
            status = STATUS_REFERENCE_ONLY if reference else STATUS_AVAILABLE
        else:
            status = STATUS_UNAVAILABLE
        results.append(BaselineAvailability(policy, status, reason, required))

    base_required = ("point_cost_estimate", "final_billed_label", "decision_timestamp")
    add(
        POLICY_RAW_POINT,
        has_point_estimates and has_final_labels,
        "point estimates and final labels are both required",
        base_required,
    )
    add(
        POLICY_STATIC_MARGIN,
        has_point_estimates and has_final_labels,
        "point estimates and final labels are both required",
        base_required,
    )
    add(
        POLICY_DELAYED_ACI,
        has_final_labels and has_calibration_history,
        "labels with observed-at timestamps and a prior calibration history are required",
        (*base_required, "final_label_observed_at", "calibration_history"),
    )
    add(
        POLICY_QUANTILE_REGRESSION,
        has_final_labels and training_label_count >= 30,
        "at least 30 real labelled training requests are required by the fixed rule",
        (*base_required, "request_features", "training_label_count>=30"),
    )
    add(
        POLICY_DRIFT,
        has_final_labels and has_calibration_history,
        "ordered real residuals across time are required",
        (*base_required, "residual_history"),
    )
    calibur_ready = (
        has_point_estimates
        and has_final_labels
        and has_telemetry
        and has_ledger_context
    )
    calibur_required = (
        *base_required,
        "upper_cost_bound_or_calibration",
        "telemetry_observation",
        "ledger_state_at_decision",
        "revision_history",
    )
    add(POLICY_CALIBUR, calibur_ready, "all CALIBUR input channels are required", calibur_required)
    add(
        POLICY_CALIBUR_NO_EARLY_WARNING,
        has_point_estimates and has_final_labels and has_ledger_context,
        "CALIBUR ablation needs point estimates, final labels, and ledger context",
        (*base_required, "ledger_state_at_decision"),
    )
    add(
        POLICY_CALIBUR_SIMPLE_ESTIMATOR,
        calibur_ready,
        "CALIBUR ablation shares the full input requirement",
        calibur_required,
    )
    add(
        POLICY_CALIBUR_FIXED_RELEASE,
        calibur_ready and has_revision_history,
        "fixed-release ablation needs observed revision history",
        (*calibur_required, "observed_revision_history"),
    )
    add(
        POLICY_ORACLE,
        has_final_labels,
        "oracle uses the final label and is an unattainable reference only",
        ("final_billed_label",),
        reference=True,
    )
    return tuple(results)
