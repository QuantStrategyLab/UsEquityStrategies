"""BacktestRunner adapter for US global ETF rotation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

import pandas as pd

from typing import cast

from us_equity_strategies.backtest.combo_simulator import (
    ComboMode,
    MEGA_CAP_PROXY_SYMBOLS,
    RUSSELL_PROXY_SYMBOL,
    UsComboBacktestConfig,
    run_combo_backtest,
)
from us_equity_strategies.backtest.etf_rotation_simulator import UsRotationBacktestConfig, run_etf_rotation_backtest
from us_equity_strategies.strategies.global_etf_rotation import (
    DEFAULT_MIN_HISTORY_DAYS,
    PROFILE_NAME,
    build_target_weights,
    extract_managed_symbols_universe,
)
from us_equity_strategies.strategies.us_equity_combo import PROFILE_NAME as US_EQUITY_COMBO_PROFILE

try:
    from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult
except ImportError:  # pragma: no cover
    BacktestResult = None  # type: ignore[misc, assignment]


SUPPORTED_PROFILES = frozenset({PROFILE_NAME, US_EQUITY_COMBO_PROFILE})


def _combo_proxy_symbols() -> tuple[str, ...]:
    return tuple(dict.fromkeys([RUSSELL_PROXY_SYMBOL, *MEGA_CAP_PROXY_SYMBOLS]))


def _synthetic_market_history(
    *,
    days: int = 900,
    start: str = "2022-01-03",
    include_combo_proxies: bool = False,
) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=days)
    symbols = list(extract_managed_symbols_universe())
    if include_combo_proxies:
        symbols = list(dict.fromkeys([*symbols, *_combo_proxy_symbols()]))
    rates = {symbol: 1.00012 + (idx * 0.00003) for idx, symbol in enumerate(symbols)}
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        price = 20.0 + (hash(symbol) % 11)
        rate = rates.get(symbol, 1.00012)
        for idx, day in enumerate(dates):
            price *= rate
            close = price * (1.0 + 0.025 * ((idx % 9) - 4) / 9)
            rows.append({"date": day, "symbol": symbol, "close": close})
    return pd.DataFrame(rows)


def _slice_history(
    market_history: pd.DataFrame,
    *,
    start_date: date | None,
    end_date: date | None,
    lookback_days: int = 0,
) -> pd.DataFrame:
    frame = market_history.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=False).dt.tz_localize(None).dt.normalize()
    if start_date is not None:
        effective_start = pd.Timestamp(start_date) - pd.tseries.offsets.BDay(max(int(lookback_days), 0))
        frame = frame[frame["date"] >= effective_start]
    if end_date is not None:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def _signal_fn(history: Any, **kwargs: Any):
    return build_target_weights(history, **kwargs)


def _metrics_to_backtest_result(
    *,
    strategy_profile: str,
    params: Mapping[str, Any],
    metrics: Mapping[str, Any],
    start_date: date | None,
    end_date: date | None,
    run_duration_seconds: float,
) -> Any:
    if BacktestResult is None:
        raise ImportError("quant_platform_kit is required to build BacktestResult")
    annual_return = float(metrics.get("annual_return") or 0.0)
    max_drawdown = float(metrics.get("max_drawdown") or 0.0)
    calmar = abs(annual_return / max_drawdown) if max_drawdown else None
    return BacktestResult(
        strategy_profile=strategy_profile,
        domain="us_equity",
        param_set_id="",
        params=dict(params),
        sharpe_ratio=float(metrics.get("sharpe_ratio") or 0.0),
        calmar_ratio=calmar,
        max_drawdown=max_drawdown,
        cagr=annual_return,
        volatility=float(metrics.get("annual_volatility") or 0.0),
        total_return=float(metrics.get("total_return") or 0.0),
        start_date=start_date,
        end_date=end_date,
        observation_count=int(metrics.get("days") or 0),
        source_script="us_equity_strategies.backtest.orchestrator_runner",
        computed_at=datetime.now(timezone.utc).isoformat(),
        run_duration_seconds=run_duration_seconds,
    )


class UsEtfRotationBacktestRunner:
    """Protocol-compatible BacktestRunner for US global ETF rotation."""

    def __init__(
        self,
        *,
        market_history: pd.DataFrame | None = None,
        synthetic_days: int = 900,
    ) -> None:
        self._market_history = market_history
        self._synthetic_days = int(synthetic_days)

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Any:
        if strategy_profile not in SUPPORTED_PROFILES:
            raise ValueError(
                f"Unsupported strategy_profile={strategy_profile!r}; "
                f"supported={sorted(SUPPORTED_PROFILES)}"
            )

        min_history_days = int(params.get("min_history_days", DEFAULT_MIN_HISTORY_DAYS))
        history = self._market_history
        if history is None:
            history = _synthetic_market_history(days=max(self._synthetic_days, min_history_days + 400))
        sliced = _slice_history(
            history,
            start_date=start_date,
            end_date=end_date,
            lookback_days=min_history_days + 5,
        )
        if sliced.empty:
            raise ValueError("No market history rows for requested window")

        started = datetime.now(timezone.utc)
        result = run_etf_rotation_backtest(
            sliced,
            _signal_fn,
            config=UsRotationBacktestConfig(min_history_days=min_history_days),
            universe_symbols=extract_managed_symbols_universe(),
            strategy_kwargs={"min_history_days": min_history_days},
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        eval_frame = sliced
        if start_date is not None:
            eval_frame = sliced[sliced["date"] >= pd.Timestamp(start_date)]
        return _metrics_to_backtest_result(
            strategy_profile=strategy_profile,
            params=params,
            metrics=result.metrics,
            start_date=start_date or (eval_frame["date"].min().date() if not eval_frame.empty else None),
            end_date=end_date or (eval_frame["date"].max().date() if not eval_frame.empty else None),
            run_duration_seconds=elapsed,
        )


class UsEquityComboBacktestRunner:
    """Protocol-compatible BacktestRunner for US equity combo research."""

    def __init__(
        self,
        *,
        market_history: pd.DataFrame | None = None,
        synthetic_days: int = 900,
    ) -> None:
        self._market_history = market_history
        self._synthetic_days = int(synthetic_days)

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Any:
        if strategy_profile != US_EQUITY_COMBO_PROFILE:
            raise ValueError(
                f"Unsupported strategy_profile={strategy_profile!r}; "
                f"supported={US_EQUITY_COMBO_PROFILE!r}"
            )

        min_history_days = int(params.get("min_history_days", DEFAULT_MIN_HISTORY_DAYS))
        combo_mode = str(params.get("combo_mode", "dynamic"))
        if combo_mode not in {"static", "dynamic"}:
            raise ValueError("combo_mode must be 'static' or 'dynamic'")

        history = self._market_history
        if history is None:
            history = _synthetic_market_history(
                days=max(self._synthetic_days, min_history_days + 400),
                include_combo_proxies=True,
            )
        sliced = _slice_history(
            history,
            start_date=start_date,
            end_date=end_date,
            lookback_days=min_history_days + 5,
        )
        if sliced.empty:
            raise ValueError("No market history rows for requested window")

        started = datetime.now(timezone.utc)
        result = run_combo_backtest(
            sliced,
            _signal_fn,
            combo_config=UsComboBacktestConfig(
                combo_mode=cast(ComboMode, combo_mode),
                min_history_days=min_history_days,
            ),
            rotation_config=UsRotationBacktestConfig(min_history_days=min_history_days),
            universe_symbols=extract_managed_symbols_universe(),
            strategy_kwargs={"min_history_days": min_history_days},
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        eval_frame = sliced
        if start_date is not None:
            eval_frame = sliced[sliced["date"] >= pd.Timestamp(start_date)]
        return _metrics_to_backtest_result(
            strategy_profile=strategy_profile,
            params=params,
            metrics=result.metrics,
            start_date=start_date or (eval_frame["date"].min().date() if not eval_frame.empty else None),
            end_date=end_date or (eval_frame["date"].max().date() if not eval_frame.empty else None),
            run_duration_seconds=elapsed,
        )


def build_backtest_runner(
    strategy_profile: str,
    *,
    market_history: pd.DataFrame | None = None,
    synthetic_days: int = 900,
) -> UsEtfRotationBacktestRunner | UsEquityComboBacktestRunner:
    if strategy_profile == US_EQUITY_COMBO_PROFILE:
        return UsEquityComboBacktestRunner(
            market_history=market_history,
            synthetic_days=synthetic_days,
        )
    return UsEtfRotationBacktestRunner(
        market_history=market_history,
        synthetic_days=synthetic_days,
    )


__all__ = [
    "SUPPORTED_PROFILES",
    "UsEquityComboBacktestRunner",
    "UsEtfRotationBacktestRunner",
    "build_backtest_runner",
]
