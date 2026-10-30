"""Explicit historical estimate mode over a packaged, redacted FOCUS summary."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

WARNING = (
    "Historical-data estimate — not live billing and not a verified current AWS charge."
)
def load_catalog() -> dict[str, Any]:
    """Load the aggregate catalog included in the Lambda package."""
    path = Path(__file__).with_name("historical_focus_catalog.json")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def estimate_from_catalog(
    catalog: dict[str, Any], *, instance_type: str, region: str,
    duration_hours: int, ebs_gb: int,
) -> dict[str, Any]:
    """Estimate only the supported compute component; do not invent EBS cost."""
    base = {
        "mode": "HISTORICAL_ESTIMATE",
        "warning": WARNING,
        "source": catalog.get("source", {"status": "unavailable"}),
        "historical_period": catalog.get("historical_period"),
        "method": catalog.get("method"),
        "uncertainty": {
            "kind": "descriptive_observed_resource_rate_range",
            "confidence_interval": False,
            "interpretation": (
                "Minimum and maximum per-resource historical rates in the selected "
                "FOCUS snapshot; not a probabilistic interval or guarantee."
            ),
        },
        "storage": {
            "status": "NOT_ESTIMABLE",
            "reason": (
                "The FOCUS snapshot does not identify a verified gp3 volume rate "
                "and matching GB-month quantity for this request."
            ),
            "requested_gb": ebs_gb,
        },
        "total": {
            "status": "NOT_ESTIMABLE",
            "amount_micro": None,
            "reason": "The total requires a supported storage estimate.",
        },
        "limitations": [
            "FOCUS billing-period timestamps are period boundaries, not first-availability times.",
            "FOCUS rows are not linked to Spendctl requests or reservations.",
            "The selected delivery can be an incomplete billing-period snapshot.",
            "Historical rates do not establish current AWS prices or charges.",
        ],
    }
    if not isinstance(duration_hours, int) or isinstance(duration_hours, bool) or duration_hours <= 0:
        return {**base, "status": "NOT_ESTIMABLE", "reason": "duration_hours must be a positive integer."}
    if not isinstance(ebs_gb, int) or isinstance(ebs_gb, bool) or ebs_gb <= 0:
        return {**base, "status": "NOT_ESTIMABLE", "reason": "ebs_gb must be a positive integer."}

    matches = [
        row for row in catalog.get("compute_rates", [])
        if row.get("instance_type") == instance_type
        and row.get("region") == region
        and row.get("unit") == "Hours"
        and row.get("currency") == "USD"
    ]
    if len(matches) != 1:
        return {
            **base,
            "status": "NOT_ESTIMABLE",
            "reason": (
                "No unique, checksum-verified historical compute rate matches "
                "the requested instance type and region."
            ),
        }

    row = matches[0]
    try:
        mean_rate = Decimal(str(row["mean_rate_micro_per_hour"]))
        low_rate = Decimal(str(row["minimum_rate_micro_per_hour"]))
        high_rate = Decimal(str(row["maximum_rate_micro_per_hour"]))
        if not all(value.is_finite() and value >= 0 for value in (mean_rate, low_rate, high_rate)):
            raise InvalidOperation
        mean_micro = int((mean_rate * duration_hours).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        low_micro = int((low_rate * duration_hours).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        high_micro = int((high_rate * duration_hours).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return {**base, "status": "NOT_ESTIMABLE", "reason": "The historical rate catalog is invalid."}

    return {
        **base,
        "status": "HISTORICAL_ESTIMATE",
        "request": {"instance_type": instance_type, "region": region, "duration_hours": duration_hours},
        "compute": {
            "status": "HISTORICAL_ESTIMATE",
            "amount_micro": mean_micro,
            "lower_observed_amount_micro": low_micro,
            "upper_observed_amount_micro": high_micro,
            "unit": "micro_usd",
            "unit_rate_micro_per_hour": str(mean_rate),
            "observed_rate_range_micro_per_hour": [str(low_rate), str(high_rate)],
            "observation_rows": int(row.get("observation_rows", 0)),
            "resource_count": int(row.get("resource_count", 0)),
            "operating_system": row.get("operating_system"),
            "charge_period_start": row.get("charge_period_start"),
            "charge_period_end": row.get("charge_period_end"),
        },
        "historical_period": {
            **(catalog.get("historical_period") or {}),
            "charge_period_start": row.get("charge_period_start"),
            "charge_period_end": row.get("charge_period_end"),
        },
    }

