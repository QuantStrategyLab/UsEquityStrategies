"""Opt-in A2 comparison of daily-bar timing for one security and zero-return cash.

Targets are close-time decisions supplied by the caller; the adapter never
builds a signal or fetches data. Prices must be synthetic or a coherent
corporate-action-adjusted open/close index. Raw OHLC/action accounting and
multi-asset strategy migration remain separate work. Completed-close signals
with same_close fills are hypothetical, not a live execution promise.

Gross weights drift before every rebalance. Costs are computed on pre-fill
NAV and charged at the fill (open costs precede intraday returns, close costs
follow the day's return). Like the legacy research evaluator, costs are a
separate NAV overlay and do not feed back into gross target weights.

This module is not imported by production. It loads the new QPK interface
only when called; unchanged QPK pins and legacy generator defaults remain valid.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise


@dataclass(frozen=True)
class ResearchFill:
    signal_index: int
    fill_index: int
    price: float
    turnover: float
    target_weight: float


@dataclass(frozen=True)
class ExecutionPath:
    timing: str
    model_version: str
    price_basis: str
    sessions: tuple[date, ...]
    gross_returns: tuple[float, ...]
    net_returns: tuple[float, ...]
    fill_turnover: tuple[float, ...]
    legacy_turnover: tuple[float, ...]
    fills: tuple[ResearchFill, ...]
    unfilled_signal_indices: tuple[int, ...]
    commission_bps: float
    slippage_bps: float
    cost_multiplier: float
    scope: str = "DEVELOPMENT_ONLY"
    cost_accounting: str = "at_fill_pre_fill_nav_no_weight_feedback"


def _finite(value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("RESEARCH_INPUT_INVALID")  # noqa: TRY004 - one validation error contract
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("RESEARCH_INPUT_INVALID")
    return number


def replay_targets(sessions: Sequence[date], opens: Sequence[float], closes: Sequence[float],
                   targets: Sequence[float | None], *, timing: str,
                   commission_bps: float = 0.0, slippage_bps: float = 0.0,
                   cost_multiplier: float = 1.0, price_basis: str = "synthetic") -> ExecutionPath:
    """Compare an explicitly supplied close-decision path; no terminal forced exit.

    ``None`` means no signal (e.g. warmup), zero means a deliberate cash target.
    ``legacy_turnover`` shifts close fills to the next earning session, exactly
    as the original next_close generator did. It is diagnostic only: using it
    for costs loses a final-close fill outside the window. Missing next-session
    fills are separately recorded as unfilled signals.
    """
    try:
        from quant_platform_kit.research_stats.execution_models import (
            EXECUTION_MODEL_VERSION,
            CostModel,
            FillModel,
        )
    except ImportError as exc:
        raise ValueError("QPK_EXECUTION_MODELS_REQUIRED") from exc

    fill_model = FillModel(timing)
    cost_model = CostModel(commission_bps, slippage_bps, cost_multiplier)
    days = tuple(sessions)
    n = len(days)
    if not n or any(type(d) is not date for d in days) or any(b <= a for a, b in pairwise(days)):
        raise ValueError("SESSIONS_INVALID")
    if any(len(values) != n for values in (opens, closes, targets)):
        raise ValueError("LENGTH_MISMATCH")
    if price_basis not in ("synthetic", "corporate_action_adjusted"):
        raise ValueError("CORPORATE_ACTION_ADJUSTMENT_REQUIRED")
    open_prices = tuple(_finite(v, positive=True) for v in opens)
    close_prices = tuple(_finite(v, positive=True) for v in closes)
    weights = tuple(None if v is None else _finite(v) for v in targets)
    if any(w is not None and not 0 <= w <= 1 for w in weights):
        raise ValueError("TARGET_WEIGHT_INVALID")
    scheduled = {}
    unfilled = []
    for signal, target in enumerate(weights):
        if target is None:
            continue
        point = fill_model.resolve(signal_index=signal, available_index=signal, bar_count=n)
        if point is None:
            unfilled.append(signal)
        else:
            scheduled[point.index] = (signal, target)

    gross, net, turnover, legacy, fills = [], [], [0.0] * n, [0.0] * n, []
    weight = 0.0
    for i in range(n):
        pending = scheduled.get(i)
        if timing == "next_open":
            gap = 0.0 if i == 0 else weight * (open_prices[i] / close_prices[i - 1] - 1)
            if not math.isfinite(gap) or gap <= -1:
                raise ValueError("RUINED")
            if i:
                weight = weight * (open_prices[i] / close_prices[i - 1]) / (1 + gap)
            if pending is not None:
                signal, target = pending
                turnover[i] = abs(target - weight)
                fills.append(ResearchFill(signal, i, open_prices[i], turnover[i], target))
                weight = target
            intraday = weight * (close_prices[i] / open_prices[i] - 1)
            if not math.isfinite(intraday) or intraday <= -1:
                raise ValueError("RUINED")
            daily = (1 + gap) * (1 + intraday) - 1
            net_daily = (1 + gap) * (1 + cost_model.net_return(intraday, turnover[i], charge_at="start")) - 1
            weight = weight * (close_prices[i] / open_prices[i]) / (1 + intraday)
            legacy[i] = turnover[i]
        else:
            # Direct close-to-close arithmetic preserves legacy bitwise gross results.
            asset_return = 0.0 if i == 0 else close_prices[i] / close_prices[i - 1] - 1
            daily = math.fsum((weight * asset_return,))
            if not math.isfinite(daily) or daily <= -1:
                raise ValueError("RUINED")
            weight = weight * (1 + asset_return) / (1 + daily)
            if pending is not None:
                signal, target = pending
                turnover[i] = abs(target - weight)
                fills.append(ResearchFill(signal, i, close_prices[i], turnover[i], target))
                weight = target
                if i + 1 < n:
                    legacy[i + 1] = turnover[i]
            # Cost / pre-fill NAV is converted to the day's starting-NAV fraction.
            net_daily = cost_model.net_return(daily, turnover[i] * (1 + daily), charge_at="end")
        if not math.isfinite(daily) or daily <= -1 or not math.isfinite(net_daily) or net_daily <= -1:
            raise ValueError("RUINED")
        gross.append(daily)
        net.append(net_daily)
    return ExecutionPath(timing, EXECUTION_MODEL_VERSION, price_basis, days,
                         tuple(gross), tuple(net), tuple(turnover), tuple(legacy), tuple(fills),
                         tuple(unfilled), commission_bps, slippage_bps, cost_multiplier)


def compare_execution_timings(sessions: Sequence[date], opens: Sequence[float], closes: Sequence[float],
                              targets: Sequence[float | None], **kwargs) -> tuple[ExecutionPath, ...]:
    """Return three labeled paths on identical inputs; no hypothesis or trial registration."""
    return tuple(replay_targets(sessions, opens, closes, targets, timing=timing, **kwargs)
                 for timing in ("same_close", "next_close", "next_open"))
