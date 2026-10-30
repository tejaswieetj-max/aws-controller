"""CostSamples writer and revision-aware loader."""

import pytest
from spendctl.api import _load_margin_policies
from spendctl.sample_writer import (
    PARTITION_CALIBRATION,
    PARTITION_EVALUATION,
    SampleWriteError,
    build_sample_item,
)

RESERVATION = {
    "settlement_stage": "FINAL",
    "final_settlement_id": "settlement-1",
    "compute_micro": 12_000,
    "storage_micro": 877,
    "reserved_micro": 14_165,
    "settlement_version": 2,
    "settled_at": "2026-10-29T00:00:00Z",
    "resource": {"instance_type": "t3.micro"},
}
DETAIL = {
    "reservation_id": "reservation-1",
    "settlement_id": "settlement-1",
    "new_actual_micro": 13_000,
}


def test_build_sample_item_carries_the_settled_ratio_inputs():
    item = build_sample_item(DETAIL, RESERVATION)
    assert item is not None
    assert item["resource_class"] == "ec2:t3"
    assert item["settlement_id"] == "settlement-1"
    assert item["reservation_id"] == "reservation-1"
    assert item["settlement_version"] == 2
    assert item["settlement_stage"] == "FINAL"
    assert item["attributed"] is True
    assert item["actual_micro"] == 13_000
    assert item["modeled_runtime_micro"] == 12_877
    assert item["sample_partition"] in {PARTITION_CALIBRATION, PARTITION_EVALUATION}


def test_stale_revision_is_ignored():
    reservation = {**RESERVATION, "final_settlement_id": "settlement-2"}
    assert build_sample_item(DETAIL, reservation) is None


def test_provisional_settlement_is_ignored():
    reservation = {**RESERVATION, "settlement_stage": "PROVISIONAL"}
    assert build_sample_item(DETAIL, reservation) is None


def test_missing_cost_component_is_not_replaced_with_zero():
    reservation = {key: value for key, value in RESERVATION.items() if key != "storage_micro"}
    with pytest.raises(SampleWriteError, match="integer cost component"):
        build_sample_item(DETAIL, reservation)


def test_negative_actual_is_rejected():
    with pytest.raises(SampleWriteError, match="non-negative"):
        build_sample_item({**DETAIL, "new_actual_micro": -1}, RESERVATION)


def test_explicit_calibration_partition_is_honoured():
    item = build_sample_item(
        {**DETAIL, "sample_partition": PARTITION_EVALUATION}, RESERVATION
    )
    assert item is not None
    assert item["sample_partition"] == PARTITION_EVALUATION


def test_implicit_partition_is_deterministic():
    first = build_sample_item(DETAIL, RESERVATION)
    second = build_sample_item(DETAIL, RESERVATION)
    assert first is not None and second is not None
    assert first["sample_partition"] == second["sample_partition"]


class _FakeDb:
    def __init__(self, items):
        self._items = items

    def query(self, **kwargs):
        return {"Items": self._items}


def _wire(item):
    out = {}
    for key, value in item.items():
        if isinstance(value, bool):
            out[key] = {"BOOL": value}
        elif isinstance(value, int):
            out[key] = {"N": str(value)}
        else:
            out[key] = {"S": str(value)}
    return out


def _row(reservation_id, settlement_id, settled_at, actual, version, partition):
    return {
        "resource_class": "ec2:t3",
        "settled_at": settled_at,
        "settlement_id": settlement_id,
        "reservation_id": reservation_id,
        "settlement_version": version,
        "settlement_stage": "FINAL",
        "attributed": True,
        "sample_partition": partition,
        "actual_micro": actual,
        "reserved_micro": 1_100_000,
        "modeled_runtime_micro": 1_000_000,
    }


def test_loader_uses_two_samples_and_dedupes_revisions():
    rows = [
        _row("r1", "s1", "2026-10-01T00:00:00Z", 1_000_000, 1, PARTITION_CALIBRATION),
        _row("r1", "s2", "2026-10-02T00:00:00Z", 1_500_000, 2, PARTITION_CALIBRATION),
        _row("r2", "s3", "2026-10-03T00:00:00Z", 1_200_000, 1, PARTITION_EVALUATION),
    ]

    policies = _load_margin_policies(
        _FakeDb([_wire(row) for row in rows]), "CostSamples"
    )

    samples = policies.samples["ec2:t3"]
    assert len(samples) == 2
    assert sorted(sample.ratio for sample in samples) == [1.2, 1.5]
    # The revised r1 label replaces s1 and is the only calibration point.
    assert policies.split_calibration["ec2:t3"] == [1.5]
    assert policies.get("ec2:t3", "rolling_quantile") == pytest.approx(1.5)
