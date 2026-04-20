from __future__ import annotations

import unittest

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_entrypoint

from tests.test_qqq_tech_enhancement import _feature_snapshot


def _zh_translator(key: str, **_kwargs) -> str:
    return {"no_trades": "✅ 无需调仓"}.get(key, key)


class PortfolioDashboardTests(unittest.TestCase):
    def test_semiconductor_entrypoint_attaches_strategy_cash_and_holdings_dashboard(self) -> None:
        entrypoint = get_strategy_entrypoint("soxl_soxx_trend_income")
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-21").to_pydatetime(),
            total_equity=0.0,
            buying_power=0.0,
            cash_balance=0.0,
            positions=(),
            metadata={
                "account_hash": "sg",
                "strategy_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
                "cash_by_currency": {"USD": 0.0, "SGD": 350.0},
            },
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-21",
                market_data={"derived_indicators": {"soxl": {"price": 80.0, "ma_trend": 75.0}}},
                portfolio=snapshot,
                runtime_config={"translator": _zh_translator},
            )
        )

        dashboard = decision.diagnostics["execution_annotations"]["dashboard_text"]
        self.assertIn("📌 策略账户概览", dashboard)
        self.assertIn("总资产（策略标的+现金）: $0.00", dashboard)
        self.assertIn("购买力: $0.00", dashboard)
        self.assertIn("各币种现金: SGD 350.00", dashboard)
        self.assertIn("SOXL: $0.00 / 0股", dashboard)
        self.assertIn("SPYI: $0.00 / 0股", dashboard)

    def test_snapshot_entrypoint_attaches_strategy_portfolio_dashboard(self) -> None:
        entrypoint = get_strategy_entrypoint("qqq_tech_enhancement")
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-21").to_pydatetime(),
            total_equity=12500.0,
            buying_power=2500.0,
            cash_balance=2500.0,
            positions=(
                Position(symbol="AAPL", quantity=3, market_value=600.0),
                Position(symbol="BOXX", quantity=10, market_value=1000.0),
            ),
            metadata={
                "account_hash": "demo",
                "strategy_symbols": ("AAPL", "BOXX"),
            },
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-21",
                market_data={"feature_snapshot": _feature_snapshot()},
                portfolio=snapshot,
                state={"current_holdings": {"AAPL"}},
                runtime_config={"translator": _zh_translator},
            )
        )

        dashboard = decision.diagnostics["execution_annotations"]["dashboard_text"]
        self.assertIn("总资产（策略标的+现金）: $12,500.00", dashboard)
        self.assertIn("购买力: $2,500.00", dashboard)
        self.assertIn("AAPL: $600.00 / 3股", dashboard)
        self.assertIn("BOXX: $1,000.00 / 10股", dashboard)

    def test_russell_entrypoint_accepts_runtime_helpers_and_attaches_dashboard(self) -> None:
        entrypoint = get_strategy_entrypoint("russell_1000_multi_factor_defensive")
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-21").to_pydatetime(),
            total_equity=25000.0,
            buying_power=5000.0,
            positions=(Position(symbol="BOXX", quantity=20, market_value=2000.0),),
            metadata={"strategy_symbols": ("AAPL", "BOXX")},
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-21",
                market_data={
                    "feature_snapshot": [
                        {
                            "as_of": "2026-03-31",
                            "symbol": "SPY",
                            "sector": "benchmark",
                            "mom_6_1": 0.1,
                            "mom_12_1": 0.1,
                            "sma200_gap": 0.1,
                            "vol_63": 0.1,
                            "maxdd_126": 0.1,
                            "eligible": False,
                        }
                    ]
                },
                portfolio=snapshot,
                runtime_config={
                    "translator": _zh_translator,
                    "signal_text_fn": lambda icon: icon,
                    "run_as_of": "2026-04-21",
                    "execution_cash_reserve_ratio": 0.0,
                },
            )
        )

        dashboard = decision.diagnostics["execution_annotations"]["dashboard_text"]
        self.assertIn("总资产（策略标的+现金）: $25,000.00", dashboard)
        self.assertIn("购买力: $5,000.00", dashboard)
        self.assertIn("BOXX: $2,000.00 / 20股", dashboard)


if __name__ == "__main__":
    unittest.main()
