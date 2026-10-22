"""Explicit opt-in v2 research evaluator. Legacy/M2 entrypoints stay unchanged.

Only orchestration is versioned here; fit, path, cost and report helpers are
reused from v1. Real-data preregistration and actual-net trials do not transfer.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from us_equity_strategies.research.kelly_disclosures import (
    COST_MODEL_DISCLOSURE,
    METRIC_BASIS,
    paired_stationary_bootstrap,
)
from us_equity_strategies.research.fractional_kelly_preregistration import (
    RealDataAuthorization,
)
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import (
    WalkForwardPlan,
)
from us_equity_strategies.research.fractional_kelly_walk_forward import (
    DEFAULT_FRACTIONS,
    DEFAULT_MULTIPLIERS,
    STATUS_COMPUTED,
    STATUS_UNCOMPUTABLE,
    UNAVAILABLE,
    MIN_TRAIN_OBSERVATIONS,
    PERIODS_PER_YEAR,
    _stats_module,
    _series,
    _finite,
    _scaled_path,
    _path_metrics,
    _build_report,
    fit_fractional_kelly,
)
from us_equity_strategies.research.walk_forward_v2_support import coverage_disclosure, versioned_report_context

EVALUATOR_VERSION = "fractional_kelly_walk_forward_v2"


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
    if synthetic is not True or real_data is not None:
        raise ValueError("NEED_USER_V2_REAL_DATA_PROTOCOL_NOT_REGISTERED")
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
    envelope = {
        "research_only": True, "synthetic": True, "promotion_eligible": False,
        "data_identity": "development",
        "preregistration_id": None,
        "strategy_key": None,
        "strategy_variant": None,
        "gates": (),
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "evaluator_version": EVALUATOR_VERSION,
        "cv_contract": {"label_horizon_sessions": plan.label_horizon_sessions,
                        "embargo_sessions": plan.embargo_sessions},
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
    # Stitch only admitted test sessions; a continuous slice includes embargo.
    test_indices = tuple(i for _, _, start, end in folds for i in range(start, end))
    s_test = tuple(strat[i] for i in test_indices)
    c_test = tuple(cash[i] for i in test_indices)
    b_test = tuple(bench[i] for i in test_indices)
    int_test = None if internal is None else tuple(internal[i] for i in test_indices)

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

    n_test = len(test_indices)
    paths: dict[str, tuple[list[float], list[float] | None]] = {}
    for c in cs:
        paths[f"kelly_c{format(c, 'g')}"] = _scaled_path(schedules[c], s_test, c_test, int_test)
    paths["full_exposure"] = _scaled_path([1.0] * n_test, s_test, c_test, int_test)
    paths["fixed_50pct"] = _scaled_path([0.5] * n_test, s_test, c_test, int_test)
    bench_turnover = [1.0] + [0.0] * (n_test - 1)  # buy-and-hold entry from cash
    paths["benchmark"] = (list(b_test), bench_turnover)
    if r8 is not None:
        paths["r8"] = ([r8[i] for i in test_indices],
                       None if r8_traded is None else [r8_traded[i] for i in test_indices])

    comparators: dict[str, dict] = {}
    net1x: dict[str, list[float]] = {}
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
                if float(scenario.multiplier) == 1.0 and not scenario.ruined:
                    net1x[name] = list(scenario.net_returns)
        psr = stats.probabilistic_sharpe_ratio([g - k for g, k in zip(gross, c_test)])
        comparators[name] = {"tiers": tiers, "psr_gross_excess": {
            "status": psr.status, "value": psr.value if psr.status == STATUS_COMPUTED else None,
            "reason_code": psr.reason_code}}
        if name in net1x:
            psr_net = stats.probabilistic_sharpe_ratio([g - k for g, k in zip(net1x[name], c_test)])
            comparators[name]["psr_net1x_excess"] = {
                "status": psr_net.status, "value": psr_net.value if psr_net.status == STATUS_COMPUTED else None,
                "reason_code": psr_net.reason_code}
        if bootstrap_seed is not None and hasattr(stats, "stationary_bootstrap_ci") and name.startswith("kelly_"):
            for key, series in (("bootstrap_gross", gross), ("bootstrap_net1x", net1x.get(name))):
                if series is None:
                    continue
                try:
                    ci = stats.stationary_bootstrap_ci(series, seed=bootstrap_seed, mean_block_length=5.0,
                                                       n_resamples=500, statistics=("log_growth", "max_drawdown"))
                    comparators[name][key] = ci.to_dict()
                except ImportError:
                    comparators[name][key] = {"status": STATUS_UNCOMPUTABLE, "reason_code": "NUMPY_UNAVAILABLE"}

    sizing = [name for name in comparators if name.startswith("kelly_") or name in ("full_exposure", "fixed_50pct")]
    excess_1x: dict[str, list[float]] = {}
    for name in sizing:
        gross, traded = paths[name]
        stressed = stats.cost_stress_recompute(gross, traded, cost_bps_per_side=base_bps, multipliers=(1.0,))
        if stressed.status == STATUS_COMPUTED and not stressed.scenarios[0].ruined:
            excess_1x[name] = [r - k for r, k in zip(stressed.scenarios[0].net_returns, c_test)]
    trial_sharpes = []
    for series in excess_1x.values():
        m = math.fsum(series) / len(series)
        sd = math.sqrt(math.fsum((x - m) ** 2 for x in series) / (len(series) - 1)) if len(series) > 1 else 0.0
        if sd > 0:
            trial_sharpes.append(m / sd)
    for name in sizing:
        if name.startswith("kelly_") and name in excess_1x and hasattr(stats, "deflated_sharpe_ratio"):
            dsr = stats.deflated_sharpe_ratio(excess_1x[name], trial_sharpes)
            comparators[name]["dsr_net1x_excess_vs_sizing_trials"] = {
                "status": dsr.status, "value": dsr.value if dsr.status == STATUS_COMPUTED else None,
                "reason_code": dsr.reason_code, "n_trials": len(trial_sharpes)}

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

    paired = {}
    if bootstrap_seed is not None:
        for name in comparators:
            if name.startswith("kelly_") and name in net1x and "fixed_50pct" in net1x:
                paired[f"{name}__vs__fixed_50pct"] = paired_stationary_bootstrap(
                    net1x[name], net1x["fixed_50pct"], seed=bootstrap_seed)

    trial_log = json.dumps({"evaluator_version": EVALUATOR_VERSION,
                            "label_horizon_sessions": plan.label_horizon_sessions,
                            "embargo_sessions": plan.embargo_sessions, "folds": fold_records, "fractions": list(cs)}, sort_keys=True, allow_nan=False)
    trial_log_sha = hashlib.sha256(trial_log.encode()).hexdigest()
    result = {
        **envelope, "status": STATUS_COMPUTED, "reason_code": None,
        "test_window": {"start": dates[test_start].isoformat(), "end": dates[test_end - 1].isoformat(),
                        "sessions": n_test},
        "plan": {"train_sessions": plan.train_sessions, "test_sessions": plan.test_sessions,
                 "purge_sessions": plan.purge_sessions, "anchored": plan.anchored,
                 "label_horizon_sessions": plan.label_horizon_sessions,
                 "embargo_sessions": plan.embargo_sessions},
        "folds": fold_records, "comparators": comparators, "kelly_cap_note": cap_note,
        "trial_log_sha256": trial_log_sha, "paired_bootstrap": paired,
        "coverage": coverage_disclosure(dates, folds), "metric_basis": METRIC_BASIS,
        "cost_model_disclosure": COST_MODEL_DISCLOSURE,
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
        result["backtest_report"] = _build_report(stats, result, versioned_report_context(report_context, EVALUATOR_VERSION, plan), dates, folds, base_bps,
                                                  multipliers, trial_log_sha, cs)
    return result
