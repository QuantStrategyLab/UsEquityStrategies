"""Multi-strategy (portfolio) fractional-Kelly walk-forward evaluator (RS-06, research-only).

For N already-defined strategy return streams (full exposure each, with their
internal turnover) plus a cash series, each walk-forward fold fits on the
**training** sessions only:

1. per-asset excess mean over cash, shrunk toward zero exactly like RS-04
   (positive-part ``max(0, 1 - 1/t^2)``);
2. covariance of training excess returns with Ledoit-Wolf (2004) linear
   shrinkage toward a scaled identity (analytic intensity, demeaned, 1/n), as
   ``sklearn.covariance.ledoit_wolf``;
3. full Kelly ``f* = Sigma^-1 mu`` and, for each ``c`` in the pre-registered
   fractions, long-only / no-leverage weights by two pre-registered methods:
   ``qp`` (maximise ``w'mu - w'Sigma w / (2c)`` s.t. ``w >= 0``, ``sum w <= 1``,
   exact KKT active-set enumeration) and ``clip_scale`` (``c f*``, negatives to
   0, scaled down to ``sum <= 1``).

Weights are held through the **test** fold (after a purge gap) and rebalanced to
target at every session start; the rest sits in cash. Comparators run on exactly
the same stitched test sessions and cost tiers: single-strategy Kelly per asset
(the RS-04 fit), equal weight 50/50 and 25/25, fixed 50 % and full exposure per
asset, and buy-and-hold / 50-50 benchmarks.

Boundaries: no leverage, no shorting; synthetic by default; real data only via a
``PortfolioRealDataAuthorization`` matching ``portfolio_kelly_preregistration``;
results only inform a cap note and never raise any budget; ``UNCOMPUTABLE`` when
QPK ``research_stats`` is unavailable. Runtime code never imports this module.
"""
from __future__ import annotations

import hashlib
import itertools
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
    coverage_disclosure,
    paired_stationary_bootstrap,
)
from us_equity_strategies.research.portfolio_kelly_preregistration import (
    METHOD_CLIP_SCALE,
    METHOD_QP,
    METHODS,
    PortfolioRealDataAuthorization,
    authorize_portfolio_real_data,
)

EVALUATOR_VERSION = "portfolio_kelly_walk_forward_v1"
STATUS_COMPUTED = rs04.STATUS_COMPUTED
STATUS_UNCOMPUTABLE = rs04.STATUS_UNCOMPUTABLE
STATUS_PARKED = rs04.STATUS_PARKED
PERIODS_PER_YEAR = rs04.PERIODS_PER_YEAR
MIN_TRAIN_OBSERVATIONS = rs04.MIN_TRAIN_OBSERVATIONS
_TOL = 1e-12


# ---------------- small dense linear algebra (N is tiny) ----------------

def _solve(a: Sequence[Sequence[float]], b: Sequence[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when (near) singular."""
    n = len(b)
    m = [list(map(float, row)) + [float(b[i])] for i, row in enumerate(a)]
    scale = max((abs(x) for row in a for x in row), default=0.0)
    if scale == 0.0:
        return None
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) <= 1e-14 * scale:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            for k in range(col, n + 1):
                m[r][k] -= factor * m[col][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - math.fsum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _matvec(a: Sequence[Sequence[float]], x: Sequence[float]) -> list[float]:
    return [math.fsum(a_ij * x_j for a_ij, x_j in zip(row, x)) for row in a]


def _corr(cov: Sequence[Sequence[float]]) -> list[list[float]]:
    d = [math.sqrt(cov[i][i]) for i in range(len(cov))]
    return [[cov[i][j] / (d[i] * d[j]) if d[i] > 0 and d[j] > 0 else float("nan")
             for j in range(len(cov))] for i in range(len(cov))]


def sample_covariance(columns: Sequence[Sequence[float]], ddof: int) -> list[list[float]]:
    n, p = len(columns[0]), len(columns)
    means = [math.fsum(c) / n for c in columns]
    return [[math.fsum((columns[i][t] - means[i]) * (columns[j][t] - means[j]) for t in range(n)) / (n - ddof)
             for j in range(p)] for i in range(p)]


def ledoit_wolf(columns: Sequence[Sequence[float]]) -> tuple[list[list[float]], float]:
    """Ledoit-Wolf (2004) shrunk covariance toward ``mu * I`` (sklearn ledoit_wolf, assume_centered=False)."""
    p, n = len(columns), len(columns[0])
    means = [math.fsum(c) / n for c in columns]
    x = [[columns[i][t] - means[i] for i in range(p)] for t in range(n)]
    emp = [[math.fsum(x[t][i] * x[t][j] for t in range(n)) / n for j in range(p)] for i in range(p)]
    mu = math.fsum(emp[i][i] for i in range(p)) / p
    delta = math.fsum((emp[i][j] - (mu if i == j else 0.0)) ** 2 for i in range(p) for j in range(p)) / p
    x2 = [[v * v for v in row] for row in x]
    beta_sum = math.fsum(math.fsum(x2[t][i] * x2[t][j] for t in range(n)) for i in range(p) for j in range(p))
    sq_emp = math.fsum(emp[i][j] ** 2 for i in range(p) for j in range(p))
    beta = (beta_sum / n - sq_emp) / (p * n)
    beta = min(beta, delta)
    shrinkage = 0.0 if beta == 0.0 else beta / delta
    shrunk = [[(1.0 - shrinkage) * emp[i][j] + (shrinkage * mu if i == j else 0.0) for j in range(p)]
              for i in range(p)]
    return shrunk, shrinkage


# ---------------- constrained fractional Kelly ----------------

def _objective(w: Sequence[float], mu: Sequence[float], cov: Sequence[Sequence[float]], c: float) -> float:
    return math.fsum(a * b for a, b in zip(w, mu)) - math.fsum(a * b for a, b in zip(w, _matvec(cov, w))) / (2 * c)


def constrained_kelly_qp(mu: Sequence[float], cov: Sequence[Sequence[float]], c: float) -> list[float] | None:
    """Maximise ``w'mu - w'Sigma w/(2c)`` s.t. ``w >= 0``, ``sum(w) <= 1`` by KKT active-set enumeration."""
    p = len(mu)
    best: tuple[float, list[float]] | None = None
    for size in range(p + 1):
        for subset in itertools.combinations(range(p), size):
            for budget in (False, True):
                if size == 0 and budget:
                    continue
                w = [0.0] * p
                lam = 0.0
                if size:
                    sub = [[cov[i][j] for j in subset] for i in subset]
                    inv_mu = _solve(sub, [mu[i] for i in subset])
                    inv_one = _solve(sub, [1.0] * size)
                    if inv_mu is None or inv_one is None:
                        continue
                    if budget:
                        denom = math.fsum(inv_one)
                        if abs(denom) <= _TOL:
                            continue
                        lam = (math.fsum(inv_mu) - 1.0 / c) / denom
                        if lam < -_TOL:
                            continue
                    for k, i in enumerate(subset):
                        w[i] = c * (inv_mu[k] - lam * inv_one[k])
                    if any(w[i] < -_TOL for i in subset):
                        continue
                total = math.fsum(w)
                if total > 1.0 + 1e-9:
                    continue
                grad = [mu[j] - g / c - lam for j, g in enumerate(_matvec(cov, w))]
                if any(grad[j] > 1e-10 * (1.0 + abs(mu[j])) for j in range(p) if j not in subset):
                    continue
                w = [max(0.0, v) for v in w]
                if math.fsum(w) > 1.0:
                    w = [v / math.fsum(w) for v in w]
                value = _objective(w, mu, cov, c)
                if best is None or value > best[0] + 1e-15:
                    best = (value, w)
    return None if best is None else best[1]


def clip_scale(full_kelly: Sequence[float], c: float) -> list[float]:
    w = [max(0.0, c * f) for f in full_kelly]
    total = math.fsum(w)
    return [v / total for v in w] if total > 1.0 else w


def fit_portfolio_kelly(assets: Sequence[str], strategy: Mapping[str, Sequence[float]], cash: Sequence[float],
                        fractions: Sequence[float], methods: Sequence[str] = METHODS) -> dict:
    """Fit one training sample for all assets; PARKED (all cash) when any asset cannot be fitted."""
    n = len(cash)
    singles = {a: rs04.fit_fractional_kelly(strategy[a], cash, 0.5) for a in assets}
    base = {"train_observations": n, "assets": list(assets)}
    parked = {"status": STATUS_PARKED, "weights": {format(c, "g"): {m: dict.fromkeys(assets, 0.0) for m in methods}
                                                    for c in fractions}}
    if n < MIN_TRAIN_OBSERVATIONS:
        return {**base, **parked, "reason_code": "INSUFFICIENT_TRAIN_OBSERVATIONS"}
    bad = [a for a in assets if singles[a]["status"] != STATUS_COMPUTED]
    if bad:
        return {**base, **parked, "reason_code": "ASSET_NOT_FITTABLE:" + ",".join(bad)}
    excess = [[s - k for s, k in zip(strategy[a], cash)] for a in assets]
    cov_lw, shrink = ledoit_wolf(excess)
    cov_s = sample_covariance(excess, ddof=1)
    mu = [singles[a]["mu"] for a in assets]
    mu_s = [singles[a]["mu_shrunk"] for a in assets]
    full = _solve(cov_lw, mu_s)
    if full is None:
        return {**base, **parked, "reason_code": "COVARIANCE_SINGULAR"}
    weights: dict[str, dict[str, dict[str, float]]] = {}
    for c in fractions:
        per = {}
        for m in methods:
            if m == METHOD_QP:
                w = constrained_kelly_qp(mu_s, cov_lw, c)
                if w is None:
                    return {**base, **parked, "reason_code": "QP_NO_KKT_POINT"}
            elif m == METHOD_CLIP_SCALE:
                w = clip_scale(full, c)
            else:
                raise ValueError("METHOD_INVALID")
            per[m] = dict(zip(assets, w))
        weights[format(c, "g")] = per
    sd = [math.sqrt(cov_s[i][i]) for i in range(len(assets))]
    return {**base, "status": STATUS_COMPUTED, "reason_code": None,
            "mu_excess": dict(zip(assets, mu)), "mu_shrunk": dict(zip(assets, mu_s)),
            "t_stat": {a: singles[a]["t_stat"] for a in assets},
            "sharpe_annualized": {a: (mu[i] / sd[i] * math.sqrt(PERIODS_PER_YEAR) if sd[i] > 0 else None)
                                  for i, a in enumerate(assets)},
            "covariance_sample": cov_s, "covariance_ledoit_wolf": cov_lw, "ledoit_wolf_shrinkage": shrink,
            "correlation_sample": _corr(cov_s), "correlation_ledoit_wolf": _corr(cov_lw),
            "full_kelly": dict(zip(assets, full)), "weights": weights}


# ---------------- paths ----------------

def portfolio_path(schedule: Sequence[Mapping[str, float]], returns: Mapping[str, Sequence[float]],
                   cash: Sequence[float], turnover: Mapping[str, Sequence[float]] | None
                   ) -> tuple[list[float], list[float]]:
    """Gross returns and traded notional for a per-session target-weight schedule (start in cash)."""
    held: dict[str, float] = {}
    gross, traded = [], []
    for i, target in enumerate(schedule):
        keys = set(target) | set(held)
        t = math.fsum(abs(target.get(a, 0.0) - held.get(a, 0.0)) for a in keys)
        if turnover is not None:
            t += math.fsum(w * turnover[a][i] for a, w in target.items() if a in turnover)
        invested = math.fsum(target.values())
        r = math.fsum(w * returns[a][i] for a, w in target.items()) + (1.0 - invested) * cash[i]
        gross.append(r)
        traded.append(t)
        end = 1.0 + r
        held = {} if end <= 0 else {a: w * (1.0 + returns[a][i]) / end for a, w in target.items()}
    return gross, traded


def _sharpe(series: Sequence[float]) -> float | None:
    n = len(series)
    if n < 2:
        return None
    m = math.fsum(series) / n
    sd = math.sqrt(math.fsum((x - m) ** 2 for x in series) / (n - 1))
    return m / sd if sd > 0 else None


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
) -> dict:
    if synthetic is True:
        if real_data is not None:
            raise ValueError("REAL_DATA_AUTHORIZATION_WITH_SYNTHETIC")
    elif synthetic is not False or real_data is None:
        raise ValueError("REAL_DATA_REQUIRES_APPROVAL")
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
    prereg = None
    if synthetic is False:
        prereg = authorize_portfolio_real_data(real_data, plan=plan, fractions=cs, methods=ms,
                                               cost_bps_per_side=base_bps, multipliers=multipliers, assets=assets,
                                               dates=dates, report_context=report_context)
    envelope = {
        "research_only": True, "synthetic": prereg is None, "promotion_eligible": False,
        "data_identity": "development",
        "preregistration_id": None if prereg is None else prereg.preregistration_id,
        "strategy_variant": None if prereg is None else prereg.variant,
        "gates": () if prereg is None else prereg.gates, "assets": list(assets),
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "evaluator_version": EVALUATOR_VERSION, "kelly_ready_role": "CAP_NOTE_ONLY_NEVER_RAISES_BUDGET",
        "budget_effect": "NONE", "full_kelly_allowed": False, "leverage_allowed": False, "short_allowed": False,
    }
    stats = rs04._stats_module()
    if stats is None:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": rs04.UNAVAILABLE}
    folds = plan.folds(len(dates))
    if not folds:
        return {**envelope, "status": STATUS_UNCOMPUTABLE, "reason_code": "NO_COMPLETE_FOLD"}

    lo, hi = folds[0][2], folds[-1][3]
    n_test = hi - lo
    r_test = {a: rets[a][lo:hi] for a in assets}
    c_test = cash[lo:hi]
    int_test = None if internal is None else {a: internal[a][lo:hi] for a in assets}

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
    b_test = {k: v[lo:hi] for k, v in bench.items()}
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
    trial_log = json.dumps({"folds": fold_records, "fractions": list(cs), "methods": list(ms), "trials": sizing},
                           sort_keys=True, allow_nan=False)
    trial_sha = hashlib.sha256(trial_log.encode()).hexdigest()
    result = {
        **envelope, "status": STATUS_COMPUTED, "reason_code": None,
        "test_window": {"start": dates[lo].isoformat(), "end": dates[hi - 1].isoformat(), "sessions": n_test},
        "plan": {"train_sessions": plan.train_sessions, "test_sessions": plan.test_sessions,
                 "purge_sessions": plan.purge_sessions, "anchored": plan.anchored},
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
        result["backtest_report"] = _build_report(stats, result, report_context, dates, folds, base_bps,
                                                  multipliers, trial_sha, len(sizing))
    return result


def _build_report(stats: Any, result: dict, ctx: Mapping[str, Any], dates: Sequence[date],
                  folds: Sequence[tuple[int, int, int, int]], base_bps: float, multipliers: Sequence[float],
                  trial_sha: str, n_trials: int) -> dict:
    metrics = []
    for name, comp in result["comparators"].items():
        for tier, values in comp["tiers"].items():
            for key in ("log_growth_per_session", "cagr", "max_drawdown", "cvar_95_daily"):
                if values.get("status") == STATUS_COMPUTED:
                    metrics.append(stats.MetricEntry(f"{name}.{key}", float(tier), STATUS_COMPUTED, values[key]))
                else:
                    metrics.append(stats.MetricEntry(f"{name}.{key}", float(tier), STATUS_UNCOMPUTABLE, None,
                                                     values.get("reason_code") or "UNCOMPUTABLE"))
    report = stats.BacktestReportV1(
        report_id=ctx["report_id"], candidate_id=ctx["candidate_id"], source_revision=ctx["source_revision"],
        created_at=ctx["created_at"],
        data=stats.DataSpec(ctx["manifest_sha256"], ctx["source"], ctx["license"], "XNYS",
                            dates[folds[0][0]].isoformat(), dates[folds[-1][3] - 1].isoformat(),
                            folds[-1][3] - folds[0][0], "development"),
        timing=stats.TimingSpec(ctx["signal_cutoff"], ctx["execution"], "rebalance_to_target_every_session"),
        cost_model=stats.CostModelSpec(ctx.get("cost_model_id", "single_side_bps"), base_bps, 0.0, 0.0,
                                       tuple(float(m) for m in multipliers),
                                       ctx.get("settlement_rule", "not_modeled_fractional_weights"), False),
        benchmark=stats.BenchmarkSpec(ctx["benchmark_name"], ctx["benchmark_rationale"]),
        trials=stats.TrialSummary(n_trials, 0, 0, "report-only: no selection among sizing variants", trial_sha),
        walk_forward=stats.WalkForwardSpec(
            "session_index_walk_forward_purged",
            tuple(stats.WalkForwardFold(dates[a].isoformat(), dates[b - 1].isoformat(),
                                        dates[c].isoformat(), dates[d - 1].isoformat()) for a, b, c, d in folds),
            None),
        status="DEVELOPMENT_ONLY", metrics=tuple(metrics),
        limitations=(*tuple(ctx.get("limitations", ())),
                     "portfolio fractional Kelly output is a cap note only; it never raises any budget",
                     rs04._tail_note(result["coverage"]),
                     "PSR/bootstrap reported on net 1x (primary) and gross (diagnostic); DSR on net 1x",
                     "outer cost model is a virtual-asset approximation, not security-level netted; "
                     "outer cash leg free"),
    )
    return report.to_dict()
