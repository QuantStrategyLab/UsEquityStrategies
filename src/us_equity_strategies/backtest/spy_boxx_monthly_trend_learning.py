"""Offline-only SPY/BOXX monthly absolute-trend learning runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import math
from typing import Any, Mapping

import pandas as pd
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.position_sizing import risk_budgeted_target_weight
from quant_platform_kit.strategy_contracts import (
    PositionTarget,
    StrategyContext,
    StrategyDecision,
)
from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import (
    BacktestOrchestrator,
)
from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult

from us_equity_strategies.backtest.etf_rotation_simulator import (
    compute_backtest_metrics,
)
from us_equity_strategies.entrypoints._common import apply_risk_gate

PROFILE_NAME = "spy_boxx_monthly_absolute_trend_v1"
RISK_SYMBOL = "SPY"
DEFENSIVE_SYMBOL = "BOXX"
FORMATION_SESSIONS = 251
OOS_FOLD_SESSIONS = 126
EMBARGO_SESSIONS = 20
REQUIRED_SESSIONS = FORMATION_SESSIONS + 2 * OOS_FOLD_SESSIONS + EMBARGO_SESSIONS
SMA_SESSIONS = 200
COST_PER_SIDE_BPS = 5.0
STOP_LOSS_DISTANCE = 0.05
STOP_SLIPPAGE_BPS = 5.0
RISK_MANDATE_ID = "bootstrap_small_account_v2"
AVAILABLE_ACCOUNT_EXPOSURE = 0.50
EFFECTIVE_EXPOSURE_CAP = 0.50
PRODUCT_LEVERAGE_FACTORS = {RISK_SYMBOL: 1, DEFENSIVE_SYMBOL: 1}


class _DiscardingResultSink:
    """BacktestOrchestrator-compatible sink that deliberately persists nothing."""

    def save_backtest_result(self, result: BacktestResult) -> None:
        return None


@dataclass
class _FoldSummary:
    returns: pd.Series
    turnover: float
    costs: float
    risk_rejects: int


@dataclass
class SpyBoxxMonthlyTrendLearningRunner:
    """In-memory BacktestOrchestrator adapter for the frozen learning protocol."""

    market_history: pd.DataFrame
    _bars: dict[str, pd.DataFrame] = field(init=False, repr=False)
    _close: pd.DataFrame = field(init=False, repr=False)
    _stop_events: int = field(default=0, init=False)
    _stop_gap_open_events: int = field(default=0, init=False)
    _stop_slippage_costs: float = field(default=0.0, init=False)
    _drawdown_breaker: bool = field(default=False, init=False)
    _max_effective_exposure: float = field(default=0.0, init=False)
    fold_summaries: list[_FoldSummary] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._bars = _frozen_ohlc_matrices(self.market_history)
        self._close = self._bars["close"]

    def _simulate(
        self, *, start_index: int, end_index: int
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        index = self._close.index[start_index : end_index + 1]
        daily_returns = pd.Series(0.0, index=index, dtype=float)
        daily_turnover = pd.Series(0.0, index=index, dtype=float)
        daily_costs = pd.Series(0.0, index=index, dtype=float)
        daily_rejects = pd.Series(0, index=index, dtype=int)
        active_symbol: str | None = None
        active_weight = 0.0
        entry_price: float | None = None
        completed_equity = 1.0
        peak_completed_equity = 1.0
        stop_events = 0
        stop_gap_open_events = 0
        stop_slippage_costs = 0.0
        drawdown_breaker = False
        max_effective_exposure = 0.0

        for index_position in range(start_index, end_index + 1):
            as_of = self._close.index[index_position]
            turnover = 0.0
            gross_return = 0.0
            stopped_this_session = False
            if active_symbol is not None and index_position > start_index:
                previous_close = float(
                    self._close[active_symbol].iloc[index_position - 1]
                )
                open_price = float(
                    self._bars["open"][active_symbol].iloc[index_position]
                )
                low_price = float(
                    self._bars["low"][active_symbol].iloc[index_position]
                )
                close_price = float(self._close[active_symbol].iloc[index_position])
                stop_price = float(entry_price) * (1.0 - STOP_LOSS_DISTANCE)
                fill_price: float | None = None
                if open_price <= stop_price:
                    fill_price = open_price * (1.0 - STOP_SLIPPAGE_BPS / 10_000.0)
                    stop_gap_open_events += 1
                elif low_price <= stop_price:
                    fill_price = stop_price * (1.0 - STOP_SLIPPAGE_BPS / 10_000.0)
                if fill_price is None:
                    gross_return = active_weight * (close_price / previous_close - 1.0)
                else:
                    gross_return = active_weight * (fill_price / previous_close - 1.0)
                    stop_events += 1
                    stop_slippage_costs += active_weight * (
                        STOP_SLIPPAGE_BPS / 10_000.0
                    )
                    turnover += active_weight
                    active_symbol = None
                    active_weight = 0.0
                    entry_price = None
                    stopped_this_session = True

            drawdown = 1.0 - completed_equity / peak_completed_equity
            scalar = _drawdown_scalar(drawdown)
            if scalar == 0.0:
                drawdown_breaker = True

            if (
                not stopped_this_session
                and not drawdown_breaker
                and index_position >= SMA_SESSIONS
                and (
                    index_position == start_index
                    or _is_first_eligible_session(self._close.index, index_position)
                )
            ):
                decision = _monthly_decision_from_close(
                    self._close,
                    as_of=as_of,
                    account_equity=completed_equity,
                    drawdown_scalar=scalar,
                )
                if not decision.positions:
                    daily_rejects.loc[as_of] = 1
                next_symbol = (
                    decision.positions[0].symbol if decision.positions else None
                )
                next_weight = (
                    float(decision.positions[0].target_weight or 0.0)
                    if decision.positions
                    else 0.0
                )
                if next_symbol != active_symbol:
                    turnover += active_weight + next_weight
                    active_symbol = next_symbol
                    active_weight = next_weight
                    entry_price = (
                        float(self._close[next_symbol].iloc[index_position])
                        if next_symbol is not None and next_weight > 0.0
                        else None
                    )
                elif next_symbol is not None:
                    turnover += abs(next_weight - active_weight)
                    if next_weight > active_weight:
                        entry_price = _blended_entry_price(
                            float(entry_price),
                            active_weight,
                            float(self._close[next_symbol].iloc[index_position]),
                            next_weight - active_weight,
                        )
                    active_weight = next_weight
                max_effective_exposure = max(
                    max_effective_exposure,
                    active_weight
                    * PRODUCT_LEVERAGE_FACTORS.get(active_symbol or "", 0),
                )

            cost = turnover * COST_PER_SIDE_BPS / 10_000.0
            net_return = gross_return - cost
            daily_returns.loc[as_of] = net_return
            daily_turnover.loc[as_of] = turnover
            daily_costs.loc[as_of] = cost
            completed_equity *= 1.0 + net_return
            peak_completed_equity = max(peak_completed_equity, completed_equity)

            latest_drawdown = 1.0 - completed_equity / peak_completed_equity
            if _drawdown_scalar(latest_drawdown) == 0.0:
                drawdown_breaker = True
            if drawdown_breaker and active_symbol is not None:
                turnover += active_weight
                daily_turnover.loc[as_of] += active_weight
                breaker_cost = active_weight * COST_PER_SIDE_BPS / 10_000.0
                daily_costs.loc[as_of] += breaker_cost
                daily_returns.loc[as_of] -= breaker_cost
                completed_equity *= 1.0 - breaker_cost
                active_symbol = None
                active_weight = 0.0
                entry_price = None

        self._stop_events += stop_events
        self._stop_gap_open_events += stop_gap_open_events
        self._stop_slippage_costs += stop_slippage_costs
        self._drawdown_breaker = self._drawdown_breaker or drawdown_breaker
        self._max_effective_exposure = max(
            self._max_effective_exposure, max_effective_exposure
        )
        return daily_returns, daily_turnover, daily_costs, daily_rejects

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        if strategy_profile != PROFILE_NAME:
            raise ValueError(f"unsupported strategy_profile={strategy_profile!r}")
        if start_date is None or end_date is None:
            raise ValueError(
                "frozen learning folds require explicit start_date and end_date"
            )

        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        start_index = int(self._close.index.get_loc(start))
        end_index = int(self._close.index.get_loc(end))
        returns, turnover, costs, risk_rejects = self._simulate(
            start_index=start_index, end_index=end_index
        )
        if len(returns) != OOS_FOLD_SESSIONS:
            raise ValueError("each frozen OOS fold must contain exactly 126 sessions")

        metrics = compute_backtest_metrics(returns)
        self.fold_summaries.append(
            _FoldSummary(
                returns=returns.copy(),
                turnover=float(turnover.sum()),
                costs=float(costs.sum()),
                risk_rejects=int(risk_rejects.sum()),
            )
        )
        return BacktestResult(
            strategy_profile=PROFILE_NAME,
            domain="us_equity",
            param_set_id="",
            params=dict(params),
            sharpe_ratio=float(metrics["sharpe_ratio"]),
            max_drawdown=float(metrics["max_drawdown"]),
            cagr=float(metrics["annual_return"]),
            volatility=float(metrics["annual_volatility"]),
            total_return=float(metrics["total_return"]),
            start_date=start_date,
            end_date=end_date,
            observation_count=int(metrics["days"]),
            source_script=__name__,
            computed_at=datetime.now(timezone.utc).isoformat(),
            cost_model="fixed_5bps_per_side_allocation_change_plus_stop_slippage",
        )


def build_monthly_decision(
    market_history: pd.DataFrame, *, as_of: object
) -> StrategyDecision:
    """Build a lagged 200-session decision through the approved QPK gate."""
    return _monthly_decision_from_close(
        _frozen_ohlc_matrices(market_history)["close"],
        as_of=pd.Timestamp(as_of),
        account_equity=1.0,
        drawdown_scalar=1.0,
    )


def _monthly_decision_from_close(
    close: pd.DataFrame,
    *,
    as_of: pd.Timestamp,
    account_equity: float,
    drawdown_scalar: float,
) -> StrategyDecision:
    try:
        position = close.index.get_loc(as_of)
    except KeyError as exc:
        raise ValueError("as_of must be an eligible trading session") from exc
    if not isinstance(position, int) or position < SMA_SESSIONS:
        raise ValueError(
            "at least 200 completed sessions are required before a decision"
        )

    completed_spy = close[RISK_SYMBOL].iloc[position - SMA_SESSIONS : position]
    selected_symbol = (
        RISK_SYMBOL
        if completed_spy.iloc[-1] > completed_spy.mean()
        else DEFENSIVE_SYMBOL
    )
    target_weight = risk_budgeted_target_weight(
        risk_mandate_id=RISK_MANDATE_ID,
        account_equity=account_equity,
        risk_fraction=0.01,
        stop_loss_distance=STOP_LOSS_DISTANCE,
        drawdown_scalar=drawdown_scalar,
        available_account_exposure=AVAILABLE_ACCOUNT_EXPOSURE,
        product_leverage_factor=PRODUCT_LEVERAGE_FACTORS[selected_symbol],
        inputs_fresh=True,
    )
    snapshot = PortfolioSnapshot(
        as_of=as_of.to_pydatetime(), total_equity=account_equity, positions=()
    )
    context = StrategyContext(
        as_of=as_of.to_pydatetime(),
        portfolio=snapshot,
        market_data={"learning_only": True, "no_order": True},
        state={},
        runtime_config={},
    )
    decision = StrategyDecision(
        positions=(PositionTarget(symbol=selected_symbol, target_weight=target_weight),)
        if target_weight > 0.0
        else (),
        diagnostics={
            "signal_lag_sessions": 1,
            "sma_sessions": SMA_SESSIONS,
            "learning_only": True,
            "no_order": True,
            "risk_mandate_id": RISK_MANDATE_ID,
            "stop_loss_distance": STOP_LOSS_DISTANCE,
            "drawdown_scalar": drawdown_scalar,
            "available_account_exposure": AVAILABLE_ACCOUNT_EXPOSURE,
        },
    )
    return apply_risk_gate(
        decision,
        ctx=context,
        risk_mandate_id=RISK_MANDATE_ID,
        product_leverage_factors={
            selected_symbol: PRODUCT_LEVERAGE_FACTORS[selected_symbol]
        },
        available_account_exposure=AVAILABLE_ACCOUNT_EXPOSURE,
    )


def run_spy_boxx_monthly_trend_learning(
    market_history: pd.DataFrame,
) -> dict[str, object]:
    """Run the fixed two-fold, embargoed in-memory protocol."""
    runner = SpyBoxxMonthlyTrendLearningRunner(market_history)
    index = runner._close.index
    windows = (
        (
            index[FORMATION_SESSIONS].date(),
            index[FORMATION_SESSIONS + OOS_FOLD_SESSIONS - 1].date(),
        ),
        (
            index[FORMATION_SESSIONS + OOS_FOLD_SESSIONS + EMBARGO_SESSIONS].date(),
            index[-1].date(),
        ),
    )
    orchestrator = BacktestOrchestrator(store=_DiscardingResultSink())
    orchestrator.register_runner("us_equity", runner)
    results = orchestrator.walk_forward(
        PROFILE_NAME,
        domain="us_equity",
        params={"learning_only": True, "no_order": True},
        windows=windows,
    )
    if len(results) != 2 or len(runner.fold_summaries) != 2:
        raise RuntimeError("frozen walk-forward protocol did not complete both folds")

    returns = pd.concat([summary.returns for summary in runner.fold_summaries])
    metrics = compute_backtest_metrics(returns)
    turnover = (
        sum(summary.turnover for summary in runner.fold_summaries)
        * 252.0
        / len(returns)
    )
    costs = sum(summary.costs for summary in runner.fold_summaries)
    risk_rejects = sum(summary.risk_rejects for summary in runner.fold_summaries)
    accepted = (
        not runner._drawdown_breaker
        and risk_rejects == 0
        and float(metrics["sharpe_ratio"]) > 0.0
        and float(metrics["annual_return"]) > 0.0
        and float(metrics["max_drawdown"]) >= -0.20
        and turnover <= 12.0
    )
    return {
        "folds": 2,
        "sessions": len(returns),
        "sharpe": float(metrics["sharpe_ratio"]),
        "cagr": float(metrics["annual_return"]),
        "mdd": float(metrics["max_drawdown"]),
        "turnover": float(turnover),
        "costs": float(costs),
        "risk_rejects": risk_rejects,
        "risk_mandate_id": RISK_MANDATE_ID,
        "effective_exposure_cap": EFFECTIVE_EXPOSURE_CAP,
        "max_effective_exposure": runner._max_effective_exposure,
        "stop_events": runner._stop_events,
        "stop_gap_open_events": runner._stop_gap_open_events,
        "stop_slippage_costs": runner._stop_slippage_costs,
        "drawdown_breaker": runner._drawdown_breaker,
        "embargo_sessions": EMBARGO_SESSIONS,
        "decision_label": "PROMOTION_CANDIDATE_DECISION_REQUIRED"
        if accepted
        else "PARK",
        "learning_only": True,
        "promotion_eligible": False,
        "live_ready": False,
        "size_zero_required": True,
        "no_order": True,
    }


def _frozen_ohlc_matrices(market_history: pd.DataFrame) -> dict[str, pd.DataFrame]:
    required_columns = {"date", "symbol", "open", "high", "low", "close"}
    missing = sorted(required_columns.difference(market_history.columns))
    if missing:
        raise ValueError(
            f"market_history requires adjusted OHLC columns: {', '.join(missing)}"
        )
    frame = market_history.loc[
        :, ["date", "symbol", "open", "high", "low", "close"]
    ].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.tz_localize(
        None
    ).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["date"].isna().any() or set(frame["symbol"]) != {
        RISK_SYMBOL,
        DEFENSIVE_SYMBOL,
    }:
        raise ValueError(
            "frozen learning input permits only complete SPY/BOXX sessions"
        )
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError(
            "market_history must contain one adjusted bar per symbol and session"
        )
    numeric = frame[["open", "high", "low", "close"]]
    if not numeric.apply(lambda column: column.map(math.isfinite)).all().all():
        raise ValueError("adjusted OHLC prices must be finite")
    if (
        (numeric <= 0.0).any().any()
        or (frame["high"] < frame[["open", "close"]].max(axis=1)).any()
        or (frame["low"] > frame[["open", "close"]].min(axis=1)).any()
    ):
        raise ValueError(
            "adjusted OHLC prices must be positive and internally consistent"
        )

    bars = {
        column: frame.pivot(index="date", columns="symbol", values=column).sort_index()
        for column in ("open", "high", "low", "close")
    }
    if any(
        list(matrix.columns) != [DEFENSIVE_SYMBOL, RISK_SYMBOL]
        or matrix.isna().any().any()
        or len(matrix) < REQUIRED_SESSIONS
        for matrix in bars.values()
    ):
        raise ValueError(
            "frozen learning input requires at least 523 complete aligned SPY/BOXX sessions"
        )
    return {
        column: matrix.iloc[-REQUIRED_SESSIONS:].loc[:, [RISK_SYMBOL, DEFENSIVE_SYMBOL]]
        for column, matrix in bars.items()
    }


def _blended_entry_price(
    current_price: float,
    current_weight: float,
    added_price: float,
    added_weight: float,
) -> float:
    return (
        current_price * current_weight + added_price * added_weight
    ) / (current_weight + added_weight)


def _drawdown_scalar(drawdown: float) -> float:
    if not math.isfinite(drawdown) or drawdown < 0.0:
        return 0.0
    if drawdown <= 0.05:
        return 1.0
    if drawdown <= 0.10:
        return 0.5
    return 0.0


def _is_first_eligible_session(index: pd.DatetimeIndex, position: int) -> bool:
    return position == 0 or index[position].month != index[position - 1].month


__all__ = [
    "AVAILABLE_ACCOUNT_EXPOSURE",
    "COST_PER_SIDE_BPS",
    "DEFENSIVE_SYMBOL",
    "EMBARGO_SESSIONS",
    "FORMATION_SESSIONS",
    "OOS_FOLD_SESSIONS",
    "PROFILE_NAME",
    "REQUIRED_SESSIONS",
    "RISK_MANDATE_ID",
    "RISK_SYMBOL",
    "SMA_SESSIONS",
    "SpyBoxxMonthlyTrendLearningRunner",
    "build_monthly_decision",
    "run_spy_boxx_monthly_trend_learning",
]
