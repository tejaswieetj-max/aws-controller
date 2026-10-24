"""Offline request and estimate walkthrough; never admits or provisions a request."""

from decimal import Decimal

from spendctl.calibration import MarginPolicies
from spendctl.money import multiply_ceil
from spendctl.pricing import FixturePricingProvider, estimate_request
from spendctl.validation import validate_request


def _usd(micro_usd: int) -> str:
    return f"${Decimal(micro_usd) / Decimal(1_000_000):.6f}"


def main() -> None:
    body = {
        "team": "alpha",
        "resource": {"type": "ec2", "instance_type": "t3.micro", "ebs_gb": 8},
        "duration_hours": 4,
        "purpose": "presentation demo",
    }
    request = validate_request(body)
    pricing = FixturePricingProvider()
    estimate = estimate_request(body, pricing)
    multiplier = Decimal(str(MarginPolicies().get("ec2")))
    reserve_micro = multiply_ceil(estimate["total_micro"], multiplier)

    print("SPENDCTL local request walkthrough")
    print(
        f"Validated request: team={request.team}, type={request.instance_type}, "
        f"gp3={request.ebs_gb}GB, hours={request.duration_hours}"
    )
    print(f"Pricing source: {estimate['source']} (illustrative fixture; not live AWS pricing)")
    print(f"Estimated compute: {_usd(estimate['compute_micro'])}")
    print(f"Estimated gp3 storage: {_usd(estimate['storage_micro'])}")
    print(f"Estimated total: {_usd(estimate['total_micro'])}")
    print(f"Policy reserve at {multiplier:.2f}x: {_usd(reserve_micro)}")
    print("No budget was reserved; no AWS API, workflow, or EC2 instance was used.")


if __name__ == "__main__":
    main()
