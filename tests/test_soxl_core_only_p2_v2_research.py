from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import StrategyContext

import us_equity_strategies.entrypoints as entrypoints


_AS_OF = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
_CORE_ASSETS = ("SOXL", "SOXX", "BOXX")
_EXCLUDED_ASSETS = ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI")


def _core_only_runtime_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "managed_symbols": _CORE_ASSETS,
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


def _context(
    *,
    realized_volatility: float = 0.20,
    runtime_config: dict[str, object] | None = None,
) -> StrategyContext:
    snapshot = PortfolioSnapshot(
        as_of=_AS_OF,
        total_equity=100_000.0,
        buying_power=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        metadata={"observed_effective_exposure": 0.0},
    )
    return StrategyContext(
        as_of=_AS_OF,
        portfolio=snapshot,
        market_data={
            "derived_indicators": {
                "SOXL": {"price": 80.0, "ma_trend": 75.0},
                "SOXX": {
                    "price": 109.0,
                    "ma_trend": 100.0,
                    "realized_volatility_10": realized_volatility,
                    "realized_volatility_10_dynamic_threshold": 0.50,
                    "realized_volatility_10_dynamic_sample_count": 252.0,
                },
            }
        },
        runtime_config=runtime_config or _core_only_runtime_config(),
    )


def _target_values(ctx: StrategyContext) -> tuple[dict[str, float], dict[str, object]]:
    decision = entrypoints.build_soxl_soxx_core_only_p2_v2_research_decision(ctx)
    return (
        {position.symbol: float(position.target_value or 0.0) for position in decision.positions},
        dict(decision.diagnostics),
    )


def test_public_p2_v2_adapter_validates_then_delegates_without_execution_side_effects() -> None:
    ctx = _context()
    with (
        patch.object(
            entrypoints,
            "_build_soxl_soxx_trend_income_decision",
            wraps=entrypoints._build_soxl_soxx_trend_income_decision,
        ) as builder,
        patch.object(entrypoints, "assess_with_evidence") as assess,
        patch.object(entrypoints, "risk_budgeted_target_weights") as size,
        patch.object(entrypoints, "record_strategy_decision") as record,
    ):
        targets, diagnostics = _target_values(ctx)

    builder.assert_called_once_with(ctx)
    assert "build_soxl_soxx_core_only_p2_v2_research_decision" in entrypoints.__all__
    assess.assert_not_called()
    size.assert_not_called()
    record.assert_not_called()
    assert targets["SOXL"] > 0.0
    assert targets["SOXX"] > 0.0
    assert targets["BOXX"] > 0.0
    assert all(targets.get(symbol, 0.0) == 0.0 for symbol in _EXCLUDED_ASSETS)
    assert diagnostics["market_regime_control_enabled"] is False
    assert diagnostics["blend_gate_volatility_delever_retention_mode"] == "none"


def test_p2_v2_synthetic_volatility_delever_redirects_soxl_to_soxx() -> None:
    targets, diagnostics = _target_values(_context(realized_volatility=0.80))

    assert targets["SOXL"] == 0.0
    assert targets["SOXX"] > 0.0
    assert targets["BOXX"] > 0.0
    assert diagnostics["blend_gate_volatility_delever_triggered"] is True
    assert diagnostics["blend_gate_volatility_delever_redirect_symbol"] == "SOXX"


@pytest.mark.parametrize(
    ("override", "label"),
    (
        ({"income_layer_enabled": True}, "income"),
        ({"option_overlay_enabled": True}, "option"),
        ({"market_regime_control_enabled": True}, "external market-regime control"),
        ({"blend_gate_volatility_delever_retention_mode": "environment"}, "retention"),
        ({"ai_extensions": {"enabled": True}}, "AI extension"),
    ),
)
def test_p2_v2_rejects_reenabled_noncore_components_before_builder_runs(
    override: dict[str, object],
    label: str,
) -> None:
    ctx = _context(runtime_config=_core_only_runtime_config(**override))
    with patch.object(entrypoints, "_build_soxl_soxx_trend_income_decision") as builder:
        with pytest.raises(ValueError, match="invalid SOXL core-only research config"):
            entrypoints.build_soxl_soxx_core_only_p2_v2_research_decision(ctx)

    assert label
    builder.assert_not_called()
