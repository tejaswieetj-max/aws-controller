"""Release timing from observed revision history, with explicit censoring.

Only real, caller-supplied completion and revision observations are used.  The
module never creates revision times, never treats an unobserved revision as
"never revised", and never generates a delay distribution.  When the supplied
history has no observed revision event the result is an explicit
``NOT_ESTIMABLE`` status instead of a fitted curve.

Two layers are kept separate:

* :func:`summarize_revision_history` is retrospective and descriptive.
* :func:`plan_release` is a prospective, fixed-rule policy.  It uses a
  Kaplan--Meier product-limit quantile only when the history contains observed
  revision events and enough of them, and it always retains a documented
  residual hold.  The recommendation is not a guarantee and the Spendctl ledger
  remains authoritative.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from fractions import Fraction

from .models import (
    CURRENCY_USD,
    MICRO_USD,
    FutureInputError,
    InvalidModelError,
    SourceRef,
    _amount,
    _currency,
    _decimal,
    _sources,
    _text,
    _timestamp,
    _unit,
    assert_known_by,
)

__all__ = [
    "REASON_CENSORED_HISTORY_ONLY",
    "REASON_HOLD_CAPPED",
    "REASON_INSUFFICIENT_OBSERVED_REVISIONS",
    "REASON_NO_OBSERVATIONS",
    "REASON_NO_OBSERVED_REVISIONS",
    "REASON_QUANTILE_NOT_REACHED",
    "REASON_RELEASE_FROM_HISTORY",
    "RELEASE_LIMITATIONS",
    "RELEASE_POLICY_VERSION",
    "STATUS_ESTIMABLE",
    "STATUS_NOT_ESTIMABLE",
    "ReleasePolicyConfig",
    "ReleaseProposal",
    "RevisionObservation",
    "RevisionSummary",
    "km_quantile_seconds",
    "plan_release",
    "summarize_revision_history",
]

RELEASE_POLICY_VERSION = "calibur-release/0.1"

STATUS_ESTIMABLE = "ESTIMABLE"
STATUS_NOT_ESTIMABLE = "NOT_ESTIMABLE"
STATUS_DESCRIPTIVE_ONLY = "DESCRIPTIVE_ONLY"

REASON_NO_OBSERVATIONS = "NO_OBSERVATIONS"
REASON_NO_OBSERVED_REVISIONS = "NO_OBSERVED_REVISIONS"
REASON_CENSORED_HISTORY_ONLY = "CENSORED_HISTORY_ONLY"
REASON_INSUFFICIENT_OBSERVED_REVISIONS = "INSUFFICIENT_OBSERVED_REVISIONS"
REASON_QUANTILE_NOT_REACHED = "QUANTILE_NOT_REACHED"
REASON_HOLD_CAPPED = "HOLD_CAPPED_BY_MAX"
REASON_RELEASE_FROM_HISTORY = "RELEASE_FROM_OBSERVED_HISTORY"

RELEASE_LIMITATIONS = (
    (
        "Revision observations describe when an observed billing value changed; a "
        "record that did not change before its observation deadline is right-censored."
    ),
    (
        "A Kaplan-Meier quantile is a descriptive estimate from the supplied "
        "history, not a guarantee that no later revision will arrive."
    ),
    (
        "A residual amount is always retained; the Spendctl ledger remains "
        "authoritative for release writes."
    ),
    "No revision time, delay distribution, or censoring deadline is generated.",
)


def _pairs(
    observations: Iterable[RevisionObservation],
) -> tuple[RevisionObservation, ...]:
    if isinstance(observations, RevisionObservation):
        raise InvalidModelError("observations must be an iterable of RevisionObservation")
    if isinstance(observations, (str, bytes)):
        raise InvalidModelError("observations must be an iterable of RevisionObservation")
    try:
        items = tuple(observations)
    except TypeError as exc:
        raise InvalidModelError("observations must be an iterable of RevisionObservation") from exc
    if any(not isinstance(item, RevisionObservation) for item in items):
        raise InvalidModelError("observations must contain RevisionObservation instances")
    return items


@dataclass(frozen=True)
class RevisionObservation:
    """One real record's completion, observation deadline, and revision time.

    ``observed_until`` is the end of the period in which the record was actually
    observed.  A record with no ``revised_at`` is right-censored at that time.
    """

    record_key: str
    completed_at: datetime
    observed_until: datetime
    source: SourceRef
    revised_at: datetime | None = None
    revision_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "record_key", _text(self.record_key, field_name="record_key")
        )
        completed = _timestamp(self.completed_at, field_name="completed_at")
        observed = _timestamp(self.observed_until, field_name="observed_until")
        if observed < completed:
            raise InvalidModelError("observed_until must not precede completed_at")
        object.__setattr__(self, "completed_at", completed)
        object.__setattr__(self, "observed_until", observed)
        if not isinstance(self.source, SourceRef):
            raise InvalidModelError("source must be a SourceRef")
        if self.revised_at is not None:
            revised = _timestamp(self.revised_at, field_name="revised_at")
            if revised <= completed:
                raise InvalidModelError("revised_at must be later than completed_at")
            if revised > observed:
                raise InvalidModelError("revised_at must not be later than observed_until")
            object.__setattr__(self, "revised_at", revised)
        if self.revision_version is not None:
            object.__setattr__(
                self,
                "revision_version",
                _text(self.revision_version, field_name="revision_version"),
            )

    @property
    def is_revised(self) -> bool:
        return self.revised_at is not None

    @property
    def is_censored(self) -> bool:
        """True when the observation window closed without a revision event."""
        return self.revised_at is None

    @property
    def delay_seconds(self) -> int | None:
        if self.revised_at is None:
            return None
        return int((self.revised_at - self.completed_at).total_seconds())

    def to_dict(self) -> dict[str, object]:
        return {
            "record_key": self.record_key,
            "completed_at": self.completed_at.isoformat().replace("+00:00", "Z"),
            "observed_until": self.observed_until.isoformat().replace("+00:00", "Z"),
            "revised_at": (
                None
                if self.revised_at is None
                else self.revised_at.isoformat().replace("+00:00", "Z")
            ),
            "revision_version": self.revision_version,
            "censored": self.is_censored,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class RevisionSummary:
    """Retrospective description of supplied revision observations."""

    status: str
    reason: str | None
    total_count: int
    revised_count: int
    censored_count: int
    delays_seconds: tuple[int, ...] = ()
    median_delay_seconds: int | None = None
    max_delay_seconds: int | None = None

    @property
    def is_estimable(self) -> bool:
        return self.status == STATUS_DESCRIPTIVE_ONLY

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "total_count": self.total_count,
            "revised_count": self.revised_count,
            "censored_count": self.censored_count,
            "delays_seconds": list(self.delays_seconds),
            "median_delay_seconds": self.median_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
        }


def _median(values: tuple[int, ...]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def summarize_revision_history(
    observations: Iterable[RevisionObservation],
    *,
    as_of: datetime | None = None,
) -> RevisionSummary:
    """Describe observed revision delays without fitting a curve.

    Returns ``NOT_ESTIMABLE`` when no observation exists or when no revision
    event was observed.  Censored records are counted, never reclassified as
    unrevised.
    """
    items = _pairs(observations)
    if as_of is not None:
        cutoff = _timestamp(as_of, field_name="as_of")
        for item in items:
            assert_known_by(item.observed_until, now=cutoff, field_name="observed_until")
    if not items:
        return RevisionSummary(
            status=STATUS_NOT_ESTIMABLE,
            reason=REASON_NO_OBSERVATIONS,
            total_count=0,
            revised_count=0,
            censored_count=0,
        )
    delays = tuple(
        sorted(
            delay
            for item in items
            if (delay := item.delay_seconds) is not None
        )
    )
    revised_count = len(delays)
    censored_count = len(items) - revised_count
    if revised_count == 0:
        return RevisionSummary(
            status=STATUS_NOT_ESTIMABLE,
            reason=REASON_NO_OBSERVED_REVISIONS,
            total_count=len(items),
            revised_count=0,
            censored_count=censored_count,
        )
    return RevisionSummary(
        status=STATUS_DESCRIPTIVE_ONLY,
        reason=REASON_CENSORED_HISTORY_ONLY if censored_count else None,
        total_count=len(items),
        revised_count=revised_count,
        censored_count=censored_count,
        delays_seconds=delays,
        median_delay_seconds=_median(delays),
        max_delay_seconds=max(delays),
    )


def _duration_seconds(observation: RevisionObservation) -> int:
    """Observed revision delay, or the censoring horizon for an unrevised record."""
    if observation.revised_at is not None:
        return int((observation.revised_at - observation.completed_at).total_seconds())
    return int((observation.observed_until - observation.completed_at).total_seconds())


def km_quantile_seconds(
    observations: Iterable[RevisionObservation],
    quantile: Decimal | str | float,
) -> int | None:
    """Return the smallest observed event time whose Kaplan--Meier survival
    estimate is at or below ``1 - quantile``.

    Returns ``None`` when the history has no observed revision event or when
    censoring prevents survival from reaching the requested level.  The result
    is descriptive and carries no guarantee.
    """
    q = _decimal(quantile, field_name="quantile", minimum=Decimal(0), maximum=Decimal(1))
    if q >= 1:
        raise InvalidModelError("quantile must be less than one")
    if q <= 0:
        raise InvalidModelError("quantile must be greater than zero")
    items = _pairs(observations)
    events = [(_duration_seconds(item), item.is_revised) for item in items]
    event_times = sorted({duration for duration, revised in events if revised})
    if not event_times:
        return None
    survival = Fraction(1)
    target = Fraction(1) - Fraction(q)
    for time in event_times:
        at_risk = sum(1 for duration, _ in events if duration >= time)
        failures = sum(1 for duration, revised in events if revised and duration == time)
        if at_risk == 0:
            continue
        survival *= Fraction(at_risk - failures, at_risk)
        if survival <= target:
            return time
    return None


@dataclass(frozen=True)
class ReleasePolicyConfig:
    """Fixed prospective release parameters, never fitted during evaluation."""

    retention_quantile: Decimal = Decimal("0.90")
    min_observed_revisions: int = 30
    residual_hold_fraction: Decimal = Decimal("0.05")
    max_hold: timedelta = timedelta(days=30)
    currency: str = CURRENCY_USD
    unit: str = MICRO_USD

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "retention_quantile",
            _decimal(
                self.retention_quantile,
                field_name="retention_quantile",
                minimum=Decimal(0),
                maximum=Decimal(1),
            ),
        )
        if not Decimal(0) < self.retention_quantile < Decimal(1):
            raise InvalidModelError("retention_quantile must be in (0, 1)")
        if not isinstance(self.min_observed_revisions, int) or isinstance(
            self.min_observed_revisions, bool
        ):
            raise InvalidModelError("min_observed_revisions must be an integer")
        if self.min_observed_revisions < 1:
            raise InvalidModelError("min_observed_revisions must be at least 1")
        object.__setattr__(
            self,
            "residual_hold_fraction",
            _decimal(
                self.residual_hold_fraction,
                field_name="residual_hold_fraction",
                minimum=Decimal(0),
                maximum=Decimal(1),
            ),
        )
        if not isinstance(self.max_hold, timedelta):
            raise InvalidModelError("max_hold must be a timedelta")
        if self.max_hold <= timedelta(0):
            raise InvalidModelError("max_hold must be positive")
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))


@dataclass(frozen=True)
class ReleaseProposal:
    """Advisory release plan; never a ledger write or a safety guarantee."""

    status: str
    reason: str
    completed_at: datetime
    as_of: datetime
    committed_amount_micro: int | None
    retained_amount_micro: int | None
    release_at: datetime | None
    quantile: Decimal
    observed_revisions: int
    censored_count: int
    source_refs: tuple[SourceRef, ...]
    policy_version: str = RELEASE_POLICY_VERSION
    currency: str = CURRENCY_USD
    unit: str = MICRO_USD
    limitations: tuple[str, ...] = RELEASE_LIMITATIONS

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _text(self.status, field_name="status"))
        object.__setattr__(self, "reason", _text(self.reason, field_name="reason"))
        object.__setattr__(
            self, "completed_at", _timestamp(self.completed_at, field_name="completed_at")
        )
        object.__setattr__(self, "as_of", _timestamp(self.as_of, field_name="as_of"))
        for name in ("committed_amount_micro", "retained_amount_micro"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _amount(value, field_name=name))
        if self.release_at is not None:
            object.__setattr__(
                self, "release_at", _timestamp(self.release_at, field_name="release_at")
            )
        object.__setattr__(
            self,
            "quantile",
            _decimal(self.quantile, field_name="quantile", minimum=Decimal(0), maximum=Decimal(1)),
        )
        if not isinstance(self.observed_revisions, int) or isinstance(
            self.observed_revisions, bool
        ):
            raise InvalidModelError("observed_revisions must be an integer")
        if self.observed_revisions < 0:
            raise InvalidModelError("observed_revisions must not be negative")
        if not isinstance(self.censored_count, int) or isinstance(self.censored_count, bool):
            raise InvalidModelError("censored_count must be an integer")
        if self.censored_count < 0:
            raise InvalidModelError("censored_count must not be negative")
        object.__setattr__(self, "source_refs", _sources(self.source_refs))
        object.__setattr__(
            self, "policy_version", _text(self.policy_version, field_name="policy_version")
        )
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "unit", _unit(self.unit))
        if self.status == STATUS_NOT_ESTIMABLE:
            if self.release_at is not None or self.retained_amount_micro is not None:
                raise InvalidModelError(
                    "a NOT_ESTIMABLE release must not carry a release time or retained amount"
                )
        elif self.status == STATUS_ESTIMABLE:
            if self.release_at is None or self.retained_amount_micro is None:
                raise InvalidModelError(
                    "an ESTIMABLE release requires a release time and retained amount"
                )
        else:
            raise InvalidModelError("status must be ESTIMABLE or NOT_ESTIMABLE")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "completed_at": self.completed_at.isoformat().replace("+00:00", "Z"),
            "as_of": self.as_of.isoformat().replace("+00:00", "Z"),
            "committed_amount_micro": self.committed_amount_micro,
            "retained_amount_micro": self.retained_amount_micro,
            "release_at": (
                None if self.release_at is None else self.release_at.isoformat().replace("+00:00", "Z")
            ),
            "quantile": str(self.quantile),
            "observed_revisions": self.observed_revisions,
            "censored_count": self.censored_count,
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "policy_version": self.policy_version,
            "currency": self.currency,
            "unit": self.unit,
            "limitations": list(self.limitations),
        }


def _dedupe(refs: Iterable[SourceRef]) -> tuple[SourceRef, ...]:
    seen: set[tuple[str, datetime, str, str]] = set()
    ordered: list[SourceRef] = []
    for ref in refs:
        key = (ref.source_id, ref.observed_at, ref.source_file, ref.semantics)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(ref)
    return tuple(ordered)


def plan_release(
    *,
    completed_at: datetime,
    committed_amount_micro: int,
    history: Iterable[RevisionObservation],
    as_of: datetime,
    job_source: SourceRef,
    config: ReleasePolicyConfig | None = None,
) -> ReleaseProposal:
    """Return a prospective release proposal from observed revision history.

    The proposal is ``NOT_ESTIMABLE`` when the history has no observed revision
    events, fewer than ``min_observed_revisions`` events, or when censoring keeps
    the Kaplan--Meier survival estimate above ``1 - retention_quantile``.
    """
    policy = config or ReleasePolicyConfig()
    if not isinstance(policy, ReleasePolicyConfig):
        raise InvalidModelError("config must be a ReleasePolicyConfig or None")
    completed = _timestamp(completed_at, field_name="completed_at")
    cutoff = _timestamp(as_of, field_name="as_of")
    if completed > cutoff:
        raise FutureInputError("completed_at is later than as_of")
    amount = _amount(committed_amount_micro, field_name="committed_amount_micro")
    if not isinstance(job_source, SourceRef):
        raise InvalidModelError("job_source must be a SourceRef")
    items = _pairs(history)
    for item in items:
        assert_known_by(item.observed_until, now=cutoff, field_name="observed_until")
    summary = summarize_revision_history(items, as_of=cutoff)
    refs = [job_source, *(item.source for item in items)]
    common = {
        "completed_at": completed,
        "as_of": cutoff,
        "committed_amount_micro": amount,
        "quantile": policy.retention_quantile,
        "observed_revisions": summary.revised_count,
        "censored_count": summary.censored_count,
        "source_refs": _dedupe(refs),
    }

    if summary.status == STATUS_NOT_ESTIMABLE:
        return ReleaseProposal(
            status=STATUS_NOT_ESTIMABLE,
            reason=summary.reason or REASON_NO_OBSERVED_REVISIONS,
            retained_amount_micro=None,
            release_at=None,
            **common,
        )
    if summary.revised_count < policy.min_observed_revisions:
        return ReleaseProposal(
            status=STATUS_NOT_ESTIMABLE,
            reason=REASON_INSUFFICIENT_OBSERVED_REVISIONS,
            retained_amount_micro=None,
            release_at=None,
            **common,
        )
    quantile_seconds = km_quantile_seconds(items, policy.retention_quantile)
    if quantile_seconds is None:
        return ReleaseProposal(
            status=STATUS_NOT_ESTIMABLE,
            reason=REASON_QUANTILE_NOT_REACHED,
            retained_amount_micro=None,
            release_at=None,
            **common,
        )
    hold = timedelta(seconds=quantile_seconds)
    reason = REASON_RELEASE_FROM_HISTORY
    if hold > policy.max_hold:
        hold = policy.max_hold
        reason = REASON_HOLD_CAPPED
    retained = int(
        (Decimal(amount) * policy.residual_hold_fraction).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    return ReleaseProposal(
        status=STATUS_ESTIMABLE,
        reason=reason,
        retained_amount_micro=retained,
        release_at=completed + hold,
        **common,
    )
