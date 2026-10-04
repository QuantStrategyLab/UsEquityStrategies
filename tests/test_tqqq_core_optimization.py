from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
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
                for metric, value in metrics.items():
                    assert restored["metrics"][name][window][metric] == (value.hex() if type(value) is float else value)
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
