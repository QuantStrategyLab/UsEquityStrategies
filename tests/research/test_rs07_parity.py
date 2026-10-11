"""RS-07 research-live parity fixes and disclosures. Synthetic data only."""
import dataclasses
import importlib
import inspect
import math
import os
import random
import socket
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import fractional_kelly_preregistration as prereg
from us_equity_strategies.research import fractional_kelly_walk_forward as rs04
from us_equity_strategies.research import kelly_core_return_generator as gen
from us_equity_strategies.research import kelly_disclosures as disc
from us_equity_strategies.research import kelly_private_runner as runner
from us_equity_strategies.research import portfolio_kelly_preregistration as pk

REQUIRE_QPK_STATS = os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1"


def _has_stats():
    try:
        return hasattr(importlib.import_module("quant_platform_kit.research_stats"), "cost_stress_recompute")
    except ImportError:
        return False


requires_qpk_stats = pytest.mark.skipif(not _has_stats() and not REQUIRE_QPK_STATS,
                                        reason="installed QPK lacks research_stats")


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


def _walk(days, seed, drift, vol, start=100.0):
    rng, price, bars = random.Random(seed), start, []
    for d in days:
        price *= math.exp(rng.gauss(drift, vol))
        bars.append(gen.RawBar(d, price, price))
    return bars


# ---------- (b) split-adjusted signal prices ----------

def test_price_returns_exclude_dividends_but_keep_splits():
    days = _days(4)
    bars = [gen.RawBar(days[0], 100, 100), gen.RawBar(days[1], 50, 50), gen.RawBar(days[2], 49, 49),
            gen.RawBar(days[3], 49, 49)]
    acts = gen.CorporateActions(splits=((days[1], 2.0),), dividends=((days[2], 1.0),))
    pr = gen.signal_returns(bars, acts, days, gen.SIGNAL_SPLIT_ADJUSTED)
    tr = gen.signal_returns(bars, acts, days, gen.SIGNAL_TOTAL_RETURN)
    assert pr[1] == pytest.approx(0.0) and pr[2] == pytest.approx(49 / 50 - 1)
    assert tr[2] == pytest.approx(0.0)
    hist = gen.split_adjusted_history(gen._index(pr), 49.0)
    assert hist[-1] == pytest.approx(49.0) and hist[0] == pytest.approx(50.0)  # pre-split 100 -> 50
    with pytest.raises(gen.CoreGeneratorError, match="SIGNAL_PRICE_BASIS_INVALID"):
        gen.signal_returns(bars, acts, days, "adjusted_close")


def test_tqqq_split_adjusted_signal_differs_only_through_dividends():
    days = _days(420)
    qqq = _walk(days, 5, 0.0005, 0.012)
    tqqq = [gen.RawBar(b.session, b.close ** 3 / 1e4, b.close ** 3 / 1e4) for b in qqq]
    bars = {"QQQ": qqq, "TQQQ": tqqq, "QQQM": _walk(days, 6, 0.0005, 0.012), "BOXX": _walk(days, 7, 0.0001, 0.0002)}
    v1 = gen.generate_tqqq_core_path(bars, {}, days, trade_start=days[300])
    v2 = gen.generate_tqqq_core_path(bars, {}, days, trade_start=days[300], signal_price_basis="split_adjusted")
    assert v1.gross_returns == v2.gross_returns  # no dividends: identical decisions
    divs = {"QQQ": gen.CorporateActions(dividends=tuple((d, 3.0) for d in days[50::20]))}
    w1 = gen.generate_tqqq_core_path(bars, divs, days, trade_start=days[300])
    w2 = gen.generate_tqqq_core_path(bars, divs, days, trade_start=days[300], signal_price_basis="split_adjusted")
    assert w1.weights != w2.weights


# ---------- (a) dynamic RSI from the live builder ----------

def test_research_simulator_lacks_dynamic_rsi_threshold():
    import pandas as pd

    from us_equity_strategies.backtest.soxl_trend_simulator import build_indicator_history
    days = _days(400)
    soxx = [b.close for b in _walk(days, 1, 0.0004, 0.015)]
    frame = pd.DataFrame({"SOXL": soxx, "SOXX": soxx}, index=pd.to_datetime(days))
    hist = build_indicator_history(frame, trend_ma_window=140, volatility_window=10, volatility_lookback=252,
                                   volatility_percentile=0.95, volatility_min_periods=126, volatility_floor=0.5,
                                   volatility_cap=0.75)
    assert "rsi14_dynamic_threshold" not in hist["soxx"].columns  # claim (a): core_v1 falls back to fixed 70


def test_manifest_matches_qpk_live_builder_defaults():
    """Live IBKR passes only trend_ma_window; the manifest must equal the QPK defaults for everything else."""
    from quant_platform_kit.common.runtime_inputs import build_semiconductor_rotation_indicators_from_history as b

    from us_equity_strategies.manifests import soxl_soxx_trend_income_manifest as m
    d = {k: v.default for k, v in inspect.signature(b).parameters.items()}
    c = m.default_config
    assert c["blend_gate_volatility_delever_window"] == d["dynamic_volatility_delever_window"]
    assert c["blend_gate_volatility_delever_dynamic_lookback"] == d["dynamic_volatility_delever_quantile_window"]
    assert c["blend_gate_volatility_delever_dynamic_percentile"] == d["dynamic_volatility_delever_quantile"]
    assert c["blend_gate_volatility_delever_dynamic_min_periods"] == d["dynamic_volatility_delever_min_periods"]
    assert c["blend_gate_volatility_delever_dynamic_floor"] == d["dynamic_volatility_delever_floor"]
    assert c["blend_gate_volatility_delever_dynamic_cap"] == d["dynamic_volatility_delever_cap"]
    assert c["blend_gate_rsi_threshold"] == d["dynamic_rsi_floor"]
    assert c["blend_gate_dynamic_rsi_threshold_enabled"] is True


def _soxl_bars(n):
    days = _days(n)
    soxx = _walk(days, 11, 0.0025, 0.012)
    soxl = [gen.RawBar(b.session, b.close ** 3 / 1e4, b.close ** 3 / 1e4) for b in soxx]
    return days, {"SOXL": soxl, "SOXX": soxx, "BOXX": _walk(days, 3, 0.00015, 0.0003)}


def test_soxl_live_builder_path_uses_dynamic_threshold(monkeypatch):
    from quant_platform_kit.common import runtime_inputs as ri
    days, bars = _soxl_bars(700)
    seen = []
    real = ri.build_semiconductor_rotation_indicators_from_history

    def spy(**kw):
        out = real(**kw)
        seen.append((len(kw["soxx_history"]), out["soxx"]["rsi14_dynamic_threshold"], kw["soxx_history"][-1]))
        return out
    monkeypatch.setattr(ri, "build_semiconductor_rotation_indicators_from_history", spy)
    path = gen.generate_soxl_core_path(bars, {}, days, trade_start=days[400], signal_price_basis="split_adjusted",
                                       indicator_source="qpk_live_builder", indicator_history_bars=504)
    w = path.window(days[400], days[-1])
    assert all(0.0 < sum(x.values()) <= 1.0 + 1e-9 for x in w.weights)
    assert max(n for n, _, _ in seen) == 504 and min(n for n, _, _ in seen) >= gen.MIN_INDICATOR_HISTORY_BARS
    assert any(t > 70.0 for _, t, _ in seen)  # dynamic threshold actually varies above the fixed floor
    assert seen[-1][2] == pytest.approx(bars["SOXX"][-2].close)  # split-adjusted history ends at the raw close
    with pytest.raises(gen.CoreGeneratorError, match="INDICATOR_HISTORY_BARS_TOO_SHORT"):
        gen.generate_soxl_core_path(bars, {}, days, indicator_source="qpk_live_builder", indicator_history_bars=100)
    with pytest.raises(gen.CoreGeneratorError, match="INDICATOR_SOURCE_INVALID"):
        gen.generate_soxl_core_path(bars, {}, days, indicator_source="other")


# ---------- preregistration (new trials) ----------

def test_v2_preregistrations_freeze_everything_but_generator():
    keep = ("plan", "fractions", "base_cost_bps_per_side", "multipliers", "signal_cutoff", "execution",
            "data_manifest_sha256", "data_source", "window_start", "window_end", "data_confirmed_variants")
    for v2, v1 in (("rs04-kelly-soxl-v2", "rs04-kelly-soxl-v1"), ("rs04-kelly-tqqq-v2", "rs04-kelly-tqqq-v1"),
                   ("rs04-kelly-soxl-v2-trsig", "rs04-kelly-soxl-v1")):
        a, b = prereg.PREREGISTRATIONS[v2], prereg.PREREGISTRATIONS[v1]
        assert all(getattr(a, k) == getattr(b, k) for k in keep)
        assert a.generator_version == "core_v2_parity" and b.generator_version == "core_v1"
    assert prereg.PREREGISTRATIONS["rs04-kelly-soxl-v2"].signal_price_basis == "split_adjusted"
    assert prereg.PREREGISTRATIONS["rs04-kelly-soxl-v2"].indicator_source == "qpk_live_builder"
    assert prereg.PREREGISTRATIONS["rs04-kelly-soxl-v2-trsig"].trial_role == "sensitivity"
    p2 = pk.PREREGISTRATIONS["rs06-kelly-portfolio-v2"]
    assert p2.components == (("SOXL", "rs04-kelly-soxl-v2"), ("TQQQ", "rs04-kelly-tqqq-v2"))
    assert runner.generator_kwargs(prereg.PREREGISTRATIONS["rs04-kelly-soxl-v1"]) == {}
    assert runner.generator_kwargs(prereg.PREREGISTRATIONS["rs04-kelly-tqqq-v2"]) == {
        "signal_price_basis": "split_adjusted"}


def test_trial_ledger_lists_every_preregistration():
    import json
    from pathlib import Path
    ledger = json.loads((Path(__file__).resolve().parents[2] / "docs/research/kelly_trial_ledger.json").read_text())
    ids = {t["preregistration_id"] for t in ledger["trials"]}
    assert set(prereg.PREREGISTRATIONS) | set(pk.PREREGISTRATIONS) <= ids
    assert ledger["cumulative_sizing_trials"] == sum(t["sizing_trials"] for t in ledger["trials"])
    assert all("result" not in t and "metrics" not in t for t in ledger["trials"])  # data-free


# ---------- (d)(e) disclosures and paired bootstrap ----------

def test_coverage_discloses_dropped_tail():
    days = _days(100)
    plan = rs04.WalkForwardPlan(40, 20, 2)
    cov = disc.coverage_disclosure(days, plan.folds(100))
    assert cov["train_only_warmup_sessions"] == 42 and cov["tested_sessions"] == 40
    assert cov["dropped_tail_sessions"] == 18 and cov["dropped_tail_start"] == days[82].isoformat()


def test_paired_bootstrap_properties():
    rng = random.Random(1)
    a = [rng.gauss(0.001, 0.01) for _ in range(300)]
    same = disc.paired_stationary_bootstrap(a, a, seed=3)
    assert same["mean_diff_per_session"] == 0.0 and same["ci_low"] == same["ci_high"] == 0.0
    up = disc.paired_stationary_bootstrap([x + 0.002 for x in a], a, seed=3, n_resamples=300)
    again = disc.paired_stationary_bootstrap([x + 0.002 for x in a], a, seed=3, n_resamples=300)
    assert up == again and up["ci_low"] > 0 and up["share_resamples_le_zero"] == 0.0
    assert disc.paired_stationary_bootstrap(a, a[:-1], seed=1)["status"] == "UNCOMPUTABLE"


@requires_qpk_stats
def test_rs04_result_carries_disclosures():
    days = _days(400)
    rng = random.Random(9)
    strat = [(d, rng.gauss(0.001, 0.02)) for d in days]
    bench = [(d, rng.gauss(0.0005, 0.01)) for d in days]
    r = rs04.evaluate_fractional_kelly_walk_forward(
        strategy_returns=strat, benchmark_returns=bench, plan=rs04.WalkForwardPlan(120, 40, 2),
        cost_bps_per_side=5.0, assume_zero_cash=True, bootstrap_seed=7)
    assert r["coverage"]["dropped_tail_sessions"] == 400 - (122 + 40 * 6)
    assert r["metric_basis"]["psr_net1x_excess"].startswith("PRIMARY")
    assert r["cost_model_disclosure"]["outer_cash_leg"].startswith("FREE")
    for c in ("0.25", "0.5"):
        comp = r["comparators"][f"kelly_c{c}"]
        assert comp["psr_net1x_excess"]["status"] == "COMPUTED"
        assert comp["psr_net1x_excess"]["value"] <= comp["psr_gross_excess"]["value"] + 1e-12
        assert "bootstrap_net1x" in comp
        assert r["paired_bootstrap"][f"kelly_c{c}__vs__fixed_50pct"]["status"] == "COMPUTED"


@requires_qpk_stats
def test_v2_runner_end_to_end(tmp_path, monkeypatch):
    import hashlib
    import json
    days, bars = _soxl_bars(800)
    root = tmp_path / "raw"
    inputs = []
    for symbol, rows in bars.items():
        for kind, payload in (("bars", {"symbol": symbol, "next_page_token": None,
                                        "bars": [{"t": f"{b.session}T04:00:00Z", "o": b.open, "c": b.close}
                                                 for b in rows]}),
                              ("actions", {"next_page_token": None, "corporate_actions": {}})):
            path = root / kind / symbol / "page-001.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload).encode()
            path.write_bytes(data)
            inputs.append({"symbol": symbol, "kind": kind, "complete_pagination": True, "count": len(rows),
                           "pages": [{"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}]})
    (root / "manifest.json").write_text(json.dumps({"inputs": inputs}))
    sha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(prereg, "PREREGISTRATIONS", {
        k: dataclasses.replace(v, data_source="synthetic", data_manifest_sha256=sha, data_license="synthetic",
                               window_start=days[400], window_end=days[-1])
        for k, v in prereg.PREREGISTRATIONS.items()})
    r = runner.run_private(root, tmp_path / "out", "rs04-kelly-soxl-v2", source_revision="0" * 40,
                           created_at="2026-10-11T00:00:00Z")
    assert r["status"] == "COMPUTED" and r["generator"]["indicator_source"] == "qpk_live_builder"
    assert any("core_v2_parity" in x for x in r["backtest_report"]["limitations"])
    assert any("not evaluated" in x or "no window sessions" in x for x in r["backtest_report"]["limitations"])
    assert r["paired_bootstrap"]
