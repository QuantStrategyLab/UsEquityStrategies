"""R3: B4 predictive calibration. Synthetic states and observations only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
from statistics import NormalDist

import pytest

from us_equity_strategies.research import b4_predictive_calibration as r3
from us_equity_strategies.research.bayesian_kelly import (
    B4Error,
    B4Observation,
    B4Spec,
    b4_predictive_moments,
    initialise_b4,
    update_b4,
)

ANCHOR = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
SPEC = B4Spec(("TQQQ",), ANCHOR)
SIGMA = ((0.0004,),)
TRAINING = "synthetic-r3-training"


def observation(i, values=(0.002,), *, spec=SPEC, identity=None, revision=1):
    close = ANCHOR + timedelta(days=i)
    return B4Observation(identity or f"session-{i}", revision, close,
                         close + timedelta(seconds=1), spec.core_ids, values)


def admit(state, obs):
    cutoff = obs.available_at + timedelta(seconds=1)
    if state.last_cutoff is not None:
        cutoff = max(cutoff, state.last_cutoff)
    return update_b4(state, obs, decision_cutoff=cutoff).state


def forecast_time(i):
    # ANCHOR is the 21:00 close of session 0; forecast the next close at 01:00.
    return ANCHOR + timedelta(days=i - 1, hours=4)


def record(state, realized, *, forecast_at):
    return r3.B4ForecastRecord(state, realized, forecast_at)


def calibrated_walk(n=100):
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    records = []
    normal = NormalDist()
    for i in range(1, n + 1):
        moments = b4_predictive_moments(state)
        u = (i - 0.5) / n
        y = moments.mean_log[0] + normal.inv_cdf(u) * math.sqrt(
            moments.predictive_cov_log[0][0])
        obs = observation(i, (y,))
        records.append(record(state, obs, forecast_at=forecast_time(i)))
        state = admit(state, obs)
    return records


def test_nll_crps_and_pit_match_hand_calculation():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    moments = b4_predictive_moments(state)
    sd = math.sqrt(moments.predictive_cov_log[0][0])
    y = moments.mean_log[0] + 0.5 * sd
    result = r3.score_b4_predictive_calibration(
        [record(state, observation(1, (y,)), forecast_at=ANCHOR + timedelta(hours=1))])
    assert result["status"] == "COMPUTED"
    row = result["records"][0]["cores"]["TQQQ"]
    variance = moments.predictive_cov_log[0][0]
    expected_nll = 0.5 * math.log(2.0 * math.pi * variance) + 0.125
    expected_crps = sd * (0.5 * (2.0 * 0.6914624612740131 - 1.0)
                          + 2.0 * 0.3520653267642995 - 1.0 / math.sqrt(math.pi))
    assert row["z"] == pytest.approx(0.5)
    assert row["nll"] == pytest.approx(expected_nll)
    assert row["crps"] == pytest.approx(expected_crps)
    assert row["pit"] == pytest.approx(0.6914624612740131)
    assert row["sample_mean_log"] == 0.0
    assert row["sample_mean_nll"] == pytest.approx(row["zero_mean_nll"])


def test_under_the_model_pit_is_uniform_and_coverage_is_nominal():
    result = r3.score_b4_predictive_calibration(calibrated_walk(100))
    core = result["cores"]["TQQQ"]
    assert core["observations"] == 100
    assert core["pit_mean"] == pytest.approx(0.5, abs=1e-12)
    # Midpoint grid (i - 0.5)/n has sample variance (n + 1) / (12 n).
    assert core["pit_variance"] == pytest.approx(101.0 / 1200.0, rel=1e-6)
    assert core["pit_ks_distance"] < 0.02
    assert core["pit_ks_exceeds_5pct"] is False
    for level, actual in core["pit_coverage"].items():
        assert actual == pytest.approx(float(level), abs=0.02)
    assert core["z_mean"] == pytest.approx(0.0, abs=1e-12)
    assert core["z_std"] == pytest.approx(1.0, abs=0.005)


def test_a_shifted_outcome_is_detected_as_miscalibrated():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    records = []
    for i in range(1, 51):
        moments = b4_predictive_moments(state)
        sd = math.sqrt(moments.predictive_cov_log[0][0])
        y = moments.mean_log[0] + 2.0 * sd
        obs = observation(i, (y,))
        records.append(record(state, obs, forecast_at=forecast_time(i)))
        state = admit(state, obs)
    core = r3.score_b4_predictive_calibration(records)["cores"]["TQQQ"]
    assert core["pit_mean"] > 0.97
    assert core["pit_coverage"]["0.5"] < 0.1
    assert core["pit_ks_exceeds_5pct"] is True


def test_sample_mean_baseline_uses_only_the_admitted_prefix():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    state = admit(state, observation(1, (0.01,)))
    state = admit(state, observation(2, (-0.005,)))
    realized = observation(3, (0.001,))
    result = r3.score_b4_predictive_calibration(
        [record(state, realized, forecast_at=forecast_time(3))])
    row = result["records"][0]["cores"]["TQQQ"]
    assert row["sample_mean_log"] == pytest.approx(0.0025)
    variance = row["predictive_variance_log"]
    expected_zero_nll = 0.5 * math.log(2.0 * math.pi * variance) + 0.5 * (
        (0.001 - 0.0) ** 2 / variance)
    assert row["zero_mean_nll"] == pytest.approx(expected_zero_nll)


def test_state_with_future_information_is_rejected():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    state = admit(state, observation(1, (0.001,)))
    state = admit(state, observation(2, (0.001,)))
    forecast_at = ANCHOR + timedelta(days=1, hours=1)
    with pytest.raises(ValueError, match="STATE_CONTAINS_FUTURE_INFORMATION"):
        r3.score_b4_predictive_calibration(
            [record(state, observation(3, (0.001,)), forecast_at=forecast_at)])


def test_forecast_must_precede_realization():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    realized = observation(1, (0.001,))
    with pytest.raises(ValueError, match="FORECAST_NOT_BEFORE_REALIZATION"):
        r3.score_b4_predictive_calibration(
            [record(state, realized, forecast_at=realized.observed_at)])


def test_training_cutoff_must_not_follow_the_forecast():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    with pytest.raises(ValueError, match="TRAINING_CUTOFF_AFTER_FORECAST"):
        r3.score_b4_predictive_calibration(
            [record(state, observation(1, (0.001,)),
                    forecast_at=ANCHOR - timedelta(hours=1))])


def test_mixed_provenance_is_rejected():
    first = record(initialise_b4(SPEC, SIGMA, TRAINING), observation(1, (0.001,)),
                   forecast_at=forecast_time(1))
    other_sigma = ((0.0009,),)
    second = record(initialise_b4(SPEC, other_sigma, TRAINING), observation(2, (0.001,)),
                    forecast_at=forecast_time(2))
    with pytest.raises(ValueError, match="MIXED_B4_SIGMA"):
        r3.score_b4_predictive_calibration([first, second])
    other_identity = record(initialise_b4(SPEC, SIGMA, "other-training"),
                            observation(2, (0.001,)),
                            forecast_at=forecast_time(2))
    with pytest.raises(ValueError, match="MIXED_B4_TRAINING_IDENTITY"):
        r3.score_b4_predictive_calibration([first, other_identity])
    other_cutoff_spec = B4Spec(("TQQQ",), ANCHOR + timedelta(days=1))
    other_cutoff = record(initialise_b4(other_cutoff_spec, SIGMA, TRAINING),
                          observation(2, (0.001,)), forecast_at=forecast_time(2))
    with pytest.raises(ValueError, match="MIXED_B4_TRAINING_CUTOFF"):
        r3.score_b4_predictive_calibration([first, other_cutoff])


def test_duplicate_ids_and_unordered_realizations_are_rejected():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    first = record(state, observation(1, (0.001,), identity="same"),
                   forecast_at=forecast_time(1))
    duplicate = record(state, observation(2, (0.001,), identity="same"),
                       forecast_at=forecast_time(2))
    with pytest.raises(ValueError, match="DUPLICATE_REALIZATION_ID"):
        r3.score_b4_predictive_calibration([first, duplicate])
    same_time = record(state, observation(1, (0.001,), identity="other"),
                       forecast_at=forecast_time(1))
    with pytest.raises(ValueError, match="REALIZATIONS_NOT_STRICTLY_ORDERED"):
        r3.score_b4_predictive_calibration([first, same_time])


def test_two_core_states_are_scored_as_marginals():
    spec = B4Spec(("TQQQ", "SOXL"), ANCHOR)
    sigma = ((0.0004, 0.0001), (0.0001, 0.0009))
    state = initialise_b4(spec, sigma, TRAINING)
    records = []
    for i in range(1, 21):
        moments = b4_predictive_moments(state)
        values = tuple(moments.mean_log[j] + 0.25 * math.sqrt(
            moments.predictive_cov_log[j][j]) for j in range(2))
        obs = observation(i, values, spec=spec)
        records.append(record(state, obs, forecast_at=forecast_time(i)))
        state = admit(state, obs)
    result = r3.score_b4_predictive_calibration(records)
    assert result["status"] == "COMPUTED"
    assert result["joint_scoring"] == "NOT_COMPUTED_MARGINAL_ONLY"
    assert set(result["cores"]) == {"TQQQ", "SOXL"}
    for core in result["cores"].values():
        assert core["observations"] == 20


def test_moment_errors_and_nonpositive_variance_are_uncomputable(monkeypatch):
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    rec = record(state, observation(1, (0.001,)), forecast_at=ANCHOR + timedelta(hours=1))

    def boom(_state):
        raise B4Error("UNCOMPUTABLE_MOMENTS")

    monkeypatch.setattr(r3, "b4_predictive_moments", boom)
    result = r3.score_b4_predictive_calibration([rec])
    assert result["status"] == "UNCOMPUTABLE"
    assert result["reason_code"] == "B4_UNCOMPUTABLE_MOMENTS"

    real = b4_predictive_moments
    monkeypatch.setattr(
        r3, "b4_predictive_moments",
        lambda value: replace(real(value), predictive_cov_log=((0.0,),)))
    result = r3.score_b4_predictive_calibration([rec])
    assert result["status"] == "UNCOMPUTABLE"
    assert result["reason_code"] == "NONPOSITIVE_PREDICTIVE_VARIANCE"

    records = calibrated_walk(2)
    calls = {"n": 0}

    def flaky(value):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise B4Error("UNCOMPUTABLE_MOMENTS")
        return real(value)

    monkeypatch.setattr(r3, "b4_predictive_moments", flaky)
    result = r3.score_b4_predictive_calibration(records)
    assert result["status"] == "UNCOMPUTABLE"
    assert result["reason_code"] == "B4_UNCOMPUTABLE_MOMENTS"


def test_nll_overflow_is_uncomputable_not_infinity():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    rec = record(state, observation(1, (1e308,)), forecast_at=ANCHOR + timedelta(hours=1))
    result = r3.score_b4_predictive_calibration([rec])
    assert result["status"] == "UNCOMPUTABLE"
    assert result["reason_code"] in ("UNCOMPUTABLE_NLL", "UNCOMPUTABLE_BASELINE")


def test_input_validation_and_output_boundaries():
    state = initialise_b4(SPEC, SIGMA, TRAINING)
    rec = record(state, observation(1, (0.001,)), forecast_at=ANCHOR + timedelta(hours=1))
    with pytest.raises(ValueError, match="FORECAST_RECORDS_EMPTY"):
        r3.score_b4_predictive_calibration([])
    with pytest.raises(TypeError, match="B4_FORECAST_RECORD_REQUIRED"):
        r3.score_b4_predictive_calibration([(state, observation(1))])
    with pytest.raises(ValueError, match="BINS_INVALID"):
        r3.score_b4_predictive_calibration([rec], bins=1)
    result = r3.score_b4_predictive_calibration([rec])
    encoding = json.dumps(result, allow_nan=False, sort_keys=True)
    assert '"budget_effect": "NONE"' in encoding
    assert '"no_order": true' in encoding
    assert '"live_ready": false' in encoding
    assert result["promotion_eligible"] is False
    assert result["data_identity"] == "CALLER_SUPPLIED"
    assert len(result["scored_series_sha256"]) == 64
    assert result["validation"]["sigma_training_provenance_honesty"].startswith("CALLER_ATTESTATION")
