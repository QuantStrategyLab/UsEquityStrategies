"""Direct synthetic checks for the frozen Phase 4 funding and known-at rules."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module(monkeypatch):
    path = (Path(__file__).parents[2] /
            "docs/research/first_compounding_20260925/phase4_qqqm_tqqq_boxx_compare.py")
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("phase4_qqqm_tqqq_boxx_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state(*, cash=1000, tqqq=0, qqqm=0, boxx=0, claims=None):
    return {"cash": cash, "shares": {"TQQQ": tqqq, "QQQM": qqqm, "BOXX": boxx},
            "pending": [], "claims": list(claims or []), "nav": 1000}


def _row():
    return {f"{symbol}_open": price for symbol, price in
            (("tqqq", 10), ("qqqm", 10), ("boxx", 10))}


def test_process_date_itself_cannot_change_decision_equity(monkeypatch) -> None:
    module = _module(monkeypatch)
    state = _state(claims=[{"symbol": "QQQM", "amount": 20,
        "process_date": "2024-06-18", "payable_date": "2024-06-18", "paid": True}])
    assert module._decision_equity(state, "2024-06-17") == 980
    assert module._decision_equity(state, "2024-06-18") == 980
    assert module._decision_equity(state, "2024-06-19") == 1000


def test_member_cash_is_reserved_before_both_qqqm_and_boxx(monkeypatch) -> None:
    module = _module(monkeypatch)
    state = _state()
    result = module._execute(state, _row(), signal_day="2024-06-19", index=1,
        budget=200, target_usd={"TQQQ": 50, "QQQM": 350, "BOXX": 390},
        cash_target=10, fee_rate=0)
    assert state["shares"] == {"TQQQ": 5, "QQQM": 35, "BOXX": 39}
    assert state["cash"] == 210
    assert result["member_reserved_cash_at_open_usd"] == 150


def test_same_open_sale_and_unrecognized_payment_cannot_fund_buys(monkeypatch) -> None:
    module = _module(monkeypatch)
    state = _state(cash=0, boxx=10)
    module._execute(state, {"tqqq_open": 10, "qqqm_open": 10, "boxx_open": 100},
        signal_day="2024-06-18", index=1, budget=50,
        target_usd={"TQQQ": 50, "QQQM": 0, "BOXX": 0}, cash_target=0,
        fee_rate=0.001)
    assert state["shares"] == {"TQQQ": 0, "QQQM": 0, "BOXX": 0}
    assert state["cash"] == 0
    assert state["pending"] == [{"release": 3, "amount": 999, "symbol": "BOXX"}]

    state = _state(claims=[{"symbol": "QQQM", "amount": 200,
        "process_date": "2024-06-18", "payable_date": "2024-06-18", "paid": True}])
    module._execute(state, _row(), signal_day="2024-06-18", index=1,
        budget=0, target_usd={"TQQQ": 0, "QQQM": 1000, "BOXX": 0},
        cash_target=0, fee_rate=0)
    assert state["shares"]["QQQM"] == 80
    assert state["cash"] == 200


def test_unrecognized_tqqq_claim_cannot_release_member_cash(monkeypatch) -> None:
    module = _module(monkeypatch)
    resulting_states = []
    for amount in (100, 200):
        state = _state(claims=[{"symbol": "TQQQ", "amount": amount,
            "process_date": "2024-06-21", "payable_date": "2024-06-21", "paid": False}])
        state["nav"] = 1000 + amount
        assert module._decision_equity(state, "2024-06-18") == 1000
        result = module._execute(state, _row(), signal_day="2024-06-18", index=1,
            budget=100, target_usd={"TQQQ": 0, "QQQM": 900, "BOXX": 0},
            cash_target=0, fee_rate=0)
        resulting_states.append((state["shares"].copy(), state["cash"],
                                 result["member_reserved_cash_at_open_usd"]))
    assert resulting_states == [({"TQQQ": 0, "QQQM": 90, "BOXX": 0}, 100, 100)] * 2


def test_same_day_processing_and_payment_remains_restricted(monkeypatch) -> None:
    module = _module(monkeypatch)
    actions = {symbol: {"cash_dividends": [], "forward_splits": []}
               for symbol in ("TQQQ", "QQQM", "BOXX")}
    actions["TQQQ"]["cash_dividends"] = [{"symbol": "TQQQ",
        "ex_date": "2024-06-17", "process_date": "2024-06-18",
        "payable_date": "2024-06-18", "rate": 0.1}]
    assert len(module._events(actions)["TQQQ"]["dividends"]["2024-06-17"]) == 1
    state = _state(cash=800, claims=[{"symbol": "TQQQ", "amount": 200,
        "process_date": "2024-06-18", "payable_date": "2024-06-18", "paid": False}])
    module._due_events(state, "2024-06-18", 1)
    assert state["cash"] == 1000
    assert module._restricted_paid_cash(state, "2024-06-18") == 200
    assert module._restricted_paid_cash(state, "2024-06-19") == 0


def test_frozen_policy_parses_and_keeps_separate_candidates(monkeypatch) -> None:
    module = _module(monkeypatch)
    policy = module._policy()
    assert policy["candidate_id"] != policy["comparator_id"]
    assert policy["data_window"]["historical_status"] == "seen_development"
    assert policy["paths"]["enhanced"]["tqqq_member_budget_cap_nav_fraction"] == 0.05


def test_drawdown_amount_and_recovery_include_initial_nav(monkeypatch) -> None:
    module = _module(monkeypatch)
    rows = [{"date": "2024-01-03", "economic_nav_close_usd": 90,
             "total_cost_usd": 1, "actual_security_value_to_nav": 0.5,
             "nasdaq_lookthrough_to_nav": 0.6, "tqqq_value_usd": 0,
             "qqqm_value_usd": 45, "boxx_value_usd": 0, "settled_cash_usd": 45,
             "outer_free_settled_cash_usd": 45, "pending_sale_usd": 0,
             "receivable_usd": 0, "decision_equity_usd": 100,
             "unrecognized_claim_usd": 0, "account_identity_error_usd": 0},
            {"date": "2024-01-04", "economic_nav_close_usd": 101,
             "total_cost_usd": 0, "actual_security_value_to_nav": 0.5,
             "nasdaq_lookthrough_to_nav": 0.6, "tqqq_value_usd": 0,
             "qqqm_value_usd": 50.5, "boxx_value_usd": 0, "settled_cash_usd": 50.5,
             "outer_free_settled_cash_usd": 50.5, "pending_sale_usd": 0,
             "receivable_usd": 0, "decision_equity_usd": 90,
             "unrecognized_claim_usd": 0, "account_identity_error_usd": 0}]
    result = module._metrics(rows, 100, "2024-01-02")
    assert result["max_drawdown"] == pytest.approx(-0.1)
    assert result["dollar_loss_at_max_drawdown_usd"] == 10
    assert result["peak_to_recovery_sessions"] == 2
    assert result["peak_to_recovery_calendar_days"] == 2
