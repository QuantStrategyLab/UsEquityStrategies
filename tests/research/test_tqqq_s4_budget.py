"""Small synthetic checks for the frozen one-member budget policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from us_equity_strategies.research.tqqq_qqq_guard_cash_research import _simulate


def _allocator():
    path = Path(__file__).parents[2] / "docs/research/first_compounding_20260925/auto_allocate.py"
    spec = importlib.util.spec_from_file_location("s4_auto_allocate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.allocate_single_member_budget


def _recovery(monkeypatch):
    path = Path(__file__).parents[2] / "docs/research/first_compounding_20260925/tqqq_cash_budget_compare.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("s4_budget_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._recovery


def _input(intraday: float = 1.02, overnight: float = 1.0, shares: int = 0) -> dict:
    dates = pd.bdate_range("2022-10-03", periods=60)
    return {"decision_date": dates[-1].date().isoformat(), "nav_usd": 10000,
            "cash_usd": 10000 - shares * 100, "receivable_usd": 0,
            "current_shares": shares, "current_close": 100,
            "member_tqqq_exposure_ratio": 0.45,
            "observed_high_water_usd": 10000, "initial_principal_usd": 10000,
            "wealth_reference_usd": 10000, "security_trade_cost_bps": 0,
            "max_worst_scenario_loss_ratio": 0.05, "search_step_usd": 100,
            "capital_curve": {"a0_usd": 10000, "lower": 0.2, "upper": 0.8, "curvature": 1},
            "scenarios": [{"date": day.date().isoformat(),
                           "overnight_open_to_prior_close": overnight,
                           "intraday_close_to_open": intraday,
                           "dividend_per_prior_close": 0} for day in dates]}


def test_one_member_budget_uses_exposure_not_full_member_as_stock() -> None:
    result = _allocator()(_input())
    assert result["status"] == "MECHANISM_APPROXIMATION"
    # The 4,900 and 5,000 budgets both buy 22 shares; the tie break keeps
    # the smaller member budget and more outer cash.
    assert result["member_budget_usd"] == 4900
    assert result["curve_budget_cap_usd"] == 5000
    assert result["estimated_worst_scenario_loss_ratio"] == 0


def test_one_member_budget_may_choose_cash_and_future_scenarios_fail() -> None:
    data = _input(intraday=0.98)
    assert _allocator()(data)["member_budget_usd"] == 0
    data["scenarios"][-1]["date"] = "2024-01-01"
    assert _allocator()(data)["status"] == "DATA_INSUFFICIENT"


def test_old_shares_carry_unavoidable_overnight_loss() -> None:
    result = _allocator()(_input(intraday=1, overnight=0.6, shares=20))
    assert result["status"] == "INFEASIBLE"


def test_actual_builder_replays_half_budget_with_whole_shares() -> None:
    days = pd.bdate_range("2023-01-02", periods=260)
    rows = [{"date": day.date().isoformat(), "qqq_close": 100 + i * 0.1,
             "tqqq_open": 50.0, "tqqq_close": 50.0} for i, day in enumerate(days)]
    contract = {"core": {"first_signal_min_qqq_bars": 257},
                "qqq_guard": {"lookback_sessions": 20, "soft_drawdown": -0.05,
                              "hard_drawdown": -0.1, "soft_scalar": 0.5,
                              "hard_scalar": 0, "max_price_age_days": 3},
                "portfolio": {"research_initial_usd": 10000},
                "window": {"first_signal_rule": "index 256 after 257 complete common bars"}}
    _, ledger = _simulate(rows, {}, contract, cost_bps=0, use_guard=True,
                          budget_selector=lambda _prefix, **kw: 0.5 * kw["nav"])
    assert ledger[0]["shares"] == 45
    assert ledger[0]["member_budget_ratio"] == 0.5
    assert ledger[0]["cash_usd"] == 7750


def test_recovery_uses_most_recent_equal_peak(monkeypatch) -> None:
    ledger = [{"nav_close": value} for value in (100, 100, 90, 100)]
    recovery = _recovery(monkeypatch)(ledger, 100)
    assert recovery["max_drawdown_peak_to_recovery_sessions"] == 2
    assert recovery["max_drawdown_trough_to_recovery_sessions"] == 1


def test_receivable_is_not_called_outer_cash() -> None:
    data = _input()
    data["cash_usd"] = 9000
    data["receivable_usd"] = 1000
    result = _allocator()(data)
    assert result["status"] == "MECHANISM_APPROXIMATION"
    assert result["outer_cash_budget_usd"] + result["unpaid_dividend_receivable_usd"] + result["member_budget_usd"] == 10000


def test_exposure_summary_separates_stock_cash_and_receivable(monkeypatch) -> None:
    path = Path(__file__).parents[2] / "docs/research/first_compounding_20260925/s4_exposure_attribution.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("s4_exposure_attribution", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = [
        {"shares": 1, "tqqq_close": 20, "nav_close": 100, "cash_usd": 70,
         "receivable_usd": 10, "member_budget_ratio": .5,
         "member_budget_usd": 50, "target_tqqq_ratio": .2,
         "signal_guard_route": "no_action"},
        {"shares": 1, "tqqq_close": 20, "nav_close": 100, "cash_usd": 70,
         "receivable_usd": 10, "member_budget_ratio": .5,
         "member_budget_usd": 50, "target_tqqq_ratio": .2,
         "signal_guard_route": "no_action"},
    ]
    summary = module._summary(ledger, 100)
    assert summary["mean_actual_tqqq_exposure"] == .2
    assert summary["mean_cash_ratio"] == .7
    assert summary["mean_receivable_ratio"] == .1
    assert summary["mean_planned_outer_cash_budget_ratio"] == .45
