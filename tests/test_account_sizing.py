from __future__ import annotations

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_entrypoint
from us_equity_strategies.account_sizing import (
    append_account_size_warning,
    build_account_size_diagnostics,
    get_min_recommended_equity_usd,
)

from tests.test_qqq_tech_enhancement import _feature_snapshot


def test_min_recommended_equity_is_profile_specific() -> None:
    assert get_min_recommended_equity_usd("tqqq_growth_income") == 500.0
    assert get_min_recommended_equity_usd("soxl_soxx_trend_income") == 1_000.0
    assert get_min_recommended_equity_usd("qqq_tech_enhancement") == 10_000.0
    assert get_min_recommended_equity_usd("mega_cap_leader_rotation_top50_balanced") == 10_000.0
    assert get_min_recommended_equity_usd("russell_1000_multi_factor_defensive") == 30_000.0
    assert get_min_recommended_equity_usd("unknown") is None


def test_account_size_diagnostics_warn_below_recommended_equity() -> None:
    diagnostics = build_account_size_diagnostics(
        "tech_communication_pullback_enhancement",
        1_000.0,
    )

    assert diagnostics["min_recommended_equity_usd"] == 10_000.0
    assert diagnostics["portfolio_total_equity"] == 1_000.0
    assert diagnostics["small_account_warning"] is True
    assert diagnostics["small_account_warning_reason"] == "integer_shares_min_position_value_may_prevent_backtest_replication"
    assert "small account warning" in append_account_size_warning("signal", diagnostics)
    assert "recommended $10,000" in append_account_size_warning("signal", diagnostics)


def test_account_size_warning_uses_translator_when_available() -> None:
    diagnostics = build_account_size_diagnostics(
        "soxl_soxx_trend_income",
        0.0,
    )

    def translate(key: str, **kwargs) -> str:
        messages = {
            "small_account_warning_note": (
                "小账户提示：净值 {portfolio_equity} 低于建议 {min_recommended_equity}；{reason}"
            ),
            "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication": (
                "整数股和最小仓位限制可能导致实盘无法完全复现回测"
            ),
        }
        return messages[key].format(**kwargs)

    assert append_account_size_warning("信号", diagnostics, translator=translate) == (
        "信号 | 小账户提示：净值 $0 低于建议 $1,000；整数股和最小仓位限制可能导致实盘无法完全复现回测"
    )


def test_account_size_diagnostics_do_not_warn_at_recommended_equity() -> None:
    diagnostics = build_account_size_diagnostics(
        "mega_cap_leader_rotation_top50_balanced",
        10_000.0,
    )

    assert diagnostics["small_account_warning"] is False
    assert append_account_size_warning("signal", diagnostics) == "signal"


def test_entrypoint_appends_small_account_warning_to_signal_description() -> None:
    entrypoint = get_strategy_entrypoint("tech_communication_pullback_enhancement")
    portfolio = PortfolioSnapshot(
        as_of=pd.Timestamp("2026-04-01").to_pydatetime(),
        total_equity=1_000.0,
        buying_power=1_000.0,
        cash_balance=1_000.0,
        positions=(),
        metadata={"account_hash": "demo"},
    )

    decision = entrypoint.evaluate(
        StrategyContext(
            as_of="2026-04-01",
            market_data={"feature_snapshot": _feature_snapshot()},
            portfolio=portfolio,
            state={"current_holdings": set()},
        )
    )

    assert decision.diagnostics["small_account_warning"] is True
    assert decision.diagnostics["min_recommended_equity_usd"] == 10_000.0
    assert "small account warning" in decision.diagnostics["signal_description"]
