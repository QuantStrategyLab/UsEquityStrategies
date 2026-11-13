"""Fake-only engineering checks; no vendor, real replay or forward observation."""
from __future__ import annotations

import json
import multiprocessing
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from us_equity_strategies.research import new_evidence_package as package


def bundle():
    sessions = ["2030-01-02", "2030-01-03", "2030-01-04"]
    return {
        "schema_version": "ues-new-assurance.v1", "evidence_origin": "NEW",
        "evidence_class": "retrospective", "calendar_id": "XNYS",
        "expected_sessions": sessions, "sessions": sessions.copy(),
        "symbols": list(package.SYMBOLS), "basis": "raw_plus_actions_once",
        "rows": [{"symbol": s, "session": d, "open": 10., "high": 11.,
                  "low": 9., "close": 10., "volume": 10., "verifier_close": 10.,
                  "verifier_open": 10., "verifier_high": 11., "verifier_low": 9.,
                  "available_at": d + "T22:00:00Z", "decision_at": d + "T23:00:00Z"}
                 for s in package.SYMBOLS for d in sessions],
        "actions": [],
        "coverage": [{"symbol": s, "kind": k, "start": sessions[0], "end": sessions[-1],
                      "canonical_complete": True, "verifier_complete": True,
                      "independent": True, "pagination_exhausted": True,
                      "negative_coverage_proven": True}
                     for s in package.SYMBOLS for k in ("splits", "distributions")],
        "qqq_distributions_qualified": True,
    }


def admission():
    # Test declarations only, never source/rights/producer approval.
    return {"identity": {**package.source_identity(), "producer_sha256": "a" * 64},
            "package_id": package.PACKAGE_ID, "evidence_origin": "NEW",
            "execution_authorized": False, "promotion_eligible": False,
            "historical_receipts_redeemed": False,
            "logical_paths": package.package_definition()["logical_paths"],
            "definition_sha256": package._digest(package.package_definition()),
            "resources": dict(package.RESOURCE_CEILINGS),
            "provenance": {k: True for k in ("source_bound", "rights_bound",
                "zero_incremental_cost_proven", "runner_bound", "store_bound")},
            "retention": {"evaluator_readback": True, "immutable": True,
                          "months_from_registration": 18, "days_after_evaluation": 90},
            "assurance": bundle()}


def test_fixed_new_definitions_and_legacy_non_redemption():
    d = package.package_definition()
    assert tuple(d["symbols"]) == package.SYMBOLS
    assert len(d["logical_paths"]) == len({tuple(x.values()) for x in d["logical_paths"]}) == 12
    assert {p["cost_bps"] for p in d["logical_paths"]} == {5, 10, 15}
    assert d["legacy"]["admissible_fills"] == "0/8"
    assert set(d["legacy"]["items"].values()) == {"INSUFFICIENT_EVIDENCE"}
    assert d["legacy"]["actual_params"] is None
    assert d["execution_authorized"] is d["promotion_eligible"] is False
    t, s = d["candidates"]
    assert t["candidate_id"] == "tqqq-current-guard-new-v1"
    assert s["candidate_id"] == "soxl-current-regime-retention-new-v1"
    for c in (t, s):
        delta = {k for k in c["treatment"] if c["treatment"][k] != c["control"][k]}
        assert delta == set(c["allowed_differences"])
        assert c["treatment"]["income_layer_enabled"] is False
        assert c["treatment"]["option_overlay_enabled"] is False
    assert s["treatment"]["trend_ma_window"] == 140
    assert d["materialization_status"] == "BLOCKED"
    d["candidates"][0]["treatment"]["income_layer_enabled"] = True
    assert package.package_definition()["candidates"][0]["treatment"]["income_layer_enabled"] is False


@pytest.mark.parametrize("mutate,code", [
    (lambda b: b.update(evidence_origin="fixture"), "PROVENANCE_INVALID"),
    (lambda b: b.update(evidence_class="historical_pit"), "PIT_UNPROVEN"),
    (lambda b: b["rows"].pop(), "SESSION_SYMBOL_GAP"),
    (lambda b: b["rows"].append(deepcopy(b["rows"][0])), "SESSION_SYMBOL_GAP"),
    (lambda b: b["sessions"].pop(), "SESSION_GAP"),
    (lambda b: b.update(basis="adjustment_all_plus_dividends"), "BASIS_MISMATCH"),
    (lambda b: b["rows"][0].update(close=float("nan")), "BAR_INVALID"),
    (lambda b: b["rows"][0].update(volume=-1), "BAR_INVALID"),
    (lambda b: b["rows"][0].update(verifier_close=10.02), "SOURCE_DISAGREEMENT"),
    (lambda b: b["rows"][0].update(available_at="2030-01-03T00:00:00Z"), "INPUT_LATE"),
    (lambda b: b["coverage"][0].update(pagination_exhausted=False), "ACTION_COVERAGE_UNKNOWN"),
    (lambda b: b["coverage"][0].update(negative_coverage_proven=False), "ACTION_COVERAGE_UNKNOWN"),
    (lambda b: b["coverage"].pop(), "ACTION_COVERAGE_UNKNOWN"),
    (lambda b: b["actions"].append({"kind": "split", "symbol": "SOXL"}), "ACTION_UNVERIFIED"),
])
def test_assurance_adverse(mutate, code):
    b = bundle(); mutate(b)
    with pytest.raises(package.NewEvidenceError, match="^" + code + "$" ):
        package.validate_assurance(b)


def test_assurance_success_is_only_offline_mechanism_and_dividend_dependency_is_local():
    b = bundle()
    assert package.validate_assurance(b)["financial_status"] == "NOT_RUN"
    b["qqq_distributions_qualified"] = False
    assert package.validate_assurance(b, use="qqq_split_signal")["financial_status"] == "NOT_RUN"
    with pytest.raises(package.NewEvidenceError, match="QQQ_DISTRIBUTIONS_UNPROVEN"):
        package.validate_assurance(b, use="qqq_total_return")


@pytest.mark.parametrize("key", list(package.RESOURCE_CEILINGS))
def test_each_resource_ceiling_rejects_excess_or_unknown(key):
    limits = dict(package.RESOURCE_CEILINGS)
    package.validate_resources(limits)
    limits[key] += 1
    with pytest.raises(package.NewEvidenceError, match="RESOURCE_CEILING"):
        package.validate_resources(limits)
    limits[key] = None
    with pytest.raises(package.NewEvidenceError, match="RESOURCE_CEILING"):
        package.validate_resources(limits)


def forward_receipt():
    start = date(2030, 1, 2)
    days = [(start + timedelta(days=i)).isoformat() for i in range(366)]
    return {"expected_sessions": days, "observed_sessions": days.copy(),
            "registered_at": "2030-01-01T00:00:00Z", "first_open_at": "2030-01-02T14:30:00Z",
            "first_session": days[0], "last_session": days[-1],
            "hard_expiry": "2031-02-01", "p3_sha256": "a" * 64,
            "frozen_sha256": "b" * 64, "observed_sha256": "b" * 64,
            "outcome_access_recorded": True, "unsealed_early": False,
            "historical_sessions_counted": 0, "pending_fill": False}


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(historical_sessions_counted=252), "FORWARD_HISTORY_FORBIDDEN"),
    (lambda r: r.update(observed_sessions=r["observed_sessions"][:251]), "FORWARD_SESSION_GAP"),
    (lambda r: r["observed_sessions"].pop(10), "FORWARD_SESSION_GAP"),
    (lambda r: r.update(registered_at="2030-01-02T15:00:00Z"), "FORWARD_REGISTRATION_LATE"),
    (lambda r: r.update(observed_sha256="c" * 64), "FORWARD_IDENTITY_DRIFT"),
    (lambda r: r.update(unsealed_early=True), "FORWARD_ACCESS_UNPROVEN"),
    (lambda r: r.update(pending_fill=True), "FORWARD_PENDING_FILL"),
    (lambda r: r.update(hard_expiry="2032-01-02"), "FORWARD_EXPIRY"),
])
def test_forward_gates_no_clock_started(mutate, code):
    r = forward_receipt(); mutate(r)
    with pytest.raises(package.NewEvidenceError, match=code): package.validate_forward_receipt(r)


def test_short_calendar_year_and_wrong_calendar_cannot_qualify():
    r = forward_receipt()
    r["last_session"] = "2030-12-31"
    with pytest.raises(package.NewEvidenceError, match="FORWARD_MONTHS"):
        package.validate_forward_receipt(r)
    # Validators do not attest that arbitrary dates are official XNYS sessions.
    assert package.validate_forward_receipt(forward_receipt())["calendar_authenticity"] == "UNKNOWN"


def test_observed_seam_validates_then_stops_without_replay(monkeypatch):
    from us_equity_strategies.research import optimized_strategy_replay as replay
    def forbidden(*a, **k): pytest.fail("economic replay must not run")
    monkeypatch.setattr(replay, "_replay_optimized_strategy", forbidden)
    with pytest.raises(package.NewEvidenceError, match="IDENTITY_UNKNOWN"):
        replay.replay_new_observed_research({})
    manifest = admission()
    with pytest.raises(package.NewEvidenceError, match="PRODUCER_COMPATIBILITY_UNKNOWN"):
        replay.replay_new_observed_research(manifest)
    for section, key in [("identity", "caller_sha256"), ("provenance", "source_bound"),
                         ("retention", "evaluator_readback"), ("identity", "producer_sha256")]:
        m = deepcopy(manifest); m[section][key] = None
        with pytest.raises(package.NewEvidenceError): replay.replay_new_observed_research(m)


def test_no_fixture_evidence_or_swapped_costs_can_enter_observed_seam():
    m = admission()
    m["evidence_origin"] = "fixture"
    with pytest.raises(package.NewEvidenceError, match="PROVENANCE_INVALID"):
        package.validate_observed_admission(m)
    m = admission(); m["logical_paths"][0]["cost_bps"] = 1
    with pytest.raises(package.NewEvidenceError, match="PATH_IDENTITY_MISMATCH"):
        package.validate_observed_admission(m)


def _claim(root, queue):
    try: package.persist_fake_attempt(Path(root), "fake-attempt"); queue.put("created")
    except package.NewEvidenceError as e: queue.put(e.code)


def test_two_processes_restart_and_tampered_readback(tmp_path):
    ctx = multiprocessing.get_context("spawn"); q = ctx.Queue()
    processes = [ctx.Process(target=_claim, args=(str(tmp_path), q)) for _ in range(2)]
    for p in processes: p.start()
    for p in processes: p.join(20); assert p.exitcode == 0
    assert sorted(q.get(timeout=2) for _ in processes) == ["ATTEMPT_EXISTS", "created"]
    with pytest.raises(package.NewEvidenceError, match="ATTEMPT_EXISTS"):
        package.persist_fake_attempt(tmp_path, "fake-attempt")
    folder = tmp_path / "fake-attempt"
    assert package.read_fake_attempt(folder)["execution_authorized"] is False
    (folder / "terminal.json").write_text('{}')
    with pytest.raises(package.NewEvidenceError, match="RETENTION_UNPROVEN"):
        package.read_fake_attempt(folder)


def test_symlink_and_incomplete_attempt_fail_closed(tmp_path):
    (tmp_path / "fake-incomplete").mkdir()
    with pytest.raises(package.NewEvidenceError, match="RETENTION_UNPROVEN"):
        package.read_fake_attempt(tmp_path / "fake-incomplete")
    (tmp_path / "fake-link").symlink_to(tmp_path / "fake-incomplete", target_is_directory=True)
    with pytest.raises(package.NewEvidenceError, match="RETENTION_UNPROVEN"):
        package.read_fake_attempt(tmp_path / "fake-link")


def test_preflight_and_cli_never_echo_supplied_private_values(capsys):
    assert package.main(["preflight"]) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["execution_authorized"] is False
    assert result["status"] == "PREFLIGHT_BLOCKED"
    assert "PRODUCER_COMPATIBILITY_UNKNOWN" in result["reason_codes"]
    for command in ("collect", "compare", "register-forward", "observe-session", "finalize", "freeze"):
        assert package.main([command]) == 2
        assert "DATA_EXECUTION_NOT_AUTHORIZED" in capsys.readouterr().out
    assert package.main(["unexpected-private-value@example.invalid"]) == 2
    assert "unexpected-private-value" not in capsys.readouterr().out


def test_manual_workflow_has_no_execution_or_credentials():
    text = (Path(__file__).parents[2] / ".github/workflows/new-tqqq-soxl-evidence.yml").read_text()
    assert "workflow_dispatch:" in text and "schedule:" not in text
    assert text.count("default: false") == 2
    assert "secrets." not in text and "id-token:" not in text
    assert "if-no-files-found: error" in text


def fake_context(config, *, soxl=False, artifact=None):
    from quant_platform_kit.common.models import PortfolioSnapshot
    from quant_platform_kit.common.strategy_contracts import StrategyContext
    at = datetime(2030, 1, 2, 21, tzinfo=timezone.utc)
    market = {"benchmark_history": [{"close": 100. + i, "high": 101. + i, "low": 99. + i}
                                     for i in range(400)]}
    if soxl:
        market = {"derived_indicators": {
            "soxl": {"price": 50., "ma_trend": 40.},
            "soxx": {"price": 120., "ma_trend": 100., "ma20_slope": -.25,
                     "rsi14": 50., "rsi14_dynamic_threshold": 65., "bb_mid": 100.,
                     "bb_upper": 130., "bb_lower": 80., "realized_volatility_10": .8,
                     "realized_volatility_10_dynamic_threshold": .5,
                     "realized_volatility_10_dynamic_sample_count": 252.}}}
    metadata = {"observed_effective_exposure": 0.}
    if artifact is not None: metadata["market_regime_control"] = artifact
    return StrategyContext(as_of=at, runtime_config=deepcopy(config), market_data=market,
                           portfolio=PortfolioSnapshot(as_of=at, total_equity=100000.,
                               buying_power=100000., cash_balance=100000., positions=(), metadata=metadata))


def fake_artifact():
    return {"plugin": "market_regime_control", "schema_version": "market_regime_control.v1",
            "as_of": "2030-01-02", "execution_controls": {"position_control_allowed": True,
                "consumption_evidence_status": "research_backtest_approved"},
            "position_control": {"final_route": "risk_off", "route_source": "fake-test",
                "suggested_action": "defend", "risk_budget_scalar": 0., "leverage_scalar": 0.,
                "risk_asset_scalar": 0., "crisis_defense_required": True,
                "volatility_delever_context": {"actionable_for_position_control": True,
                    "hard_risk": False, "soft_risk": False, "constructive": True, "rebound_confirm": True}}}


def test_new_treatment_controls_exercise_actual_current_builders(monkeypatch):
    import us_equity_strategies.entrypoints as entry
    definition = package.package_definition()
    for i, c in enumerate(definition["candidates"]):
        seen = []
        name = "_build_soxl_soxx_trend_income_decision" if i else "_build_tqqq_growth_income_decision"
        original = getattr(entry, name)
        def observe(ctx):
            result = original(ctx); seen.append(result); return result
        with monkeypatch.context() as patcher:
            patcher.setattr(entry, name, observe)
            on_ctx = fake_context(c["treatment"], soxl=bool(i), artifact=fake_artifact())
            off_ctx = fake_context(c["control"], soxl=bool(i))
            if i:
                on = getattr(entry, name)(entry._research_only_market_regime_context(on_ctx))
            else:
                on = entry.build_tqqq_core_only_p2_benchmark_guard_research_decision(on_ctx)
            off = getattr(entry, c["control_caller"])(off_ctx)
        assert len(seen) == 2
        assert [(x.symbol, x.target_value) for x in on.positions] != [(x.symbol, x.target_value) for x in off.positions]
        assert on.diagnostics["market_regime_control_applied" if i else "dual_drive_crisis_defense_applied"] is True
        assert fake_artifact()["execution_controls"]["consumption_evidence_status"] == "research_backtest_approved"


def test_existing_fixture_entry_still_rejects_real_evidence():
    from us_equity_strategies.research.optimized_strategy_replay import (
        ReplayRequest, ReplayIdentity, replay_optimized_strategy, OptimizedStrategyReplayError,
    )
    # Rejection must precede any price, configuration, or economic-loop access.
    r = ReplayRequest(identity=ReplayIdentity("tqqq_growth_income", "us_equity", "fake-new", "fake-source"),
        runtime_config={}, calendar=(), initial_cash=100000., initial_quantities={}, prices=(),
        execution=None, cost_model=None, computed_at="2030-01-02T22:00:00Z", evidence_use="NEW",
        promotion_eligible=False, calendar_id="XNYS", periods_per_year=252.)
    with pytest.raises(OptimizedStrategyReplayError, match="REAL_ENTRY_PIT_REQUIRED"):
        replay_optimized_strategy(r)


@pytest.mark.parametrize("field", ["open", "high", "low"])
def test_every_ohlc_verifier_field_must_agree(field):
    b = bundle()
    for row in b["rows"]:
        for key in ("open", "high", "low"):
            row["verifier_" + key] = row[key]
    b["rows"][0]["verifier_" + field] += .02
    with pytest.raises(package.NewEvidenceError, match="SOURCE_DISAGREEMENT"):
        package.validate_assurance(b)


def test_unknown_retention_and_private_invalid_arguments_are_fixed_codes():
    m = admission(); m["retention"]["months_from_registration"] = "fake-private@example.invalid"
    with pytest.raises(package.NewEvidenceError, match="^RETENTION_UNPROVEN$"):
        package.validate_observed_admission(m)


@pytest.mark.parametrize("first_open", [
    "2031-02-03T14:30:00Z", "2030-01-03T14:30:00Z",
    "2030-01-01T14:30:00Z", "invalid", None,
    "2030-01-02T14:30:00+00:00",
])
def test_forward_first_open_must_bind_first_expected_session(first_open):
    r = forward_receipt()
    r["first_open_at"] = first_open
    if first_open == "2031-02-03T14:30:00Z":
        r["registered_at"] = "2031-02-02T00:00:00Z"
    with pytest.raises(package.NewEvidenceError, match="FORWARD_REGISTRATION_LATE"):
        package.validate_forward_receipt(r)


@pytest.mark.parametrize("registered,accepted", [
    ("2030-01-01T00:00:00Z", True),
    ("2030-01-02T14:29:59Z", True),
    ("2030-01-02T14:30:00Z", False),
    ("2030-01-02T14:30:01Z", False),
])
def test_forward_matching_open_registration_order(registered, accepted):
    r = forward_receipt()
    r["registered_at"] = registered
    if accepted:
        assert package.validate_forward_receipt(r)["calendar_authenticity"] == "UNKNOWN"
    else:
        with pytest.raises(package.NewEvidenceError, match="FORWARD_REGISTRATION_LATE"):
            package.validate_forward_receipt(r)


@pytest.mark.parametrize("field,value", [
    ("first_session", "2030-01-01"), ("last_session", "2031-01-03"),
])
def test_forward_session_endpoints_stay_bound(field, value):
    r = forward_receipt()
    r[field] = value
    with pytest.raises(package.NewEvidenceError, match="FORWARD_SESSION_GAP"):
        package.validate_forward_receipt(r)
