"""Synthetic timing checks for the descriptive Phase 5 capital-curve overlay."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest


def _module(monkeypatch):
    path = (Path(__file__).parents[2] /
            "docs/research/first_compounding_20260925/phase5_curve_binding_diagnostic.py")
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("phase5_curve_binding_diagnostic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ledger():
    prior = "2023-03-27"
    rows = []
    for i in range(444):
        day = (date(2023, 3, 28) + timedelta(days=i)).isoformat()
        if i == 443:
            day = "2024-12-31"
        rows.append({"date": day, "signal_date": prior,
            "decision_equity_usd": 100, "member_budget_usd": 5,
            "economic_nav_close_usd": 100,
            "settled_cash_usd": 50, "restricted_paid_cash_usd": 0,
            "pending_sale_usd": 0, "receivable_usd": 0,
            "member_reserved_cash_at_open_usd": 5,
            "actual_security_value_to_nav": 0.5})
        prior = day
    return rows


def test_frozen_curve_is_descriptive_and_uses_current_nav_cap(monkeypatch) -> None:
    module = _module(monkeypatch)
    result = module._diagnose(
        _ledger(), 200, {"a0_usd": 100, "lower": 0.2,
                         "upper": 0.8, "curvature": 1})
    assert result["first_signal_wealth_reference_usd"] == 200
    assert result["first_signal_decision_nav_usd"] == 100
    assert result["first_signal_ratio"] == pytest.approx(0.4)
    assert result["first_signal_reference_amount_usd"] == pytest.approx(80)
    assert result["first_signal_current_cap_usd"] == pytest.approx(40)
    assert result["binding_sessions"] == 0
    assert result["minimum_cap_minus_budget_usd"] == pytest.approx(35)


def test_final_mark_cannot_change_prior_signal_curve_inputs(monkeypatch) -> None:
    module = _module(monkeypatch)
    curve = {"a0_usd": 100, "lower": 0.2, "upper": 0.8, "curvature": 1}
    original = _ledger()
    changed = _ledger()
    changed[-1]["economic_nav_close_usd"] = 300
    assert module._diagnose(original, 200, curve) == module._diagnose(changed, 200, curve)


def test_current_known_decision_equity_updates_reference_without_future_mark(monkeypatch) -> None:
    module = _module(monkeypatch)
    rows = _ledger()
    rows[-1]["decision_equity_usd"] = 300
    result = module._diagnose(
        rows, 200, {"a0_usd": 100, "lower": 0.2,
                    "upper": 0.8, "curvature": 1})
    assert result["first_signal_wealth_reference_usd"] == 200
    assert result["last_signal_wealth_reference_usd"] == 300
    assert result["last_signal_decision_nav_usd"] == 300
    assert result["last_signal_ratio"] == pytest.approx(0.35)
    assert result["last_signal_current_cap_usd"] == pytest.approx(105)
