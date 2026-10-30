"""API-independent validation, estimation, and atomic budget admission (M4)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ReadTimeoutError,
)

from calibur_engine import (
    BudgetState,
    CalibrationSnapshot,
    EstimateSnapshot,
    RequestFeatures,
    RiskPolicyConfig,
    SourceRef,
    compute_reservation,
)
from calibur_engine.models import Decision, ReservationProposal

from .calibration import MarginPolicies, resource_class_for
from .ledger import (
    AmbiguousLedgerOutcome,
    BudgetClosed,
    BudgetNotFound,
    DuplicateIdempotencyKey,
    InsufficientBudget,
    LedgerTransactionCancelled,
    ReservationConflict,
    ReservationNotFound,
)
from .money import multiply_ceil
from .observability import emit_emf, structured_log
from .pricing import PricingError, PricingProvider, estimate_request
from .validation import Request, validate_request

_TRANSPORT_ERRORS = (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ReadTimeoutError,
    TimeoutError,
    ConnectionError,
)
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class AdmissionError(Exception):
    """Base class for expected M4 request outcomes."""


class UnauthorizedTeam(AdmissionError):
    """The verified identity is not authorized for the requested team."""


class AdmissionConflict(AdmissionError):
    """An idempotency key was reused with different request semantics."""


class AdmissionInsufficientBudget(AdmissionError):
    """The team's open budget cannot cover the required reserve."""

    def __init__(self, message: str, proposal: ReservationProposal | None = None):
        self.proposal = proposal
        super().__init__(message)


class AdmissionPolicyDenied(AdmissionError):
    """CALIBUR rejected a decision-time evidence bundle without a ledger write."""

    def __init__(self, proposal: ReservationProposal):
        self.proposal = proposal
        super().__init__("CALIBUR policy denied admission: " + ", ".join(proposal.reason_codes))


class AdmissionRetryableFailure(AdmissionError):
    """A known non-committed ledger failure that may be retried with the same key."""


class AdmissionPricingUnavailable(AdmissionError):
    """The estimate could not be produced from the configured provider."""


class AdmissionOutcomeUnknown(AdmissionError):
    """Reservation may have committed; reconcile using the same request/key."""

    def __init__(self, reservation_id: str):
        self.reservation_id = reservation_id
        self.state = "PENDING_UNKNOWN"
        self.reconciliation_required = True
        super().__init__(
            f"reservation outcome is unknown for {reservation_id}; retry/reconcile with the same "
            "team, period, idempotency key, and request"
        )


@dataclass(frozen=True)
class VerifiedIdentity:
    """Identity claims supplied by a future verified auth adapter, never request JSON."""

    team: str
    subject: str

    def __post_init__(self):
        if not isinstance(self.team, str) or not self.team.strip():
            raise ValueError("verified identity must include a team")
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise ValueError("verified identity must include a subject")


_VALID_POLICIES = frozenset({"fixed", "rolling_quantile", "adaptive", "split_conformal"})


def resolve_active_policy(raw: str | None) -> str:
    """Return a valid margin policy name, falling back to ``fixed``.

    A typo in ``CALIBUR_ACTIVE_POLICY`` must not take down admission.  The
    fallback is logged with a stable outcome so the misconfiguration is still
    visible in CloudWatch.
    """
    if raw is None or raw.strip() == "":
        return "fixed"
    policy = raw.strip().lower()
    if policy in _VALID_POLICIES:
        return policy
    structured_log(
        "M4",
        "margin_policy",
        "fallback",
        requested=raw[:64],
        active="fixed",
        error_category="unknown_active_policy",
    )
    return "fixed"


def active_policy_from_env() -> str:
    """Resolve ``CALIBUR_ACTIVE_POLICY``; unset keeps the static policy."""
    return resolve_active_policy(os.environ.get("CALIBUR_ACTIVE_POLICY"))


@dataclass(frozen=True)
class AdmissionService:
    ledger: object
    pricing: PricingProvider
    policies: MarginPolicies | None = None
    reserve_multiplier: Decimal = Decimal("1.10")
    # active_policy controls which margin algorithm is actually applied to the
    # reserve decision.  "fixed" remains the code default; the dev stack sets
    # CALIBUR_ACTIVE_POLICY=rolling_quantile.  Below the sample gate
    # (CALIBUR_MIN_SAMPLES, default 2) MarginPolicies.get() falls back to the
    # static 1.10 floor.
    active_policy: str = "fixed"

    def __post_init__(self):
        if self.reserve_multiplier <= 0:
            raise ValueError("reserve_multiplier must be positive")
        if self.active_policy not in _VALID_POLICIES:
            raise ValueError(
                f"active_policy must be one of {sorted(_VALID_POLICIES)}, "
                f"got {self.active_policy!r}"
            )

    def estimate(self, payload: dict, identity: VerifiedIdentity) -> dict:
        request = _authorized_request(payload, identity)
        try:
            return estimate_request(_normalized_payload(payload, request), self.pricing)
        except PricingError as error:
            raise AdmissionPricingUnavailable(str(error)) from error

    def admit(
        self,
        payload: dict,
        *,
        identity: VerifiedIdentity,
        period: str,
        idempotency_key: str,
    ) -> dict:
        """Reserve budget atomically. This method never starts provisioning."""
        started = time.perf_counter()
        result = "error"
        request_id = _request_id(identity.team, identity.subject, idempotency_key)
        try:
            outcome = self._admit(
                payload,
                identity=identity,
                period=period,
                idempotency_key=idempotency_key,
            )
            result = "replay" if outcome.get("replayed") else "admitted"
            return outcome
        except AdmissionOutcomeUnknown:
            result = "pending_unknown"
            raise
        except AdmissionInsufficientBudget as error:
            result = "insufficient_budget"
            self._record_denial(
                request_id,
                identity,
                period,
                idempotency_key,
                "INSUFFICIENT_BUDGET",
                evidence=error.proposal.to_dict() if error.proposal else None,
            )
            raise
        except AdmissionPolicyDenied as error:
            result = "policy_denied"
            self._record_denial(
                request_id,
                identity,
                period,
                idempotency_key,
                "CALIBUR_POLICY_DENIED",
                evidence=error.proposal.to_dict(),
            )
            raise
        except AdmissionConflict:
            result = "conflict"
            raise
        except AdmissionPricingUnavailable:
            result = "pricing_unavailable"
            raise
        except AdmissionRetryableFailure:
            result = "retryable_error"
            raise
        except (TypeError, ValueError):
            result = "invalid_request"
            raise
        except Exception as error:
            result = "unexpected_error"
            structured_log("M5", "admission", "error", error_category="unexpected", error=type(error).__name__, error_message=str(error)[:200], request_id=request_id)
            raise
        finally:
            _emit_admission_emf(result, (time.perf_counter() - started) * 1000)

    def _admit(
        self,
        payload: dict,
        *,
        identity: VerifiedIdentity,
        period: str,
        idempotency_key: str,
    ) -> dict:
        request = _authorized_request(payload, identity)
        _validate_admission_metadata(period, idempotency_key)
        reservation_id = _reservation_id(identity.team, identity.subject, idempotency_key)
        request_id = _request_id(identity.team, identity.subject, idempotency_key)

        # A durable row wins over current pricing. This makes a replay stable even
        # if the provider's live price changes after the first successful reserve.
        try:
            existing = self.ledger.get_reservation(reservation_id)
        except ReservationNotFound:
            existing = None
        except (AmbiguousLedgerOutcome, BotoCoreError, ClientError, *_TRANSPORT_ERRORS) as error:
            raise AdmissionOutcomeUnknown(reservation_id) from error
        if existing is not None:
            _check_existing(existing, request, identity, period, idempotency_key)
            return _result(existing, replayed=True)

        try:
            budget = self.ledger.get_budget(identity.team, period)
        except (BotoCoreError, ClientError, *_TRANSPORT_ERRORS) as error:
            raise AdmissionRetryableFailure(
                "budget lookup failed before any reservation was attempted"
            ) from error
        if not budget:
            raise AdmissionInsufficientBudget("team has no budget for the requested period")
        if budget.get("status") == "CLOSED":
            raise AdmissionInsufficientBudget("team budget is closed")

        try:
            estimate = estimate_request(_normalized_payload(payload, request), self.pricing)
        except PricingError as error:
            raise AdmissionPricingUnavailable(str(error)) from error

        decision_at = datetime.now(UTC)
        multiplier = self._multiplier(resource_class_for(request.instance_type))
        proposal = self._calibur_proposal(
            request=request,
            request_id=request_id,
            budget=budget,
            estimate=estimate,
            decision_at=decision_at,
            multiplier=multiplier,
        )
        if proposal.decision is Decision.DENY:
            if "BUDGET_INSUFFICIENT" in proposal.reason_codes:
                raise AdmissionInsufficientBudget(
                    "team budget cannot cover CALIBUR's required reservation",
                    proposal,
                )
            raise AdmissionPolicyDenied(proposal)
        # The engine's proposal is the requested hold.  The ledger transaction
        # below independently rechecks availability, so a concurrent admission
        # cannot turn this decision-time snapshot into an overspend.
        reserved_micro = proposal.reservation_amount_micro
        policies = self.policies
        shadow_candidates = (
            {
                policy: str(Decimal(str(policies.get(resource_class_for(request.instance_type), policy))))
                for policy in ("fixed", "rolling_quantile", "split_conformal", "adaptive")
            }
            if policies is not None
            else {}
        )
        record = {
            "request_id": request_id,
            "decision_at": proposal.decision_at.isoformat().replace("+00:00", "Z"),
            "policy_version": proposal.policy_version,
            "margin_policy": self.active_policy,
            "calibur_proposal": proposal.to_dict(),
            "currency": "USD",
            "resource": {
                "type": "ec2",
                "instance_type": request.instance_type,
                "ebs_gb": request.ebs_gb,
                "resource_class": resource_class_for(request.instance_type),
            },
            "planned_hours": request.duration_hours,
            "estimate_micro": estimate["total_micro"],
            "compute_micro": estimate["compute_micro"],
            "storage_micro": estimate["storage_micro"],
            "margin": str(multiplier),
            "shadow_margin_candidates": shadow_candidates,
            "price_snapshot_id": estimate["price_snapshot_id"],
            "price_fetched_at": getattr(self.pricing, "price_fetched_at", None)
            or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "price_source": estimate["source"],
            "estimate_breakdown": estimate["breakdown"],
            "region": estimate["region"],
            "purpose": request.purpose,
        }
        try:
            outcome = self.ledger.reserve(
                team=identity.team,
                period=period,
                reservation_id=reservation_id,
                idempotency_key=idempotency_key,
                requester=identity.subject,
                reserved_micro=reserved_micro,
                record=record,
                decision_budget_snapshot=budget,
            )
        except (InsufficientBudget, BudgetNotFound) as error:
            raise AdmissionInsufficientBudget(str(error)) from error
        except (DuplicateIdempotencyKey, ReservationConflict) as error:
            raise AdmissionConflict(str(error)) from error
        except LedgerTransactionCancelled as error:
            emit_emf("Spendctl/Admission", {"TransactionConflicts": (1, "Count")})
            structured_log(
                "M4", "reserve", "error", reservation_id=reservation_id,
                error_category="transaction_cancelled",
            )
            raise AdmissionRetryableFailure(
                "ledger transaction was cancelled without a confirmed reservation; retry with the same key"
            ) from error
        except AmbiguousLedgerOutcome as error:
            # Resolve only through a consistent durable reservation read. If it
            # cannot be confirmed, leave the hold alone and report unknown.
            try:
                existing = self.ledger.get_reservation(reservation_id)
            except (
                ReservationNotFound,
                AmbiguousLedgerOutcome,
                BotoCoreError,
                ClientError,
                *_TRANSPORT_ERRORS,
            ):
                raise AdmissionOutcomeUnknown(reservation_id) from error
            _check_existing(existing, request, identity, period, idempotency_key)
            return _result(existing, replayed=True)
        except BudgetClosed as error:
            raise AdmissionInsufficientBudget(str(error)) from error

        reservation = outcome["reservation"]
        if outcome.get("replayed"):
            return _result(reservation, replayed=True)
        _emit_budget_utilization(self.ledger, identity.team, period)
        print(
            json.dumps(
                {
                    "event": "AdmissionReserved",
                    "reservation_id": reservation_id,
                    "estimate_breakdown": estimate["breakdown"],
                    "price_snapshot_id": estimate["price_snapshot_id"],
                    "shadow_margin_candidates": shadow_candidates,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return {
            "state": "ADMITTED",
            "http_status": 200 if outcome.get("replayed") else 202,
            "reservation_id": reservation["reservation_id"],
            "request_id": reservation["request_id"],
            "reserved_micro": reservation["reserved_micro"],
            "estimate_micro": reservation["estimate_micro"],
            "estimate": estimate,
            "reservation": reservation,
            "replayed": bool(outcome.get("replayed")),
            "provisioning_started": False,
        }

    def _record_denial(
        self,
        request_id: str,
        identity: VerifiedIdentity,
        period: str,
        idempotency_key: str,
        reason: str,
        evidence: dict | None = None,
    ) -> None:
        """Write evidence for a final rejection without altering API semantics."""
        if not _PERIOD_RE.fullmatch(period) or not idempotency_key:
            return
        self.ledger.record_admission_denial(
            request_id=request_id,
            team=identity.team,
            requester=identity.subject,
            period=period,
            idempotency_key=idempotency_key,
            reason=reason,
            decision_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            evidence=evidence,
        )

    def reconcile(
        self,
        payload: dict,
        *,
        identity: VerifiedIdentity,
        period: str,
        idempotency_key: str,
    ) -> dict:
        """Idempotent reconciliation path: retry the exact admission identity."""
        return self.admit(
            payload,
            identity=identity,
            period=period,
            idempotency_key=idempotency_key,
        )

    def _multiplier(self, resource_class: str = "ec2:t3") -> Decimal:
        configured = self.reserve_multiplier
        if self.policies is not None:
            configured = Decimal(str(self.policies.get(resource_class, self.active_policy)))
        if not Decimal("1.0") <= configured <= Decimal("2.0"):
            raise ValueError("reserve multiplier must be between 1.0 and 2.0")
        return configured

    def _calibur_proposal(
        self,
        *,
        request: Request,
        request_id: str,
        budget: dict,
        estimate: dict,
        decision_at: datetime,
        multiplier: Decimal,
    ) -> ReservationProposal:
        """Build a decision-time-only CALIBUR input bundle from authoritative inputs."""
        observed = decision_at
        resource_class = resource_class_for(request.instance_type)
        request_ref = SourceRef(request_id, observed, "spendctl/admission", "admission_request")
        budget_ref = SourceRef(
            f"budget:{request.team}:{budget.get('period', '')}:{budget.get('version', '')}",
            observed,
            "spendctl/ledger",
            "consistent_budget_snapshot",
        )
        estimate_ref = SourceRef(
            str(estimate["price_snapshot_id"]),
            observed,
            "spendctl/pricing",
            "decision_time_price_snapshot",
        )
        policy_ref = SourceRef(
            f"margin:{self.active_policy}:{resource_class}",
            observed,
            "spendctl/calibration",
            "persisted_margin_policy",
        )
        point = int(estimate["total_micro"])
        upper = multiply_ceil(point, multiplier)
        calibration = self._calibration_snapshot(
            resource_class=resource_class,
            point_micro=point,
            upper_micro=upper,
            observed=observed,
            source=policy_ref,
        )
        return compute_reservation(
            RequestFeatures(
                request_id=request_id,
                team=request.team,
                requested_at=observed,
                instance_type=request.instance_type,
                duration_hours=Decimal(request.duration_hours),
                ebs_gb=request.ebs_gb,
                region=str(estimate["region"]),
                source=request_ref,
            ),
            EstimateSnapshot(
                point_cost_micro=point,
                upper_cost_micro=upper,
                method=f"pricing/{estimate['source']}",
                as_of=observed,
                source_refs=(estimate_ref, policy_ref),
                limitations=("Price estimate is derived, not provider billing.",),
            ),
            BudgetState(
                team=request.team,
                period=str(budget["period"]),
                cap_micro=int(budget["budget_micro"]),
                available_micro=int(budget["available_micro"]),
                reserved_micro=int(budget["reserved_micro"]),
                spent_micro=int(budget["spent_micro"]),
                as_of=observed,
                source=budget_ref,
                status=str(budget.get("status", "OPEN")),
            ),
            decision_at=decision_at,
            calibration=calibration,
            config=RiskPolicyConfig(static_margin=multiplier),
        )

    def _calibration_snapshot(
        self,
        *,
        resource_class: str,
        point_micro: int,
        upper_micro: int,
        observed: datetime,
        source: SourceRef,
    ) -> CalibrationSnapshot | None:
        """Freeze only final-label policy state available at the decision.

        Cold start deliberately returns ``None``.  The risk engine then emits
        ``NO_CALIBRATION`` and keeps the configured static policy visible in
        the durable proposal rather than pretending an empirical calibration
        exists.  Provisional telemetry is never converted to a cost label here.
        """
        if self.policies is None:
            return None
        samples = self.policies.samples.get(resource_class, [])
        if not samples:
            return None
        adaptive = self.policies.adaptive.get(resource_class)
        alpha = Decimal(str(adaptive.alpha if adaptive is not None else self.policies.target_alpha))
        return CalibrationSnapshot(
            upper_cost_micro=upper_micro,
            alpha=alpha,
            score_count=len(samples),
            as_of=observed,
            source_refs=(source,),
            state_version=f"margin/{self.active_policy}/{resource_class}/samples={len(samples)}",
        )


def _authorized_request(payload: dict, identity: VerifiedIdentity) -> Request:
    request = validate_request(payload)
    if request.team != identity.team:
        raise UnauthorizedTeam("request team does not match the verified identity team")
    return request


def _normalized_payload(payload: dict, request: Request) -> dict:
    # Keep validation as the source of contract policy while ensuring provider
    # input has the authorized team and no caller-controlled region/SKU fields.
    return {
        "team": request.team,
        "resource": {
            "type": "ec2",
            "instance_type": request.instance_type,
            "ebs_gb": request.ebs_gb,
        },
        "duration_hours": request.duration_hours,
        "purpose": request.purpose,
    }


def _validate_admission_metadata(period: str, idempotency_key: str) -> None:
    if not isinstance(period, str) or not _PERIOD_RE.fullmatch(period):
        raise ValueError("period must use YYYY-MM format")
    if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 256:
        raise ValueError("idempotency_key must contain 1 to 256 characters")


def _reservation_id(team: str, subject: str, key: str) -> str:
    material = json.dumps([team, subject, key], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(material.encode()).hexdigest()
    return f"rsv_{digest[:40]}"


def _request_id(team: str, subject: str, key: str) -> str:
    """Stable admission-attempt identity, including retries of the same key."""
    material = json.dumps([team, subject, key], ensure_ascii=False, separators=(",", ":"))
    return f"req_{hashlib.sha256(material.encode()).hexdigest()[:40]}"


def _emit_admission_emf(result: str, latency_ms: float) -> None:
    """Write Embedded Metric Format to Lambda stdout for CloudWatch extraction."""
    outcome = (
        "admitted"
        if result in {"admitted", "replay"}
        else "denied"
        if result in {"insufficient_budget", "invalid_request", "conflict"}
        else "error"
    )
    metrics = [
        {"Name": "AdmissionDecision", "Unit": "Count"},
        {"Name": "AdmissionLatencyMs", "Unit": "Milliseconds"},
    ]
    values = {"AdmissionDecision": 1, "AdmissionLatencyMs": latency_ms}
    if result == "replay":
        metrics.append({"Name": "IdempotentReplays", "Unit": "Count"})
        values["IdempotentReplays"] = 1
    if result == "insufficient_budget":
        metrics.append({"Name": "InsufficientBudgetDenials", "Unit": "Count"})
        values["InsufficientBudgetDenials"] = 1
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": "Spendctl/Admission",
                            "Dimensions": [["result"]],
                            "Metrics": metrics,
                        }
                    ],
                },
                "result": outcome,
                **values,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _emit_budget_utilization(ledger, team: str, period: str) -> None:
    """Publish a dimensionless team utilization sample for 50/80/100% alarms."""
    try:
        budget = ledger.get_budget(team, period)
    except (BotoCoreError, ClientError, *_TRANSPORT_ERRORS):
        structured_log("M11", "budget_utilization", "error", error_category="budget_read_failed")
        return
    if not budget or not budget.get("budget_micro"):
        return
    used = int(budget.get("reserved_micro", 0)) + int(budget.get("spent_micro", 0))
    percent = used * 100.0 / int(budget["budget_micro"])
    emit_emf("Spendctl/Budget", {"BudgetUtilizationPercent": (percent, "Percent")})


def _check_existing(
    reservation: dict, request: Request, identity: VerifiedIdentity, period: str, key: str
) -> None:
    resource = reservation.get("resource", {})
    if (
        reservation.get("team") != identity.team
        or reservation.get("requester") != identity.subject
        or reservation.get("budget_period") != period
        or reservation.get("idempotency_key") != key
        or resource.get("type") != "ec2"
        or resource.get("instance_type") != request.instance_type
        or resource.get("ebs_gb") != request.ebs_gb
        or reservation.get("planned_hours") != request.duration_hours
        or reservation.get("purpose", "") != request.purpose
    ):
        raise AdmissionConflict("idempotency key is already bound to a different admission")


def _result(reservation: dict, *, replayed: bool) -> dict:
    return {
        "state": "ADMITTED",
        "http_status": 200,
        "reservation_id": reservation["reservation_id"],
        "request_id": reservation.get("request_id"),
        "reserved_micro": reservation["reserved_micro"],
        "estimate_micro": reservation.get("estimate_micro"),
        "estimate": {
            "compute_micro": reservation.get("compute_micro"),
            "storage_micro": reservation.get("storage_micro"),
            "total_micro": reservation.get("estimate_micro"),
            "breakdown": reservation.get("estimate_breakdown"),
            "region": reservation.get("region"),
            "price_snapshot_id": reservation.get("price_snapshot_id"),
            "source": reservation.get("price_source"),
        },
        "reservation": reservation,
        "replayed": replayed,
        "provisioning_started": False,
    }
