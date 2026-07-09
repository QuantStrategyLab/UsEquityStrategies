"""Shared helpers for US research scripts calling BacktestOrchestrator adapters."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import pandas as pd

from us_equity_strategies.backtest.orchestrator_runner import (
    UsEquityComboBacktestRunner,
    UsEtfRotationBacktestRunner,
)
from us_equity_strategies.strategies.global_etf_rotation import DEFAULT_MIN_HISTORY_DAYS, PROFILE_NAME
from us_equity_strategies.strategies.us_equity_combo import PROFILE_NAME as US_EQUITY_COMBO_PROFILE


def _result_to_metrics(result: Any) -> dict[str, Any]:
    return {
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown": result.max_drawdown,
        "annual_return": result.cagr,
        "total_return": result.total_return,
        "annual_volatility": result.volatility,
        "days": result.observation_count,
    }


def run_etf_rotation_profile_backtest(
    profile: str,
    *,
    market_history: pd.DataFrame | None = None,
    synthetic_days: int = 900,
    start_date: date | None = None,
    end_date: date | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single-window US ETF rotation backtest through UsEtfRotationBacktestRunner."""
    if profile != PROFILE_NAME:
        raise ValueError(f"unsupported profile={profile!r}")

    runner = UsEtfRotationBacktestRunner(
        market_history=market_history,
        synthetic_days=synthetic_days,
    )
    merged_params = {"min_history_days": DEFAULT_MIN_HISTORY_DAYS}
    if params:
        merged_params.update(dict(params))
    result = runner.run(profile, merged_params, start_date=start_date, end_date=end_date)
    return {
        "profile": profile,
        "params": merged_params,
        "start_date": result.start_date.isoformat() if result.start_date else None,
        "end_date": result.end_date.isoformat() if result.end_date else None,
        "metrics": _result_to_metrics(result),
        "source": "UsEtfRotationBacktestRunner",
        "run_id": getattr(result, "run_id", None),
    }


def run_combo_profile_backtest(
    profile: str,
    *,
    market_history: pd.DataFrame | None = None,
    synthetic_days: int = 900,
    start_date: date | None = None,
    end_date: date | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single-window US equity combo backtest through UsEquityComboBacktestRunner."""
    if profile != US_EQUITY_COMBO_PROFILE:
        raise ValueError(f"unsupported profile={profile!r}")

    runner = UsEquityComboBacktestRunner(
        market_history=market_history,
        synthetic_days=synthetic_days,
    )
    merged_params = {
        "min_history_days": DEFAULT_MIN_HISTORY_DAYS,
        "combo_mode": "dynamic",
    }
    if params:
        merged_params.update(dict(params))
    result = runner.run(profile, merged_params, start_date=start_date, end_date=end_date)
    return {
        "profile": profile,
        "params": merged_params,
        "start_date": result.start_date.isoformat() if result.start_date else None,
        "end_date": result.end_date.isoformat() if result.end_date else None,
        "metrics": _result_to_metrics(result),
        "source": "UsEquityComboBacktestRunner",
        "run_id": getattr(result, "run_id", None),
    }


__all__ = ["run_combo_profile_backtest", "run_etf_rotation_profile_backtest"]
