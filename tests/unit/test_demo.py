import os
import subprocess
import sys
from pathlib import Path


def test_offline_demo_uses_fixture_pricing_and_does_not_claim_admission():
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(root / "services")}
    result = subprocess.run(
        [sys.executable, "scripts/demo.py"],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Validated request: team=alpha, type=t3.micro, gp3=8GB, hours=4" in result.stdout
    assert "Pricing source: fixture_unverified" in result.stdout
    assert "Estimated compute: $0.048000" in result.stdout
    assert "Estimated gp3 storage: $0.003507" in result.stdout
    assert "Estimated total: $0.051507" in result.stdout
    assert "Policy reserve at 1.10x: $0.056658" in result.stdout
    assert "No budget was reserved" in result.stdout
