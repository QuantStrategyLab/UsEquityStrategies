"""R5 same-window walk-forward comparator (research-only).

Compares, on exactly the same folds, stitched test sessions, fitted parameters
and cost tiers:

* ``kelly_c*`` -- the RS-04 fixed fractional-Kelly exposure, rebalanced to the
  fold target at every session start;
* ``kelly_cost_aware_c*`` -- the R5 no-trade band built from the *same* fold
  fit, allowed to hold inside the band.

Only the sizing rule differs.  Both variants consume the fit produced by
``fractional_kelly_walk_forward.fit_fractional_kelly`` for that fold (the same
``mu_shrunk`` and ``sigma2``); the fixed path is built with the RS-04
``_scaled_path`` helper and the cost-aware path mirrors its weight drift.

Decisions use the base one-side cost.  The stress multipliers are applied
afterwards by QPK ``research_stats.cost_stress_recompute`` and never feed back
into a target, so all tiers describe the same decision schedule.  The R5
``estimated_cost_fraction`` field is a first-order decision proxy; the
accounted cost comes from QPK, which charges
``(1 - turnover * rate) * (1 + gross) - 1``.

Why a separate module
---------------------
RS-04's public output, its DSR trial set and its report are left untouched.
This module reports paired net-1x differences only; it does **not** recompute
DSR, because adding variants to the RS-04 trial set would change an existing
multiplicity correction.

Boundaries
----------
- Synthetic data only.  Real data would first need the pre-registration
  extended with ``holding_sessions`` and ``min_trade_weight``; passing
  ``synthetic=False`` raises ``REAL_DATA_REQUIRES_PREREGISTRATION_EXTENSION``.
- ``report_context`` is refused: this comparator does not emit a
  ``qsl.backtest_report.v1`` yet.
- No leverage, no shorting, no authority: ``live_ready=False``,
  ``no_order=True``, ``budget_effect="NONE"``.  The output can only be used as
  a comparison note; it never raises a budget.
- A parked fold is cash (target 0) in **both** variants.  The cost band is not
  allowed to carry a position through a fold with no valid fit.
- ``holding_sessions`` is a modelling assumption re-evaluated every session,
  not a commitment to hold for that many sessions.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from us_equity_strategies.research.cost_aware_fractional_kelly import (
    STATUS_PARKED as TARGET_PARKED,
    CostAwareKellyPolicy,
    from_fractional_kelly_fit,
)
from us_equity_strategies.research.fractional_kelly_walk_forward import (
    DEFAULT_FRACTIONS,
    DEFAULT_MULTIPLIERS,
    EVALUATOR_VERSION as RS04_EVALUATOR_VERSION,
    MIN_TRAIN_OBSERVATIONS,
    PERIODS_PER_YEAR,
    STATUS_COMPUTED,
    STATUS_UNCOMPUTABLE,
    UNAVAILABLE,
    _path_metrics,
    _scaled_path,
    _series,
    fit_fractional_kelly,
)
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import (
    WalkForwardPlan,
)
from us_equity_strategies.research.kelly_disclosures import (
    coverage_disclosure,
    paired_stationary_bootstrap,
)

COMPARATOR_VERSION = "r5_cost_aware_walk_forward_comparator_v1"


def _stats_module() -> Any | None:
    """QPK ``research_stats`` is the single cost-accounting source.

    Kept as a local wrapper so tests can patch comparator availability.
    """
    from us_equity_strategies.research import fractional_kelly_walk_forward as rs04

    return rs04._stats_module()


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label}_INVALID")
    return float(value)


def _fixed_rebalance(schedule: Sequence[float], strategy: Sequence[float],
                     cash: Sequence[float]) -> list[float]:
    """Per-session trade to the schedule, mirroring RS-04 weight drift."""
    weight, out = 0.0, []
    for f, s, k in zip(schedule, strategy, cash):
        out.append(abs(f - weight))
        r = f * s + (1.0 - f) * k
        end_value = 1.0 + r
        weight = 0.0 if end_value <= 0.0 else f * (1.0 + s) / end_value
    return out


def _summary(*, turnover: Sequence[float], rebalance: Sequence[float],
             targets: Sequence[float], statuses: Sequence[str] | None = None) -> dict:
    n = len(turnover)
    counts: dict[str, int] = {}
    if statuses is not None:
        for status in statuses:
            counts[status] = counts.get(status, 0) + 1
    return {
        "sessions": n,
        "rebalance_sessions": sum(1 for x in rebalance if x > 0.0),
        "rebalance_turnover_total": math.fsum(rebalance),
        "turnover_total": math.fsum(turnover),
        "mean_target_weight": math.fsum(targets) / n,
        "min_target_weight": min(targets),
        "max_target_weight": max(targets),
        "decision_status_counts": counts if statuses is not None else None,
    }


def _simulate_cost_aware(*, folds: Sequence[tuple[int, int, int, int]],
                         fits_by_fold: Sequence[Mapping[str, dict]],
                         policy: CostAwareKellyPolicy,
                         strategy: Sequence[float], cash: Sequence[float],
                         internal: Sequence[float] | None) -> dict:
    gross: list[float] = []
    turnover: list[float] = []
    rebalance: list[float] = []
    targets: list[float] = []
    statuses: list[str] = []
    weight = 0.0
    for (_, _, f_start, f_end), fits in zip(folds, fits_by_fold):
        fit = fits[policy.c]
        for i in range(f_start, f_end):
            decision = from_fractional_kelly_fit(fit, current_weight=weight, policy=policy)
            target = 0.0 if decision.status == TARGET_PARKED else decision.target_weight
            if target is None or not math.isfinite(target) or not 0.0 <= target <= policy.position_cap:
                raise ValueError("COST_AWARE_TARGET_OUT_OF_RANGE")
            traded = abs(target - weight)
            total = traded if internal is None else traded + target * internal[i]
            r = target * strategy[i] + (1.0 - target) * cash[i]
            gross.append(r)
            rebalance.append(traded)
            turnover.append(total)
            targets.append(target)
            statuses.append(decision.status)
            end_value = 1.0 + r
            weight = 0.0 if end_value <= 0.0 else target * (1.0 + strategy[i]) / end_value
            if not -1e-12 <= weight <= 1.0 + 1e-12:
                raise ValueError("WEIGHT_OUT_OF_RANGE")
            weight = min(1.0, max(0.0, weight))
    return {"gross": gross, "turnover": turnover, "rebalance": rebalance,
            "targets": targets, "statuses": statuses}


def _build_paths(*, dates: Sequence[date], strategy: Sequence[float], cash: Sequence[float],
                 internal: Sequence[float] | None,
                 folds: Sequence[tuple[int, int, int, int]],
                 fractions: Sequence[float], position_cap: float,
                 cost_bps_per_side: float, holding_sessions: int,
                 min_trade_weight: float) -> dict:
    """Build both variants' gross paths and turnover without cost accounting."""
    test_start, test_end = folds[0][2], folds[-1][3]
    window = slice(test_start, test_end)
    s_test, c_test = strategy[window], cash[window]
    int_test = None if internal is None else internal[window]
    n_test = test_end - test_start

    fold_records, fits_by_fold, schedules = [], [], {c: [] for c in fractions}
    for train_start, train_end, f_start, f_end in folds:
        fits = {c: fit_fractional_kelly(strategy[train_start:train_end],
                                        cash[train_start:train_end], c) for c in fractions}
        fits_by_fold.append(fits)
        for c in fractions:
            schedules[c].extend([min(position_cap, fits[c]["exposure"])] * (f_end - f_start))
        fold_records.append({
            "train_start": dates[train_start].isoformat(),
            "train_end": dates[train_end - 1].isoformat(),
            "test_start": dates[f_start].isoformat(),
            "test_end": dates[f_end - 1].isoformat(),
            "fits": {format(c, "g"): fits[c] for c in fractions},
        })

    paths: dict[str, tuple[list[float], list[float]]] = {}
    summaries: dict[str, dict] = {}
    for c in fractions:
        name = f"kelly_c{format(c, 'g')}"
        schedule = schedules[c]
        gross, turnover = _scaled_path(schedule, s_test, c_test, int_test)
        rebalance = _fixed_rebalance(schedule, s_test, c_test)
        if len(gross) != n_test:
            raise ValueError("PATH_LENGTH_MISMATCH")
        paths[name] = (gross, turnover)
        summaries[name] = _summary(turnover=turnover, rebalance=rebalance, targets=schedule)

        policy = CostAwareKellyPolicy(
            c=c, position_cap=position_cap, cost_bps_per_side=cost_bps_per_side,
            holding_sessions=holding_sessions, min_trade_weight=min_trade_weight)
        sim = _simulate_cost_aware(folds=folds, fits_by_fold=fits_by_fold, policy=policy,
                                   strategy=strategy, cash=cash, internal=internal)
        if len(sim["gross"]) != n_test:
            raise ValueError("PATH_LENGTH_MISMATCH")
        aware_name = f"kelly_cost_aware_c{format(c, 'g')}"
        paths[aware_name] = (sim["gross"], sim["turnover"])
        summaries[aware_name] = _summary(turnover=sim["turnover"], rebalance=sim["rebalance"],
                                         targets=sim["targets"], statuses=sim["statuses"])

    return {"test_start": test_start, "test_end": test_end, "fold_records": fold_records,
            "paths": paths, "summaries": summaries}


def _paired_delta(cost_aware: Mapping[str, Any], fixed: Mapping[str, Any]) -> dict:
    if (cost_aware.get("status") != STATUS_COMPUTED
            or fixed.get("status") != STATUS_COMPUTED):
        return {"status": STATUS_UNCOMPUTABLE, "reason_code": "NET_1X_TIER_UNCOMPUTABLE",
                "basis": "NET_1X_ACCOUNTING", "same_sessions": True}
    out = {"status": STATUS_COMPUTED, "reason_code": None,
           "basis": "NET_1X_ACCOUNTING", "same_sessions": True}
    for key in ("log_growth_per_session", "cagr", "max_drawdown", "cvar_95_daily",
                "turnover_total", "total_cost_log_drag"):
        left, right = cost_aware.get(key), fixed.get(key)
        if left is None or right is None:
            out[key] = None
        else:
            out[key] = left - right
    return out


def evaluate_cost_aware_walk_forward(
    *,
    strategy_returns: Sequence[tuple[date, float]],
    plan: WalkForwardPlan,
    cost_bps_per_side: float,
    fractions: Sequence[float] = DEFAULT_FRACTIONS,
    multipliers: Sequence[float] = DEFAULT_MULTIPLIERS,
    holding_sessions: int = 1,
    position_cap: float = 1.0,
    min_trade_weight: float = 0.0,
    cash_returns: Sequence[tuple[date, float]] | None = None,
    assume_zero_cash: bool = False,
    strategy_turnover: Sequence[float] | None = None,
    synthetic: bool = True,
    bootstrap_seed: int | None = None,
    report_context: Mapping[str, Any] | None = None,
) -> dict:
    """Compare fixed fractional Kelly with the R5 cost-aware band.

    All inputs describe the full session series; ``plan.folds`` selects the
    training/test slices.  The returned ``paths`` are not included, only the
    cost-stressed metrics and paired net-1x deltas.
    """
    if synthetic is not True:
        raise ValueError("REAL_DATA_REQUIRES_PREREGISTRATION_EXTENSION")
    if report_context is not None:
        raise ValueError("REPORT_CONTEXT_NOT_SUPPORTED_IN_COMPARATOR")
    if not isinstance(plan, WalkForwardPlan):
        raise TypeError("WALK_FORWARD_PLAN_INVALID")
    dates, strat = _series(strategy_returns, "STRATEGY_RETURNS")
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
        if len(internal) != len(dates) or any(v < 0.0 for v in internal):
            raise ValueError("STRATEGY_TURNOVER_INVALID")
    cs = tuple(_finite(c, "FRACTION") for c in fractions)
    if not cs or any(not 0.0 < c < 1.0 for c in cs) or len(set(cs)) != len(cs):
        raise ValueError("FRACTIONS_INVALID_FULL_KELLY_NOT_ALLOWED")
    ms = tuple(_finite(m, "MULTIPLIER") for m in multipliers)
    if not ms or any(m <= 0.0 for m in ms) or len(set(ms)) != len(ms):
        raise ValueError("MULTIPLIERS_INVALID")
    base_bps = _finite(cost_bps_per_side, "COST_BPS")
    if base_bps < 0.0:
        raise ValueError("COST_BPS_INVALID")
    cap = _finite(position_cap, "POSITION_CAP")
    if not 0.0 <= cap <= 1.0:
        raise ValueError("POSITION_CAP_OUT_OF_RANGE")
    min_trade = _finite(min_trade_weight, "MIN_TRADE_WEIGHT")
    if not 0.0 <= min_trade <= 1.0:
        raise ValueError("MIN_TRADE_WEIGHT_OUT_OF_RANGE")
    if isinstance(holding_sessions, bool) or not isinstance(holding_sessions, int) or holding_sessions < 1:
        raise ValueError("HOLDING_SESSIONS_INVALID")
    if bootstrap_seed is not None and (isinstance(bootstrap_seed, bool) or not isinstance(bootstrap_seed, int)):
        raise ValueError("BOOTSTRAP_SEED_INVALID")

    envelope = {
        "research_only": True, "synthetic": True, "promotion_eligible": False,
        "data_identity": "development", "preregistration_id": None,
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "budget_effect": "NONE",
        "kelly_ready_role": "COMPARATOR_ONLY_NEVER_RAISES_BUDGET",
        "comparator_version": COMPARATOR_VERSION,
        "rs04_evaluator_version": RS04_EVALUATOR_VERSION,
        "full_kelly_allowed": False, "leverage_allowed": False,
    }
    stats = _stats_module()
    if stats is None:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": UNAVAILABLE}
    folds = plan.folds(len(dates))
    if not folds:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": "NO_COMPLETE_FOLD"}

    built = _build_paths(dates=dates, strategy=strat, cash=cash, internal=internal,
                         folds=folds, fractions=cs, position_cap=cap,
                         cost_bps_per_side=base_bps, holding_sessions=holding_sessions,
                         min_trade_weight=min_trade)
    test_start, test_end = built["test_start"], built["test_end"]

    comparators: dict[str, dict] = {}
    net1x: dict[str, list[float]] = {}
    for name, (gross, turnover) in built["paths"].items():
        stressed = stats.cost_stress_recompute(gross, turnover, cost_bps_per_side=base_bps,
                                               multipliers=ms)
        if stressed.status != STATUS_COMPUTED:
            return {**envelope, "status": STATUS_UNCOMPUTABLE,
                    "reason_code": "COST_STRESS_" + str(stressed.reason_code)}
        tiers: dict[str, dict] = {}
        for scenario in stressed.scenarios:
            metrics = (_path_metrics(scenario.net_returns) if not scenario.ruined else
                       {"status": STATUS_UNCOMPUTABLE, "reason_code": "RUINED_BY_COSTS"})
            metrics["turnover_total"] = math.fsum(turnover)
            metrics["total_cost_log_drag"] = scenario.total_cost_log_drag
            tiers[format(float(scenario.multiplier), "g")] = metrics
            if float(scenario.multiplier) == 1.0 and not scenario.ruined:
                net1x[name] = list(scenario.net_returns)
        comparators[name] = {"tiers": tiers, "path_summary": built["summaries"][name]}

    paired: dict[str, dict] = {}
    for c in cs:
        aware = f"kelly_cost_aware_c{format(c, 'g')}"
        fixed = f"kelly_c{format(c, 'g')}"
        delta = _paired_delta(comparators[aware]["tiers"].get("1", {}),
                              comparators[fixed]["tiers"].get("1", {}))
        if bootstrap_seed is not None and aware in net1x and fixed in net1x:
            delta["paired_stationary_bootstrap"] = paired_stationary_bootstrap(
                net1x[aware], net1x[fixed], seed=bootstrap_seed)
        paired[f"{aware}__vs__{fixed}"] = delta

    return {
        **envelope, "status": STATUS_COMPUTED, "reason_code": None,
        "test_window": {"start": dates[test_start].isoformat(),
                        "end": dates[test_end - 1].isoformat(),
                        "sessions": test_end - test_start},
        "plan": {"train_sessions": plan.train_sessions, "test_sessions": plan.test_sessions,
                 "purge_sessions": plan.purge_sessions, "anchored": plan.anchored},
        "folds": built["fold_records"],
        "comparators": comparators,
        "paired_deltas_net1x": paired,
        "coverage": coverage_disclosure(dates, folds),
        "evaluation_contract": {
            "same_folds": True,
            "same_fitted_parameters_per_fold": True,
            "fit": "RS04_TRAIN_ONLY_EXCESS_MEAN_SAMPLE_VARIANCE_DDOF1",
            "fixed_exposure": "CLIP_C_TIMES_MU_SHRUNK_OVER_SIGMA2_TO_0_POSITION_CAP",
            "cost_aware_policy": "R5_BAND_ON_THE_SAME_FOLD_FIT",
            "decision_cost_bps_per_side": base_bps,
            "cost_tiers": "ACCOUNTING_STRESS_ONLY_DECISIONS_NOT_REOPTIMISED",
            "rebalance": "TO_DECISION_AT_EVERY_SESSION_START",
            "strategy_internal_costs": ("STRESSED_VIA_SUPPLIED_TURNOVER_SCALED_BY_TARGET"
                                        if internal is not None else "AS_SUPPLIED_NOT_STRESSED"),
            "parked_fold": "CASH_ZERO_EXPOSURE_BOTH_VARIANTS",
            "cash": "ASSUMED_ZERO_DECLARED" if cash_returns is None else "SUPPLIED_SERIES",
            "min_train_observations": MIN_TRAIN_OBSERVATIONS,
            "periods_per_year": PERIODS_PER_YEAR,
            "holding_sessions": holding_sessions,
            "min_trade_weight": min_trade,
            "position_cap": cap,
            "dsr": "NOT_COMPUTED_SEPARATE_TRIAL_SET_FROM_RS04",
            "bootstrap": ("PAIRED_STATIONARY_ON_NET_1X" if bootstrap_seed is not None
                          else "NOT_REQUESTED"),
            "delta_sign": ("POSITIVE_LOG_GROWTH_AND_CVAR_FAVOUR_COST_AWARE; "
                           "POSITIVE_MAX_DRAWDOWN_MEANS_SHALLOWER; "
                           "NEGATIVE_TURNOVER_OR_COST_DRAG_MEANS_LOWER_CHURN"),
        },
    }
