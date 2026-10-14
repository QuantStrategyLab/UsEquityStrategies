"""RS-04d: no-plugin core path generators + private runner. Synthetic data only."""
import dataclasses
import hashlib
import importlib
import inspect
import json
import math
import os
import random
import socket
import stat
import sys
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import fractional_kelly_preregistration as prereg
from us_equity_strategies.research import kelly_core_return_generator as gen
from us_equity_strategies.research import kelly_private_runner as runner

REQUIRE_QPK_STATS = os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1"


def _has_stats():
    try:
        return hasattr(importlib.import_module("quant_platform_kit.research_stats"), "cost_stress_recompute")
    except ImportError:
        return False


requires_qpk_stats = pytest.mark.skipif(not _has_stats() and not REQUIRE_QPK_STATS,
                                        reason="installed QPK lacks research_stats")


def _has_execution_models():
    try:
        importlib.import_module("quant_platform_kit.research_stats.execution_models")
        return True
    except ImportError:
        return False


requires_execution_models = pytest.mark.skipif(
    not _has_execution_models() and os.environ.get("UES_REQUIRE_QPK_EXECUTION_MODELS") != "1",
    reason="installed QPK lacks opt-in A2 execution models; pin remains unchanged",
)


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


def test_total_return_handles_split_and_dividend():
    days = _days(4)
    bars = [gen.RawBar(days[0], 100, 100), gen.RawBar(days[1], 50, 50), gen.RawBar(days[2], 49, 49),
            gen.RawBar(days[3], 49, 49)]
    acts = gen.CorporateActions(splits=((days[1], 2.0),), dividends=((days[2], 1.0),))
    r = gen.total_return_series(bars, acts, days)
    assert r[0] == 0.0 and r[1] == pytest.approx(0.0) and r[2] == pytest.approx(0.0) and r[3] == 0.0
    with pytest.raises(gen.CoreGeneratorError, match="SPLIT_AND_DIVIDEND_SAME_DAY"):
        gen.total_return_series(bars, gen.CorporateActions(((days[1], 2.0),), ((days[1], 1.0),)), days)
    with pytest.raises(gen.CoreGeneratorError, match="MISSING_BAR"):
        gen.total_return_series(bars[:3], gen.CorporateActions(), days)


def test_replay_next_close_timing_and_turnover():
    days = _days(8)
    ra = (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07)

    def decide(i, drifted):
        return None if i < 2 else {"A": 1.0}

    path = gen._replay("X", days, {"A": ra}, ("A",), decide, None)
    assert path.first_signal == days[2]
    # decided at close 2 -> traded at close 3 -> earns session 4
    assert path.gross_returns[3] == 0.0 and path.gross_returns[4] == pytest.approx(0.04)
    assert path.turnover[4] == pytest.approx(1.0) and path.turnover[5] == pytest.approx(0.0)
    assert dict(path.weights[4]) == {"A": 1.0}


def _close_fill_path(targets, returns=None):
    days = _days(len(targets))
    path = gen._replay("X", days, {"A": returns or (0.0,) * len(days)}, ("A",),
                       lambda i, _weights: {"A": targets[i]}, None)
    return path


@requires_execution_models
def test_close_fill_costs_include_terminal_entry_and_exit():
    from quant_platform_kit.research_stats.execution_models import CostModel

    model = CostModel(commission_bps=10)
    exit_path = _close_fill_path((1.0, 0.0, 0.0))
    assert exit_path.turnover == (0.0, 0.0, 1.0)  # legacy remains shifted
    assert exit_path.close_fill_turnover == (0.0, 1.0, 1.0)
    net = exit_path.measure_close_fill_costs(model)
    assert net == pytest.approx((0.0, -.001, -.001))
    assert math.prod(1 + r for r in net) == pytest.approx(.999 ** 2)
    entry_path = _close_fill_path((0.0, 1.0, 0.0))
    assert entry_path.turnover == (0.0, 0.0, 0.0)
    assert entry_path.close_fill_turnover == (0.0, 0.0, 1.0)
    assert entry_path.measure_close_fill_costs(model) == pytest.approx((0.0, 0.0, -.001))
    assert _close_fill_path((0.0, 0.0, 1.0)).measure_close_fill_costs(model) == (0.0, 0.0, 0.0)


@requires_execution_models
def test_close_fill_costs_use_pre_fill_nav_once_and_preserve_window():
    from quant_platform_kit.research_stats.execution_models import CostModel

    path = _close_fill_path((1.0, 0.0, 0.0), (0.0, 0.0, .5))
    # Last day's starting NAV=1: asset gain=.5, pre-fill NAV=1.5,
    # exit notional=1.5, cost=.015. No extra multiplication by 1.5.
    model = CostModel(commission_bps=100)
    assert path.measure_close_fill_costs(model) == pytest.approx((0.0, -.01, .485))
    window = path.window(path.sessions[2], path.sessions[2])
    assert window.close_fill_turnover == (1.0,)
    assert window.measure_close_fill_costs(model) == pytest.approx((.485,))
    assert path.measure_close_fill_costs(CostModel()) == path.gross_returns


@requires_execution_models
def test_close_fill_measurement_changes_neither_gross_state_nor_default_wire():
    from quant_platform_kit.research_stats.execution_models import CostModel
    from us_equity_strategies.research.execution_model_adapter import replay_targets

    path = _close_fill_path((.5, .5, 0.0, 1.0), (0.0, 0.0, 120 / 100 - 1, 0.0))
    before = dataclasses.asdict(path)
    digest = gen.path_digest_payload(path)
    # This is the existing private runner's explicit core-path payload.
    wire = {"sessions": [d.isoformat() for d in path.sessions],
            "gross_returns": path.gross_returns, "turnover": path.turnover,
            "first_signal": str(path.first_signal)}
    assert path.close_fill_turnover == pytest.approx((0.0, .5, 1 / 22, .5))
    assert path.turnover == pytest.approx((0.0, 0.0, .5, 1 / 22))
    net = path.measure_close_fill_costs(CostModel(commission_bps=10, slippage_bps=5, multiplier=2))
    a2 = replay_targets(path.sessions, (100., 100., 120., 120.), (100., 100., 120., 120.),
                        (.5, .5, 0., 1.), timing="next_close", commission_bps=10,
                        slippage_bps=5, cost_multiplier=2)
    assert path.gross_returns == a2.gross_returns
    assert net == a2.net_returns
    assert net[1] == pytest.approx(-.0015)  # charged on fill, before next earning session
    assert dataclasses.asdict(path) == before
    assert gen.path_digest_payload(path) == digest
    assert set(digest) == {"strategy_key", "variant", "sessions", "start", "end", "first_signal"}
    assert "close_fill_turnover" not in wire
    assert wire["turnover"] == path.turnover
    # Final decision at close3 has no next-session fill; only prior exit is charged.
    assert path.close_fill_turnover[-1] == .5


@requires_execution_models
@pytest.mark.parametrize("invalid", ["missing", "length", "nan", "negative", "bool", "gross_nan"])
def test_close_fill_measurement_rejects_unusable_diagnostics(invalid):
    from quant_platform_kit.research_stats.execution_models import CostModel

    path = _close_fill_path((1.0, 0.0, 0.0))
    changes = {"close_fill_turnover": {"missing": (), "length": (1.0,), "nan": (0., 1., math.nan),
                                      "negative": (0., 1., -1.), "bool": (False, 1., 1.)}.get(invalid,
                                                                                              path.close_fill_turnover)}
    if invalid == "gross_nan":
        changes["gross_returns"] = (0., 0., math.nan)
    with pytest.raises(gen.CoreGeneratorError):
        dataclasses.replace(path, **changes).measure_close_fill_costs(CostModel())
    with pytest.raises(gen.CoreGeneratorError, match="COST_MODEL_INVALID"):
        path.measure_close_fill_costs(object())


def test_close_fill_measurement_requires_explicit_qpk_capability(monkeypatch):
    path = _close_fill_path((1.0, 0.0, 0.0))
    monkeypatch.setitem(sys.modules, "quant_platform_kit.research_stats.execution_models", None)
    with pytest.raises(gen.CoreGeneratorError, match="QPK_EXECUTION_MODELS_REQUIRED"):
        path.measure_close_fill_costs(object())


def test_replay_fails_closed_after_warmup():
    days = _days(8)
    calls = iter([None, None, {"A": 1.0}, None])

    def decide(i, drifted):
        return next(calls, {"A": 1.0})

    with pytest.raises(gen.CoreGeneratorError, match="AFTER_WARMUP"):
        gen._replay("X", days, {"A": (0.0,) * 8}, ("A",), decide, None)
    with pytest.raises(gen.CoreGeneratorError, match="LEVERED"):
        gen._replay("X", days, {"A": (0.0,) * 8}, ("A",), lambda i, w: {"A": 1.5}, None)
    with pytest.raises(gen.CoreGeneratorError, match="BEFORE_FIRST_BAR"):
        gen._replay("X", days, {"A": (0.0,) * 8}, ("A",), lambda i, w: {"A": 1.0}, None, {"A": 5})


def test_no_plugin_overrides_match_live_config_keys():
    from us_equity_strategies.manifests import soxl_soxx_trend_income_manifest, tqqq_growth_income_manifest
    from us_equity_strategies.strategies import soxl_soxx_trend_income, tqqq_growth_income
    for overrides, manifest, fn in (
            (gen.SOXL_NO_PLUGIN_OVERRIDES, soxl_soxx_trend_income_manifest, soxl_soxx_trend_income.build_rebalance_plan),
            (gen.TQQQ_NO_PLUGIN_OVERRIDES, tqqq_growth_income_manifest, tqqq_growth_income.build_rebalance_plan)):
        params = set(inspect.signature(fn).parameters)
        for key in overrides:
            assert key in params, key
        assert set(overrides) - set(manifest.default_config) <= {"dual_drive_macro_risk_governor_enabled"}


def _soxl_bars(n=700):
    days = _days(n)
    soxx = _walk(days, 1, 0.0004, 0.015)
    soxl = [gen.RawBar(b.session, b.close ** 3 / 1e4, b.close ** 3 / 1e4) for b in soxx]
    boxx = _walk(days[300:], 3, 0.00015, 0.0003)
    return days, {"SOXL": soxl, "SOXX": soxx, "BOXX": boxx}


def test_soxl_core_path_on_synthetic_bars():
    days, bars = _soxl_bars()
    path = gen.generate_soxl_core_path(bars, {}, days, trade_start=days[400])
    assert path.first_signal is not None and path.first_signal >= days[398]
    w = path.window(days[400], days[-1])
    for weights, r, i in zip(w.weights, w.gross_returns, range(len(w.sessions)), strict=True):
        assert 0.0 < sum(weights.values()) <= 1.0 + 1e-9
        k = 400 + i
        expected = sum(v * w.asset_returns[a][i] for a, v in weights.items())
        assert r == pytest.approx(expected)
        assert k < len(days)
    assert all(t >= 0 for t in w.turnover) and w.turnover[0] > 0  # entry from cash charged
    with pytest.raises(gen.CoreGeneratorError, match="WARMUP|NOT_COVERED"):
        path.window(days[0], days[-1])


def test_tqqq_core_path_on_synthetic_bars():
    days = _days(520)
    qqq = _walk(days, 5, 0.0005, 0.012)
    tqqq = [gen.RawBar(b.session, b.close ** 3 / 1e4, b.close ** 3 / 1e4) for b in qqq]
    bars = {"QQQ": qqq, "TQQQ": tqqq, "QQQM": _walk(days, 6, 0.0005, 0.012), "BOXX": _walk(days, 7, 0.0001, 0.0002)}
    path = gen.generate_tqqq_core_path(bars, {}, days, trade_start=days[300])
    w = path.window(days[300], days[-1])
    assert all(0.0 < sum(x.values()) <= 1.0 + 1e-9 for x in w.weights)
    assert set().union(*[set(x) for x in w.weights]) <= {"TQQQ", "QQQM", "BOXX"}


# ---------- private runner on a synthetic archive ----------

def _write_archive(root, bars, actions=None):
    inputs = []
    for symbol, rows in bars.items():
        for kind, payload in (("bars", {"symbol": symbol, "next_page_token": None,
                                        "bars": [{"t": f"{b.session.isoformat()}T04:00:00Z", "o": b.open,
                                                  "h": max(b.open, b.close), "l": min(b.open, b.close),
                                                  "c": b.close, "v": 1000} for b in rows]}),
                              ("actions", {"next_page_token": None,
                                           "corporate_actions": (actions or {}).get(symbol, {})})):
            path = root / kind / symbol / "page-001.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload).encode()
            path.write_bytes(data)
            inputs.append({"symbol": symbol, "kind": kind, "complete_pagination": True,
                           "count": len(rows), "pages": [{"sha256": hashlib.sha256(data).hexdigest(),
                                                          "bytes": len(data)}]})
    (root / "manifest.json").write_text(json.dumps({"inputs": inputs}))
    return hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()


@pytest.fixture
def archive(tmp_path, monkeypatch):
    days, bars = _soxl_bars(1000)
    root = tmp_path / "raw"
    sha = _write_archive(root, bars)
    registry = {k: dataclasses.replace(v, data_source="synthetic_archive", data_manifest_sha256=sha,
                                       data_license="synthetic", window_start=days[400], window_end=days[-1],
                                       data_confirmed_variants=(prereg.VARIANT_NO_PLUGIN,))
                for k, v in prereg.PREREGISTRATIONS.items()}
    monkeypatch.setattr(prereg, "PREREGISTRATIONS", registry)
    return root, days


@requires_qpk_stats
def test_private_runner_end_to_end_on_synthetic_archive(archive, tmp_path):
    root, _days_ = archive
    out = tmp_path / "out"
    result = runner.run_private(root, out, "rs04-kelly-soxl-v1", source_revision="0" * 40,
                                created_at="2026-10-11T00:00:00+08:00")
    assert result["status"] == "COMPUTED" and result["synthetic"] is False
    assert result["strategy_variant"] == prereg.VARIANT_NO_PLUGIN and result["budget_effect"] == "NONE"
    assert result["backtest_report"]["data"]["data_identity"] == "development"
    assert "dsr_net1x_excess_vs_sizing_trials" in result["comparators"]["kelly_c0.5"]
    assert "cvar_95_daily" in result["comparators"]["full_exposure"]["tiers"]["1"]
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    core_wire = json.loads((out / "rs04-kelly-soxl-v1.core_path.json").read_text())
    assert set(core_wire) == {"sessions", "gross_returns", "turnover", "first_signal"}
    assert "close_fill_turnover" not in json.dumps(result)
    assert "measure_close_fill_costs" not in json.dumps(result)
    for f in out.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_private_runner_rejects_wrong_archive(archive, tmp_path):
    root, _ = archive
    (root / "manifest.json").write_text("{}")
    with pytest.raises(runner.PrivateRunError, match="ARCHIVE_MANIFEST_SHA256_MISMATCH"):
        runner.run_private(root, tmp_path / "o", "rs04-kelly-soxl-v1", source_revision="0" * 40, created_at="x")
    with pytest.raises(runner.PrivateRunError, match="ARCHIVE_NOT_FOUND"):
        runner.run_private(tmp_path / "missing", tmp_path / "o", "rs04-kelly-soxl-v1",
                           source_revision="0" * 40, created_at="x")


def test_private_runner_rejects_unhandled_corporate_actions(tmp_path):
    days, bars = _soxl_bars(50)
    root = tmp_path / "raw"
    sha = _write_archive(root, bars, {"SOXL": {"reverse_splits": [{"ex_date": days[3].isoformat()}]}})
    with pytest.raises(runner.PrivateRunError, match="UNHANDLED_CORPORATE_ACTION:SOXL:reverse_splits"):
        runner.load_archive(root, ["SOXL"], sha)


def test_private_runner_refuses_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(SystemExit, match="PRIVATE_RUN_FORBIDDEN_IN_CI"):
        runner.main(["--raw-root", "/nonexistent", "--out-dir", "/tmp/x", "--preregistration-id",
                     "rs04-kelly-soxl-v1", "--source-revision", "0" * 40, "--created-at", "x"])
