"""Synthetic causal and accounting checks for the separate research candidate."""

from __future__ import annotations

import pandas as pd

from us_equity_strategies.research import tqqq_qqq_guard_cash_research as candidate
from us_equity_strategies.research.tqqq_qqq_guard_cash_research import _decision, _simulate


def _contract() -> dict:
    return {
        "candidate_id": "tqqq_qqq_guard_cash_research_v1",
        "core": {"first_signal_min_qqq_bars": 257},
        "qqq_guard": {"lookback_sessions": 20, "soft_drawdown": -0.05,
                      "hard_drawdown": -0.10, "soft_scalar": 0.5,
                      "hard_scalar": 0.0, "max_price_age_days": 3},
        "portfolio": {"research_initial_usd": 10000},
        "window": {"first_signal_rule": "index 256 after 257 complete common bars"},
    }


def _rows(n: int = 265) -> list[dict]:
    days = pd.bdate_range("2023-01-02", periods=n)
    return [{"date": day.date().isoformat(), "qqq_close": 100 + i * 0.1,
             "tqqq_open": 50.0, "tqqq_close": 50.0} for i, day in enumerate(days)]


def test_prefix_decision_ignores_future_bars() -> None:
    rows = _rows()
    first = _decision(rows, 256, shares=0, nav=10000, use_guard=True, config=_contract())
    rows[-1]["qqq_close"] *= 0.7
    rows[-1]["tqqq_close"] *= 1.5
    assert _decision(rows, 256, shares=0, nav=10000, use_guard=True, config=_contract()) == first
    assert first["signal_date"] == rows[256]["date"]
    assert first["target_tqqq_ratio"] == 0.45


def test_split_and_dividend_conserve_wealth_with_no_price_return() -> None:
    rows = _rows()
    rows[258]["tqqq_open"] = rows[258]["tqqq_close"] = 25.0
    for row in rows[259:]:
        row["tqqq_open"] = row["tqqq_close"] = 24.0
    actions = {"forward_splits": [{"ex_date": rows[258]["date"], "new_rate": 2, "old_rate": 1}],
               "cash_dividends": [{"ex_date": rows[259]["date"],
                                   "payable_date": rows[261]["date"], "rate": 1.0}]}
    _, ledger = _simulate(rows, actions, _contract(), cost_bps=0, use_guard=True, sessions=5)
    assert ledger[0]["shares"] == 90
    assert ledger[1]["split_ratio"] == 2
    assert ledger[1]["trade_shares"] == 0
    assert ledger[1]["nav_close"] == ledger[0]["nav_close"]
    assert ledger[2]["dividend_accrued_usd"] == 180
    assert ledger[2]["nav_close"] == ledger[1]["nav_close"]
    assert ledger[4]["receivable_usd"] == 0
    assert all(row["identity_error_usd"] < 1e-6 for row in ledger)


def test_guard_hard_drawdown_blocks_tqqq_even_when_core_trend_is_up() -> None:
    rows = _rows()
    rows[256]["qqq_close"] *= 0.89
    decision = _decision(rows, 256, shares=0, nav=10000, use_guard=True, config=_contract())
    assert decision["guard_route"] == "risk_off"
    assert decision["target_tqqq_usd"] == 0


def test_soft_guard_applies_both_scalars_without_volatility_veto(monkeypatch) -> None:
    rows = _rows()
    rows[256]["qqq_close"] *= 0.94
    monkeypatch.setattr(candidate, "_resolve_volatility_delever_thresholds", lambda *_args, **_kwargs: {
        "dynamic_sample_count": 252, "metric": 0.01,
        "entry_threshold": 0.24, "exit_threshold": 0.24,
    })
    guarded = _decision(rows, 256, shares=1, nav=10000, use_guard=True, config=_contract())
    unguarded = _decision(rows, 256, shares=1, nav=10000, use_guard=False, config=_contract())
    assert guarded["guard_route"] == "risk_reduced"
    assert guarded["target_tqqq_ratio"] == 0.1125
    assert unguarded["target_tqqq_ratio"] == 0.45
