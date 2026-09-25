"""Synthetic execution checks for the frozen fixed-ticket-fee sensitivity."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


def _module(monkeypatch):
    path = (Path(__file__).parents[2] /
            "docs/research/first_compounding_20260925/phase5_fixed_ticket_fee_compare.py")
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("phase5_fixed_ticket_fee_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state(*, cash=100, tqqq=0, qqqm=0, boxx=0):
    return {"cash": cash, "shares": {"TQQQ": tqqq, "QQQM": qqqm, "BOXX": boxx},
            "pending": [], "claims": [], "nav": cash + 10 * (tqqq + qqqm + boxx)}


def _row():
    return {f"{symbol}_open": 10 for symbol in ("tqqq", "qqqm", "boxx")}


def _execute(module, state, *, fixed, targets, budget=0, fee_rate=0.001):
    return module._execute_with_ticket_fee(
        state, _row(), signal_day="2024-06-18", index=1, budget=budget,
        target_usd=targets, cash_target=0, fee_rate=fee_rate,
        fixed_fee_usd=fixed)


def test_zero_fixed_fee_preserves_frozen_execution(monkeypatch) -> None:
    module = _module(monkeypatch)
    targets = {"TQQQ": 50, "QQQM": 30, "BOXX": 0}
    original_state = _state(cash=100, boxx=1)
    new_state = copy.deepcopy(original_state)
    original = module.phase4._execute(
        original_state, _row(), signal_day="2024-06-18", index=1,
        budget=50, target_usd=targets, cash_target=0, fee_rate=0.001)
    new = _execute(module, new_state, fixed=0, targets=targets, budget=50)
    assert new == original
    assert new_state == original_state


def test_buy_pays_ticket_only_on_filled_side(monkeypatch) -> None:
    module = _module(monkeypatch)
    state = _state(cash=10.5)
    result = _execute(module, state, fixed=1,
                      targets={"TQQQ": 10, "QQQM": 0, "BOXX": 0})
    assert result["trades"] == {"TQQQ": 0.0, "QQQM": 0.0, "BOXX": 0.0}
    assert result["costs"] == {"TQQQ": 0.0, "QQQM": 0.0, "BOXX": 0.0}
    assert state["cash"] == 10.5

    state = _state(cash=12)
    result = _execute(module, state, fixed=1,
                      targets={"TQQQ": 10, "QQQM": 0, "BOXX": 0})
    assert result["trades"]["TQQQ"] == 1
    assert result["costs"]["TQQQ"] == pytest.approx(1.01)
    assert state["cash"] == pytest.approx(0.99)


def test_sale_fee_reduces_unsettled_proceeds_once(monkeypatch) -> None:
    module = _module(monkeypatch)
    state = _state(cash=0, tqqq=5)
    result = _execute(module, state, fixed=1,
                      targets={"TQQQ": 0, "QQQM": 0, "BOXX": 0})
    assert result["trades"]["TQQQ"] == -5
    assert result["costs"]["TQQQ"] == pytest.approx(1.05)
    assert state["cash"] == 0
    assert state["pending"] == [{"release": 3, "amount": pytest.approx(48.95),
                                 "symbol": "TQQQ"}]


def test_isolated_callback_restores_original(monkeypatch) -> None:
    module = _module(monkeypatch)
    original = module.phase4._execute
    with pytest.raises(RuntimeError), module._isolated_execution(1):
        assert module.phase4._execute is not original
        raise RuntimeError("synthetic replay failure")
    assert module.phase4._execute is original


def test_sale_that_cannot_fund_ticket_fails_closed(monkeypatch) -> None:
    module = _module(monkeypatch)
    with pytest.raises(ValueError, match="PHASE5_SALE_CANNOT_COVER_TICKET_FEE"):
        _execute(module, _state(cash=0, tqqq=1), fixed=10,
                 targets={"TQQQ": 0, "QQQM": 0, "BOXX": 0})
