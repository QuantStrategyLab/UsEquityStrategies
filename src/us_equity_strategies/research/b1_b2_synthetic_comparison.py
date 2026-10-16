"""Synthetic-only B1/B2 comparison harness for RS-04. Not a freeze, not evidence of edge.

B1 rows (exactly the four listed in the freeze contract, no pure vol-target row):
``fixed_50_subject_to_original_cap``, ``kelly_c_0.25_once``, ``kelly_c_0.5_once``,
``ewma_vol_target_and_kelly_cap``.

B2 first comparison (not a cartesian product with B1): ``original_ladder`` vs
``continuous_drawdown`` on one shared, caller-supplied base exposure.

HAR stays PARKED; B4 (Bayesian Kelly) and B5 (partial rebalance) are rejected.
Every parameter that the freeze contract leaves NOT_ATTESTED (cap, target_vol,
D_max, ladder thresholds, EWMA lambda / initial variance / warmup, f_star) is a
required caller fixture with no default, and results carry
``fixture_values_attested=False``. Fees are not modelled: rows report gross
growth and turnover side by side, never a net figure. Exposure at session ``t``
uses only returns up to ``t - 1``.
"""
from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from us_equity_strategies.research.b1_b2_rs04_preparation import (
    BUDGET_EFFECT_NONE,
    STATUS_DEVELOPMENT_ONLY,
    PreparationStop,
    compose_exposure,
    continuous_drawdown_factor,
    ewma_variance_update,
    original_ladder_factor,
)

B1_ROWS = (
    "fixed_50_subject_to_original_cap",
    "kelly_c_0.25_once",
    "kelly_c_0.5_once",
    "ewma_vol_target_and_kelly_cap",
)
B2_ROWS = ("original_ladder", "continuous_drawdown")
ALLOWED_C = (0.25, 0.5)
PARKED_ROWS = {"har_vol_target": "HAR_PARKED_DAILY_SQUARE_IS_NOT_RV"}
REJECTED_ROWS = {
    "bayesian_kelly": "B4_OUT_OF_SCOPE",
    "partial_rebalance": "B5_OUT_OF_SCOPE",
    "pure_vol_target": "NOT_IN_D03_ROW_LIST",
    "normalized_cushion": "NOT_REGISTERED_IN_FIRST_B2_TRIAL_SET",
}
_COMMON = {
    "status": STATUS_DEVELOPMENT_ONLY,
    "data": "SYNTHETIC",
    "live_ready": False,
    "no_order": True,
    "budget_effect": BUDGET_EFFECT_NONE,
    "research_frozen": False,
    "fixture_values_attested": False,
    "fees_modelled": False,
    "winner_declared": False,
}


@dataclass(frozen=True)
class B1Fixture:
    existing_cap: float | None
    f_star: float
    target_vol: float
    ewma_decay: float
    ewma_initial_variance: float
    ewma_warmup: int
    c_for_vol_target_row: float


@dataclass(frozen=True)
class B2Fixture:
    base_exposure: float
    d_max: float
    ladder_full_until: float
    ladder_half_until: float


@dataclass(frozen=True)
class ScopeLimits:
    """Caller-supplied comparison constraints, never a production risk policy."""
    total_gross_cap: float | None
    cash_reserve: float | None
    other_gross_exposure: float | None


def _effective_cap(existing_cap: float | None, limits: ScopeLimits) -> float | None:
    values = (existing_cap, limits.total_gross_cap, limits.cash_reserve, limits.other_gross_exposure)
    if any(v is None for v in values):
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or not 0.0 <= v <= 1.0 for v in values):
        raise PreparationStop("SCOPE_LIMITS_INVALID")
    remaining = min(limits.total_gross_cap, 1.0 - limits.cash_reserve) - limits.other_gross_exposure
    if remaining < 0:
        raise PreparationStop("OTHER_EXPOSURE_EXCEEDS_SCOPE_BUDGET")
    return min(existing_cap, remaining)


def synthetic_returns(*, seed: int, sessions: int, regimes: Sequence[tuple[float, float]],
                      regime_length: int) -> tuple[float, ...]:
    """Deterministic regime-switching Gaussian simple returns. Synthetic, not market data."""
    if type(sessions) is not int or sessions < 1 or type(regime_length) is not int or regime_length < 1:
        raise PreparationStop("SYNTHETIC_SHAPE_INVALID")
    if not regimes:
        raise PreparationStop("SYNTHETIC_SHAPE_INVALID")
    rng = random.Random(seed)
    out = []
    for t in range(sessions):
        mu, sigma = regimes[(t // regime_length) % len(regimes)]
        r = rng.gauss(mu, sigma)
        out.append(max(r, -0.95))
    return tuple(out)


def _path_stats(returns: Sequence[float], exposures: Sequence[float | None]) -> dict[str, object]:
    wealth, peak, max_dd, log_growth, turnover, prev = 1.0, 1.0, 0.0, 0.0, 0.0, 0.0
    parked = 0
    for r, e in zip(returns, exposures):
        x = 0.0 if e is None else e
        parked += e is None
        turnover += abs(x - prev)
        step = 1.0 + x * r
        if step <= 0.0:
            raise PreparationStop("RUINED")
        wealth *= step
        log_growth += math.log(step)
        peak = max(peak, wealth)
        max_dd = max(max_dd, 1.0 - wealth / peak)
        # drift the held exposure to the pre-trade weight of the next session
        prev = x * (1.0 + r) / step
    n = len(returns)
    used = [0.0 if e is None else e for e in exposures]
    return {
        "final_gross_wealth": wealth,
        "mean_log_growth": log_growth / n,
        "max_drawdown": max_dd,
        "mean_exposure": sum(used) / n,
        "max_exposure": max(used),
        "gross_turnover": turnover,
        "sessions_parked_or_null": parked,
        "turnover_and_growth_summable": False,
    }


def _ewma_forecasts(returns: Sequence[float], fx: B1Fixture) -> list[float | None]:
    """Forecast for session t uses returns[:t]. None until warmup is complete."""
    out: list[float | None] = []
    var = fx.ewma_initial_variance
    if isinstance(var, bool) or not isinstance(var, (int, float)) or not var > 0:
        return [None] * len(returns)
    for t in range(len(returns)):
        out.append(math.sqrt(var) if t >= fx.ewma_warmup else None)
        var = ewma_variance_update(var, returns[t], fx.ewma_decay)
    return out


def b1_exposures(returns: Sequence[float], fx: B1Fixture, *, limits: ScopeLimits) -> dict[str, list[float | None]]:
    cap = _effective_cap(fx.existing_cap, limits)
    if fx.c_for_vol_target_row not in ALLOWED_C:
        raise PreparationStop("C_NOT_IN_ALLOWED_SET")
    if type(fx.ewma_warmup) is not int or fx.ewma_warmup < 1:
        raise PreparationStop("WARMUP_INVALID")
    forecasts = _ewma_forecasts(returns, fx)
    rows: dict[str, list[float | None]] = {name: [] for name in B1_ROWS}
    for t in range(len(returns)):
        calls = {
            "fixed_50_subject_to_original_cap": dict(kind="fixed_50"),
            "kelly_c_0.25_once": dict(kind="fractional_kelly", c=0.25, f_star=fx.f_star),
            "kelly_c_0.5_once": dict(kind="fractional_kelly", c=0.5, f_star=fx.f_star),
            "ewma_vol_target_and_kelly_cap": dict(
                kind="vol_target_and_kelly", c=fx.c_for_vol_target_row, f_star=fx.f_star,
                target_vol=fx.target_vol, forecast_vol=forecasts[t]),
        }
        for name, kw in calls.items():
            res = compose_exposure(existing_cap=cap, **kw)
            rows[name].append(res["exposure"])
    return rows


def b2_exposures(returns: Sequence[float], fx: B2Fixture, *, existing_cap: float | None,
                 limits: ScopeLimits) -> dict[str, list[float | None]]:
    """Each row tracks its own gross wealth; the factor at t uses wealth through t - 1."""
    base = fx.base_exposure
    if isinstance(base, bool) or not isinstance(base, (int, float)) or not math.isfinite(base) or not 0.0 <= base <= 1.0:
        raise PreparationStop("BASE_EXPOSURE_INVALID")
    cap = _effective_cap(existing_cap, limits)
    if cap is None:
        return {name: [None] * len(returns) for name in B2_ROWS}
    base = min(base, cap)
    rows: dict[str, list[float | None]] = {}
    for name in B2_ROWS:
        wealth, peak, path = 1.0, 1.0, []
        for r in returns:
            if name == "original_ladder":
                factor = original_ladder_factor(1.0 - wealth / peak, full_until=fx.ladder_full_until,
                                                half_until=fx.ladder_half_until)
            else:
                factor = continuous_drawdown_factor(wealth, peak, fx.d_max)
            x = base * factor
            path.append(x)
            wealth *= 1.0 + x * r
            if wealth <= 0.0:
                raise PreparationStop("RUINED")
            peak = max(peak, wealth)
        rows[name] = path
    return rows


def run_comparison(returns: Sequence[float], *, b1: B1Fixture, b2: B2Fixture,
                   limits: ScopeLimits,
                   requested_rows: Sequence[str] | None = None) -> dict[str, object]:
    """Report B1 and B2 tables separately. Never crosses B1 rows with B2 rows."""
    requested = tuple(requested_rows) if requested_rows is not None else B1_ROWS + B2_ROWS
    for name in requested:
        if name in PARKED_ROWS or name in REJECTED_ROWS:
            continue
        if name not in B1_ROWS + B2_ROWS:
            raise PreparationStop("ROW_NOT_REGISTERED:" + name)
    if not returns:
        raise PreparationStop("SYNTHETIC_SHAPE_INVALID")
    if any(isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r) or r <= -1 for r in returns):
        raise PreparationStop("RETURN_INVALID")
    cap = _effective_cap(b1.existing_cap, limits)
    b1_paths = b1_exposures(returns, b1, limits=limits)
    b2_paths = b2_exposures(returns, b2, existing_cap=b1.existing_cap, limits=limits)
    b1_table: dict[str, Mapping[str, object]] = {}
    for name in B1_ROWS:
        if name not in requested:
            continue
        if cap is None:
            b1_table[name] = {"status": "UNCOMPUTABLE", "reason_code": "SCOPE_LIMITS_NOT_ATTESTED"}
        else:
            b1_table[name] = _path_stats(returns, b1_paths[name])
    b2_table = {n: (_path_stats(returns, b2_paths[n]) if cap is not None else
                    {"status": "UNCOMPUTABLE", "reason_code": "SCOPE_LIMITS_NOT_ATTESTED"})
                for n in B2_ROWS if n in requested}
    return {
        **_COMMON,
        "b1": b1_table,
        "b2": b2_table,
        "b1_x_b2_cartesian": False,
        "parked": {n: PARKED_ROWS[n] for n in requested if n in PARKED_ROWS},
        "rejected": {n: REJECTED_ROWS[n] for n in requested if n in REJECTED_ROWS},
        "trial_count": len(b1_table) + len(b2_table),
    }
