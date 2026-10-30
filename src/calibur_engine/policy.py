"""Public, pure CALIBUR admission-policy entry point.

``compute_reservation`` is the single function application code calls.  It
validates that the supplied evidence is chronologically and dimensionally
consistent, then delegates to :func:`calibur_engine.risk_policy.decide_reservation`.
It returns an advisory proposal only: authorization, idempotency, the atomic
ledger transaction, and persistence remain in the existing Spendctl code.

There is no AWS SDK, network, filesystem, environment, database, or clock access
in this module.  The decision time is always an explicit caller-supplied value.
"""

from __future__ import annotations

from datetime import datetime

from .models import (
    BudgetState,
    CalibrationSnapshot,
    EstimateSnapshot,
    InvalidModelError,
    RequestFeatures,
    ReservationProposal,
    RiskPolicyConfig,
    TelemetrySnapshot,
    _timestamp,
)
from .risk_policy import decide_reservation

__all__ = ["compute_reservation"]


def _aware_decision_time(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise InvalidModelError("decision_at must be timezone-aware")
        return _timestamp(value, field_name="decision_at")
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise InvalidModelError("decision_at is not a valid ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise InvalidModelError("decision_at must be timezone-aware")
        return _timestamp(parsed, field_name="decision_at")
    return _timestamp(value, field_name="decision_at")


def compute_reservation(
    request: RequestFeatures,
    estimate: EstimateSnapshot,
    budget: BudgetState,
    *,
    decision_at: datetime | str,
    calibration: CalibrationSnapshot | None = None,
    telemetry: TelemetrySnapshot | None = None,
    config: RiskPolicyConfig | None = None,
) -> ReservationProposal:
    """Return an advisory reservation proposal for one real request.

    Parameters
    ----------
    request:
        The real request as known at the decision time.
    estimate:
        Point and upper cost estimates observable at the decision time.
    budget:
        The authoritative ledger snapshot supplied by the caller.  Its
        ``available + reserved + spent == cap`` invariant is validated on
        construction; the ledger itself is not written here.
    decision_at:
        Timezone-aware decision timestamp.  No input may be observed later.
    calibration:
        Optional final-label calibration snapshot observable at the decision
        time.  Provisional telemetry is passed separately.
    telemetry:
        Optional observed provisional telemetry.  Only a caller-computed
        ``additional_exposure_micro`` is used; provisional usage is never
        converted to currency here.
    config:
        Fixed policy configuration.  It is not tuned on evaluation data.

    Raises
    ------
    InvalidModelError
        For a malformed input, a team mismatch, a budget period that does not
        match the decision month, or mixed currency/unit evidence.
    FutureInputError
        When any input was observed after ``decision_at``.
    """
    now = _aware_decision_time(decision_at)
    if not isinstance(request, RequestFeatures):
        raise InvalidModelError("request must be a RequestFeatures instance")
    if not isinstance(estimate, EstimateSnapshot):
        raise InvalidModelError("estimate must be an EstimateSnapshot instance")
    if not isinstance(budget, BudgetState):
        raise InvalidModelError("budget must be a BudgetState instance")
    if request.team != budget.team:
        raise InvalidModelError(
            f"request team {request.team!r} does not match budget team {budget.team!r}"
        )
    if budget.period != now.strftime("%Y-%m"):
        raise InvalidModelError(
            f"budget period {budget.period!r} does not match decision month "
            f"{now.strftime('%Y-%m')!r}; supply the ledger snapshot for the decision period"
        )
    return decide_reservation(
        request,
        estimate,
        budget,
        decision_at=now,
        calibration=calibration,
        telemetry=telemetry,
        config=config,
    )
