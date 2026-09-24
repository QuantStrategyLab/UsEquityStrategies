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

import scripts.run_walk_forward_backtest as walk_forward
from scripts.run_walk_forward_backtest import (
    LIFECYCLE_PREFLIGHT_PROFILES,
    _baseline_param_set_id,
    run_walk_forward,
)
from us_equity_strategies.backtest import soxl_trend_simulator as soxl_sim
from us_equity_strategies.backtest.orchestrator_runner import (
    SUPPORTED_PROFILES,
    UsSoxlTrendIncomeBacktestRunner,
    build_backtest_runner,
)
from us_equity_strategies.backtest.soxl_trend_simulator import (
    CORE_ASSETS,
    run_soxl_core_only_backtest,
)
from us_equity_strategies.backtest.soxl_trend_simulator import (
    PROFILE_NAME as SOXL_PROFILE,
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


def test_lifecycle_baseline_model_metadata_is_stable() -> None:
    assert hasattr(soxl_sim, "LIFECYCLE_BASELINE_MODEL_METADATA")
    metadata = soxl_sim.LIFECYCLE_BASELINE_MODEL_METADATA
    assert metadata["model_kind"] == "lifecycle_core_only_close_fill_proxy"
    assert metadata["signal_timing"] == "signal_at_close"
    assert metadata["target_lag"] == "target_lag_one"
    assert metadata["rebalance_fill"] == "rebalance_at_prior_close"
    assert metadata["includes_full_sleeve_income_layer"] is False
    assert metadata["includes_market_regime_apply"] is False


def test_soxl_lifecycle_three_day_close_fill_timing_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signal at d0 close fills at d0 close and marks at d1 close (not next-open T+1)."""
    dates = pd.bdate_range("2024-01-02", periods=3)
    soxl_closes = (100.0, 110.0, 121.0)
    rows: list[dict[str, object]] = []
    for day, soxl_px in zip(dates, soxl_closes, strict=True):
        for symbol, px in (("SOXL", soxl_px), ("SOXX", 50.0), ("BOXX", 50.0)):
            rows.append({"date": day, "symbol": symbol, "close": px})
    history = pd.DataFrame(rows)

    def fake_indicators(close_matrix: pd.DataFrame, **_kwargs: object) -> dict[str, pd.DataFrame]:
        return {
            "soxl": pd.DataFrame(
                {"price": close_matrix["SOXL"], "ma_trend": 1.0},
                index=close_matrix.index,
            ),
            "soxx": pd.DataFrame(
                {"price": close_matrix["SOXX"], "ma_trend": 1.0},
                index=close_matrix.index,
            ),
        }

    def fake_plan(
        _indicators: object,
        account_state: dict[str, object],
        translator: object = None,
        **_kwargs: object,
    ) -> dict[str, dict[str, float]]:
        del translator
        equity = float(account_state["total_strategy_equity"])
        return {"targets": {"SOXL": equity, "SOXX": 0.0, "BOXX": 0.0}}

    real_strategy_kwargs = soxl_sim._strategy_kwargs()
    monkeypatch.setattr(
        "us_equity_strategies.backtest.soxl_trend_simulator._strategy_kwargs",
        lambda _overrides=None: dict(real_strategy_kwargs),
    )
    monkeypatch.setattr(
        "us_equity_strategies.backtest.soxl_trend_simulator.build_indicator_history",
        fake_indicators,
    )
    monkeypatch.setattr(
        "us_equity_strategies.backtest.soxl_trend_simulator.build_rebalance_plan",
        fake_plan,
    )

    result = run_soxl_core_only_backtest(history, min_history_days=1, cost_bps=0.0)
    returns = result.daily_returns
    assert float(returns.iloc[0]) == 0.0
    assert float(returns.iloc[1]) == pytest.approx(0.10)
    assert float(returns.iloc[2]) == pytest.approx(0.10)


def test_run_walk_forward_persists_soxl_lifecycle_baseline(tmp_path: Path) -> None:
    payload = run_walk_forward(
        profile=SOXL_PROFILE,
        windows=walk_forward.DEFAULT_WINDOWS,
        synthetic_days=900,
        store_root=tmp_path,
    )
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "backtest" / "us_equity" / SOXL_PROFILE).rglob("*.json")
    ]
    assert payload["strategy_profile"] == SOXL_PROFILE
    assert payload["baseline"]["sharpe_ratio"] is not None
    assert records
    baseline_records = [record for record in records if "_baseline_" in record["param_set_id"]]
    assert baseline_records
    metadata = soxl_sim.LIFECYCLE_BASELINE_MODEL_METADATA
    for record in baseline_records:
        assert record["params"]["lifecycle_baseline_model"] == metadata
        assert record["params"]["min_history_days"] == 260
        assert record["params"]["cost_bps"] == 5.0

    bare_params = {"min_history_days": 260, "cost_bps": 5.0}
    labeled_params = {
        **bare_params,
        "lifecycle_baseline_model": dict(metadata),
    }
    _, effective_days, fingerprint = walk_forward._shared_market_history(
        SOXL_PROFILE,
        bare_params,
        900,
        walk_forward.DEFAULT_WINDOWS,
    )
    assert _baseline_param_set_id(
        SOXL_PROFILE,
        bare_params,
        synthetic_days=effective_days,
        windows=walk_forward.DEFAULT_WINDOWS,
        data_fingerprint=fingerprint,
    ) != _baseline_param_set_id(
        SOXL_PROFILE,
        labeled_params,
        synthetic_days=effective_days,
        windows=walk_forward.DEFAULT_WINDOWS,
        data_fingerprint=fingerprint,
    )
    assert baseline_records[0]["param_set_id"] == _baseline_param_set_id(
        SOXL_PROFILE,
        labeled_params,
        synthetic_days=effective_days,
        windows=walk_forward.DEFAULT_WINDOWS,
        data_fingerprint=fingerprint,
    )


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
