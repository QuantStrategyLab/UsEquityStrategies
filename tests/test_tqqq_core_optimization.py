from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json

import pytest

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


def _source(count: int = 753) -> OfflineInput:
    rows: list[InputRow] = []
    for index in range(count):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        qqq_close = 100.0 + (index % 31)
        tqqq_open = 50.0 + (index % 7)
        tqqq_close = tqqq_open * (1.0 + ((index % 5) - 2) / 100.0)
        rows.extend((
            InputRow("QQQ", day, qqq_close, qqq_close, qqq_close, qqq_close, 1.0),
            InputRow("TQQQ", day, tqqq_open, max(tqqq_open, tqqq_close), min(tqqq_open, tqqq_close), tqqq_close, 1.0),
        ))
    return OfflineInput(tuple(rows), b"test", "8cc682b2d1acc23a8dd93c3bfd67b445d7305844d2c4d254f4f52e0ac817c6cb", "c" * 40)


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
    first = run_tqqq_core_optimization(source)
    second = run_tqqq_core_optimization(source)
    assert first == second
    assert first["plugin_control"] == PLUGIN_CONTROL
    assert first["research_only"] is True
    assert first["live_adoption_authorized"] is False
    assert first["size_zero_required"] is True


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


def test_persistence_is_canonical_atomic_idempotent_and_strict(tmp_path) -> None:
    result = run_tqqq_core_optimization(_source())
    paths = persist_result(result, tmp_path, source_commit="c" * 40)
    bundle = paths.bundle.read_bytes()
    assert bundle.endswith(b"\n")
    assert paths.sidecar.read_text() == hashlib.sha256(bundle).hexdigest() + "\n"
    assert load_persisted_result(tmp_path) == json.loads(bundle)
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
