"""Reconcile existing paths on aligned synthetic units, never fit an optimizer.

Each RS06 strategy is one security here, with no internal turnover, common
fractional target weights and zero-return free residual cash. A close target
earns the following session in the ledger; RS06 applies that same target at
the start of the following period. The funding and final cash sessions are
real fixture observations in both paths, not an initial-NAV placeholder.
"""
import importlib.util
import math
import os
from datetime import date

import pytest

from us_equity_strategies.research.portfolio_kelly_walk_forward import portfolio_path
from us_equity_strategies.research.security_netting_runner import NettingSession, run_offline_security_netting

try:
    AVAILABLE = importlib.util.find_spec("quant_platform_kit.research_stats.security_netting") is not None
except ModuleNotFoundError:
    AVAILABLE = False
pytestmark = pytest.mark.skipif(
    not AVAILABLE and os.environ.get("UES_REQUIRE_SECURITY_NETTING") != "1",
    reason="production QPK pin lacks research-only security netting",
)


def _paths(bps):
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec

    sessions = (date(2026, 1, 30), date(2026, 2, 2), date(2026, 2, 3),
                date(2026, 2, 4), date(2026, 2, 5))
    # Fixed fixture targets, not Kelly fits, signal caps or trade quantities.
    earning_targets = [{}, {"A": .4, "B": .2}, {"A": .15, "B": .35}, {"A": .3, "B": .25}, {}]
    close_targets = earning_targets[1:] + [{}]
    returns = {"A": [0., .1, -.2, .03, 0.], "B": [0., -.04, .02, -.02, 0.]}
    rows = [NettingSession(day, sessions[i - 1] if i else date(2026, 1, 29),
                          1., close_targets[i], {s: r[i] for s, r in returns.items()}, 0.)
            for i, day in enumerate(sessions)]
    ledger = run_offline_security_netting(
        rows, initial_nav=100., initial_f=1., initial_inner_weights={},
        spec=SecurityNettingSpec(bps_per_side={"A": bps, "B": bps}),
        fee_cash_role="inner_residual_cash", execution_timing="next_close", report_periods_per_year=5)
    gross, turnover = portfolio_path(earning_targets, returns, [0.] * len(sessions), None)
    return ledger, gross, turnover, earning_targets


def test_rs06_zero_cost_aligned_targets_match_net_ledger_and_report():
    from quant_platform_kit.research_stats.unified_report import build_unified_net_report

    ledger, gross, turnover, earning_targets = _paths(0.)
    assert gross == pytest.approx([0., .032, -.023, .004, 0.])
    assert ledger["gross_returns"] == pytest.approx(gross)
    assert ledger["net_returns"] == pytest.approx(gross)
    # Align physical fill boundaries, not same-index turnover labels.
    assert [r["net_security_turnover"] for r in ledger["ledger"][:-1]] == pytest.approx(turnover[1:])
    assert turnover[0] == ledger["ledger"][-1]["net_security_turnover"] == 0.
    rs_report = build_unified_net_report(gross, periods_per_year=5)
    measured = {m["name"]: m for m in ledger["unified_net_report"]["metrics"]}
    assert rs_report.observations == ledger["unified_net_report"]["observations"] == 5
    for name in ("net_log_growth_per_period", "net_cagr", "max_drawdown", "net_calmar_ratio"):
        assert measured[name]["value"] == pytest.approx(rs_report.metric(name).value)
    assert ledger["final_nav"] == pytest.approx(100. * math.prod(1. + r for r in gross))
    for i, row in enumerate(ledger["ledger"]):
        assert row["fees"] == 0.
        assert row["f_target"] == 1.  # Sleeve allocation is not risky-asset exposure.
        assert sum(row["inner_target"].values()) < row["f_target"]
        if i:
            assert ledger["ledger"][i - 1]["inner_target"] == earning_targets[i]


def test_rs06_cost_model_difference_reconciles_actual_cash_and_single_fee_deduction():
    from quant_platform_kit.research_stats import cost_stress_recompute
    from quant_platform_kit.research_stats.unified_report import build_unified_net_report

    bps = 100.  # Synthetic common rate, not a production fee policy.
    ledger, gross, turnover, _ = _paths(bps)
    stressed = cost_stress_recompute(gross, turnover, cost_bps_per_side=bps, multipliers=(1.,))
    assert stressed.status == "COMPUTED"
    rs_net = stressed.scenarios[0].net_returns
    rate = bps / 10_000.
    for i, row in enumerate(ledger["ledger"]):
        # RS06 applies a multiplicative haircut to virtual-strategy gross
        # returns; its subsequent weights/turnover still use the gross path.
        assert rs_net[i] == pytest.approx((1. - turnover[i] * rate) * (1. + gross[i]) - 1.)
        # The actual ledger pays each net security fill from cash, once.
        assert row["fees"] == pytest.approx(math.fsum(abs(v) for v in row["net_trades"].values()) * rate)
        assert row["net_return"] == pytest.approx(row["gross_return"] - row["fees"] / row["nav_start"])
        assert row["nav_end"] == pytest.approx(row["nav_pre_trade"] - row["fees"])
        assert row["nav_end"] == pytest.approx(sum(row["security_end"].values()) + sum(row["virtual_cash_end"].values()))
        pre_cash = row["nav_pre_trade"] - sum(row["security_pre"].values())
        assert sum(row["virtual_cash_end"].values()) == pytest.approx(pre_cash - sum(row["net_trades"].values()) - row["fees"])
        assert min(row["virtual_cash_end"].values()) >= 0.
        assert max(abs(v) for v in row["reconciliation_errors"].values()) < 1e-10
        for symbol in set(row["security_pre"]) | set(row["security_end"]):
            assert row["security_end"].get(symbol, 0.) == pytest.approx(
                row["security_pre"].get(symbol, 0.) + row["net_trades"].get(symbol, 0.))
    # Entry is charged at the first close in the ledger, at the next start in
    # RS06. Cash-funded ledger holdings keep their full security notional.
    entry_fee = ledger["ledger"][0]["fees"]
    assert ledger["net_returns"][0] == pytest.approx(-entry_fee / 100.)
    assert rs_net[0] == 0.
    rs_after_first_return = 100. * math.prod(1. + r for r in rs_net[:2])
    assert ledger["ledger"][1]["nav_pre_trade"] - rs_after_first_return == pytest.approx(entry_fee * gross[1])
    assert ledger["gross_returns"][1] != pytest.approx(gross[1])
    rs_report = build_unified_net_report(rs_net, periods_per_year=5)
    measured = {m["name"]: m for m in ledger["unified_net_report"]["metrics"]}
    assert measured["net_cagr"]["value"] == pytest.approx(ledger["final_nav"] / 100. - 1.)
    assert measured["net_cagr"]["value"] != pytest.approx(rs_report.metric("net_cagr").value)
    assert ledger["final_nav"] == pytest.approx(100. * math.prod(1. + r for r in ledger["net_returns"]))
    twice_charged = 100. * math.prod(1. + row["net_return"] - row["fees"] / row["nav_start"]
                                   for row in ledger["ledger"])
    assert ledger["final_nav"] != pytest.approx(twice_charged)
    assert ledger["gross_return_basis"] == "same_net_book_before_close_fee"
    assert ledger["no_order"] is True and ledger["live_ready"] is False
