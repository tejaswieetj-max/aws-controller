"""Durable observation adapter tests use labeled local fixtures only."""

import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws
from spendctl.state_estimation import (
    ObservationConflict,
    _model_config,
    _parse_observation,
    estimate_handler,
    ingest_observation_handler,
    list_observations,
    put_observation,
)

CONFIG = {
    "state_definition": ["accrued_micro_usd", "unbilled_micro_usd"],
    "model": {
        "transition": [[1, 0], [0, 1]],
        "process_noise": [[0, 0], [0, 0]],
        "initial_state": [0, 0],
        "initial_covariance": [[100, 0], [0, 100]],
        "transition_interval_seconds": 60,
    },
    "observation_rows": {"cloudwatch": [0, 1], "cost_explorer": [1, 0], "cur": [1, 0]},
    "observation_noise_by_source": {"cloudwatch": 1, "cost_explorer": 4, "cur": 2},
    "max_observation_age_seconds": 86400,
}


def detail(revision, amount):
    observed = datetime(2026, 10, 26, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return {
        "source": "cur", "record_id": "line-1", "revision": revision,
        "source_at": observed, "observed_at": observed, "measurement": amount,
        "source_file": "cur-export", "semantics": "provider-record-observed-by-collector",
    }


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="ap-south-1")
        table = resource.create_table(
            TableName="Reservations",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield table


def test_ingestion_requires_explicit_model_and_source_noise(monkeypatch):
    monkeypatch.delenv("CALIBUR_KALMAN_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="no default covariance"):
        _model_config()
    monkeypatch.setenv("CALIBUR_KALMAN_CONFIG", json.dumps(CONFIG))
    config = _model_config()
    parsed = _parse_observation(detail(0, 500), config)
    assert parsed.observation_row == (1.0, 0.0)
    assert parsed.observation_noise == 2.0


def test_revision_is_durable_idempotent_and_replaces_current_state(table, monkeypatch):
    monkeypatch.setenv("CALIBUR_KALMAN_CONFIG", json.dumps(CONFIG))
    config = _model_config()
    first = _parse_observation(detail(0, 500), config)
    revised = _parse_observation(detail(1, 650), config)
    assert put_observation(table, first) == {"replayed": False}
    assert put_observation(table, first) == {"replayed": True}
    assert put_observation(table, revised) == {"replayed": False}
    assert len(list_observations(table)) == 1
    assert list_observations(table)[0].revision == 1
    with pytest.raises(ObservationConflict, match="stale"):
        put_observation(table, first)
    assert table.get_item(Key={"pk": "CALIBUR_OBSERVATIONS#cur", "sk": "line-1#REV#000000000000"})["Item"]["measurement"] == 500


def test_api_estimate_returns_insufficient_state_when_no_observations(table, monkeypatch):
    monkeypatch.setenv("CALIBUR_KALMAN_CONFIG", json.dumps(CONFIG))
    monkeypatch.setenv("RESERVATIONS_TABLE", "Reservations")
    result = estimate_handler({}, None)
    assert result["estimate"]["status"] == "INSUFFICIENT_DATA"
    assert result["estimate"]["state"] is None
    assert result["observation_count"] == 0


def test_eventbridge_ingestion_validates_transport_and_is_idempotent(table, monkeypatch):
    monkeypatch.setenv("CALIBUR_KALMAN_CONFIG", json.dumps(CONFIG))
    monkeypatch.setattr("spendctl.state_estimation._reservations_table", lambda: table)
    event = {"source": "spendctl.observations", "detail-type": "CaliburObservation", "detail": detail(0, 500)}
    assert ingest_observation_handler(event, None)["replayed"] is False
    assert ingest_observation_handler(event, None)["replayed"] is True
    with pytest.raises(ValueError, match="unsupported"):
        ingest_observation_handler({**event, "source": "spendctl.billing"}, None)


def test_nonfinite_measurement_is_rejected(monkeypatch):
    monkeypatch.setenv("CALIBUR_KALMAN_CONFIG", json.dumps(CONFIG))
    config = _model_config()
    with pytest.raises(ValueError, match="finite"):
        _parse_observation(detail(0, float("inf")), config)


def test_focus_rows_convert_to_observations_and_store_idempotently(table, monkeypatch):
    """Real FOCUS rows convert to valid observations and store idempotently."""
    monkeypatch.setenv("CALIBUR_KALMAN_CONFIG", json.dumps(CONFIG))
    monkeypatch.setattr("spendctl.state_estimation._reservations_table", lambda: table)
    _model_config()

    from research.ingestion.focus_loader import cost_to_micro, load_parquet
    result = load_parquet("data/raw/focus12/CALIBUR_FOCUS_12-00001.snappy.parquet")
    nonzero = [r for r in result.rows if r.fields.get("EffectiveCost", 0) > 0]
    assert len(nonzero) > 0, "FOCUS delivery must contain non-zero cost observations"

    stored = 0
    for idx, row in enumerate(nonzero[:5]):
        resource_id = row.fields.get("ResourceId") or "unknown"
        cps = row.fields.get("ChargePeriodStart")
        event = {
            "source": "spendctl.observations",
            "detail-type": "CaliburObservation",
            "detail": {
                "source": "cur",
                "record_id": f"focus:{resource_id}:{cps}:{idx}",
                "revision": 1,
                "source_at": str(cps),
                "observed_at": "2026-10-26T23:00:00+00:00",
                "measurement": int(cost_to_micro(row.fields["EffectiveCost"])),
                "source_file": result.delivery_path,
                "semantics": "FOCUS EffectiveCost for " + str(row.fields.get("ServiceName", "unknown")),
            },
        }
        resp = ingest_observation_handler(event, None)
        assert resp["state"] == "STORED"
        stored += 1

    # Idempotent replay of first observation
    first = nonzero[0]
    resource_id = first.fields.get("ResourceId") or "unknown"
    cps = first.fields.get("ChargePeriodStart")
    replay_event = {
        "source": "spendctl.observations",
        "detail-type": "CaliburObservation",
        "detail": {
            "source": "cur",
            "record_id": f"focus:{resource_id}:{cps}:0",
            "revision": 1,
            "source_at": str(cps),
            "observed_at": "2026-10-26T23:00:00+00:00",
            "measurement": int(cost_to_micro(first.fields["EffectiveCost"])),
            "source_file": result.delivery_path,
            "semantics": "FOCUS EffectiveCost for " + str(first.fields.get("ServiceName", "unknown")),
        },
    }
    resp = ingest_observation_handler(replay_event, None)
    assert resp["replayed"] is True
    assert stored == len(list_observations(table))
