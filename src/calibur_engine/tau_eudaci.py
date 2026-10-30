"""Auditable delayed-feedback early-update ledger.

This records operational early-warning events in their own idempotent stream.
It intentionally does *not* update conformal scores, alpha, or bounds: only a
final provider-billing label may do that through ``ConformalCalibrator``.
When a final label arrives, it closes the warning's pending state and retains
whether it was an overrun; no extra calibration update is created.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


class TauEudaciError(ValueError): pass


@dataclass(frozen=True)
class EarlyUpdateEvent:
    event_id: str
    job_id: str
    observed_at: datetime
    threshold_micro: int
    provisional_micro: int | None

    def __post_init__(self):
        if not self.event_id or not self.job_id or self.observed_at.tzinfo is None:
            raise TauEudaciError("event_id, job_id, and timezone-aware observed_at are required")
        if self.threshold_micro < 0 or (self.provisional_micro is not None and self.provisional_micro < 0):
            raise TauEudaciError("amounts must be non-negative")


@dataclass(frozen=True)
class FinalBillEvent:
    bill_id: str
    job_id: str
    observed_at: datetime
    actual_micro: int
    revision: int = 0

    def __post_init__(self):
        if not self.bill_id or not self.job_id or self.observed_at.tzinfo is None or self.actual_micro < 0 or self.revision < 0:
            raise TauEudaciError("invalid final billing event")


@dataclass(frozen=True)
class TauJobState:
    job_id: str
    warning_event_ids: tuple[str, ...]
    final_bill_id: str | None
    final_revision: int | None
    final_overrun: bool | None
    pending_final: bool


@dataclass
class TauEudaciLedger:
    """In-memory pure state; applications must persist its serialized events."""

    _events: dict[str, EarlyUpdateEvent] = field(default_factory=dict)
    _bills: dict[tuple[str, str], FinalBillEvent] = field(default_factory=dict)

    def record_early(self, event: EarlyUpdateEvent) -> tuple[TauJobState, bool]:
        prior = self._events.get(event.event_id)
        if prior is not None:
            if prior != event: raise TauEudaciError("conflicting early event identity")
            return self.state(event.job_id), False
        self._events[event.event_id] = event
        return self.state(event.job_id), True

    def record_final(self, event: FinalBillEvent) -> tuple[TauJobState, bool]:
        key = (event.job_id, event.bill_id)
        prior = self._bills.get(key)
        if prior is not None and event.revision < prior.revision:
            raise TauEudaciError("stale final bill revision")
        if prior is not None and event.revision == prior.revision:
            if prior != event: raise TauEudaciError("conflicting final bill identity")
            return self.state(event.job_id), False
        self._bills[key] = event
        return self.state(event.job_id), True

    def state(self, job_id: str) -> TauJobState:
        warnings = tuple(sorted(item.event_id for item in self._events.values() if item.job_id == job_id and item.provisional_micro is not None and item.provisional_micro > item.threshold_micro))
        bills = [item for (candidate, _), item in self._bills.items() if candidate == job_id]
        latest = max(bills, key=lambda item: (item.revision, item.observed_at, item.bill_id), default=None)
        threshold = max((item.threshold_micro for item in self._events.values() if item.job_id == job_id), default=None)
        return TauJobState(job_id, warnings, latest.bill_id if latest else None, latest.revision if latest else None, None if latest is None or threshold is None else latest.actual_micro > threshold, latest is None)
