"""Synthetic account-return checks for the frozen joint risk reader."""

from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import pytest


def _module():
    path = (Path(__file__).parents[2] /
            "docs/research/first_compounding_20260925/phase4_joint_account_risk.py")
    spec = importlib.util.spec_from_file_location("phase4_joint_account_risk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows() -> list[dict]:
    rows = []
    for index in range(444):
        day = date(2023, 3, 28) + timedelta(days=index)
        if index == 443:
            day = date(2024, 12, 31)
        nav = 90.0 if index == 0 else 99.0
        rows.append({"date": day.isoformat(), "economic_nav_close_usd": nav,
                     "qqqm_value_usd": nav, "tqqq_value_usd": 0.0,
                     "boxx_value_usd": 0.0, "settled_cash_usd": 0.0,
                     "pending_sale_usd": 0.0, "receivable_usd": 0.0,
                     "account_identity_error_usd": 0.0, "total_cost_usd": 0.0,
                     "trade_cost_usd": {"QQQM": 0.0, "TQQQ": 0.0, "BOXX": 0.0}})
    return rows


def test_first_return_tail_and_five_session_compounding() -> None:
    metrics, dates, returns = _module()._path_metrics(_rows(), 100.0)
    assert (dates[0], dates[-1]) == ("2023-03-28", "2024-12-31")
    assert returns[:3] == pytest.approx((-0.1, 0.1, 0.0))
    assert metrics["tail_observations"] == 23
    assert metrics["historical_worst_5pct_daily_return_mean"] == pytest.approx(-0.1 / 23)
    assert metrics["worst_observed_five_session_compounded_return"] == pytest.approx(-0.01)
    assert math.isclose(metrics["max_drawdown"], -0.1)


@pytest.mark.parametrize("change", [
    lambda rows: rows[1].update(qqqm_value_usd=98.0),
    lambda rows: rows[1].update(external_flow_usd=1.0),
    lambda rows: rows[1].update(total_cost_usd=1.0),
    lambda rows: rows[1].update(date=rows[0]["date"]),
])
def test_mismatched_or_unfunded_account_fails_closed(change) -> None:
    rows = _rows()
    change(rows)
    with pytest.raises(ValueError):
        _module()._path_metrics(rows, 100.0)
