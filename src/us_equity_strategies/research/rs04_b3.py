"""Research-only daily-close B3 signals, upstream of frozen B1/B2 sizing.

No provider, filesystem, broker, plugin registration, or order entry. Prices
must already share a causal split-adjusted unit, without dividend adjustment.
A decision filled at close t may only consume completed observations <=t-1.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import math
from statistics import stdev
from typing import Mapping, Sequence

from .b1_b2_rs04_preparation import original_ladder_factor
from .rs04_b3_config import CONFIG


@dataclass(frozen=True)
class Bar:
    session: date
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Features:
    session: date
    available: date
    price: float
    ma: float
    slope: float
    adx: float
    rv_percentile: float
    rsi: float
    atr: float
    trade_price: float


@dataclass(frozen=True)
class State:
    regime: str = "TRANSITION"
    age: int = CONFIG.min_hold
    trend_count: int = 0
    range_count: int = 0
    exit_count: int = 0
    entry: float = 0.
    initial_r: float = 0.
    peak: float = 0.
    stop: float = 0.
    entry_index: int = -1
    sleeve: str = "NONE"
    cooldown_until: int = -1
    fresh_entry: bool = True
    exit_reason: str = ""
    last_index: int = -1


@dataclass(frozen=True)
class Decision:
    state: State
    targets: Mapping[str, float]
    reasons: tuple[str, ...]
    sleeve: str


@dataclass(frozen=True)
class SizedTargets:
    targets: Mapping[str, float]
    upstream_nominal: float
    sizing_factor: float
    cash: float
    effective_exposure: float
    b1_nominal_budget: float
    b2_drawdown_factor: float


def _valid(*values: float) -> bool:
    return all(type(x) in (int, float) and math.isfinite(x) for x in values)


def _weights(weights: Mapping[str, float]) -> dict[str, float]:
    if not isinstance(weights, Mapping) or any(not isinstance(k, str) or not k.isalpha()
            or not _valid(v) or v < 0 for k, v in weights.items()):
        raise ValueError("WEIGHTS_INVALID")
    return dict(weights)


def _profile(profile: str) -> tuple[float, tuple[str, str]]:
    if profile == "SOXL":
        return CONFIG.soxl_total, ("SOXL", "SOXX")
    if profile == "TQQQ":
        return CONFIG.tqqq_total, ("TQQQ", "QQQM")
    raise ValueError("PROFILE_INVALID")


def cap_targets(targets: Mapping[str, float], profile: str) -> dict[str, float]:
    out = _weights(targets)
    cap, _ = _profile(profile)
    if profile == "SOXL":
        out["SOXL"] = min(out.get("SOXL", 0.), CONFIG.soxl_single)
    total = math.fsum(out.values())
    effective = total + 2*out.get(profile, 0.)
    factor = min(1., cap/total if total else 1.,
                 CONFIG.soxl_effective/effective if profile == "SOXL" and effective else 1.)
    return {key: weight*factor for key, weight in out.items()}


def combined_targets(upstream: Mapping[str, float], *, profile: str, kelly_full: float,
                     forecast_vol: float | None, drawdown: float) -> SizedTargets:
    """B1=min(original budget, vol cap, .25*f*); B2 original ladder once.

    B1 is an absolute nominal budget, not another cash-reserve multiplier.
    B3 has already changed the signal weights. No second B3 scalar is applied.
    Uninvested budget is actual zero-yield cash, never an uncharged BOXX leg.
    """
    weights = cap_targets(upstream, profile)
    if not _valid(kelly_full, drawdown) or drawdown < 0:
        raise ValueError("SIZING_INPUT_INVALID")
    factor, budget = 0., 0.
    ladder = original_ladder_factor(drawdown, full_until=CONFIG.dd_full, half_until=CONFIG.dd_half)
    if forecast_vol is not None and _valid(forecast_vol) and forecast_vol > 0:
        budget = min(_profile(profile)[0], max(0., CONFIG.kelly_c*kelly_full),
                     CONFIG.target_vol/forecast_vol)
        nominal = math.fsum(weights.values())
        factor = min(1., budget/nominal) if nominal else 0.
        factor *= ladder
    result = cap_targets({key: value*factor for key, value in weights.items()}, profile)
    nominal = math.fsum(result.values())
    return SizedTargets(result, math.fsum(weights.values()), factor, 1.-nominal,
                        nominal+2*result.get(profile, 0.), budget, ladder)


def ewma_forecast(past_returns: Sequence[float]) -> float | None:
    """t-1-only input: first 63 ddof=1 seed, then zero-mean recursion."""
    if len(past_returns) < CONFIG.ewma_warmup or not _valid(*past_returns):
        return None
    v = stdev(past_returns[:CONFIG.ewma_warmup])**2
    for r in past_returns[CONFIG.ewma_warmup:]:
        v = CONFIG.ewma_lambda*v+(1-CONFIG.ewma_lambda)*r*r
    return math.sqrt(v*CONFIG.annual_sessions) if v > 0 else None


def _wilder(values: Sequence[float | None], period: int) -> list[float | None]:
    result, seed, previous = [], [], None
    for v in values:
        if v is None:
            result.append(None)
            continue
        if previous is None:
            seed.append(v)
            if len(seed) == period:
                previous = math.fsum(seed)/period
        else:
            previous = (previous*(period-1)+v)/period
        result.append(previous)
    return result


def _ohlc(bars: Sequence[Bar]) -> None:
    if any(not _valid(b.high, b.low, b.close) or b.low <= 0 or not b.low <= b.close <= b.high
           for b in bars) or any(b.session <= a.session for a, b in zip(bars, bars[1:])):
        raise ValueError("OHLC_INVALID")


def _atr(bars: Sequence[Bar]) -> list[float | None]:
    tr = [b.high-b.low if i == 0 else max(b.high-b.low, abs(b.high-bars[i-1].close),
          abs(b.low-bars[i-1].close)) for i, b in enumerate(bars)]
    return _wilder(tr, CONFIG.atr_period)


def build_features(signal: Sequence[Bar], traded: Sequence[Bar]) -> list[Features | None]:
    """Trailing OHLC features. No forward fill, fabricated H/L, or centred window."""
    _ohlc(signal)
    _ohlc(traded)
    if [b.session for b in signal] != [b.session for b in traded]:
        raise ValueError("SESSIONS_NOT_ALIGNED")
    n = len(signal)
    returns = [None]+[b.close/a.close-1 for a, b in zip(signal, signal[1:])]
    gains = [None]+[max(0., b.close-a.close) for a, b in zip(signal, signal[1:])]
    losses = [None]+[max(0., a.close-b.close) for a, b in zip(signal, signal[1:])]
    avg_up, avg_down = _wilder(gains, CONFIG.rsi_period), _wilder(losses, CONFIG.rsi_period)
    tr_atr, trade_atr = _atr(signal), _atr(traded)
    up, down = [None], [None]
    for a, b in zip(signal, signal[1:]):
        plus, minus = b.high-a.high, a.low-b.low
        up.append(plus if plus > minus and plus > 0 else 0.)
        down.append(minus if minus > plus and minus > 0 else 0.)
    p, m = _wilder(up, CONFIG.adx_period), _wilder(down, CONFIG.adx_period)
    dx = [None if x is None or y is None or atr is None or atr <= 0 else
          (100*abs(x-y)/(x+y) if x+y else 0.) for x, y, atr in zip(p, m, tr_atr)]
    adx = _wilder(dx, CONFIG.adx_period)
    ma, rv = [], []
    for i in range(n):
        ma.append(math.fsum(b.close for b in signal[i-CONFIG.ma_period+1:i+1])/CONFIG.ma_period
                  if i+1 >= CONFIG.ma_period else None)
        rv.append(stdev(returns[i-CONFIG.rv_period+1:i+1]) if i >= CONFIG.rv_period else None)
    result = []
    for i in range(n):
        history = [v for v in rv[max(0, i-CONFIG.percentile_period):i] if v is not None]
        if (i < CONFIG.slope_period or ma[i] is None or ma[i-CONFIG.slope_period] is None
                or adx[i] is None or trade_atr[i] is None or avg_up[i] is None
                or len(history) < CONFIG.percentile_period):
            result.append(None)
            continue
        rsi = (100*avg_up[i]/(avg_up[i]+avg_down[i]) if avg_up[i]+avg_down[i] else 50.)
        result.append(Features(signal[i].session, signal[i].session, signal[i].close, ma[i],
                               math.log(ma[i]/ma[i-CONFIG.slope_period]), adx[i],
                               sum(x <= rv[i] for x in history)/len(history), rsi,
                               trade_atr[i], traded[i].close))
    return result


def advance(state: State, feature: Features | None, *, decision: date, index: int, profile: str,
            baseline: Mapping[str, float], held: Mapping[str, float], hard_risk: bool = False) -> Decision:
    """One close decision; fill feedback is supplied separately after execution."""
    if index <= state.last_index:
        raise ValueError("DUPLICATE_OR_REVERSED_SESSION")
    _, risks = _profile(profile)
    out, current = cap_targets(baseline, profile), _weights(held)
    if feature is not None and (feature.session >= decision or feature.available >= decision):
        raise ValueError("FEATURE_NOT_AVAILABLE")
    valid = feature is not None and _valid(feature.price, feature.ma, feature.slope, feature.adx,
                feature.rv_percentile, feature.rsi, feature.atr, feature.trade_price)
    valid = valid and min(feature.price, feature.ma, feature.atr, feature.trade_price) > 0
    reasons, sleeve = [], "TREND"
    s = replace(state, last_index=index, age=state.age+1)
    if not out.get(profile, 0.):
        s = replace(s, fresh_entry=True)
    risk = hard_risk or not valid or (feature.price < CONFIG.risk_ma_ratio*feature.ma and feature.slope < 0)
    if risk:
        s = replace(s, regime="RISK_OFF", age=0, trend_count=0, range_count=0, exit_count=0)
        reasons.append("HARD_RISK" if hard_risk else "RISK_OFF_OR_MISSING_FEATURES")
    else:
        f = feature
        trend = f.adx >= CONFIG.trend_enter_adx and f.price > f.ma and f.slope > 0
        quiet = (f.adx <= CONFIG.range_enter_adx and abs(f.slope) <= CONFIG.range_slope
                 and f.rv_percentile < CONFIG.range_rv_percentile and f.price >= CONFIG.risk_ma_ratio*f.ma)
        exiting = ((s.regime == "TREND" and f.adx < CONFIG.trend_exit_adx)
                   or (s.regime == "RANGE" and f.adx > CONFIG.range_exit_adx))
        s = replace(s, trend_count=s.trend_count+1 if trend else 0,
                    range_count=s.range_count+1 if quiet else 0,
                    exit_count=s.exit_count+1 if exiting else 0)
        if s.age >= CONFIG.min_hold:
            new = s.regime
            if s.regime == "TREND" and s.exit_count >= CONFIG.exit_confirm:
                new = "TRANSITION"
            elif s.regime == "RANGE" and s.exit_count >= CONFIG.exit_confirm:
                new = "TRANSITION"
            elif s.regime in ("TRANSITION", "RISK_OFF"):
                if s.trend_count >= CONFIG.trend_confirm:
                    new = "TREND"
                elif s.range_count >= CONFIG.range_confirm:
                    new = "RANGE"
                elif s.regime == "RISK_OFF":
                    new = "TRANSITION"
            if new != s.regime:
                s = replace(s, regime=new, age=0, exit_count=0)
    if valid and s.regime == "RANGE" and feature.rv_percentile >= CONFIG.range_rv_percentile:
        s = replace(s, regime="TRANSITION", age=0, range_count=0)
        reasons.append("RANGE_VOLATILITY_EXIT")
    if s.regime == "RISK_OFF":
        out.update(dict.fromkeys(risks, 0.))
    elif s.regime == "TRANSITION":
        for asset in risks:
            out[asset] = min(out.get(asset, 0.), current.get(asset, 0.))
        if s.sleeve == "MR":
            out[profile] = 0.
            reasons.append("MR_REGIME_EXIT")
    elif s.regime == "RANGE":
        out.update(dict.fromkeys(risks, 0.))
        sleeve = "MR"
        if s.sleeve == "MR" and s.entry > 0:
            if feature.rsi > CONFIG.mr_exit_rsi or index-s.entry_index >= CONFIG.mr_max_hold:
                reasons.append("MR_EXIT")
            elif feature.trade_price <= s.entry-s.initial_r:
                reasons.append("MR_STOP")
            else:
                out[profile] = min(current.get(profile, 0.), CONFIG.mr_cap)
        elif s.entry == 0 and feature.rsi < CONFIG.mr_enter_rsi and index > s.cooldown_until:
            total_room = _profile(profile)[0]-math.fsum(out.values())
            out[profile] = min(CONFIG.mr_cap, CONFIG.mr_loss_budget/(CONFIG.entry_atr_r*feature.atr/feature.trade_price),
                               max(0., total_room))
        else:
            reasons.append("MR_NO_ENTRY")
    # Switching out of the MR sleeve closes it before any later trend entry.
    if s.sleeve == "MR" and s.regime != "RANGE":
        out[profile] = 0.
        reasons.append("MR_REGIME_EXIT")
    if s.entry > 0 and valid:
        peak = max(s.peak, feature.trade_price)
        stop = s.stop
        if peak-s.entry >= CONFIG.trail_activation_r*s.initial_r:
            stop = max(stop, s.entry, peak-CONFIG.trail_atr*feature.atr)
        s = replace(s, peak=peak, stop=stop)
        if stop and feature.trade_price <= stop:
            out[profile] = 0.
            reasons.append("PROFIT_TRAIL")
    if s.entry == 0 and sleeve != "MR" and (index <= s.cooldown_until or not s.fresh_entry):
        out[profile] = 0.
        reasons.append("WAIT_FOR_FRESH_ENTRY")
    return Decision(replace(s, exit_reason="|".join(reasons)), cap_targets(out, profile), tuple(reasons), sleeve)


def record_fill(state: State, *, old_quantity: int, quantity: int, price: float,
                fee_per_share: float, atr: float, index: int, sleeve: str) -> State:
    """Actual net fill feedback: partial sales never reset the original R/peak."""
    if old_quantity < 0 or quantity < 0 or not _valid(price, fee_per_share, atr) or price <= 0 or atr <= 0 or fee_per_share < 0:
        raise ValueError("FILL_INVALID")
    if old_quantity == 0 and quantity > 0:
        return replace(state, entry=price+fee_per_share, initial_r=CONFIG.entry_atr_r*atr,
                       peak=price, stop=0., entry_index=index, sleeve=sleeve, exit_reason="")
    if quantity == 0 and old_quantity > 0:
        protected = bool(state.exit_reason)
        return replace(state, entry=0., initial_r=0., peak=0., stop=0., entry_index=-1, sleeve="NONE",
                       cooldown_until=index+CONFIG.cooldown if protected else state.cooldown_until,
                       fresh_entry=not protected)
    if quantity > old_quantity > 0:
        # Keep the fixed initial R and old peak; only tighten break-even after adding.
        entry = (state.entry*old_quantity+(price+fee_per_share)*(quantity-old_quantity))/quantity
        return replace(state, entry=entry, stop=max(state.stop, entry) if state.stop else 0.)
    return state
