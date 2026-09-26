"""Direct counterexamples for the bounded R8 estimator and funding transitions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _modules():
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(root))
    modules = {}
    for name in ("auto_allocate", "r7_joint_account_compare", "r8_joint_allocation_compare"):
        spec = importlib.util.spec_from_file_location(name, root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


def test_finite_score_prefers_previous_within_frozen_tolerance() -> None:
    scorer = _modules()["auto_allocate"].select_finite_executable_action
    selection = scorer(scenario_wealth_usd={"B0": [100.0, 100.0],
                                             "B1": [100.00000001, 100.00000001]},
                       nav_usd=100.0, previous_action="B0", tie_log_tolerance=1e-8)
    assert selection["selected_action"] == "B0"
    with pytest.raises(ValueError, match="paired scenario"):
        scorer(scenario_wealth_usd={"B0": [100.0], "B1": [100.0, 101.0]},
               nav_usd=100.0, previous_action="B0", tie_log_tolerance=1e-8)


def test_split_normalizes_old_price_and_dividend_without_changing_current_shares() -> None:
    m = _modules()["r8_joint_allocation_compare"]
    rows = [{"date": "2023-03-27"}, {"date": "2023-03-28"}]
    for symbol in m.SYMBOLS:
        rows[0][symbol.lower() + "_close"] = 100.0
        rows[1][symbol.lower() + "_open"] = 50.0 if symbol == "SOXX" else 100.0
        rows[1][symbol.lower() + "_close"] = rows[1][symbol.lower() + "_open"]
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in m.SYMBOLS}
    actions["SOXX"] = {
        "forward_splits": [{"symbol": "SOXX", "ex_date": "2023-03-28",
                            "old_rate": 1, "new_rate": 2}],
        "cash_dividends": [{"symbol": "SOXX", "ex_date": "2023-03-28",
                            "payable_date": "2023-03-28", "process_date": "2023-03-28", "rate": 1.0}],
    }
    scenario = m._historical_scenarios(rows, actions, 1, count=1)[0]
    assert scenario["overnight"]["SOXX"] == pytest.approx(1.0)
    assert scenario["intraday"]["SOXX"] == pytest.approx(1.0)
    assert scenario["dividend_yield"]["SOXX"] == pytest.approx(0.02)


def test_unprocessed_dividend_cannot_fill_required_training_window() -> None:
    m = _modules()["r8_joint_allocation_compare"]
    rows = [{"date": "2023-03-27"}, {"date": "2023-03-28"}]
    for symbol in m.SYMBOLS:
        rows[0][symbol.lower() + "_close"] = 100.0
        rows[1][symbol.lower() + "_open"] = 100.0
        rows[1][symbol.lower() + "_close"] = 100.0
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in m.SYMBOLS}
    actions["SOXL"]["cash_dividends"].append({"symbol": "SOXL",
        "ex_date": "2023-03-28", "payable_date": "2023-03-29",
        "process_date": "2023-03-29", "rate": 1.0})
    with pytest.raises(ValueError, match="INSUFFICIENT_KNOWN_PAIRED"):
        m._historical_scenarios(rows, actions, 1, count=1)


def test_unknown_claim_excluded_from_both_estimator_values() -> None:
    modules = _modules()
    m = modules["r8_joint_allocation_compare"]
    r7 = modules["r7_joint_account_compare"]
    books = {owner: r7._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    books["outer"]["cash"] = 100.0
    books["outer"]["claims"].append({"amount": 5.0, "process_date": "2023-03-28",
                                       "payable_date": "2023-03-28", "paid": False})
    prices = {symbol: 100.0 for symbol in m.SYMBOLS}
    assert r7._book_decision_equity(books["outer"], "2023-03-27", prices) == 100.0
    known = m._known_books(books, "2023-03-27")
    assert r7._book_value(known["outer"], prices) == 100.0
    assert books["outer"]["claims"][0]["amount"] == 5.0


def test_pending_exit_consumes_aggregate_member_capacity_until_settlement() -> None:
    r7 = _modules()["r7_joint_account_compare"]
    books = {owner: r7._book(0, symbols) for owner, symbols in r7.OWNER_SYMBOLS.items()}
    books["outer"]["cash"] = 1000.0
    books["tqqq"]["pending"].append({"amount": 500.0, "release": 2})
    prices = {symbol: 100.0 for symbol in r7.SYMBOLS}
    transfer = r7._fund_owners(books, {"tqqq": 0.0, "soxl": 500.0},
                               {"tqqq": 500.0, "soxl": 0.0}, prices,
                               "2023-03-27", 100.0, None, 0.0,
                               sweep_zero_budget=True, aggregate_member_cap_usd=500.0)
    assert transfer == {"tqqq": 0.0, "soxl": 0.0}
    assert books["soxl"]["cash"] == 0.0
    r7._release(books, "2023-03-29", 2)
    transfer = r7._fund_owners(books, {"tqqq": 0.0, "soxl": 500.0},
                               {"tqqq": 500.0, "soxl": 0.0}, prices,
                               "2023-03-29", 100.0, None, 0.0,
                               sweep_zero_budget=True, aggregate_member_cap_usd=500.0)
    assert transfer == {"tqqq": -500.0, "soxl": 500.0}
    assert books["soxl"]["cash"] == 500.0
