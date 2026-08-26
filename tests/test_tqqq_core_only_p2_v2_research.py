from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.strategy_contracts import StrategyContext

import us_equity_strategies.entrypoints as entrypoints


_AS_OF = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
_CORE_ASSETS = ("TQQQ", "QQQM", "BOXX")


def _core_only_runtime_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "benchmark_symbol": "QQQ",
        "managed_symbols": _CORE_ASSETS,
        "signal_effective_after_trading_days": 1,
        "dual_drive_unlevered_symbol": "QQQM",
        "income_layer_enabled": False,
        "option_overlay_enabled": False,
        "option_growth_overlay_enabled": False,
        "option_income_overlay_enabled": False,
        "ai_extensions": {"enabled": False},
        "dual_drive_volatility_delever_retention_mode": "none",
        "dual_drive_volatility_delever_retention_ratio": 0.0,
        "dual_drive_volatility_delever_taco_veto_enabled": False,
        "dual_drive_macro_risk_governor_enabled": False,
        "dual_drive_crisis_defense_enabled": False,
        "market_regime_control_enabled": False,
    }
    config.update(overrides)
    return config


def _context(
    history: list[dict[str, float]],
    *,
    runtime_config: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> StrategyContext:
    snapshot = PortfolioSnapshot(
        as_of=_AS_OF,
        total_equity=100_000.0,
        buying_power=100_000.0,
        cash_balance=100_000.0,
        positions=(),
        metadata={"observed_effective_exposure": 0.0, **(metadata or {})},
    )
    return StrategyContext(
        as_of=_AS_OF,
        portfolio=snapshot,
        market_data={"benchmark_history": history},
        runtime_config=runtime_config or _core_only_runtime_config(),
    )


def _targets(ctx: StrategyContext) -> tuple[dict[str, float], dict[str, object]]:
    decision = entrypoints.build_tqqq_core_only_p2_v2_research_decision(ctx)
    return (
        {position.symbol: float(position.target_value or 0.0) for position in decision.positions},
        dict(decision.diagnostics),
    )


def _rising_history() -> list[dict[str, float]]:
    return [
        {"close": 100.0 + index, "high": 101.0 + index, "low": 99.0 + index}
        for index in range(260)
    ]


def test_public_p2_v2_adapter_validates_then_delegates_to_existing_builder() -> None:
    ctx = _context(_rising_history())
    with (
        patch.object(
            entrypoints,
            "_build_tqqq_growth_income_decision",
            wraps=entrypoints._build_tqqq_growth_income_decision,
        ) as builder,
        patch.object(entrypoints, "assess_with_evidence") as assess,
        patch.object(entrypoints, "risk_budgeted_target_weights") as size,
        patch.object(entrypoints, "record_strategy_decision") as record,
    ):
        targets, diagnostics = _targets(ctx)

    builder.assert_called_once_with(ctx)
    assert "build_tqqq_core_only_p2_v2_research_decision" in entrypoints.__all__
    assess.assert_not_called()
    size.assert_not_called()
    record.assert_not_called()
    assert targets == {
        "BOXX": 8_000.0,
        "DGRO": 0.0,
        "QQQI": 0.0,
        "QQQM": 45_000.0,
        "SCHD": 0.0,
        "SGOV": 0.0,
        "SPYI": 0.0,
        "TQQQ": 45_000.0,
    }
    assert diagnostics["notification_context"]["signal"]["state"] == "entry"


def test_p2_v2_synthetic_trend_defense_parks_in_boxx() -> None:
    history = [
        {"close": 360.0 - index, "high": 361.0 - index, "low": 359.0 - index}
        for index in range(260)
    ]

    targets, diagnostics = _targets(_context(history))

    assert targets["TQQQ"] == 0.0
    assert targets["QQQM"] == 0.0
    assert targets["BOXX"] == 98_000.0
    assert diagnostics["notification_context"]["signal"]["state"] == "idle"


def test_p2_v2_synthetic_pullback_reentry_restores_tqqq_and_qqqm() -> None:
    history = [
        {"close": 120.0, "high": 121.0, "low": 119.0} for _ in range(220)
    ] + [
        {
            "close": 100.0 + index * 0.45,
            "high": 101.0 + index * 0.45,
            "low": 99.0 + index * 0.45,
        }
        for index in range(21)
    ]

    targets, diagnostics = _targets(_context(history))

    assert targets["TQQQ"] == 45_000.0
    assert targets["QQQM"] == 45_000.0
    assert targets["BOXX"] == 8_000.0
    assert diagnostics["notification_context"]["signal"]["state"] == "entry"


def test_p2_v2_synthetic_volatility_redirects_tqqq_to_qqqm() -> None:
    history = [
        {"close": 100.0, "high": 101.0, "low": 99.0} for _ in range(230)
    ] + [
        {"close": close, "high": close + 1.0, "low": close - 1.0}
        for close in (130.0, 80.0, 135.0, 82.0, 140.0, 85.0, 145.0, 88.0, 150.0, 90.0, 155.0)
    ]

    targets, diagnostics = _targets(_context(history))

    assert targets["TQQQ"] == 0.0
    assert targets["QQQM"] == 90_000.0
    assert targets["BOXX"] == 8_000.0
    assert diagnostics["dual_drive_volatility_delever_applied"] is True
    assert diagnostics["dual_drive_volatility_delever_redirect_symbol"] == "QQQM"


def test_p2_v2_rejects_reenabled_retention_before_builder_runs() -> None:
    ctx = _context(
        _rising_history(),
        runtime_config=_core_only_runtime_config(
            dual_drive_volatility_delever_retention_mode="environment",
        ),
    )
    with patch.object(entrypoints, "_build_tqqq_growth_income_decision") as builder:
        with pytest.raises(ValueError, match="invalid TQQQ core-only research config"):
            entrypoints.build_tqqq_core_only_p2_v2_research_decision(ctx)

    builder.assert_not_called()


def test_benchmark_guard_research_adapter_defends_when_the_authorized_guard_is_blocked() -> None:
    context = _context(
        _rising_history(),
        runtime_config=_core_only_runtime_config(
            market_regime_control_enabled=True,
            dual_drive_macro_risk_governor_enabled=True,
            dual_drive_crisis_defense_enabled=True,
        ),
        metadata={
            "market_regime_control": {
                "plugin": "market_regime_control",
                "schema_version": "market_regime_control.v1",
                "canonical_route": "blocked",
                "suggested_action": "blocked",
                "execution_controls": {
                    "position_control_allowed": True,
                    "consumption_evidence_status": "research_backtest_approved",
                },
                "position_control": {
                    "final_route": "blocked",
                    "suggested_action": "blocked",
                    "route_source": "benchmark_guard",
                    "risk_budget_scalar": 0.0,
                    "leverage_scalar": 0.0,
                    "risk_asset_scalar": 0.0,
                    "crisis_defense_required": True,
                    "reason_codes": ["benchmark_guard:benchmark_history_unavailable"],
                },
            }
        },
    )

    default_decision = entrypoints._build_tqqq_growth_income_decision(context)
    default_targets = {
        position.symbol: float(position.target_value or 0.0) for position in default_decision.positions
    }
    research_decision = entrypoints.build_tqqq_core_only_p2_benchmark_guard_research_decision(context)
    research_targets = {
        position.symbol: float(position.target_value or 0.0) for position in research_decision.positions
    }

    assert "build_tqqq_core_only_p2_benchmark_guard_research_decision" in entrypoints.__all__
    assert default_targets["TQQQ"] > 0.0
    assert default_targets["QQQM"] > 0.0
    assert research_targets["TQQQ"] == 0.0
    assert research_targets["QQQM"] == 0.0
    assert research_targets["BOXX"] == 98_000.0
    assert research_decision.diagnostics["market_regime_control_route_source"] == "benchmark_guard"
    assert research_decision.diagnostics["dual_drive_crisis_defense_applied"] is True
