"""Adaptive margin policy: gate, floor, and variance response."""

from decimal import Decimal

import pytest
from spendctl.admission import AdmissionService, active_policy_from_env, resolve_active_policy
from spendctl.calibration import FIXED, MarginPolicies, Sample

MODELED_MICRO = 1_000_000


def _add(policies: MarginPolicies, settlement_id: str, ratio: float, order: int = 0) -> None:
    policies.add_settlement(
        "ec2:t3",
        Sample(
            settlement_id=settlement_id,
            ratio=ratio,
            reserved_micro=int(Decimal("1.10") * MODELED_MICRO),
            actual_micro=int(ratio * MODELED_MICRO),
            modeled_runtime_micro=MODELED_MICRO,
            settled_order=order,
        ),
    )


def test_rolling_quantile_engages_with_two_samples():
    policies = MarginPolicies(min_samples=2)
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(FIXED)

    _add(policies, "s1", 1.20, 0)
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(FIXED)

    _add(policies, "s2", 1.40, 1)
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(1.40)
    assert policies.summary("ec2:t3", "rolling_quantile")["sample_gate_met"] is True


def test_below_gate_falls_back_to_static_multiplier():
    policies = MarginPolicies(min_samples=3)
    _add(policies, "s1", 1.5)
    _add(policies, "s2", 1.6)
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(FIXED)


def test_higher_variance_widens_the_margin():
    tight = MarginPolicies(min_samples=2)
    wide = MarginPolicies(min_samples=2)
    for index, ratio in enumerate((1.01, 1.02)):
        _add(tight, f"t{index}", ratio, index)
    for index, ratio in enumerate((1.00, 1.50)):
        _add(wide, f"w{index}", ratio, index)

    tight_margin = tight.get("ec2:t3", "rolling_quantile")
    wide_margin = wide.get("ec2:t3", "rolling_quantile")
    assert wide_margin > tight_margin
    assert tight_margin < FIXED


def test_consistent_predictions_tighten_below_the_static_floor():
    policies = MarginPolicies(min_samples=2, min_multiplier=1.0)
    for index, ratio in enumerate((1.005, 1.010)):
        _add(policies, f"s{index}", ratio, index)
    margin = policies.get("ec2:t3", "rolling_quantile")
    assert margin == pytest.approx(1.010)
    assert margin < FIXED


def test_min_multiplier_floor_applies():
    policies = MarginPolicies(min_samples=2, min_multiplier=1.05)
    for index, ratio in enumerate((1.001, 1.002)):
        _add(policies, f"s{index}", ratio, index)
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(1.05)


def test_max_multiplier_ceiling_applies():
    policies = MarginPolicies(min_samples=2, max_multiplier=2.0)
    for index, ratio in enumerate((2.5, 3.0)):
        _add(policies, f"s{index}", ratio, index)
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(2.0)


def test_min_samples_env_override(monkeypatch):
    monkeypatch.setenv("CALIBUR_MIN_SAMPLES", "2")
    assert MarginPolicies().min_samples == 2


def test_env_gate_engages_rolling_quantile_at_two(monkeypatch):
    monkeypatch.setenv("CALIBUR_MIN_SAMPLES", "2")
    policies = MarginPolicies()
    _add(policies, "s1", 1.20, 0)
    _add(policies, "s2", 1.40, 1)
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(1.40)


def test_active_policy_env_resolution(monkeypatch):
    monkeypatch.setenv("CALIBUR_ACTIVE_POLICY", "rolling_quantile")
    assert active_policy_from_env() == "rolling_quantile"
    monkeypatch.setenv("CALIBUR_ACTIVE_POLICY", "not-a-policy")
    assert active_policy_from_env() == "fixed"


def test_min_multiplier_env_override(monkeypatch):
    monkeypatch.setenv("CALIBUR_MIN_MULTIPLIER", "1.03")
    assert MarginPolicies().min_multiplier == pytest.approx(1.03)


def test_invalid_env_threshold_is_rejected(monkeypatch):
    monkeypatch.setenv("CALIBUR_MIN_SAMPLES", "not-an-integer")
    with pytest.raises(ValueError, match="CALIBUR_MIN_SAMPLES"):
        MarginPolicies()


def test_invalid_bounds_are_rejected():
    with pytest.raises(ValueError, match="multiplier bounds"):
        MarginPolicies(min_multiplier=1.5, max_multiplier=1.2)
    with pytest.raises(ValueError, match="multiplier bounds"):
        MarginPolicies(max_multiplier=2.5)
    with pytest.raises(ValueError, match="min_samples"):
        MarginPolicies(min_samples=0)


def test_unknown_active_policy_falls_back_to_fixed():
    assert resolve_active_policy("rolling_quantile") == "rolling_quantile"
    assert resolve_active_policy("") == "fixed"
    assert resolve_active_policy(None) == "fixed"
    assert resolve_active_policy("not-a-policy") == "fixed"


def test_admission_multiplier_uses_the_selected_policy():
    policies = MarginPolicies(min_samples=2)
    _add(policies, "s1", 1.20, 0)
    _add(policies, "s2", 1.40, 1)

    service = AdmissionService(
        ledger=object(),
        pricing=object(),
        policies=policies,
        active_policy="rolling_quantile",
    )
    assert service.active_policy == "rolling_quantile"
    assert service._multiplier("ec2:t3") == Decimal("1.4")


def test_split_conformal_uses_only_the_frozen_partition():
    policies = MarginPolicies(min_samples=2)
    _add(policies, "settled-1", 1.50, 0)
    _add(policies, "settled-2", 1.60, 1)
    assert policies.get("ec2:t3", "split_conformal") == pytest.approx(FIXED)

    policies.set_split_calibration("ec2:t3", [1.10, 1.12, 1.15])
    # 95% one-sided order statistic needs enough points for k <= n.
    assert policies.get("ec2:t3", "split_conformal") == pytest.approx(FIXED)
    policies.set_split_calibration("ec2:t3", [1.05] * 20)
    assert policies.get("ec2:t3", "split_conformal") == pytest.approx(1.05)
