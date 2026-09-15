from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from us_equity_strategies.research.soxl_soxx_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import run_typed_baseline
from us_equity_strategies.research.soxl_core_optimization import (
    BASELINE_WINDOW_DAYS,
    DailyPoint,
    RSI2_MEAN_REVERSION_CANDIDATES,
    RSI2_MEAN_REVERSION_PLUGIN_CONTROL,
    RSI2_MEAN_REVERSION_SCHEMA,
    SCENARIOS,
    OptimizationError,
    _rsi2_values,
    _rsi2_research_target,
    _rsi2_buy_and_hold_metrics,
    _rsi2_metrics_with_unified_soxx,
    _rsi2_wfa_qualifying_count,
    _select_rsi2_mean_reversion_winner,
    load_persisted_rsi2_mean_reversion_result,
    persist_rsi2_mean_reversion_result,
    run_soxl_rsi2_mean_reversion,
    simulate_candidate,
    simulate_rsi2_mean_reversion_candidate,
)


def _canonical(rows: list[InputRow]) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for row in rows:
        lines.append(",".join((row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume)))))
    return ("\n".join(lines) + "\n").encode()


def _source(closes: list[float] | None = None) -> OfflineInput:
    closes = closes or [100.0 + index / 10.0 for index in range(753)]
    rows: list[InputRow] = []
    for index, soxx_close in enumerate(closes):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        soxl_open = 50.0 + index % 7
        soxl_close = soxl_open * (1.0 + ((index % 5) - 2) / 100.0)
        rows.extend((
            InputRow("SOXL", day, soxl_open, max(soxl_open, soxl_close), min(soxl_open, soxl_close), soxl_close, 1.0),
            InputRow("SOXX", day, soxx_close, soxx_close, soxx_close, soxx_close, 1.0),
        ))
    return OfflineInput(tuple(rows), _canonical(rows), "a" * 64, "fixture_v1")


def _provenance_repo(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    repo = tmp_path / "source"
    repo.mkdir()
    def git(*args: str) -> str:
        return subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    paths = (
        "src/us_equity_strategies/research/soxl_core_optimization.py",
        "src/us_equity_strategies/research/soxl_soxx_offline_input_contract.py",
        "src/us_equity_strategies/research/soxl_soxx_typed_baseline_result.py",
    )
    for relative in paths:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n")
    git("add", ".")
    git("commit", "-qm", "fixture")
    head = git("rev-parse", "HEAD")
    return repo, head, {relative: git("rev-parse", f"HEAD:{relative}") for relative in paths}


def test_frozen_candidates_plugin_and_exact_rsi2_edges() -> None:
    assert RSI2_MEAN_REVERSION_CANDIDATES == (
        "UNSCALED_SMA200", "RSI2_ENTRY_5_EXIT_70", "RSI2_ENTRY_10_EXIT_70", "RSI2_ENTRY_15_EXIT_70",
    )
    assert RSI2_MEAN_REVERSION_PLUGIN_CONTROL == {"state": "ABSENT_DISABLED", "enabled": False, "optimization_eligible": False}
    assert _rsi2_values((100.0, 100.0, 100.0))[2] == 50.0
    assert _rsi2_values((100.0, 101.0, 102.0))[2] == 100.0
    assert _rsi2_values((102.0, 101.0, 100.0))[2] == 0.0
    values = _rsi2_values((100.0, 102.0, 101.0, 103.0))
    assert values[3] == pytest.approx(85.71428571428571)


def test_rsi2_research_target_preserves_entry_exit_edges() -> None:
    prior_closes = (100.0, 101.0, 99.0)
    cases = (
        (False, None, 5.0, False),
        (False, 5.0, 5.0, True),
        (False, 5.0001, 5.0, False),
        (True, None, 5.0, True),
        (True, 69.9999, 5.0, True),
        (True, 70.0, 5.0, False),
    )
    for held, lagged_rsi, entry_threshold, expected in cases:
        actual = _rsi2_research_target(
            held=held,
            lagged_rsi=lagged_rsi,
            entry_threshold=entry_threshold,
            prior_closes=prior_closes,
        )
        assert type(actual) is bool
        assert actual is expected


def test_rsi2_research_target_rejects_non_bool_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "us_equity_strategies.research.soxl_core_optimization._rsi2_research_target",
        lambda **_: 1,
    )
    with pytest.raises(OptimizationError, match="RSI2_TARGET_INVALID"):
        simulate_rsi2_mean_reversion_candidate(_source(), "RSI2_ENTRY_5_EXIT_70", SCENARIOS[0])


def test_unscaled_parity_strict_trend_and_next_open_no_lookahead() -> None:
    source = _source()
    for scenario in SCENARIOS:
        actual = simulate_rsi2_mean_reversion_candidate(source, "UNSCALED_SMA200", scenario)
        expected = simulate_candidate(source, BASELINE_WINDOW_DAYS, scenario)
        assert [(p.date, p.end_equity.hex(), p.cash.hex(), p.quantity.hex()) for p in actual] == [(p.date, p.end_equity.hex(), p.cash.hex(), p.quantity.hex()) for p in expected]
    equality = _source([100.0] * 753)
    assert all(point.quantity == 0.0 for point in simulate_rsi2_mean_reversion_candidate(equality, "UNSCALED_SMA200", SCENARIOS[0]))
    original = simulate_rsi2_mean_reversion_candidate(source, "RSI2_ENTRY_15_EXIT_70", SCENARIOS[2])
    rows = list(source.rows)
    execution = 250 * 2
    row = rows[execution]
    rows[execution] = InputRow(row.symbol, row.as_of, row.open, max(row.open, row.close * 3.0), min(row.open, row.close * 3.0), row.close * 3.0, row.volume)
    changed = OfflineInput(tuple(rows), _canonical(rows), source.input_digest, source.source_revision)
    assert original[50].quantity.hex() == simulate_rsi2_mean_reversion_candidate(changed, "RSI2_ENTRY_15_EXIT_70", SCENARIOS[2])[50].quantity.hex()


def test_rsi_state_machine_costs_and_exposure_bounds() -> None:
    closes = [100.0 + index / 10.0 for index in range(200)] + [118.0, 117.0, 116.0, 117.0, 119.0] + [120.0 + index / 10.0 for index in range(548)]
    source = _source(closes)
    for candidate in RSI2_MEAN_REVERSION_CANDIDATES[1:]:
        for scenario in SCENARIOS:
            points = simulate_rsi2_mean_reversion_candidate(source, candidate, scenario)
            assert all(point.cash >= 0.0 and point.quantity >= 0.0 for point in points)
            assert all(point.quantity == 0.0 or point.cash == 0.0 for point in points)
            assert any(point.transition for point in points)


def test_validation_selection_tie_and_runner_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    metric = {"max_drawdown": -0.1, "expected_shortfall_95": -0.02, "annualized_volatility": 0.2, "cagr": 0.1, "turnover": 1.0, "activity_observed": True}
    validation = {candidate: [metric] * 3 for candidate in RSI2_MEAN_REVERSION_CANDIDATES}
    assert _select_rsi2_mean_reversion_winner(validation) == "UNSCALED_SMA200"
    monkeypatch.setattr("us_equity_strategies.research.soxl_core_optimization._terminal_loss_probability", lambda _: 0.0)
    result = run_soxl_rsi2_mean_reversion(_source())
    assert result["schema"] == RSI2_MEAN_REVERSION_SCHEMA
    assert result["research_only"] is True and result["live_adoption_authorized"] is False and result["size_zero_required"] is True
    assert set(result["post_lock_metrics"]) <= {result["locked_winner"], "UNSCALED_SMA200"}
    assert all(set(metrics) == {"FINAL_HOLDOUT"} for metrics in result["post_lock_metrics"].values())
    assert [item["validation_windows"] for item in result["wfa_fold_metrics"]] == [
        ["F1_VALIDATION"],
        ["F1_VALIDATION", "F2_VALIDATION"],
        ["F1_VALIDATION", "F2_VALIDATION", "F3_VALIDATION"],
    ]
    assert result["outcome"] == "NO_IMPROVEMENT"
    assert result["acceptance_classes"]["benchmark_relative_compounding"]["eligible"] is False
    assert "FINAL_HOLDOUT_BENCHMARK_RELATIVE_COMPOUNDING" in result["failure_codes"]


def test_rsi2_selection_accepts_only_available_validation_prefix() -> None:
    baseline = {"max_drawdown": -0.2, "expected_shortfall_95": -0.1, "cagr": 0.1, "turnover": 1.0, "activity_observed": True}
    better = {"max_drawdown": -0.1, "expected_shortfall_95": -0.05, "cagr": 0.2, "turnover": 1.0, "activity_observed": True}
    validation = {
        "UNSCALED_SMA200": [baseline],
        "RSI2_ENTRY_5_EXIT_70": [better],
        "RSI2_ENTRY_10_EXIT_70": [baseline],
        "RSI2_ENTRY_15_EXIT_70": [baseline],
    }
    assert _select_rsi2_mean_reversion_winner(validation) == "RSI2_ENTRY_5_EXIT_70"

    validation = {candidate: [baseline, baseline] for candidate in RSI2_MEAN_REVERSION_CANDIDATES}
    validation["RSI2_ENTRY_10_EXIT_70"] = [better, better]
    assert _select_rsi2_mean_reversion_winner(validation) == "RSI2_ENTRY_10_EXIT_70"

    invalid = {candidate: [baseline] for candidate in RSI2_MEAN_REVERSION_CANDIDATES}
    invalid["RSI2_ENTRY_10_EXIT_70"] = [baseline, baseline]
    with pytest.raises(OptimizationError, match="VALIDATION_METRICS_INVALID"):
        _select_rsi2_mean_reversion_winner(invalid)


def test_future_validation_cannot_rewrite_earlier_fold_selection() -> None:
    baseline = {"max_drawdown": -0.2, "expected_shortfall_95": -0.1, "cagr": 0.1, "turnover": 1.0, "activity_observed": True}
    better_a = {"max_drawdown": -0.1, "expected_shortfall_95": -0.05, "cagr": 0.2, "turnover": 1.0, "activity_observed": True}
    better_b = {"max_drawdown": -0.1, "expected_shortfall_95": -0.05, "cagr": 0.3, "turnover": 1.0, "activity_observed": True}
    validation = {
        "UNSCALED_SMA200": [baseline, baseline, baseline],
        "RSI2_ENTRY_5_EXIT_70": [better_a, {**baseline, "cagr": 0.0}, {**baseline, "cagr": 0.0}],
        "RSI2_ENTRY_10_EXIT_70": [baseline, better_b, better_b],
        "RSI2_ENTRY_15_EXIT_70": [baseline, baseline, baseline],
    }
    assert _select_rsi2_mean_reversion_winner({candidate: metrics[:1] for candidate, metrics in validation.items()}) == "RSI2_ENTRY_5_EXIT_70"
    assert _select_rsi2_mean_reversion_winner(validation) == "RSI2_ENTRY_10_EXIT_70"


def test_runner_uses_each_fold_winner_for_wfa_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    import us_equity_strategies.research.soxl_core_optimization as optimization

    calls: list[int] = []
    winners = {1: "RSI2_ENTRY_5_EXIT_70", 2: "RSI2_ENTRY_15_EXIT_70", 3: "RSI2_ENTRY_10_EXIT_70"}

    def select(validation: dict[str, list[dict[str, object]]]) -> str:
        lengths = {len(metrics) for metrics in validation.values()}
        assert len(lengths) == 1
        length = next(iter(lengths))
        calls.append(length)
        return winners[length]

    monkeypatch.setattr(optimization, "_select_rsi2_mean_reversion_winner", select)
    monkeypatch.setattr(optimization, "_terminal_loss_probability", lambda _: 0.0)
    result = run_soxl_rsi2_mean_reversion(_source())

    assert calls == [3, 1, 2, 3]
    assert result["locked_winner"] == "RSI2_ENTRY_10_EXIT_70"
    assert result["wfa_fold_winners"] == ["RSI2_ENTRY_5_EXIT_70", "RSI2_ENTRY_15_EXIT_70", "RSI2_ENTRY_10_EXIT_70"]
    assert result["wfa_fold_metrics"][0]["selected_candidate"] == "RSI2_ENTRY_5_EXIT_70"
    assert set(result["post_lock_metrics"][result["locked_winner"]]) == {"FINAL_HOLDOUT"}
    assert run_soxl_rsi2_mean_reversion(_source(), plugin_control={"state": "ABSENT_DISABLED"})["evidence_valid"] is False
    assert run_soxl_rsi2_mean_reversion(None)["evidence_valid"] is False


def test_persistence_set_once_strict_readback_and_symlink_rejection(tmp_path: Path) -> None:
    repo, head, blobs = _provenance_repo(tmp_path)
    result = {"schema": RSI2_MEAN_REVERSION_SCHEMA, "value": 1.0}
    output = tmp_path / "out"
    paths = persist_rsi2_mean_reversion_result(result, output, source_commit=head, source_blobs=blobs, repo_root=repo)
    bundle = paths.bundle.read_bytes()
    assert paths.bundle.name == "soxl_rsi2_mean_reversion_v1.json"
    assert paths.sidecar.read_text() == hashlib.sha256(bundle).hexdigest() + "\n"
    assert load_persisted_rsi2_mean_reversion_result(output) == json.loads(bundle)
    assert persist_rsi2_mean_reversion_result(result, output, source_commit=head, source_blobs=blobs, repo_root=repo).bundle.read_bytes() == bundle
    paths.bundle.write_bytes(b"different")
    with pytest.raises(OptimizationError, match="EXISTING_DIFFERENT_BYTES"):
        persist_rsi2_mean_reversion_result(result, output, source_commit=head, source_blobs=blobs, repo_root=repo)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "soxl_rsi2_mean_reversion_v1.json").symlink_to(tmp_path / "outside")
    with pytest.raises(OptimizationError, match="OUTPUT_PATH_INVALID"):
        persist_rsi2_mean_reversion_result(result, linked, source_commit=head, source_blobs=blobs, repo_root=repo)


def test_rsi2_acceptance_soxx_path_includes_prior_close_for_return_cagr_and_drawdown() -> None:
    points = (
        DailyPoint("2024-01-01", 100.0, 101.0, 0.0, 1.0, 0.01, False, 0.0, 0.0, 0.0),
        DailyPoint("2024-01-02", 101.0, 102.0, 0.0, 1.0, 1.0 / 101.0, False, 0.0, 0.0, 0.0),
    )
    soxx = tuple(InputRow("SOXX", f"2024-01-{index + 1:02d}", close, close, close, close, 1.0) for index, close in enumerate([100.0] * 199 + [120.0, 110.0, 115.0]))

    metrics = _rsi2_metrics_with_unified_soxx(points, soxx, 200, 201)

    assert metrics["soxx_close_path_cumulative_return"] == pytest.approx(115.0 / 120.0 - 1.0)
    assert metrics["soxx_close_path_cagr"] == pytest.approx((115.0 / 120.0) ** (252.0 / 2.0) - 1.0)
    assert metrics["soxx_close_path_max_drawdown"] == pytest.approx(110.0 / 120.0 - 1.0)
    assert metrics["observation_count"] == 2
    rising_soxx = tuple(InputRow("SOXX", f"2024-03-{index + 1:02d}", close, close, close, close, 1.0) for index, close in enumerate([100.0] * 199 + [100.0, 110.0, 115.0]))
    rising = _rsi2_metrics_with_unified_soxx(points, rising_soxx, 200, 201)
    assert rising["soxx_close_path_cumulative_return"] == pytest.approx(0.15)
    assert rising["soxx_close_path_cagr"] == pytest.approx(1.15 ** (252.0 / 2.0) - 1.0)


def test_rsi2_buy_and_hold_benchmark_starts_in_cash_and_pays_one_entry_cost() -> None:
    prefix = tuple(InputRow("SOXL", f"2024-04-{index + 1:03d}", 1.0, 1.0, 1.0, 1.0, 1.0) for index in range(BASELINE_WINDOW_DAYS))
    window = tuple(
        InputRow("SOXL", f"2025-01-{index + 1:02d}", opening, opening, opening, close, 1.0)
        for index, (opening, close) in enumerate(((10.0, 10.0), (20.0, 20.0), (30.0, 15.0)))
    )
    rows = prefix + window
    metrics = _rsi2_buy_and_hold_metrics(rows, BASELINE_WINDOW_DAYS, BASELINE_WINDOW_DAYS + 2, SCENARIOS[2])
    fill = 10.0 * 1.0005
    quantity = 100_000.0 / (fill * 1.0002)
    expected_commission = quantity * fill * 0.0002
    expected_slippage = quantity * (fill - 10.0)
    expected_final_equity = quantity * 15.0

    assert metrics["benchmark_symbol"] == "SOXL"
    assert metrics["benchmark_policy"] == "next_open_buy_and_hold_with_costs"
    assert metrics["initial_equity"] == pytest.approx(100_000.0)
    assert metrics["entry_quantity"] == pytest.approx(quantity)
    assert metrics["commission_paid"] == pytest.approx(expected_commission)
    assert metrics["slippage_impact_vs_open"] == pytest.approx(expected_slippage)
    assert metrics["total_cost"] == pytest.approx(expected_commission + expected_slippage)
    assert metrics["final_equity"] == pytest.approx(expected_final_equity)
    assert metrics["cumulative_return"] == pytest.approx(expected_final_equity / 100_000.0 - 1.0)
    assert metrics["max_drawdown"] == pytest.approx(expected_final_equity / (quantity * 20.0) - 1.0)
    assert metrics["max_drawdown"] < 0.0


def test_rsi2_buy_and_hold_benchmark_is_next_open_causal_and_same_calendar() -> None:
    prefix = tuple(InputRow("SOXX", f"2024-05-{index + 1:03d}", 1.0, 1.0, 1.0, 1.0, 1.0) for index in range(BASELINE_WINDOW_DAYS))
    window = tuple(
        InputRow("SOXX", f"2025-02-{index + 1:02d}", opening, opening, opening, close, 1.0)
        for index, (opening, close) in enumerate(((10.0, 10.0), (20.0, 20.0), (30.0, 60.0)))
    )
    rows = prefix + window
    baseline = _rsi2_buy_and_hold_metrics(rows, BASELINE_WINDOW_DAYS, BASELINE_WINDOW_DAYS + 2, SCENARIOS[0])
    changed_after_entry = tuple(
        InputRow(row.symbol, row.as_of, row.open, row.high, row.low, 999.0 if index == 1 else row.close, row.volume)
        for index, row in enumerate(rows)
    )
    changed_entry_open = tuple(
        InputRow(row.symbol, row.as_of, 11.0 if index == BASELINE_WINDOW_DAYS else row.open, row.high, row.low, row.close, row.volume)
        for index, row in enumerate(rows)
    )
    assert _rsi2_buy_and_hold_metrics(changed_after_entry, BASELINE_WINDOW_DAYS, BASELINE_WINDOW_DAYS + 2, SCENARIOS[0])["entry_quantity"] == pytest.approx(baseline["entry_quantity"])
    assert _rsi2_buy_and_hold_metrics(changed_entry_open, BASELINE_WINDOW_DAYS, BASELINE_WINDOW_DAYS + 2, SCENARIOS[0])["entry_quantity"] != pytest.approx(baseline["entry_quantity"])
    assert baseline["start_date"] == changed_after_entry[BASELINE_WINDOW_DAYS].as_of
    assert baseline["end_date"] == changed_after_entry[BASELINE_WINDOW_DAYS + 2].as_of
    assert baseline["observation_count"] == 3
    costed = _rsi2_buy_and_hold_metrics(rows, BASELINE_WINDOW_DAYS, BASELINE_WINDOW_DAYS + 2, SCENARIOS[2])
    continuation = _rsi2_buy_and_hold_metrics(rows, BASELINE_WINDOW_DAYS + 1, BASELINE_WINDOW_DAYS + 2, SCENARIOS[2])
    assert continuation["entry_quantity"] == pytest.approx(costed["entry_quantity"])
    assert continuation["initial_equity"] == pytest.approx(costed["entry_quantity"] * 10.0)
    assert continuation["cumulative_return"] == pytest.approx(60.0 / 10.0 - 1.0)
    assert continuation["total_cost"] == pytest.approx(0.0)


def test_rsi2_output_exposes_net_soxl_and_soxx_benchmarks_without_relabeling_gross_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("us_equity_strategies.research.soxl_core_optimization._terminal_loss_probability", lambda _: 0.0)
    result = run_soxl_rsi2_mean_reversion(_source())
    metrics = result["post_lock_metrics"]["UNSCALED_SMA200"]["FINAL_HOLDOUT"]["C2_5"]
    benchmarks = metrics["buy_and_hold_benchmarks"]
    assert set(benchmarks) == {"SOXL", "SOXX"}
    assert benchmarks["SOXL"]["benchmark_policy"] == "next_open_buy_and_hold_with_costs"
    assert benchmarks["SOXX"]["benchmark_policy"] == "next_open_buy_and_hold_with_costs"
    assert (benchmarks["SOXL"]["start_date"], benchmarks["SOXL"]["end_date"], benchmarks["SOXL"]["observation_count"]) == (benchmarks["SOXX"]["start_date"], benchmarks["SOXX"]["end_date"], benchmarks["SOXX"]["observation_count"])
    assert metrics["soxx_close_path_basis"] == "gross_close_price_path_without_execution_costs"
    assert "total_cost" in benchmarks["SOXX"]


def test_rsi2_acceptance_activity_filters_zero_activity_and_ranks_by_cagr_first() -> None:
    carried = DailyPoint("2024-01-01", 100.0, 101.0, 0.0, 1.0, 0.01, False, 0.0, 0.0, 0.0)
    exit_at_open = DailyPoint("2024-01-02", 101.0, 100.0, 100.0, 0.0, -0.01, True, 0.0, 0.0, 101.0)
    cash = DailyPoint("2024-01-03", 100.0, 100.0, 100.0, 0.0, 0.0, False, 0.0, 0.0, 0.0)
    soxx = tuple(InputRow("SOXX", f"2024-02-{index + 1:02d}", 100.0, 100.0, 100.0, 100.0, 1.0) for index in range(202))
    active = _rsi2_metrics_with_unified_soxx((carried, exit_at_open), soxx, 200, 201)
    inactive = _rsi2_metrics_with_unified_soxx((cash, cash), soxx, 200, 201)
    assert active["exposure_session_count"] == 1 and active["activity_observed"] is True
    assert inactive["exposure_session_count"] == 0 and inactive["activity_observed"] is False

    baseline = {"max_drawdown": -0.2, "expected_shortfall_95": -0.1, "annualized_volatility": 0.1, "cagr": 0.1, "turnover": 1.0, "activity_observed": True}
    better = {"max_drawdown": -0.1, "expected_shortfall_95": -0.05, "annualized_volatility": 9.0, "cagr": 0.2, "turnover": 1.0, "activity_observed": True}
    inactive_candidate = {**better, "cagr": 99.0, "activity_observed": False}
    validation = {
        "UNSCALED_SMA200": [baseline] * 3,
        "RSI2_ENTRY_5_EXIT_70": [better] * 3,
        "RSI2_ENTRY_10_EXIT_70": [inactive_candidate] * 3,
        "RSI2_ENTRY_15_EXIT_70": [better] * 3,
    }
    assert _select_rsi2_mean_reversion_winner(validation) == "RSI2_ENTRY_5_EXIT_70"
    validation["UNSCALED_SMA200"] = [{**baseline, "activity_observed": False}] * 3
    assert _select_rsi2_mean_reversion_winner(validation) is None


def test_rsi2_wfa_requires_complete_same_window_predicates() -> None:
    baseline = {"max_drawdown": -0.2, "expected_shortfall_95": -0.1}
    qualifying = {"activity_observed": True, "cumulative_return": 0.01, "max_drawdown": -0.1, "expected_shortfall_95": -0.05}
    split = [
        {**qualifying, "cumulative_return": -0.01},
        {**qualifying, "max_drawdown": -0.3},
        {**qualifying, "expected_shortfall_95": -0.2},
    ]
    assert _rsi2_wfa_qualifying_count(split, [baseline] * 3) == 0
    assert _rsi2_wfa_qualifying_count([qualifying, qualifying, split[0]], [baseline] * 3) == 2
    assert _rsi2_wfa_qualifying_count([qualifying, None, qualifying], [baseline] * 3) == 2


def test_rsi2_zero_activity_rejection_is_fail_closed_and_consistent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("us_equity_strategies.research.soxl_core_optimization._terminal_loss_probability", lambda _: pytest.fail("zero activity must not reach Monte Carlo"))
    result = run_soxl_rsi2_mean_reversion(_source([1000.0 - index for index in range(753)]))
    assert result["outcome"] == "NO_IMPROVEMENT"
    assert result["locked_winner"] is None
    assert result["research_recommendation"] is None
    assert result["recommendation_eligible"] is False and result["r4a_eligible"] is False
    assert result["acceptance_classes"] == {
        "activity": {"selection_windows_all_active": False, "wfa_qualifying_test_window_count": 0, "wfa_at_least_two_of_three": False},
        "benchmark_relative_compounding": {"final_holdout_drawdown_no_worse_than_soxx": False, "final_holdout_cumulative_return_strictly_greater_than_soxx": False, "final_holdout_cagr_strictly_greater_than_soxx": False, "eligible": False},
    }
    assert "VALIDATION_MEDIANS_STRICTLY_IMPROVE_DRAWDOWN_AND_ES95" in result["failure_codes"]
    assert "SELECTION_WINDOWS_ALL_ACTIVE" in result["failure_codes"]
    assert "FINAL_HOLDOUT_BENCHMARK_RELATIVE_COMPOUNDING" in result["failure_codes"]
    assert result["evidence_gates"]["wfa_tests_at_least_two_simultaneous_predicates"] is False
