"""Synthetic accounting checks for descriptive risk from frozen daily ledgers."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[2] / "docs/research/first_compounding_20260925/boxx_outer_cash_risk.py"
    spec = importlib.util.spec_from_file_location("boxx_outer_cash_risk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(day: str, nav: float, cost: float = 0.0) -> dict:
    return {"date": day, "nav_close": nav, "total_cost_usd": cost,
            "tqqq_value_usd": nav, "boxx_value_usd": 0.0, "settled_cash_usd": 0.0,
            "pending_sale_usd": 0.0, "receivable_usd": 0.0, "identity_error_usd": 0.0}


def test_initial_nav_enters_first_return_and_drawdown_peak():
    risk = _module()
    rows = [_row("2023-01-02", 90, 1), _row("2023-01-03", 99, 2),
            _row("2023-01-04", 99)]
    metrics, dates, returns = risk._metrics(rows, 100)
    assert dates == ("2023-01-02", "2023-01-03", "2023-01-04")
    assert returns == pytest.approx((-0.1, 0.1, 0))
    assert metrics["max_drawdown"] == pytest.approx(-0.1)
    assert metrics["worst_daily_return"] == pytest.approx(-0.1)
    assert metrics["historical_worst_5pct_daily_return_mean"] == pytest.approx(-0.1)
    assert metrics["annualized_historical_daily_volatility_252"] == pytest.approx(
        math.sqrt(0.01) * math.sqrt(252))
    assert metrics["total_cost_usd"] == 3


def test_tail_count_rounds_up_and_retains_signed_returns():
    risk = _module()
    rows = [_row(f"2023-01-{day:02d}", 100) for day in range(2, 23)]
    rows[0]["nav_close"] = rows[0]["tqqq_value_usd"] = 90
    rows[1]["nav_close"] = rows[1]["tqqq_value_usd"] = 100
    metrics, _, _ = risk._metrics(rows, 100)
    assert metrics["tail_observations"] == 2
    assert metrics["actual_tail_fraction"] == pytest.approx(2 / 21)
    assert metrics["historical_worst_5pct_daily_return_mean"] == pytest.approx(-0.05)


def test_incomplete_reference_export_is_labeled_not_reconciled():
    risk = _module()
    rows = [_row("2023-01-02", 100), _row("2023-01-03", 101)]
    rows[1]["tqqq_value_usd"] = 0
    with pytest.raises(ValueError):
        risk._metrics(rows, 100)
    metrics, _, _ = risk._metrics(rows, 100, balance_components_complete=False)
    assert metrics["exported_balance_components_reconciled"] is False


@pytest.mark.parametrize("change", [
    lambda rows: rows[1].update(date=rows[0]["date"]),
    lambda rows: rows[1].update(nav_close=0),
    lambda rows: rows[1].update(identity_error_usd=1),
    lambda rows: rows[1].update(external_flow_usd=1),
])
def test_invalid_or_unfunded_ledger_fails_closed(change):
    risk = _module()
    rows = [_row("2023-01-02", 100), _row("2023-01-03", 101)]
    change(rows)
    with pytest.raises(ValueError):
        risk._metrics(rows, 100)
