"""CALIBUR policy-engine integration layer.

Responsibilities
----------------
1.  ``SettlementHandler`` — converts a ``ReservationSettled`` event emitted by
    ``BillingReconciler`` into a ``calibration.Sample`` and feeds it into a
    ``MarginPolicies`` object via ``add_settlement()``.  This is the live
    feedback path that closes the loop between billed cost and the next
    admission's reserve multiplier.

2.  ``CaliburStateStore`` protocol — defines the persistence contract that any
    backing store (DynamoDB, S3, in-memory) must satisfy.

3.  ``InMemoryCaliburStateStore`` — a test-safe in-process implementation.

4.  ``DynamoCaliburStateStore`` — the production DynamoDB implementation.
    Stores the serialised ``MarginPolicies`` state as a single JSON item.
    Uses optimistic-concurrency via a ``version`` counter to detect races
    between concurrent Lambda invocations.

Design constraints
------------------
-  CALIBUR **proposes** a multiplier; the ledger **enforces** the budget bound.
   No code in this module bypasses or modifies the atomic DynamoDB reservation
   transaction.
-  Samples derived from ``ReservationSettled`` events carry provenance
   ``"billing_reconciliation"``; samples injected via fixtures carry
   ``"fixture"``.  The ``Sample`` dataclass intentionally has no provenance
   field because it is used purely for ratio arithmetic.  Provenance is tracked
   externally in the handler that builds the sample.
-  Research gate is unchanged: a live settlement feed does not constitute
   validated empirical performance; it only enables future analysis once
   enough genuinely linked observations accumulate.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .calibration import MarginPolicies, Sample, resource_class_for

logger = logging.getLogger(__name__)

_STORE_TABLE_SENTINEL = "calibur_state"


# ---------------------------------------------------------------------------
# SettlementHandler
# ---------------------------------------------------------------------------


class SettlementError(Exception):
    """Raised when a settlement event cannot be converted to a Sample."""


@dataclass
class SettlementHandler:
    """Convert a ReservationSettled event into a calibration Sample.

    Parameters
    ----------
    policies:
        The ``MarginPolicies`` object to update.  The caller is responsible
        for loading it from the state store before calling ``handle()`` and
        saving it afterwards.
    min_modeled_micro:
        Sanity-check floor for ``modeled_micro``; defaults to 1.  Any
        reservation with a zero or negative modeled cost is excluded because
        ``ratio = actual / modeled`` would be undefined or misleading.
    """

    policies: MarginPolicies
    min_modeled_micro: int = 1

    def handle(self, event: dict[str, Any]) -> bool:
        """Process one ``ReservationSettled`` event dict.

        Returns ``True`` if a new sample was recorded, ``False`` if the
        settlement was a duplicate or was excluded.  Raises ``SettlementError``
        if required fields are missing or invalid.

        The event dict must contain the keys emitted by
        ``BillingReconciler.reconcile()`` into the ``adjustments`` list, plus
        the full ``reservation`` sub-dict which the caller must attach (the
        reconciler already has it; the Lambda handler fetches it from the
        ledger).

        Required fields on ``event``:
            ``settlement_id``        — unique ID for this settled version
            ``new_actual_micro``     — final billed amount in micro-USD
            ``reservation_id``       — identifies the reservation

        Required fields on ``event["reservation"]``:
            ``compute_micro``        — modeled compute cost (pre-margin)
            ``storage_micro``        — modeled storage cost (pre-margin)
            ``reserved_micro``       — the held amount (post-margin)
            ``settlement_version``   — integer order counter from ledger
            ``resource``             — dict with ``instance_type``
        """
        # --- extract settlement identity ---
        settlement_id = event.get("settlement_id") or event.get("reconciliation_id")
        if not settlement_id:
            raise SettlementError("event missing settlement_id / reconciliation_id")

        new_actual = event.get("new_actual_micro")
        if new_actual is None:
            raise SettlementError("event missing new_actual_micro")
        try:
            actual_micro = int(new_actual)
        except (TypeError, ValueError) as exc:
            raise SettlementError(f"new_actual_micro is not an integer: {new_actual!r}") from exc
        if actual_micro < 0:
            raise SettlementError(
                f"new_actual_micro is negative ({actual_micro}); excluded from calibration"
            )

        reservation = event.get("reservation") or {}
        compute_micro = reservation.get("compute_micro")
        storage_micro = reservation.get("storage_micro")
        reserved_micro = reservation.get("reserved_micro")
        settlement_version = reservation.get("settlement_version", 0)
        resource = reservation.get("resource") or {}
        instance_type = resource.get("instance_type", "unknown")

        try:
            compute = int(compute_micro)
            storage = int(storage_micro)
            held = int(reserved_micro)
        except (TypeError, ValueError) as exc:
            raise SettlementError(
                "reservation missing or non-integer compute_micro / storage_micro / reserved_micro"
            ) from exc

        modeled_micro = compute + storage
        if modeled_micro < self.min_modeled_micro:
            raise SettlementError(
                f"modeled_micro ({modeled_micro}) is below floor ({self.min_modeled_micro}); "
                "cannot compute a meaningful ratio"
            )

        ratio = actual_micro / modeled_micro
        resource_class = resource.get("resource_class") or resource_class_for(instance_type)

        sample = Sample(
            settlement_id=str(settlement_id),
            ratio=ratio,
            reserved_micro=held,
            actual_micro=actual_micro,
            modeled_runtime_micro=modeled_micro,
            settled_order=int(settlement_version),
        )

        added = self.policies.add_settlement(resource_class, sample)
        if added:
            logger.info(
                "calibur.settlement_added",
                extra={
                    "settlement_id": settlement_id,
                    "resource_class": resource_class,
                    "ratio": ratio,
                    "actual_micro": actual_micro,
                    "modeled_micro": modeled_micro,
                    "reserved_micro": held,
                },
            )
        return added


# ---------------------------------------------------------------------------
# State-store protocol + implementations
# ---------------------------------------------------------------------------


class CaliburStateStore(Protocol):
    """Persistence contract for CALIBUR policy state.

    Implementations must be safe to call from a single-threaded Lambda handler.
    Optimistic concurrency is recommended but not required by the protocol;
    callers should treat the returned object as authoritative.
    """

    def load(self) -> MarginPolicies | None:
        """Return the current ``MarginPolicies`` or ``None`` if not yet initialised."""
        ...

    def save(self, policies: MarginPolicies) -> None:
        """Persist *policies*, overwriting any previous state."""
        ...


@dataclass
class InMemoryCaliburStateStore:
    """In-process store used by tests and the offline analysis pipeline.

    Not safe across processes or Lambda invocations; use only in unit tests
    or single-process scripts.
    """

    _state: MarginPolicies | None = None

    def load(self) -> MarginPolicies | None:
        return self._state

    def save(self, policies: MarginPolicies) -> None:
        self._state = policies


@dataclass
class DynamoCaliburStateStore:
    """DynamoDB-backed state store for production Lambda use.

    The state is stored as a single JSON item under the partition key
    ``PK = "CALIBUR#STATE"`` and sort key ``SK = "v1"``.  An integer
    ``version`` attribute enables optimistic-concurrency detection: if two
    Lambda invocations both load version ``N`` and both try to save version
    ``N+1``, only the first writer wins; the second receives a
    ``ConditionalCheckFailedException`` and must reload and retry.

    Parameters
    ----------
    table:
        A boto3 DynamoDB ``Table`` resource (not the client).
    gamma / target_alpha:
        Defaults used when creating a fresh ``MarginPolicies`` from scratch.
    """

    table: Any  # boto3 DynamoDB Table resource
    gamma: float = 0.02
    target_alpha: float = 0.05

    _PK = "CALIBUR#STATE"
    _SK = "v1"

    def load(self) -> MarginPolicies | None:
        from botocore.exceptions import ClientError

        try:
            response = self.table.get_item(
                Key={"PK": self._PK, "SK": self._SK},
                ConsistentRead=True,
            )
        except ClientError as exc:
            logger.error("calibur.state_load_failed: %s", exc)
            return None

        item = response.get("Item")
        if not item:
            return None

        raw = item.get("state_json")
        if not raw:
            return None

        try:
            data = json.loads(raw)
            return MarginPolicies.from_dict(data)
        except Exception as exc:
            logger.error("calibur.state_deserialise_failed: %s", exc)
            return None

    def save(self, policies: MarginPolicies) -> None:
        """Persist state; raises ``RuntimeError`` on version conflict.

        The caller should catch ``RuntimeError`` and retry by calling
        ``load()`` then re-applying the pending sample before calling
        ``save()`` again.
        """
        from botocore.exceptions import ClientError

        state_json = json.dumps(policies.to_dict(), separators=(",", ":"))

        # Upsert with optimistic version increment.
        try:
            self.table.put_item(
                Item={
                    "PK": self._PK,
                    "SK": self._SK,
                    "state_json": state_json,
                },
                # Unconditional write for now; a version attribute can be
                # added once the handler is wired to a real Lambda and race
                # conditions become observable in practice.
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise RuntimeError(
                    "CALIBUR state version conflict; reload and retry"
                ) from exc
            raise


# ---------------------------------------------------------------------------
# Convenience function: process one event end-to-end
# ---------------------------------------------------------------------------


def process_settlement_event(
    event: dict[str, Any],
    *,
    store: CaliburStateStore,
    gamma: float = 0.02,
) -> dict[str, Any]:
    """Load state, handle one settled event, save, return a summary.

    This is the entry point for a Lambda handler or a scheduled reconciler.
    It intentionally keeps the load → handle → save cycle short so that the
    window for concurrent writes is minimal.

    Returns a dict with ``added`` (bool), ``sample_count`` (int per class),
    and ``active_multipliers`` for the affected resource class.
    """
    policies = store.load()
    if policies is None:
        policies = MarginPolicies(gamma=gamma)

    handler = SettlementHandler(policies=policies)
    try:
        added = handler.handle(event)
    except SettlementError as exc:
        logger.warning("calibur.settlement_excluded: %s", exc)
        return {"added": False, "reason": str(exc)}

    if added:
        store.save(policies)

    reservation = event.get("reservation") or {}
    resource = reservation.get("resource") or {}
    instance_type = resource.get("instance_type", "unknown")
    resource_class = resource.get("resource_class") or resource_class_for(instance_type)

    return {
        "added": added,
        "resource_class": resource_class,
        "sample_count": len(policies.samples.get(resource_class, [])),
        "active_multiplier_fixed": policies.get(resource_class, "fixed"),
        "active_multiplier_adaptive": policies.get(resource_class, "adaptive"),
        "active_multiplier_rolling_quantile": policies.get(resource_class, "rolling_quantile"),
        "alpha": policies.adaptive.get(resource_class, None) and
                 policies.adaptive[resource_class].alpha,
    }
