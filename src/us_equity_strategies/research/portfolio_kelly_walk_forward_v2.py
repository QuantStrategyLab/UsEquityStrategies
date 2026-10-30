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
from us_equity_strategies.research import fractional_kelly_walk_forward as rs04
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan
from us_equity_strategies.research.kelly_disclosures import (
    COST_MODEL_DISCLOSURE,
    METRIC_BASIS,
    paired_stationary_bootstrap,
)
from us_equity_strategies.research.portfolio_kelly_preregistration import (
    METHODS,
    PortfolioRealDataAuthorization,
)
from us_equity_strategies.research.portfolio_kelly_walk_forward import (
    STATUS_COMPUTED,
    STATUS_UNCOMPUTABLE,
    MIN_TRAIN_OBSERVATIONS,
    PERIODS_PER_YEAR,
    fit_portfolio_kelly,
    portfolio_path,
    _corr,
    sample_covariance,
    _sharpe,
    _build_report,
)
from us_equity_strategies.research.walk_forward_v2_support import coverage_disclosure, versioned_report_context

EVALUATOR_VERSION = "portfolio_kelly_walk_forward_v2"


def evaluate_portfolio_kelly_walk_forward(
    *, asset_returns: Sequence[tuple[str, Sequence[tuple[date, float]]]],
    cash_returns: Sequence[tuple[date, float]],
    benchmark_returns: Sequence[tuple[str, Sequence[tuple[date, float]]]],
    plan: WalkForwardPlan,
    cost_bps_per_side: float,
    asset_turnover: Mapping[str, Sequence[float]] | None = None,
    fractions: Sequence[float] = rs04.DEFAULT_FRACTIONS,
    methods: Sequence[str] = METHODS,
    multipliers: Sequence[float] = rs04.DEFAULT_MULTIPLIERS,
    synthetic: bool = True,
    real_data: PortfolioRealDataAuthorization | None = None,
    report_context: Mapping[str, Any] | None = None,
    bootstrap_seed: int | None = None,
    netting_candidate: Mapping[str, Any] | None = None,
) -> dict:
    if netting_candidate is not None:
        raise ValueError("V2_NETTING_NOT_SUPPORTED")
    if synthetic is not True or real_data is not None:
        raise ValueError("NEED_USER_V2_REAL_DATA_PROTOCOL_NOT_REGISTERED")
    if not isinstance(plan, WalkForwardPlan):
        raise TypeError("WALK_FORWARD_PLAN_INVALID")
    assets = tuple(k for k, _ in asset_returns)
    if len(assets) < 2 or len(set(assets)) != len(assets):
        raise ValueError("ASSETS_INVALID")
    dates, cash = rs04._series(cash_returns, "CASH_RETURNS")
    rets: dict[str, tuple[float, ...]] = {}
    for key, rows in asset_returns:
        d, v = rs04._series(rows, f"ASSET_RETURNS_{key}")
        if d != dates:
            raise ValueError("SESSIONS_NOT_ALIGNED")
        rets[key] = v
    bench: dict[str, tuple[float, ...]] = {}
    for key, rows in benchmark_returns:
        d, v = rs04._series(rows, f"BENCHMARK_RETURNS_{key}")
        if d != dates:
            raise ValueError("SESSIONS_NOT_ALIGNED")
        bench[key] = v
    internal = None
    if asset_turnover is not None:
        internal = {}
        for key in assets:
            vals = tuple(rs04._finite(x, "ASSET_TURNOVER") for x in asset_turnover[key])
            if len(vals) != len(dates) or any(x < 0 for x in vals):
                raise ValueError("ASSET_TURNOVER_INVALID")
            internal[key] = vals
    cs = tuple(rs04._finite(c, "FRACTION") for c in fractions)
    if not cs or any(not 0.0 < c < 1.0 for c in cs) or len(set(cs)) != len(cs):
        raise ValueError("FRACTIONS_INVALID_FULL_KELLY_NOT_ALLOWED")
    ms = tuple(methods)
    if not ms or any(m not in METHODS for m in ms):
        raise ValueError("METHODS_INVALID")
    base_bps = rs04._finite(cost_bps_per_side, "COST_BPS")
    if base_bps < 0:
        raise ValueError("COST_BPS_INVALID")
    envelope = {
        "research_only": True, "synthetic": True, "promotion_eligible": False,
        "data_identity": "development",
        "preregistration_id": None,
        "strategy_variant": None,
        "gates": (), "assets": list(assets),
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "evaluator_version": EVALUATOR_VERSION,
        "cv_contract": {"label_horizon_sessions": plan.label_horizon_sessions,
                        "embargo_sessions": plan.embargo_sessions}, "kelly_ready_role": "CAP_NOTE_ONLY_NEVER_RAISES_BUDGET",
        "budget_effect": "NONE", "full_kelly_allowed": False, "leverage_allowed": False, "short_allowed": False,
    }
    stats = rs04._stats_module()
    if stats is None:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": rs04.UNAVAILABLE}
    folds = plan.folds(len(dates))
    if not folds:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": "NO_COMPLETE_FOLD"}

    lo, hi = folds[0][2], folds[-1][3]
    # Match every return/cost stream to the schedules, excluding embargo rows.
    test_indices = tuple(i for _, _, start, end in folds for i in range(start, end))
    n_test = len(test_indices)
    r_test = {a: tuple(rets[a][i] for i in test_indices) for a in assets}
    c_test = tuple(cash[i] for i in test_indices)
    int_test = None if internal is None else {
        a: tuple(internal[a][i] for i in test_indices) for a in assets}

    schedules: dict[str, list[dict[str, float]]] = {}
    fold_records = []
    for tr0, tr1, te0, te1 in folds:
        train = {a: rets[a][tr0:tr1] for a in assets}
        fit = fit_portfolio_kelly(assets, train, cash[tr0:tr1], cs, ms)
        length = te1 - te0
        singles = {}
        for c in cs:
            key_c = format(c, "g")
            for m in ms:
                schedules.setdefault(f"portfolio_kelly.{m}.c{key_c}", []).extend([fit["weights"][key_c][m]] * length)
            for a in assets:
                s = rs04.fit_fractional_kelly(train[a], cash[tr0:tr1], c)
                singles.setdefault(a, {})[key_c] = s["exposure"]
                schedules.setdefault(f"single_kelly.{a}.c{key_c}", []).extend([{a: s["exposure"]}] * length)
        fold_records.append({
            "train_start": dates[tr0].isoformat(), "train_end": dates[tr1 - 1].isoformat(),
            "test_start": dates[te0].isoformat(), "test_end": dates[te1 - 1].isoformat(),
            "portfolio_fit": fit, "single_kelly_exposure": singles,
        })
    a0, a1 = assets[0], assets[1]
    equal = 1.0 / len(assets)
    schedules[f"equal_weight_{round(100 * equal)}_{round(100 * equal)}"] = [dict.fromkeys(assets, equal)] * n_test
    schedules[f"equal_weight_{round(50 * equal)}_{round(50 * equal)}"] = [dict.fromkeys(assets, equal / 2)] * n_test
    for a in assets:
        schedules[f"fixed_50.{a}"] = [{a: 0.5}] * n_test
        schedules[f"full.{a}"] = [{a: 1.0}] * n_test
    sizing = list(schedules)
    paths = {name: portfolio_path(sched, r_test, c_test, int_test) for name, sched in schedules.items()}
    b_test = {k: tuple(v[i] for i in test_indices) for k, v in bench.items()}
    for k in bench:
        paths[f"benchmark.{k}"] = (list(b_test[k]), [1.0] + [0.0] * (n_test - 1))
    if len(bench) == 2:
        k0, k1 = list(bench)
        paths[f"benchmark.{k0}_{k1}_50_50"] = portfolio_path([{k0: 0.5, k1: 0.5}] * n_test, b_test, c_test, None)

    comparators: dict[str, dict] = {}
    excess_1x: dict[str, list[float]] = {}
    net1x: dict[str, list[float]] = {}
    for name, (gross, traded) in paths.items():
        stressed = stats.cost_stress_recompute(gross, traded, cost_bps_per_side=base_bps, multipliers=multipliers)
        if stressed.status != STATUS_COMPUTED:
            return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": "COST_STRESS_" + str(stressed.reason_code)}
        tiers = {}
        for sc in stressed.scenarios:
            metrics = (rs04._path_metrics(sc.net_returns) if not sc.ruined else
                       {"status": STATUS_UNCOMPUTABLE, "reason_code": "RUINED_BY_COSTS"})
            metrics["turnover_total"] = math.fsum(traded)
            tiers[format(sc.multiplier, "g")] = metrics
            if float(sc.multiplier) == 1.0 and not sc.ruined:
                net1x[name] = list(sc.net_returns)
                if name in sizing:
                    excess_1x[name] = [r - k for r, k in zip(sc.net_returns, c_test)]
        psr = stats.probabilistic_sharpe_ratio([g - k for g, k in zip(gross, c_test)])
        comparators[name] = {"tiers": tiers, "psr_gross_excess": {
            "status": psr.status, "value": psr.value if psr.status == STATUS_COMPUTED else None,
            "reason_code": psr.reason_code}}
        if name in net1x:
            psr_net = stats.probabilistic_sharpe_ratio([g - k for g, k in zip(net1x[name], c_test)])
            comparators[name]["psr_net1x_excess"] = {
                "status": psr_net.status, "value": psr_net.value if psr_net.status == STATUS_COMPUTED else None,
                "reason_code": psr_net.reason_code}
        if bootstrap_seed is not None and hasattr(stats, "stationary_bootstrap_ci") and name.startswith("portfolio_"):
            for key, series in (("bootstrap_gross", gross), ("bootstrap_net1x", net1x.get(name))):
                if series is None:
                    continue
                try:
                    ci = stats.stationary_bootstrap_ci(series, seed=bootstrap_seed, mean_block_length=5.0,
                                                       n_resamples=500, statistics=("log_growth", "max_drawdown"))
                    comparators[name][key] = ci.to_dict()
                except ImportError:
                    comparators[name][key] = {"status": STATUS_UNCOMPUTABLE, "reason_code": "NUMPY_UNAVAILABLE"}
    trial_sharpes = [s for s in (_sharpe(v) for v in excess_1x.values()) if s is not None]
    for name in sizing:
        if name in excess_1x and hasattr(stats, "deflated_sharpe_ratio"):
            dsr = stats.deflated_sharpe_ratio(excess_1x[name], trial_sharpes)
            comparators[name]["dsr_net1x_excess_vs_all_sizing_trials"] = {
                "status": dsr.status, "value": dsr.value if dsr.status == STATUS_COMPUTED else None,
                "reason_code": dsr.reason_code, "n_trials": len(trial_sharpes)}

    paired = {}
    if bootstrap_seed is not None:
        baselines = [b for b in (f"equal_weight_{round(100 * equal)}_{round(100 * equal)}",
                                 f"equal_weight_{round(50 * equal)}_{round(50 * equal)}",
                                 *(f"fixed_50.{a}" for a in assets)) if b in net1x]
        for name in sizing:
            if name.startswith(("portfolio_kelly.", "single_kelly.")) and name in net1x:
                for base in baselines:
                    paired[f"{name}__vs__{base}"] = paired_stationary_bootstrap(net1x[name], net1x[base],
                                                                                seed=bootstrap_seed)
    excess_test = [[r - k for r, k in zip(r_test[a], c_test)] for a in assets]
    realized = {"correlation_sample": _corr(sample_covariance(excess_test, ddof=1)),
                "sharpe_annualized_gross_excess": {
                    a: (None if _sharpe(excess_test[i]) is None else _sharpe(excess_test[i]) * math.sqrt(PERIODS_PER_YEAR))
                    for i, a in enumerate(assets)}}
    cap_note = {}
    primary = ms[0]
    for c in cs:
        key_c = format(c, "g")
        latest = fold_records[-1]["portfolio_fit"]["weights"][key_c][primary]
        cap_note[key_c] = {"method": primary, "latest_fold_weights": latest,
                           "latest_fold_invested": math.fsum(latest.values()),
                           "folds_parked": sum(1 for f in fold_records if f["portfolio_fit"]["status"] != STATUS_COMPUTED),
                           "folds_total": len(fold_records),
                           "usage": "MAY_ONLY_LOWER_OR_CAP_NEVER_RAISE_EXISTING_BUDGET"}
    trial_log = json.dumps({"evaluator_version": EVALUATOR_VERSION,
                            "label_horizon_sessions": plan.label_horizon_sessions,
                            "embargo_sessions": plan.embargo_sessions, "folds": fold_records, "fractions": list(cs), "methods": list(ms), "trials": sizing},
                           sort_keys=True, allow_nan=False)
    trial_sha = hashlib.sha256(trial_log.encode()).hexdigest()
    result = {
        **envelope, "status": STATUS_COMPUTED, "reason_code": None,
        "test_window": {"start": dates[lo].isoformat(), "end": dates[hi - 1].isoformat(), "sessions": n_test},
        "plan": {"train_sessions": plan.train_sessions, "test_sessions": plan.test_sessions,
                 "purge_sessions": plan.purge_sessions, "anchored": plan.anchored,
                 "label_horizon_sessions": plan.label_horizon_sessions,
                 "embargo_sessions": plan.embargo_sessions},
        "folds": fold_records, "comparators": comparators, "realized_test_window": realized,
        "dsr_trials": sizing, "kelly_cap_note": cap_note, "trial_log_sha256": trial_sha,
        "paired_bootstrap": paired, "coverage": coverage_disclosure(dates, folds), "metric_basis": METRIC_BASIS,
        "cost_model_disclosure": COST_MODEL_DISCLOSURE,
        "evaluation_contract": {
            "mean": "TRAIN_ONLY_EXCESS_MEAN_POSITIVE_PART_SHRINK_SAME_AS_RS04",
            "covariance": "LEDOIT_WOLF_2004_SCALED_IDENTITY_ANALYTIC_DEMEANED_1_OVER_N",
            "kelly": "SIGMA_INV_MU_SHRUNK", "methods": list(ms), "primary_method": primary,
            "constraints": "LONG_ONLY_SUM_LE_1_REST_CASH", "min_train_observations": MIN_TRAIN_OBSERVATIONS,
            "parked_fold_weights": "ALL_CASH", "rebalance": "TO_TARGET_EVERY_SESSION_START_FROM_CASH",
            "cost": "QPK_COST_STRESS_SINGLE_SIDE_ON_TRADED_NOTIONAL_CASH_LEG_FREE",
            "strategy_internal_costs": ("STRESSED_VIA_SUPPLIED_TURNOVER_SCALED_BY_WEIGHT" if internal is not None
                                        else "AS_SUPPLIED_NOT_STRESSED"),
            "benchmarks": "BUY_AND_HOLD_ENTRY_FROM_CASH_CHARGED; 50_50_REBALANCED_EVERY_SESSION",
            "dsr_trial_set": "ALL_SIZING_VARIANTS_NET_1X_EXCESS",
            "base_cost_bps_per_side": base_bps, "multipliers": [float(m) for m in multipliers],
            "same_window": True, "periods_per_year": PERIODS_PER_YEAR, "asset_order": [a0, a1, *assets[2:]],
        },
    }
    if report_context is not None:
        result["backtest_report"] = _build_report(stats, result, versioned_report_context(report_context, EVALUATOR_VERSION, plan), dates, folds, base_bps,
                                                  multipliers, trial_sha, len(sizing))
    return result
