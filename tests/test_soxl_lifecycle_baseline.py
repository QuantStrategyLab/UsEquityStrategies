"""Lifecycle baseline wiring for soxl_soxx_trend_income (core-only proxy)."""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
QPK_CANDIDATES = (
    ROOT.parents[1] / "QuantPlatformKit" / "src",
    ROOT.parent.parent / "QuantPlatformKit" / "src",
)
for candidate in QPK_CANDIDATES:
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from us_equity_strategies.backtest.orchestrator_runner import (  # noqa: E402
    SUPPORTED_PROFILES,
    UsSoxlTrendIncomeBacktestRunner,
    build_backtest_runner,
)
from us_equity_strategies.backtest.soxl_trend_simulator import (  # noqa: E402
    CORE_ASSETS,
    PROFILE_NAME as SOXL_PROFILE,
    run_soxl_core_only_backtest,
)
import scripts.run_walk_forward_backtest as walk_forward  # noqa: E402
from scripts.run_walk_forward_backtest import (  # noqa: E402
    LIFECYCLE_PREFLIGHT_PROFILES,
    run_walk_forward,
)


def _synthetic_soxl_history(*, days: int = 900, start: str = "2022-01-03") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=days)
    rows: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate((*CORE_ASSETS, "SPY")):
        price = 40.0 + symbol_index * 7.0
        rate = 1.00015 + symbol_index * 0.00002
        for day_index, day in enumerate(dates):
            price *= rate
            # Mild oscillation so trend / vol indicators are finite.
            close = price * (1.0 + 0.03 * ((day_index % 11) - 5) / 11)
            rows.append({"date": day, "symbol": symbol, "close": close})
    return pd.DataFrame(rows)


def test_supported_and_lifecycle_profiles_include_soxl() -> None:
    assert SOXL_PROFILE in SUPPORTED_PROFILES
    assert SOXL_PROFILE in LIFECYCLE_PREFLIGHT_PROFILES
    assert SOXL_PROFILE in sorted(LIFECYCLE_PREFLIGHT_PROFILES)


def test_build_backtest_runner_dispatches_soxl() -> None:
    runner = build_backtest_runner(SOXL_PROFILE, synthetic_days=500)
    assert isinstance(runner, UsSoxlTrendIncomeBacktestRunner)


def test_soxl_simulator_produces_finite_daily_returns() -> None:
    history = _synthetic_soxl_history(days=700)
    result = run_soxl_core_only_backtest(history, min_history_days=260)
    returns = result.daily_returns.dropna()
    assert len(returns) > 100
    assert returns.index.min().date() >= date(2022, 1, 3)
    assert all(math.isfinite(float(value)) for value in returns)
    assert all(math.isfinite(float(value)) for value in result.metrics.values())


def test_soxl_runner_returns_backtest_result() -> None:
    runner = UsSoxlTrendIncomeBacktestRunner(
        market_history=_synthetic_soxl_history(days=700),
        synthetic_days=700,
    )
    result = runner.run(
        SOXL_PROFILE,
        {"min_history_days": 260},
        start_date=date(2023, 6, 1),
        end_date=date(2024, 6, 1),
    )
    assert result.strategy_profile == SOXL_PROFILE
    assert result.domain == "us_equity"
    assert result.observation_count > 0
    assert not runner.last_daily_returns.empty
    assert result.observation_count == len(runner.last_daily_returns)


def test_run_walk_forward_persists_soxl_lifecycle_baseline(tmp_path: Path) -> None:
    payload = run_walk_forward(
        profile=SOXL_PROFILE,
        synthetic_days=900,
        store_root=tmp_path,
    )
    records = list((tmp_path / "backtest" / "us_equity" / SOXL_PROFILE).glob("*.json"))
    assert payload["strategy_profile"] == SOXL_PROFILE
    assert payload["baseline"]["sharpe_ratio"] is not None
    assert records
    assert all("_baseline_" in json.loads(path.read_text())["param_set_id"] for path in records)


def test_shared_market_history_allows_boxx_late_listing() -> None:
    dates = pd.bdate_range("2022-01-03", "2025-05-30")
    boxx_start = pd.Timestamp("2022-12-28")
    rows = []
    for symbol in (*CORE_ASSETS, "SPY"):
        for day in dates:
            if symbol == "BOXX" and day < boxx_start:
                continue
            rows.append({"date": day, "symbol": symbol, "close": 100.0})
    history, _, fingerprint = walk_forward._shared_market_history(
        SOXL_PROFILE,
        {"min_history_days": 260},
        900,
        walk_forward.DEFAULT_WINDOWS,
        pd.DataFrame(rows),
    )
    assert fingerprint
    assert set(history["symbol"]) >= set(CORE_ASSETS) | {"SPY"}


def test_shared_market_history_rejects_missing_soxl_core_symbol() -> None:
    dates = pd.bdate_range("2022-01-03", "2025-05-30")
    rows = [
        {"date": day, "symbol": symbol, "close": 100.0}
        for symbol in ("SOXX", "BOXX", "SPY")
        for day in dates
    ]
    with pytest.raises(ValueError, match="missing required symbols: SOXL"):
        walk_forward._shared_market_history(
            SOXL_PROFILE,
            {"min_history_days": 260},
            900,
            walk_forward.DEFAULT_WINDOWS,
            pd.DataFrame(rows),
        )
