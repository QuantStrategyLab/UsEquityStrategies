from __future__ import annotations

from datetime import date, timedelta
from dataclasses import replace

import pytest
from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
from quant_platform_kit.strategy_lifecycle.contracts import PromotionCostModel, PurgedWalkForwardFold
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

from us_equity_strategies.research.soxl_soxx_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.soxl_core_optimization import SCENARIOS
from us_equity_strategies.research.soxl_core_optimization import DailyPoint
from us_equity_strategies.research.soxl_rsi2_promotion_runner import (
    SoxlRsi2PromotionRunner,
    SoxlRsi2PromotionRunnerError,
)


def _source() -> OfflineInput:
    rows: list[InputRow] = []
    for index in range(753):
        day = (date(2023, 7, 14) + timedelta(days=index)).isoformat()
        soxx = 100.0 + index * 0.02 + ((index % 21) - 10) * 0.5
        soxl_open = 50.0 + index % 7
        soxl_close = soxl_open * (1.0 + ((index % 5) - 2) / 100.0)
        rows.extend((InputRow("SOXL", day, soxl_open, max(soxl_open, soxl_close), min(soxl_open, soxl_close), soxl_close, 1.0), InputRow("SOXX", day, soxx, soxx, soxx, soxx, 1.0)))
    canonical = ["symbol,as_of,open,high,low,close,volume"]
    canonical.extend(
        ",".join((row.symbol, row.as_of, *(format(value, ".17g") for value in (row.open, row.high, row.low, row.close, row.volume))))
        for row in rows
    )
    return OfflineInput(tuple(rows), ("\n".join(canonical) + "\n").encode(), "a" * 64, "fixture_v1")


def _changed_future_bar(source: OfflineInput) -> OfflineInput:
    rows = list(source.rows)
    index = next(index for index, row in enumerate(rows) if row.symbol == "SOXL" and row.as_of == "2025-07-01")
    row = rows[index]
    rows[index] = replace(row, close=row.close * 2.0, high=row.high * 2.0)
    canonical = ["symbol,as_of,open,high,low,close,volume"]
    canonical.extend(
        ",".join((item.symbol, item.as_of, *(format(value, ".17g") for value in (item.open, item.high, item.low, item.close, item.volume))))
        for item in rows
    )
    return OfflineInput(tuple(rows), ("\n".join(canonical) + "\n").encode(), source.input_digest, source.source_revision)


def _cost(model_id: str = "C2_5") -> PromotionCostModel:
    scenario = next(item for item in SCENARIOS if item.scenario_id == model_id)
    return PromotionCostModel(model_id, scenario.commission_bps, scenario.slippage_bps)


def _folds() -> tuple[PurgedWalkForwardFold, ...]:
    return (
        PurgedWalkForwardFold(date(2023, 7, 14), date(2024, 1, 5), date(2024, 2, 1), date(2024, 2, 20)),
        PurgedWalkForwardFold(date(2024, 2, 25), date(2024, 3, 20), date(2024, 4, 1), date(2024, 4, 20)),
        PurgedWalkForwardFold(date(2024, 5, 1), date(2024, 6, 1), date(2024, 7, 1), date(2024, 7, 20)),
    )


def test_fixed_candidate_returns_dated_result_and_costs() -> None:
    runner = SoxlRsi2PromotionRunner(_source(), candidate_id="RSI2_ENTRY_10_EXIT_70")
    result = runner.run_locked_oos(
        "soxl_rsi2_mean_reversion",
        {"candidate_id": "RSI2_ENTRY_10_EXIT_70"},
        start_date=date(2024, 2, 1),
        end_date=date(2024, 2, 20),
        cost_model=_cost(),
    )
    assert result.start_date == date(2024, 2, 1)
    assert result.end_date == date(2024, 2, 20)
    assert result.observation_count == 20
    assert result.cost_model == "C2_5"
    assert result.cost_inputs == {"commission_bps": 2.0, "slippage_bps": 5.0, "market_impact_bps": 0.0}
    assert result.win_rate is None
    assert result.total_return is not None and result.max_drawdown is not None


def test_candidate_and_cost_are_frozen() -> None:
    runner = SoxlRsi2PromotionRunner(_source(), candidate_id="RSI2_ENTRY_10_EXIT_70")
    with pytest.raises(SoxlRsi2PromotionRunnerError, match="candidate"):
        runner.run_locked_oos("soxl_rsi2_mean_reversion", {"candidate_id": "RSI2_ENTRY_5_EXIT_70"}, start_date=date(2024, 2, 1), end_date=date(2024, 2, 20), cost_model=_cost())
    with pytest.raises(SoxlRsi2PromotionRunnerError, match="cost"):
        runner.run_locked_oos("soxl_rsi2_mean_reversion", {"candidate_id": "RSI2_ENTRY_10_EXIT_70"}, start_date=date(2024, 2, 1), end_date=date(2024, 2, 20), cost_model=PromotionCostModel("C2_5", 2, 5, 1))


def test_cost_changes_net_result_and_future_bars_do_not_change_early_window() -> None:
    source = _source()
    runner = SoxlRsi2PromotionRunner(source, candidate_id="UNSCALED_SMA200")
    zero = runner.run_locked_oos("soxl_rsi2_mean_reversion", {"candidate_id": "UNSCALED_SMA200"}, start_date=date(2024, 2, 1), end_date=date(2024, 2, 20), cost_model=_cost("ZERO"))
    charged = runner.run_locked_oos("soxl_rsi2_mean_reversion", {"candidate_id": "UNSCALED_SMA200"}, start_date=date(2024, 2, 1), end_date=date(2024, 2, 20), cost_model=_cost())
    assert charged.total_return < zero.total_return
    future_runner = SoxlRsi2PromotionRunner(_changed_future_bar(source), candidate_id="UNSCALED_SMA200")
    future = future_runner.run_locked_oos("soxl_rsi2_mean_reversion", {"candidate_id": "UNSCALED_SMA200"}, start_date=date(2024, 2, 1), end_date=date(2024, 2, 20), cost_model=_cost())
    assert future.total_return == charged.total_return
    assert future.max_drawdown == charged.max_drawdown


def test_nav_mapping_uses_window_start_equity_for_return_and_drawdown() -> None:
    runner = SoxlRsi2PromotionRunner(_source(), candidate_id="UNSCALED_SMA200")
    points = (
        DailyPoint("2024-02-01", 100.0, 90.0, 90.0, 0.0, -0.1, False, 0.0, 0.0, 0.0),
        DailyPoint("2024-02-02", 90.0, 99.0, 99.0, 0.0, 0.1, False, 0.0, 0.0, 0.0),
    )
    result = runner._result(points, start_date=date(2024, 2, 1), end_date=date(2024, 2, 2), cost_model=_cost("ZERO"), elapsed_seconds=0.25)
    assert result.total_return == pytest.approx(-0.01)
    assert result.max_drawdown == pytest.approx(-0.10)
    assert result.run_duration_seconds == pytest.approx(0.25)


def test_short_window_and_missing_dates_fail_closed() -> None:
    runner = SoxlRsi2PromotionRunner(_source(), candidate_id="UNSCALED_SMA200")
    with pytest.raises(SoxlRsi2PromotionRunnerError, match="window"):
        runner.run_locked_oos("soxl_rsi2_mean_reversion", {"candidate_id": "UNSCALED_SMA200"}, start_date=date(2023, 7, 14), end_date=date(2023, 8, 1), cost_model=_cost())
    with pytest.raises(SoxlRsi2PromotionRunnerError, match="date"):
        runner.run_locked_oos("soxl_rsi2_mean_reversion", {"candidate_id": "UNSCALED_SMA200"}, start_date=date(2026, 1, 1), end_date=date(2026, 2, 1), cost_model=_cost())


def test_qpk_promotion_plan_uses_fixed_runner_without_claiming_real_data(tmp_path) -> None:
    runner = SoxlRsi2PromotionRunner(_source(), candidate_id="UNSCALED_SMA200")
    orchestrator = BacktestOrchestrator(store=PerformanceStore(local_root=tmp_path))
    orchestrator.register_runner("us_equity", runner)
    result = orchestrator.run_promotion(
        "soxl_rsi2_mean_reversion",
        domain="us_equity",
        params={"candidate_id": "UNSCALED_SMA200"},
        folds=_folds(),
        locked_oos_start=date(2024, 8, 1),
        locked_oos_end=date(2025, 8, 1),
        purge_days=2,
        embargo_days=2,
        source_revision="a" * 40,
        cost_model=_cost(),
    )
    assert result.locked_oos_result.observation_count > 0
    assert result.locked_oos_result.source_revision == "a" * 40
    with pytest.raises(ValueError, match="12 calendar months"):
        orchestrator.run_promotion(
            "soxl_rsi2_mean_reversion",
            domain="us_equity",
            params={"candidate_id": "UNSCALED_SMA200"},
            folds=_folds(),
            locked_oos_start=date(2024, 8, 1),
            locked_oos_end=date(2024, 12, 31),
            purge_days=2,
            embargo_days=2,
            source_revision="a" * 40,
            cost_model=_cost(),
        )
