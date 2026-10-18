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
    netting_candidate: Mapping[str, Any] | None = None,
) -> dict:
    if netting_candidate is not None and synthetic is not True:
        raise ValueError("NETTING_SYNTHETIC_ONLY")
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
    if netting_candidate is not None:
        if asset_turnover is not None or benchmark_returns or report_context is not None or bootstrap_seed is not None:
            raise ValueError("NETTING_SINGLE_SECURITY_FIXTURE_ONLY")
        return _net_candidate(request=netting_candidate, assets=assets, dates=dates, rets=rets, cash=cash,
                              plan=plan, cs=cs, ms=ms, base_bps=base_bps, multipliers=multipliers,
                              envelope=envelope)
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


# ---------------- single synthetic actual-net candidate ----------------

def _candidate_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _check_netting_store(root: Any) -> None:
    """Reject links and escapes in this dedicated local trial, before store I/O."""
    try:
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("NETTING_STORE_SYMLINK")
        resolved_root = root.resolve()
        pending = [root]
        while pending:
            path = pending.pop()
            if path.is_symlink():
                raise ValueError("NETTING_STORE_SYMLINK")
            if not path.resolve().is_relative_to(resolved_root):
                raise ValueError("NETTING_STORE_PATH_ESCAPE")
            if path.exists():
                if path.is_dir():
                    pending.extend(path.iterdir())
                elif not path.is_file() or path == root:
                    raise ValueError("NETTING_STORE_PATH_INVALID")
    except OSError as exc:
        raise ValueError("NETTING_STORE_PATH_UNREADABLE") from exc


def _net_candidate(*, request: Mapping[str, Any], assets: Sequence[str], dates: Sequence[date],
                   rets: Mapping[str, Sequence[float]], cash: Sequence[float], plan: WalkForwardPlan,
                   cs: Sequence[float], ms: Sequence[str], base_bps: float,
                   multipliers: Sequence[float], envelope: dict) -> dict:
    """Thin RS06 adapter, one fixture trial. Never a research search or live entry.

    Each input strategy is exactly one synthetic security, with price-only total
    returns and no internal turnover. ``f=1`` allocates the whole book to the
    inner sleeve; fitted Kelly weights are its security weights, with residual
    cash. Fraction c is NOT outer f. Cash return must be zero because #624's
    inner residual is non-interest-bearing. Close prices mark fractional shares
    in dollars; trades and fees are dollars, returns signed decimal fractions.

    Optional ``risk_budget`` requires explicit policy and asset_risk_specs. Its
    cash symbol represents inner residual cash, never a traded security. This
    turnover gate uses the runner's fill-session pre-trade/pre-fee net book.
    Its current marks are not information available at the prior decision.
    """
    # Capture options before imports, store callbacks or fitting can observe
    # caller-owned mutable maps. Absence leaves legacy identities/bytes intact.
    request = dict(request)
    cost_options = None
    if "research_cost_inputs" in request:
        if not isinstance(request["research_cost_inputs"], Mapping):
            raise ValueError("COST_OPENING_REQUIRED")
        try:
            cost_options = json.loads(json.dumps(dict(request["research_cost_inputs"]), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("COST_FACT_INVALID") from exc
    from dataclasses import asdict, replace
    from pathlib import Path
    from tempfile import gettempdir

    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    from quant_platform_kit.strategy_lifecycle.contracts import (
        BacktestResult, ResearchDailyLedger, ResearchLedgerDay, ResearchPositionMark,
        ResearchTrialRecord, ResearchTrialStatus,
    )
    from quant_platform_kit.strategy_lifecycle.performance_store import SCHEMA_VERSION, PerformanceStore

    from us_equity_strategies.research.security_netting_runner import (
        RS06_TURNOVER_CURRENT_BOOK_BASIS, NettingSession, run_offline_security_netting,
    )

    if len(cs) != 1 or len(ms) != 1 or tuple(multipliers) != (1.,):
        raise ValueError("NETTING_SINGLE_CANDIDATE_REQUIRED")
    folds = plan.folds(len(dates))
    if len(folds) != 1 or folds[0][3] != len(dates):
        raise ValueError("NETTING_ONE_COMPLETE_FOLD_REQUIRED")
    tr0, tr1, te0, te1 = folds[0]
    if any(cash):
        raise ValueError("NETTING_ZERO_RESIDUAL_CASH_RETURN_REQUIRED")
    spec = request["spec"]
    if (type(spec) is not SecurityNettingSpec or spec.outer_cash_mode != "separate"
            or spec.cash_like_symbols or set(spec.bps_per_side) != set(assets)
            or any(v != base_bps for v in spec.bps_per_side.values()) or spec.multiplier != 1.):
        raise ValueError("NETTING_COST_OR_SECURITY_IDENTITY_MISMATCH")
    spec = replace(spec, bps_per_side=dict(spec.bps_per_side))
    if request["fee_cash_role"] != spec.inner_residual_role:
        raise ValueError("NETTING_FEE_ROLE_INVALID")
    if request["end_target"] is not None and request["end_target"] != {}:
        raise ValueError("NETTING_END_TARGET_INVALID")
    budget_config = None
    turnover_context = None
    if "risk_budget" in request:
        from us_equity_strategies.portfolio_risk_budget import (
            PortfolioAssetRiskSpec, PortfolioRiskBudgetPolicy, assess_portfolio_risk_budget,
        )

        supplied = request["risk_budget"]
        if not isinstance(supplied, Mapping) or set(supplied) != {"policy", "asset_risk_specs"}:
            raise ValueError("NETTING_RISK_BUDGET_NOT_CONFIGURED")
        policy, risk_specs = supplied["policy"], supplied["asset_risk_specs"]
        if type(policy) is not PortfolioRiskBudgetPolicy or not isinstance(risk_specs, Mapping):
            raise ValueError("NETTING_RISK_BUDGET_NOT_CONFIGURED")
        try:
            # Frozen dataclasses may still contain caller-owned mutable maps.
            policy = replace(policy, max_symbol_weights=dict(policy.max_symbol_weights),
                             max_underlying_effective_exposure=dict(policy.max_underlying_effective_exposure))
            risk_specs = dict(risk_specs)
            if (policy.cash_symbol in assets or set(risk_specs) != set(assets) | {policy.cash_symbol}
                    or any(type(v) is not PortfolioAssetRiskSpec for v in risk_specs.values())
                    or risk_specs[policy.cash_symbol].is_cash is not True
                    or any(risk_specs[a].is_cash is not False for a in assets)):
                raise ValueError("cash/security mapping")
            budget_config = {"policy": asdict(policy),
                             "asset_risk_specs": {a: asdict(v) for a, v in risk_specs.items()},
                             "cash_representation": spec.inner_residual_role}
            _candidate_digest(budget_config)
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("NETTING_RISK_BUDGET_INVALID") from exc
        if policy.max_one_way_risk_turnover is not None:
            budget_config["turnover_current_book_basis"] = RS06_TURNOVER_CURRENT_BOOK_BASIS
            turnover_context = {"policy": policy, "asset_risk_specs": risk_specs,
                                "synthetic": envelope["synthetic"],
                                "valuation_basis": RS06_TURNOVER_CURRENT_BOOK_BASIS}
    periods = request["periods_per_year"]
    if type(periods) is not int or periods <= 0:
        raise ValueError("NETTING_PERIODS_INVALID")
    # Absence is the original legacy rf=0 assumption, never v2 qualification.
    # Bind explicit rf/calendar inputs into the existing immutable candidate
    # identity so recovery cannot reinterpret an old report under new inputs.
    report_options = {}
    if request.get("sharpe_basis", "legacy_net_rf0_v1") != "legacy_net_rf0_v1":
        report_options = {"sharpe_basis": request["sharpe_basis"],
                          "risk_free_returns": (None if request.get("risk_free_returns") is None
                                                else list(request["risk_free_returns"])),
                          "risk_free_source": request.get("risk_free_source"),
                          "calendar_id": request["calendar_id"]}
        from quant_platform_kit.research_stats.unified_report import build_unified_net_report
        from inspect import signature
        if not set(report_options).issubset(signature(build_unified_net_report).parameters):
            raise ValueError("QPK_EXPLICIT_SHARPE_BASIS_REQUIRED")
        # Validate basis/length/source before any trial is persisted. These
        # zeros are validation-only placeholders, never research observations.
        build_unified_net_report([0.] * (te1 - te0), periods_per_year=periods, **report_options)
    elif request.get("risk_free_returns") is not None or request.get("risk_free_source") is not None:
        raise ValueError("RF_INPUTS_REQUIRE_EXPLICIT_BASIS")
    initial_nav = rs04._finite(request["initial_nav"], "INITIAL_NAV_INVALID")
    if initial_nav <= 0:
        raise ValueError("INITIAL_NAV_INVALID")
    source = request["source_revision"]
    if not isinstance(source, str) or len(source) != 40 or any(c not in "0123456789abcdef" for c in source):
        raise ValueError("NETTING_SOURCE_INVALID")
    prices = {}
    if set(request["close_prices"]) != set(assets):
        raise ValueError("NETTING_PRICE_IDENTITY_MISMATCH")
    for a in assets:
        rows = request["close_prices"][a]
        if tuple(d for d, _ in rows) != tuple(dates):
            raise ValueError("NETTING_PRICE_SESSIONS_INVALID")
        vals = tuple(rs04._finite(v, "NETTING_PRICE_INVALID") for _, v in rows)
        if any(v <= 0 for v in vals):
            raise ValueError("NETTING_PRICE_INVALID")
        if any(not math.isclose(vals[i] / vals[i - 1] - 1, rets[a][i], rel_tol=1e-10, abs_tol=1e-12)
               for i in range(1, len(dates))):
            raise ValueError("NETTING_PRICE_RETURN_MISMATCH")
        prices[a] = vals
    decisions = tuple(request["decision_sessions"])
    expected_decisions = tuple(zip(dates[te0:te1], dates[te0 - 1:te1 - 1]))
    if decisions != expected_decisions or dates[tr1 - 1] > decisions[0][1]:
        raise ValueError("NETTING_DECISION_NOT_PRIOR_SUPPLIED_SESSION")
    if cost_options is not None:
        from us_equity_strategies.research.security_netting_runner import _research_cost_begin

        _research_cost_begin(cost_options, [NettingSession(d, prior, 1., {}, {}, 0.) for d, prior in decisions],
                             {}, initial_nav)
        # Cost quantities and candidate fractional shares use the same explicit
        # prices. These supplied close marks establish no real source or PIT.
        for i, (day, _) in enumerate(decisions, start=te0):
            marks = cost_options["fill_marks"][day.isoformat()]
            if not isinstance(marks, dict) or set(marks) != set(assets):
                raise ValueError("COST_MARKS_REQUIRED")
            if any(rs04._finite(marks[a], "COST_MARKS_REQUIRED") != prices[a][i] for a in assets):
                raise ValueError("COST_MARK_BOOK_MISMATCH")
    raw_root = Path(request["local_root"]).absolute()
    if any(p.is_symlink() for p in (raw_root, *raw_root.parents)):
        raise ValueError("NETTING_STORE_SYMLINK")
    root = raw_root.resolve()
    if not root.is_relative_to(Path(gettempdir()).resolve()) or root == Path(gettempdir()).resolve():
        raise ValueError("NETTING_EXPLICIT_TEMP_STORE_REQUIRED")
    _check_netting_store(root)
    store = PerformanceStore(local_root=root, cloud_bucket="")
    config = {"method": ms[0], "fraction": cs[0], "plan": asdict(plan), "spec": asdict(spec),
              "initial_nav": initial_nav, "end_target": request["end_target"],
              "fee_cash_role": request["fee_cash_role"], "calendar_id": request["calendar_id"],
              "periods_per_year": periods, "execution": "next_close", "initial_f": 1.,
              "inner_units": "one_strategy_one_security_fractional_shares", "outer_cash_return": 0.}
    if budget_config is not None:
        config["risk_budget"] = budget_config
    if report_options:
        config["report_basis"] = report_options
    if cost_options is not None:
        config["research_cost_inputs"] = cost_options
    input_sha = _candidate_digest({"dates": [d.isoformat() for d in dates], "returns": rets,
                                   "cash": cash, "prices": prices,
                                   "decisions": [[d.isoformat(), p.isoformat()] for d, p in decisions]})
    config_sha = _candidate_digest(config)
    identity = {"candidate_id": request["candidate_id"], "trial_id": request["trial_id"],
                "run_id": request["run_id"], "calendar_id": request["calendar_id"],
                "periods_per_year": periods, "cost_source": "security_level_netting_v1",
                "cost_spec": asdict(spec), "initial_session_date": dates[te0 - 1].isoformat(),
                "window_end": dates[te1 - 1].isoformat(),
                "source_revision": source, "input_sha256": input_sha, "config_sha256": config_sha,
                "synthetic": True, "no_order": True, "live_ready": False,
                "promotion_eligible": False, "budget_effect": "NONE"}
    if budget_config is not None:
        identity["risk_budget"] = budget_config
    if report_options:
        identity["report_basis"] = report_options
    if cost_options is not None:
        identity["research_cost_inputs"] = cost_options
    identity = json.loads(json.dumps(identity, allow_nan=False))
    cost_inputs = {**{a + "_bps_per_side": float(spec.bps_per_side[a]) for a in assets}, "multiplier": 1.}
    started = ResearchTrialRecord(
        trial_id=request["trial_id"], domain="us_equity", strategy_profile="rs06_synthetic_netting",
        status=ResearchTrialStatus.STARTED, candidate_config_id=request["candidate_id"], actual_params=None,
        param_set_id=config_sha, source_revision=source, input_id=input_sha,
        window_start=dates[te0 - 1], window_end=dates[te1 - 1], calendar_id=request["calendar_id"],
        periods_per_year=periods, cost_source="security_level_netting_v1", cost_inputs=cost_inputs,
        reason_code="", synthetic=True, run_id=None, param_version=None, research_identity=identity)
    params = {"method": ms[0], "fraction": cs[0], "synthetic": True, "no_order": True,
              "live_ready": False, "promotion_eligible": False, "budget_effect": "NONE"}
    response = {**envelope, "binding": identity, "trial_id": started.trial_id}

    def candidate_result(ledger: Any, public: dict) -> Any:
        return BacktestResult(
            strategy_profile=started.strategy_profile, domain=started.domain, param_set_id=config_sha,
            params={**params, "research_identity": identity}, **public, total_return=ledger.total_return,
            start_date=ledger.window_start, end_date=ledger.window_end, observation_count=ledger.observation_count,
            run_id=request["run_id"], source_script=__name__ + ".evaluate_portfolio_kelly_walk_forward",
            source_revision=source, cost_model=started.cost_source, cost_inputs=cost_inputs,
            calendar_id=started.calendar_id, periods_per_year=periods)

    def store_call(method: Any, *args: Any, **kwargs: Any) -> Any:
        _check_netting_store(root)
        return method(*args, **kwargs)

    receipt_key = store._research_key(started.domain, started.strategy_profile, started.trial_id, "receipt")

    def record_digests(terminal: Any, *, before_terminal: bool = False) -> dict:
        keys = {name: store._research_key(started.domain, started.strategy_profile, started.trial_id, name)
                for name in ("started", "ledger", "terminal")}
        keys["backtest"] = store._backtest_key(candidate_result_placeholder)
        digests = {}
        for name, key in keys.items():
            _check_netting_store(root)
            data = ({**terminal.to_dict(), "schema_version": SCHEMA_VERSION}
                    if before_terminal and name == "terminal"
                    else json.loads((root / key).read_text(encoding="utf-8")))
            digests[name] = _candidate_digest(data)
        return digests

    # Only the existing run-based key is needed before the result is written.
    candidate_result_placeholder = BacktestResult(
        started.strategy_profile, started.domain, config_sha, {}, run_id=request["run_id"])

    def acceptance_binding(payload: dict) -> dict:
        return {**identity, "report_sha256": _candidate_digest(payload["netting"]["unified_net_report"]),
                "netting_sha256": _candidate_digest(payload["netting"]),
                "ledger_sha256": _candidate_digest(payload["research_ledger"]),
                "result_sha256": _candidate_digest(payload["candidate"]),
                "trial_sha256": _candidate_digest(payload["trial"]),
                "folds_sha256": _candidate_digest(payload["folds"])}

    def cost_binding(net: dict) -> dict:
        companion = net.get("research_cost_receipt")
        if not isinstance(companion, dict):
            raise ValueError("COST_COMPANION_INCOMPLETE")
        return {"candidate_identity": identity, "options_sha256": _candidate_digest(cost_options),
                **{key: companion.get(key) for key in ("receipt_id", "source_hash", "ledger_hash")}}

    def verify_cost_companion(net: dict) -> None:
        if cost_options is None:
            if "research_cost_receipt" in net or "research_cost_binding" in net:
                raise ValueError("COST_COMPANION_UNEXPECTED")
            return
        if cost_options is not None:
            from us_equity_strategies.research.security_netting_runner import _verify_research_cost_receipt

            _verify_research_cost_receipt(cost_options, net["ledger"], initial_nav, spec.multiplier,
                                         net.get("research_cost_receipt"))
            if _candidate_digest(net.get("research_cost_binding")) != _candidate_digest(cost_binding(net)):
                raise ValueError("COST_CANDIDATE_BINDING")

    def verify_recovered_book(net: dict, ledger: Any) -> None:
        """Reconcile returned book to stored marks/cash; never refit or replay fills."""
        from quant_platform_kit.research_stats.unified_report import build_unified_net_report

        def close(actual: Any, expected: float) -> None:
            number = rs04._finite(actual, "NETTING_RECOVERY_BOOK_MISMATCH")
            if not math.isclose(number, expected, rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError("NETTING_RECOVERY_BOOK_MISMATCH")

        def securities(actual: Mapping[str, float], expected: Mapping[str, float]) -> None:
            if not isinstance(actual, Mapping) or not set(actual).issubset(assets):
                raise ValueError("NETTING_RECOVERY_BOOK_MISMATCH")
            for symbol in assets:
                close(actual.get(symbol, 0.), expected.get(symbol, 0.))

        net_returns = [day.daily_return for day in ledger.days]
        if (len(net["ledger"]) != len(ledger.days) or net["net_returns"] != net_returns
                or net["initial_nav"] != ledger.initial_nav or net["final_nav"] != ledger.days[-1].nav
                or net["no_order"] is not True or net["live_ready"] is not False
                or net["timing"] != {"execution": "next_close"}
                or net["fee_cash_role"] != spec.inner_residual_role
                or _candidate_digest(net["spec"]) != _candidate_digest(asdict(spec))):
            raise ValueError("NETTING_RECOVERY_BOOK_MISMATCH")
        basis = net["unified_net_report_basis"]
        if (basis["sessions"] != [day.session_date.isoformat() for day in ledger.days]
                or basis["periods_per_year"] != periods or basis["initial_nav_is_return_observation"] is not False
                or basis["costs_deducted_again"] is not False):
            raise ValueError("NETTING_RECOVERY_REPORT_BASIS_MISMATCH")
        previous_nav, previous_cash = ledger.initial_nav, ledger.initial_cash
        previous_values = {mark.symbol: mark.valuation for mark in ledger.initial_positions}
        gross = []
        for i, (row, day) in enumerate(zip(net["ledger"], ledger.days, strict=True), start=te0):
            end_values = {mark.symbol: mark.valuation for mark in day.positions}
            pnl = {a: value * rets[a][i] for a, value in previous_values.items()}
            pre_values = {a: value + pnl[a] for a, value in previous_values.items()}
            trades = {a: end_values.get(a, 0.) - pre_values.get(a, 0.) for a in assets}
            if (row["session"] != day.session_date.isoformat()
                    or row["decision_session"] != dates[i - 1].isoformat()
                    or row["fees"] != day.fees or row["net_return"] != day.daily_return
                    or row["nav_start"] != previous_nav or row["nav_end"] != day.nav
                    or row["fee_cash_role"] != spec.inner_residual_role or row["f_target"] != 1.):
                raise ValueError("NETTING_RECOVERY_BOOK_MISMATCH")
            for field, values in (("security_start", previous_values), ("security_pnl", pnl),
                                  ("security_pre", pre_values), ("security_end", end_values), ("net_trades", trades)):
                securities(row[field], values)
            if turnover_context is not None:
                securities(row["inner_target"], {a: value / (day.nav + day.fees) for a, value in end_values.items()})
            cash = row["virtual_cash_end"]
            if set(cash) != {spec.inner_residual_role, "outer_cash_leg"}:
                raise ValueError("NETTING_RECOVERY_BOOK_MISMATCH")
            close(cash[spec.inner_residual_role], day.cash)
            close(cash["outer_cash_leg"], 0.)
            close(row["outer_cash_pnl"], 0.)
            close(row["nav_pre_trade"], day.nav + day.fees)
            close(row["nav_pre_trade"], previous_cash + math.fsum(pre_values.values()))
            close(-math.fsum(trades.values()), day.trade_net_cashflow)
            # Check the recorded fee breakdown against the frozen rates and
            # independently reconciled security trades, without a cost replay.
            fees = {a: abs(value) * spec.bps_per_side[a] * spec.multiplier / 10_000 for a, value in trades.items()}
            securities(row["fee_by_security"], fees)
            close(math.fsum(fees.values()), day.fees)
            # Use the verified pre-fee mark to preserve the original floating
            # point gross return; its cash/fee/NAV identity was checked above.
            gross.append(row["nav_pre_trade"] / previous_nav - 1.)
            if row["gross_return"] != gross[-1]:
                raise ValueError("NETTING_RECOVERY_BOOK_MISMATCH")
            previous_nav, previous_cash, previous_values = day.nav, day.cash, end_values
        if net["gross_returns"] != gross:
            raise ValueError("NETTING_RECOVERY_BOOK_MISMATCH")
        # Rebuild only B3 statistics from the verified recorded observations.
        # Default missing trial evidence and all None/UNCOMPUTABLE states stay
        # authoritative; receipt-local digests cannot attest report semantics.
        report = build_unified_net_report(net_returns, gross_returns=gross, periods_per_year=periods,
                                          **report_options).to_dict()
        if report_options and any(net["unified_net_report_basis"].get(k) != v
                                  for k, v in report_options.items()):
            raise ValueError("NETTING_RECOVERY_REPORT_BASIS_MISMATCH")
        if _candidate_digest(net["unified_net_report"]) != _candidate_digest(report):
            raise ValueError("NETTING_RECOVERY_REPORT_MISMATCH")
        verify_cost_companion(net)

    def read_success(reopened: Any, terminal: Any) -> dict:
        if (terminal is None or terminal.status is not ResearchTrialStatus.SUCCEEDED
                or terminal.run_id != request["run_id"] or terminal.param_version != 1
                or terminal.actual_params != params
                or replace(terminal, status=ResearchTrialStatus.STARTED, actual_params=None,
                           run_id=None, param_version=None, reason_code="") != started):
            raise ValueError("NETTING_CANDIDATE_IDENTITY_MISMATCH")
        # QPK permits a terminal without a started object; this caller requires
        # the immutable pair, including after a partial/corrupt local recovery.
        saved_start = root / reopened._research_key(started.domain, started.strategy_profile, started.trial_id, "started")
        if not saved_start.is_file():
            raise ValueError("NETTING_STARTED_MISSING")
        ledger = store_call(reopened.load_research_ledger,started.domain, started.strategy_profile, started.trial_id,
                                               request["run_id"], 1)
        backtest = store_call(reopened.load_backtest_by_run_id,started.domain, started.strategy_profile,
                                                    request["run_id"], param_version=1)
        if ledger is None or backtest is None:
            raise ValueError("NETTING_CANDIDATE_PAIR_MISSING")
        if (ledger.initial_nav != initial_nav or ledger.initial_cash != initial_nav or ledger.initial_positions
                or [d.session_date for d in ledger.days] != list(dates[te0:te1])):
            raise ValueError("NETTING_LEDGER_INITIAL_OR_SESSION_MISMATCH")
        for i, day in enumerate(ledger.days, start=te0):
            if any(mark.symbol not in prices or mark.quantity < 0 or mark.valuation < 0
                   or not math.isclose(mark.quantity * prices[mark.symbol][i], mark.valuation,
                                       rel_tol=1e-12, abs_tol=1e-9) for mark in day.positions):
                raise ValueError("NETTING_POSITION_PRICE_MISMATCH")
        expected = _candidate_public_metrics([d.daily_return for d in ledger.days], periods,
                                             report_options=report_options)
        if backtest.to_dict() != candidate_result(ledger, expected).to_dict():
            raise ValueError("NETTING_PUBLIC_METRIC_MISMATCH")
        _check_netting_store(root)
        try:
            receipt = json.loads((root / receipt_key).read_text(encoding="utf-8"))
            body = {"payload": receipt["payload"], "record_digests": receipt["record_digests"]}
            payload = body["payload"]
            if (set(receipt) != {"payload", "record_digests", "sha256"}
                    or receipt["sha256"] != _candidate_digest(body)
                    or body["record_digests"] != record_digests(terminal)
                    or _candidate_digest({key: payload[key] for key in response}) != _candidate_digest(response)
                    or payload["status"] != "SUCCEEDED"
                    or payload["candidate"] != backtest.to_dict() or payload["trial"] != terminal.to_dict()
                    or payload["research_ledger"] != ledger.to_dict()
                    or payload["acceptance_binding"] != acceptance_binding(payload)):
                raise ValueError("NETTING_RECEIPT_MISMATCH")
            verify_recovered_book(payload["netting"], ledger)
        except (OSError, KeyError, TypeError) as exc:
            raise ValueError("NETTING_RECEIPT_MISSING_OR_INCOMPLETE") from exc
        # The current request/envelope owns research boundaries, never the receipt.
        # This read-time label is outside the immutable legacy payload/digests.
        # It discloses the old assumption without rewriting historical evidence.
        return {**payload, **json.loads(json.dumps(response, allow_nan=False)),
                "report_basis_interpretation": (report_options["sharpe_basis"] if report_options
                                                else "legacy_implicit_rf0_assumption_not_v2_evidence")}

    try:
        previous = store_call(store.load_research_trial,started.domain, started.strategy_profile, started.trial_id)
        if previous is not None:
            if replace(previous, status=ResearchTrialStatus.STARTED, actual_params=None,
                       run_id=None, param_version=None, reason_code="") != started:
                raise ValueError("NETTING_CANDIDATE_IDENTITY_MISMATCH")
            if previous.status is ResearchTrialStatus.SUCCEEDED:
                return {**read_success(store, previous), "recovered": True}
            return {**response, "status": ("UNKNOWN" if previous.status is ResearchTrialStatus.STARTED
                                           else previous.status.value.upper()), "reason_code": "EXISTING_TRIAL_NO_REPLAY"}
        # Dedicated temp root per candidate. A missing/corrupt record is not a
        # fresh attempt; never overwrite it or hide it behind a new key.
        if root.exists() and any(root.rglob("*.json")):
            raise ValueError("NETTING_STORE_INCOMPLETE_OR_CORRUPT")
        store_call(store.save_research_trial, started)
        if store_call(store.load_research_trial, started.domain, started.strategy_profile, started.trial_id) != started:
            return {**response, "status": "UNKNOWN", "reason_code": "STARTED_RECEIPT_UNCERTAIN"}
    except ValueError as exc:
        return {**response, "status": "REJECTED", "reason_code": str(exc)}
    except Exception:
        return {**response, "status": "UNKNOWN", "reason_code": "STORE_RECEIPT_UNCERTAIN"}

    try:
        fit = fit_portfolio_kelly(assets, {a: rets[a][tr0:tr1] for a in assets}, cash[tr0:tr1], cs, ms)
        weights = fit["weights"][format(cs[0], "g")][ms[0]]
        schedule = [dict(weights) for _ in range(te1 - te0)]
        if request["end_target"] is not None:
            schedule[-1] = dict(request["end_target"])
        if budget_config is not None and turnover_context is None:
            for i, target in enumerate(schedule):
                assessed = assess_portfolio_risk_budget(
                    target_weights={**target, policy.cash_symbol: max(0., 1. - sum(target.values()))},
                    asset_risk_specs=risk_specs, policy=policy)
                if assessed["status"] not in ("APPROVE", "REDUCE"):
                    raise ValueError("NETTING_RISK_BUDGET_PARKED:" + ";".join(assessed["reason_codes"]))
                # Follow the assessor's recommendation, including APPROVE's
                # epsilon filtering. The runner owns the same virtual residual.
                schedule[i] = {a: w for a, w in assessed["recommended_target_weights"].items()
                               if a != policy.cash_symbol}
        sessions = [NettingSession(d, prior, 1., target, {a: rets[a][i] for a in assets}, 0.)
                    for i, ((d, prior), target) in enumerate(zip(decisions, schedule), start=te0)]
        net = run_offline_security_netting(
            sessions, initial_nav=initial_nav, initial_f=1., initial_inner_weights={}, spec=spec,
            fee_cash_role=request["fee_cash_role"], execution_timing="next_close", report_periods_per_year=periods,
            **({"report_sharpe_basis": report_options["sharpe_basis"],
                "report_risk_free_returns": report_options["risk_free_returns"],
                "report_risk_free_source": report_options["risk_free_source"],
                "report_calendar_id": report_options["calendar_id"]} if report_options else {}),
            **({"rs06_risk_budget": turnover_context} if turnover_context is not None else {}),
            **({"research_cost_inputs": json.loads(json.dumps(cost_options, allow_nan=False))}
               if cost_options is not None else {}))
        if cost_options is not None:
            net["research_cost_binding"] = cost_binding(net)
            verify_cost_companion(net)
        # Verify report and ledger identity before mapping public metrics. No
        # virtual cost_stress and no second fee subtraction on this branch.
        from quant_platform_kit.research_stats.unified_report import build_unified_net_report
        expected_report = build_unified_net_report(net["net_returns"], gross_returns=net["gross_returns"],
                                                    periods_per_year=periods, **report_options).to_dict()
        if (net["unified_net_report"] != expected_report
                or net["unified_net_report_basis"]["sessions"] != [d.isoformat() for d, _ in decisions]
                or [r["net_return"] for r in net["ledger"]] != net["net_returns"]
                or [r["gross_return"] for r in net["ledger"]] != net["gross_returns"]
                or len(net["ledger"]) != len(decisions)
                or [(r["session"], r["decision_session"]) for r in net["ledger"]]
                != [(d.isoformat(), prior.isoformat()) for d, prior in decisions]
                or net["initial_nav"] != initial_nav or net["ledger"][0]["nav_start"] != initial_nav
                or net["final_nav"] != net["ledger"][-1]["nav_end"]):
            raise ValueError("NETTING_REPORT_LEDGER_MISMATCH")
        days = []
        for i, row in enumerate(net["ledger"], start=te0):
            days.append(ResearchLedgerDay(
                session_date=dates[i], cash=math.fsum(row["virtual_cash_end"].values()),
                positions=tuple(ResearchPositionMark(a, value / prices[a][i], value)
                                for a, value in row["security_end"].items() if value != 0.),
                trade_net_cashflow=-math.fsum(row["net_trades"].values()), fees=row["fees"],
                nav=row["nav_end"], daily_return=row["net_return"]))
        ledger = ResearchDailyLedger(
            trial_id=started.trial_id, domain=started.domain, strategy_profile=started.strategy_profile,
            run_id=request["run_id"], param_version=1, input_id=input_sha, calendar_id=started.calendar_id,
            periods_per_year=periods, cost_source=started.cost_source, cost_inputs=cost_inputs,
            initial_session_date=dates[te0 - 1], initial_nav=initial_nav, initial_cash=initial_nav,
            initial_positions=(), days=tuple(days), synthetic=True)
        public = _candidate_public_metrics(net["net_returns"], periods, report=net["unified_net_report"])
        backtest = candidate_result(ledger, public)
        terminal = replace(started, status=ResearchTrialStatus.SUCCEEDED, actual_params=params,
                           run_id=request["run_id"], param_version=1)
    except ValueError as exc:
        reason = (str(exc) if budget_config is not None and str(exc).startswith("NETTING_RISK_BUDGET_")
                  else "NETTING_INPUT_OR_LEDGER_REJECTED")
        try:
            store_call(store.save_research_trial, replace(started, status=ResearchTrialStatus.REJECTED,
                                              reason_code="netting_input_or_ledger_rejected"))
        except Exception:
            return {**response, "status": "UNKNOWN", "reason_code": "REJECTION_RECEIPT_UNCERTAIN"}
        return {**response, "status": "REJECTED", "reason_code": reason}
    payload = {**response, "status": "SUCCEEDED", "candidate": backtest.to_dict(),
               "trial": terminal.to_dict(), "research_ledger": ledger.to_dict(), "netting": net,
               "readback": "REOPENED_EXACT_TRIAL_RUN_INPUT_CONFIG_SOURCE_COST_CALENDAR_PERIODS",
               "folds": [{"train_start": dates[tr0].isoformat(), "train_end": dates[tr1 - 1].isoformat(),
                          "test_start": dates[te0].isoformat(), "test_end": dates[te1 - 1].isoformat(),
                          "portfolio_fit": fit}]}
    payload["acceptance_binding"] = acceptance_binding(payload)
    try:
        # Store and return the same JSON representation, including None/statuses.
        payload = json.loads(json.dumps(payload, allow_nan=False))
        store_call(store.save_backtest_result, backtest)
        store_call(store.save_research_ledger, ledger)
        body = {"payload": payload, "record_digests": record_digests(terminal, before_terminal=True)}
        # Existing QPK exclusive-create/fsync primitive; one local immutable
        # receipt in the same trial. No report in params or QPK schema change.
        store_call(store._create_same, receipt_key, {**body, "sha256": _candidate_digest(body)})
        # Receipt first: lost receipt ack leaves STARTED/UNKNOWN. Lost terminal
        # ack can recover only after the full receipt + all records match.
        store_call(store.save_research_trial, terminal)
        reopened = PerformanceStore(local_root=root, cloud_bucket="")
        observed = store_call(reopened.load_research_trial, started.domain, started.strategy_profile, started.trial_id,
                              run_id=request["run_id"], param_version=1)
        return read_success(reopened, observed)
    except Exception:
        return {**response, "status": "UNKNOWN", "reason_code": "STORE_RECEIPT_UNCERTAIN"}


def _candidate_public_metrics(net_returns: Sequence[float], periods: int, *, report: dict | None = None,
                              report_options: Mapping[str, Any] | None = None) -> dict:
    from quant_platform_kit.research_stats.unified_report import build_unified_net_report

    if report is None:
        report = build_unified_net_report(net_returns, periods_per_year=periods, **(report_options or {})).to_dict()
    metrics = {m["name"]: m for m in report["metrics"]}

    def value(name: str) -> float | None:
        metric = metrics[name]
        return metric["value"] if metric["status"] == STATUS_COMPUTED else None

    sr = value("sharpe_per_period")
    # Preserve lifecycle's signed negative drawdown and QPK's undefined metrics.
    # The report Sharpe is per session; BacktestResult Sharpe is annualized.
    dd = value("max_drawdown")
    return {"cagr": value("net_cagr"), "calmar_ratio": value("net_calmar_ratio"),
            "max_drawdown": dd,
            "sharpe_ratio": None if sr is None else sr * math.sqrt(periods)}


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
