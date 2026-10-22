"""B4 predictive calibration scorer (R3, research-only).

B4 exposes an exact log-gross-return predictive distribution conditional on a
caller-supplied, fixed innovation covariance: for core ``j``,

    y_t = log(1 + r_t) ~ Normal(mean_log_j, predictive_cov_log[j][j]).

This module scores that distribution against realised observations and checks
that the state used for each forecast contains no information from the future.
It is a scorer, not a model fitter and not a data loader.

What is computed per core
-------------------------
* Gaussian negative log-likelihood (NLL; lower is better);
* Gaussian CRPS (lower is better);
* probability integral transform ``PIT = Phi(z)`` and calibration summaries:
  mean/variance, central-interval coverage, histogram and a
  Kolmogorov-Smirnov distance to Uniform(0, 1) with its asymptotic 5 %
  threshold ``1.36 / sqrt(n)`` (diagnostic only; serial dependence makes the
  iid threshold optimistic);
* two mean-only baselines that keep the **same** predictive variance:
  zero mean and the expanding sample mean of the state's admitted log returns.
  Their NLL/CRPS differences isolate the value of B4's posterior mean.

PIT safety
----------
Every record carries the state used for the forecast and an explicit
``forecast_at``.  For each record the scorer requires:

* every admitted observation in the state was observed, available and admitted
  at or before ``forecast_at``;
* the state's training cutoff is at or before ``forecast_at``;
* the realised observation occurs strictly after ``forecast_at``, after the
  state watermark and after the training cutoff.

This proves the state's visible inputs were available by the forecast time.
It does **not** prove that the caller's ``sigma`` and ``training_identity``
were honestly estimated on a disjoint training window; that remains a caller
attestation, as in B4.

Boundaries
----------
- Research only: no fitting, data access, storage, orders or budget effect.
- Marginal scores only.  The joint covariance is available in B4 but is not
  scored here; a two-core result is two marginal results, not a joint score.
- The predictive law is Gaussian in log returns **conditional on fixed
  Sigma**.  Sigma estimation uncertainty, serial dependence and model
  adequacy are outside this scorer.
- Baselines share B4's predictive variance; they are mean comparisons, not
  alternative full predictive models.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from typing import Any

from us_equity_strategies.research.bayesian_kelly import (
    B4Error,
    B4Observation,
    B4State,
    LOG_RETURN_UNIT,
    MODEL_ID as B4_MODEL_ID,
    b4_predictive_moments,
)

SCORER_ID = "r3_b4_predictive_calibration_v1"
STATUS_COMPUTED = "COMPUTED"
STATUS_UNCOMPUTABLE = "UNCOMPUTABLE"
COVERAGE_LEVELS = (0.5, 0.8, 0.9, 0.95)


@dataclass(frozen=True)
class B4ForecastRecord:
    """One PIT-safe forecast/realisation pair."""

    state: B4State
    realized: B4Observation
    forecast_at: datetime


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label}_AWARE_TIMESTAMP_REQUIRED")
    return value.astimezone(timezone.utc)


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label}_INVALID")
    return float(value)


def _observation(value: object, core_ids: tuple[str, ...]) -> B4Observation:
    if type(value) is not B4Observation:
        raise TypeError("B4_OBSERVATION_REQUIRED")
    if value.unit != LOG_RETURN_UNIT or value.core_ids != core_ids:
        raise ValueError("OBSERVATION_UNIT_OR_CORE_ORDER_MISMATCH")
    if type(value.revision) is not int or value.revision < 1:
        raise ValueError("POSITIVE_REVISION_REQUIRED")
    if not isinstance(value.observation_id, str) or not value.observation_id:
        raise ValueError("OBSERVATION_ID_REQUIRED")
    observed = _utc(value.observed_at, "OBSERVED_AT")
    available = _utc(value.available_at, "AVAILABLE_AT")
    if available < observed:
        raise ValueError("AVAILABILITY_BEFORE_OBSERVATION")
    if len(value.values) != len(core_ids):
        raise ValueError("DIMENSION_MISMATCH")
    for x in value.values:
        _finite(x, "OBSERVATION_VALUE")
    return value


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _normal_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _nll(y: float, mu: float, variance: float) -> tuple[float, float, float]:
    sd = math.sqrt(variance)
    z = (y - mu) / sd
    return 0.5 * math.log(2.0 * math.pi * variance) + 0.5 * z * z, z, sd


def _crps(z: float, sd: float) -> float:
    return sd * (z * (2.0 * _normal_cdf(z) - 1.0) + 2.0 * _normal_pdf(z)
                 - 1.0 / math.sqrt(math.pi))


def _pit(z: float) -> float:
    return min(1.0, max(0.0, _normal_cdf(z)))


def _sample_variance(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = math.fsum(values) / n
    return math.fsum((x - mean) ** 2 for x in values) / (n - 1)


def _ks_distance(sorted_pit: Sequence[float]) -> float:
    n = len(sorted_pit)
    d_plus = max((i + 1) / n - u for i, u in enumerate(sorted_pit))
    d_minus = max(u - i / n for i, u in enumerate(sorted_pit))
    return max(d_plus, d_minus)


def _aggregate(rows: Sequence[dict], bins: int) -> dict:
    n = len(rows)
    pit = sorted(row["pit"] for row in rows)
    histogram = [0] * bins
    for u in pit:
        histogram[min(bins - 1, int(u * bins))] += 1
    levels = {}
    for level in COVERAGE_LEVELS:
        low, high = (1.0 - level) / 2.0, 1.0 - (1.0 - level) / 2.0
        levels[format(level, "g")] = sum(1 for u in pit if low <= u <= high) / n
    threshold = 1.36 / math.sqrt(n)
    ks = _ks_distance(pit)
    return {
        "observations": n,
        "nll_mean": math.fsum(row["nll"] for row in rows) / n,
        "crps_mean": math.fsum(row["crps"] for row in rows) / n,
        "pit_mean": math.fsum(pit) / n,
        "pit_variance": _sample_variance(pit),
        "pit_histogram": {"bins": bins, "counts": histogram},
        "pit_coverage": levels,
        "pit_ks_distance": ks,
        "pit_ks_threshold_5pct": threshold,
        "pit_ks_exceeds_5pct": ks > threshold,
        "z_mean": math.fsum(row["z"] for row in rows) / n,
        "z_std": (math.sqrt(_sample_variance([row["z"] for row in rows]))
                  if n >= 2 else None),
        "nll_mean_delta_vs_zero_mean": (
            math.fsum(row["nll"] - row["zero_mean_nll"] for row in rows) / n),
        "nll_mean_delta_vs_sample_mean": (
            math.fsum(row["nll"] - row["sample_mean_nll"] for row in rows) / n),
        "crps_mean_delta_vs_zero_mean": (
            math.fsum(row["crps"] - row["zero_mean_crps"] for row in rows) / n),
        "crps_mean_delta_vs_sample_mean": (
            math.fsum(row["crps"] - row["sample_mean_crps"] for row in rows) / n),
        "share_b4_nll_better_than_zero_mean": (
            sum(1 for row in rows if row["nll"] < row["zero_mean_nll"]) / n),
        "share_b4_nll_better_than_sample_mean": (
            sum(1 for row in rows if row["nll"] < row["sample_mean_nll"]) / n),
        "delta_sign": ("NLL_AND_CRPS_DELTAS_NEGATIVE_FAVOUR_B4; "
                       "PIT_NEAR_UNIFORM_AND_COVERAGE_NEAR_NOMINAL_FAVOUR_CALIBRATION"),
    }


def _score_record(record: B4ForecastRecord, core_ids: tuple[str, ...],
                  base_sigma: tuple[tuple[float, ...], ...],
                  base_identity: str, base_training: datetime) -> dict:
    forecast_at = _utc(record.forecast_at, "FORECAST_AT")
    if type(record.state) is not B4State:
        raise TypeError("B4_STATE_REQUIRED")
    try:
        moments = b4_predictive_moments(record.state)
    except B4Error as exc:
        return {"status": STATUS_UNCOMPUTABLE, "reason_code": "B4_" + str(exc)}
    if record.state.spec.core_ids != core_ids:
        raise ValueError("CORE_ORDER_MISMATCH")
    if record.state.sigma != base_sigma:
        raise ValueError("MIXED_B4_SIGMA")
    if record.state.training_identity != base_identity:
        raise ValueError("MIXED_B4_TRAINING_IDENTITY")
    if record.state.spec.training_cutoff != base_training:
        raise ValueError("MIXED_B4_TRAINING_CUTOFF")
    realized = _observation(record.realized, core_ids)
    observed = _utc(realized.observed_at, "OBSERVED_AT")
    available = _utc(realized.available_at, "AVAILABLE_AT")
    if base_training > forecast_at:
        raise ValueError("TRAINING_CUTOFF_AFTER_FORECAST")
    for accepted in record.state.records:
        old = accepted.observation
        if (old.observed_at > forecast_at or old.available_at > forecast_at
                or accepted.decision_cutoff > forecast_at):
            raise ValueError("STATE_CONTAINS_FUTURE_INFORMATION")
    if observed <= forecast_at:
        raise ValueError("FORECAST_NOT_BEFORE_REALIZATION")
    if observed <= base_training:
        raise ValueError("TRAINING_OVERLAP")
    if record.state.watermark is not None and observed <= record.state.watermark:
        raise ValueError("REALIZATION_NOT_AFTER_WATERMARK")

    core_rows = {}
    for j, core in enumerate(core_ids):
        y = _finite(realized.values[j], "OBSERVATION_VALUE")
        mu = _finite(moments.mean_log[j], "PREDICTIVE_MEAN")
        variance = _finite(moments.predictive_cov_log[j][j], "PREDICTIVE_VARIANCE")
        if variance <= 0.0:
            return {"status": STATUS_UNCOMPUTABLE, "reason_code": "NONPOSITIVE_PREDICTIVE_VARIANCE"}
        nll, z, sd = _nll(y, mu, variance)
        if not all(math.isfinite(x) for x in (nll, z, sd)):
            return {"status": STATUS_UNCOMPUTABLE, "reason_code": "UNCOMPUTABLE_NLL"}
        crps = _crps(z, sd)
        pit = _pit(z)
        if not all(math.isfinite(x) for x in (crps, pit)):
            return {"status": STATUS_UNCOMPUTABLE, "reason_code": "UNCOMPUTABLE_CRPS"}
        zero_nll, _, _ = _nll(y, 0.0, variance)
        sample_mu = 0.0 if record.state.n == 0 else (
            record.state.sum_y[j] + record.state.compensation[j]) / record.state.n
        sample_nll, _, _ = _nll(y, sample_mu, variance)
        zero_crps = _crps((y - 0.0) / sd, sd)
        sample_crps = _crps((y - sample_mu) / sd, sd)
        if not all(math.isfinite(x) for x in (zero_nll, sample_mu, sample_nll,
                                               zero_crps, sample_crps)):
            return {"status": STATUS_UNCOMPUTABLE, "reason_code": "UNCOMPUTABLE_BASELINE"}
        core_rows[core] = {
            "realized_log_return": y, "predictive_mean_log": mu,
            "predictive_variance_log": variance, "z": z, "pit": pit,
            "nll": nll, "crps": crps,
            "zero_mean_nll": zero_nll, "zero_mean_crps": zero_crps,
            "sample_mean_log": sample_mu, "sample_mean_nll": sample_nll,
            "sample_mean_crps": sample_crps,
        }
    return {
        "status": STATUS_COMPUTED, "reason_code": None,
        "forecast_at": forecast_at.isoformat(),
        "observation_id": realized.observation_id,
        "observed_at": observed.isoformat(), "available_at": available.isoformat(),
        "state_n": record.state.n,
        "state_status": moments.status,
        "source_prefix_sha256": moments.source_prefix_sha256,
        "cores": core_rows,
    }


def score_b4_predictive_calibration(
    records: Sequence[B4ForecastRecord],
    *,
    bins: int = 10,
) -> dict:
    """Score a PIT-safe sequence of B4 forecasts; never an order or budget.

    ``records`` must be ordered by ``realized.observed_at`` with unique
    observation ids.  The first record fixes the core order, Sigma and
    training provenance for the whole evaluation.
    """
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise TypeError("FORECAST_RECORDS_REQUIRED")
    if not records:
        raise ValueError("FORECAST_RECORDS_EMPTY")
    if type(bins) is not int or bins < 2:
        raise ValueError("BINS_INVALID")
    envelope = {
        "research_only": True, "promotion_eligible": False, "data_identity": "CALLER_SUPPLIED",
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "budget_effect": "NONE", "scorer_id": SCORER_ID, "b4_model_id": B4_MODEL_ID,
        "distribution": "GAUSSIAN_CONDITIONAL_ON_FIXED_SIGMA",
        "scoring_unit": LOG_RETURN_UNIT,
        "joint_scoring": "NOT_COMPUTED_MARGINAL_ONLY",
    }
    if any(type(record) is not B4ForecastRecord for record in records):
        raise TypeError("B4_FORECAST_RECORD_REQUIRED")
    if any(type(record.realized) is not B4Observation for record in records):
        raise TypeError("B4_OBSERVATION_REQUIRED")
    first = records[0]
    if type(first.state) is not B4State:
        raise TypeError("B4_STATE_REQUIRED")
    try:
        b4_predictive_moments(first.state)
    except B4Error as exc:
        return {**envelope, "status": STATUS_UNCOMPUTABLE,
                "reason_code": "B4_" + str(exc)}
    core_ids = first.state.spec.core_ids
    observations = [_utc(record.realized.observed_at, "OBSERVED_AT") for record in records]
    if any(later <= earlier for earlier, later in itertools.pairwise(observations)):
        raise ValueError("REALIZATIONS_NOT_STRICTLY_ORDERED")
    if len({record.realized.observation_id for record in records}) != len(records):
        raise ValueError("DUPLICATE_REALIZATION_ID")

    rows = []
    for record in records:
        scored = _score_record(record, core_ids, first.state.sigma,
                               first.state.training_identity,
                               first.state.spec.training_cutoff)
        if scored["status"] != STATUS_COMPUTED:
            return {**envelope, "status": STATUS_UNCOMPUTABLE,
                    "reason_code": scored["reason_code"]}
        rows.append(scored)

    cores = {}
    for core in core_ids:
        core_rows = [row["cores"][core] for row in rows]
        cores[core] = _aggregate(core_rows, bins)

    canonical = json.dumps(
        {"scorer_id": SCORER_ID, "core_ids": list(core_ids), "rows": rows},
        sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        **envelope, "status": STATUS_COMPUTED, "reason_code": None,
        "cores": cores, "records": rows,
        "scored_series_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "validation": {
            "state_inputs_available_by_forecast_at": "CHECKED",
            "realization_after_forecast_and_watermark": "CHECKED",
            "training_cutoff_before_forecast": "CHECKED",
            "sigma_training_provenance_honesty": "CALLER_ATTESTATION_NOT_VERIFIED",
        },
    }
