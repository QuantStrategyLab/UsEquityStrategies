from __future__ import annotations

from datetime import datetime, timezone

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyContext, StrategyDecision

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
