"""Application adapters for validated, durable CALIBUR observations.

The engine's Kalman model is configuration driven.  No covariance or
observation mapping is supplied here by default; deployment must provide an
owner-reviewed ``CALIBUR_KALMAN_CONFIG`` JSON object. Observations are stored
under stable source record keys in the existing Reservations table and the
current estimate is deterministically replayed from the durable observation
history. A revised source record replaces the previous revision at ingestion.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from calibur_engine.models import SourceRef
from calibur_engine.state_estimator import (
    LinearModel,
    StateObservation,
    estimate_state,
)

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_SOURCES = {"cloudwatch", "cost_explorer", "cur"}


class ObservationConflict(ValueError):
    """A stable source record and revision was reused with different values."""


class StateEstimatorConfigurationError(RuntimeError):
    """The estimator cannot run without valid, owner-reviewed model settings."""


def ingest_observation_handler(event: dict, context: Any) -> dict:
    """Internal event handler. EventBridge source and schema are validated.

    Observation storage works without Kalman configuration.  When
    ``CALIBUR_KALMAN_CONFIG`` is present, observation rows and noise are
    attached so the record is immediately filterable; otherwise the raw
    observation is stored and becomes filterable once configuration is supplied.

    Also handles S3 ``ObjectCreated:Put`` events from the FOCUS billing export
    bucket: each non-zero cost row is converted to a CUR observation and stored.
    """
    event_source = event.get("source") or event.get("Records", [{}])[0].get("eventSource", "")
    if event_source == "spendctl.observations":
        detail = event.get("detail")
        if not isinstance(detail, dict):
            raise TypeError("event detail must be an object")
        try:
            config = _model_config()
        except StateEstimatorConfigurationError:
            config = None
        observation = _parse_observation(detail, config)
        table = _reservations_table()
        result = put_observation(table, observation)
        return {"state": "STORED", "record_id": observation.record_id, "revision": observation.revision, **result}
    if event_source == "aws:s3":
        return _ingest_s3_focus(event)
    raise ValueError(f"unsupported observation event source: {event_source!r}")


def _ingest_s3_focus(event: dict) -> dict:
    """Ingest FOCUS 1.2 parquet exports from S3 into the observation store.

    Each non-zero ``EffectiveCost`` row becomes one CUR observation.  The
    observation source is ``cur``, the record key is derived from the
    ``ResourceId`` plus ``ChargePeriodStart`` for dedup/revision safety, and
    the charge period timestamps become ``source_at`` / ``observed_at``.
    """
    import os

    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    records = event.get("Records", [])
    stored = 0
    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        try:
            import io

            import pyarrow.parquet as pq
            pq.read_table(io.BytesIO(body))
        except ImportError:
            raise RuntimeError("pyarrow is required to read FOCUS parquet exports; add pyarrow as a Lambda layer")
        from research.ingestion.focus_loader import cost_to_micro, load_parquet
        # Reuse the research loader for consistent parsing
        result = load_parquet(key)
        for row in result.rows:
            cost = row.fields.get("EffectiveCost", 0)
            if not cost or cost <= 0:
                continue
            resource_id = row.fields.get("ResourceId") or "unknown"
            charge_start = row.fields.get("ChargePeriodStart")
            event = {
                "source": "cur",
                "record_id": f"focus:{resource_id}:{charge_start}",
                "revision": 1,
                "source_at": str(charge_start),
                "observed_at": datetime.now(UTC).isoformat(),
                "measurement": int(cost_to_micro(cost)),
                "source_file": key,
                "semantics": f"FOCUS EffectiveCost for {row.fields.get('ServiceName', 'unknown')}",
            }
            try:
                observation = _parse_observation(event, None)
                put_observation(_reservations_table(), observation)
                stored += 1
            except ObservationConflict:
                # Duplicate revision - idempotent, skip
                continue
    return {"state": "STORED", "stored": stored}


def estimate_handler(event: dict, context: Any) -> dict:
    """Authenticated API route target: replay persisted observations at now."""
    config = _model_config()
    instant = datetime.now(UTC)
    observations = list_observations(_reservations_table())
    result = estimate_state(
        config["model"], observations, decision_at=instant, max_age=config["max_age"]
    )
    return {
        "estimate": {
            "status": result.status,
            "state": list(result.state) if result.state is not None else None,
            "covariance": [list(row) for row in result.covariance] if result.covariance is not None else None,
            "used_record_ids": list(result.used_record_ids),
            "skipped": [{"record_id": key, "reason": reason} for key, reason in result.skipped],
            "assumptions": list(result.assumptions),
            "decision_at": instant.isoformat().replace("+00:00", "Z"),
            "state_definition": config["state_definition"],
        },
        "observation_count": len(observations),
        "sources": sorted({observation.source for observation in observations}),
    }


def put_observation(table: Any, observation: StateObservation) -> dict:
    """Idempotently retain one immutable revision, and advance revisions safely."""
    current_key = _current_key(observation)
    history_key = _history_key(observation)
    payload = _observation_dict(observation)
    response = table.get_item(Key=current_key, ConsistentRead=True)
    prior = response.get("Item")
    if prior:
        prior_revision = int(prior["revision"])
        if observation.revision < prior_revision:
            raise ObservationConflict("observation revision is stale")
        if observation.revision == prior_revision:
            if _observation_payload(prior) != _observation_payload(payload):
                raise ObservationConflict("same observation revision has conflicting content")
            return {"replayed": True}
    history_item = {**history_key, **payload, "record_type": "CALIBUR_OBSERVATION_REVISION"}
    current_item = {**current_key, **payload, "record_type": "CALIBUR_OBSERVATION_CURRENT"}
    current_condition = "attribute_not_exists(pk)"
    expression_values = {}
    if prior:
        current_condition = "revision < :revision"
        expression_values[":revision"] = observation.revision
    try:
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": history_item,
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": current_item,
                        "ConditionExpression": current_condition,
                        **({"ExpressionAttributeValues": expression_values} if expression_values else {}),
                    }
                },
            ]
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code not in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            raise
        latest = table.get_item(Key=current_key, ConsistentRead=True).get("Item")
        if latest and int(latest["revision"]) == observation.revision:
            if _observation_payload(latest) == _observation_payload(payload):
                return {"replayed": True}
            raise ObservationConflict("same observation revision has conflicting content") from error
        raise ObservationConflict("observation changed concurrently; retry the same source revision") from error
    return {"replayed": False}


def list_observations(table: Any) -> list[StateObservation]:
    rows: list[dict] = []
    for source in sorted(_SOURCES):
        response = table.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": f"CALIBUR_OBSERVATIONS#{source}"},
            ConsistentRead=True,
        )
        rows.extend(
            item for item in response.get("Items", [])
            if item.get("record_type") == "CALIBUR_OBSERVATION_CURRENT"
        )
        while response.get("LastEvaluatedKey"):
            response = table.query(
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": f"CALIBUR_OBSERVATIONS#{source}"},
                ExclusiveStartKey=response["LastEvaluatedKey"],
                ConsistentRead=True,
            )
            rows.extend(
                item for item in response.get("Items", [])
                if item.get("record_type") == "CALIBUR_OBSERVATION_CURRENT"
            )
    return [_from_item(row) for row in rows]


def _parse_observation(data: dict, config: dict | None = None) -> StateObservation:
    required = ("source", "record_id", "revision", "source_at", "observed_at", "measurement", "source_file", "semantics")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"missing observation fields: {missing}")
    source = str(data["source"])
    if source not in _SOURCES:
        raise ValueError("source must be cloudwatch, cost_explorer, or cur")
    observation_row = ()
    observation_noise = None
    if config is not None:
        observation_row = tuple(float(x) for x in config["observation_rows"][source])
        observation_noise = float(config["observation_noise_by_source"][source])
    return StateObservation(
        source=source,
        record_id=str(data["record_id"]),
        revision=_integer(data["revision"], "revision"),
        source_at=_time(data["source_at"]),
        observed_at=_time(data["observed_at"]),
        measurement=_finite_number(data["measurement"], "measurement"),
        observation_row=observation_row,
        observation_noise=observation_noise,
        provenance=SourceRef(
            str(data["record_id"]),
            _time(data["observed_at"]),
            str(data["source_file"]),
            str(data["semantics"]),
        ),
    )


def _model_config() -> dict:
    raw = os.environ.get("CALIBUR_KALMAN_CONFIG")
    if not raw:
        raise StateEstimatorConfigurationError(
            "CALIBUR_KALMAN_CONFIG is required; no default covariance or source mapping is defensible"
        )
    try:
        value = json.loads(raw)
        model_data = value["model"]
        model = LinearModel(
            transition=tuple(tuple(float(x) for x in row) for row in model_data["transition"]),
            process_noise=tuple(tuple(float(x) for x in row) for row in model_data["process_noise"]),
            initial_state=tuple(float(x) for x in model_data["initial_state"]),
            initial_covariance=tuple(tuple(float(x) for x in row) for row in model_data["initial_covariance"]),
            transition_interval_seconds=float(model_data["transition_interval_seconds"]),
        )
        state_definition = value["state_definition"]
        if (
            not isinstance(state_definition, list)
            or len(state_definition) != len(model.initial_state)
            or any(not isinstance(item, str) or not item.strip() for item in state_definition)
        ):
            raise ValueError("state_definition must describe every state component")
        source_rows = value["observation_rows"]
        if set(source_rows) != _SOURCES or any(len(source_rows[source]) != len(model.initial_state) for source in _SOURCES):
            raise ValueError("observation_rows must explicitly map each supported source to the state")
        noise = value["observation_noise_by_source"]
        if set(noise) != _SOURCES or any(
            isinstance(noise[source], bool)
            or not isinstance(noise[source], (int, float))
            or not math.isfinite(noise[source])
            or noise[source] <= 0
            for source in _SOURCES
        ):
            raise ValueError("observation_noise_by_source must supply a positive configured value for each source")
        observation_rows = {source: tuple(float(x) for x in source_rows[source]) for source in _SOURCES}
        if any(not all(math.isfinite(x) for x in row) for row in observation_rows.values()):
            raise ValueError("observation rows must be finite")
        age = timedelta(seconds=int(value["max_observation_age_seconds"]))
        if age <= timedelta(0):
            raise ValueError("max_observation_age_seconds must be positive")
        return {"model": model, "state_definition": state_definition, "observation_rows": observation_rows, "observation_noise_by_source": {source: float(noise[source]) for source in _SOURCES}, "max_age": age}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise StateEstimatorConfigurationError("CALIBUR_KALMAN_CONFIG is invalid") from error


def _from_item(item: dict) -> StateObservation:
    row = tuple(float(x) for x in item.get("observation_row") or [])
    noise = float(item["observation_noise"]) if item.get("observation_noise") is not None else None
    return StateObservation(
        source=item["source"], record_id=item["record_id"], revision=int(item["revision"]),
        source_at=_time(item["source_at"]), observed_at=_time(item["observed_at"]),
        measurement=float(item["measurement"]), observation_row=row,
        observation_noise=noise,
        provenance=SourceRef(item["record_id"], _time(item["observed_at"]), item["source_file"], item["semantics"]),
    )


def _observation_dict(value: StateObservation) -> dict:
    return {
        "source": value.source, "record_id": value.record_id, "revision": value.revision,
        "source_at": _iso(value.source_at), "observed_at": _iso(value.observed_at),
        "measurement": Decimal(str(value.measurement)),
        "observation_row": [Decimal(str(x)) for x in value.observation_row] if value.observation_row else [],
        "observation_noise": Decimal(str(value.observation_noise)) if value.observation_noise is not None else None,
        "source_file": value.provenance.source_file if value.provenance else None,
        "semantics": value.provenance.semantics if value.provenance else None,
    }


def _observation_payload(item: dict) -> dict:
    return {key: item[key] for key in ("source", "record_id", "revision", "source_at", "observed_at", "measurement", "observation_row", "observation_noise", "source_file", "semantics") if key in item and item[key] is not None}


def _current_key(value): return {"pk": f"CALIBUR_OBSERVATIONS#{value.source}", "sk": f"{value.record_id}#CURRENT"}
def _history_key(value): return {"pk": f"CALIBUR_OBSERVATIONS#{value.source}", "sk": f"{value.record_id}#REV#{value.revision:012d}"}
def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0: raise ValueError(f"{name} must be a non-negative integer")
    return value
def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)
def _time(value):
    if not isinstance(value, str): raise TypeError("timestamps must be ISO-8601 strings")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    result = datetime.fromisoformat(text)
    if result.tzinfo is None: raise ValueError("timestamps must include a timezone")
    return result.astimezone(UTC)
def _iso(value): return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
def _encoded(value): return _SERIALIZER.serialize(value)
def _encoded_item(item): return {key: _encoded(value) for key, value in item.items()}
def _encoded_key(value): return {key: _encoded(item) for key, item in value.items()}
def _decoded(item): return {key: _DESERIALIZER.deserialize(value) for key, value in item.items()} if item else None
def _reservations_table():
    name = os.environ.get("RESERVATIONS_TABLE")
    if not name: raise RuntimeError("RESERVATIONS_TABLE is required")
    return boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1")).Table(name)
