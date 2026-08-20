from __future__ import annotations

import hashlib
import json

import pytest

from us_equity_strategies.research.soxl_core_only_p3_source_runner import (
    ENTRYPOINT,
    INPUT_SCHEMA,
    OUTPUT_SCHEMA,
    SoxlCoreOnlyP3SourceRunnerError,
    main,
    run_soxl_core_only_p3_source,
)


def _runtime_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "managed_symbols": ["SOXL", "SOXX", "BOXX"],
        "income_layer_enabled": False,
        "option_overlay_enabled": False,
        "option_growth_overlay_enabled": False,
        "option_income_overlay_enabled": False,
        "blend_gate_volatility_delever_retention_mode": "none",
        "blend_gate_volatility_delever_retention_ratio": 0.0,
        "blend_gate_volatility_delever_retention_context_required": False,
        "market_regime_control_enabled": False,
        "market_regime_control_apply_risk_reduced": False,
        "market_regime_control_apply_risk_off": False,
    }
    config.update(overrides)
    return config


def _input(*, realized_volatility: float = 0.20, **runtime_overrides: object) -> dict[str, object]:
    as_of = "2026-08-20T12:00:00+00:00"
    return {
        "schema_version": INPUT_SCHEMA,
        "as_of": as_of,
        "portfolio": {
            "as_of": as_of,
            "total_equity": 100_000.0,
            "buying_power": 100_000.0,
            "cash_balance": 100_000.0,
            "positions": [],
            "metadata": {"observed_effective_exposure": 0.0},
        },
        "market_data": {
            "derived_indicators": {
                "SOXL": {"price": 80.0, "ma_trend": 75.0},
                "SOXX": {
                    "price": 109.0,
                    "ma_trend": 100.0,
                    "ma20": 105.0,
                    "ma20_slope": 1.0,
                    "rsi14": 50.0,
                    "bb_upper": 115.0,
                    "realized_volatility_10": realized_volatility,
                    "realized_volatility_10_dynamic_threshold": 0.50,
                    "realized_volatility_10_dynamic_sample_count": 252.0,
                },
            }
        },
        "runtime_config": _runtime_config(**runtime_overrides),
    }


def _digest(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def test_source_runner_returns_only_canonical_research_decision_summary() -> None:
    result = run_soxl_core_only_p3_source(_input())

    assert result["schema_version"] == OUTPUT_SCHEMA
    assert result["entrypoint"] == ENTRYPOINT
    assert result["target_values"] == pytest.approx(
        {"SOXL": 70_000.0, "SOXX": 20_000.0, "BOXX": 10_000.0}
    )
    assert result["diagnostics"] == {
        "blend_tier": "full",
        "base_blend_tier": "full",
        "active_risk_asset": "SOXX+SOXL",
        "blend_gate_volatility_delever_triggered": False,
        "blend_gate_volatility_delever_redirect_symbol": "SOXX",
        "market_regime_control_enabled": False,
        "market_regime_control_applied": False,
    }
    digest_input = dict(result)
    expected_material = {
        key: value for key, value in digest_input.items() if key != "output_sha256"
    }
    assert result["output_sha256"] == _digest(expected_material)
    assert "order" not in json.dumps(result).lower()


def test_source_runner_preserves_strategy_internal_volatility_delever() -> None:
    result = run_soxl_core_only_p3_source(_input(realized_volatility=0.80))

    assert result["target_values"] == pytest.approx(
        {"SOXL": 0.0, "SOXX": 90_000.0, "BOXX": 10_000.0}
    )
    assert result["diagnostics"]["blend_gate_volatility_delever_triggered"] is True
    assert result["diagnostics"]["blend_gate_volatility_delever_redirect_symbol"] == "SOXX"


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload["market_data"]["derived_indicators"]["SOXX"].pop("ma20"),
        lambda payload: payload["runtime_config"].update({"income_layer_enabled": True}),
        lambda payload: payload["portfolio"].update({"as_of": "2026-08-19T12:00:00+00:00"}),
    ),
)
def test_source_runner_rejects_incomplete_or_noncore_context(
    mutate,
) -> None:
    payload = _input()
    mutate(payload)

    with pytest.raises(SoxlCoreOnlyP3SourceRunnerError):
        run_soxl_core_only_p3_source(payload)


def test_cli_parks_invalid_json_without_echoing_input(tmp_path, capsys) -> None:
    source = tmp_path / "context.json"
    source.write_text('{"secret":"do-not-echo"}', encoding="utf-8")

    assert main(["--input", str(source)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output == {
        "failure_class": "strategy_context_invalid",
        "schema_version": OUTPUT_SCHEMA,
        "status": "PARKED",
    }
