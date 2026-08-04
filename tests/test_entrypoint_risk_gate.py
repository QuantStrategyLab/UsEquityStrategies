from __future__ import annotations

from datetime import datetime, timezone

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext, StrategyDecision

import us_equity_strategies.entrypoints._common as common
from us_equity_strategies.entrypoints._common import apply_risk_gate


def test_apply_risk_gate_enriches_stop_loss_diagnostics_from_portfolio() -> None:
    snapshot = PortfolioSnapshot(
        as_of=datetime(2026, 7, 9, tzinfo=timezone.utc),
        total_equity=1000.0,
        positions=(
            Position(symbol="SPY", quantity=10.0, market_value=700.0, average_cost=100.0),
        ),
        metadata={"consecutive_losses": 2},
    )
    ctx = StrategyContext(as_of=snapshot.as_of, portfolio=snapshot, market_data={}, state={}, runtime_config={})
    decision = StrategyDecision(positions=(PositionTarget(symbol="SPY", target_weight=0.5),))
    result = apply_risk_gate(decision, ctx=ctx)
    assert result.positions == ()
    assert "rejected:stop_loss" in result.risk_flags


def test_apply_risk_gate_passes_bootstrap_mandate_parameters_to_qpk(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def _gate(decision, **kwargs):
        captured.update(kwargs)
        return decision

    monkeypatch.setattr(common, "_qpk_apply_risk_gate", _gate)
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="SPY", target_weight=0.20),)
    )

    result = apply_risk_gate(
        decision,
        risk_mandate_id="bootstrap_small_account_v2",
        product_leverage_factors={"SPY": 1},
        available_account_exposure=0.50,
    )

    assert result is decision
    assert captured["risk_mandate_id"] == "bootstrap_small_account_v2"
    assert captured["product_leverage_factors"] == {"SPY": 1}
    assert captured["available_account_exposure"] == 0.50


def test_unmandated_consumer_allows_only_explicit_1x_single_position_at_ten_percent() -> None:
    result = apply_risk_gate(
        StrategyDecision(
            positions=(PositionTarget(symbol="SPY", target_weight=0.10),),
        ),
        product_leverage_factors={"SPY": 1},
        max_single_weight=1.0,
        portfolio_snapshot={"total_equity": 100_000.0},
    )

    assert result.positions == (
        PositionTarget(symbol="SPY", target_weight=0.10),
    )
    assert result.budgets == ()
    assert result.risk_flags == ("risk_gate:passed",)
    assert result.diagnostics["risk_gate"] == "APPROVE"


def test_unmandated_consumer_fail_closed_matrix() -> None:
    single = (PositionTarget(symbol="SPY", target_weight=0.10),)
    cases = (
        (single, {"SPY": 1}, None, ("rejected:risk_engine",)),
        (single, {"SPY": 1}, {"total_equity": float("nan")}, ("rejected:risk_engine",)),
        (
            (PositionTarget(symbol="SPY", target_weight=0.11),),
            {"SPY": 1},
            {"total_equity": 100_000.0},
            ("rejected:concentration",),
        ),
        (
            (
                PositionTarget(symbol="SPY", target_weight=0.05),
                PositionTarget(symbol="BOXX", target_weight=0.05),
            ),
            {"SPY": 1, "BOXX": 1},
            {"total_equity": 100_000.0},
            ("rejected:too_many_positions",),
        ),
        (
            single,
            None,
            {"total_equity": 100_000.0},
            ("rejected:leverage_classification",),
        ),
        (
            single,
            {"QQQ": 1},
            {"total_equity": 100_000.0},
            ("rejected:leverage_classification",),
        ),
        (
            single,
            {"SPY": 2},
            {"total_equity": 100_000.0},
            ("rejected:leverage_classification",),
        ),
    )

    for positions, leverage_factors, snapshot, expected_flags in cases:
        result = apply_risk_gate(
            StrategyDecision(positions=positions),
            product_leverage_factors=leverage_factors,
            max_single_weight=1.0,
            portfolio_snapshot=snapshot,
        )

        assert result.positions == ()
        assert result.budgets == ()
        assert result.risk_flags == expected_flags
        assert result.diagnostics["risk_gate"] == "REJECT"
