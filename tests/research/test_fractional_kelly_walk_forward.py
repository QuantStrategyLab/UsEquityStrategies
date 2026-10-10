"""RS-04: fractional-Kelly walk-forward evaluator. Synthetic returns only."""
import importlib
import itertools
import json
import math
import os
import random
import socket
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import fractional_kelly_walk_forward as rs04


def _qpk_stats():
    try:
        module = importlib.import_module("quant_platform_kit.research_stats")
    except ImportError:
        return None
    return module if hasattr(module, "cost_stress_recompute") else None


REQUIRE_QPK_STATS = os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1"
requires_qpk_stats = pytest.mark.skipif(
    _qpk_stats() is None and not REQUIRE_QPK_STATS,
    reason="installed QPK lacks research_stats (runs in research-stats-integration CI job)")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _dates(n):
    start, out, d = date(2001, 1, 1), [], date(2001, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    assert out[0] >= start
    return out


def _synthetic(n=400, seed=7, drift=0.0008, vol=0.01):
    rng = random.Random(seed)
    days = _dates(n)
    strat = [(d, rng.gauss(drift, vol)) for d in days]
    bench = [(d, rng.gauss(drift / 2, vol / 2)) for d in days]
    return days, strat, bench


PLAN = rs04.WalkForwardPlan(train_sessions=120, test_sessions=40, purge_sessions=2)
CONTEXT = {
    "report_id": "rs04-synthetic", "candidate_id": "synthetic_strategy",
    "source_revision": "0" * 40, "created_at": "2026-10-10T00:00:00+08:00",
    "manifest_sha256": "a" * 64, "source": "synthetic_gaussian", "license": "synthetic",
    "signal_cutoff": "prior_close", "execution": "next_close",
    "benchmark_name": "synthetic_benchmark", "benchmark_rationale": "synthetic test only",
}


def _run(**overrides):
    _, strat, bench = _synthetic()
    kwargs = {"strategy_returns": strat, "benchmark_returns": bench, "plan": PLAN,
              "cost_bps_per_side": 5.0, "synthetic": True, "assume_zero_cash": True}
    kwargs.update(overrides)
    return rs04.evaluate_fractional_kelly_walk_forward(**kwargs)


def test_real_data_requires_approval():
    with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_APPROVAL"):
        _run(synthetic=False)


def test_full_kelly_and_leverage_refused():
    for bad in ((1.0,), (1.5,), (0.0,), (-0.25,), (0.25, 0.25)):
        with pytest.raises(ValueError, match="FULL_KELLY_NOT_ALLOWED"):
            _run(fractions=bad)


def test_plan_is_purged_and_contiguous():
    folds = PLAN.folds(400)
    assert folds[0] == (0, 120, 122, 162)
    for (a, b, c, d), (_, _, c2, _) in itertools.pairwise(folds):
        assert c - b == 2 and c2 == d
    assert all(d <= 400 for *_, d in folds)
    anchored = rs04.WalkForwardPlan(120, 40, 2, anchored=True).folds(400)
    assert all(f[0] == 0 for f in anchored)
    with pytest.raises(ValueError):
        rs04.WalkForwardPlan(120, 40, 0).folds(400)  # purge is mandatory


def test_fit_matches_hand_calculation_and_shrinks():
    rng = random.Random(1)
    sample = [rng.gauss(0.001, 0.01) for _ in range(200)]
    fit = rs04.fit_fractional_kelly(sample, [0.0] * 200, 0.5)
    n = len(sample)
    mu = sum(sample) / n
    var = sum((x - mu) ** 2 for x in sample) / (n - 1)
    t = mu / math.sqrt(var / n)
    lam = max(0.0, 1 - 1 / t ** 2)
    assert fit["status"] == "COMPUTED"
    assert fit["mu_shrunk"] == pytest.approx(lam * mu)
    assert fit["exposure"] == pytest.approx(min(1.0, max(0.0, 0.5 * lam * mu / var)))
    assert 0.0 <= fit["shrink"] < 1.0


def test_weak_or_negative_edge_goes_to_cash():
    rng = random.Random(3)
    noise = [rng.gauss(-0.0005, 0.01) for _ in range(200)]
    assert rs04.fit_fractional_kelly(noise, [0.0] * 200, 0.5)["exposure"] == 0.0


def test_parked_folds_hold_cash():
    assert rs04.fit_fractional_kelly([0.01] * 10, [0.0] * 10, 0.5)["reason_code"] == "INSUFFICIENT_TRAIN_OBSERVATIONS"
    one_sided = rs04.fit_fractional_kelly([0.01] * 50, [0.0] * 50, 0.5)
    assert one_sided["status"] == "PARKED" and one_sided["exposure"] == 0.0


def test_strong_edge_is_capped_at_one_no_leverage():
    rng = random.Random(5)
    strong = [rng.gauss(0.01, 0.005) for _ in range(200)]
    fit = rs04.fit_fractional_kelly(strong, [0.0] * 200, 0.5)
    assert fit["exposure"] == 1.0 and fit["capped_at_one"] is True


@requires_qpk_stats
def test_future_returns_do_not_change_fold_fit():
    _, strat, bench = _synthetic()
    base = _run(strategy_returns=strat, benchmark_returns=bench)
    perturbed = list(strat)
    for i in range(162, 400):  # everything after fold 0 training + purge
        perturbed[i] = (perturbed[i][0], -0.02)
    other = _run(strategy_returns=perturbed, benchmark_returns=bench)
    assert base["folds"][0]["fits"] == other["folds"][0]["fits"]


@requires_qpk_stats
def test_all_comparators_share_window_and_tiers():
    days, _, _ = _synthetic()
    r8 = [(d, 0.0003) for d in days]
    result = _run(r8_returns=r8)
    assert result["status"] == "COMPUTED"
    assert set(result["comparators"]) == {"kelly_c0.25", "kelly_c0.5", "full_exposure", "fixed_50pct", "benchmark", "r8"}
    n = result["test_window"]["sessions"]
    for name, comp in result["comparators"].items():
        assert set(comp["tiers"]) == {"1", "2", "3"}
        if name != "r8":
            assert all(t["observations"] == n for t in comp["tiers"].values())
    assert result["comparators"]["r8"]["tiers"]["1"]["cost_basis"] == "AS_SUPPLIED_NET"
    assert result["comparators"]["r8"]["tiers"]["2"]["reason_code"] == "R8_TURNOVER_NOT_SUPPLIED"


@requires_qpk_stats
def test_zero_cost_paths_match_hand_calculation():
    days, strat, _ = _synthetic()
    result = _run(cost_bps_per_side=0.0)
    window = slice(122, 122 + result["test_window"]["sessions"])
    s = [r for _, r in strat[window]]
    full = math.prod(1 + r for r in s) - 1
    half = math.prod(1 + 0.5 * r for r in s) - 1
    assert result["comparators"]["full_exposure"]["tiers"]["1"]["total_return"] == pytest.approx(full)
    assert result["comparators"]["fixed_50pct"]["tiers"]["1"]["total_return"] == pytest.approx(half)
    fs = []
    for fold in result["folds"]:
        start = days.index(date.fromisoformat(fold["test_start"]))
        end = days.index(date.fromisoformat(fold["test_end"])) + 1
        fs.extend([fold["fits"]["0.25"]["exposure"]] * (end - start))
    kelly = math.prod(1 + f * r for f, r in zip(fs, s)) - 1
    assert result["comparators"]["kelly_c0.25"]["tiers"]["1"]["total_return"] == pytest.approx(kelly)


@requires_qpk_stats
def test_costs_are_monotone_and_charge_rebalancing():
    result = _run(cost_bps_per_side=10.0, strategy_turnover=[0.1] * 400)
    for name in ("kelly_c0.25", "kelly_c0.5", "full_exposure", "fixed_50pct", "benchmark"):
        tiers = result["comparators"][name]["tiers"]
        g = [tiers[k]["log_growth_per_session"] for k in ("1", "2", "3")]
        assert g[0] >= g[1] >= g[2]
    assert result["comparators"]["fixed_50pct"]["tiers"]["1"]["turnover_total"] > 0.5  # drift rebalancing charged
    assert result["evaluation_contract"]["strategy_internal_costs"].startswith("STRESSED")


@requires_qpk_stats
def test_cap_note_never_raises_budget_and_output_is_deterministic():
    first, second = _run(), _run()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["budget_effect"] == "NONE" and first["leverage_allowed"] is False
    assert first["live_ready"] is False and first["no_order"] is True and first["promotion_eligible"] is False
    for note in first["kelly_cap_note"].values():
        assert note["usage"] == "MAY_ONLY_LOWER_OR_CAP_NEVER_RAISE_EXISTING_BUDGET"
        assert 0.0 <= note["latest_fold_exposure"] <= 1.0


@requires_qpk_stats
def test_backtest_report_v1_validates():
    result = _run(report_context=CONTEXT, bootstrap_seed=11)
    report = result["backtest_report"]
    assert report["schema_version"] == "qsl.backtest_report.v1"
    assert report["live_ready"] is False and report["no_order"] is True
    assert report["data"]["data_identity"] == "development" and report["status"] == "DEVELOPMENT_ONLY"
    assert len(report["walk_forward"]["folds"]) == len(result["folds"])
    assert report["trials"]["trial_log_sha256"] == result["trial_log_sha256"]
    assert "bootstrap_gross" in result["comparators"]["kelly_c0.5"]
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(report, _qpk_stats().load_backtest_report_schema())


def test_missing_research_stats_is_uncomputable_without_numbers(monkeypatch):
    monkeypatch.setattr(rs04, "_stats_module", lambda: None)
    result = _run(report_context=CONTEXT)
    assert result["status"] == "UNCOMPUTABLE" and result["reason_code"] == rs04.UNAVAILABLE
    assert "comparators" not in result and "backtest_report" not in result and "kelly_cap_note" not in result


def test_input_contract_errors():
    days, strat, bench = _synthetic()
    with pytest.raises(ValueError, match="SESSIONS_NOT_ALIGNED"):
        _run(benchmark_returns=bench[1:])
    with pytest.raises(ValueError, match="CASH_RETURNS_REQUIRED_OR_DECLARE_ZERO"):
        _run(assume_zero_cash=False)
    with pytest.raises(ValueError, match="STRATEGY_RETURNS_INVALID"):
        _run(strategy_returns=[(days[0], -1.0)] + strat[1:])
    with pytest.raises(ValueError, match="R8_TURNOVER_WITHOUT_R8_RETURNS"):
        _run(r8_turnover=[0.0] * 400)
