"""Synthetic account-level checks for the bounded QPK promotion adapter."""

from __future__ import annotations

import copy
import importlib.util
import math
import sys
from datetime import date
from pathlib import Path

import pytest
from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import _validate_promotion_result
from quant_platform_kit.strategy_lifecycle.contracts import PromotionCostModel, PurgedWalkForwardFold
from quant_platform_kit.strategy_lifecycle.performance_metrics import compute_window_metrics


ROOT = Path(__file__).parents[2]
RESEARCH = ROOT / "docs/research/first_compounding_20260925"
sys.path.insert(0, str(RESEARCH))

import r7_joint_account_compare as r7  # noqa: E402
import r8_joint_allocation_compare as r8  # noqa: E402
import r8_promotion_runner as promotion  # noqa: E402
from r8_promotion_runner import R8PromotionBacktestRunner  # noqa: E402


def _fixtures(*, flat: bool = False, future_event: bool = True):
    import pandas as pd

    days = [item.date() for item in pd.bdate_range("2023-01-02", "2023-05-31")]
    rows = []
    for index, day in enumerate(days):
        row = {"date": day.isoformat()}
        for position, symbol in enumerate(r7.SYMBOLS):
            base = 35.0 + position * 17.0
            factor = 1.0 if flat else 1.0 + 0.0007 * index + 0.003 * math.sin(index / 4 + position)
            close = base * factor
            row[symbol.lower() + "_close"] = close
            row[symbol.lower() + "_open"] = close if index == 0 else rows[-1][symbol.lower() + "_close"] * 1.0002
        rows.append(row)
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in r7.SYMBOLS}
    if future_event:
        actions["BOXX"]["cash_dividends"].append({
            "symbol": "BOXX", "ex_date": "2023-03-30", "payable_date": "2023-04-05",
            "process_date": "2023-04-03", "rate": 0.04,
        })
    actions["QQQM"]["cash_dividends"].append({
        "symbol": "QQQM", "ex_date": "2023-05-01", "payable_date": "2023-05-05",
        "process_date": "2023-05-02", "rate": 0.1,
    })
    indicators = {day.isoformat(): {} for day in days}
    return rows, actions, indicators, days


def _runner(*, flat: bool = False, future_event: bool = True, rows=None, actions=None,
            indicators=None):
    original_rows, original_actions, default_indicators, days = _fixtures(
        flat=flat, future_event=future_event)
    rows = original_rows if rows is None else rows
    actions = original_actions if actions is None else actions
    indicators = default_indicators if indicators is None else indicators
    return R8PromotionBacktestRunner(
        rows=rows, actions=actions, indicators=indicators, contract={},
        research_identity="synthetic_r8_runner_test", source_revision="a" * 40,
        input_binding_sha256="b" * 64, session_dates=days, evidence_kind="synthetic",
    )


def _cost(total: float = 10.0, model_id: str = "synthetic-all-in"):
    return PromotionCostModel(model_id=model_id, commission_bps=total, slippage_bps=0,
                              market_impact_bps=0)


def _no_tqqq_signal(rows, index, *, shares, nav, use_guard, config, member_budget_usd):
    return {"signal_date": rows[index]["date"], "target_tqqq_usd": 0.0,
            "guard_route": "synthetic_zero", "core_state": "synthetic_zero"}


def _no_soxl_signal(indicators, book, signal_day, closes, budget):
    return {"targets": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
            "blend_tier": "synthetic_zero", "cap_scale": 0.0,
            "current_min_trade": 100.0, "reserved_cash": 0.0}


@pytest.fixture
def neutral_members(monkeypatch):
    monkeypatch.setattr(r7, "_decision", _no_tqqq_signal)
    monkeypatch.setattr(r7, "_soxl_signal", _no_soxl_signal)
    monkeypatch.setattr(r8, "_decision", _no_tqqq_signal)
    monkeypatch.setattr(r8, "_soxl_signal", _no_soxl_signal)


def _fold(start: str, end: str, train_start: str = "2023-03-20", train_end: str = "2023-03-24"):
    return PurgedWalkForwardFold(date.fromisoformat(train_start), date.fromisoformat(train_end),
                                 date.fromisoformat(start), date.fromisoformat(end))


def test_bounded_r7_replay_requires_explicit_end_and_preserves_legacy_modes():
    policy = r7._policy()
    rows, actions, indicators, days = _fixtures()
    end = next(day.isoformat() for day in days if day.isoformat() == "2023-03-30")
    _, ledger = r7._replay(rows, actions, indicators, {}, policy, path_name="B0", cost_bps=10,
                           replay_end_session=end)
    assert ledger[-1]["date"] == end
    assert len(ledger) == days.index(date.fromisoformat(policy["data"]["first_trade"])) - days.index(
        date.fromisoformat(policy["data"]["first_trade"])) + 3
    with pytest.raises(ValueError, match="BOUNDED_END_WINDOW_CONFLICT"):
        r7._replay(rows, actions, indicators, {}, policy, path_name="B0", cost_bps=10,
                   short_sessions=2, replay_end_session=end)
    with pytest.raises(ValueError, match="BOUNDED_END_MISSING"):
        r7._replay(rows, actions, indicators, {}, policy, path_name="B0", cost_bps=10,
                   replay_end_session="2023-03-31-not-present")


def test_r8_runner_returns_real_qpk_result_and_keeps_claims_across_gap(neutral_members):
    runner = _runner()
    start, end = date(2023, 3, 28), date(2023, 4, 6)
    first = runner.run_purged_fold("R8", {}, fold=_fold("2023-03-28", "2023-03-30"),
                                   purge_days=1, embargo_days=1, cost_model=_cost())
    second = runner.run_purged_fold(
        "R8", {}, fold=_fold("2023-04-06", "2023-04-06", "2023-04-01", "2023-04-04"),
        purge_days=1, embargo_days=1, cost_model=_cost())
    assert first.start_date == start
    assert second.end_date == end
    assert first.observation_count == 3
    assert first.source_revision == "a" * 40
    assert first.cost_inputs == {"commission_bps": 10.0, "slippage_bps": 0.0,
                                 "market_impact_bps": 0.0}
    assert first.win_rate is None
    assert first.periods_per_year == 252.0 and first.calendar_id == "XNYS"
    assert "synthetic_r8_runner_test" in first.param_set_id
    assert "synthetic" in first.param_set_id
    assert math.isfinite(first.sharpe_ratio)
    _validate_promotion_result(first, start_date=start, end_date=date(2023, 3, 30))
    state_key = runner._state_key("R8", {}, _cost())
    ledger = runner._last_ledgers[state_key]
    claim_day = next(row for row in ledger.values() if row["date"] == "2023-03-30")
    paid_day = ledger["2023-04-06"]
    assert claim_day["owner_receivable_usd"]["outer"] > 0
    assert paid_day["owner_receivable_usd"]["outer"] == 0
    assert math.isfinite(second.max_drawdown)
    assert all(row["action_selection"]["scenario_count"] == 60
               for row in ledger.values() if row["action_selection"]["observed_through"] >= "2023-03-29"
               and row["action_selection"]["startup_fixed"] is False)
    startup = [row for row in ledger.values()
               if row["action_selection"].get("startup_fixed")]
    assert [row["signal_date"] for row in startup] == ["2023-03-27", "2023-03-28"]
    navs = [ledger[day]["economic_nav_close_usd"]
            for day in ("2023-03-28", "2023-03-29", "2023-03-30")]
    daily = [navs[0] / 10_000.0 - 1.0,
             navs[1] / navs[0] - 1.0,
             navs[2] / navs[1] - 1.0]
    assert first.total_return == pytest.approx(math.prod(1 + item for item in daily) - 1)
    metric_check = compute_window_metrics(
        __import__("pandas").Series(daily, index=__import__("pandas").date_range("2023-03-28", periods=3)),
        risk_free_rate=0.0, periods_per_year=252, calendar_id="XNYS")
    assert first.cagr == pytest.approx(metric_check.cagr)


@pytest.mark.parametrize("profile", ["B0", "R8"])
def test_full_and_split_checkpoint_replay_have_identical_daily_account_rows(neutral_members, profile):
    rows, actions, indicators, days = _fixtures()
    full = _runner(rows=rows, actions=actions)
    split = _runner(rows=rows, actions=actions)
    full.run_locked_oos(profile, {}, start_date=date(2023, 3, 28), end_date=date(2023, 4, 6),
                        cost_model=_cost())
    split.run_purged_fold(profile, {}, fold=_fold("2023-03-28", "2023-03-30"),
                          purge_days=1, embargo_days=1, cost_model=_cost())
    split.run_purged_fold(profile, {}, fold=_fold("2023-04-06", "2023-04-06", "2023-04-01", "2023-04-04"),
                          purge_days=1, embargo_days=1, cost_model=_cost())
    key = split._state_key(profile, {}, _cost())
    assert full._last_ledgers[key] == split._last_ledgers[key]
    assert full._checkpoints[key] == split._checkpoints[key]


def test_fold_end_future_prices_actions_and_indicators_do_not_change_bounded_metrics(neutral_members):
    rows, actions, indicators, days = _fixtures()
    end_index = days.index(date(2023, 4, 6))
    baseline = _runner(rows=rows, actions=actions)
    result = baseline.run_purged_fold(
        "R8", {}, fold=_fold("2023-04-04", "2023-04-06", "2023-03-28", "2023-03-31"),
        purge_days=1, embargo_days=1, cost_model=_cost())
    changed_rows = copy.deepcopy(rows)
    for row in changed_rows[end_index + 1:]:
        for symbol in r7.SYMBOLS:
            row[symbol.lower() + "_open"] = 999999.0
            row[symbol.lower() + "_close"] = 0.001
    changed_actions = copy.deepcopy(actions)
    changed_actions["BOXX"]["cash_dividends"].append({
        "symbol": "BOXX", "ex_date": "2023-04-10", "payable_date": "2023-04-14",
        "process_date": "2023-04-11", "rate": 1000.0,
    })
    changed_indicators = {day.isoformat(): {} for day in days}
    changed_indicators["2023-05-30"] = {"future_feature": 1e9}
    perturbed = R8PromotionBacktestRunner(
        rows=changed_rows, actions=changed_actions, indicators=changed_indicators, contract={},
        research_identity="synthetic_r8_runner_test", source_revision="a" * 40,
        input_binding_sha256="b" * 64, session_dates=days, evidence_kind="synthetic")
    perturbed_result = perturbed.run_purged_fold(
        "R8", {}, fold=_fold("2023-04-04", "2023-04-06", "2023-03-28", "2023-03-31"),
        purge_days=1, embargo_days=1, cost_model=_cost())
    assert (perturbed_result.total_return, perturbed_result.max_drawdown,
            perturbed_result.cagr, perturbed_result.sharpe_ratio) == (
                result.total_return, result.max_drawdown, result.cagr, result.sharpe_ratio)
    assert perturbed._last_ledgers[perturbed._state_key("R8", {}, _cost())] == (
        baseline._last_ledgers[baseline._state_key("R8", {}, _cost())])


def test_cost_identity_with_same_total_bps_uses_separate_checkpoints(neutral_members):
    runner = _runner()
    fold = _fold("2023-03-28", "2023-03-30")
    runner.run_purged_fold("B0", {}, fold=fold, purge_days=1, embargo_days=1,
                           cost_model=_cost(10.0, "commission-only"))
    runner.run_purged_fold("B0", {}, fold=fold, purge_days=1, embargo_days=1,
                           cost_model=PromotionCostModel("split-cost", 5.0, 5.0, 0.0))
    assert len(runner._checkpoints) == 2


@pytest.mark.parametrize("first,second", [("B0", "R8"), ("R8", "B1"), ("R8", "B0")])
def test_existing_b0_benchmark_is_reused_for_another_profile(neutral_members, first, second):
    runner = _runner()
    window = (date(2023, 3, 28), date(2023, 4, 6))
    runner.run_locked_oos(first, {}, start_date=window[0], end_date=window[1], cost_model=_cost())
    result = runner.run_locked_oos(second, {}, start_date=window[0], end_date=window[1],
                                   cost_model=_cost())
    assert result.benchmark_symbol == "B0"
    assert result.observation_count == 8
    assert runner._checkpoints[runner._state_key(second, {}, _cost())]["checkpoint"]["last_date"] == "2023-04-06"
    if second == "B0":
        later = _fold("2023-04-10", "2023-04-11", "2023-04-05", "2023-04-06")
        next_result = runner.run_purged_fold("B0", {}, fold=later, purge_days=1,
                                             embargo_days=1, cost_model=_cost())
        assert next_result.observation_count == 2


def test_dynamic_same_window_rerun_remains_rejected(neutral_members):
    runner = _runner()
    args = {"start_date": date(2023, 3, 28), "end_date": date(2023, 4, 6),
            "cost_model": _cost()}
    runner.run_locked_oos("R8", {}, **args)
    with pytest.raises(ValueError, match="WINDOW_OVERLAPS_CHECKPOINT"):
        runner.run_locked_oos("R8", {}, **args)


def test_failed_profile_run_does_not_leave_partial_checkpoint(neutral_members, monkeypatch):
    runner = _runner()
    runner.run_locked_oos("B0", {}, start_date=date(2023, 3, 28), end_date=date(2023, 4, 6),
                          cost_model=_cost())
    before_checkpoints = copy.deepcopy(runner._checkpoints)
    before_ledgers = copy.deepcopy(runner._last_ledgers)

    def fail_metrics(*args, **kwargs):
        raise ValueError("synthetic metric failure")

    monkeypatch.setattr(promotion, "compute_window_metrics", fail_metrics)
    with pytest.raises(ValueError, match="synthetic metric failure"):
        runner.run_locked_oos("B1", {}, start_date=date(2023, 3, 28), end_date=date(2023, 4, 6),
                              cost_model=_cost())
    assert runner._checkpoints == before_checkpoints
    assert runner._last_ledgers == before_ledgers


def test_sale_cash_releases_after_two_global_sessions_without_reset(neutral_members):
    rows, actions, indicators, days = _fixtures()
    jump_index = days.index(date(2023, 3, 29))
    rows[jump_index]["qqqm_close"] = 100.0
    for row in rows[jump_index + 1:]:
        row["qqqm_open"] = 100.0
        row["qqqm_close"] = 100.0
    runner = _runner(rows=rows, actions=actions)
    runner.run_purged_fold("B0", {}, fold=_fold("2023-03-28", "2023-03-30"),
                           purge_days=1, embargo_days=1, cost_model=_cost())
    runner.run_purged_fold(
        "B0", {}, fold=_fold("2023-04-04", "2023-04-04", "2023-03-31", "2023-04-02"),
        purge_days=1, embargo_days=1, cost_model=_cost())
    ledger = runner._last_ledgers[runner._state_key("B0", {}, _cost())]
    sold = ledger["2023-03-30"]
    gap = ledger["2023-03-31"]
    released = ledger["2023-04-03"]
    assert sold["owner_pending_sale_usd"]["outer"] > 0
    assert gap["owner_pending_sale_usd"]["outer"] > 0
    assert released["sale_proceeds_released_usd"] > 0
    assert released["owner_pending_sale_usd"]["outer"] == 0
    assert all(item["account_identity_error_usd"] < 1e-6 for item in ledger.values())


def test_r8_missing_sixty_session_warmup_is_inconclusive(neutral_members):
    rows, actions, indicators, days = _fixtures()
    cut = days.index(date(2023, 3, 1))
    shortened_rows = rows[cut:]
    shortened_dates = days[cut:]
    shortened_indicators = {day.isoformat(): indicators[day.isoformat()] for day in shortened_dates}
    runner = R8PromotionBacktestRunner(
        rows=shortened_rows, actions=actions, indicators=shortened_indicators, contract={},
        research_identity="synthetic_short_warmup", source_revision="a" * 40,
        input_binding_sha256="c" * 64, session_dates=shortened_dates, evidence_kind="synthetic")
    with pytest.raises(ValueError, match="INSUFFICIENT_KNOWN_PAIRED_SESSIONS"):
        runner.run_purged_fold("R8", {}, fold=_fold("2023-03-28", "2023-03-30"),
                               purge_days=1, embargo_days=1, cost_model=_cost())


def test_invalid_price_cost_window_and_identity_are_rejected(neutral_members):
    rows, actions, indicators, days = _fixtures()
    bad = copy.deepcopy(rows)
    bad[days.index(date(2023, 4, 4))]["qqqm_close"] = float("nan")
    runner = _runner(rows=bad, actions=actions)
    with pytest.raises(ValueError, match="PRICE_INVALID"):
        runner.run_purged_fold("B0", {}, fold=_fold("2023-04-04", "2023-04-06", "2023-03-28", "2023-03-31"),
                               purge_days=1, embargo_days=1, cost_model=_cost())
    with pytest.raises(ValueError, match="COST_SCENARIO_UNSUPPORTED"):
        _runner().run_locked_oos("B0", {}, start_date=date(2023, 3, 28), end_date=date(2023, 3, 30),
                                 cost_model=_cost(7.0))
    with pytest.raises(ValueError, match="PARAMS_ARE_FROZEN"):
        _runner().run_locked_oos("B0", {"tune": True}, start_date=date(2023, 3, 28),
                                 end_date=date(2023, 3, 30), cost_model=_cost())
    missing_session = copy.deepcopy(rows)
    missing_session.pop(days.index(date(2023, 3, 29)))
    with pytest.raises(ValueError, match="SESSION_COVERAGE_INVALID"):
        R8PromotionBacktestRunner(rows=missing_session, actions=actions, indicators=indicators,
                                  contract={}, research_identity="synthetic", source_revision="a" * 40,
                                  input_binding_sha256="b" * 64, session_dates=days)


def test_constant_window_keeps_undefined_sharpe_none_for_orchestrator_to_reject(neutral_members):
    rows, actions, indicators, days = _fixtures(flat=True)
    runner = _runner(flat=True, rows=rows, actions=actions)
    result = runner.run_purged_fold(
        "B0", {}, fold=_fold("2023-04-04", "2023-04-06", "2023-03-28", "2023-03-31"),
        purge_days=1, embargo_days=1, cost_model=_cost())
    assert result.sharpe_ratio is None
    with pytest.raises(ValueError, match="sharpe_ratio"):
        _validate_promotion_result(result, start_date=result.start_date, end_date=result.end_date)
