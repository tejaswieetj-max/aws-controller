"""Margin policies. Ratios are actual cost / modeled cost for observed runtime.

Policy selection
----------------
``CALIBUR_ACTIVE_POLICY`` selects ``fixed``, ``rolling_quantile``, ``adaptive``
or ``split_conformal``; ``spendctl.admission.active_policy_from_env()`` resolves
it and falls back to ``fixed`` on an unknown value.  ``MarginPolicies.get()``
returns the dynamic multiplier only once the class has at least
``CALIBUR_MIN_SAMPLES`` settled ratios (default 2); below the gate it returns
the static 1.10 floor.  ``CALIBUR_MIN_MULTIPLIER`` (default 1.0) and
``CALIBUR_MAX_MULTIPLIER`` (default 2.0) clamp the result.  ``split_conformal``
additionally requires enough CALIBRATION-partition rows for its 95% order
statistic (``k <= n``), which is about 19 rows.

Persistence contract
--------------------
``MarginPolicies.to_dict()`` / ``MarginPolicies.from_dict()`` produce a
JSON-serialisable snapshot so state can round-trip through DynamoDB, S3, or
any key-value store between Lambda invocations.  The format is intentionally
stable: new keys may be added in future versions but existing keys are never
renamed.

Resource-class convention
--------------------------
``resource_class_for(instance_type)`` returns the canonical key used by all
callers, e.g. ``"ec2:t3"`` for ``"t3.micro"`` or ``"t3.small"``.  Using
a shared family key pools samples across instance sizes within a family,
which is appropriate for the small number of supported instance types.
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from math import ceil
from typing import Any

FIXED = 1.10
DEFAULT_MIN_SAMPLES = 2
DEFAULT_MIN_MULTIPLIER = 1.0
DEFAULT_MAX_MULTIPLIER = 2.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from error


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number, got {raw!r}") from error


def resource_class_for(instance_type: str) -> str:
    """Return the canonical CALIBUR resource-class key for *instance_type*.

    Examples::

        resource_class_for("t3.micro")  -> "ec2:t3"
        resource_class_for("t3.small")  -> "ec2:t3"
        resource_class_for("m5.xlarge") -> "ec2:m5"
    """
    family = instance_type.split(".")[0] if instance_type else "unknown"
    return f"ec2:{family}"


def quantile(values: Iterable[float], level: float) -> float:
    xs = sorted(values)
    if not xs:
        return FIXED
    # Nearest-rank empirical quantile, rank clamped to [1,n].
    return xs[max(0, min(len(xs) - 1, ceil(level * len(xs)) - 1))]


@dataclass
class Sample:
    settlement_id: str
    ratio: float
    reserved_micro: int
    actual_micro: int
    modeled_runtime_micro: int
    settled_order: int


@dataclass
class AdaptiveState:
    alpha: float = 0.05
    samples: list[Sample] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)


class MarginPolicies:
    def __init__(
        self,
        gamma: float = 0.02,
        target_alpha: float = 0.05,
        *,
        min_samples: int | None = None,
        min_multiplier: float | None = None,
        max_multiplier: float | None = None,
    ):
        if not 0.01 <= gamma <= 0.05:
            raise ValueError("gamma must be in [0.01, 0.05]")
        if target_alpha != 0.05:
            raise ValueError("v1 target alpha is fixed at 0.05")
        self.gamma, self.target_alpha = gamma, target_alpha
        self.min_samples = (
            _env_int("CALIBUR_MIN_SAMPLES", DEFAULT_MIN_SAMPLES)
            if min_samples is None
            else int(min_samples)
        )
        if self.min_samples < 1:
            raise ValueError("min_samples must be at least 1")
        self.min_multiplier = (
            _env_float("CALIBUR_MIN_MULTIPLIER", DEFAULT_MIN_MULTIPLIER)
            if min_multiplier is None
            else float(min_multiplier)
        )
        self.max_multiplier = (
            _env_float("CALIBUR_MAX_MULTIPLIER", DEFAULT_MAX_MULTIPLIER)
            if max_multiplier is None
            else float(max_multiplier)
        )
        if not 1.0 <= self.min_multiplier <= self.max_multiplier <= 2.0:
            raise ValueError(
                "multiplier bounds must satisfy 1.0 <= min <= max <= 2.0"
            )
        self.samples: dict[str, list[Sample]] = {}
        self.adaptive: dict[str, AdaptiveState] = {}
        self.split_calibration: dict[str, list[float]] = {}

    def set_split_calibration(self, resource_class: str, ratios: Iterable[float]) -> None:
        """Freeze an independent calibration partition for split conformal."""
        xs = sorted(float(x) for x in ratios if float(x) > 0)
        self.split_calibration[resource_class] = xs

    def add_settlement(self, resource_class: str, sample: Sample) -> bool:
        """Deduplicate by settlement id; stable-sort by billing order then id.

        Delayed arrivals are inserted in deterministic order. We replay the full
        alpha sequence from the initial state, so arrival order cannot change the
        result. A correction must use a new settlement version/id.
        """
        state = self.adaptive.setdefault(resource_class, AdaptiveState())
        if sample.settlement_id in state.seen:
            return False
        state.seen.add(sample.settlement_id)
        values = self.samples.setdefault(resource_class, [])
        values.append(sample)
        values.sort(key=lambda s: (s.settled_order, s.settlement_id))
        self._replay(resource_class)
        return True

    def _replay(self, resource_class: str) -> None:
        state = self.adaptive[resource_class]
        alpha = self.target_alpha
        for s in self.samples[resource_class]:
            miss = 1 if s.actual_micro > s.reserved_micro else 0
            alpha = min(0.5, max(0.001, alpha + self.gamma * (self.target_alpha - miss)))
        state.alpha = alpha

    def _bounded(self, value: float) -> float:
        """Clamp a margin to the configured floor/ceiling.

        The floor defaults to 1.0 (never reserve below the point estimate);
        operators can raise it with ``CALIBUR_MIN_MULTIPLIER``.
        """
        return min(self.max_multiplier, max(self.min_multiplier, value))

    def get(self, resource_class: str, policy: str = "fixed") -> float:
        xs = [s.ratio for s in self.samples.get(resource_class, []) if s.ratio > 0]
        if policy == "fixed":
            return self._bounded(FIXED)
        if policy == "rolling_quantile":
            if len(xs) < self.min_samples:
                return self._bounded(FIXED)
            return self._bounded(quantile(xs[-50:], 0.95))
        if policy == "adaptive":
            if len(xs) < self.min_samples:
                return self._bounded(FIXED)
            alpha = self.adaptive.setdefault(resource_class, AdaptiveState()).alpha
            return self._bounded(quantile(xs, 1 - alpha))
        if policy == "split_conformal":
            # One-sided split-conformal order statistic. If k=n+1, no finite
            # empirical bound is available, so report the fixed cold-start rule.
            cal = self.split_calibration.get(resource_class, [])
            k = ceil((len(cal) + 1) * (1 - self.target_alpha))
            if not cal or k > len(cal):
                return self._bounded(FIXED)
            return self._bounded(cal[k - 1])
        raise ValueError(f"unknown policy {policy}")

    def summary(self, resource_class: str, policy: str = "adaptive") -> dict:
        xs = sorted(s.ratio for s in self.samples.get(resource_class, []))
        from statistics import mean

        return {
            "resource_class": resource_class,
            "sample_count": len(xs),
            "min_samples": self.min_samples,
            "sample_gate_met": len(xs) >= self.min_samples,
            "min_multiplier": self.min_multiplier,
            "max_multiplier": self.max_multiplier,
            "mean_ratio": mean(xs) if xs else None,
            "p95_ratio": quantile(xs, 0.95) if xs else None,
            "observed_coverage": sum(x <= self.get(resource_class, policy) for x in xs) / len(xs)
            if xs
            else None,
            "active_multiplier": self.get(resource_class, policy),
            "alpha": self.adaptive.setdefault(resource_class, AdaptiveState()).alpha,
            "limitations": [
                "synthetic/local sample status must be reported",
                "adaptive heuristic has no unconditional coverage guarantee",
            ],
        }

    # ------------------------------------------------------------------
    # Persistence contract
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise all state to a JSON-safe dict.

        The format is stable: callers may add new top-level keys in their
        own wrappers, but should not mutate keys produced here.
        """
        samples_out: dict[str, list[dict]] = {}
        for rc, slist in self.samples.items():
            samples_out[rc] = [
                {
                    "settlement_id": s.settlement_id,
                    "ratio": s.ratio,
                    "reserved_micro": s.reserved_micro,
                    "actual_micro": s.actual_micro,
                    "modeled_runtime_micro": s.modeled_runtime_micro,
                    "settled_order": s.settled_order,
                }
                for s in slist
            ]

        split_out: dict[str, list[float]] = {
            rc: list(ratios) for rc, ratios in self.split_calibration.items()
        }

        return {
            "version": 1,
            "gamma": self.gamma,
            "target_alpha": self.target_alpha,
            "samples": samples_out,
            "alpha_by_class": {
                rc: state.alpha for rc, state in self.adaptive.items()
            },
            "split_calibration": split_out,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarginPolicies":
        """Reconstruct a ``MarginPolicies`` from a previously serialised dict.

        Unknown keys are silently ignored so that future schema additions do
        not break older deserialisation code.  The alpha value stored in
        ``alpha_by_class`` is *applied directly*; the sample list is NOT
        re-replayed on load (the stored alpha already reflects all samples).
        """
        if not isinstance(data, dict):
            raise TypeError("calibration state must be a JSON object")
        version = data.get("version", 1)
        if version != 1:
            raise ValueError(f"unsupported calibration state version: {version}")

        obj = cls(
            gamma=float(data.get("gamma", 0.02)),
            target_alpha=float(data.get("target_alpha", 0.05)),
        )

        for rc, slist in data.get("samples", {}).items():
            parsed: list[Sample] = []
            seen: set[str] = set()
            for row in slist:
                s = Sample(
                    settlement_id=str(row["settlement_id"]),
                    ratio=float(row["ratio"]),
                    reserved_micro=int(row["reserved_micro"]),
                    actual_micro=int(row["actual_micro"]),
                    modeled_runtime_micro=int(row["modeled_runtime_micro"]),
                    settled_order=int(row["settled_order"]),
                )
                parsed.append(s)
                seen.add(s.settlement_id)
            # Preserve documented sort order.
            parsed.sort(key=lambda s: (s.settled_order, s.settlement_id))
            obj.samples[rc] = parsed
            obj.adaptive[rc] = AdaptiveState(
                alpha=float(data.get("alpha_by_class", {}).get(rc, obj.target_alpha)),
                samples=parsed,
                seen=seen,
            )

        for rc, ratios in data.get("split_calibration", {}).items():
            obj.split_calibration[rc] = sorted(float(x) for x in ratios if float(x) > 0)

        return obj
