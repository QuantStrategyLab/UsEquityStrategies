"""Focused checks of observed high-water loss-space semantics."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    directory = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location("phase5_wealth_floor_audit", directory / "phase5_wealth_floor_audit.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))


def test_high_water_uses_only_current_and_prior_decision_equity() -> None:
    rows = [{"date": "2023-03-28", "signal_date": "2023-03-27", "decision_equity_usd": 100},
            {"date": "2023-03-29", "signal_date": "2023-03-28", "decision_equity_usd": 120},
            {"date": "2023-03-30", "signal_date": "2023-03-29", "decision_equity_usd": 90}]
    result = _module()._observed_gap(rows, 100)
    assert result == {"minimum_nonbreached_constant_loss_limit_usd": 30.0,
                      "first_signal_at_maximum_gap": "2023-03-29",
                      "ratio_to_initial_principal": 0.3}
