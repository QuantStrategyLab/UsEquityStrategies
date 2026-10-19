"""Synthetic next-close mechanism candidate; not D03 or a strategy backtest.

Fixed inner core fixtures hold 90% TQQQ/SOXL, 8% BOXX, 2% zero-return
cash. They are caller-supplied CorePaths, NOT production core decisions.
Each is converted separately through #639. Only their explicit security
targets are combined, then budgeted, then sent to ONE cash-funded ledger.
The 0.5 nominal-risk budget, fees and capital are experiment fixtures, not
user risk policy. ETF returns already include leverage; 3x look-through is
reported as exposure only and is never multiplied into returns again.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import correlation, mean, stdev, variance

from us_equity_strategies.portfolio_risk_budget import (
    PortfolioAssetRiskSpec, PortfolioRiskBudgetPolicy, assess_portfolio_risk_budget,
    attribute_fixed_weight_return_risk,
)
from .b1_b2_rs04_preparation import ewma_variance_update
from .kelly_core_return_generator import CorePath
from .rs04_netting_candidate import (
    NEXT_CLOSE_RESEARCH_CANDIDATE, CloseTarget, InitialNettingBook, OuterExposure,
    build_rs04_netting_sessions, run_rs04_netting_candidate,
)
from .security_netting_runner import NettingSession, run_offline_security_netting

ROWS = ("fixed_tqqq_core", "fixed_soxl_core", "fixed_50_50", "fixed_50_50_ewma_reduce_only")
SCENARIOS = ("low_correlation", "high_correlation", "downturn_correlation_jump", "vol_gap_recovery")
FEES = {"zero": 0., "fixed": 5., "stress": 25.}  # bps per side, ALL securities incl BOXX
SYMBOLS = ("TQQQ", "SOXL", "BOXX")
CORE_TARGETS = {s: {s: .90, "BOXX": .08} for s in SYMBOLS[:2]}
PHASES = {
    "low_correlation": (("evaluation", 252, .1, .0007, .035),),
    "high_correlation": (("evaluation", 252, .9, .0007, .035),),
    "downturn_correlation_jump": (("before_drop", 126, .1, .0007, .035),
        ("drop", 63, .9, -.006, .035), ("rebound", 63, .3, .003, .035)),
    "vol_gap_recovery": (("calm", 63, .1, .0007, .02), ("vol_and_gap", 63, .8, -.001, .06),
        ("recovery", 63, .4, .003, .03), ("calm_again", 63, .1, .0007, .015)),
}


@dataclass(frozen=True)
class Fixture:
    seed: int = 20261014
    warmup: int = 63
    evaluation: int = 252
    initial_nav: float = 100_000.
    decay: float = .94
    annual_target_vol: float = .20
    periods_per_year: int = 252
    nominal_risk_budget: float = .5


def protocol() -> dict:
    return dict(fixture=asdict(Fixture()), candidates=ROWS, scenarios=SCENARIOS, fee_bps=FEES,
        core_targets=CORE_TARGETS, status="SYNTHETIC_NEXT_CLOSE_RESEARCH_CANDIDATE",
        generation=dict(phase_columns=("label", "observations", "rho", "mean", "sample_stdev"),
            warmup=("warmup", 63, .1, .0007, .035), evaluation_phases=PHASES,
            rng="stdlib random.Random.gauss, reset same seed for each scenario",
            marginal_control="Center and sample-standardize x, orthogonalize and standardize y; y=rho*x+sqrt(1-rho^2)*y",
            gap_override="vol_and_gap first two TQQQ returns=(-.18,.12), SOXL=(-.20,.14); actual moments reported",
            boxx_return=.0001),
        target_availability="Constant core fixtures known before all simulated observations",
        d09="NOT_FROZEN", d03="UNCHANGED_SEPARATE_MECHANISM_EXPERIMENT",
        execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE,
        timing="Observe completed close t; target filled at t+1 close AFTER old book earns; earns from t+2",
        opening="All cash, no free opening trades; first evaluated return is zero before entry fee",
        warmup="No holdings/trades; initialize variance from 63 prefix reference returns, ddof=1",
        ewma="Forecast at decision t uses reference returns through t only; update v=.94*v+.06*r^2",
        reference="Fixed budget portfolio: .25 TQQQ+.25 SOXL+.48 BOXX+.02 zero-return cash; no costs",
        source_wrapper_checks="Zero-fee single-core source conversion checks at f=.5/.9, never NAV inputs to combination",
        scaling="min(1, .20/sqrt(252*v)); risk removed to BOXX; cash target stays .02",
        budget="Nominal risky weight .5, equivalent look-through 1.5; decision TARGET cap only",
        fees="Fixed per-security bps per side; one net securities charge at fill, incl terminal fill",
        cash="Residual zero-return cash .02 pre-fee; BOXX one tradable asset, synthetic return .0001",
        calendar="Ordinal weekdays from 2025-01-02, not an exchange calendar; observations, not real dates",
        endpoint="Sum net log return and paired difference vs fixed_50_50 within same scenario/fee",
        recovery="Observation-index durations; initial NAV included; censor at evaluation end",
        cost_role="REPORT_SCENARIO_NOT_SELECTION_TRIAL", fitting=False, winner_required=False,
        financial_oos=False, dsr="UNCOMPUTABLE_WITHOUT_TRIAL_HISTORY", no_order=True,
        live_ready=False, budget_effect="NONE", cross_broker_transfers="NOT_MODELED_OR_AUTHORIZED")


def calendar(n: int) -> tuple[date, ...]:
    days = []
    day = date(2025, 1, 2)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def _pair(rng: random.Random, n: int, rho: float, mu: float, sigma: float):
    # Orthogonalize finite samples: low/high scenarios share exact marginal moments.
    x = [rng.gauss(0., 1.) for _ in range(n)]
    x = [(v - mean(x)) / stdev(x) for v in x]
    y = [rng.gauss(0., 1.) for _ in range(n)]
    y = [v - mean(y) for v in y]
    projection = math.fsum(a*b for a, b in zip(x, y)) / math.fsum(a*a for a in x)
    y = [b - projection*a for a, b in zip(x, y)]
    y = [v / stdev(y) for v in y]
    return ([mu + sigma*a for a in x],
            [mu + sigma*(rho*a + math.sqrt(1-rho*rho)*b) for a, b in zip(x, y)])


def synthetic_panel(scenario: str, fixture: Fixture = Fixture()) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError("SCENARIO_INVALID")
    rng = random.Random(fixture.seed)
    if fixture.warmup != 63 or fixture.evaluation != 252:
        raise ValueError("FIXED_SCENARIO_LENGTH_REQUIRED")
    phases = [("warmup", 63, .1, .0007, .035), *PHASES[scenario]]
    out = {s: [] for s in SYMBOLS}
    phase_report = []
    for label, n, rho, mu, sigma in phases:
        x, y = _pair(rng, n, rho, mu, sigma)
        if label == "vol_and_gap":
            x[0], y[0], x[1], y[1] = -.18, -.20, .12, .14
        start = len(out["TQQQ"])
        out["TQQQ"].extend(x); out["SOXL"].extend(y); out["BOXX"].extend([.0001]*n)
        phase_report.append(dict(label=label, start=start, stop=start+n, requested_correlation=rho,
            actual_correlation=correlation(x, y), tqqq_mean=mean(x), soxl_mean=mean(y),
            tqqq_stdev=stdev(x), soxl_stdev=stdev(y)))
    return dict(returns=out, phases=phase_report)


def fee_spec(bps: float):
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
    return SecurityNettingSpec(bps_per_side={s: bps for s in SYMBOLS},
        outer_cash_mode="merge_into_security", outer_cash_symbol="BOXX", cash_like_symbols=("BOXX",))


def core_inputs(symbol: str, returns: dict, days: tuple[date, ...], initial_nav: float) -> dict:
    """Explicit synthetic fixed core with cash opening and prior terminal decision."""
    target = CORE_TARGETS[symbol]
    n = len(days)-1
    weights = ({},) + (target,)*(n-1)
    series = {s: tuple(returns[s]) for s in (symbol, "BOXX")}
    gross = tuple(math.fsum(w*series[s][i] for s, w in weights[i].items()) for i in range(n))
    path = CorePath(symbol, "no_plugin_core", days[1:], gross, (0.,)*n, weights,
                    series, days[0])
    return dict(core_path=path, initial_book=InitialNettingBook(days[0], initial_nav, 1., {}, {}, initial_nav),
        last_close_target=CloseTarget(days[-2], days[-1], target), trading_sessions=days,
        outer_exposures=tuple(OuterExposure(a, b, 1.) for a, b in zip(days, days[1:])),
        synthetic_fee_spec=fee_spec(0.), execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE)


def budget_target(proposal: dict, fixture: Fixture = Fixture()) -> dict:
    specs = {s: PortfolioAssetRiskSpec(s, 3. if s != "BOXX" else 1.,
             s, is_cash=s == "BOXX") for s in SYMBOLS}
    result = assess_portfolio_risk_budget(target_weights=proposal, asset_risk_specs=specs,
        policy=PortfolioRiskBudgetPolicy("BOXX", 3*fixture.nominal_risk_budget,
            {"TQQQ": fixture.nominal_risk_budget, "SOXL": fixture.nominal_risk_budget, "BOXX": 1.},
            {"TQQQ": 3*fixture.nominal_risk_budget, "SOXL": 3*fixture.nominal_risk_budget}))
    if result["status"] not in ("APPROVE", "REDUCE"):
        raise ValueError("BUDGET_PARKED:" + str(result["reason_codes"]))
    return result


def combine_targets(left: dict, right: dict, allocations: tuple[float, float], scale: float = 1.,
                    fixture: Fixture = Fixture()) -> tuple[dict, dict]:
    if (len(allocations) != 2 or any(not math.isfinite(x) or x < 0 for x in allocations)
            or not math.isclose(sum(allocations), 1.) or not math.isfinite(scale) or not 0 <= scale <= 1):
        raise ValueError("COMBINATION_INVALID")
    proposal = {s: math.fsum(a*w.get(s, 0.) for a, w in zip(allocations, (left, right)))
                for s in SYMBOLS}
    assessed = budget_target(proposal, fixture)
    target = dict(assessed["recommended_target_weights"])
    for s in SYMBOLS[:2]:
        w = target.get(s, 0.)
        target[s] = w*scale
        target["BOXX"] = target.get("BOXX", 0.) + w*(1-scale)
    # Recheck after scaling; f=1 flattens ownership, never bypasses the cap.
    return dict(budget_target(target, fixture)["recommended_target_weights"]), assessed


def comparison_sessions(panel: dict, row_id: str, fixture: Fixture = Fixture()) -> tuple:
    if row_id not in ROWS:
        raise ValueError("ROW_INVALID")
    total = fixture.warmup+fixture.evaluation
    if set(panel) != set(SYMBOLS) or any(len(v) != total for v in panel.values()):
        raise ValueError("PANEL_ALIGNMENT")
    if any(not math.isfinite(r) or r <= -1. for v in panel.values() for r in v):
        raise ValueError("PANEL_RETURN_INVALID")
    all_days = calendar(total+1)
    days = all_days[fixture.warmup:]
    evaluated = {s: panel[s][fixture.warmup:] for s in SYMBOLS}
    source = []
    for s in SYMBOLS[:2]:
        args = core_inputs(s, evaluated, days, fixture.initial_nav)
        source.append(build_rs04_netting_sessions(**args))
    allocations = (1., 0.) if row_id == ROWS[0] else (0., 1.) if row_id == ROWS[1] else (.5, .5)
    reference = [.25*a+.25*b+.48*c for a, b, c in zip(*(panel[s] for s in SYMBOLS))]
    v = variance(reference[:fixture.warmup])
    rows, decisions = [], []
    for i, (left, right) in enumerate(zip(*source)):
        scale = (min(1., fixture.annual_target_vol/math.sqrt(fixture.periods_per_year*v))
                 if v > 0. else 0.) if row_id == ROWS[3] else 1.
        target, assessment = combine_targets(dict(left.inner_target), dict(right.inner_target), allocations, scale, fixture)
        rows.append(NettingSession(left.session, left.decision_session, 1., target,
                                   {s: evaluated[s][i] for s in SYMBOLS}, 0.))
        decisions.append(dict(decision_session=left.decision_session.isoformat(),
            fill_session=left.session.isoformat(), known_through_index=fixture.warmup+i-1,
            variance=v, scale=scale, target=target, budget_status=assessment["status"],
            target_nominal_risk=sum(target.get(s, 0.) for s in SYMBOLS[:2]),
            target_effective_risk=3*sum(target.get(s, 0.) for s in SYMBOLS[:2])))
        # Current fill-session return becomes available only AFTER this decision.
        v = ewma_variance_update(v, reference[fixture.warmup+i], fixture.decay)
    return tuple(rows), decisions


def run_candidate(panel: dict, row_id: str, bps: float, fixture: Fixture = Fixture()) -> dict:
    from quant_platform_kit.research_stats.unified_report import build_unified_net_report
    rows, decisions = comparison_sessions(panel, row_id, fixture)
    netting = run_offline_security_netting(rows, initial_nav=fixture.initial_nav, initial_f=1.,
        initial_inner_weights={}, spec=fee_spec(bps), fee_cash_role="inner_residual_cash")
    report = build_unified_net_report(netting["net_returns"], gross_returns=netting["gross_returns"],
        periods_per_year=fixture.periods_per_year, include_max_drawdown_recovery=True).to_dict()
    metrics = {m["name"]: m for m in report["metrics"]}
    recovery = metrics["max_drawdown_peak_to_recovery_observations"]
    trough_recovery = metrics["max_drawdown_trough_to_recovery_observations"]
    ledger = netting["ledger"]
    summary = dict(row=row_id, final_nav=netting["final_nav"],
        net_log_growth=math.fsum(math.log1p(r) for r in netting["net_returns"]),
        turnover=sum(r["net_security_turnover"] for r in ledger),
        traded_notional=sum(abs(v) for r in ledger for v in r["net_trades"].values()),
        fees=sum(r["fees"] for r in ledger), rejection_count=0,
        mean_cash_fraction=mean(sum(r["virtual_cash_end"].values())/r["nav_end"] for r in ledger),
        mean_boxx_fraction=mean(r["security_end"].get("BOXX", 0.)/r["nav_end"] for r in ledger),
        max_end_nominal_risk=max(sum(r["security_end"].get(s, 0.) for s in SYMBOLS[:2])/r["nav_end"] for r in ledger),
        max_pretrade_nominal_risk=max(sum(r["security_pre"].get(s, 0.) for s in SYMBOLS[:2])/r["nav_pre_trade"] for r in ledger),
        mean_scale=mean(d["scale"] for d in decisions),
        max_drawdown=metrics["max_drawdown"]["value"],
        peak_to_recovery=recovery["value"], trough_to_recovery=trough_recovery["value"],
        recovery_right_censored=recovery["details"]["right_censored"],
        peak_to_end_lower_bound=recovery["details"]["observed_peak_to_end_lower_bound"],
        trough_to_end_lower_bound=trough_recovery["details"]["observed_trough_to_end_lower_bound"],
        cost_log_drag=fixture.evaluation*metrics["cost_log_drag_per_period"]["value"],
        dsr=metrics["deflated_sharpe_ratio"]["value"],
        dsr_status=metrics["deflated_sharpe_ratio"]["status"])
    summary["max_end_effective_risk"] = 3*summary["max_end_nominal_risk"]
    return dict(summary=summary, decisions=decisions, netting=netting, unified_net_report=report)


def run_experiment() -> dict:
    fixture = Fixture()
    output = dict(protocol=protocol(), scenarios={}, comparisons=[])
    for scenario in SCENARIOS:
        panel = synthetic_panel(scenario, fixture)
        days = calendar(fixture.warmup+fixture.evaluation+1)[fixture.warmup:]
        evaluated = {s: r[fixture.warmup:] for s, r in panel["returns"].items()}
        # Real #639 wrapper calls prove the source conversion path; their NAVs
        # are never combined and are not comparator returns.
        source_checks = {}
        for s in SYMBOLS[:2]:
            args = core_inputs(s, evaluated, days, fixture.initial_nav)
            args["outer_exposures"] = tuple(OuterExposure(a, b, fixture.nominal_risk_budget/.9)
                                             for a, b in zip(days, days[1:]))
            source_book = run_rs04_netting_candidate(**args, fee_cash_role="inner_residual_cash")["netting"]
            source_checks[s] = dict(observations=len(source_book["ledger"]),
                max_target_nominal_risk=max(r["f_target"]*r["inner_target"].get(s, 0.)
                                            for r in source_book["ledger"]))
        sample_risk = attribute_fixed_weight_return_risk(weights={"TQQQ": .5, "SOXL": .5},
            asset_returns={s: [dict(date=d.isoformat(), simple_return=r) for d, r in zip(days[1:], evaluated[s])]
                           for s in SYMBOLS[:2]}, as_of=days[-1].isoformat(), quote_currency="USD", minimum_observations=2)
        entry = dict(panel=panel, source_wrapper_observations=source_checks,
                     fixed_weight_sample_diagnostic=sample_risk, fee_tiers={})
        for tier, bps in FEES.items():
            results = {r: run_candidate(panel["returns"], r, bps, fixture) for r in ROWS}
            base = results[ROWS[2]]["summary"]["net_log_growth"]
            for row_id, result in results.items():
                summary = result["summary"]
                summary.update(scenario=scenario, fee_tier=tier, bps_per_side=bps,
                               delta_log_growth_vs_fixed_50_50=summary["net_log_growth"]-base)
                output["comparisons"].append(dict(summary))
            entry["fee_tiers"][tier] = results
        output["scenarios"][scenario] = entry
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    # Persist all rules BEFORE any scenario generation or candidate computation.
    manifest = args.output/"protocol.json"
    with manifest.open("x") as handle:
        handle.write(json.dumps(protocol(), indent=2, allow_nan=False)+"\n")
    result = run_experiment()
    with (args.output/"results.json").open("x") as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
