from __future__ import annotations

import unittest

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_entrypoint
from us_equity_strategies.entrypoints._portfolio_dashboard import build_portfolio_dashboard

from tests.test_qqq_tech_enhancement import _feature_snapshot


def _zh_translator(key: str, **_kwargs) -> str:
    templates = {
        "no_trades": "✅ 无需调仓",
        "signal_monthly_snapshot_waiting": "月度快照节奏 | 等待进入执行窗口",
        "status_monthly_snapshot_waiting_window": "不执行 | 原因=当前不在月度执行窗口 | 快照日期={snapshot_as_of} | 允许日期={allowed_dates}",
        "status_no_execution_window_after_snapshot": "不执行 | 原因=快照后没有可用执行窗口 | 快照日期={snapshot_as_of}",
    }
    template = templates.get(key, key)
    return template.format(**_kwargs) if _kwargs else template


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
        self.assertIn("💼 策略持仓", dashboard)
        self.assertIn("SOXL: $0.00 / 0股", dashboard)
        self.assertIn("SOXX: $0.00 / 0股", dashboard)
        self.assertIn("BOXX: $0.00 / 0股", dashboard)
        self.assertIn("QQQI: $0.00 / 0股", dashboard)
        self.assertIn("SPYI: $0.00 / 0股", dashboard)
        self.assertNotIn("跟踪股票池", dashboard)

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

    def test_snapshot_entrypoint_renders_structured_monthly_waiting_text_in_zh(self) -> None:
        entrypoint = get_strategy_entrypoint("qqq_tech_enhancement")
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-21").to_pydatetime(),
            total_equity=12500.0,
            buying_power=2500.0,
            cash_balance=2500.0,
            positions=(),
            metadata={
                "account_hash": "demo",
                "strategy_symbols": ("AAPL", "BOXX"),
            },
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-10",
                market_data={"feature_snapshot": _feature_snapshot()},
                portfolio=snapshot,
                state={"current_holdings": set()},
                runtime_config={"translator": _zh_translator, "run_as_of": "2026-04-10"},
            )
        )

        dashboard = decision.diagnostics["execution_annotations"]["dashboard_text"]
        self.assertEqual(decision.diagnostics["signal_description"], "月度快照节奏 | 等待进入执行窗口")
        self.assertIn("当前不在月度执行窗口", decision.diagnostics["status_description"])
        self.assertIn("🎯 信号: 月度快照节奏 | 等待进入执行窗口", dashboard)
        self.assertNotIn("monthly snapshot cadence", dashboard)

    def test_large_universe_dashboard_still_compacts_zero_weight_symbols(self) -> None:
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-21").to_pydatetime(),
            total_equity=50000.0,
            buying_power=10000.0,
            cash_balance=10000.0,
            positions=(Position(symbol="AAPL", quantity=10, market_value=1800.0),),
            metadata={"strategy_symbols": tuple(f"S{i:02d}" for i in range(13))},
        )

        dashboard = build_portfolio_dashboard(
            snapshot,
            strategy_symbols=("AAPL",) + tuple(f"S{i:02d}" for i in range(13)),
            translator=_zh_translator,
        )

        self.assertIn("AAPL: $1,800.00 / 10股", dashboard)
        self.assertIn("跟踪股票池: 14只", dashboard)
        self.assertNotIn("S00: $0.00 / 0股", dashboard)


if __name__ == "__main__":
    unittest.main()
