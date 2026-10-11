"""Single-strategy fractional-Kelly walk-forward evaluator (RS-04, research-only).

What it does
------------
For one already-defined strategy (its dated per-session returns at full
exposure), each walk-forward fold:

1. fits on the **training** sessions only: excess mean ``mu`` over cash and
   sample variance ``sigma2`` (ddof=1), then shrinks ``mu`` toward zero with
   the positive-part James–Stein-style factor ``lambda = max(0, 1 − 1/t²)``,
   ``t = mu / sqrt(sigma2 / n)``;
2. computes the continuous Kelly fraction ``f* = mu_shrunk / sigma2`` and the
   applied exposure ``f = clip(c · f*, 0, 1)`` for each ``c`` (default
   0.25 and 0.5; ``c >= 1`` is refused: no full Kelly);
3. applies ``f`` unchanged to the **test** sessions (after a purge gap), with
   the remainder in cash, rebalanced to ``f`` at every session start.

Folds whose training sample is too short, one-sided or zero-variance are
``PARKED`` with exposure 0 (cash), mirroring the QPK Kelly contract.

Comparators on exactly the same stitched test sessions and cost tiers:
full exposure (f = 1), fixed 50 %, the supplied benchmark (buy and hold) and
an optional caller-supplied R8 path. R8 is **not** recomputed here.

Costs: every scaled path pays ``cost_bps_per_side × tier`` on its traded
notional (entry from cash, daily drift rebalancing, fold changes, plus the
strategy's internal turnover scaled by ``f`` when supplied), through QPK
``research_stats.cost_stress_recompute``. Cash-leg trades are not charged.

Boundaries
----------
- No leverage, no shorting: ``0 <= f <= 1``.
- Synthetic data by default (``synthetic=True``). Real data only for the
  allowlist ``{SOXL, TQQQ}`` with a ``RealDataAuthorization`` (pre-registration
  id + data manifest) that matches ``fractional_kelly_preregistration``
  exactly; anything else raises ``REAL_DATA_REQUIRES_APPROVAL``.
- The output may only inform a ``kelly_ready`` **cap note**. It never raises
  any risk budget, never authorizes promotion/paper/shadow/live, and every
  report is ``live_ready=False`` / ``no_order=True``.
- When the installed QPK lacks ``research_stats`` the whole evaluation is
  ``UNCOMPUTABLE`` (``QPK_RESEARCH_STATS_UNAVAILABLE``) with no numbers.
- Runtime entrypoints/strategies never import this module.
"""
from __future__ import annotations

import hashlib
import importlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from us_equity_strategies.research.fractional_kelly_preregistration import (
    RealDataAuthorization,
    authorize_real_data,
)
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import (
    WalkForwardPlan,
)

EVALUATOR_VERSION = "fractional_kelly_walk_forward_v1"
STATUS_COMPUTED = "COMPUTED"
STATUS_UNCOMPUTABLE = "UNCOMPUTABLE"
STATUS_PARKED = "PARKED"
UNAVAILABLE = "QPK_RESEARCH_STATS_UNAVAILABLE"
PERIODS_PER_YEAR = 252
DEFAULT_FRACTIONS = (0.25, 0.5)
DEFAULT_MULTIPLIERS = (1.0, 2.0, 3.0)
MIN_TRAIN_OBSERVATIONS = 30  # mirrors QPK Kelly contract v2 sample floor


def _stats_module() -> Any | None:
    try:
        module = importlib.import_module("quant_platform_kit.research_stats")
    except ImportError:
        return None
    needed = ("cost_stress_recompute", "probabilistic_sharpe_ratio", "BacktestReportV1")
    return module if all(hasattr(module, n) for n in needed) else None


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label}_INVALID")
    return float(value)


def _series(rows: Sequence[tuple[date, float]], label: str) -> tuple[tuple[date, ...], tuple[float, ...]]:
    if isinstance(rows, (str, bytes)):
        raise TypeError(f"{label}_INVALID")
    dates, values = [], []
    for item in rows:
        if not isinstance(item, tuple) or len(item) != 2 or type(item[0]) is not date:
            raise ValueError(f"{label}_INVALID")
        r = _finite(item[1], label)
        if r <= -1.0:
            raise ValueError(f"{label}_INVALID")
        dates.append(item[0])
        values.append(r)
    if any(b <= a for a, b in itertools.pairwise(dates)):
        raise ValueError(f"{label}_DATES_NOT_STRICTLY_INCREASING")
    return tuple(dates), tuple(values)


def fit_fractional_kelly(strategy: Sequence[float], cash: Sequence[float], c: float) -> dict:
    """Fit one training sample; PARKED (f=0) when the sample cannot support it."""
    n = len(strategy)
    excess = [s - k for s, k in zip(strategy, cash)]
    base = {"c": c, "train_observations": n}
    if n < MIN_TRAIN_OBSERVATIONS:
        return {**base, "status": STATUS_PARKED, "reason_code": "INSUFFICIENT_TRAIN_OBSERVATIONS", "exposure": 0.0}
    if not (any(x > 0 for x in strategy) and any(x < 0 for x in strategy)):
        return {**base, "status": STATUS_PARKED, "reason_code": "ONE_SIDED_TRAIN_RETURNS", "exposure": 0.0}
    mu = math.fsum(excess) / n
    mean_s = math.fsum(strategy) / n
    sigma2 = math.fsum((s - mean_s) ** 2 for s in strategy) / (n - 1)
    if not math.isfinite(sigma2) or sigma2 <= 0.0:
        return {**base, "status": STATUS_PARKED, "reason_code": "ZERO_TRAIN_VARIANCE", "exposure": 0.0}
    t_stat = mu / math.sqrt(sigma2 / n)
    shrink = 0.0 if t_stat == 0.0 else max(0.0, 1.0 - 1.0 / (t_stat * t_stat))
    mu_shrunk = shrink * mu
    kelly = mu_shrunk / sigma2
    exposure = min(1.0, max(0.0, c * kelly))
    return {**base, "status": STATUS_COMPUTED, "reason_code": None, "mu": mu, "sigma2": sigma2,
            "t_stat": t_stat, "shrink": shrink, "mu_shrunk": mu_shrunk, "kelly_fraction": kelly,
            "exposure": exposure, "capped_at_one": c * kelly > 1.0}


def _scaled_path(fractions: Sequence[float], strategy: Sequence[float], cash: Sequence[float],
                 internal_turnover: Sequence[float] | None) -> tuple[list[float], list[float]]:
    """Gross path and per-session traded notional for a target-exposure schedule."""
    gross, turnover = [], []
    weight = 0.0  # start in cash
    for i, f in enumerate(fractions):
        traded = abs(f - weight)
        if internal_turnover is not None:
            traded += f * internal_turnover[i]
        r = f * strategy[i] + (1.0 - f) * cash[i]
        gross.append(r)
        turnover.append(traded)
        end_value = 1.0 + r
        weight = 0.0 if end_value <= 0.0 else f * (1.0 + strategy[i]) / end_value
    return gross, turnover


def _path_metrics(returns: Sequence[float]) -> dict:
    n = len(returns)
    if n == 0 or any(r <= -1.0 for r in returns):
        return {"status": STATUS_UNCOMPUTABLE, "reason_code": "EMPTY_OR_RUINED_PATH"}
    logs = [math.log1p(r) for r in returns]
    total_log = math.fsum(logs)
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        equity *= 1.0 + r
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1.0)
    return {"status": STATUS_COMPUTED, "reason_code": None, "observations": n,
            "log_growth_per_session": total_log / n,
            "total_return": math.expm1(total_log),
            "cagr": math.expm1(total_log * PERIODS_PER_YEAR / n),
            "max_drawdown": mdd}


def evaluate_fractional_kelly_walk_forward(
    *, strategy_returns: Sequence[tuple[date, float]],
    benchmark_returns: Sequence[tuple[date, float]],
    plan: WalkForwardPlan,
    cost_bps_per_side: float,
    synthetic: bool = True,
    real_data: RealDataAuthorization | None = None,
    cash_returns: Sequence[tuple[date, float]] | None = None,
    assume_zero_cash: bool = False,
    strategy_turnover: Sequence[float] | None = None,
    r8_returns: Sequence[tuple[date, float]] | None = None,
    r8_turnover: Sequence[float] | None = None,
    fractions: Sequence[float] = DEFAULT_FRACTIONS,
    multipliers: Sequence[float] = DEFAULT_MULTIPLIERS,
    report_context: Mapping[str, Any] | None = None,
    bootstrap_seed: int | None = None,
) -> dict:
    if synthetic is True:
        if real_data is not None:
            raise ValueError("REAL_DATA_AUTHORIZATION_WITH_SYNTHETIC")
    elif synthetic is not False or real_data is None:
        raise ValueError("REAL_DATA_REQUIRES_APPROVAL")
    if not isinstance(plan, WalkForwardPlan):
        raise TypeError("WALK_FORWARD_PLAN_INVALID")
    dates, strat = _series(strategy_returns, "STRATEGY_RETURNS")
    bench_dates, bench = _series(benchmark_returns, "BENCHMARK_RETURNS")
    if bench_dates != dates:
        raise ValueError("SESSIONS_NOT_ALIGNED")
    if cash_returns is not None:
        if assume_zero_cash:
            raise ValueError("CASH_ASSUMPTION_CONFLICT")
        cash_dates, cash = _series(cash_returns, "CASH_RETURNS")
        if cash_dates != dates:
            raise ValueError("SESSIONS_NOT_ALIGNED")
    elif assume_zero_cash:
        cash = (0.0,) * len(dates)
    else:
        raise ValueError("CASH_RETURNS_REQUIRED_OR_DECLARE_ZERO")
    internal = None
    if strategy_turnover is not None:
        internal = tuple(_finite(v, "STRATEGY_TURNOVER") for v in strategy_turnover)
        if len(internal) != len(dates) or any(v < 0 for v in internal):
            raise ValueError("STRATEGY_TURNOVER_INVALID")
    r8 = None
    if r8_returns is not None:
        r8_dates, r8 = _series(r8_returns, "R8_RETURNS")
        if r8_dates != dates:
            raise ValueError("SESSIONS_NOT_ALIGNED")
    r8_traded = None
    if r8_turnover is not None:
        if r8 is None:
            raise ValueError("R8_TURNOVER_WITHOUT_R8_RETURNS")
        r8_traded = tuple(_finite(v, "R8_TURNOVER") for v in r8_turnover)
        if len(r8_traded) != len(dates) or any(v < 0 for v in r8_traded):
            raise ValueError("R8_TURNOVER_INVALID")
    cs = tuple(_finite(c, "FRACTION") for c in fractions)
    if not cs or any(not 0.0 < c < 1.0 for c in cs) or len(set(cs)) != len(cs):
        raise ValueError("FRACTIONS_INVALID_FULL_KELLY_NOT_ALLOWED")
    base_bps = _finite(cost_bps_per_side, "COST_BPS")
    if base_bps < 0:
        raise ValueError("COST_BPS_INVALID")
    prereg = None
    if synthetic is False:
        prereg = authorize_real_data(real_data, plan=plan, fractions=cs, cost_bps_per_side=base_bps,
                                     multipliers=multipliers, dates=dates, report_context=report_context)

    envelope = {
        "research_only": True, "synthetic": prereg is None, "promotion_eligible": False,
        "data_identity": "development",
        "preregistration_id": None if prereg is None else prereg.preregistration_id,
        "strategy_key": None if prereg is None else prereg.strategy_key,
        "strategy_variant": None if prereg is None else real_data.variant,
        "gates": () if prereg is None else prereg.gates,
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "evaluator_version": EVALUATOR_VERSION,
        "kelly_ready_role": "CAP_NOTE_ONLY_NEVER_RAISES_BUDGET",
        "budget_effect": "NONE", "full_kelly_allowed": False, "leverage_allowed": False,
    }
    stats = _stats_module()
    if stats is None:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": UNAVAILABLE}
    folds = plan.folds(len(dates))
    if not folds:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": "NO_COMPLETE_FOLD"}

    test_start, test_end = folds[0][2], folds[-1][3]
    window = slice(test_start, test_end)
    s_test, c_test, b_test = strat[window], cash[window], bench[window]
    int_test = None if internal is None else internal[window]

    fold_records, schedules = [], {c: [] for c in cs}
    for train_start, train_end, f_start, f_end in folds:
        fits = {c: fit_fractional_kelly(strat[train_start:train_end], cash[train_start:train_end], c) for c in cs}
        for c in cs:
            schedules[c].extend([fits[c]["exposure"]] * (f_end - f_start))
        fold_records.append({
            "train_start": dates[train_start].isoformat(), "train_end": dates[train_end - 1].isoformat(),
            "test_start": dates[f_start].isoformat(), "test_end": dates[f_end - 1].isoformat(),
            "fits": {format(c, "g"): fits[c] for c in cs},
        })

    n_test = test_end - test_start
    paths: dict[str, tuple[list[float], list[float] | None]] = {}
    for c in cs:
        paths[f"kelly_c{format(c, 'g')}"] = _scaled_path(schedules[c], s_test, c_test, int_test)
    paths["full_exposure"] = _scaled_path([1.0] * n_test, s_test, c_test, int_test)
    paths["fixed_50pct"] = _scaled_path([0.5] * n_test, s_test, c_test, int_test)
    bench_turnover = [1.0] + [0.0] * (n_test - 1)  # buy-and-hold entry from cash
    paths["benchmark"] = (list(b_test), bench_turnover)
    if r8 is not None:
        paths["r8"] = (list(r8[window]), None if r8_traded is None else list(r8_traded[window]))

    comparators: dict[str, dict] = {}
    for name, (gross, traded) in paths.items():
        tiers: dict[str, dict] = {}
        if traded is None:  # R8 supplied net of its own costs; no turnover to stress
            for m in multipliers:
                key = format(float(m), "g")
                tiers[key] = (_path_metrics(gross) | {"cost_basis": "AS_SUPPLIED_NET"} if float(m) == 1.0 else
                              {"status": STATUS_UNCOMPUTABLE, "reason_code": "R8_TURNOVER_NOT_SUPPLIED"})
        else:
            stressed = stats.cost_stress_recompute(gross, traded, cost_bps_per_side=base_bps, multipliers=multipliers)
            if stressed.status != STATUS_COMPUTED:
                return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": "COST_STRESS_" + str(stressed.reason_code)}
            for scenario in stressed.scenarios:
                metrics = (_path_metrics(scenario.net_returns) if not scenario.ruined else
                           {"status": STATUS_UNCOMPUTABLE, "reason_code": "RUINED_BY_COSTS"})
                metrics["turnover_total"] = math.fsum(traded)
                tiers[format(scenario.multiplier, "g")] = metrics
        psr = stats.probabilistic_sharpe_ratio([g - k for g, k in zip(gross, c_test)])
        comparators[name] = {"tiers": tiers, "psr_gross_excess": {
            "status": psr.status, "value": psr.value if psr.status == STATUS_COMPUTED else None,
            "reason_code": psr.reason_code}}
        if bootstrap_seed is not None and hasattr(stats, "stationary_bootstrap_ci") and name.startswith("kelly_"):
            try:
                ci = stats.stationary_bootstrap_ci(gross, seed=bootstrap_seed, mean_block_length=5.0,
                                                   n_resamples=500, statistics=("log_growth", "max_drawdown"))
                comparators[name]["bootstrap_gross"] = ci.to_dict()
            except ImportError:
                comparators[name]["bootstrap_gross"] = {"status": STATUS_UNCOMPUTABLE, "reason_code": "NUMPY_UNAVAILABLE"}

    cap_note = {}
    for c in cs:
        computed = [f["fits"][format(c, "g")] for f in fold_records
                    if f["fits"][format(c, "g")]["status"] == STATUS_COMPUTED]
        exposures = [fit["exposure"] for fit in computed]
        cap_note[format(c, "g")] = {
            "folds_total": len(fold_records), "folds_computed": len(computed),
            "folds_parked": len(fold_records) - len(computed),
            "min_exposure": min(exposures) if exposures else None,
            "max_exposure": max(exposures) if exposures else None,
            "latest_fold_exposure": fold_records[-1]["fits"][format(c, "g")]["exposure"],
            "unit": "CAPITAL_EXPOSURE_FRACTION_0_TO_1_REST_CASH",
            "usage": "MAY_ONLY_LOWER_OR_CAP_NEVER_RAISE_EXISTING_BUDGET",
        }

    trial_log = json.dumps({"folds": fold_records, "fractions": list(cs)}, sort_keys=True, allow_nan=False)
    trial_log_sha = hashlib.sha256(trial_log.encode()).hexdigest()
    result = {
        **envelope, "status": STATUS_COMPUTED, "reason_code": None,
        "test_window": {"start": dates[test_start].isoformat(), "end": dates[test_end - 1].isoformat(),
                        "sessions": n_test},
        "plan": {"train_sessions": plan.train_sessions, "test_sessions": plan.test_sessions,
                 "purge_sessions": plan.purge_sessions, "anchored": plan.anchored},
        "folds": fold_records, "comparators": comparators, "kelly_cap_note": cap_note,
        "trial_log_sha256": trial_log_sha,
        "evaluation_contract": {
            "fit": "TRAIN_ONLY_EXCESS_MEAN_SAMPLE_VARIANCE_DDOF1",
            "shrinkage": "POSITIVE_PART_MAX_0_1_MINUS_1_OVER_T_SQUARED",
            "exposure": "CLIP_C_TIMES_MU_SHRUNK_OVER_SIGMA2_TO_0_1",
            "min_train_observations": MIN_TRAIN_OBSERVATIONS,
            "parked_fold_exposure": 0.0,
            "rebalance": "TO_TARGET_EXPOSURE_AT_EVERY_SESSION_START_FROM_CASH",
            "cost": "QPK_COST_STRESS_SINGLE_SIDE_ON_TRADED_NOTIONAL_CASH_LEG_FREE",
            "strategy_internal_costs": ("STRESSED_VIA_SUPPLIED_TURNOVER_SCALED_BY_EXPOSURE"
                                        if internal is not None else "AS_SUPPLIED_NOT_STRESSED"),
            "cash": "ASSUMED_ZERO_DECLARED" if cash_returns is None else "SUPPLIED_SERIES",
            "benchmark": "BUY_AND_HOLD_ENTRY_FROM_CASH_CHARGED",
            "r8": ("NOT_SUPPLIED" if r8 is None else
                   "SUPPLIED_PATH_NOT_RECOMPUTED" + ("_TURNOVER_STRESSED" if r8_traded is not None else "_AS_NET")),
            "base_cost_bps_per_side": base_bps, "multipliers": [float(m) for m in multipliers],
            "same_window": True, "periods_per_year": PERIODS_PER_YEAR,
        },
    }
    if report_context is not None:
        result["backtest_report"] = _build_report(stats, result, report_context, dates, folds, base_bps,
                                                  multipliers, trial_log_sha, cs)
    return result


def _build_report(stats: Any, result: dict, ctx: Mapping[str, Any], dates: Sequence[date],
                  folds: Sequence[tuple[int, int, int, int]], base_bps: float,
                  multipliers: Sequence[float], trial_log_sha: str, cs: Sequence[float]) -> dict:
    metrics = []
    for name, comp in result["comparators"].items():
        for tier, values in comp["tiers"].items():
            for key in ("log_growth_per_session", "cagr", "max_drawdown"):
                if values.get("status") == STATUS_COMPUTED:
                    metrics.append(stats.MetricEntry(f"{name}.{key}", float(tier), STATUS_COMPUTED, values[key]))
                else:
                    metrics.append(stats.MetricEntry(f"{name}.{key}", float(tier), STATUS_UNCOMPUTABLE, None,
                                                     values.get("reason_code") or "UNCOMPUTABLE"))
    report = stats.BacktestReportV1(
        report_id=ctx["report_id"], candidate_id=ctx["candidate_id"],
        source_revision=ctx["source_revision"], created_at=ctx["created_at"],
        data=stats.DataSpec(ctx["manifest_sha256"], ctx["source"], ctx["license"], "XNYS",
                            dates[folds[0][0]].isoformat(), dates[folds[-1][3] - 1].isoformat(),
                            folds[-1][3] - folds[0][0], "development"),
        timing=stats.TimingSpec(ctx["signal_cutoff"], ctx["execution"], "rebalance_to_target_every_session"),
        cost_model=stats.CostModelSpec(ctx.get("cost_model_id", "single_side_bps"), base_bps, 0.0, 0.0,
                                       tuple(float(m) for m in multipliers),
                                       ctx.get("settlement_rule", "not_modeled_fractional_exposure"), False),
        benchmark=stats.BenchmarkSpec(ctx["benchmark_name"], ctx["benchmark_rationale"]),
        trials=stats.TrialSummary(len(cs), 0, 0, "report-only: no selection among fractions", trial_log_sha),
        walk_forward=stats.WalkForwardSpec(
            "session_index_walk_forward_purged",
            tuple(stats.WalkForwardFold(dates[a].isoformat(), dates[b - 1].isoformat(),
                                        dates[c].isoformat(), dates[d - 1].isoformat()) for a, b, c, d in folds),
            None),
        status="DEVELOPMENT_ONLY", metrics=tuple(metrics),
        limitations=tuple(ctx.get("limitations", ())) + (
            "fractional Kelly output is a cap note only; it never raises any budget",
            "R8 path, if present, is caller-supplied and not recomputed",
        ),
    )
    return report.to_dict()
