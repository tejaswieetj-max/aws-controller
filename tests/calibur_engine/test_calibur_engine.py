"""Tests for the CALIBUR policy-engine integration layer.

All tests are isolated in-process; no DynamoDB, AWS, or external I/O.
Fixtures are labelled synthetic and never contribute to empirical results.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from spendctl.calibration import (
    FIXED,
    MarginPolicies,
    Sample,
    resource_class_for,
)
from spendctl.calibur import (
    DynamoCaliburStateStore,
    InMemoryCaliburStateStore,
    SettlementError,
    SettlementHandler,
    process_settlement_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample(n: int, ratio: float = 1.05, *, actual_over_reserved: bool = False) -> Sample:
    """Build a synthetic Sample.  Label: SYNTHETIC_FIXTURE."""
    modeled = 1_000_000
    actual = int(modeled * ratio)
    reserved = int(modeled * 1.10)
    if actual_over_reserved:
        reserved = actual - 1  # force a miss
    return Sample(
        settlement_id=f"fixture-{n}",
        ratio=ratio,
        reserved_micro=reserved,
        actual_micro=actual,
        modeled_runtime_micro=modeled,
        settled_order=n,
    )


def _event(
    n: int,
    *,
    actual_micro: int = 1_050_000,
    compute_micro: int = 900_000,
    storage_micro: int = 100_000,
    reserved_micro: int = 1_100_000,
    instance_type: str = "t3.micro",
    settlement_version: int | None = None,
) -> dict:
    """Build a synthetic ReservationSettled event dict."""
    return {
        "settlement_id": f"settle-{n}",
        "new_actual_micro": actual_micro,
        "reservation_id": f"rsv-{n}",
        "reservation": {
            "compute_micro": compute_micro,
            "storage_micro": storage_micro,
            "reserved_micro": reserved_micro,
            "settlement_version": settlement_version if settlement_version is not None else n,
            "resource": {
                "type": "ec2",
                "instance_type": instance_type,
                "resource_class": resource_class_for(instance_type),
            },
        },
    }


# ---------------------------------------------------------------------------
# 1. resource_class_for
# ---------------------------------------------------------------------------

class TestResourceClassFor:
    def test_t3_micro_gives_ec2_t3(self):
        assert resource_class_for("t3.micro") == "ec2:t3"

    def test_t3_small_gives_ec2_t3(self):
        assert resource_class_for("t3.small") == "ec2:t3"

    def test_m5_xlarge_gives_ec2_m5(self):
        assert resource_class_for("m5.xlarge") == "ec2:m5"

    def test_empty_string_does_not_raise(self):
        rc = resource_class_for("")
        assert rc.startswith("ec2:")


# ---------------------------------------------------------------------------
# 2. MarginPolicies.to_dict / from_dict round-trip
# ---------------------------------------------------------------------------

class TestMarginPoliciesPersistence:
    def test_empty_state_round_trips(self):
        p = MarginPolicies()
        d = p.to_dict()
        p2 = MarginPolicies.from_dict(d)
        assert p2.gamma == p.gamma
        assert p2.target_alpha == p.target_alpha
        assert p2.samples == {}

    def test_samples_survive_round_trip(self):
        p = MarginPolicies()
        for i in range(5):
            p.add_settlement("ec2:t3", _sample(i, ratio=1.05 + i * 0.01))
        d = p.to_dict()
        p2 = MarginPolicies.from_dict(d)
        assert len(p2.samples["ec2:t3"]) == 5
        ratios_orig = [s.ratio for s in p.samples["ec2:t3"]]
        ratios_loaded = [s.ratio for s in p2.samples["ec2:t3"]]
        assert ratios_orig == ratios_loaded

    def test_alpha_survives_round_trip(self):
        p = MarginPolicies(gamma=0.02)
        # Feed 12 samples, some misses, so alpha drifts from 0.05
        for i in range(12):
            p.add_settlement("ec2:t3", _sample(i, actual_over_reserved=(i % 3 == 0)))
        alpha_before = p.adaptive["ec2:t3"].alpha
        assert alpha_before != 0.05  # alpha must have moved

        p2 = MarginPolicies.from_dict(p.to_dict())
        assert abs(p2.adaptive["ec2:t3"].alpha - alpha_before) < 1e-9

    def test_split_calibration_survives_round_trip(self):
        p = MarginPolicies()
        p.set_split_calibration("ec2:t3", [1.01, 1.05, 1.08, 1.12, 1.20])
        p2 = MarginPolicies.from_dict(p.to_dict())
        assert p2.split_calibration["ec2:t3"] == sorted([1.01, 1.05, 1.08, 1.12, 1.20])

    def test_version_1_is_accepted(self):
        p = MarginPolicies()
        d = p.to_dict()
        assert d["version"] == 1
        MarginPolicies.from_dict(d)  # must not raise

    def test_unsupported_version_raises(self):
        p = MarginPolicies()
        d = p.to_dict()
        d["version"] = 99
        with pytest.raises(ValueError, match="unsupported calibration state version"):
            MarginPolicies.from_dict(d)

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="JSON object"):
            MarginPolicies.from_dict([1, 2, 3])

    def test_to_dict_is_json_serialisable(self):
        p = MarginPolicies()
        for i in range(5):
            p.add_settlement("ec2:t3", _sample(i))
        # Must not raise
        json.dumps(p.to_dict())

    def test_dedup_ids_survive_round_trip(self):
        p = MarginPolicies()
        p.add_settlement("ec2:t3", _sample(0))
        p2 = MarginPolicies.from_dict(p.to_dict())
        # After reload, the same settlement_id must still be treated as duplicate
        added = p2.add_settlement("ec2:t3", _sample(0))
        assert added is False


# ---------------------------------------------------------------------------
# 3. SettlementHandler — happy path
# ---------------------------------------------------------------------------

class TestSettlementHandlerHappyPath:
    def test_adds_sample_to_policies(self):
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        added = h.handle(_event(1))
        assert added is True
        assert len(p.samples["ec2:t3"]) == 1

    def test_ratio_computed_from_actual_and_modeled(self):
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        h.handle(_event(1, actual_micro=1_100_000, compute_micro=900_000, storage_micro=100_000))
        s = p.samples["ec2:t3"][0]
        # ratio = 1_100_000 / (900_000 + 100_000) = 1.10
        assert abs(s.ratio - 1.10) < 1e-9

    def test_duplicate_event_returns_false(self):
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        h.handle(_event(1))
        added = h.handle(_event(1))  # same settlement_id
        assert added is False
        assert len(p.samples["ec2:t3"]) == 1

    def test_uses_resource_class_from_event(self):
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        h.handle(_event(1, instance_type="t3.small"))
        assert "ec2:t3" in p.samples

    def test_settled_order_from_settlement_version(self):
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        h.handle(_event(1, settlement_version=42))
        s = p.samples["ec2:t3"][0]
        assert s.settled_order == 42

    def test_zero_actual_is_accepted(self):
        """Zero actual (full credit) is a valid billing outcome."""
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        added = h.handle(_event(1, actual_micro=0))
        assert added is True
        s = p.samples["ec2:t3"][0]
        assert s.actual_micro == 0
        assert s.ratio == 0.0


# ---------------------------------------------------------------------------
# 4. SettlementHandler — exclusion and error cases
# ---------------------------------------------------------------------------

class TestSettlementHandlerExclusions:
    def test_missing_settlement_id_raises(self):
        h = SettlementHandler(policies=MarginPolicies())
        ev = _event(1)
        del ev["settlement_id"]
        with pytest.raises(SettlementError, match="missing settlement_id"):
            h.handle(ev)

    def test_missing_new_actual_raises(self):
        h = SettlementHandler(policies=MarginPolicies())
        ev = _event(1)
        del ev["new_actual_micro"]
        with pytest.raises(SettlementError, match="missing new_actual_micro"):
            h.handle(ev)

    def test_negative_actual_raises(self):
        h = SettlementHandler(policies=MarginPolicies())
        with pytest.raises(SettlementError, match="negative"):
            h.handle(_event(1, actual_micro=-1))

    def test_zero_modeled_micro_raises(self):
        h = SettlementHandler(policies=MarginPolicies())
        ev = _event(1, compute_micro=0, storage_micro=0)
        with pytest.raises(SettlementError, match="modeled_micro"):
            h.handle(ev)

    def test_non_integer_actual_raises(self):
        h = SettlementHandler(policies=MarginPolicies())
        ev = _event(1)
        ev["new_actual_micro"] = "not-a-number"
        with pytest.raises(SettlementError):
            h.handle(ev)

    def test_missing_reservation_fields_raises(self):
        h = SettlementHandler(policies=MarginPolicies())
        ev = _event(1)
        ev["reservation"]["compute_micro"] = None
        with pytest.raises(SettlementError):
            h.handle(ev)

    def test_reconciliation_id_used_when_settlement_id_absent(self):
        """billing.py emits reconciliation_id; handler must accept it."""
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        ev = _event(1)
        ev["reconciliation_id"] = ev.pop("settlement_id")
        added = h.handle(ev)
        assert added is True


# ---------------------------------------------------------------------------
# 5. Feedback loop: settlements change the active multiplier
# ---------------------------------------------------------------------------

class TestFeedbackLoop:
    def test_below_sample_gate_returns_fixed(self):
        """Fewer than 10 samples must keep FIXED regardless of policy."""
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        for i in range(9):
            h.handle(_event(i, actual_micro=950_000))  # ratio ≈ 0.95 (low)
        # 9 samples — adaptive still falls back to FIXED
        assert p.get("ec2:t3", "adaptive") == FIXED

    def test_at_sample_gate_adaptive_activates(self):
        """Exactly 10 samples must allow adaptive to depart from FIXED."""
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        # ratios all 0.95 → Q(1-alpha) of [0.95]*10 < 1.10
        for i in range(10):
            h.handle(_event(i, actual_micro=950_000))
        multiplier = p.get("ec2:t3", "adaptive")
        # Must be < FIXED because all ratios are well below 1.10
        assert multiplier < FIXED

    def test_alpha_decreases_after_misses(self):
        """Repeated misses (actual > reserved) must push alpha down."""
        p = MarginPolicies(gamma=0.02)
        h = SettlementHandler(policies=p)
        # Feed 10 misses: actual >> reserved
        for i in range(10):
            h.handle(_event(
                i,
                actual_micro=2_000_000,  # 2× modeled
                reserved_micro=1_100_000,  # miss every time
            ))
        alpha_after = p.adaptive["ec2:t3"].alpha
        assert alpha_after < 0.05  # more misses → lower alpha → higher multiplier

    def test_alpha_increases_after_hits(self):
        """Consistent coverage (actual < reserved) pushes alpha up toward target."""
        p = MarginPolicies(gamma=0.02)
        h = SettlementHandler(policies=p)
        for i in range(10):
            h.handle(_event(
                i,
                actual_micro=500_000,   # well below reserved
                reserved_micro=1_100_000,
            ))
        alpha_after = p.adaptive["ec2:t3"].alpha
        assert alpha_after > 0.05  # all hits → alpha drifts up

    def test_multiplier_increases_with_persistent_misses(self):
        """After many misses the adaptive multiplier must be >= FIXED."""
        p = MarginPolicies(gamma=0.02)
        h = SettlementHandler(policies=p)
        for i in range(30):
            h.handle(_event(
                i,
                actual_micro=1_600_000,  # ratio 1.6
                reserved_micro=1_100_000,
            ))
        mult = p.get("ec2:t3", "adaptive")
        assert mult >= FIXED

    def test_out_of_order_arrivals_are_stable(self):
        """Order of handle() calls must not change the final multiplier."""
        def run_in_order(order):
            p = MarginPolicies(gamma=0.02)
            h = SettlementHandler(policies=p)
            for i in order:
                h.handle(_event(i, actual_micro=1_050_000))
            return p.get("ec2:t3", "adaptive")

        forward = run_in_order(range(15))
        backward = run_in_order(reversed(range(15)))
        assert abs(forward - backward) < 1e-9


# ---------------------------------------------------------------------------
# 6. InMemoryCaliburStateStore
# ---------------------------------------------------------------------------

class TestInMemoryStateStore:
    def test_load_from_empty_store_returns_none(self):
        store = InMemoryCaliburStateStore()
        assert store.load() is None

    def test_save_then_load_returns_same_policies(self):
        store = InMemoryCaliburStateStore()
        p = MarginPolicies()
        p.add_settlement("ec2:t3", _sample(0))
        store.save(p)
        loaded = store.load()
        assert loaded is p  # in-memory: same object

    def test_overwrite_replaces_previous(self):
        store = InMemoryCaliburStateStore()
        p1 = MarginPolicies()
        p2 = MarginPolicies(gamma=0.03)
        store.save(p1)
        store.save(p2)
        assert store.load() is p2


# ---------------------------------------------------------------------------
# 7. process_settlement_event convenience function
# ---------------------------------------------------------------------------

class TestProcessSettlementEvent:
    def test_returns_added_true_on_new_event(self):
        store = InMemoryCaliburStateStore()
        result = process_settlement_event(_event(1), store=store)
        assert result["added"] is True

    def test_returns_added_false_on_duplicate(self):
        store = InMemoryCaliburStateStore()
        process_settlement_event(_event(1), store=store)
        result = process_settlement_event(_event(1), store=store)
        assert result["added"] is False

    def test_returns_sample_count(self):
        store = InMemoryCaliburStateStore()
        for i in range(5):
            process_settlement_event(_event(i), store=store)
        result = process_settlement_event(_event(5), store=store)
        assert result["sample_count"] == 6

    def test_creates_fresh_policies_when_store_empty(self):
        store = InMemoryCaliburStateStore()
        result = process_settlement_event(_event(1), store=store)
        assert result["added"] is True
        assert store.load() is not None

    def test_excludes_invalid_event_gracefully(self):
        store = InMemoryCaliburStateStore()
        ev = _event(1)
        del ev["settlement_id"]
        result = process_settlement_event(ev, store=store)
        assert result["added"] is False
        assert "reason" in result

    def test_multipliers_present_in_result(self):
        store = InMemoryCaliburStateStore()
        for i in range(10):
            process_settlement_event(_event(i), store=store)
        result = process_settlement_event(_event(10), store=store)
        assert "active_multiplier_fixed" in result
        assert "active_multiplier_adaptive" in result
        assert result["active_multiplier_fixed"] == FIXED

    def test_state_persisted_after_new_event(self):
        store = InMemoryCaliburStateStore()
        process_settlement_event(_event(1), store=store)
        loaded = store.load()
        assert loaded is not None
        assert len(loaded.samples.get("ec2:t3", [])) == 1

    def test_state_not_written_on_duplicate(self):
        store = InMemoryCaliburStateStore()
        process_settlement_event(_event(1), store=store)
        p_before = store.load()
        process_settlement_event(_event(1), store=store)
        # Same object: no save() called on duplicate, so same identity
        assert store.load() is p_before


# ---------------------------------------------------------------------------
# 8. AdmissionService active_policy integration
# ---------------------------------------------------------------------------

class TestAdmissionServiceActivePolicy:
    """Verify that active_policy flows from AdmissionService into _multiplier()."""

    def _make_service(self, policies, active_policy="fixed"):
        from spendctl.admission import AdmissionService
        from unittest.mock import MagicMock

        ledger = MagicMock()
        pricing = MagicMock()
        return AdmissionService(
            ledger=ledger,
            pricing=pricing,
            policies=policies,
            active_policy=active_policy,
        )

    def test_fixed_policy_returns_110(self):
        p = MarginPolicies()
        svc = self._make_service(p, "fixed")
        assert svc._multiplier() == Decimal("1.10")

    def test_invalid_policy_raises_at_construction(self):
        from spendctl.admission import AdmissionService
        from unittest.mock import MagicMock

        with pytest.raises(ValueError, match="active_policy"):
            AdmissionService(
                ledger=MagicMock(),
                pricing=MagicMock(),
                active_policy="not_a_real_policy",
            )

    def test_adaptive_below_sample_gate_still_gives_fixed(self):
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        for i in range(9):  # 9 < 10 gate
            h.handle(_event(i, actual_micro=800_000))
        svc = self._make_service(p, "adaptive")
        assert svc._multiplier() == Decimal("1.10")

    def test_adaptive_above_gate_differs_when_ratios_are_low(self):
        """Once >= 10 samples with low ratios, adaptive multiplier < 1.10."""
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        for i in range(10):
            h.handle(_event(i, actual_micro=800_000))  # ratio 0.80
        svc = self._make_service(p, "adaptive")
        mult = svc._multiplier()
        assert mult < Decimal("1.10")

    def test_rolling_quantile_above_gate(self):
        p = MarginPolicies()
        h = SettlementHandler(policies=p)
        for i in range(10):
            h.handle(_event(i, actual_micro=800_000))
        svc = self._make_service(p, "rolling_quantile")
        # rolling_quantile Q0.95 of 10 identical 0.8 ratios = 0.8, clamped to 1.0
        mult = svc._multiplier()
        assert Decimal("1.0") <= mult <= Decimal("2.0")

    def test_no_policies_uses_static_multiplier(self):
        from spendctl.admission import AdmissionService
        from unittest.mock import MagicMock

        svc = AdmissionService(
            ledger=MagicMock(),
            pricing=MagicMock(),
            reserve_multiplier=Decimal("1.15"),
        )
        assert svc._multiplier() == Decimal("1.15")
