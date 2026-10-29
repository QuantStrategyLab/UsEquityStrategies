from __future__ import annotations

from quant_platform_kit.common.strategy_contracts import StrategyContext

from us_equity_strategies.entrypoints._common import (
    apply_market_regime_control_to_weights as _apply_market_regime_control_to_weights,
)



def _binding(payload: dict[str, object]) -> dict[str, object]:
    import hashlib
    import json
    return {"plugin": "market_regime_control", "schema_version": "market_regime_control.v1",
            "as_of": payload["as_of"], "valid_until": "2026-12-31",
            "payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False).encode()).hexdigest()}


def apply_market_regime_control_to_weights(weights, *, market_regime_control_config, ctx, **kwargs):
    config = dict(market_regime_control_config)
    payload = ctx.artifacts.get("market_regime_control")
    if isinstance(payload, dict) and payload.get("schema_version") == "market_regime_control.v1":
        try:
            config.setdefault("market_regime_control_expected", _binding(payload))
        except ValueError:
            pass
    return _apply_market_regime_control_to_weights(weights, market_regime_control_config=config, ctx=ctx, **kwargs)

def _market_regime_payload(route: str, scalar: float, *, authorized: bool = True) -> dict[str, object]:
    return {
        "plugin": "market_regime_control",
        "as_of": "2026-03-31",
        "schema_version": "market_regime_control.v1",
        "canonical_route": route,
        "suggested_action": "delever" if route == "risk_reduced" else "defend",
        "execution_controls": {
            "position_control_allowed": authorized,
            "consumption_evidence_status": "automation_approved" if authorized else "notification_only",
        },
        "consumption_policy": {
            "position_control_allowed": authorized,
            "evidence_status": "automation_approved" if authorized else "notification_only",
        },
        "position_control": {
            "final_route": route,
            "suggested_action": "delever" if route == "risk_reduced" else "defend",
            "route_source": "macro",
            "risk_budget_scalar": scalar,
            "risk_asset_scalar": scalar,
            "reason_codes": ("macro:vix_crisis_level",),
        },
        "localized_messages": {
            "schema_version": "strategy_plugin_messages.v1",
            "notification": {
                "en-US": "Notification required: market regime risk reduced.",
                "zh-CN": "需要通知：市场状态风险降低。",
            },
            "log": {
                "en-US": "route=risk_reduced action=delever",
                "zh-CN": "路线=risk_reduced 动作=delever",
            },
        },
        "log_record": {
            "schema_version": "strategy_plugin_log.v1",
            "event": "strategy_plugin_signal",
            "localized_messages": {"zh-CN": "路线=risk_reduced 动作=delever"},
        },
        "notification": {
            "localized_message_schema_version": "strategy_plugin_messages.v1",
            "localized_messages": {"zh-CN": "需要通知：市场状态风险降低。"},
        },
    }


def _core_only_unavailable_regime() -> dict[str, object]:
    unavailable = {"enabled": False, "available": False}
    return {
        "profile": "market_regime_control",
        "schema_version": "soxl_core_only_market_regime_unavailable.v1",
        "as_of": "2026-05-28",
        "candidate_id": "SOXL_P3_CORE_ONLY_9_INPUT_V1",
        "market_regime_control_enabled": False,
        "component_signals": {
            name: dict(unavailable)
            for name in (
                "crisis",
                "macro",
                "taco",
                "panic_reversal",
                "volatility_delever_price_rebound",
            )
        },
        "execution_controls": {
            "position_control_allowed": False,
            "consumption_evidence_status": "static_research_only",
        },
    }


def test_market_regime_control_weight_consumer_scales_risk_weight_to_safe_haven() -> None:
    weights, diagnostics = apply_market_regime_control_to_weights(
        {"AAPL": 0.80, "BOXX": 0.20},
        market_regime_control_config={
            "market_regime_control_enabled": True,
            "market_regime_control_risk_reduced_scalar": 0.50,
        },
        ctx=StrategyContext(
            as_of="2026-05-28",
            artifacts={"market_regime_control": _market_regime_payload("risk_reduced", 0.0)},
        ),
        safe_haven="BOXX",
    )

    assert weights is not None
    assert weights["AAPL"] == 0.40
    assert round(weights["BOXX"], 10) == 0.60
    assert diagnostics["market_regime_control_applied"] is True
    assert diagnostics["market_regime_control_position_control_authorized"] is True
    assert diagnostics["market_regime_control_removed_weight"] == 0.40
    assert diagnostics["market_regime_control_risk_symbols"] == ("AAPL",)
    market_regime_notice = diagnostics["market_regime_control_notification_context"]["risk_controls"][
        "market_regime_control"
    ]
    assert market_regime_notice["localized_messages"]["schema_version"] == "strategy_plugin_messages.v1"
    assert market_regime_notice["log_record"]["schema_version"] == "strategy_plugin_log.v1"
    assert market_regime_notice["notification"]["localized_message_schema_version"] == "strategy_plugin_messages.v1"


def test_market_regime_control_weight_consumer_can_leave_risk_reduced_notification_only() -> None:
    weights, diagnostics = apply_market_regime_control_to_weights(
        {"AAPL": 0.80, "BOXX": 0.20},
        market_regime_control_config={
            "market_regime_control_enabled": True,
            "market_regime_control_apply_risk_reduced": False,
        },
        ctx=StrategyContext(
            as_of="2026-05-28",
            artifacts={"market_regime_control": _market_regime_payload("risk_reduced", 0.0)},
        ),
        safe_haven="BOXX",
    )

    assert weights == {"AAPL": 0.80, "BOXX": 0.20}
    assert diagnostics["market_regime_control_active"] is True
    assert diagnostics["market_regime_control_route_allowed"] is False
    assert diagnostics["market_regime_control_applied"] is False


def test_market_regime_control_weight_consumer_does_not_apply_unapproved_position_control() -> None:
    weights, diagnostics = apply_market_regime_control_to_weights(
        {"AAPL": 0.80, "BOXX": 0.20},
        market_regime_control_config={
            "market_regime_control_enabled": True,
            "market_regime_control_risk_reduced_scalar": 0.50,
        },
        ctx=StrategyContext(
            as_of="2026-05-28",
            artifacts={"market_regime_control": _market_regime_payload("risk_reduced", 0.0, authorized=False)},
        ),
        safe_haven="BOXX",
    )

    assert weights == {"AAPL": 0.80, "BOXX": 0.20}
    assert diagnostics["market_regime_control_found"] is True
    assert diagnostics["market_regime_control_active"] is False
    assert diagnostics["market_regime_control_position_control_allowed"] is False
    assert diagnostics["market_regime_control_position_control_authorized"] is False
    assert diagnostics["market_regime_control_consumption_evidence_status"] == "notification_only"
    assert diagnostics["market_regime_control_applied"] is False


def test_core_only_unavailable_regime_cannot_change_weights_when_consumer_is_enabled() -> None:
    weights, diagnostics = apply_market_regime_control_to_weights(
        {"SOXL": 0.70, "SOXX": 0.20, "BOXX": 0.10},
        market_regime_control_config={"market_regime_control_enabled": True},
        ctx=StrategyContext(
            as_of="2026-05-28",
            artifacts={"market_regime_control": _core_only_unavailable_regime()},
        ),
        safe_haven="BOXX",
    )

    assert weights is None
    assert diagnostics["market_regime_control_binding_valid"] is False


def test_regime_scalar_values_and_cash_preservation() -> None:
    for route in ("risk_reduced", "risk_off"):
        for scalar, expected in ((0, 0), (0.25, 0.2), (1, 0.8), (None, 0.8)):
            weights, diagnostics = apply_market_regime_control_to_weights(
                {"AAPL": 0.8}, market_regime_control_config={"market_regime_control_enabled": True},
                ctx=StrategyContext(as_of="2026-05-28", artifacts={"market_regime_control": _market_regime_payload(route, scalar)}),
                safe_haven=None,
            )
            assert weights == {"AAPL": expected}
            assert diagnostics["market_regime_control_scalar_valid"] is True


def test_invalid_scalar_makes_authorized_decision_non_actionable() -> None:
    for route in ("risk_reduced", "risk_off"):
        for invalid in (float("nan"), float("inf"), float("-inf"), True, "bad", -0.1, 1.1):
            for config in ({}, {f"market_regime_control_{route}_scalar": 0.5}):
                weights, diagnostics = apply_market_regime_control_to_weights(
                    {"AAPL": 0.8, "BOXX": 0.2},
                    market_regime_control_config={"market_regime_control_enabled": True, **config},
                    ctx=StrategyContext(as_of="2026-05-28", artifacts={"market_regime_control": _market_regime_payload(route, invalid)}),
                    safe_haven="BOXX",
                )
                assert weights is None
                assert diagnostics.get("market_regime_control_scalar_valid", diagnostics.get("market_regime_control_binding_valid")) is False
        weights, diagnostics = apply_market_regime_control_to_weights(
            {"AAPL": 0.8}, market_regime_control_config={"market_regime_control_enabled": True, f"market_regime_control_{route}_scalar": "bad"},
            ctx=StrategyContext(as_of="2026-05-28", artifacts={"market_regime_control": _market_regime_payload(route, 0.25)}),
            safe_haven=None,
        )
        assert weights is None
        assert diagnostics.get("market_regime_control_scalar_valid", diagnostics.get("market_regime_control_binding_valid")) is False


def test_global_etf_real_entrypoint_preserves_zero_and_invalid_is_no_execute() -> None:
    from unittest.mock import patch
    from us_equity_strategies import get_strategy_entrypoint
    from tests.test_entrypoints import _global_etf_snapshot

    for scalar in (0, float('nan')):
        ctx = StrategyContext(
            as_of='2026-03-31', market_data={'feature_snapshot': _global_etf_snapshot()},
            state={'current_holdings': ()},
            runtime_config={'translator': lambda key, **kwargs: key, 'income_layer_enabled': False,
                            'market_regime_control_enabled': True, 'market_regime_control_apply_risk_reduced': True,
                            'market_regime_control_risk_reduced_scalar': None,
                            'market_regime_control_expected': _binding(_market_regime_payload('risk_reduced', scalar)) if scalar == 0 else None},
            artifacts={'market_regime_control': _market_regime_payload('risk_reduced', scalar)},
        )
        with patch('us_equity_strategies.entrypoints.legacy_global_etf_rotation.compute_signals_from_feature_snapshot',
                   return_value=({'SMH': 0.8, 'BIL': 0.2}, '', False, '')), patch(
                'us_equity_strategies.entrypoints.apply_risk_gate', side_effect=lambda decision, **kwargs: decision):
            decision = get_strategy_entrypoint('global_etf_rotation').evaluate(ctx)
        if scalar == 0:
            assert {p.symbol: p.target_weight for p in decision.positions} == {'BIL': 1.0, 'SMH': 0.0}
        else:
            assert 'no_execute' in decision.risk_flags
            assert decision.positions == ()


def test_global_etf_observer_receipt_is_read_only_and_bound() -> None:
    from unittest.mock import patch
    from quant_strategy_plugins.plugin_signal_envelope_v2 import build_signal_envelope
    from us_equity_strategies import get_strategy_entrypoint
    from tests.test_entrypoints import _global_etf_snapshot

    producer = {'repo': 'QuantStrategyLab/QuantStrategyPlugins', 'revision': 'a' * 40,
                'entrypoint': 'quant_strategy_plugins.qqq_price_regime_observer_v2:build_qqq_price_regime_signal_v2',
                'code_sha256': 'b' * 64, 'config_sha256': 'c' * 64}
    inputs = {'p1_manifest_sha256': 'd' * 64, 'input_root_sha256': 'e' * 64, 'date_cutoff': '2026-03-31'}
    signal = build_signal_envelope(plugin_id='qqq_price_regime_observer', producer=producer,
                                   input_provenance=inputs, payload={'as_of': '2026-03-31', 'facts': {'trend': 'up'}})
    sentinel = object()
    def evaluate(artifact, *, omit_digest=False, digest_value=sentinel):
        binding = {'producer': producer, 'input': inputs}
        if not omit_digest:
            binding['payload_sha256'] = signal['payload_sha256'] if digest_value is sentinel else digest_value
        ctx = StrategyContext(as_of='2026-03-31', market_data={'feature_snapshot': _global_etf_snapshot()},
            state={'current_holdings': ()}, artifacts={'qqq_price_regime_observer': artifact},
            runtime_config={'translator': lambda key, **kwargs: key, 'income_layer_enabled': False,
                'qqq_observer_expected_binding': binding})
        with patch('us_equity_strategies.entrypoints.legacy_global_etf_rotation.compute_signals_from_feature_snapshot',
                   return_value=({'SMH': 0.8, 'BIL': 0.2}, '', False, '')), patch(
                'us_equity_strategies.entrypoints.apply_risk_gate', side_effect=lambda decision, **kwargs: decision):
            return get_strategy_entrypoint('global_etf_rotation').evaluate(ctx)
    accepted = evaluate(signal)
    missing_digest = evaluate(signal, omit_digest=True)
    corrupted = evaluate({**signal, 'payload': {'changed': True}})
    from quant_strategy_plugins.plugin_signal_envelope_v2 import payload_sha256
    rewritten = {'changed': True}
    rehashed = evaluate({**signal, 'payload': rewritten, 'payload_sha256': payload_sha256(rewritten)})
    assert accepted.diagnostics['qqq_observer_status'] == 'consumed_read_only'
    assert accepted.diagnostics['qqq_observer_receipt']['payload_sha256'] == signal['payload_sha256']
    assert missing_digest.diagnostics['qqq_observer_status'] == 'unavailable_or_invalid'
    for invalid_digest in (None, '', True, 'bad', 'A' * 64):
        assert evaluate(signal, digest_value=invalid_digest).diagnostics['qqq_observer_status'] == 'unavailable_or_invalid'
    assert corrupted.diagnostics['qqq_observer_status'] == 'unavailable_or_invalid'
    assert rehashed.diagnostics['qqq_observer_status'] == 'unavailable_or_invalid'
    assert accepted.positions == rehashed.positions
    assert accepted.positions == corrupted.positions


def test_v1_requires_independent_matching_unexpired_binding() -> None:
    payload = _market_regime_payload('risk_reduced', 0.25)
    ctx = StrategyContext(as_of='2026-05-28', artifacts={'market_regime_control': payload})
    valid = _binding(payload)
    for binding in (None, {**valid, 'payload_sha256': '0' * 64},
                    {**valid, 'valid_until': '2026-04-01'}, {**valid, 'schema_version': 'wrong'}):
        weights, diagnostics = _apply_market_regime_control_to_weights(
            {'AAPL': 0.8}, market_regime_control_config={'market_regime_control_enabled': True,
                'market_regime_control_expected': binding}, ctx=ctx, safe_haven=None)
        assert weights is None
        assert diagnostics['market_regime_control_binding_valid'] is False
    weights, _ = _apply_market_regime_control_to_weights(
        {'AAPL': 0.8}, market_regime_control_config={'market_regime_control_enabled': True,
            'market_regime_control_expected': valid}, ctx=ctx, safe_haven=None)
    assert weights == {'AAPL': 0.2}


def test_global_etf_snapshot_excludes_future_late_and_revised_data() -> None:
    import pandas as pd
    from tests.test_entrypoints import _global_etf_snapshot
    from us_equity_strategies.strategies.global_etf_rotation import compute_signals_from_feature_snapshot
    base = _global_etf_snapshot()
    for column in ('available_at', 'received_at', 'revised_at'):
        base[column] = '2026-03-30T12:00:00Z'
    future = base.iloc[[0]].copy()
    future['as_of'] = '2026-04-30'
    late = base.iloc[[0]].copy()
    late['received_at'] = '2026-04-01T00:00:00Z'
    revision = base.iloc[[0]].copy()
    revision['revised_at'] = '2026-04-01T00:00:00Z'
    for frame in (future, late, revision):
        frame['score'] = -999
    kwargs = dict(as_of_date='2026-03-31T20:00:00Z', translator=lambda key, **kwargs: key)
    assert compute_signals_from_feature_snapshot(pd.concat([base, future, late, revision]), (), **kwargs) == compute_signals_from_feature_snapshot(base, (), **kwargs)


def test_global_etf_emergency_survives_invalid_plugin_binding() -> None:
    from unittest.mock import patch
    from us_equity_strategies import get_strategy_entrypoint
    from tests.test_entrypoints import _global_etf_snapshot

    ctx = StrategyContext(
        as_of='2026-03-31', market_data={'feature_snapshot': _global_etf_snapshot()},
        state={'current_holdings': ('SMH',)},
        runtime_config={'translator': lambda key, **kwargs: key, 'income_layer_enabled': False,
                        'market_regime_control_enabled': True},
        artifacts={'market_regime_control': _market_regime_payload('risk_off', 0)},
    )
    with patch('us_equity_strategies.entrypoints.legacy_global_etf_rotation.compute_signals_from_feature_snapshot',
               return_value=({'BIL': 1.0}, '', True, '')), patch(
            'us_equity_strategies.entrypoints.apply_risk_gate', side_effect=lambda decision, **kwargs: decision):
        decision = get_strategy_entrypoint('global_etf_rotation').evaluate(ctx)
    assert decision.diagnostics['market_regime_control_binding_valid'] is False
    assert 'emergency' in decision.risk_flags
    assert 'no_execute' not in decision.risk_flags
    assert {p.symbol: p.target_weight for p in decision.positions} == {'BIL': 1.0}
