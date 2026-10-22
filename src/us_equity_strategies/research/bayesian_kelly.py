"""Conditional Gaussian-mean research kernel; no fitting, data, storage or orders.

Sigma is a caller-supplied, fixed positive-definite training estimate. Its
estimation uncertainty, serial dependence and model adequacy are not covered.
The lognormal moments are exact under that conditional model; sizing is only
a second-order, zero-cash, frictionless proxy, never a production mandate.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

MODEL_ID = "b4_loggross_normal_mean_fixed_cov_v1"
LOG_RETURN_UNIT = "LOG_GROSS_RETURN_PER_SESSION_DECIMAL"
SIMPLE_RETURN_UNIT = "SIMPLE_RETURN_PER_SESSION_DECIMAL"
CHECKPOINT_SCHEMA = "b4_normal_mean_checkpoint.v1"
APPROXIMATION = "SECOND_ORDER_FRICTIONLESS_PROXY"
KAPPA0 = 1.0
Matrix = tuple[tuple[float, ...], ...]


class B4Error(ValueError):
    """Invalid or uncomputable research input; never replace it with zero risk."""


class B4RebuildRequired(B4Error):
    """Stop the candidate and rebuild from a newly admitted complete prefix."""


@dataclass(frozen=True)
class B4Spec:
    core_ids: tuple[str, ...]
    training_cutoff: datetime
    unit: str = LOG_RETURN_UNIT


@dataclass(frozen=True)
class B4Observation:
    observation_id: str
    revision: int
    observed_at: datetime
    available_at: datetime
    core_ids: tuple[str, ...]
    values: tuple[float, ...]
    unit: str = LOG_RETURN_UNIT


@dataclass(frozen=True)
class _Accepted:
    observation: B4Observation
    decision_cutoff: datetime


@dataclass(frozen=True)
class B4State:
    spec: B4Spec
    sigma: Matrix
    training_identity: str
    sum_y: tuple[float, ...]
    compensation: tuple[float, ...]
    records: tuple[_Accepted, ...] = ()

    @property
    def n(self) -> int:
        return len(self.records)

    @property
    def watermark(self) -> datetime | None:
        return self.records[-1].observation.observed_at if self.records else None

    @property
    def last_cutoff(self) -> datetime | None:
        return self.records[-1].decision_cutoff if self.records else None


@dataclass(frozen=True)
class B4Update:
    state: B4State
    status: str


@dataclass(frozen=True)
class B4Moments:
    core_ids: tuple[str, ...]
    n: int
    status: str
    mean_log: tuple[float, ...]
    innovation_cov_log: Matrix
    parameter_cov_log: Matrix
    predictive_cov_log: Matrix
    mean_simple: tuple[float, ...]
    innovation_cov_simple: Matrix
    parameter_cov_simple: Matrix
    predictive_cov_simple: Matrix
    second_raw_moment_simple: tuple[float, ...]
    source_prefix_sha256: str
    unit: str = SIMPLE_RETURN_UNIT


@dataclass(frozen=True)
class B4Target:
    core_ids: tuple[str, ...]
    status: str
    targets: tuple[float, ...] | None
    raw_second_order: tuple[float, ...] | None
    c: float
    cap_source: str | None
    fractional_applied_once: bool
    approximation: str = APPROXIMATION
    unit: str = "CAPITAL_FRACTION"
    no_order: bool = True
    live_ready: bool = False
    budget_effect: str = "NONE"


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise B4Error("FINITE_NUMBER_REQUIRED")
    try:
        out = float(value)
    except (OverflowError, ValueError) as exc:
        raise B4Error("FINITE_NUMBER_REQUIRED") from exc
    if not math.isfinite(out):
        raise B4Error("FINITE_NUMBER_REQUIRED")
    return 0.0 if out == 0.0 else out


def _text(value: Any) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 256 or any(ord(c) < 32 for c in value)):
        raise B4Error("IDENTITY_REQUIRED")
    return value


def _utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise B4Error("AWARE_TIMESTAMP_REQUIRED")
    return value.astimezone(timezone.utc)


def _spec(value: B4Spec) -> B4Spec:
    if (type(value) is not B4Spec or not isinstance(value.core_ids, tuple)
            or not 1 <= len(value.core_ids) <= 2 or len(set(value.core_ids)) != len(value.core_ids)
            or any(x not in ("TQQQ", "SOXL") for x in value.core_ids)):
        raise B4Error("ORDERED_CORE_IDS_REQUIRED")
    if value.unit != LOG_RETURN_UNIT:
        raise B4Error("LOG_RETURN_UNIT_REQUIRED")
    return B4Spec(value.core_ids, _utc(value.training_cutoff), value.unit)


def _vector(values: Any, size: int) -> tuple[float, ...]:
    if not isinstance(values, (tuple, list)) or len(values) != size:
        raise B4Error("DIMENSION_MISMATCH")
    return tuple(_number(x) for x in values)


def _covariance(values: Any, size: int) -> Matrix:
    if not isinstance(values, (tuple, list)) or len(values) != size:
        raise B4Error("COVARIANCE_DIMENSION_MISMATCH")
    out = tuple(_vector(row, size) for row in values)
    if any(out[i][j] != out[j][i] for i in range(size) for j in range(size)):
        raise B4Error("COVARIANCE_MUST_BE_SYMMETRIC")
    if any(out[i][i] <= 0.0 for i in range(size)):
        raise B4Error("COVARIANCE_MUST_BE_POSITIVE_DEFINITE")
    # Cholesky without jitter or a fallback estimator. PSD-but-singular Sigma
    # is outside this fixed-covariance v1, not silently repaired.
    if size == 2:
        cross = out[1][0] / math.sqrt(out[0][0])
        residual = out[1][1] - cross * cross
        if not math.isfinite(residual) or residual <= 0.0:
            raise B4Error("COVARIANCE_MUST_BE_POSITIVE_DEFINITE")
    return out


def initialise_b4(spec: B4Spec, sigma: Sequence[Sequence[float]],
                  training_identity: str) -> B4State:
    """Bind a conditional model, without fitting or attesting its provenance."""
    frozen_spec = _spec(spec)
    frozen_sigma = _covariance(sigma, len(frozen_spec.core_ids))
    zeros = (0.0,) * len(frozen_spec.core_ids)
    return B4State(frozen_spec, frozen_sigma, _text(training_identity), zeros, zeros)


def _state(value: B4State) -> B4State:
    if type(value) is not B4State:
        raise B4Error("B4_STATE_REQUIRED")
    spec = _spec(value.spec)
    _covariance(value.sigma, len(spec.core_ids))
    _text(value.training_identity)
    _vector(value.sum_y, len(spec.core_ids))
    _vector(value.compensation, len(spec.core_ids))
    if not isinstance(value.records, tuple) or any(type(x) is not _Accepted for x in value.records):
        raise B4Error("ACCEPTED_PREFIX_REQUIRED")
    return value


def _observation(value: B4Observation, spec: B4Spec) -> B4Observation:
    if type(value) is not B4Observation:
        raise B4Error("B4_OBSERVATION_REQUIRED")
    if value.unit != LOG_RETURN_UNIT or value.core_ids != spec.core_ids:
        raise B4Error("OBSERVATION_UNIT_OR_CORE_ORDER_MISMATCH")
    if type(value.revision) is not int or value.revision < 1:
        raise B4Error("POSITIVE_REVISION_REQUIRED")
    observed, available = _utc(value.observed_at), _utc(value.available_at)
    if available < observed:
        raise B4Error("AVAILABILITY_BEFORE_OBSERVATION")
    return B4Observation(_text(value.observation_id), value.revision, observed,
                         available, spec.core_ids, _vector(value.values, len(spec.core_ids)))


def update_b4(state: B4State, observation: B4Observation, *,
              decision_cutoff: datetime) -> B4Update:
    """Admit once; corrections require a clean rebuild, never past mutation."""
    _state(state)
    obs = _observation(observation, state.spec)
    cutoff = _utc(decision_cutoff)
    if state.last_cutoff is not None and cutoff < state.last_cutoff:
        raise B4Error("DECISION_CUTOFF_MOVED_BACKWARDS")
    for accepted in state.records:
        old = accepted.observation
        if old.observation_id == obs.observation_id:
            if old.revision != obs.revision:
                raise B4RebuildRequired("REVISION_REQUIRES_REBUILD")
            if old != obs:
                raise B4RebuildRequired("CONTENT_CONFLICT_REQUIRES_REBUILD")
            return B4Update(state, "DUPLICATE_NO_UPDATE")
    if obs.observed_at <= state.spec.training_cutoff:
        raise B4Error("TRAINING_OVERLAP")
    if obs.available_at > cutoff or obs.observed_at > cutoff:
        raise B4Error("FUTURE_OBSERVATION")
    if state.watermark is not None and obs.observed_at <= state.watermark:
        raise B4RebuildRequired("OUT_OF_ORDER_REQUIRES_REBUILD")
    sums, compensation = [], []
    for old, correction, x in zip(state.sum_y, state.compensation, obs.values, strict=True):
        total = old + x
        if not math.isfinite(total):
            raise B4Error("UNCOMPUTABLE_SUM")
        correction += ((old - total) + x) if abs(old) >= abs(x) else ((x - total) + old)
        if not math.isfinite(correction) or not math.isfinite(total + correction):
            raise B4Error("UNCOMPUTABLE_SUM")
        sums.append(total)
        compensation.append(correction)
    updated = B4State(state.spec, state.sigma, state.training_identity,
                      tuple(sums), tuple(compensation), state.records + (_Accepted(obs, cutoff),))
    return B4Update(updated, "UPDATED")


def batch_b4(spec: B4Spec, sigma: Sequence[Sequence[float]], training_identity: str,
             observations: Sequence[B4Observation], *, decision_cutoff: datetime) -> B4State:
    """A new prefix, also usable for a separately admitted correction rebuild.

    No old checkpoint or old decision is overwritten. Rebuilt provenance has a
    different prefix digest; its cutoff cannot certify historical availability.
    """
    state = initialise_b4(spec, sigma, training_identity)
    cutoff = _utc(decision_cutoff)
    for observation in observations:
        state = update_b4(state, observation, decision_cutoff=cutoff).state
    return state


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise B4Error("CHECKPOINT_PRIMITIVES_REQUIRED") from exc


def _record_dict(record: _Accepted) -> dict[str, Any]:
    obs = record.observation
    return {"decision_cutoff": record.decision_cutoff.isoformat(), "observation": {
        "observation_id": obs.observation_id, "revision": obs.revision,
        "observed_at": obs.observed_at.isoformat(), "available_at": obs.available_at.isoformat(),
        "core_ids": list(obs.core_ids), "values": list(obs.values), "unit": obs.unit}}


def _prefix(state: B4State) -> dict[str, Any]:
    return {"model_id": MODEL_ID, "kappa0": KAPPA0, "core_ids": list(state.spec.core_ids),
            "unit": state.spec.unit, "training_cutoff": state.spec.training_cutoff.isoformat(),
            "sigma": [list(row) for row in state.sigma], "training_identity": state.training_identity,
            "records": [_record_dict(x) for x in state.records]}


def _prefix_digest(state: B4State) -> str:
    return hashlib.sha256(_canonical(_prefix(state))).hexdigest()


def _psd(matrix: Matrix) -> None:
    if any(not math.isfinite(x) for row in matrix for x in row) or any(
            matrix[i][i] < 0.0 for i in range(len(matrix))):
        raise B4Error("UNCOMPUTABLE_COVARIANCE_MOMENT")
    if len(matrix) == 2:
        bound = math.sqrt(matrix[0][0]) * math.sqrt(matrix[1][1])
        if abs(matrix[0][1]) > bound * (1.0 + 1e-12):
            raise B4Error("UNCOMPUTABLE_NON_PSD_MOMENT")


def b4_predictive_moments(state: B4State) -> B4Moments:
    """Exact conditional lognormal moments, including total covariance split."""
    _state(state)
    size, n = len(state.spec.core_ids), state.n
    try:
        parameter = tuple(tuple(x / (KAPPA0 + n) for x in row) for row in state.sigma)
        predictive = tuple(tuple(state.sigma[i][j] + parameter[i][j] for j in range(size))
                           for i in range(size))
        prior = tuple(-.5 * (state.sigma[i][i] + state.sigma[i][i] / KAPPA0) for i in range(size))
        mean_log = tuple((KAPPA0 * prior[i] + math.fsum((state.sum_y[i], state.compensation[i])))
                         / (KAPPA0 + n) for i in range(size))
        a = tuple(mean_log[i] + .5 * predictive[i][i] for i in range(size))
        means = tuple(math.expm1(x) for x in a)
        covariance = tuple(tuple(math.exp(a[i] + a[j]) * math.expm1(predictive[i][j])
                                 for j in range(size)) for i in range(size))
        param_simple = tuple(tuple(math.exp(a[i] + a[j]) * math.expm1(parameter[i][j])
                                   for j in range(size)) for i in range(size))
        innov_simple = tuple(tuple(math.exp(a[i] + a[j] + parameter[i][j]) * math.expm1(state.sigma[i][j])
                                   for j in range(size)) for i in range(size))
        q = tuple(covariance[i][i] + means[i] * means[i] for i in range(size))
        if any(not math.isfinite(x) for x in (*mean_log, *means, *q)):
            raise B4Error("UNCOMPUTABLE_MOMENTS")
        for matrix in (covariance, param_simple, innov_simple):
            _psd(matrix)
    except (OverflowError, ZeroDivisionError) as exc:
        raise B4Error("UNCOMPUTABLE_MOMENTS") from exc
    return B4Moments(state.spec.core_ids, n, "PRIOR_ONLY" if n == 0 else "AVAILABLE",
                     mean_log, state.sigma, parameter, predictive, means, innov_simple,
                     param_simple, covariance, q, _prefix_digest(state))


def b4_fractional_target(moments: B4Moments, *, c: float = .25,
                         scope_cap: Mapping[str, float] | None = None,
                         cap_source: str | None = None, veto: bool = False) -> B4Target:
    """Research proxy for c=.10/.25/.40; apply c once before the scope cap.

    A caller-supplied cap reference is not attestation or execution authority.
    """
    fraction = _number(c)
    if fraction not in (.10, .25, .40) or type(veto) is not bool:
        raise B4Error("FRACTION_OR_VETO_INVALID")
    if type(moments) is not B4Moments or moments.unit != SIMPLE_RETURN_UNIT:
        raise B4Error("SIMPLE_RETURN_MOMENTS_REQUIRED")
    if moments.n == 0:
        return B4Target(moments.core_ids, "PRIOR_ONLY", None, None, fraction, None, False)
    if scope_cap is None or cap_source is None:
        return B4Target(moments.core_ids, "UNAVAILABLE_SCOPE_CAP", None, None, fraction, None, False)
    source = _text(cap_source)
    if not isinstance(scope_cap, Mapping) or set(scope_cap) != set(moments.core_ids):
        raise B4Error("SCOPE_CAP_CORE_MISMATCH")
    caps = tuple(_number(scope_cap[x]) for x in moments.core_ids)
    if any(not 0.0 <= x <= 1.0 for x in caps):
        raise B4Error("SCOPE_CAP_INVALID")
    means = _vector(moments.mean_simple, len(caps))
    second = _vector(moments.second_raw_moment_simple, len(caps))
    if any(q <= 0.0 for q in second):
        raise B4Error("UNCOMPUTABLE_SECOND_MOMENT")
    raw = tuple(max(0.0, m) / q for m, q in zip(means, second, strict=True))
    if any(not math.isfinite(x) for x in raw):
        raise B4Error("UNCOMPUTABLE_TARGET")
    targets = tuple(0.0 if veto else min(1.0, cap, fraction * f)
                    for cap, f in zip(caps, raw, strict=True))
    return B4Target(moments.core_ids, "VETOED" if veto else "AVAILABLE", targets,
                    raw, fraction, source, True)


def _checkpoint_body(state: B4State) -> dict[str, Any]:
    return {**_prefix(state), "schema": CHECKPOINT_SCHEMA, "n": state.n,
            "sum_y": list(state.sum_y), "compensation": list(state.compensation),
            "watermark": state.watermark.isoformat() if state.watermark else None,
            "last_cutoff": state.last_cutoff.isoformat() if state.last_cutoff else None,
            "prefix_sha256": _prefix_digest(state)}


def state_to_dict(state: B4State) -> dict[str, Any]:
    """Primitive checkpoint, no filesystem writes or authenticity assertion."""
    _state(state)
    body = _checkpoint_body(state)
    return {**body, "content_sha256": hashlib.sha256(_canonical(body)).hexdigest()}


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise B4Error("CHECKPOINT_TIMESTAMP_REQUIRED")
    try:
        return _utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise B4Error("CHECKPOINT_TIMESTAMP_INVALID") from exc


def state_from_dict(payload: Mapping[str, Any], *, expected_spec: B4Spec,
                    expected_sigma: Sequence[Sequence[float]],
                    expected_training_identity: str) -> B4State:
    """Require expected identity and replay-check every sufficient statistic.

    The digest detects corruption, not source authority. It cannot attest data
    availability, write success, an original fold, or approval of real policy.
    """
    initial = initialise_b4(expected_spec, expected_sigma, expected_training_identity)
    expected_keys = set(state_to_dict(initial))
    if not isinstance(payload, Mapping) or set(payload) != expected_keys:
        raise B4Error("CHECKPOINT_SCHEMA_MISMATCH")
    body = {k: v for k, v in payload.items() if k != "content_sha256"}
    if hashlib.sha256(_canonical(body)).hexdigest() != payload["content_sha256"]:
        raise B4Error("CHECKPOINT_DIGEST_MISMATCH")
    original = _checkpoint_body(initial)
    for key in ("schema", "model_id", "kappa0", "core_ids", "unit", "training_cutoff",
                "sigma", "training_identity"):
        if _canonical(body[key]) != _canonical(original[key]):
            raise B4Error("CHECKPOINT_MODEL_OR_TRAINING_MISMATCH")
    if type(body["n"]) is not int or not isinstance(body["records"], list):
        raise B4Error("CHECKPOINT_PREFIX_INVALID")
    rebuilt = initial
    try:
        for record in body["records"]:
            if set(record) != {"decision_cutoff", "observation"}:
                raise B4Error("CHECKPOINT_RECORD_INVALID")
            obs = record["observation"]
            if set(obs) != {"observation_id", "revision", "observed_at", "available_at", "core_ids", "values", "unit"}:
                raise B4Error("CHECKPOINT_OBSERVATION_INVALID")
            parsed = B4Observation(obs["observation_id"], obs["revision"], _time(obs["observed_at"]),
                _time(obs["available_at"]), tuple(obs["core_ids"]), tuple(obs["values"]), obs["unit"])
            result = update_b4(rebuilt, parsed, decision_cutoff=_time(record["decision_cutoff"]))
            if result.status != "UPDATED":
                raise B4Error("CHECKPOINT_DUPLICATE_PREFIX")
            rebuilt = result.state
    except (TypeError, KeyError) as exc:
        raise B4Error("CHECKPOINT_PREFIX_INVALID") from exc
    if _canonical(_checkpoint_body(rebuilt)) != _canonical(body):
        raise B4Error("CHECKPOINT_STATISTIC_OR_PREFIX_MISMATCH")
    return rebuilt
