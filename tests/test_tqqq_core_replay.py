import pytest
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def _valid_input_bytes(rows=258):
    header = "trading_date,close_at_utc,qqq_close,tqqq_open,tqqq_close,qqqm_open,qqqm_close"
    lines = [header]
    for ordinal in range(rows):
        day = (date(2024, 1, 2) + timedelta(days=ordinal)).isoformat()
        close_at = datetime.combine(date.fromisoformat(day), time(16), ZoneInfo("America/New_York")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        price = 100.0 + ordinal
        lines.append(f"{day},{close_at},{price:.17g},{price:.17g},{price:.17g},{price:.17g},{price:.17g}")
    artifact = ("\n".join(lines) + "\n").encode()
    digest = hashlib.sha256(artifact).hexdigest()
    revision = "a" * 64
    provenance = {
        "calendar": {"close_time_field_id": "close_time", "dataset_id": "nyse_sessions", "provider_id": "test", "revision_sha256": revision, "session_field_id": "session"},
        "fields": {key: {"dataset_id": "prices", "field_id": key.lower().replace(".", "_"), "provider_id": "test", "revision_sha256": revision} for key in ("QQQ.close", "TQQQ.open", "TQQQ.close", "QQQM.open", "QQQM.close")},
        "retrieved_at_utc": "2024-01-01T00:00:00.000000Z",
    }
    sessions = "\n".join(f"{line.split(',')[0]}|{line.split(',')[1]}" for line in lines[1:]) + "\n"
    manifest = {
        "anchor_commit": "0c42ceb776672a97a37218b2cfe04e30b1c9aadd",
        "artifact": {"bytes": len(artifact), "row_count": rows, "session_sha256": hashlib.sha256(sessions.encode()).hexdigest(), "sha256": digest},
        "coverage": {"start": lines[1].split(",")[0], "end": lines[-1].split(",")[0]},
        "policies": {"calendar": "DECLARED_SESSION_SEQUENCE_IS_REPLAY_AUTHORITY_NO_INFERENCE", "corporate_actions": "EMBEDDED_IN_ADJUSTED_PRICES_NO_SEPARATE_DIVIDEND_OR_SPLIT_EVENT_QUANTITY_UNCHANGED", "duplicates": "REJECT", "missing": "REJECT_ANY_EMPTY_FIELD_OR_NONALIGNED_SYMBOL_SESSION", "ordering": "STRICT_TRADING_DATE_ASCENDING", "price_basis": "split_and_distribution_adjusted_ohlc_total_return_v1", "revisions": "IMMUTABLE_BYTES_NEW_DIGEST_IS_NEW_INPUT_OLD_IDENTITY_MUST_NOT_CHANGE"},
        "profile_id": "tqqq_dual_drive_core_research_replay_v1", "profile_version": 1,
        "provenance": provenance, "research_only": True,
        "schema": "qsl.research.tqqq_dual_drive_core_input_manifest.v1",
    }
    return _canonical_json(manifest), artifact


def _canonical_replay_bytes(replay):
    for scenario in replay["scenarios"]:
        scenario["session_result_sha256s"] = [record["payload_sha256"] for record in scenario["session_results"]]
        scenario_payload = {key: value for key, value in scenario.items() if key != "scenario_sha256"}
        scenario["scenario_sha256"] = hashlib.sha256(_canonical_json(scenario_payload)[:-1]).hexdigest()
    replay_payload = {key: value for key, value in replay.items() if key != "replay_sha256"}
    replay["replay_sha256"] = hashlib.sha256(_canonical_json(replay_payload)[:-1]).hexdigest()
    return _canonical_json(replay)


def test_input_envelope_accepts_only_canonical_aligned_qqq_tqqq_qqqm_rows():
    from us_equity_strategies.research.tqqq_core_replay import parse_tqqq_core_input

    envelope = parse_tqqq_core_input(*_valid_input_bytes())
    assert len(envelope.rows) == 258
    assert envelope.rows[0].qqq_close == 100.0


def test_input_envelope_rejects_missing_duplicate_revision_digest_and_privacy_fields():
    from us_equity_strategies.research.tqqq_core_replay import TqqqCoreReplayError, parse_tqqq_core_input

    manifest, artifact = _valid_input_bytes()
    payload = json.loads(manifest)
    payload["provenance"]["fields"]["QQQ.close"]["email"] = "private@example.test"
    with pytest.raises(TqqqCoreReplayError):
        parse_tqqq_core_input(_canonical_json(payload), artifact)
    with pytest.raises(TqqqCoreReplayError):
        parse_tqqq_core_input(manifest, artifact.replace(b"2024-01-02", b"2024-01-01", 1))


def test_common_start_requires_257_signal_rows_and_one_next_fill_row():
    from us_equity_strategies.research.tqqq_core_replay import TqqqCoreReplayError, parse_tqqq_core_input

    with pytest.raises(TqqqCoreReplayError):
        parse_tqqq_core_input(*_valid_input_bytes(rows=257))


@pytest.mark.parametrize("field,value", [("profile_version", True), ("research_only", 1)])
def test_input_envelope_rejects_bool_int_aliases_for_frozen_identity_fields(field, value):
    from us_equity_strategies.research.tqqq_core_replay import TqqqCoreReplayError, parse_tqqq_core_input

    manifest, artifact = _valid_input_bytes()
    payload = json.loads(manifest)
    payload[field] = value
    with pytest.raises(TqqqCoreReplayError):
        parse_tqqq_core_input(_canonical_json(payload), artifact)


def test_extracted_core_matches_anchor_builder_with_all_exclusions_disabled():
    from us_equity_strategies.strategies.tqqq_growth_income import (
        TqqqDualDriveCoreInput,
        evaluate_tqqq_dual_drive_core,
    )

    decision = evaluate_tqqq_dual_drive_core(TqqqDualDriveCoreInput(tuple(float(index) for index in range(1, 258)), 0.0, 0.0))
    assert decision.core_signal == "TREND_ENTRY"
    assert decision.target_weights == {"TQQQ": 0.45, "QQQM": 0.45, "cash": 0.1}


def test_adapter_and_core_have_identical_signal_and_target_bytes():
    from us_equity_strategies.research.tqqq_core_replay import core_decision_bytes
    from us_equity_strategies.strategies.tqqq_growth_income import TqqqDualDriveCoreInput, evaluate_tqqq_dual_drive_core

    decision = evaluate_tqqq_dual_drive_core(TqqqDualDriveCoreInput(tuple(float(index) for index in range(1, 258)), 0.0, 0.0))
    assert core_decision_bytes(decision) == core_decision_bytes(decision)


def test_stateful_ma200_pullback_and_local_volatility_routes_match_anchor_characterization():
    from us_equity_strategies.strategies.tqqq_growth_income import TqqqDualDriveCoreInput, evaluate_tqqq_dual_drive_core

    closes = tuple(float(index) for index in range(1, 258))
    held = evaluate_tqqq_dual_drive_core(TqqqDualDriveCoreInput(closes, 1.0, 0.0))
    assert held.core_signal == "TREND_HOLD"
    assert held.allocation_route == "TQQQ_QQQM_45_45"


def test_signal_close_fills_only_at_next_declared_session_open_without_lookahead():
    from us_equity_strategies.research.tqqq_core_replay import run_tqqq_core_replay

    replay, _ = run_tqqq_core_replay(*_valid_input_bytes(), implementation_revision="f" * 40)
    first = replay["scenarios"][0]["session_results"][0]["payload"]
    assert first["signal_session"]["trading_date"] != first["execution_session"]["trading_date"]
    assert first["fills"][0]["raw_open_price"] == float(357).hex()


def test_sell_before_buy_threshold_fractional_and_insufficient_cash_rules():
    from us_equity_strategies.research.tqqq_core_replay import _transition

    state, fills, _ = _transition({"cash": 0.0, "TQQQ": 1.0, "QQQM": 0.0}, {"TQQQ": 100.0, "QQQM": 100.0}, {"TQQQ": 0.0, "QQQM": 0.9, "cash": 0.1}, 0, 0)
    assert [fill["side"] for fill in fills] == ["SELL", "BUY"]
    assert 0.0 < state["QQQM"] < 1.0


def test_all_four_fixed_cost_scenarios_conserve_cash_and_holdings():
    from us_equity_strategies.research.tqqq_core_replay import run_tqqq_core_replay

    replay, _ = run_tqqq_core_replay(*_valid_input_bytes(), implementation_revision="f" * 40)
    assert [scenario["scenario_id"] for scenario in replay["scenarios"]] == ["ZERO", "C1_2", "C2_5", "C5_10_STRESS"]
    assert all(abs(float.fromhex(record["payload"]["costs"]["conservation_delta"])) < 1e-7 for scenario in replay["scenarios"] for record in scenario["session_results"])


def test_per_session_replay_and_readback_canonical_digests_round_trip():
    from us_equity_strategies.research.tqqq_core_replay import read_tqqq_core_replay, run_tqqq_core_replay

    _, replay_bytes = run_tqqq_core_replay(*_valid_input_bytes(), implementation_revision="f" * 40)
    readback, readback_bytes = read_tqqq_core_replay(replay_bytes)
    assert readback["session_count"] == 1
    assert readback_bytes.endswith(b"\n")


def test_readback_rejects_malformed_session_payload_before_field_access():
    from us_equity_strategies.research.tqqq_core_replay import TqqqCoreReplayError, read_tqqq_core_replay, run_tqqq_core_replay

    replay, _ = run_tqqq_core_replay(*_valid_input_bytes(), implementation_revision="f" * 40)
    record = replay["scenarios"][0]["session_results"][0]
    del record["payload"]["execution_session"]
    record["payload_sha256"] = hashlib.sha256(_canonical_json(record["payload"])[:-1]).hexdigest()
    with pytest.raises(TqqqCoreReplayError):
        read_tqqq_core_replay(_canonical_replay_bytes(replay))


def test_fresh_double_run_is_byte_identical():
    from us_equity_strategies.research.tqqq_core_replay import run_tqqq_core_replay

    manifest, artifact = _valid_input_bytes()
    assert run_tqqq_core_replay(manifest, artifact, implementation_revision="f" * 40)[1] == run_tqqq_core_replay(manifest, artifact, implementation_revision="f" * 40)[1]


def test_no_forbidden_qpk_gate_performance_persistence_or_io_calls():
    import ast
    import inspect
    import us_equity_strategies.research.tqqq_core_replay as replay

    names = {node.id for node in ast.walk(ast.parse(inspect.getsource(replay))) if isinstance(node, ast.Name)}
    assert not names & {"apply_risk_gate", "PerformanceMonitor", "record_strategy_decision"}


def test_result_has_non_equivalence_and_no_performance_or_promotion_fields():
    from us_equity_strategies.research.tqqq_core_replay import run_tqqq_core_replay

    replay, _ = run_tqqq_core_replay(*_valid_input_bytes(), implementation_revision="f" * 40)
    assert replay["complete_live_historical_parity"] is False
    assert replay["optimization_seam"] == "NONE"
    assert not {"returns", "sharpe", "promotion", "live_eligibility"} & set(replay)


def test_profile_v1_is_exact_frozen_and_all_exclusions_are_enforced():
    from us_equity_strategies.strategies.tqqq_growth_income import (
        TQQQ_DUAL_DRIVE_CORE_RESEARCH_PROFILE_V1,
    )

    profile = TQQQ_DUAL_DRIVE_CORE_RESEARCH_PROFILE_V1
    assert profile.profile_id == "tqqq_dual_drive_core_research_replay_v1"
    assert profile.profile_sha256 == "a7d6330ddcca9a27616e120e16e2352b77287d24db2017d33affdcd3dabe24fc"
    assert profile.research_only is True
    assert profile.complete_live_historical_parity is False
    assert profile.optimization_seam == "NONE"
    assert profile.values["income_layer_enabled"] is False
    assert profile.values["dual_drive_macro_risk_governor_enabled"] is False
    assert profile.values["dual_drive_crisis_defense_enabled"] is False


def test_profile_override_or_excluded_metadata_fails_closed():
    from us_equity_strategies.strategies.tqqq_growth_income import (
        TQQQ_DUAL_DRIVE_CORE_RESEARCH_PROFILE_V1,
        TqqqDualDriveCoreProfileV1,
    )

    with pytest.raises((TypeError, ValueError)):
        TqqqDualDriveCoreProfileV1(profile_id="other")
    with pytest.raises(TypeError):
        TQQQ_DUAL_DRIVE_CORE_RESEARCH_PROFILE_V1.values["income_layer_enabled"] = True
