"""RS-04b: real-data allowlist + pre-registration gate. Synthetic series only."""
import dataclasses
import importlib
import os
import random
import socket
from datetime import date, timedelta
from pathlib import Path

import pytest

from us_equity_strategies.research import fractional_kelly_preregistration as prereg
from us_equity_strategies.research import fractional_kelly_walk_forward as rs04

REQUIRE_QPK_STATS = os.environ.get("UES_REQUIRE_QPK_RESEARCH_STATS") == "1"


def _has_stats():
    try:
        return hasattr(importlib.import_module("quant_platform_kit.research_stats"), "cost_stress_recompute")
    except ImportError:
        return False


requires_qpk_stats = pytest.mark.skipif(not _has_stats() and not REQUIRE_QPK_STATS,
                                        reason="installed QPK lacks research_stats")
DOC = Path(__file__).resolve().parents[2] / "docs/research/fractional_kelly_preregistration.zh-CN.md"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _series(n=600, seed=3):
    rng = random.Random(seed)
    days, d = [], date(2001, 1, 1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days, [(x, rng.gauss(0.0008, 0.01)) for x in days], [(x, rng.gauss(0.0004, 0.005)) for x in days]


DAYS, STRAT, BENCH = _series()
SOURCE = "synthetic_test_source_not_real"


def _manifest(**over):
    base = {"sha256": "b" * 64, "source": SOURCE, "license": "synthetic", "calendar": "XNYS",
            "start_date": DAYS[0], "end_date": DAYS[-1], "observations": len(DAYS)}
    base.update(over)
    return prereg.DataManifest(**base)


def _ctx(manifest, **over):
    ctx = {"report_id": "rs04b-test", "candidate_id": "soxl_test", "source_revision": "0" * 40,
           "created_at": "2026-10-11T00:00:00+08:00", "manifest_sha256": manifest.sha256,
           "source": manifest.source, "license": manifest.license,
           "signal_cutoff": "completed_regular_session_close_t", "execution": "next_close",
           "benchmark_name": "SOXX", "benchmark_rationale": "underlying 1x index ETF"}
    ctx.update(over)
    return ctx


@pytest.fixture
def confirmed(monkeypatch):
    registry = {k: dataclasses.replace(v, data_source=SOURCE, window_start=DAYS[0], window_end=DAYS[-1],
                                       data_manifest_sha256="b" * 64, data_license="synthetic")
                for k, v in prereg.PREREGISTRATIONS.items()}
    monkeypatch.setattr(prereg, "PREREGISTRATIONS", registry)
    return registry


def _run(key="SOXL", pid="rs04-kelly-soxl-v1", manifest=None, ctx=None, variant=prereg.VARIANT_NO_PLUGIN, **over):
    manifest = manifest or _manifest()
    p = prereg.PREREGISTRATIONS.get(pid) or prereg.PREREGISTRATIONS["rs04-kelly-soxl-v1"]
    kwargs = {"strategy_returns": STRAT, "benchmark_returns": BENCH, "plan": p.plan,
              "cost_bps_per_side": p.base_cost_bps_per_side, "fractions": p.fractions,
              "multipliers": p.multipliers, "synthetic": False, "assume_zero_cash": True,
              "real_data": prereg.RealDataAuthorization(key, pid, manifest, variant),
              "report_context": ctx if ctx is not None else _ctx(manifest)}
    kwargs.update(over)
    return rs04.evaluate_fractional_kelly_walk_forward(**kwargs)


def test_allowlist_is_exactly_soxl_and_tqqq():
    assert frozenset({"SOXL", "TQQQ"}) == prereg.REAL_DATA_ALLOWLIST
    assert {p.strategy_key for p in prereg.PREREGISTRATIONS.values()} == {"SOXL", "TQQQ"}


def test_shipped_preregistrations_are_frozen_and_confirmed():
    for p in prereg.PREREGISTRATIONS.values():
        assert p.plan == rs04.WalkForwardPlan(252, 63, 5, anchored=False)
        assert p.fractions == (0.25, 0.5) and p.base_cost_bps_per_side == 5.0
        assert p.multipliers == (1.0, 2.0, 3.0) and p.execution == "next_close"
        assert p.data_identity == "development" and "NO_BUDGET_INCREASE" in p.gates
        assert p.data_confirmed
        assert p.data_source == "alpaca_sip_raw_bars_plus_corporate_actions_private_archive"
        assert p.data_manifest_sha256 == "cb14a511083c824a748d137a271c93cfe0e8adf38f648905b26e37decf4c6182"
        assert (p.window_start, p.window_end) == (date(2023, 3, 28), date(2026, 8, 25))
        assert p.data_confirmed_variants == (prereg.VARIANT_NO_PLUGIN,)
    soxl = prereg.PREREGISTRATIONS["rs04-kelly-soxl-v1"]
    assert "SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED" in soxl.gates
    assert soxl.live_profile == "soxl_soxx_trend_income"
    assert prereg.PREREGISTRATIONS["rs04-kelly-tqqq-v1"].live_profile == "tqqq_growth_income"


def test_doc_mirrors_ids():
    text = DOC.read_text(encoding="utf-8")
    for pid in prereg.PREREGISTRATIONS:
        assert pid in text


def test_synthetic_stays_default():
    result = rs04.evaluate_fractional_kelly_walk_forward(
        strategy_returns=STRAT, benchmark_returns=BENCH, plan=rs04.WalkForwardPlan(120, 40, 2),
        cost_bps_per_side=5.0, assume_zero_cash=True)
    assert result["synthetic"] is True and result["preregistration_id"] is None


def test_non_allowlisted_or_missing_authorization_refused():
    with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_APPROVAL"):
        _run(real_data=None)
    for key in ("SPY", "QQQ", "soxl", "TECL"):
        with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_APPROVAL"):
            _run(key=key)
    with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_APPROVAL"):
        _run(real_data={"strategy_key": "SOXL"})


def test_synthetic_with_authorization_is_rejected():
    with pytest.raises(ValueError, match="REAL_DATA_AUTHORIZATION_WITH_SYNTHETIC"):
        _run(synthetic=True)


def test_unconfirmed_preregistration_blocks(monkeypatch):
    registry = {k: dataclasses.replace(v, data_source=None) for k, v in prereg.PREREGISTRATIONS.items()}
    monkeypatch.setattr(prereg, "PREREGISTRATIONS", registry)
    with pytest.raises(ValueError, match="PREREGISTRATION_PENDING_DATA_CONFIRMATION"):
        _run()


def test_shipped_preregistration_rejects_other_manifest():
    with pytest.raises(ValueError, match="DATA_MANIFEST_DIFFERS_FROM_PREREGISTRATION"):
        _run(manifest=_manifest(start_date=date(2023, 3, 28), end_date=date(2026, 8, 25)))


def test_wrong_or_cross_preregistration_id(confirmed):
    with pytest.raises(ValueError, match="PREREGISTRATION_ID_INVALID"):
        _run(pid="rs04-kelly-tqqq-v1")
    with pytest.raises(ValueError, match="PREREGISTRATION_ID_INVALID"):
        _run(pid="made-up")


def test_parameters_must_equal_preregistration(confirmed):
    with pytest.raises(ValueError, match="PARAMETERS_DIFFER_FROM_PREREGISTRATION"):
        _run(plan=rs04.WalkForwardPlan(252, 63, 5, anchored=True))
    with pytest.raises(ValueError, match="PARAMETERS_DIFFER_FROM_PREREGISTRATION"):
        _run(cost_bps_per_side=2.0)
    with pytest.raises(ValueError, match="PARAMETERS_DIFFER_FROM_PREREGISTRATION"):
        _run(fractions=(0.25,))


def test_manifest_is_required_and_must_match(confirmed):
    with pytest.raises(ValueError, match="DATA_MANIFEST_SHA256_INVALID"):
        _run(manifest=_manifest(sha256="xyz"))
    with pytest.raises(ValueError, match="DATA_MANIFEST_FIELD_MISSING"):
        _run(manifest=_manifest(license=" "))
    with pytest.raises(ValueError, match="DATA_MANIFEST_DIFFERS_FROM_PREREGISTRATION"):
        _run(manifest=_manifest(source="other"))
    with pytest.raises(ValueError, match="DATA_WINDOW_DIFFERS_FROM_PREREGISTRATION"):
        _run(manifest=_manifest(end_date=DAYS[-2]))
    with pytest.raises(ValueError, match="DATA_MANIFEST_DOES_NOT_MATCH_SERIES"):
        _run(manifest=_manifest(observations=len(DAYS) - 1))
    m = _manifest()
    with pytest.raises(ValueError, match="REPORT_CONTEXT_REQUIRED_FOR_REAL_DATA"):
        rs04.evaluate_fractional_kelly_walk_forward(
            strategy_returns=STRAT, benchmark_returns=BENCH, plan=rs04.WalkForwardPlan(252, 63, 5),
            cost_bps_per_side=5.0, synthetic=False, assume_zero_cash=True,
            real_data=prereg.RealDataAuthorization("SOXL", "rs04-kelly-soxl-v1", m, prereg.VARIANT_NO_PLUGIN))
    with pytest.raises(ValueError, match="REPORT_CONTEXT_DIFFERS"):
        _run(ctx=_ctx(m, execution="next_open"))
    with pytest.raises(ValueError, match="REPORT_CONTEXT_DIFFERS"):
        _run(ctx=_ctx(m, manifest_sha256="c" * 64))


@requires_qpk_stats
def test_confirmed_preregistration_runs_and_keeps_gates(confirmed):
    result = _run()
    assert result["status"] == "COMPUTED"
    assert result["synthetic"] is False and result["preregistration_id"] == "rs04-kelly-soxl-v1"
    assert result["strategy_variant"] == prereg.VARIANT_NO_PLUGIN
    assert "SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED" in result["gates"]
    assert result["budget_effect"] == "NONE" and result["live_ready"] is False
    report = result["backtest_report"]
    assert report["data"]["manifest_sha256"] == "b" * 64 and report["data"]["data_identity"] == "development"
    assert report["timing"]["execution"] == "next_close"


def test_plugin_inventory_covers_live_plugins():
    soxl = {p.name.split(" ")[0] for p in prereg.PREREGISTRATIONS["rs04-kelly-soxl-v1"].plugins}
    tqqq = {p.name.split(" ")[0] for p in prereg.PREREGISTRATIONS["rs04-kelly-tqqq-v1"].plugins}
    assert {"market_regime_control", "volatility_delever_retention", "income_layer", "runtime_risk_gate",
            "option_income_overlay"} <= soxl
    assert {"market_regime_control", "volatility_delever_retention", "dual_drive_crisis_defense",
            "income_layer", "option_growth_overlay", "ai_extensions"} <= tqqq
    for p in prereg.PREREGISTRATIONS.values():
        assert p.primary_variant == prereg.VARIANT_LIVE_PLUGINS
        assert set(p.variants) == {prereg.VARIANT_LIVE_PLUGINS, prereg.VARIANT_NO_PLUGIN}
        for plugin in p.plugins:
            assert plugin.reproducible in {prereg.REPRO_YES, prereg.REPRO_NEEDS_ADAPTER, prereg.REPRO_NO,
                                           prereg.REPRO_OFF_IN_LIVE}
            assert plugin.config_source and plugin.live_state


def test_doc_lists_every_plugin():
    text = DOC.read_text(encoding="utf-8")
    for p in prereg.PREREGISTRATIONS.values():
        for plugin in p.plugins:
            assert plugin.name.split(" ")[0] in text


def test_live_plugin_variant_blocked_while_plugins_not_reproducible(confirmed):
    with pytest.raises(ValueError, match="LIVE_PLUGIN_VARIANT_NOT_REPRODUCIBLE:.*market_regime_control"):
        _run(variant=prereg.VARIANT_LIVE_PLUGINS)
    with pytest.raises(ValueError, match="LIVE_PLUGIN_VARIANT_NOT_REPRODUCIBLE:.*dual_drive_crisis_defense"):
        _run(key="TQQQ", pid="rs04-kelly-tqqq-v1", variant=prereg.VARIANT_LIVE_PLUGINS)


def test_unknown_variant_refused(confirmed):
    with pytest.raises(ValueError, match="VARIANT_NOT_PREREGISTERED"):
        _run(variant="plugins_cherry_picked")


def test_live_variant_needs_its_own_confirmation_even_if_plugins_become_reproducible(confirmed, monkeypatch):
    registry = {k: dataclasses.replace(v, plugins=()) for k, v in prereg.PREREGISTRATIONS.items()}
    monkeypatch.setattr(prereg, "PREREGISTRATIONS", registry)
    with pytest.raises(ValueError, match="VARIANT_NOT_CONFIRMED_FOR_REAL_DATA"):
        _run(variant=prereg.VARIANT_LIVE_PLUGINS)


def test_manifest_sha_and_license_bound(confirmed):
    with pytest.raises(ValueError, match="DATA_MANIFEST_DIFFERS_FROM_PREREGISTRATION"):
        _run(manifest=_manifest(sha256="d" * 64))
    with pytest.raises(ValueError, match="DATA_MANIFEST_DIFFERS_FROM_PREREGISTRATION"):
        _run(manifest=_manifest(license="commercial"))
