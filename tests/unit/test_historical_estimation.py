"""Software tests use explicitly synthetic FOCUS-like fixtures, never evidence."""

from decimal import Decimal

from scripts.build_historical_focus_catalog import build_catalog
from services.spendctl.historical_estimation import estimate_from_catalog


def _row(resource_id, billed_cost, hours):
    return {
        "ChargeCategory": "Usage",
        "ResourceType": "instance",
        "ConsumedUnit": "Hours",
        "BillingCurrency": "USD",
        "SkuPriceDetails": [("InstanceType", "t3.micro"), ("OperatingSystem", "Linux")],
        "RegionId": "ap-south-1",
        "ResourceId": resource_id,
        "BilledCost": billed_cost,
        "ConsumedQuantity": hours,
        "ChargePeriodStart": "2026-01-01T00:00:00+00:00",
        "ChargePeriodEnd": "2026-01-02T00:00:00+00:00",
    }


def test_synthetic_catalog_aggregates_verified_hourly_cost_without_raw_ids():
    catalog = build_catalog(
        [_row("synthetic-fixture-resource-a", 0.024, 2), _row("synthetic-fixture-resource-b", 0.010, 1)],
        {"dataset": "synthetic test fixture", "sha256_verified": False},
    )
    rate = catalog["compute_rates"][0]
    assert Decimal(rate["mean_rate_micro_per_hour"]) == Decimal(11000)
    assert Decimal(rate["minimum_rate_micro_per_hour"]) == Decimal(10000)
    assert Decimal(rate["maximum_rate_micro_per_hour"]) == Decimal(12000)
    assert "synthetic-fixture-resource-a" not in str(catalog)


def test_historical_estimate_has_explicit_method_range_and_unestimable_total():
    catalog = build_catalog(
        [_row("synthetic-fixture-a", 0.024, 2), _row("synthetic-fixture-b", 0.010, 1)],
        {"dataset": "synthetic test fixture", "sha256_verified": False},
    )
    result = estimate_from_catalog(
        catalog, instance_type="t3.micro", region="ap-south-1", duration_hours=2, ebs_gb=8
    )
    assert result["mode"] == "HISTORICAL_ESTIMATE"
    assert result["status"] == "HISTORICAL_ESTIMATE"
    assert result["compute"]["amount_micro"] == 22000
    assert result["compute"]["lower_observed_amount_micro"] == 20000
    assert result["compute"]["upper_observed_amount_micro"] == 24000
    assert result["uncertainty"]["confidence_interval"] is False
    assert result["storage"]["status"] == "NOT_ESTIMABLE"
    assert result["total"] == {
        "status": "NOT_ESTIMABLE", "amount_micro": None,
        "reason": "The total requires a supported storage estimate.",
    }
    assert "not live billing" in result["warning"]


def test_missing_instance_or_region_returns_not_estimable():
    catalog = build_catalog([_row("synthetic-fixture-a", 0.024, 2)], {"dataset": "synthetic test fixture"})
    result = estimate_from_catalog(
        catalog, instance_type="t3.small", region="ap-south-1", duration_hours=1, ebs_gb=8
    )
    assert result["mode"] == "HISTORICAL_ESTIMATE"
    assert result["status"] == "NOT_ESTIMABLE"
    assert result["reason"]


def test_invalid_duration_and_catalog_rates_do_not_create_a_number():
    catalog = {
        "compute_rates": [{"instance_type": "t3.micro", "region": "ap-south-1", "unit": "Hours", "currency": "USD", "mean_rate_micro_per_hour": "NaN", "minimum_rate_micro_per_hour": "0", "maximum_rate_micro_per_hour": "0"}],
        "source": {},
    }
    invalid_request = estimate_from_catalog(
        catalog, instance_type="t3.micro", region="ap-south-1", duration_hours=0, ebs_gb=8
    )
    invalid_catalog = estimate_from_catalog(
        catalog, instance_type="t3.micro", region="ap-south-1", duration_hours=1, ebs_gb=8
    )
    assert invalid_request["status"] == "NOT_ESTIMABLE"
    assert invalid_catalog["status"] == "NOT_ESTIMABLE"
