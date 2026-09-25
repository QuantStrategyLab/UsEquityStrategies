"""Synthetic checks for member cash protection and historical scenario causality."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


def _module(monkeypatch):
    path = Path(__file__).parents[2] / "docs/research/first_compounding_20260925/boxx_outer_cash_compare.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("boxx_outer_cash_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_member_internal_cash_cannot_buy_boxx(monkeypatch) -> None:
    module = _module(monkeypatch)
    result = module._execute(
        {"cash": 1000, "shares": {"TQQQ": 0, "BOXX": 0}, "pending": [], "receivable": 0},
        t_open=10, b_open=100, t_close=10, b_close=100,
        budget=500, t_target=200, b_target=500, fee=0.0005, release=3)
    assert result["shares"] == {"TQQQ": 20, "BOXX": 4}
    assert result["cash"] >= 300
    assert result["nav"] == 1000 - sum(result["costs"].values())


def test_sale_proceeds_are_pending_and_cannot_fund_same_open(monkeypatch) -> None:
    module = _module(monkeypatch)
    result = module._execute(
        {"cash": 0, "shares": {"TQQQ": 0, "BOXX": 10}, "pending": [], "receivable": 0},
        t_open=10, b_open=100, t_close=10, b_close=100,
        budget=500, t_target=200, b_target=0, fee=0.0005, release=3)
    assert result["shares"] == {"TQQQ": 0, "BOXX": 0}
    assert result["cash"] == 0
    assert result["pending"] == [{"release": 3, "amount": 999.5, "symbol": "BOXX"}]


def test_paired_scenarios_are_prefix_only(monkeypatch) -> None:
    module = _module(monkeypatch)
    days = pd.bdate_range("2023-01-02", periods=62)
    rows = [{"date": day.date().isoformat(), "tqqq_open": 10, "tqqq_close": 10,
             "boxx_open": 100, "boxx_close": 100} for day in days]
    actions = {"TQQQ": {}, "BOXX": {}}
    original = module._scenario_pairs(rows, 60, actions, True)
    rows[61].update(tqqq_open=900, tqqq_close=901, boxx_open=1, boxx_close=2)
    assert module._scenario_pairs(rows, 60, actions, True) == original
    assert len(original) == 60 and all(set(item) == {"TQQQ", "BOXX"} for item in original)
