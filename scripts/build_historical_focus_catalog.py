"""Build a redacted aggregate-rate catalog from the approved latest FOCUS file.

This script never copies raw FOCUS rows or ResourceIds into the catalog. Run
after an approved delivery changes; the source loader verifies its checksum.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from research.ingestion.focus_billing_adapter import DELIVERY_REGISTRY
from research.ingestion.focus_loader import load_parquet

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "services" / "spendctl" / "historical_focus_catalog.json"


def _sku_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple)):
        try:
            return dict(value)
        except (TypeError, ValueError):
            return {}
    return {}


def build_catalog(rows: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
    """Create only aggregates for observed, attributable hourly compute usage."""
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"resources": {}, "rows": 0, "skipped_missing_resource": 0}
    )
    for row in rows:
        sku = _sku_map(row.get("SkuPriceDetails"))
        if (
            row.get("ChargeCategory") != "Usage"
            or str(row.get("ResourceType", "")).lower() != "instance"
            or row.get("ConsumedUnit") != "Hours"
            or row.get("BillingCurrency") != "USD"
        ):
            continue
        instance_type = sku.get("InstanceType")
        region = row.get("RegionId")
        operating_system = sku.get("OperatingSystem")
        resource_id = row.get("ResourceId")
        if not all(isinstance(value, str) and value for value in (instance_type, region, operating_system)):
            continue
        key = (instance_type, region, operating_system, "USD")
        group = groups[key]
        if not isinstance(resource_id, str) or not resource_id:
            group["skipped_missing_resource"] += 1
            continue
        try:
            cost = Decimal(str(row["BilledCost"]))
            quantity = Decimal(str(row["ConsumedQuantity"]))
            if not cost.is_finite() or not quantity.is_finite() or cost < 0 or quantity <= 0:
                continue
        except (KeyError, InvalidOperation, TypeError, ValueError):
            continue
        resource = group["resources"].setdefault(resource_id, {"cost": Decimal(0), "hours": Decimal(0)})
        resource["cost"] += cost
        resource["hours"] += quantity
        resource.setdefault("charge_period_starts", []).append(row.get("ChargePeriodStart"))
        resource.setdefault("charge_period_ends", []).append(row.get("ChargePeriodEnd"))
        group["rows"] += 1

    rates: list[dict[str, Any]] = []
    for (instance_type, region, operating_system, currency), group in sorted(groups.items()):
        per_resource = [
            values["cost"] * Decimal(1_000_000) / values["hours"]
            for values in group["resources"].values()
            if values["hours"] > 0
        ]
        if not per_resource:
            continue
        starts = [
            stamp for values in group["resources"].values()
            for stamp in values.get("charge_period_starts", []) if isinstance(stamp, str)
        ]
        ends = [
            stamp for values in group["resources"].values()
            for stamp in values.get("charge_period_ends", []) if isinstance(stamp, str)
        ]
        rates.append({
            "instance_type": instance_type,
            "region": region,
            "operating_system": operating_system,
            "unit": "Hours",
            "currency": currency,
            "resource_count": len(per_resource),
            "observation_rows": group["rows"],
            "skipped_rows_missing_resource_id": group["skipped_missing_resource"],
            "charge_period_start": min(starts) if starts else None,
            "charge_period_end": max(ends) if ends else None,
            "mean_rate_micro_per_hour": str(sum(per_resource) / len(per_resource)),
            "minimum_rate_micro_per_hour": str(min(per_resource)),
            "maximum_rate_micro_per_hour": str(max(per_resource)),
        })

    return {
        "schema_version": 1,
        "mode": "HISTORICAL_ESTIMATE",
        "source": source,
        "historical_period": {
            "billing_period_start": source.get("billing_period_start"),
            "billing_period_end": source.get("billing_period_end"),
            "charge_period_semantics": "FOCUS billing/charge period boundaries; not arrival timestamps",
        },
        "method": (
            "For each observed resource, sum BilledCost and consumed Hours in the "
            "latest approved snapshot; average the per-resource USD/hour rates, "
            "then multiply by requested runtime."
        ),
        "compute_rates": rates,
        "storage_status": "NOT_ESTIMABLE",
        "storage_reason": (
            "The selected FOCUS rows do not expose a verified gp3 volume SKU and "
            "matching GB-month usage needed for the requested EBS estimate."
        ),
    }


def main() -> None:
    delivery = DELIVERY_REGISTRY[-1]
    loaded = load_parquet(delivery["relative_path"])
    source = {
        "dataset": "AWS FOCUS 1.2",
        "delivery_label": delivery["label"],
        "delivery_path": delivery["relative_path"],
        "delivery_timestamp": delivery["delivery_timestamp"],
        "delivery_timestamp_semantics": "export delivery timestamp; not billing amount first-availability",
        "sha256": loaded.sha256,
        "sha256_verified": loaded.sha256_verified,
        "row_count": loaded.row_count,
        "currency": loaded.currency,
        "billing_period_start": loaded.billing_period_start,
        "billing_period_end": loaded.billing_period_end,
    }
    catalog = build_catalog([row.fields for row in loaded.rows], source)
    OUTPUT.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} from checksum-verified {delivery['label']} ({loaded.row_count} source rows).")


if __name__ == "__main__":
    main()
