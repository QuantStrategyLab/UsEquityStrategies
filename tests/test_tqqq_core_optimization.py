from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import math
from unittest.mock import patch

import pytest

from us_equity_strategies.research import tqqq_core_optimization as optimization
from us_equity_strategies.research.tqqq_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.tqqq_typed_baseline_result import run_typed_baseline
from us_equity_strategies.research.tqqq_core_optimization import (
    BASELINE_WINDOW_DAYS,
    CANDIDATE_WINDOWS,
    PLUGIN_CONTROL,
    SCENARIOS,
    WINDOW_SPECS,
    _eligibility,
    _pareto_winner,
    load_persisted_result,
    persist_result,
    run_tqqq_core_optimization,
    simulate_candidate,
)


def _canonical_bytes(rows: list[InputRow]) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for row in rows:
        lines.append(
            ",".join(
                (row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))
            )
        )
    return ("\n".join(lines) + "\n").encode()


def _source(count: int = 753, *, changed_after: int | None = None) -> OfflineInput:
    rows: list[InputRow] = []
    for index in range(count):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        qqq_close = 100.0 + (index % 31)
        tqqq_open = 50.0 + (index % 7)
        tqqq_close = tqqq_open * (1.0 + ((index % 5) - 2) / 100.0)
        if changed_after is not None and index > changed_after:
            qqq_close = 500.0 + index
            tqqq_open = 10.0 + index / 100.0
            tqqq_close = tqqq_open * 1.01
        rows.extend((
            InputRow("QQQ", day, qqq_close, qqq_close, qqq_close, qqq_close, 1.0),
            InputRow("TQQQ", day, tqqq_open, max(tqqq_open, tqqq_close), min(tqqq_open, tqqq_close), tqqq_close, 1.0),
        ))
    return OfflineInput(
        tuple(rows),
        _canonical_bytes(rows),
        "8cc682b2d1acc23a8dd93c3bfd67b445d7305844d2c4d254f4f52e0ac817c6cb",
        "c" * 40,
    )


@contextmanager
def _synthetic_artifact_identity(source: OfflineInput):
    """Bind only this test's synthetic bytes; restore the real artifact pin."""
    with patch.object(optimization, "EXPECTED_ARTIFACT_SHA256", hashlib.sha256(source.canonical_bytes).hexdigest()):
        yield


def _without_risk_evidence(result):
    legacy = {
        key: value for key, value in result.items()
        if key != "mc_terminal_loss_probability_c2_5_evidence"
    }
    if "metrics" in legacy:
        legacy["metrics"] = {
            name: {
                window: {
                    key: value for key, value in metrics.items()
                    if key != "expected_shortfall_95_evidence"
                }
                for window, metrics in candidates.items()
            }
            for name, candidates in legacy["metrics"].items()
        }
    return legacy


@pytest.mark.parametrize(("count", "tail_count"), ((42, 3), (126, 7)))
def test_empirical_es_discloses_actual_ceiling_tail_and_signed_return_method(count, tail_count):
    returns = tuple((index - count // 2) / 10_000.0 for index in reversed(range(count)))
    equity = 100_000.0
    points = []
    for index, daily_return in enumerate(returns):
        start = equity
        equity *= 1.0 + daily_return
        points.append(optimization.DailyPoint(
            str(index), start, equity, equity, 0.0, daily_return, False, 0.0, 0.0, 0.0,
        ))
    metrics = optimization._window_metrics(points, 200, 200, 200 + count - 1)
    assert metrics["expected_shortfall_95"] == math.fsum(sorted(returns)[:tail_count]) / tail_count
    assert metrics["observation_count"] == count
    assert metrics["expected_shortfall_95_evidence"] == {
        "method": "EMPIRICAL_MEAN_OF_LOWEST_CEIL_N_TIMES_0_05_DAILY_RETURNS",
        "sign_convention": "SIGNED_DAILY_RETURN_LOWER_IS_WORSE_NOT_POSITIVE_LOSS",
        "observation_count": count,
        "nominal_tail_fraction": 0.05,
        "tail_observation_count": tail_count,
        "effective_tail_fraction": tail_count / count,
        "qualification": "FINITE_HISTORY_EMPIRICAL_TAIL_NOT_A_POPULATION_RISK_GUARANTEE",
    }
    assert metrics["expected_shortfall_95_evidence"]["effective_tail_fraction"] > 0.05


def test_empirical_es_keeps_positive_tail_returns_positive():
    points = [
        optimization.DailyPoint(str(index), 100_000.0, 101_000.0, 101_000.0, 0.0,
                                0.01, False, 0.0, 0.0, 0.0)
        for index in range(42)
    ]
    metrics = optimization._window_metrics(points, 200, 200, 241)
    assert metrics["expected_shortfall_95"] == 0.01
    assert metrics["expected_shortfall_95_evidence"]["tail_observation_count"] == 3


def test_mc_evidence_counts_paths_block_starts_and_return_steps_from_actual_inputs(monkeypatch):
    monkeypatch.setattr(optimization, "MC_TRIALS", 3)
    monkeypatch.setattr(optimization, "MC_PATH_LENGTH", 7)
    monkeypatch.setattr(optimization, "MC_BLOCK_LENGTH", 3)
    returns = (0.01,) * 7
    with patch.object(optimization, "_sample_index", return_value=6) as sample_index:
        assert optimization._terminal_loss_probability(returns) == 0.0
    assert sample_index.call_count == 9
    assert [call.args for call in sample_index.call_args_list] == [
        ("INDEPENDENT:TQQQ", trial, block, len(returns))
        for trial in range(3) for block in range(3)
    ]
    assert optimization._terminal_loss_probability_evidence(returns) == {
        "method": "CIRCULAR_MOVING_BLOCK_BOOTSTRAP",
        "source_history_observation_count": 7,
        "completed_bootstrap_path_count": 3,
        "path_length": 7,
        "block_length": 3,
        "blocks_per_path": 3,
        "sampled_block_start_count": sample_index.call_count,
        "resampled_return_step_count": 21,
        "qualification": "RESAMPLED_PATHS_DO_NOT_ADD_INDEPENDENT_HISTORICAL_OBSERVATIONS",
    }


def test_success_risk_evidence_reports_finite_holdout_history_separately_from_simulation_counts():
    source = _source()
    with _synthetic_artifact_identity(source):
        result = run_tqqq_core_optimization(source)
    assert result["evidence_valid"] is True
    evidence = result["mc_terminal_loss_probability_c2_5_evidence"]
    assert evidence["source_history_observation_count"] == 126
    assert evidence["completed_bootstrap_path_count"] == 10_000
    assert evidence["path_length"] == 126
    assert evidence["block_length"] == 12
    assert evidence["blocks_per_path"] == 11
    assert evidence["sampled_block_start_count"] == 110_000
    assert evidence["resampled_return_step_count"] == 1_260_000
    for name, candidates in result["metrics"].items():
        count, tail_count = (126, 7) if name == "FINAL_HOLDOUT" else (42, 3)
        for metrics in candidates.values():
            es = metrics["expected_shortfall_95_evidence"]
            assert es["observation_count"] == count
            assert es["tail_observation_count"] == tail_count
            assert es["effective_tail_fraction"] == tail_count / count


def test_frozen_candidates_plugin_and_windows() -> None:
    assert CANDIDATE_WINDOWS == (150, 200, 250)
    assert BASELINE_WINDOW_DAYS == 200
    assert PLUGIN_CONTROL == {"state": "ABSENT", "enabled": False, "optimization_eligible": False}
    assert [item[:3] for item in WINDOW_SPECS] == [
        ("F1_VALIDATION", 370, 411), ("F1_EMBARGO", 412, 412), ("F1_TEST", 413, 454),
        ("F2_VALIDATION", 456, 497), ("F2_EMBARGO", 498, 498), ("F2_TEST", 499, 540),
        ("F3_VALIDATION", 542, 583), ("F3_EMBARGO", 584, 584), ("F3_TEST", 585, 626),
        ("FINAL_HOLDOUT", 627, 752),
    ]
    assert [(item.scenario_id, item.commission_bps, item.slippage_bps) for item in SCENARIOS] == [
        ("ZERO", 0, 0), ("C1_2", 1, 2), ("C2_5", 2, 5), ("C5_10_STRESS", 5, 10),
    ]


def test_sma200_zero_is_bit_for_bit_baseline_and_cost_timing_is_next_open() -> None:
    source = _source()
    points = simulate_candidate(source, 200, SCENARIOS[0])
    baseline = run_typed_baseline(source)
    assert [(point.date, point.end_equity.hex(), point.cash.hex(), point.quantity.hex()) for point in points] == [
        (point.date, point.equity.hex(), point.cash.hex(), point.tqqq_quantity.hex()) for point in baseline.equity_curve
    ]
    costly = simulate_candidate(source, 200, SCENARIOS[2])
    assert costly[0].date == baseline.equity_curve[0].date
    assert costly[0].start_equity == 100_000.0
    assert costly[0].end_equity <= points[0].end_equity


def test_deterministic_repeatability_and_plugin_ineligible() -> None:
    source = _source()
    real_artifact_hash = optimization.EXPECTED_ARTIFACT_SHA256
    with _synthetic_artifact_identity(source):
        first = run_tqqq_core_optimization(source)
        second = run_tqqq_core_optimization(source)
    assert optimization.EXPECTED_ARTIFACT_SHA256 == real_artifact_hash
    assert first["evidence_valid"] is True
    assert first == second
    assert set(first["metrics"]) == {name for name, _, _ in WINDOW_SPECS if "EMBARGO" not in name}
    # This actual synthetic simulation has no unique improvement, so retain SMA200.
    assert first["locked_fold_candidates"] == [200, 200, 200]
    assert first["final_candidate"] == 200
    assert first["outcome"] == "NO_IMPROVEMENT"
    assert first["research_recommendation"] is None
    assert first["plugin_control"] == PLUGIN_CONTROL
    assert first["research_only"] is True
    assert first["live_adoption_authorized"] is False
    assert first["size_zero_required"] is True


@pytest.mark.parametrize(("fold_count", "changed_after"), ((1, 411), (2, 497), (3, 583)))
def test_actual_synthetic_validation_and_selection_ignore_later_rows(fold_count, changed_after) -> None:
    source = _source()
    changed = _source(changed_after=changed_after)
    with _synthetic_artifact_identity(source):
        original = run_tqqq_core_optimization(source)
    with _synthetic_artifact_identity(changed):
        perturbed = run_tqqq_core_optimization(changed)
    assert original["evidence_valid"] is True
    assert perturbed["evidence_valid"] is True
    # Later folds may use changed rows as past history; only already locked folds are isolated.
    assert original["locked_fold_candidates"][:fold_count] == perturbed["locked_fold_candidates"][:fold_count]
    if fold_count == 3:
        assert original["final_candidate"] == perturbed["final_candidate"]
    for name in ("F1_VALIDATION", "F2_VALIDATION", "F3_VALIDATION")[:fold_count]:
        assert original["metrics"][name] == perturbed["metrics"][name]
    # The changed suffix really changes evaluated test and holdout results.
    test_window = f"F{fold_count}_TEST"
    assert original["metrics"][test_window] != perturbed["metrics"][test_window]
    assert original["metrics"]["FINAL_HOLDOUT"] != perturbed["metrics"]["FINAL_HOLDOUT"]


@pytest.mark.parametrize(
    ("validation_winners", "holdout_winner", "test_return", "expected_outcome"),
    (
        ((150, 250, 150), 150, 0.1, "WINNER_RESEARCH_ONLY"),
        ((150, 150, 250), 250, 0.1, "WINNER_RESEARCH_ONLY"),
        ((150, 250, 150), 250, 0.1, "NO_IMPROVEMENT"),
        ((150, 250, 150), 150, -0.1, "NO_IMPROVEMENT"),
    ),
)
def test_prescribed_metrics_exercise_nonbaseline_selection_and_confirmation_control_flow(
    validation_winners, holdout_winner, test_return, expected_outcome,
) -> None:
    # Metrics and MC probability are prescribed, not numerical performance evidence.
    # Keep the real synthetic simulation and baseline-parity check in this path.
    def prescribed_metrics(points, window_days, raw_start, raw_end):
        if raw_start in (370, 456, 542):
            winner = validation_winners[(370, 456, 542).index(raw_start)]
            cumulative_return = 0.2 if window_days == winner else 0.1
        elif raw_start == 627:
            winner = holdout_winner
            cumulative_return = 0.2 if window_days == winner else 0.1
        else:
            selected = validation_winners[(413, 499, 585).index(raw_start)]
            cumulative_return = test_return if window_days == selected else 0.9
        return {
            "observation_count": raw_end - raw_start + 1,
            "cumulative_return": cumulative_return,
            "max_drawdown": -0.1,
            "annualized_volatility": 0.2,
            "expected_shortfall_95": -0.05,
            "trade_count": 0,
            "total_cost": 0.0,
        }

    source = _source()
    with _synthetic_artifact_identity(source), patch.object(
        optimization, "_window_metrics", side_effect=prescribed_metrics,
    ), patch.object(optimization, "_terminal_loss_probability", return_value=0.1) as loss_probability:
        result = run_tqqq_core_optimization(source)
    assert result["evidence_valid"] is True
    assert result["locked_fold_candidates"] == list(validation_winners)
    final_candidate = validation_winners[-1]
    assert result["final_candidate"] == final_candidate
    assert result["outcome"] == expected_outcome
    assert result["research_recommendation"] == (
        {"sma_window_days": final_candidate} if expected_outcome == "WINNER_RESEARCH_ONLY" else None
    )
    assert result["r3_eligibility_status"] == ("PASS" if test_return > 0.0 else "FAIL")
    loss_probability.assert_called_once()
    expected_final_returns = tuple(
        point.daily_return for point in simulate_candidate(source, final_candidate, SCENARIOS[2])[627 - final_candidate:]
    )
    assert len(expected_final_returns) == 126
    assert loss_probability.call_args.args[0] == expected_final_returns
    assert result["plugin_control"] == PLUGIN_CONTROL
    assert result["research_only"] is True
    assert result["live_adoption_authorized"] is False
    assert result["size_zero_required"] is True


def test_conservative_pareto_requires_one_strict_improvement_and_rejects_tradeoff() -> None:
    baseline = {"cumulative_return": 0.1, "max_drawdown": -0.2, "annualized_volatility": 0.3, "expected_shortfall_95": -0.1}
    dominates = {**baseline, "cumulative_return": 0.2}
    tradeoff = {**baseline, "cumulative_return": 0.2, "annualized_volatility": 0.4}
    assert _pareto_winner({150: dominates, 200: baseline, 250: tradeoff}) == 150
    assert _pareto_winner({150: tradeoff, 200: baseline, 250: dominates}) == 250
    assert _pareto_winner({150: tradeoff, 200: baseline, 250: baseline}) == 200


def test_r3_eligibility_is_unchanged_and_strict() -> None:
    assert _eligibility((0.1, 0.0, 0.2), 0.01, 0.01, 0.49) == ("PASS", ())
    status, failures = _eligibility((0.1, 0.0, -0.1), 0.0, 0.0, 0.5)
    assert status == "FAIL"
    assert len(failures) == 4


def test_invalid_evidence_fails_closed_without_recommendation_or_sizing() -> None:
    invalid = run_tqqq_core_optimization(None)
    assert invalid["outcome"] == "NO_IMPROVEMENT"
    assert invalid["research_recommendation"] is None
    assert invalid["size_zero_required"] is True
    assert invalid["failure_codes"]


def test_forged_rows_cannot_claim_the_blessed_digest_without_pinned_artifact_bytes() -> None:
    forged = _source()
    result = run_tqqq_core_optimization(forged)

    assert result["evidence_valid"] is False
    assert result["failure_codes"] == ["INPUT_ARTIFACT_SHA256_MISMATCH"]
    assert result["research_recommendation"] is None


def test_rows_must_match_their_canonical_bytes_before_the_pinned_hash_is_accepted() -> None:
    forged = replace(_source(), canonical_bytes=b"different canonical rows\n")
    result = run_tqqq_core_optimization(forged)

    assert result["evidence_valid"] is False
    assert result["failure_codes"] == ["INPUT_CANONICAL_BYTES_MISMATCH"]


def test_persistence_is_canonical_atomic_idempotent_and_strict(tmp_path) -> None:
    source = _source()
    with _synthetic_artifact_identity(source):
        result = run_tqqq_core_optimization(source)
        assert result["evidence_valid"] is True
        paths = persist_result(result, tmp_path, source_commit="c" * 40)
        bundle = paths.bundle.read_bytes()
        assert bundle.endswith(b"\n")
        assert paths.sidecar.read_text() == hashlib.sha256(bundle).hexdigest() + "\n"
        restored = load_persisted_result(tmp_path)
        assert restored == json.loads(bundle)
        assert restored["evidence_valid"] is True
        assert restored["locked_fold_candidates"] == result["locked_fold_candidates"]
        assert restored["final_candidate"] == result["final_candidate"]
        assert restored["r3_eligibility_status"] == result["r3_eligibility_status"]
        assert restored["mc_terminal_loss_probability_c2_5"] == result["mc_terminal_loss_probability_c2_5"].hex()
        for name, candidates in result["metrics"].items():
            for window, metrics in candidates.items():
                assert restored["metrics"][name][window] == optimization._wire(metrics)
        assert restored["mc_terminal_loss_probability_c2_5_evidence"] == optimization._wire(
            result["mc_terminal_loss_probability_c2_5_evidence"],
        )
        assert json.loads(paths.readback.read_bytes())["csv_sha256"] == hashlib.sha256(source.canonical_bytes).hexdigest()
        assert persist_result(result, tmp_path, source_commit="c" * 40).bundle.read_bytes() == bundle
        paths.bundle.write_bytes(b"different")
        with pytest.raises(ValueError):
            persist_result(result, tmp_path, source_commit="c" * 40)
        refusal = tmp_path / "refusal"
        refusal.mkdir()
        (refusal / "tqqq_core_optimization_v1.sha256").write_text("different\n")
        with pytest.raises(ValueError):
            persist_result(result, refusal, source_commit="c" * 40)
        assert not (refusal / "tqqq_core_optimization_v1.json").exists()


def test_forbidden_input_and_plugin_or_identity_mismatch_fail_closed() -> None:
    source = _source()
    assert run_tqqq_core_optimization(replace(source, input_digest="b" * 64))["evidence_valid"] is False
    assert run_tqqq_core_optimization(source, plugin_control={"state": "PRESENT"})["evidence_valid"] is False
    assert run_tqqq_core_optimization(source, expected_input_digest="b" * 64)["evidence_valid"] is False


def _assert_trial_manifest(result, source=None):
    manifest = result["trial_manifest"]
    assert set(manifest) == {"schema", "scope", "input_validation_stage", "input_linkage", "attempts"}
    assert manifest["schema"] == "qsl.research.tqqq_trial_manifest.v1"
    assert manifest["scope"] == "THIS_INVOCATION_SIMULATIONS_ONLY"
    if source is None:
        assert manifest["input_validation_stage"] == "CANONICAL_ARTIFACT_NOT_VERIFIED"
        assert manifest["input_linkage"] is None
    else:
        assert manifest["input_validation_stage"] == "CANONICAL_ARTIFACT_VERIFIED"
        assert manifest["input_linkage"] == {
            "accepted_input_digest": source.input_digest,
            "input_digest_semantics": "MATCHES_EXPECTED_LITERAL_ONLY",
            "verified_canonical_artifact_sha256": hashlib.sha256(source.canonical_bytes).hexdigest(),
            "verified_canonical_artifact_bytes": len(source.canonical_bytes),
        }
    expected = [(window, scenario) for window in CANDIDATE_WINDOWS for scenario in SCENARIOS]
    assert len(manifest["attempts"]) == len(expected) == 12
    for attempt, (window, scenario) in zip(manifest["attempts"], expected, strict=True):
        assert set(attempt) == {"window_days", "scenario_id", "commission_bps", "slippage_bps", "status", "reason_code"}
        assert (attempt["window_days"], attempt["scenario_id"]) == (window, scenario.scenario_id)
        assert (attempt["commission_bps"], attempt["slippage_bps"]) == (scenario.commission_bps, scenario.slippage_bps)
        assert attempt["status"] in {"not_started", "succeeded", "failed"}
        assert attempt["reason_code"] == (
            "CANDIDATE_SIMULATION_FAILED" if attempt["status"] == "failed" else None
        )
    return manifest


def _result_with_simulation_failure(failure_index, exception):
    source = _source()
    original = optimization.simulate_candidate
    calls = []

    def fail_one_call(input_source, window, scenario):
        index = len(calls)
        calls.append((window, scenario.scenario_id))
        if index == failure_index:
            raise exception
        return original(input_source, window, scenario)

    with _synthetic_artifact_identity(source), patch.object(
        optimization, "simulate_candidate", side_effect=fail_one_call,
    ):
        result = run_tqqq_core_optimization(source)
    return source, result, calls


@pytest.mark.parametrize("failure_index", range(12))
def test_every_simulation_failure_preserves_only_this_invocation_attempts(failure_index):
    source, result, calls = _result_with_simulation_failure(
        failure_index, optimization.OptimizationError("SIMULATION_EQUITY_INVALID"),
    )
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == (
        ["succeeded"] * failure_index + ["failed"] + ["not_started"] * (11 - failure_index)
    )
    assert len(calls) == failure_index + 1
    assert result["failure_codes"] == ["SIMULATION_EQUITY_INVALID"]
    assert result["evidence_valid"] is False
    assert result["research_recommendation"] is None
    assert result["live_adoption_authorized"] is False
    assert result["size_zero_required"] is True
    assert "metrics" not in result
    assert "mc_terminal_loss_probability_c2_5_evidence" not in result


@pytest.mark.parametrize("exception_type", [RuntimeError, optimization.OptimizationError])
def test_unexpected_exception_text_is_sanitized_in_return_and_persistence(tmp_path, exception_type):
    private_marker = "SYNTHETIC_PRIVATE_PROVIDER_DETAIL"
    source, result, calls = _result_with_simulation_failure(4, exception_type(private_marker))
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == ["succeeded"] * 4 + ["failed"] + ["not_started"] * 7
    assert len(calls) == 5
    assert result["failure_codes"] == ["OPTIMIZATION_FAILED"]
    assert private_marker not in json.dumps(result)
    with _synthetic_artifact_identity(source):
        paths = persist_result(result, tmp_path, source_commit="c" * 40)
        assert load_persisted_result(tmp_path)["trial_manifest"] == manifest
    assert all(private_marker.encode() not in path.read_bytes() for path in (paths.bundle, paths.sidecar, paths.readback))


@pytest.mark.parametrize("rejection", ["plugin", "expected_digest", "source_type", "asserted_digest", "canonical_bytes", "artifact_hash"])
def test_early_rejection_has_no_actual_input_linkage_or_started_simulations(rejection):
    source = _source()
    kwargs = {}
    if rejection == "plugin":
        kwargs["plugin_control"] = {"state": "PRESENT"}
    elif rejection == "expected_digest":
        kwargs["expected_input_digest"] = "b" * 64
    elif rejection == "source_type":
        source = None
    elif rejection == "asserted_digest":
        source = replace(source, input_digest="b" * 64)
    elif rejection == "canonical_bytes":
        source = replace(source, canonical_bytes=b"different canonical rows\n")
    with patch.object(optimization, "simulate_candidate") as simulator:
        result = run_tqqq_core_optimization(source, **kwargs)
    manifest = _assert_trial_manifest(result)
    assert [item["status"] for item in manifest["attempts"]] == ["not_started"] * 12
    assert result["evidence_valid"] is False
    assert result["research_recommendation"] is None
    simulator.assert_not_called()


@pytest.mark.parametrize(("helper", "exception", "failure_code"), [
    ("run_typed_baseline", RuntimeError("SYNTHETIC_PRIVATE_BASELINE_DETAIL"), "OPTIMIZATION_FAILED"),
    ("_window_metrics", optimization.OptimizationError("WINDOW_BOUNDARY_INVALID"), "WINDOW_BOUNDARY_INVALID"),
    ("_terminal_loss_probability", optimization.OptimizationError("MONTE_CARLO_INPUT_INVALID"), "MONTE_CARLO_INPUT_INVALID"),
    ("_five_metric_winner", RuntimeError("SYNTHETIC_PRIVATE_SELECTION_DETAIL"), "OPTIMIZATION_FAILED"),
])
def test_postprocessing_failure_keeps_successful_simulations_without_qualification(helper, exception, failure_code):
    source = _source()
    with _synthetic_artifact_identity(source), patch.object(optimization, helper, side_effect=exception):
        result = run_tqqq_core_optimization(source)
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == ["succeeded"] * 12
    assert result["evidence_valid"] is False
    assert result["failure_codes"] == [failure_code]
    assert result["research_recommendation"] is None
    assert result["live_adoption_authorized"] is False
    assert "SYNTHETIC_PRIVATE" not in json.dumps(result)
    assert "mc_terminal_loss_probability_c2_5_evidence" not in result


def test_manifest_is_deterministic_and_independent_between_invocations():
    source, first, _ = _result_with_simulation_failure(3, RuntimeError("synthetic failure"))
    _, second, _ = _result_with_simulation_failure(3, RuntimeError("synthetic failure"))
    assert first == second
    assert first["trial_manifest"] is not second["trial_manifest"]
    assert first["trial_manifest"]["attempts"] is not second["trial_manifest"]["attempts"]
    first["trial_manifest"]["attempts"][0]["reason_code"] = "synthetic mutation"
    first["trial_manifest"]["input_linkage"]["accepted_input_digest"] = "synthetic mutation"
    _assert_trial_manifest(second, source)


def test_success_manifest_does_not_change_any_legacy_result_field_or_claim_source_revision():
    source = replace(_source(), source_revision="SYNTHETIC_UNVERIFIED_SOURCE_REVISION")
    with _synthetic_artifact_identity(source):
        result = run_tqqq_core_optimization(source)
    manifest = _assert_trial_manifest(result, source)
    assert [item["status"] for item in manifest["attempts"]] == ["succeeded"] * 12
    assert source.source_revision not in json.dumps(result)
    legacy_projection = {
        key: value for key, value in _without_risk_evidence(result).items()
        if key != "trial_manifest"
    }
    # Frozen from the reviewed pre-patch source and this same synthetic fixture.
    raw = optimization._canonical_bytes(optimization._wire(legacy_projection))
    assert hashlib.sha256(raw).hexdigest() == "5fa3152c416f0631045cfa448d7bb5ec2758b8edda781752b9a05331d8f6c89c"


def test_early_rejection_readback_pins_are_expected_identity_not_verified_input(tmp_path):
    result = run_tqqq_core_optimization(None)
    _assert_trial_manifest(result)
    paths = persist_result(result, tmp_path, source_commit="c" * 40)
    restored = load_persisted_result(tmp_path)
    _assert_trial_manifest(restored)
    readback = json.loads(paths.readback.read_bytes())
    assert readback["csv_sha256"] == optimization.EXPECTED_ARTIFACT_SHA256
    assert readback["manifest_sha256"] == optimization.EXPECTED_MANIFEST_SHA256
    assert readback["typed_digest"] == optimization.EXPECTED_INPUT_DIGEST
    assert restored["trial_manifest"]["input_linkage"] is None


@pytest.mark.parametrize("include_trial_manifest", (False, True))
def test_legacy_v1_persistence_remains_readable_and_cannot_be_overwritten_by_new_metadata(tmp_path, include_trial_manifest):
    source = _source()
    with _synthetic_artifact_identity(source):
        current = run_tqqq_core_optimization(source)
        _assert_trial_manifest(current, source)
        legacy = _without_risk_evidence(current)
        if not include_trial_manifest:
            legacy.pop("trial_manifest")
        paths = persist_result(legacy, tmp_path, source_commit="c" * 40)
        original = {path: path.read_bytes() for path in (paths.bundle, paths.sidecar, paths.readback)}
        restored = load_persisted_result(tmp_path)
        assert ("trial_manifest" in restored) is include_trial_manifest
        assert "mc_terminal_loss_probability_c2_5_evidence" not in restored
        assert all(
            "expected_shortfall_95_evidence" not in metrics
            for candidates in restored["metrics"].values() for metrics in candidates.values()
        )
        assert restored == optimization._wire(legacy)
        with pytest.raises(optimization.OptimizationError, match="EXISTING_DIFFERENT_BYTES"):
            persist_result(current, tmp_path, source_commit="c" * 40)
    assert all(path.read_bytes() == raw for path, raw in original.items())


@pytest.mark.parametrize("target", ("expected_shortfall", "monte_carlo"))
def test_risk_evidence_metadata_is_bound_by_existing_persistence_digests(tmp_path, target):
    source = _source()
    with _synthetic_artifact_identity(source):
        result = run_tqqq_core_optimization(source)
        paths = persist_result(result, tmp_path, source_commit="c" * 40)
        parsed = json.loads(paths.bundle.read_bytes())
        if target == "expected_shortfall":
            parsed["metrics"]["F1_VALIDATION"]["200"]["expected_shortfall_95_evidence"]["tail_observation_count"] = 2
        else:
            parsed["mc_terminal_loss_probability_c2_5_evidence"]["source_history_observation_count"] = 10_000
        tampered = optimization._canonical_bytes(parsed)
        paths.bundle.write_bytes(tampered)
        paths.sidecar.write_text(hashlib.sha256(tampered).hexdigest() + "\n")
        with pytest.raises(optimization.OptimizationError, match="READBACK_MISMATCH"):
            load_persisted_result(tmp_path)


def test_trial_manifest_metadata_is_bound_by_existing_persistence_digests(tmp_path):
    source, result, _ = _result_with_simulation_failure(2, RuntimeError("synthetic failure"))
    with _synthetic_artifact_identity(source):
        paths = persist_result(result, tmp_path, source_commit="c" * 40)
        parsed = json.loads(paths.bundle.read_bytes())
        parsed["trial_manifest"]["attempts"][2]["status"] = "succeeded"
        tampered = optimization._canonical_bytes(parsed)
        paths.bundle.write_bytes(tampered)
        paths.sidecar.write_text(hashlib.sha256(tampered).hexdigest() + "\n")
        with pytest.raises(optimization.OptimizationError, match="READBACK_MISMATCH"):
            load_persisted_result(tmp_path)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_process_control_exceptions_are_not_converted_to_completed_accounting(exception_type):
    source = _source()
    with _synthetic_artifact_identity(source), patch.object(
        optimization, "simulate_candidate", side_effect=exception_type("synthetic interruption"),
    ), pytest.raises(exception_type):
        run_tqqq_core_optimization(source)


def _metric_source(*, cash_only=False, flat=False):
    """Closed synthetic buy/hold path: -10%, zero, +5%, then zeros."""
    rows = []
    close = 50.0
    for index in range(753):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        qqq = 1000.0 - index if cash_only else 100.0
        opening = close
        if not flat:
            close *= {370: 0.9, 372: 1.05}.get(index, 1.0)
        rows.extend((InputRow("QQQ", day, qqq, qqq, qqq, qqq, 1.0),
                     InputRow("TQQQ", day, opening, max(opening, close),
                              min(opening, close), close, 1.0)))
    return OfflineInput(tuple(rows), _canonical_bytes(rows),
                        optimization.EXPECTED_INPUT_DIGEST, "c" * 40)


def _metric_options(**overrides):
    return {"include_synthetic_metrics": True, "synthetic": True,
            "annual_risk_free_rate": 0.02,
            "annual_minimum_acceptable_return": 0.03, **overrides}


def test_optimizer_actual_opt_in_reports_every_path_without_resimulation_or_selection_change(monkeypatch):
    source = _source()
    monkeypatch.setattr(optimization, "_terminal_loss_probability", lambda returns: 0.0)
    with _synthetic_artifact_identity(source):
        original = run_tqqq_core_optimization(source)
        calls = []
        def counted(src, window, scenario):
            calls.append((window, scenario.scenario_id))
            return simulate_candidate(src, window, scenario)
        monkeypatch.setattr(optimization, "simulate_candidate", counted)
        current = run_tqqq_core_optimization(source, **_metric_options())
    report = current.pop("synthetic_metric_report")
    assert current == original
    assert calls == [(window, scenario.scenario_id)
                     for window in CANDIDATE_WINDOWS for scenario in SCENARIOS]
    assert report["status"] == "COMPUTED"
    assert report["selection_uses_report"] is False
    assert report["source_attempts"] == original["trial_manifest"]["attempts"]
    assert report["synthetic"] and not report["data_qualified"]
    expected_windows = {name for name, _, _ in WINDOW_SPECS if "EMBARGO" not in name} | {"FULL_PATH"}
    for candidate in CANDIDATE_WINDOWS:
        assert set(report["windows"][str(candidate)]) == {s.scenario_id for s in SCENARIOS}
        for scenario in SCENARIOS:
            reports = report["windows"][str(candidate)][scenario.scenario_id]
            assert set(reports) == expected_windows
            full = reports["FULL_PATH"]
            assert full["observation_count"] == 753 - candidate
            for name, start, end in WINDOW_SPECS:
                if "EMBARGO" in name:
                    continue
                row = reports[name]
                assert row["observation_count"] == end - start + 1
                contract = row["evaluation_contract"]
                assert contract["version"] == "synthetic_account_metrics_v1"
                assert contract["periods_per_year"] == 252
                assert contract["volatility_ddof"] == 1
                assert contract["rf"]["annual_simple_decimal"] == 0.02
                assert contract["mar"]["annual_simple_decimal"] == 0.03
                assert contract["initial_nav_in_drawdown"] is True
                assert contract["cost"]["net_nav_costs_deducted_again"] is False
                # Same original NAV path and session slice, irrespective of the
                # legacy risk-ratio definition or parameter selection.
                if scenario.scenario_id == "C2_5":
                    legacy = original["metrics"][name][str(candidate)]
                    assert row["metrics"]["cumulative_return"] == pytest.approx(legacy["cumulative_return"])
                    assert row["metrics"]["max_drawdown"] == pytest.approx(legacy["max_drawdown"])
    json.dumps(report, allow_nan=False)


def test_optimizer_report_hand_calculated_loss_initial_nav_cost_and_distinct_rf_mar(monkeypatch):
    source = _metric_source()
    monkeypatch.setattr(optimization, "_terminal_loss_probability", lambda returns: 0.0)
    with _synthetic_artifact_identity(source):
        current = run_tqqq_core_optimization(source, **_metric_options())
    reports = current["synthetic_metric_report"]["windows"]["200"]
    row = reports["ZERO"]["F1_VALIDATION"]
    returns = [-0.1, 0.0, 0.05] + [0.0] * 39
    mean = math.fsum(returns) / 42
    std = math.sqrt(math.fsum((r - mean) ** 2 for r in returns) / 41)
    rms = math.sqrt(math.fsum(min(r - 0.03 / 252, 0.0) ** 2 for r in returns) / 42)
    metrics = row["metrics"]
    assert metrics["cumulative_return"] == pytest.approx(-0.055)
    assert metrics["cagr"] == pytest.approx(0.945 ** 6 - 1)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["annualized_volatility"] == pytest.approx(std * math.sqrt(252))
    assert metrics["sharpe"] == pytest.approx((mean - 0.02 / 252) / std * math.sqrt(252))
    assert metrics["sortino"] == pytest.approx((mean - 0.03 / 252) / rms * math.sqrt(252))
    net_full = reports["C2_5"]["FULL_PATH"]["metrics"]
    # Adverse-fill slippage plus commission was charged on the entry only.
    # The metric consumes that net NAV once, not a second fee subtraction.
    assert net_full["cumulative_return"] == pytest.approx(0.945 / (1.0005 * 1.0002) - 1)
    assert net_full["max_drawdown"] == pytest.approx(0.9 / (1.0005 * 1.0002) - 1)


@pytest.mark.parametrize("cash_only", [True, False])
def test_optimizer_report_keeps_cash_zeros_and_constant_ratios_undefined(monkeypatch, cash_only):
    source = _metric_source(cash_only=cash_only, flat=True)
    monkeypatch.setattr(optimization, "_terminal_loss_probability", lambda returns: 0.0)
    with _synthetic_artifact_identity(source):
        report = run_tqqq_core_optimization(source, **_metric_options())["synthetic_metric_report"]
    row = report["windows"]["200"]["ZERO"]["FULL_PATH"]
    assert row["observation_count"] == 553
    assert row["metrics"]["cumulative_return"] == 0.0
    assert row["metrics"]["max_drawdown"] == 0.0
    assert row["metrics"]["annualized_volatility"] == 0.0
    assert row["metrics"]["sharpe"] is row["metrics"]["sortino"] is None
    assert row["metric_status"]["sharpe"] == "CONSTANT_RETURN_SAMPLE"
    assert row["metric_status"]["sortino"] == "CONSTANT_RETURN_SAMPLE"
    assert row["metrics"]["dsr"] is row["metrics"]["pbo"] is None
    if cash_only:
        for cost_reports in report["windows"]["200"].values():
            assert cost_reports["FULL_PATH"]["metrics"]["cumulative_return"] == 0.0


@pytest.mark.parametrize("overrides,reason", [
    ({"synthetic": False}, "REAL_RESEARCH_DATA_UNQUALIFIED"),
    ({"annual_risk_free_rate": None}, "RESEARCH_RATE_INVALID"),
    ({"annual_minimum_acceptable_return": float("nan")}, "RESEARCH_RATE_INVALID"),
    ({"annual_risk_free_rate": float("inf")}, "RESEARCH_RATE_INVALID"),
    ({"annual_risk_free_rate": True}, "RESEARCH_RATE_INVALID"),
    ({"include_synthetic_metrics": 1}, "RESEARCH_METRIC_OPT_IN_INVALID"),
    ({"include_synthetic_metrics": False}, "RESEARCH_METRIC_OPT_IN_REQUIRED"),
])
def test_optimizer_report_invalid_opt_in_rejected_before_simulation(monkeypatch, overrides, reason):
    monkeypatch.setattr(optimization, "simulate_candidate", lambda *a: pytest.fail("unexpected simulation"))
    with pytest.raises(ValueError, match=reason):
        run_tqqq_core_optimization(_source(), **_metric_options(**overrides))


def test_optimizer_report_keeps_failed_and_unattempted_source_slots(monkeypatch):
    source = _source()
    calls = []
    def failing(src, window, scenario):
        calls.append((window, scenario.scenario_id))
        if len(calls) == 2:
            raise ValueError("synthetic failure")
        return simulate_candidate(src, window, scenario)
    monkeypatch.setattr(optimization, "simulate_candidate", failing)
    with _synthetic_artifact_identity(source):
        result = run_tqqq_core_optimization(source, **_metric_options())
    report = result["synthetic_metric_report"]
    assert result["evidence_valid"] is False
    assert report["status"] == "NOT_COMPUTED"
    assert report["windows"] == {}
    assert [a["status"] for a in report["source_attempts"]] == ["succeeded", "failed"] + ["not_started"] * 10


def test_optimizer_report_accounting_failure_retains_frozen_evaluation(monkeypatch):
    source = _source()
    monkeypatch.setattr(optimization, "_terminal_loss_probability", lambda returns: 0.0)
    with _synthetic_artifact_identity(source):
        original = run_tqqq_core_optimization(source)
        def invalid(*args):
            raise ValueError("synthetic accounting mismatch")
        monkeypatch.setattr(optimization, "_synthetic_candidate_metric_reports", invalid)
        result = run_tqqq_core_optimization(source, **_metric_options())
    report = result.pop("synthetic_metric_report")
    assert result == original
    assert report["status"] == "FAILED"
    assert report["reason_code"] == "METRIC_REPORT_ACCOUNTING_INVALID"
    assert report["windows"] == {}
