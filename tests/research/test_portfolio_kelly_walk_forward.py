"""RS-06 portfolio fractional-Kelly evaluator + private runner. Synthetic data only."""
import dataclasses
import hashlib
import importlib
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
