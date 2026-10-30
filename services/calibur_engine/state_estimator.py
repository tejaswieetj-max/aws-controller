"""Pure, configuration-driven linear Kalman state estimation.

The model is deliberately not enabled by default.  A caller must provide every
transition, process covariance, source observation row, and source observation
covariance.  Thus CloudWatch, Cost Explorer, and CUR do not acquire invented
precision merely by naming the source.

The canonical two-state interpretation is ``x=[accrued_spend, unbilled_spend]``
in micro-USD.  A CloudWatch-derived provisional-cost observation may use
``H=[0, 1]``; Cost Explorer or a final CUR total may use ``H=[1, 0]``.  These
are conventions, not defaults: the integration must document them per source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from .models import SourceRef

Source = Literal["cloudwatch", "cost_explorer", "cur"]


class StateEstimatorError(ValueError):
    """Invalid model configuration or observation evidence."""


@dataclass(frozen=True)
class LinearModel:
    """A fully specified discrete linear model ``x'=Fx+w``, ``w~N(0,Q)``."""

    transition: tuple[tuple[float, ...], ...]
    process_noise: tuple[tuple[float, ...], ...]
    initial_state: tuple[float, ...]
    initial_covariance: tuple[tuple[float, ...], ...]
    transition_interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        n = len(self.initial_state)
        if (
            not n
            or len(self.transition) != n
            or len(self.process_noise) != n
            or len(self.initial_covariance) != n
            or any(len(row) != n for row in self.transition + self.process_noise + self.initial_covariance)
        ):
            raise StateEstimatorError("all model matrices must be non-empty square state matrices")
        if not all(_finite(value) for value in self.initial_state) or not all(
            _finite(value)
            for matrix in self.transition + self.process_noise + self.initial_covariance
            for value in matrix
        ):
            raise StateEstimatorError("model parameters must be finite")
        if not _symmetric(self.process_noise) or not _symmetric(self.initial_covariance):
            raise StateEstimatorError("process and initial covariance must be symmetric")
        _positive_semidefinite(self.process_noise)
        _positive_semidefinite(self.initial_covariance)
        if not _finite(self.transition_interval_seconds) or self.transition_interval_seconds <= 0:
            raise StateEstimatorError("transition_interval_seconds must be a positive finite number")


@dataclass(frozen=True)
class StateObservation:
    """One source measurement, observed at a controller-known time.

    ``record_id`` plus ``revision`` is the durable source identity.  Later
    revisions replace earlier versions for that key before filtering.

    ``observation_row`` and ``observation_noise`` are only required when the
    observation is fed into the Kalman filter.  They may be absent when an
    observation is stored before owner-reviewed Kalman configuration exists;
    such observations are durable and become filterable once configuration is
    supplied.
    """

    source: Source
    record_id: str
    revision: int
    source_at: datetime
    observed_at: datetime
    measurement: float
    observation_row: tuple[float, ...] = ()
    observation_noise: float | None = None
    provenance: SourceRef = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.source not in {"cloudwatch", "cost_explorer", "cur"}:
            raise StateEstimatorError("unsupported observation source")
        if not self.record_id or isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise StateEstimatorError("record_id and non-negative revision are required")
        if self.source_at.tzinfo is None or self.observed_at.tzinfo is None:
            raise StateEstimatorError("observation timestamps must be timezone-aware")
        if self.observed_at < self.source_at:
            raise StateEstimatorError("observed_at cannot precede source_at")
        if not _finite(self.measurement):
            raise StateEstimatorError("measurement must be finite")
        if self.observation_row and not all(_finite(value) for value in self.observation_row):
            raise StateEstimatorError("observation row values must be finite when present")
        if self.observation_noise is not None and (not _finite(self.observation_noise) or self.observation_noise <= 0):
            raise StateEstimatorError("observation_noise must be strictly positive when present")
        if self.provenance is not None and not isinstance(self.provenance, SourceRef):
            raise StateEstimatorError("provenance must be a SourceRef")


@dataclass(frozen=True)
class StateEstimate:
    status: str
    state: tuple[float, ...] | None
    covariance: tuple[tuple[float, ...], ...] | None
    used_record_ids: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]
    assumptions: tuple[str, ...]


def estimate_state(
    model: LinearModel,
    observations: list[StateObservation],
    *,
    decision_at: datetime,
    max_age: timedelta,
) -> StateEstimate:
    """Filter eligible observations without filling missing sources with zero.

    An observation is eligible only when it was controller-observed by the
    decision timestamp and its source timestamp is within ``max_age``.  A
    revision replaces its earlier version for the same ``source:record_id``;
    equal revisions with differing evidence are rejected as ambiguous.
    """
    if decision_at.tzinfo is None or max_age <= timedelta(0):
        raise StateEstimatorError("decision_at must be aware and max_age positive")
    latest: dict[tuple[str, str], StateObservation] = {}
    skipped: list[tuple[str, str]] = []
    for item in observations:
        if len(item.observation_row) != len(model.initial_state):
            skipped.append((item.record_id, "NO_CONFIGURED_OBSERVATION_ROW"))
            continue
        if item.observation_noise is None:
            skipped.append((item.record_id, "NO_CONFIGURED_OBSERVATION_NOISE"))
            continue
        key = (item.source, item.record_id)
        prior = latest.get(key)
        if prior is None or item.revision > prior.revision:
            latest[key] = item
        elif item.revision == prior.revision and item != prior:
            raise StateEstimatorError("conflicting evidence for identical source record revision")
    eligible: list[StateObservation] = []
    for item in latest.values():
        if item.observed_at > decision_at:
            skipped.append((item.record_id, "OBSERVED_AFTER_DECISION"))
        elif decision_at - item.source_at > max_age:
            skipped.append((item.record_id, "STALE_SOURCE_OBSERVATION"))
        else:
            eligible.append(item)
    if not eligible:
        return StateEstimate("INSUFFICIENT_DATA", None, None, (), tuple(sorted(skipped)), _ASSUMPTIONS)
    x = list(model.initial_state)
    p = [list(row) for row in model.initial_covariance]
    used: list[str] = []
    previous_source_at: datetime | None = None
    for item in sorted(eligible, key=lambda value: (value.source_at, value.observed_at, value.record_id)):
        # The transition is expressed per configured interval, not per arriving
        # record.  This retains source chronology across asynchronous sensors.
        elapsed = 0.0 if previous_source_at is None else (item.source_at - previous_source_at).total_seconds()
        steps = max(0.0, elapsed / model.transition_interval_seconds)
        transition = _matrix_power(model.transition, steps)
        x = _matvec(transition, x)
        # Q is configured per transition interval; this is a documented
        # discrete-time approximation, not an empirical calibration.
        p = _add(_matmul(_matmul(transition, p), _transpose(transition)), _scale(model.process_noise, steps))
        h = list(item.observation_row)
        innovation = item.measurement - _dot(h, x)
        ph = _matvec(p, h)
        residual_variance = _dot(h, ph) + item.observation_noise
        if residual_variance <= 0:
            raise StateEstimatorError("innovation covariance must be positive")
        gain = [value / residual_variance for value in ph]
        x = [value + gain[index] * innovation for index, value in enumerate(x)]
        identity_minus_kh = [
            [float(row == column) - gain[row] * h[column] for column in range(len(x))]
            for row in range(len(x))
        ]
        # Joseph form keeps covariance symmetric/PSD under finite precision.
        p = _add(
            _matmul(_matmul(identity_minus_kh, p), _transpose(identity_minus_kh)),
            [[gain[row] * item.observation_noise * gain[column] for column in range(len(x))] for row in range(len(x))],
        )
        used.append(item.record_id)
        previous_source_at = item.source_at
    return StateEstimate("OK", tuple(x), tuple(tuple(row) for row in p), tuple(used), tuple(sorted(skipped)), _ASSUMPTIONS)


_ASSUMPTIONS = (
    "Transition, process noise, observation rows, and observation noise are caller-supplied assumptions.",
    "CloudWatch, Cost Explorer, and CUR observations are never substituted for missing sources.",
    "A revision replaces one source record version; it is not an independent measurement.",
    "The output is an estimate, not an authoritative provider bill.",
)


def _dot(left: list[float], right: list[float]) -> float: return sum(a * b for a, b in zip(left, right, strict=True))
def _finite(value):
    import math

    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
def _matvec(matrix, vector): return [_dot(list(row), vector) for row in matrix]
def _transpose(matrix): return [list(row) for row in zip(*matrix, strict=True)]
def _matmul(left, right): return [[_dot(list(row), list(column)) for column in _transpose(right)] for row in left]
def _add(left, right): return [[a + b for a, b in zip(row_a, row_b, strict=True)] for row_a, row_b in zip(left, right, strict=True)]
def _scale(matrix, factor): return [[value * factor for value in row] for row in matrix]
def _identity(size): return [[float(row == column) for column in range(size)] for row in range(size)]
def _matrix_power(matrix, exponent):
    """Integer interval power; reject fractional timing instead of interpolating."""
    rounded = round(exponent)
    if abs(exponent - rounded) > 1e-9:
        raise StateEstimatorError("source timestamps must align to transition_interval_seconds")
    result, base, count = _identity(len(matrix)), [list(row) for row in matrix], int(rounded)
    while count:
        if count & 1: result = _matmul(result, base)
        base = _matmul(base, base)
        count >>= 1
    return result
def _symmetric(matrix): return all(abs(matrix[row][column] - matrix[column][row]) < 1e-12 for row in range(len(matrix)) for column in range(len(matrix)))
def _positive_semidefinite(matrix):
    # Cholesky with a tiny tolerance accepts semidefinite covariance matrices.
    n, lower = len(matrix), [[0.0] * len(matrix) for _ in matrix]
    for row in range(n):
        for column in range(row + 1):
            value = matrix[row][column] - sum(lower[row][k] * lower[column][k] for k in range(column))
            if row == column:
                if value < -1e-10: raise StateEstimatorError("covariance must be positive semidefinite")
                lower[row][column] = max(value, 0.0) ** 0.5
            elif lower[column][column] > 1e-12:
                lower[row][column] = value / lower[column][column]
            elif abs(value) > 1e-10:
                raise StateEstimatorError("covariance must be positive semidefinite")
