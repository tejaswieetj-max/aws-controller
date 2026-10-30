"""Asymmetric reservation risk policy over caller-supplied evidence.

The policy is pure: it reads no files, environment variables, clocks, or AWS
services.  It receives an estimate, an optional final-label calibration
snapshot, an optional telemetry snapshot, and the authoritative ledger state
supplied by the caller, and returns a :class:`~calibur_engine.models.ReservationProposal`.
The proposal is advisory.  The existing Spendctl ledger remains authoritative and
performs the atomic reservation write.

Reservation rule
----------------
Only a point estimate and, optionally, an upper cost bound are available.  The
policy does not assume a probability distribution, so it minimises a weighted
*worst-case loss bound* over the interval between the static-margin floor and
the upper bound:

* under-reserving is charged ``under_reserve_weight`` per unit between the
  proposed reservation and the upper bound;
* over-reserving is charged ``over_reserve_weight`` per unit between the point
  estimate and the proposed reservation.

With the default weights (under-reserve four times more costly than
over-reserve) the minimiser is the upper bound.  With a larger over-reserve
weight the minimiser is the static-margin floor.  Equal weights are indifferent,
so the policy chooses the smaller (least locked capital) amount.  These are
documented, fixed policy rules, not fitted parameters and not guarantees.

Staleness and missingness
-------------------------
Amounts are never inferred.  A missing point estimate, a missing upper bound
when the configuration requires one, a stale snapshot, or a stale telemetry
observation produces an explicit ``DENY`` with stable reason codes.  A supplied
telemetry snapshot contributes only its caller-computed
``additional_exposure_micro``; the policy never converts provisional usage to
currency and never treats an absent observation as zero.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import ROUND_CEILING, Decimal

from .models import (
    CURRENCY_USD,
    MICRO_USD,
    POLICY_VERSION,
    BudgetState,
    CalibrationSnapshot,
    Decision,
    EstimateSnapshot,
    FutureInputError,
    InvalidModelError,
    RequestFeatures,
    ReservationProposal,
    RiskPolicyConfig,
    SourceRef,
    TelemetrySnapshot,
    assert_known_by,
)

__all__ = [
    "REASON_BUDGET_CLOSED",
    "REASON_BUDGET_EXACT",
    "REASON_BUDGET_INSUFFICIENT",
    "REASON_BUDGET_OPEN",
    "REASON_BUDGET_SUFFICIENT",
    "REASON_CALIBRATION_USED",
    "REASON_ESTIMATE_MISSING",
    "REASON_ESTIMATE_PRESENT",
    "REASON_EVIDENCE_FRESH",
    "REASON_EVIDENCE_STALE",
    "REASON_MINIMUM_FLOOR_APPLIED",
    "REASON_NO_CALIBRATION",
    "REASON_NO_TELEMETRY",
    "REASON_POLICY_APPROVED",
    "REASON_POLICY_DENIED",
    "REASON_RESERVE_POINT_FLOOR",
    "REASON_RESERVE_UPPER_BOUND",
    "REASON_TELEMETRY_ADDED",
    "REASON_TELEMETRY_STALE",
    "REASON_UPPER_BELOW_STATIC_FLOOR",
    "REASON_UPPER_BOUND_MISSING",
    "REASON_UPPER_BOUND_PRESENT",
    "RISK_LIMITATIONS",
    "FutureInputError",
    "decide_reservation",
]

REASON_EVIDENCE_FRESH = "EVIDENCE_FRESH"
REASON_EVIDENCE_STALE = "EVIDENCE_STALE"
REASON_ESTIMATE_PRESENT = "ESTIMATE_PRESENT"
REASON_ESTIMATE_MISSING = "ESTIMATE_MISSING"
REASON_UPPER_BOUND_PRESENT = "UPPER_BOUND_PRESENT"
REASON_UPPER_BOUND_MISSING = "UPPER_BOUND_MISSING"
REASON_UPPER_BELOW_STATIC_FLOOR = "UPPER_BELOW_STATIC_FLOOR"
REASON_CALIBRATION_USED = "CALIBRATION_USED"
REASON_NO_CALIBRATION = "NO_CALIBRATION"
REASON_BUDGET_OPEN = "BUDGET_OPEN"
REASON_BUDGET_CLOSED = "BUDGET_CLOSED"
REASON_BUDGET_SUFFICIENT = "BUDGET_SUFFICIENT"
REASON_BUDGET_EXACT = "BUDGET_EXACT"
REASON_BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
REASON_TELEMETRY_ADDED = "TELEMETRY_ADDED"
REASON_TELEMETRY_STALE = "TELEMETRY_STALE"
REASON_NO_TELEMETRY = "NO_TELEMETRY"
REASON_RESERVE_UPPER_BOUND = "RESERVE_UPPER_BOUND"
REASON_RESERVE_POINT_FLOOR = "RESERVE_POINT_FLOOR"
REASON_MINIMUM_FLOOR_APPLIED = "MINIMUM_FLOOR_APPLIED"
REASON_POLICY_APPROVED = "POLICY_APPROVED"
REASON_POLICY_DENIED = "POLICY_DENIED"

RISK_LIMITATIONS = (
    (
        "The proposal is advisory; the existing Spendctl ledger performs the "
        "authoritative atomic reservation."
    ),
    (
        "Loss figures are weighted worst-case bounds over the supplied point and "
        "upper estimates, not expected losses, coverage, or budget-safety guarantees."
    ),
    (
        "No probability model is fitted; the under- and over-reserve weights are "
        "fixed policy parameters."
    ),
    "A missing amount is never replaced with zero or an inferred price.",
)


def _ceil_micro(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _weighted_micro(weight: Decimal, amount_micro: int) -> int:
    return _ceil_micro(weight * Decimal(amount_micro))


def _dedupe_refs(refs: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    seen: set[tuple[str, datetime, str, str]] = set()
    ordered: list[SourceRef] = []
    for ref in refs:
        key = (ref.source_id, ref.observed_at, ref.source_file, ref.semantics)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(ref)
    return tuple(ordered)


def _upper_bound(
    estimate: EstimateSnapshot,
    calibration: CalibrationSnapshot | None,
) -> tuple[int | None, bool]:
    """Return the conservative bound and whether calibration supplied it."""
    estimate_bound = estimate.upper_cost_micro
    calibration_bound = calibration.upper_cost_micro if calibration is not None else None
    if calibration_bound is None:
        return estimate_bound, False
    if estimate_bound is None:
        return calibration_bound, True
    if calibration_bound >= estimate_bound:
        return calibration_bound, True
    return estimate_bound, False


def _check_currency(
    *snapshots: EstimateSnapshot | CalibrationSnapshot | TelemetrySnapshot | BudgetState,
) -> None:
    for snapshot in snapshots:
        if snapshot is None:
            continue
        if snapshot.currency != CURRENCY_USD or snapshot.unit != MICRO_USD:
            raise InvalidModelError(
                "all snapshots must use USD micro-amounts; got "
                f"{snapshot.currency!r}/{snapshot.unit!r}"
            )


def _audit_reference(
    *,
    request: RequestFeatures,
    budget: BudgetState,
    estimate: EstimateSnapshot,
    decision: Decision,
    reservation_micro: int,
    state_version: str,
    decision_at: datetime,
) -> str:
    payload = "|".join(
        (
            POLICY_VERSION,
            request.request_id,
            decision_at.isoformat(),
            budget.team,
            budget.period,
            budget.as_of.isoformat(),
            budget.source.source_id,
            estimate.method,
            estimate.as_of.isoformat(),
            state_version,
            decision.value,
            str(reservation_micro),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"calibur-{digest[:32]}"


def decide_reservation(
    request: RequestFeatures,
    estimate: EstimateSnapshot,
    budget: BudgetState,
    *,
    decision_at: datetime,
    calibration: CalibrationSnapshot | None = None,
    telemetry: TelemetrySnapshot | None = None,
    config: RiskPolicyConfig | None = None,
) -> ReservationProposal:
    """Return an advisory APPROVE/DENY proposal with stable reason codes.

    Every timestamp is checked against *decision_at*.  The function raises
    :class:`~calibur_engine.models.FutureInputError` for future-dated inputs and
    :class:`~calibur_engine.models.InvalidModelError` for structurally
    inconsistent evidence (team mismatch, mixed currency, or an upper bound
    below the static-margin floor).
    """
    policy = config or RiskPolicyConfig()
    if not isinstance(request, RequestFeatures):
        raise InvalidModelError("request must be a RequestFeatures instance")
    if not isinstance(estimate, EstimateSnapshot):
        raise InvalidModelError("estimate must be an EstimateSnapshot instance")
    if not isinstance(budget, BudgetState):
        raise InvalidModelError("budget must be a BudgetState instance")
    if calibration is not None and not isinstance(calibration, CalibrationSnapshot):
        raise InvalidModelError("calibration must be a CalibrationSnapshot or None")
    if telemetry is not None and not isinstance(telemetry, TelemetrySnapshot):
        raise InvalidModelError("telemetry must be a TelemetrySnapshot or None")
    if not isinstance(decision_at, datetime):
        raise InvalidModelError("decision_at must be a datetime")
    if decision_at.tzinfo is None:
        raise InvalidModelError("decision_at must be timezone-aware")

    if request.team != budget.team:
        raise InvalidModelError(
            f"request team {request.team!r} does not match budget team {budget.team!r}"
        )

    snapshots: list[EstimateSnapshot | CalibrationSnapshot | TelemetrySnapshot | BudgetState] = [
        estimate,
        budget,
    ]
    if calibration is not None:
        snapshots.append(calibration)
    if telemetry is not None:
        snapshots.append(telemetry)
    _check_currency(*snapshots)

    # Every observation that informs the decision must already be known.
    for snapshot in snapshots:
        assert_known_by(snapshot.as_of, now=decision_at, field_name="snapshot.as_of")
    assert_known_by(
        request.requested_at,
        now=decision_at,
        field_name="request.requested_at",
    )
    assert_known_by(
        estimate.as_of,
        now=decision_at,
        field_name="estimate.as_of",
    )

    evidence_age = decision_at - min(snapshot.as_of for snapshot in snapshots)

    reasons: list[str] = []
    limitations = list(RISK_LIMITATIONS)

    if telemetry is not None:
        telemetry_age = decision_at - telemetry.as_of
        if telemetry_age > policy.max_evidence_age:
            reasons.append(REASON_TELEMETRY_STALE)
    if evidence_age > policy.max_evidence_age:
        reasons.append(REASON_EVIDENCE_STALE)
    else:
        reasons.append(REASON_EVIDENCE_FRESH)

    point = estimate.point_cost_micro
    upper, calibration_used = _upper_bound(estimate, calibration)
    if point is None:
        reasons.append(REASON_ESTIMATE_MISSING)
    else:
        reasons.append(REASON_ESTIMATE_PRESENT)
    if upper is None:
        reasons.append(REASON_UPPER_BOUND_MISSING)
    else:
        reasons.append(REASON_UPPER_BOUND_PRESENT)
    reasons.append(REASON_CALIBRATION_USED if calibration_used else REASON_NO_CALIBRATION)

    if budget.is_open:
        reasons.append(REASON_BUDGET_OPEN)
    else:
        reasons.append(REASON_BUDGET_CLOSED)

    denial: str | None = None
    required_micro = 0
    reserve_reason: str | None = None

    if REASON_EVIDENCE_STALE in reasons:
        denial = REASON_EVIDENCE_STALE
    elif REASON_TELEMETRY_STALE in reasons:
        denial = REASON_TELEMETRY_STALE
    elif not budget.is_open:
        denial = REASON_BUDGET_CLOSED
    elif point is None:
        denial = REASON_ESTIMATE_MISSING
    elif upper is None and policy.require_upper_bound:
        denial = REASON_UPPER_BOUND_MISSING
    else:
        static_floor = _ceil_micro(Decimal(point) * policy.static_margin)
        if upper is not None and policy.under_reserve_weight > policy.over_reserve_weight:
            required_micro = upper
            reserve_reason = REASON_RESERVE_UPPER_BOUND
        elif upper is not None:
            required_micro = min(static_floor, upper)
            reserve_reason = REASON_RESERVE_POINT_FLOOR
        else:
            required_micro = static_floor
            reserve_reason = REASON_RESERVE_POINT_FLOOR
        if upper is not None and upper < static_floor:
            # The supplied bound contradicts the policy's own margin floor; a
            # reservation cannot satisfy both, so the policy refuses.
            denial = REASON_UPPER_BELOW_STATIC_FLOOR
            required_micro = 0
            reserve_reason = None
        if (
            denial is None
            and policy.minimum_reserve_micro > required_micro
        ):
            required_micro = policy.minimum_reserve_micro
            reasons.append(REASON_MINIMUM_FLOOR_APPLIED)
        if reserve_reason is not None:
            reasons.append(reserve_reason)

    telemetry_exposure = 0
    if telemetry is not None and REASON_TELEMETRY_STALE not in reasons:
        if telemetry.additional_exposure_micro is not None:
            telemetry_exposure = telemetry.additional_exposure_micro
            reasons.append(REASON_TELEMETRY_ADDED)
        else:
            reasons.append(REASON_NO_TELEMETRY)
    elif telemetry is None:
        reasons.append(REASON_NO_TELEMETRY)
    else:
        # Stale telemetry is never added and never treated as zero exposure.
        limitations.append(
            "Telemetry was observed before the freshness window; it was not "
            "used and its unobserved usage is not assumed to be zero."
        )

    if denial is None:
        required_micro += telemetry_exposure

    available = budget.available_micro
    available_after = available
    if denial is None:
        if available > required_micro:
            reasons.append(REASON_BUDGET_SUFFICIENT)
        elif available == required_micro:
            reasons.append(REASON_BUDGET_EXACT)
        else:
            reasons.append(REASON_BUDGET_INSUFFICIENT)
            denial = REASON_BUDGET_INSUFFICIENT
        if denial is None:
            available_after = available - required_micro

    if denial is not None:
        decision = Decision.DENY
        reasons.append(REASON_POLICY_DENIED)
        reasons.append(denial)
        reasons = [
            code
            for code in reasons
            if code not in (REASON_RESERVE_UPPER_BOUND, REASON_RESERVE_POINT_FLOOR)
        ]
        reservation_micro = 0
    else:
        decision = Decision.APPROVE
        reservation_micro = required_micro
        reasons.append(REASON_POLICY_APPROVED)

    # Keep first occurrence order so reason-code sequences are stable.
    reasons = list(dict.fromkeys(reasons))

    if decision is Decision.DENY:
        under_loss = 0
        over_loss = 0
    else:
        under_loss = (
            _weighted_micro(policy.under_reserve_weight, max(0, upper - reservation_micro))
            if upper is not None
            else 0
        )
        over_loss = (
            _weighted_micro(policy.over_reserve_weight, max(0, reservation_micro - point))
            if point is not None
            else 0
        )

    state_version = (
        calibration.state_version
        if calibration_used and calibration is not None
        else f"estimate/{estimate.method}"
    )

    refs: list[SourceRef] = [
        request.source,
        budget.source,
        *estimate.source_refs,
    ]
    if calibration is not None:
        refs.extend(calibration.source_refs)
    if telemetry is not None:
        refs.extend(telemetry.source_refs)

    audit_reference = _audit_reference(
        request=request,
        budget=budget,
        estimate=estimate,
        decision=decision,
        reservation_micro=reservation_micro,
        state_version=state_version,
        decision_at=decision_at,
    )

    return ReservationProposal(
        decision=decision,
        reason_codes=tuple(reasons),
        reservation_amount_micro=reservation_micro,
        point_cost_estimate_micro=point,
        upper_cost_estimate_micro=upper,
        available_budget_before_micro=available,
        available_budget_after_micro=available_after,
        under_reserve_loss_micro=under_loss,
        over_reserve_loss_micro=over_loss,
        policy_version=POLICY_VERSION,
        state_version=state_version,
        decision_at=decision_at,
        input_refs=_dedupe_refs(tuple(refs)),
        audit_reference=audit_reference,
        telemetry_exposure_micro=telemetry_exposure,
        limitations=tuple(limitations),
    )
