from us_equity_strategies.strategies.tqqq_growth_income_live_v2 import (
    _is_taco_rebound_context_active,
    _is_true_crisis_active,
    _resolve_macro_risk_governor_context,
)


def test_shadow_metadata_never_drives_legacy_controls():
    controls = {"position_control_allowed": False}
    cases = [
        ({"plugin": "macro_risk_governor", "canonical_route": "delever"},
         lambda value: _resolve_macro_risk_governor_context(value)["active"]),
        ({"plugin": "crisis_response_shadow", "canonical_route": "true_crisis"},
         _is_true_crisis_active),
        ({"plugin": "taco_rebound_shadow", "canonical_route": "taco_fake_crisis"},
         _is_taco_rebound_context_active),
    ]
    for payload, resolve in cases:
        payload.update(strategy_runtime_metadata_non_authoritative=True, execution_controls=controls)
        assert not resolve({"plugin_signals": [payload]})


def test_shadow_authority_flags_are_typed_and_inherited():
    payload = {"strategy_runtime_metadata_non_authoritative": "false", "nested": {
        "plugin": "macro_risk_governor", "canonical_route": "delever"}}
    assert not _resolve_macro_risk_governor_context(payload)["active"]
    payload = {"execution_controls": {"position_control_allowed": "true"}, "nested": {
        "canonical_route": "true_crisis"}}
    assert not _is_true_crisis_active(payload)


def test_signal_source_time_and_cache_partition():
    from datetime import datetime, timezone
    from pathlib import Path
    from us_equity_strategies.signals.runtime_market_signal_inputs import (
        _last_valid_market_signal_inputs_path,
        _last_valid_market_signal_stale_reason,
    )

    source = {"signal_session": "2026-10-30", "available_at": "2026-10-30T18:00:00+00:00",
              "bundle_id": "bundle", "signal_bundle_manifest_sha256": "a" * 64}
    assert _last_valid_market_signal_stale_reason(source, max_stale_days=3, as_of="2020-03-16")
    assert _last_valid_market_signal_stale_reason(
        {**source, "signal_session": "2020-03-16", "available_at": "2020-03-16T18:00:00+00:00"},
        max_stale_days=3, as_of="2026-10-30")
    assert _last_valid_market_signal_stale_reason(source, max_stale_days=3, as_of="2026-10-30",
        decision_time=datetime(2026, 10, 30, 17, tzinfo=timezone.utc))
    assert _last_valid_market_signal_stale_reason(source, max_stale_days=3, as_of="2026-10-30",
        decision_time=datetime(2026, 10, 30, 19, tzinfo=timezone.utc)) is None
    args = dict(reference="index", reference_type="platform_handoff_index", consumer="x", cache_dir=Path("/tmp"))
    assert _last_valid_market_signal_inputs_path(**args, cache_namespace="runtime") != _last_valid_market_signal_inputs_path(**args, cache_namespace="replay")


def test_null_required_volatility_rejected():
    from us_equity_strategies.signals.runtime_market_signal_inputs import (
        SOXL_SOXX_TREND_INCOME_MARKET_SIGNAL_CONSUMER,
        _validate_market_signal_inputs_for_consumer,
        required_indicator_fields_for_consumer,
    )
    consumer = SOXL_SOXX_TREND_INCOME_MARKET_SIGNAL_CONSUMER
    indicators = {symbol: {field: 1.0 for field in fields}
                  for symbol, fields in required_indicator_fields_for_consumer(consumer).items()}
    indicators["SOXX"]["realized_volatility_10"] = None
    try:
        _validate_market_signal_inputs_for_consumer({"derived_indicators": indicators}, consumer=consumer)
    except ValueError:
        pass
    else:
        raise AssertionError("null volatility accepted")


def test_soxl_legacy_shadow_crisis_is_not_authoritative():
    from us_equity_strategies.strategies.soxl_soxx_trend_income_live_v2 import (
        _resolve_legacy_crisis_response_context,
    )
    payload = {"plugin": "crisis_response_shadow", "canonical_route": "true_crisis",
               "strategy_runtime_metadata_non_authoritative": True,
               "execution_controls": {"position_control_allowed": False}}
    assert not _resolve_legacy_crisis_response_context({"plugin": payload})["active"]


def test_regime_control_requires_typed_authority():
    from us_equity_strategies.market_regime_control_contract import (
        resolve_market_regime_position_control_authorization,
    )
    controls = {"position_control_allowed": True, "consumption_evidence_status": "automation_approved"}
    assert resolve_market_regime_position_control_authorization({"execution_controls": controls})["position_control_authorized"]
    assert not resolve_market_regime_position_control_authorization({"execution_controls": {**controls, "position_control_allowed": "true"}})["position_control_authorized"]
    assert not resolve_market_regime_position_control_authorization({"execution_controls": controls, "strategy_runtime_metadata_non_authoritative": True})["position_control_authorized"]
    assert not resolve_market_regime_position_control_authorization({"execution_controls": controls, "runtime_consumption": "invalid"})["position_control_authorized"]
    from us_equity_strategies.market_regime_control_contract import iter_authoritative_legacy_payloads
    nested = {"runtime_consumption": "invalid", "nested": {"plugin": "market_regime_control", "execution_controls": controls}}
    assert list(iter_authoritative_legacy_payloads(nested)) == []


def test_last_valid_cache_rejects_mutation_and_legacy_records():
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from us_equity_strategies.signals.runtime_market_signal_inputs import (
        SOXL_SOXX_TREND_INCOME_MARKET_SIGNAL_CONSUMER,
        _last_valid_market_signal_inputs_path,
        _load_last_valid_market_signal_inputs,
        _write_last_valid_market_signal_inputs,
        required_indicator_fields_for_consumer,
    )
    consumer = SOXL_SOXX_TREND_INCOME_MARKET_SIGNAL_CONSUMER
    indicators = {symbol: {field: 1.0 for field in fields}
                  for symbol, fields in required_indicator_fields_for_consumer(consumer).items()}
    metadata = {"signal_session": "2026-10-30", "available_at": "2026-10-30T18:00:00+00:00",
                "bundle_id": "bundle", "signal_bundle_manifest_sha256": "a" * 64}
    with TemporaryDirectory() as directory:
        base = dict(reference="index", reference_type="platform_handoff_index", consumer=consumer,
                    cache_dir=Path(directory), as_of="2026-10-30")
        _write_last_valid_market_signal_inputs(**base, market_inputs={"derived_indicators": indicators}, metadata=metadata)
        load = dict(**base, failed_exc=RuntimeError("unavailable"), max_stale_days=3,
                    decision_time=datetime(2026, 10, 31, 15, tzinfo=timezone.utc))
        assert _load_last_valid_market_signal_inputs(**load) is not None
        assert _load_last_valid_market_signal_inputs(**{**load, "as_of": "2026-10-31"}) is not None
        assert _load_last_valid_market_signal_inputs(**{**load, "cache_namespace": "replay"}) is None
        path = _last_valid_market_signal_inputs_path(**{k: v for k, v in base.items() if k != "as_of"})
        payload = json.loads(path.read_text())
        payload["signal_session"] = "2026-10-31"
        path.write_text(json.dumps(payload))
        assert _load_last_valid_market_signal_inputs(**load) is None
        payload["schema_version"] = "market_signal_last_valid_inputs.v1"
        path.write_text(json.dumps(payload))
        assert _load_last_valid_market_signal_inputs(**load) is None


def test_authorized_controls_remain_compatible_with_tecl_and_ibit():
    from types import SimpleNamespace
    from us_equity_strategies.strategies.tecl_xlk_trend_income import _resolve_market_regime_control_context
    from us_equity_strategies.strategies.ibit_smart_dca import _resolve_ibit_zscore_exit_context

    controls = {"position_control_allowed": True, "consumption_evidence_status": "automation_approved"}
    payload = {"plugin": "market_regime_control", "canonical_route": "risk_off",
               "execution_controls": controls, "position_control": {"final_route": "risk_off"}}
    assert _resolve_market_regime_control_context(payload)["position_control_authorized"]
    ibit_payload = {**payload, "plugin": "ibit_zscore_exit"}
    kwargs = dict(enabled=True, mode="live", parking_symbol="BOXX", risk_reduced_exposure=0.5,
                  risk_off_exposure=0.0)
    result = _resolve_ibit_zscore_exit_context(SimpleNamespace(metadata={}), explicit_context=ibit_payload, **kwargs)
    assert result["position_control_authorized"]
    denied = {**ibit_payload, "strategy_runtime_metadata_non_authoritative": True}
    result = _resolve_ibit_zscore_exit_context(SimpleNamespace(metadata={}), explicit_context=denied, **kwargs)
    assert not result["position_control_authorized"]
