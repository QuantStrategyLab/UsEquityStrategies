"""Direct checks for the frozen capital-scale comparison wrapper."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module(monkeypatch):
    path = (Path(__file__).parents[2] /
            "docs/research/first_compounding_20260925/phase5_capital_scale_compare.py")
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("phase5_capital_scale_compare", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_scale_policy_is_research_only(monkeypatch) -> None:
    module = _module(monkeypatch)
    policy = module._study_policy()
    assert policy["initial_research_principal_usd"] == [1000, 1250, 10000, 100000]
    assert policy["research_only"] and policy["development"]
    assert not any(policy[key] for key in
                   ("paper_authorized", "shadow_authorized", "live_authorized"))


def test_activation_uses_actual_fills_not_positive_budget(monkeypatch) -> None:
    module = _module(monkeypatch)
    prices = {day: {"tqqq_open": 25, "qqqm_open": 50, "boxx_open": 100}
              for day in ("2024-01-02", "2024-01-03")}
    ledger = [
        {"date": "2024-01-02", "target_usd": {"TQQQ": 30, "QQQM": 100, "BOXX": 200},
         "shares": {"TQQQ": 0, "QQQM": 2, "BOXX": 2},
         "trade_shares": {"TQQQ": 0}},
        {"date": "2024-01-03", "target_usd": {"TQQQ": 55, "QQQM": 100, "BOXX": 200},
         "shares": {"TQQQ": 1, "QQQM": 2, "BOXX": 2},
         "trade_shares": {"TQQQ": 1}},
    ]
    result = module._activation(ledger, prices)
    assert result["first_tqqq_purchase_date"] == "2024-01-03"
    assert result["tqqq_held_sessions"] == 1
    assert result["positive_tqqq_target_but_zero_shares_sessions"] == 1
    assert result["tqqq_target_share_shortfall_sessions"] == 2
    assert result["mean_tqqq_whole_share_rounding_gap_usd"] == pytest.approx(5)
