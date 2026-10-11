"""No-plugin core return paths for the RS-04 fractional-Kelly evaluator (research-only).

Builds, from **raw unadjusted** daily bars plus corporate actions, the full-exposure
gross daily return path and the daily traded notional (turnover) of the live
SOXL and TQQQ strategies' *core rules only* (pre-registered variant
``no_plugin_core``): every plugin/overlay — market-regime control, volatility
retention, crisis defense, macro risk governor, TACO veto, income layer, option
overlays, AI extensions, runtime risk gate — is switched off.

Contract
--------
- Per-symbol total return ``r_t = (close_t · split_t + dividend_t) / close_{t-1} − 1``
  (forward split ratio on its ex-date; cash dividend per post-split share on its
  ex-date). Signals use a forward-built total-return index, so only information
  up to ``t`` enters the signal at close ``t``.
- Timing ``next_close``: signal at the completed close ``t``; trade to target at
  close ``t+1``; the new weights earn session ``t+2``. ``turnover[k]`` is the
  one-side traded security notional / NAV at the start of session ``k`` (cash leg
  free), matching QPK ``cost_stress_recompute``.
- Gross returns are before costs; the evaluator applies cost tiers.
- Fractional weights; strategy cash earns 0; positions start in cash.
- Warm-up: decisions before the first valid signal are skipped; any strategy
  failure after that raises (no silent skipping).

Runtime entrypoints never import this module.
"""
from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

SOXL_ASSETS = ("SOXL", "SOXX", "BOXX")
TQQQ_ASSETS = ("TQQQ", "QQQM", "BOXX")
SOXL_SIGNAL_SYMBOLS = ("SOXL", "SOXX")
TQQQ_SIGNAL_SYMBOL = "QQQ"
REFERENCE_EQUITY_USD = 100_000.0  # below every income-layer start; income layer is off anyway

SOXL_NO_PLUGIN_OVERRIDES: dict[str, object] = {
    "income_layer_enabled": False,
    "market_regime_control_enabled": False,
    "market_regime_control_apply_risk_reduced": False,
    "market_regime_control_apply_risk_off": False,
    "blend_gate_volatility_delever_retention_mode": "none",
    "blend_gate_volatility_delever_retention_ratio": 0.0,
    "blend_gate_volatility_delever_retention_context_required": False,
}
TQQQ_NO_PLUGIN_OVERRIDES: dict[str, object] = {
    "income_layer_enabled": False,
    "market_regime_control_enabled": False,
    "dual_drive_volatility_delever_retention_mode": "none",
    "dual_drive_volatility_delever_retention_ratio": 0.0,
    "dual_drive_volatility_delever_taco_veto_enabled": False,
    "dual_drive_macro_risk_governor_enabled": False,
    "dual_drive_crisis_defense_enabled": False,
}


class CoreGeneratorError(ValueError):
    """Fail-closed input/strategy error (never echoes raw data)."""


@dataclass(frozen=True)
class RawBar:
    session: date
    open: float
    close: float


@dataclass(frozen=True)
class CorporateActions:
    splits: tuple[tuple[date, float], ...] = ()  # (ex_date, new/old)
    dividends: tuple[tuple[date, float], ...] = ()  # (ex_date, cash per post-split share)


@dataclass(frozen=True)
class CorePath:
    strategy_key: str
    variant: str
    sessions: tuple[date, ...]
    gross_returns: tuple[float, ...]
    turnover: tuple[float, ...]
    weights: tuple[Mapping[str, float], ...]  # weights earning each session
    asset_returns: Mapping[str, tuple[float, ...]] = field(default_factory=dict)
    first_signal: date | None = None

    def window(self, start: date, end: date) -> CorePath:
        idx = [i for i, d in enumerate(self.sessions) if start <= d <= end]
        if not idx or self.sessions[idx[0]] != start or self.sessions[idx[-1]] != end:
            raise CoreGeneratorError("WINDOW_NOT_COVERED")
        lo, hi = idx[0], idx[-1] + 1
        if any(not w for w in self.weights[lo:hi]):
            raise CoreGeneratorError("WINDOW_STARTS_BEFORE_WARMUP_COMPLETE")
        return CorePath(self.strategy_key, self.variant, self.sessions[lo:hi], self.gross_returns[lo:hi],
                        self.turnover[lo:hi], self.weights[lo:hi],
                        {k: v[lo:hi] for k, v in self.asset_returns.items()}, self.first_signal)


def _finite_positive(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoreGeneratorError("BAR_INVALID")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise CoreGeneratorError("BAR_INVALID")
    return number


def total_return_series(bars: Sequence[RawBar], actions: CorporateActions,
                        sessions: Sequence[date]) -> tuple[float, ...]:
    """Daily total returns on ``sessions`` (first session return is 0)."""
    by_day: dict[date, float] = {}
    for bar in bars:
        if not isinstance(bar, RawBar) or bar.session in by_day:
            raise CoreGeneratorError("BAR_INVALID")
        by_day[bar.session] = _finite_positive(bar.close)
    splits: dict[date, float] = {}
    for ex, ratio in actions.splits:
        r = _finite_positive(ratio)
        if ex in splits:
            raise CoreGeneratorError("SPLIT_DUPLICATE")
        splits[ex] = r
    dividends: dict[date, float] = {}
    for ex, cash in actions.dividends:
        if isinstance(cash, bool) or not isinstance(cash, (int, float)) or not math.isfinite(cash) or cash < 0:
            raise CoreGeneratorError("DIVIDEND_INVALID")
        dividends[ex] = dividends.get(ex, 0.0) + float(cash)
    if set(splits) & set(dividends):
        raise CoreGeneratorError("SPLIT_AND_DIVIDEND_SAME_DAY_NEEDS_REVIEW")
    in_range = {d for d in (*splits, *dividends) if sessions and sessions[0] < d <= sessions[-1]}
    if in_range - set(sessions):
        raise CoreGeneratorError("CORPORATE_ACTION_ON_NON_SESSION")
    out = [0.0]
    for prev, day in zip(sessions, sessions[1:], strict=False):
        if prev not in by_day or day not in by_day:
            raise CoreGeneratorError("MISSING_BAR")
        value = (by_day[day] * splits.get(day, 1.0) + dividends.get(day, 0.0)) / by_day[prev] - 1.0
        if not math.isfinite(value) or value <= -1.0:
            raise CoreGeneratorError("RETURN_INVALID")
        out.append(value)
    return tuple(out)


def _index(returns: Sequence[float]) -> list[float]:
    level, out = 1.0, []
    for r in returns:
        level *= 1.0 + r
        out.append(level)
    return out


def _replay(strategy_key: str, sessions: Sequence[date], asset_returns: Mapping[str, Sequence[float]],
            assets: Sequence[str], decide: Callable[[int, Mapping[str, float]], Mapping[str, float] | None],
            trade_start: date | None, available_from: Mapping[str, int] | None = None) -> CorePath:
    """Common next_close engine. ``decide(i, drifted_weights)`` returns target weights at close i."""
    n = len(sessions)
    weights: dict[str, float] = {}  # weights earning the next session (post-trade)
    pending: Mapping[str, float] | None = None
    gross = [0.0] * n
    turnover = [0.0] * n
    earning: list[Mapping[str, float]] = [{} for _ in range(n)]
    first_signal = None
    start_index = 0
    if trade_start is not None:
        if trade_start not in sessions:
            raise CoreGeneratorError("TRADE_START_NOT_A_SESSION")
        start_index = max(0, sessions.index(trade_start) - 2)
    for i in range(n):
        if i > 0 and weights:
            for a, w in weights.items():
                if w > 0 and available_from and i <= available_from.get(a, 0):
                    raise CoreGeneratorError("ASSET_HELD_BEFORE_FIRST_BAR")
            r = math.fsum(w * asset_returns[a][i] for a, w in weights.items())
            gross[i] = r
            earning[i] = dict(weights)
            end = 1.0 + r
            if end <= 0.0:
                raise CoreGeneratorError("RUINED")
            weights = {a: w * (1.0 + asset_returns[a][i]) / end for a, w in weights.items()}
        # close i: execute the order decided at close i-1
        if pending is not None:
            traded = math.fsum(abs(pending.get(a, 0.0) - weights.get(a, 0.0)) for a in assets)
            if i + 1 < n:
                turnover[i + 1] = traded
            weights = {a: w for a, w in pending.items() if w > 0.0}
            pending = None
        if i < start_index or i + 1 >= n:
            continue
        target = decide(i, weights)
        if target is None:
            if first_signal is not None:
                raise CoreGeneratorError("STRATEGY_DECISION_MISSING_AFTER_WARMUP")
            continue
        if first_signal is None:
            first_signal = sessions[i]
        cleaned = {a: float(target.get(a, 0.0)) for a in assets}
        if any(not math.isfinite(w) or w < -1e-12 for w in cleaned.values()):
            raise CoreGeneratorError("TARGET_WEIGHTS_INVALID")
        total = math.fsum(max(0.0, w) for w in cleaned.values())
        if total > 1.0 + 1e-9:
            raise CoreGeneratorError("TARGET_WEIGHTS_LEVERED")
        pending = {a: max(0.0, w) for a, w in cleaned.items()}
    return CorePath(strategy_key, "no_plugin_core", tuple(sessions), tuple(gross), tuple(turnover),
                    tuple(earning), {a: tuple(asset_returns[a]) for a in asset_returns}, first_signal)


def _filtered_kwargs(fn: Callable, config: Mapping[str, object], drop: Sequence[str]) -> dict[str, object]:
    params = set(inspect.signature(fn).parameters) - set(drop)
    return {k: v for k, v in config.items() if k in params}


def generate_soxl_core_path(bars: Mapping[str, Sequence[RawBar]], actions: Mapping[str, CorporateActions],
                            sessions: Sequence[date], *, trade_start: date | None = None) -> CorePath:
    import pandas as pd

    from us_equity_strategies.backtest.soxl_trend_simulator import build_indicator_history
    from us_equity_strategies.manifests import soxl_soxx_trend_income_manifest
    from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan

    config = dict(soxl_soxx_trend_income_manifest.default_config)
    config.update(SOXL_NO_PLUGIN_OVERRIDES)
    kwargs = _filtered_kwargs(build_rebalance_plan, config, ("indicators", "account_state", "translator"))
    returns = {s: total_return_series(bars[s], actions.get(s, CorporateActions()), sessions)
               for s in SOXL_SIGNAL_SYMBOLS}
    index = pd.DatetimeIndex(pd.to_datetime(list(sessions)))
    close = pd.DataFrame({s: _index(returns[s]) for s in SOXL_SIGNAL_SYMBOLS}, index=index)
    history = build_indicator_history(
        close, trend_ma_window=int(kwargs["trend_ma_window"]),
        volatility_window=int(kwargs["blend_gate_volatility_delever_window"]),
        volatility_lookback=int(kwargs["blend_gate_volatility_delever_dynamic_lookback"]),
        volatility_percentile=float(kwargs["blend_gate_volatility_delever_dynamic_percentile"]),
        volatility_min_periods=int(kwargs["blend_gate_volatility_delever_dynamic_min_periods"]),
        volatility_floor=float(kwargs["blend_gate_volatility_delever_dynamic_floor"]),
        volatility_cap=float(kwargs["blend_gate_volatility_delever_dynamic_cap"]),
    )
    boxx_returns = _optional_returns(bars.get("BOXX"), actions.get("BOXX"), sessions)
    asset_returns = {"SOXL": returns["SOXL"], "SOXX": returns["SOXX"], "BOXX": boxx_returns}
    required = ("ma_trend", "realized_volatility_dynamic_threshold")

    def decide(i: int, drifted: Mapping[str, float]) -> Mapping[str, float] | None:
        stamp = index[i]
        snap = {}
        for key, frame in history.items():
            row = frame.loc[stamp]
            snap[key] = {k: float(v) for k, v in row.items() if pd.notna(v)}
        if "ma_trend" not in snap.get("soxl", {}) or any(k not in snap.get("soxx", {}) for k in required):
            return None
        equity = REFERENCE_EQUITY_USD
        market_values = {a: equity * drifted.get(a, 0.0) for a in SOXL_ASSETS}
        cash = equity - math.fsum(market_values.values())
        account_state = {
            "available_cash": cash, "market_values": market_values,
            "quantities": dict(market_values), "sellable_quantities": dict(market_values),
            "total_strategy_equity": equity, "cash_sweep_symbol": "BOXX", "metadata": {},
        }
        try:
            plan = build_rebalance_plan(snap, account_state, translator=lambda key, **_kw: key, **kwargs)
        except Exception as exc:  # fail closed after warm-up; see _replay
            raise CoreGeneratorError("SOXL_STRATEGY_FAILED") from exc
        targets = dict(plan.get("targets") or {})
        out = {a: float(targets.get(a, 0.0) or 0.0) / equity for a in SOXL_ASSETS}
        if out["BOXX"] > 0 and boxx_returns is None:
            raise CoreGeneratorError("BOXX_BARS_REQUIRED")
        return out

    return _replay("SOXL", sessions, {k: v for k, v in asset_returns.items() if v is not None},
                   SOXL_ASSETS, decide, trade_start, {"BOXX": _first_index(bars.get("BOXX"), sessions)})


def generate_tqqq_core_path(bars: Mapping[str, Sequence[RawBar]], actions: Mapping[str, CorporateActions],
                            sessions: Sequence[date], *, trade_start: date | None = None,
                            min_history: int = 252) -> CorePath:
    import pandas as pd
    from quant_platform_kit.common.models import PortfolioSnapshot, Position

    from us_equity_strategies.manifests import tqqq_growth_income_manifest
    from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan

    config = dict(tqqq_growth_income_manifest.default_config)
    config.update(TQQQ_NO_PLUGIN_OVERRIDES)
    kwargs = _filtered_kwargs(build_rebalance_plan, config,
                              ("qqq_history", "snapshot", "signal_text_fn", "translator"))
    unlevered = str(config.get("dual_drive_unlevered_symbol") or "QQQM").upper()
    if unlevered != "QQQM":
        raise CoreGeneratorError("UNLEVERED_SYMBOL_CHANGED")
    qqq_returns = total_return_series(bars[TQQQ_SIGNAL_SYMBOL], actions.get(TQQQ_SIGNAL_SYMBOL, CorporateActions()),
                                      sessions)
    qqq_index = _index(qqq_returns)
    asset_returns = {"TQQQ": total_return_series(bars["TQQQ"], actions.get("TQQQ", CorporateActions()), sessions)}
    for symbol in ("QQQM", "BOXX"):
        series = _optional_returns(bars.get(symbol), actions.get(symbol), sessions)
        if series is not None:
            asset_returns[symbol] = series

    def decide(i: int, drifted: Mapping[str, float]) -> Mapping[str, float] | None:
        if i + 1 < min_history:
            return None
        frame = pd.DataFrame({"date": pd.to_datetime(list(sessions[: i + 1])), "close": qqq_index[: i + 1]})
        equity = REFERENCE_EQUITY_USD
        positions = tuple(Position(symbol=a, quantity=equity * w, market_value=equity * w)
                          for a, w in drifted.items() if w > 0)
        cash = equity - math.fsum(p.market_value for p in positions)
        snapshot = PortfolioSnapshot(as_of=datetime.combine(sessions[i], time(16, 0)), total_equity=equity,
                                     buying_power=cash, cash_balance=cash, positions=positions, metadata={})
        try:
            plan = build_rebalance_plan(frame, snapshot, signal_text_fn=lambda s, **_k: str(s),
                                        translator=lambda key, **_kw: key, **kwargs)
        except Exception as exc:
            raise CoreGeneratorError("TQQQ_STRATEGY_FAILED") from exc
        values = dict(plan.get("target_values") or {})
        extra = {k for k, v in values.items() if k not in TQQQ_ASSETS and float(v or 0.0) > 0}
        if extra:
            raise CoreGeneratorError("TQQQ_UNEXPECTED_TARGET_SYMBOLS")
        out = {a: float(values.get(a, 0.0) or 0.0) / equity for a in TQQQ_ASSETS}
        for a, w in out.items():
            if w > 0 and a not in asset_returns:
                raise CoreGeneratorError(f"{a}_BARS_REQUIRED")
        return out

    return _replay("TQQQ", sessions, asset_returns, TQQQ_ASSETS, decide, trade_start,
                   {a: _first_index(bars.get(a), sessions) for a in ("QQQM", "BOXX")})


def _first_index(bars: Sequence[RawBar] | None, sessions: Sequence[date]) -> int:
    if not bars:
        return len(sessions)
    first = min(b.session for b in bars)
    return next((i for i, d in enumerate(sessions) if d >= first), len(sessions))


def _optional_returns(bars: Sequence[RawBar] | None, actions: CorporateActions | None,
                      sessions: Sequence[date]) -> tuple[float, ...] | None:
    """Total returns for an asset that may start after ``sessions[0]`` (e.g. BOXX/QQQM).

    Sessions before the asset's first bar get return 0 and must never be held.
    """
    if not bars:
        return None
    first = min(b.session for b in bars)
    tail = [d for d in sessions if d >= first]
    if not tail:
        return None
    series = total_return_series(bars, actions or CorporateActions(), tail)
    pad = len(sessions) - len(tail)
    return (0.0,) * pad + series


def path_digest_payload(path: CorePath) -> dict[str, Any]:
    """Canonical, data-free identity of a path (for private run manifests)."""
    return {"strategy_key": path.strategy_key, "variant": path.variant, "sessions": len(path.sessions),
            "start": path.sessions[0].isoformat(), "end": path.sessions[-1].isoformat(),
            "first_signal": None if path.first_signal is None else path.first_signal.isoformat()}
