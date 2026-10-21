"""RS-06 portfolio fractional-Kelly evaluator + private runner. Synthetic data only."""
import dataclasses
import hashlib
import importlib
import inspect
import itertools
import json
import math
import os
import random
import socket
import stat
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import fractional_kelly_preregistration as single
from us_equity_strategies.research import fractional_kelly_walk_forward as rs04
from us_equity_strategies.research import kelly_core_return_generator as gen
from us_equity_strategies.research import portfolio_kelly_preregistration as pk
from us_equity_strategies.research import portfolio_kelly_private_runner as runner
from us_equity_strategies.research import portfolio_kelly_walk_forward as pw
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan

REQUIRE_QPK_STATS = os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1"


def _netting_report_basis_supported():
    try:
        importlib.import_module("quant_platform_kit.research_stats.security_netting")
        report = importlib.import_module("quant_platform_kit.research_stats.unified_report")
    except ImportError:
        if REQUIRE_QPK_STATS:
            pytest.fail("required research overlay lacks security netting/report capability")
        pytest.skip("production QPK pin lacks research netting/report; this capability is not covered")
    supported = "sharpe_basis" in inspect.signature(report.build_unified_net_report).parameters
    if REQUIRE_QPK_STATS and not supported:
        pytest.fail("required research overlay lacks explicit Sharpe basis; do not skip v2 acceptance")
    return supported


@pytest.mark.parametrize("rf", [None, .001])
def test_explicit_report_basis_candidate_generation_and_recovery(tmp_path, monkeypatch, rf):
    supported = _netting_report_basis_supported()
    from test_portfolio_netting_candidate_closed_loop import fixture_args
    from us_equity_strategies.research import security_netting_runner as netting
    args = fixture_args(tmp_path, test_sessions=35)
    request = args["netting_candidate"]
    request["sharpe_basis"] = "net_wealth_excess_sharpe_v2"
    if rf is not None:
        request["risk_free_returns"] = [rf] * 35
        request["risk_free_source"] = "synthetic-period-rf"
    if not supported:
        with pytest.raises(ValueError, match="QPK_EXPLICIT_SHARPE_BASIS_REQUIRED"):
            pw.evaluate_portfolio_kelly_walk_forward(**args)
        return  # legacy-QPK rejection evidence only, never v2 acceptance
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first["status"] == "SUCCEEDED"
    report = first["netting"]["unified_net_report"]
    metrics = {m["name"]: m for m in report["metrics"]}
    if rf is None:
        assert metrics["sharpe_per_period"]["value"] is None
        assert first["candidate"]["sharpe_ratio"] is None
    else:
        from statistics import stdev
        excess = [r - rf for r in first["netting"]["net_returns"]]
        assert metrics["sharpe_per_period"]["value"] == pytest.approx(sum(excess)/len(excess)/stdev(excess))
    assert metrics["net_cagr"]["status"] == "COMPUTED"

    def no_replay(*args, **kwargs):
        pytest.fail("recovery must not replay fills or charge fees")
    monkeypatch.setattr(netting, "run_offline_security_netting", no_replay)
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert recovered["status"] == "SUCCEEDED"
    assert recovered["netting"] == first["netting"]
    assert recovered["candidate"] == first["candidate"]
    # Changing basis must not reinterpret the immutable receipt as a new study.
    request["risk_free_returns"] = [.002] * 35
    request["risk_free_source"] = "different-synthetic-rf"
    assert pw.evaluate_portfolio_kelly_walk_forward(**args)["status"] != "SUCCEEDED"


def test_legacy_receipt_stays_legacy_and_cannot_be_upgraded_in_place(tmp_path, monkeypatch):
    supported = _netting_report_basis_supported()
    from test_portfolio_netting_candidate_closed_loop import fixture_args
    from us_equity_strategies.research import security_netting_runner as netting
    args = fixture_args(tmp_path, test_sessions=35)
    first = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert first["status"] == "SUCCEEDED"
    root = args["netting_candidate"]["local_root"]
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*.json")}

    def no_replay(*args, **kwargs):
        pytest.fail("legacy recovery must not replay fees or re-fit")
    monkeypatch.setattr(netting, "run_offline_security_netting", no_replay)
    recovered = pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert recovered["status"] == "SUCCEEDED"
    assert recovered["netting"] == first["netting"]
    assert "report_basis" not in recovered["binding"]  # absent means legacy rf=0 assumption
    assert recovered["report_basis_interpretation"] == "legacy_implicit_rf0_assumption_not_v2_evidence"
    args["netting_candidate"]["sharpe_basis"] = "net_wealth_excess_sharpe_v2"
    if supported:
        assert pw.evaluate_portfolio_kelly_walk_forward(**args)["status"] == "REJECTED"
    else:
        with pytest.raises(ValueError, match="QPK_EXPLICIT_SHARPE_BASIS_REQUIRED"):
            pw.evaluate_portfolio_kelly_walk_forward(**args)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*.json")} == before


def _has_stats():
    try:
        return hasattr(importlib.import_module("quant_platform_kit.research_stats"), "cost_stress_recompute")
    except ImportError:
        return False


requires_qpk_stats = pytest.mark.skipif(not _has_stats() and not REQUIRE_QPK_STATS,
                                        reason="installed QPK lacks research_stats")
PLAN = WalkForwardPlan(train_sessions=120, test_sessions=40, purge_sessions=3)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _days(n, start=date(2001, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _streams(n=500, seed=7, rho=0.6):
    rng = random.Random(seed)
    days = _days(n)
    a, b, cash, ba, bb = [], [], [], [], []
    for _ in days:
        z1, z2 = rng.gauss(0, 1), rng.gauss(0, 1)
        a.append(0.0012 + 0.03 * z1)
        b.append(0.0008 + 0.02 * (rho * z1 + math.sqrt(1 - rho * rho) * z2))
        cash.append(0.00015)
        ba.append(0.0005 + 0.012 * z1)
        bb.append(0.0004 + 0.009 * z2)
    z = lambda v: list(zip(days, v, strict=True))  # noqa: E731
    return days, {"assets": [("A", z(a)), ("B", z(b))], "cash": z(cash),
                  "bench": [("IA", z(ba)), ("IB", z(bb))],
                  "turnover": {"A": [0.02] * n, "B": [0.01] * n}}


def _np_ledoit_wolf(cols):
    np = pytest.importorskip("numpy")
    x = np.array(cols, dtype=float).T
    x = x - x.mean(axis=0)
    n, p = x.shape
    emp = x.T @ x / n
    mu = np.trace(emp) / p
    x2 = x ** 2
    beta_ = (x2.T @ x2).sum()
    delta_ = ((x.T @ x) ** 2).sum() / n ** 2
    beta = 1.0 / (p * n) * (beta_ / n - delta_)
    delta = (delta_ - 2.0 * mu * np.trace(emp) + p * mu ** 2) / p
    s = 0.0 if min(beta, delta) == 0 else min(beta, delta) / delta
    return (1 - s) * emp + s * mu * np.eye(p), s


def test_ledoit_wolf_matches_reference_formula():
    rng = random.Random(3)
    cols = [[rng.gauss(0.001, 0.02) for _ in range(80)] for _ in range(3)]
    cols[1] = [0.5 * x + rng.gauss(0, 0.01) for x in cols[0]]
    ours, s = pw.ledoit_wolf(cols)
    ref, s_ref = _np_ledoit_wolf(cols)
    assert 0.0 <= s <= 1.0 and s == pytest.approx(s_ref, rel=1e-9)
    for i, j in itertools.product(range(3), repeat=2):
        assert ours[i][j] == pytest.approx(float(ref[i][j]), rel=1e-9, abs=1e-15)


def test_ledoit_wolf_matches_sklearn_when_installed():
    cov_mod = pytest.importorskip("sklearn.covariance")
    np = pytest.importorskip("numpy")
    rng = random.Random(4)
    cols = [[rng.gauss(0, 0.02) for _ in range(60)] for _ in range(2)]
    ours, s = pw.ledoit_wolf(cols)
    ref, s_ref = cov_mod.ledoit_wolf(np.array(cols).T)
    assert s == pytest.approx(s_ref, rel=1e-9)
    assert ours[0][1] == pytest.approx(ref[0][1], rel=1e-9)


def _brute(mu, cov, c, step=0.0025):
    best, arg = -1e18, None
    k = round(1 / step)
    for i in range(k + 1):
        for j in range(k + 1 - i):
            w = (i * step, j * step)
            v = pw._objective(w, mu, cov, c)
            if v > best:
                best, arg = v, w
    return arg, best


@pytest.mark.parametrize("seed", range(12))
def test_qp_matches_brute_force_on_random_problems(seed):
    rng = random.Random(seed)
    s1, s2, rho = rng.uniform(0.01, 0.04), rng.uniform(0.01, 0.04), rng.uniform(-0.8, 0.95)
    cov = [[s1 * s1, rho * s1 * s2], [rho * s1 * s2, s2 * s2]]
    mu = [rng.uniform(-0.001, 0.003), rng.uniform(-0.001, 0.003)]
    c = rng.choice([0.25, 0.5])
    w = pw.constrained_kelly_qp(mu, cov, c)
    assert w is not None and all(x >= 0 for x in w) and sum(w) <= 1 + 1e-9
    _, best = _brute(mu, cov, c)
    assert pw._objective(w, mu, cov, c) >= best - 1e-9


def test_qp_interior_equals_fractional_kelly_and_clip_scale_rules():
    cov = [[0.0004, 0.0001], [0.0001, 0.0009]]
    mu = [0.00004, 0.00003]
    full = pw._solve(cov, mu)
    w = pw.constrained_kelly_qp(mu, cov, 0.25)
    assert w == pytest.approx([0.25 * f for f in full], rel=1e-9)
    assert pw.clip_scale([2.0, -1.0], 0.25) == [0.5, 0.0]
    assert pw.clip_scale([4.0, 4.0], 0.5) == [0.5, 0.5]
    assert sum(pw.clip_scale([10.0, 2.0], 0.5)) == pytest.approx(1.0)
    neg = pw.constrained_kelly_qp([-0.001, -0.002], cov, 0.5)
    assert neg == [0.0, 0.0]


def test_prediction_scaling_cash_bridge_keeps_predictive_and_excess_proxies_distinct():
    """B4 has no cash input; its target is not an excess-over-cash QP target.

    A deterministic synthetic cash return is an explicit assumption here, not
    a BOXX return or a source/PIT attestation. No production bridge is implied.
    """
    from test_bayesian_kelly import example, TRAINING
    from us_equity_strategies.research.bayesian_kelly import (
        b4_fractional_target, b4_predictive_moments,
    )
    from decimal import Decimal, localcontext

    moments = b4_predictive_moments(example())
    with localcontext() as context:
        context.prec = 60
        # Two observations .002,-.001, kappa0=1, sigma=.0004.
        p = Decimal('.0004') * Decimal(4) / Decimal(3)
        a = Decimal('.0002') + p / 2
        mean = a.exp() - 1
        covariance = (2 * a).exp() * (p.exp() - 1)
        second = covariance + mean * mean
        cash = Decimal('.001')
        excess_mean = mean - cash
        excess_second = second - 2 * cash * mean + cash * cash
        assert moments.predictive_cov_log[0][0] == pytest.approx(float(p))
        assert moments.mean_simple[0] == pytest.approx(float(mean))
        assert moments.second_raw_moment_simple[0] == pytest.approx(float(second))
        assert float(mean) != pytest.approx(moments.mean_log[0])
        assert excess_mean < 0 < mean
        assert excess_second != second  # shifting a raw moment changes it
        # Deterministic cash shifts the mean, not the covariance.
        for c in (.25, .5):
            target = b4_fractional_target(moments, c=c, scope_cap={"TQQQ": 1.},
                                         cap_source=TRAINING)
            expected = float(Decimal(str(c)) * mean / second)
            assert 0 < expected < .9  # slack cap: double-c cannot hide in clipping
            assert target.targets == pytest.approx((expected,))
            assert target.targets[0] != pytest.approx(c * expected)
            gross_cov_qp = pw.constrained_kelly_qp([float(mean)], [[float(covariance)]], c)
            assert gross_cov_qp == pytest.approx([float(Decimal(str(c)) * mean / covariance)])
            assert gross_cov_qp != pytest.approx(target.targets, rel=1e-6)
            excess_qp = pw.constrained_kelly_qp([float(excess_mean)], [[float(covariance)]], c)
            assert excess_qp == [0.0]
            assert target.no_order and not target.live_ready


def test_prediction_scaling_portfolio_fit_requires_explicit_simple_cash_basis():
    """An explicit synthetic cash shift is invariant; omitting it is not.

    Numeric arrays have no units/source DTO: this checks the existing numerical
    contract, not automatic detection of mislabeled log returns or missing PIT.
    """
    assets = ("A", "B")
    cash = [.001] * 64
    strategy = {"A": [.006 + x for x in (.01, -.01, .01, -.01)] * 16,
                "B": [.004 + x for x in (.015, .015, -.015, -.015)] * 16}
    excess = {a: [r - rf for r, rf in zip(strategy[a], cash)] for a in assets}
    original = pw.fit_portfolio_kelly(assets, strategy, cash, (.25, .5))
    shifted = pw.fit_portfolio_kelly(assets, excess, [0.] * 64, (.25, .5))
    omitted = pw.fit_portfolio_kelly(assets, strategy, [0.] * 64, (.25, .5))
    assert original["status"] == shifted["status"] == omitted["status"] == "COMPUTED"
    assert original["mu_excess"] == shifted["mu_excess"]
    assert original["mu_shrunk"] == shifted["mu_shrunk"]
    assert original["covariance_ledoit_wolf"] == shifted["covariance_ledoit_wolf"]
    assert original["weights"] == shifted["weights"]
    for a in assets:
        assert omitted["mu_excess"][a] - original["mu_excess"][a] == pytest.approx(.001)
    with pytest.raises(TypeError):
        pw.fit_portfolio_kelly(assets, strategy, None, (.25,))
    parked = pw.fit_portfolio_kelly(assets, strategy, [], (.25,))
    assert parked["status"] == "PARKED"
    assert parked["reason_code"] == "INSUFFICIENT_TRAIN_OBSERVATIONS"
    # A research PARKED zero schedule is not an executable liquidation instruction.
    assert not parked.get("execution_authorized", False)


def test_prediction_scaling_joint_covariance_is_not_full_raw_second_moment():
    """Bridge two predictive cores into the existing covariance QP, not B4 sizing.

    B4 exposes Q's diagonal for its per-core proxy, not a joint optimizer. The
    off-diagonal raw moment below is an independent expectation identity.
    """
    from decimal import Decimal as D, localcontext
    from test_bayesian_kelly import ANCHOR, TRAINING, observation
    from us_equity_strategies.research.bayesian_kelly import (
        B4Spec, batch_b4, b4_predictive_moments,
    )
    spec = B4Spec(("TQQQ", "SOXL"), ANCHOR)
    obs = [observation(1, (.002, .001), spec=spec),
           observation(2, (-.001, .002), spec=spec)]
    moments = b4_predictive_moments(batch_b4(spec,
        ((.0004, .00012), (.00012, .0009)), TRAINING, obs,
        decision_cutoff=obs[-1].available_at))
    with localcontext() as context:
        context.prec = 60
        sigma = ((D('.0004'), D('.00012')), (D('.00012'), D('.0009')))
        p = [[x * 4 / 3 for x in row] for row in sigma]
        a = [D('.0002') + p[0][0] / 2, D('.0007') + p[1][1] / 2]
        mean = [x.exp() - 1 for x in a]
        cov = [[(a[i] + a[j]).exp() * (p[i][j].exp() - 1)
                for j in range(2)] for i in range(2)]
        raw = [[cov[i][j] + mean[i] * mean[j] for j in range(2)] for i in range(2)]
        # Closed-form interior FOC for a positive-definite two-dimensional C.
        determinant = cov[0][0] * cov[1][1] - cov[0][1] ** 2
        expected = [float(D('.25') * (cov[1][1] * mean[0] - cov[0][1] * mean[1]) / determinant),
                    float(D('.25') * (cov[0][0] * mean[1] - cov[0][1] * mean[0]) / determinant)]
    assert all(x > 0 for x in expected) and sum(expected) < .9
    for i in range(2):
        assert moments.predictive_cov_log[i] == pytest.approx([float(x) for x in p[i]])
        assert moments.predictive_cov_simple[i] == pytest.approx([float(x) for x in cov[i]])
        assert moments.second_raw_moment_simple[i] == pytest.approx(float(raw[i][i]))
    weights = pw.constrained_kelly_qp(moments.mean_simple, moments.predictive_cov_simple, .25)
    assert weights == pytest.approx(expected)
    assert weights != pytest.approx(pw.constrained_kelly_qp(
        moments.mean_simple, [[float(x) for x in row] for row in raw], .25), rel=1e-6)
    assert weights != pytest.approx(pw.constrained_kelly_qp(
        moments.mean_simple, [[float(cov[0][0]), 0.], [0., float(cov[1][1])]], .25))


@pytest.mark.parametrize("c,expected", [(.25, [.05, .025]), (.5, [.1, .05])])
def test_prediction_scaling_c_is_once_not_account_capital_or_half_rebalance(c, expected):
    # Independent diagonal FOC: full Kelly=(.008/.04,.009/.09)=(.2,.1).
    target = pw.constrained_kelly_qp([.008, .009], [[.04, 0.], [0., .09]], c)
    assert target == pytest.approx(expected)
    assert sum(target) < .2  # neither the capital constraint nor a cap can mask c^2
    assert target != pytest.approx([c * x for x in expected])
    assert pw.clip_scale([.2, .1], c) == pytest.approx(expected)
    for capital in (100., 10000.):
        dollars = [capital * x for x in target]
        assert [x / capital for x in dollars] == pytest.approx(expected)
    current = [.3, .2]
    half_step = [(w + d) / 2 for w, d in zip(current, target)]
    assert half_step != pytest.approx([d / 2 for d in target])
    # Algebraic convention only: this is not a test of B5's runtime consumer.
    outer_f = .8
    assert [outer_f * x for x in target] != pytest.approx([c * x for x in target])


def test_fit_parks_short_or_degenerate_samples():
    days, s = _streams(60)
    fit = pw.fit_portfolio_kelly(("A", "B"), {"A": [0.01] * 20, "B": [0.02] * 20}, [0.0] * 20, (0.25, 0.5))
    assert fit["status"] == "PARKED" and fit["reason_code"] == "INSUFFICIENT_TRAIN_OBSERVATIONS"
    assert all(v == 0.0 for per in fit["weights"].values() for m in per.values() for v in m.values())
    fit = pw.fit_portfolio_kelly(("A", "B"), {"A": [0.01] * 40, "B": [0.01, -0.01] * 20}, [0.0] * 40, (0.25,))
    assert fit["status"] == "PARKED" and fit["reason_code"].startswith("ASSET_NOT_FITTABLE:A")


def test_portfolio_path_costs_and_drift():
    g, t = pw.portfolio_path([{"A": 0.5, "B": 0.5}] * 2, {"A": [0.1, 0.0], "B": [0.0, 0.0]}, [0.0, 0.0],
                             {"A": [0.0, 0.1], "B": [0.0, 0.0]})
    assert g == pytest.approx([0.05, 0.0])
    # after day 1 drift: A = 0.55/1.05, B = 0.5/1.05 -> rebalance traded + internal 0.5*0.1
    assert t[0] == pytest.approx(1.0)
    assert t[1] == pytest.approx(abs(0.5 - 0.55 / 1.05) + abs(0.5 - 0.5 / 1.05) + 0.05)


def _run(**over):
    _, s = _streams()
    kw = {"asset_returns": s["assets"], "cash_returns": s["cash"], "benchmark_returns": s["bench"],
          "asset_turnover": s["turnover"], "plan": PLAN, "cost_bps_per_side": 5.0}
    kw.update(over)
    return pw.evaluate_portfolio_kelly_walk_forward(**kw)


def test_real_data_requires_authorization():
    with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_APPROVAL"):
        _run(synthetic=False)
    with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_APPROVAL"):
        _run(synthetic=False, real_data=object())
    with pytest.raises(ValueError, match="FULL_KELLY_NOT_ALLOWED"):
        _run(fractions=(0.5, 1.0))
    with pytest.raises(ValueError, match="METHODS_INVALID"):
        _run(methods=("mean_variance",))


@requires_qpk_stats
def test_synthetic_evaluation_contract():
    r = _run(bootstrap_seed=1)
    assert r["status"] == "COMPUTED" and r["synthetic"] is True and r["budget_effect"] == "NONE"
    names = set(r["comparators"])
    expected = {f"portfolio_kelly.{m}.c{c}" for m in ("qp", "clip_scale") for c in ("0.25", "0.5")}
    expected |= {f"single_kelly.{a}.c{c}" for a in "AB" for c in ("0.25", "0.5")}
    expected |= {"equal_weight_50_50", "equal_weight_25_25", "fixed_50.A", "fixed_50.B", "full.A", "full.B",
                 "benchmark.IA", "benchmark.IB", "benchmark.IA_IB_50_50"}
    assert names == expected
    assert len(r["dsr_trials"]) == 14
    for name in r["dsr_trials"]:
        assert r["comparators"][name]["dsr_net1x_excess_vs_all_sizing_trials"]["n_trials"] == 14
    for fold in r["folds"]:
        fit = fold["portfolio_fit"]
        for per in fit["weights"].values():
            for w in per.values():
                assert all(v >= 0 for v in w.values()) and sum(w.values()) <= 1 + 1e-9
        if fit["status"] == "COMPUTED":
            corr = fit["correlation_ledoit_wolf"]
            assert corr[0][0] == pytest.approx(1.0) and abs(corr[0][1]) <= 1
            assert abs(corr[0][1]) <= abs(fit["correlation_sample"][0][1]) + 1e-12  # LW shrinks toward 0 corr
    for name in names:
        lg = [r["comparators"][name]["tiers"][k]["log_growth_per_session"] for k in ("1", "2", "3")]
        assert lg[0] >= lg[1] >= lg[2]


@requires_qpk_stats
def test_single_kelly_comparator_matches_rs04_evaluator():
    days, s = _streams()
    r = _run(asset_turnover=None)
    one = rs04.evaluate_fractional_kelly_walk_forward(
        strategy_returns=s["assets"][0][1], benchmark_returns=s["bench"][0][1], cash_returns=s["cash"],
        plan=PLAN, cost_bps_per_side=5.0)
    for c in ("0.25", "0.5"):
        ours = r["comparators"][f"single_kelly.A.c{c}"]["tiers"]["2"]
        ref = one["comparators"][f"kelly_c{c}"]["tiers"]["2"]
        assert ours["log_growth_per_session"] == pytest.approx(ref["log_growth_per_session"], rel=1e-12)
        assert ours["max_drawdown"] == pytest.approx(ref["max_drawdown"], rel=1e-12)
    assert r["comparators"]["full.A"]["tiers"]["1"]["log_growth_per_session"] == pytest.approx(
        one["comparators"]["full_exposure"]["tiers"]["1"]["log_growth_per_session"], rel=1e-12)


@requires_qpk_stats
def test_no_lookahead_fold_weights_ignore_test_data():
    days, s = _streams()
    base = _run()
    first_test = PLAN.folds(len(days))[0][2]
    assets = [(k, rows[:first_test] + [(d, v * -1.5) for d, v in rows[first_test:]]) for k, rows in s["assets"]]
    shocked = _run(asset_returns=assets)
    assert base["folds"][0]["portfolio_fit"]["weights"] == shocked["folds"][0]["portfolio_fit"]["weights"]


@requires_qpk_stats
def test_report_is_schema_valid():
    jsonschema = pytest.importorskip("jsonschema")
    stats = importlib.import_module("quant_platform_kit.research_stats")
    ctx = {"report_id": "x", "candidate_id": "y", "source_revision": "0" * 40, "created_at": "2026-10-11T00:00:00Z",
           "manifest_sha256": "0" * 64, "source": "synthetic", "license": "synthetic",
           "signal_cutoff": "close_t", "execution": "next_close", "benchmark_name": "IA+IB",
           "benchmark_rationale": "synthetic"}
    r = _run(report_context=ctx)
    rep = r["backtest_report"]
    assert rep["status"] == "DEVELOPMENT_ONLY" and rep["trials"]["total"] == 14
    if hasattr(stats, "backtest_report_v1_schema"):
        jsonschema.validate(rep, stats.backtest_report_v1_schema())


def test_uncomputable_without_research_stats(monkeypatch):
    monkeypatch.setattr(rs04, "_stats_module", lambda: None)
    r = _run()
    assert r["status"] == "UNCOMPUTABLE" and r["reason_code"] == rs04.UNAVAILABLE and "comparators" not in r


# ---------- private runner on a synthetic archive ----------

def _walk(days, seed, drift, vol, start=100.0):
    rng, price, bars = random.Random(seed), start, []
    for d in days:
        price *= math.exp(rng.gauss(drift, vol))
        bars.append(gen.RawBar(d, price, price))
    return bars


def _write_archive(root, bars):
    inputs = []
    for symbol, rows in bars.items():
        for kind, payload in (("bars", {"symbol": symbol, "next_page_token": None,
                                        "bars": [{"t": f"{b.session.isoformat()}T04:00:00Z", "o": b.open,
                                                  "c": b.close} for b in rows]}),
                              ("actions", {"next_page_token": None, "corporate_actions": {}})):
            path = root / kind / symbol / "page-001.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload).encode()
            path.write_bytes(data)
            inputs.append({"symbol": symbol, "kind": kind, "complete_pagination": True, "count": len(rows),
                           "pages": [{"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}]})
    (root / "manifest.json").write_text(json.dumps({"inputs": inputs}))
    return hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()


@pytest.fixture
def archive(tmp_path, monkeypatch):
    days = _days(1000)
    soxx, qqq = _walk(days, 1, 0.0004, 0.015), _walk(days, 5, 0.0005, 0.012)
    bars = {"SOXX": soxx, "SOXL": [gen.RawBar(b.session, b.close ** 3 / 1e4, b.close ** 3 / 1e4) for b in soxx],
            "QQQ": qqq, "TQQQ": [gen.RawBar(b.session, b.close ** 3 / 1e4, b.close ** 3 / 1e4) for b in qqq],
            "QQQM": _walk(days, 6, 0.0005, 0.012), "BOXX": _walk(days[300:], 3, 0.00015, 0.0003)}
    root = tmp_path / "raw"
    sha = _write_archive(root, bars)
    data = {"data_source": "synthetic_archive", "data_manifest_sha256": sha, "data_license": "synthetic",
            "window_start": days[400], "window_end": days[-1]}
    monkeypatch.setattr(single, "PREREGISTRATIONS", {
        k: dataclasses.replace(v, data_confirmed_variants=(single.VARIANT_NO_PLUGIN,), **data)
        for k, v in single.PREREGISTRATIONS.items()})
    monkeypatch.setattr(pk, "PREREGISTRATIONS", {
        k: dataclasses.replace(v, **data) for k, v in pk.PREREGISTRATIONS.items()})
    return root


@requires_qpk_stats
def test_private_runner_end_to_end_on_synthetic_archive(archive, tmp_path):
    out = tmp_path / "out"
    r = runner.run_private(archive, out, "rs06-kelly-portfolio-v1", source_revision="0" * 40,
                           created_at="2026-10-11T00:00:00Z")
    assert r["status"] == "COMPUTED" and r["synthetic"] is False and r["assets"] == ["SOXL", "TQQQ"]
    assert r["strategy_variant"] == single.VARIANT_NO_PLUGIN and r["budget_effect"] == "NONE"
    assert "benchmark.SOXX_QQQ_50_50" in r["comparators"]
    assert set(pk.PREREGISTRATIONS["rs06-kelly-portfolio-v1"].comparators) <= set(r["comparators"])
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    from test_kelly_core_return_generator import _call_receipts
    receipts = _call_receipts(out)
    assert len(receipts["started"]) == len(receipts["terminal"]) == 1
    terminal = receipts["terminal"][0]
    assert terminal["status"] == "succeeded" and terminal["domain_result_status"] == "COMPUTED"
    assert terminal["parameter_fold_trial_count"] is None
    assert terminal["output_sha256"] == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in out.glob("*.json") if not p.name.startswith(".call-")}
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in out.iterdir())


def test_private_runner_rejects_wrong_archive_and_ci(archive, tmp_path, monkeypatch):
    (archive / "manifest.json").write_text("{}")
    with pytest.raises(runner.PrivateRunError, match="ARCHIVE_MANIFEST_SHA256_MISMATCH"):
        runner.run_private(archive, tmp_path / "o", "rs06-kelly-portfolio-v1", source_revision="0" * 40,
                           created_at="x")
    monkeypatch.setenv("CI", "true")
    with pytest.raises(SystemExit, match="PRIVATE_RUN_FORBIDDEN_IN_CI"):
        runner.main(["--raw-root", "/nonexistent", "--out-dir", "/tmp/x", "--preregistration-id",
                     "rs06-kelly-portfolio-v1", "--source-revision", "0" * 40, "--created-at", "x"])


@pytest.mark.parametrize("failure", ["rejected", "failed", "keyboard", "exit"])
def test_call_receipt_precedes_archive_failure(tmp_path, monkeypatch, failure):
    from test_kelly_core_return_generator import _assert_call_archive_failure
    from us_equity_strategies.research import kelly_private_runner as single_runner
    _assert_call_archive_failure(runner, single_runner, tmp_path / "out", monkeypatch, failure)


from test_kelly_core_return_generator import CALL_EDGE_CASES


@pytest.mark.parametrize("case", CALL_EDGE_CASES)
def test_call_receipt_replay_and_uncertainty(tmp_path, monkeypatch, case):
    from test_kelly_core_return_generator import _assert_call_edge
    from us_equity_strategies.research import kelly_private_runner as single_runner
    _assert_call_edge(runner, single_runner, tmp_path / "out", monkeypatch, case)


def test_call_receipt_cli_forwards_attempt_id(tmp_path, monkeypatch, capsys):
    # This unit test forwards arguments to a stub, never a private research run.
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    seen = {}
    def invoke(*args, **kwargs):
        seen.update(kwargs)
        return {"status": "COMPUTED"}
    monkeypatch.setattr(runner, "run_private", invoke)
    assert runner.main(["--raw-root", str(tmp_path / "raw"), "--out-dir", str(tmp_path / "out"),
                        "--preregistration-id", "rs06-kelly-portfolio-v1", "--source-revision", "0" * 40,
                        "--created-at", "synthetic", "--attempt-id", "explicit-future-call"]) == 0
    assert seen["attempt_id"] == "explicit-future-call"
    assert json.loads(capsys.readouterr().out)["status"] == "COMPUTED"


def test_call_receipt_records_invalid_registration(tmp_path, monkeypatch):
    from test_kelly_core_return_generator import _assert_call_invalid_registration
    from us_equity_strategies.research import kelly_private_runner as single_runner
    _assert_call_invalid_registration(runner, single_runner, tmp_path / "out", monkeypatch)


@pytest.mark.parametrize("kind", ["overridden_add_note", "non_list_existing_notes"])
def test_call_primary_object_survives_unusable_notes(tmp_path, monkeypatch, kind):
    from test_kelly_core_return_generator import _assert_primary_survives_unusable_notes
    from us_equity_strategies.research import kelly_private_runner as single_runner
    _assert_primary_survives_unusable_notes(runner, single_runner, tmp_path / "out", monkeypatch, kind)


@pytest.mark.parametrize("ci_flag", ["CI", "GITHUB_ACTIONS"])
def test_call_receipt_cli_ci_guard_never_calls_runner(tmp_path, monkeypatch, ci_flag):
    from test_kelly_core_return_generator import _assert_cli_ci_guard_never_calls_runner
    _assert_cli_ci_guard_never_calls_runner(runner, "rs06-kelly-portfolio-v1", tmp_path, monkeypatch, ci_flag)
