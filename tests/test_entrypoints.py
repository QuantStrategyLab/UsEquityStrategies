from __future__ import annotations

import unittest

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_platform_runtime_adapter, get_strategy_entrypoint
from us_equity_strategies.runtime_adapters import describe_platform_runtime_requirements
from us_equity_strategies.strategies.global_etf_rotation import compute_signals as legacy_global_compute_signals
from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan as tqqq_growth_build_rebalance_plan
from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan as soxl_soxx_trend_build_rebalance_plan
from us_equity_strategies.strategies.russell_1000_multi_factor_defensive import extract_managed_symbols as legacy_russell_managed_symbols
from us_equity_strategies.strategies.qqq_tech_enhancement import extract_managed_symbols as qqq_tech_managed_symbols

from tests.test_russell_1000_multi_factor_defensive import _normal_snapshot
from tests.test_qqq_tech_enhancement import _feature_snapshot


class StrategyEntrypointTests(unittest.TestCase):
    def test_all_live_profiles_expose_unified_entrypoints(self) -> None:
        for profile in (
            "global_etf_rotation",
            "tqqq_growth_income",
            "soxl_soxx_trend_income",
            "russell_1000_multi_factor_defensive",
            "tech_communication_pullback_enhancement",
        ):
            entrypoint = get_strategy_entrypoint(profile)
            self.assertEqual(entrypoint.manifest.profile, profile)

    def test_global_etf_rotation_entrypoint_matches_legacy_emergency_weights(self) -> None:
        entrypoint = get_strategy_entrypoint("global_etf_rotation")
        index = pd.date_range("2024-01-01", periods=320, freq="B")
        price_series = pd.Series([100.0 + (i * 0.1) for i in range(len(index))], index=index)

        def get_historical_close(_ib, _ticker):
            return price_series

        legacy_weights, legacy_signal, legacy_is_emergency, legacy_canary = legacy_global_compute_signals(
            None,
            current_holdings={"VOO"},
            get_historical_close=get_historical_close,
            translator=lambda key, **kwargs: f"{key}:{kwargs}",
            pacing_sec=0.0,
            canary_bad_threshold=0,
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-06",
                market_data={"market_history": get_historical_close},
                state={"current_holdings": {"VOO"}},
                runtime_config={
                    "translator": lambda key, **kwargs: f"{key}:{kwargs}",
                    "canary_bad_threshold": 0,
                },
            )
        )

        self.assertTrue(legacy_is_emergency)
        self.assertEqual(decision.risk_flags, ("emergency",))
        self.assertEqual({p.symbol: p.target_weight for p in decision.positions}, legacy_weights)
        self.assertEqual(decision.diagnostics["signal_description"], legacy_signal)
        self.assertEqual(decision.diagnostics["canary_status"], legacy_canary)

    def test_global_etf_runtime_adapter_uses_canonical_market_history(self) -> None:
        adapter = get_platform_runtime_adapter("global_etf_rotation", platform_id="ibkr")
        self.assertEqual(adapter.available_inputs, frozenset({"market_history"}))
        self.assertEqual(adapter.available_capabilities, frozenset({"broker_client"}))

    def test_weight_mode_global_etf_runtime_adapters_use_portfolio_snapshot_on_value_native_platforms(self) -> None:
        for platform_id in ("schwab", "longbridge"):
            adapter = get_platform_runtime_adapter("global_etf_rotation", platform_id=platform_id)
            self.assertEqual(
                adapter.available_inputs,
                frozenset({"market_history", "portfolio_snapshot"}),
            )
            self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_tqqq_growth_income_entrypoint_maps_target_values_without_platform_layout(self) -> None:
        entrypoint = get_strategy_entrypoint("tqqq_growth_income")
        qqq_history = [
            {
                "close": 300.0 + day * 0.4,
                "high": 301.0 + day * 0.4,
                "low": 299.0 + day * 0.4,
            }
            for day in range(260)
        ]
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
            total_equity=120000.0,
            buying_power=20000.0,
            positions=(
                Position(symbol="TQQQ", quantity=10, market_value=8000.0),
                Position(symbol="BOXX", quantity=20, market_value=4000.0),
                Position(symbol="SPYI", quantity=30, market_value=1500.0),
                Position(symbol="QQQI", quantity=30, market_value=1700.0),
            ),
            metadata={"account_hash": "demo"},
        )
        legacy_plan = tqqq_growth_build_rebalance_plan(
            qqq_history,
            snapshot,
            signal_text_fn=str,
            translator=lambda key, **kwargs: key,
            **{
                key: value
                for key, value in entrypoint.manifest.default_config.items()
                if key not in {"benchmark_symbol", "managed_symbols"}
            },
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-06",
                market_data={
                    "benchmark_history": qqq_history,
                    "portfolio_snapshot": snapshot,
                },
                portfolio=snapshot,
                runtime_config={"signal_text_fn": str, "translator": lambda key, **kwargs: key},
            )
        )

        self.assertEqual(
            {position.symbol: position.target_value for position in decision.positions},
            legacy_plan["target_values"],
        )
        self.assertNotIn("sell_order_symbols", decision.diagnostics)
        self.assertNotIn("portfolio_rows", decision.diagnostics)
        self.assertEqual(decision.diagnostics["threshold"], legacy_plan["threshold"])
        self.assertEqual(
            entrypoint.manifest.default_config["managed_symbols"],
            ("TQQQ", "BOXX", "SPYI", "QQQI"),
        )

    def test_value_mode_hybrid_runtime_adapters_use_canonical_inputs(self) -> None:
        for platform_id in ("ibkr", "schwab", "longbridge"):
            adapter = get_platform_runtime_adapter("tqqq_growth_income", platform_id=platform_id)
            self.assertEqual(
                adapter.available_inputs,
                frozenset({"benchmark_history", "portfolio_snapshot"}),
            )
            self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_runtime_requirements_classify_snapshot_and_non_snapshot_profiles(self) -> None:
        tech = describe_platform_runtime_requirements("qqq_tech_enhancement", platform_id="schwab")
        self.assertEqual(tech["profile_group"], "snapshot_backed")
        self.assertEqual(tech["input_mode"], "feature_snapshot")
        self.assertTrue(tech["requires_snapshot_artifacts"])
        self.assertTrue(tech["requires_strategy_config_path"])

        tqqq = describe_platform_runtime_requirements("tqqq_growth_income", platform_id="ibkr")
        self.assertEqual(tqqq["profile_group"], "direct_runtime_inputs")
        self.assertEqual(tqqq["input_mode"], "benchmark_history+portfolio_snapshot")
        self.assertFalse(tqqq["requires_snapshot_artifacts"])
        self.assertFalse(tqqq["requires_strategy_config_path"])

    def test_soxl_soxx_trend_income_entrypoint_maps_target_values_without_execution_fields(self) -> None:
        entrypoint = get_strategy_entrypoint("soxl_soxx_trend_income")
        indicators = {"soxl": {"price": 80.0, "ma_trend": 75.0}}
        account_state = {
            "available_cash": 10000.0,
            "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 5000.0, "QQQI": 1000.0, "SPYI": 1000.0},
            "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
            "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
            "total_strategy_equity": 50000.0,
        }
        legacy_plan = soxl_soxx_trend_build_rebalance_plan(
            indicators,
            account_state,
            translator=lambda key, **kwargs: key,
            **{
                key: value
                for key, value in entrypoint.manifest.default_config.items()
                if key != "managed_symbols"
            },
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-06",
                market_data={
                    "derived_indicators": indicators,
                    "portfolio_snapshot": PortfolioSnapshot(
                        as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
                        total_equity=50000.0,
                        buying_power=10000.0,
                        positions=(
                            Position(symbol="BOXX", quantity=50, market_value=5000.0),
                            Position(symbol="QQQI", quantity=10, market_value=1000.0),
                            Position(symbol="SPYI", quantity=10, market_value=1000.0),
                        ),
                        metadata={"account_hash": "demo"},
                    ),
                },
                portfolio=PortfolioSnapshot(
                    as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
                    total_equity=50000.0,
                    buying_power=10000.0,
                    positions=(
                        Position(symbol="BOXX", quantity=50, market_value=5000.0),
                        Position(symbol="QQQI", quantity=10, market_value=1000.0),
                        Position(symbol="SPYI", quantity=10, market_value=1000.0),
                    ),
                    metadata={"account_hash": "demo"},
                ),
                runtime_config={
                    "signal_text_fn": str,
                    "translator": lambda key, **kwargs: key,
                },
            )
        )

        self.assertEqual(
            {position.symbol: position.target_value for position in decision.positions},
            legacy_plan["targets"],
        )
        self.assertNotIn("limit_order_symbols", decision.diagnostics)
        self.assertNotIn("portfolio_rows", decision.diagnostics)
        self.assertEqual(decision.diagnostics["active_risk_asset"], legacy_plan["active_risk_asset"])
        self.assertEqual(
            entrypoint.manifest.default_config["managed_symbols"],
            ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        )

    def test_value_mode_semiconductor_runtime_adapters_use_canonical_inputs(self) -> None:
        for platform_id in ("schwab", "longbridge"):
            adapter = get_platform_runtime_adapter("soxl_soxx_trend_income", platform_id=platform_id)
            self.assertEqual(
                adapter.available_inputs,
                frozenset({"derived_indicators", "portfolio_snapshot"}),
            )
            self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_russell_and_tech_entrypoints_match_legacy_weight_outputs(self) -> None:
        russell = get_strategy_entrypoint("russell_1000_multi_factor_defensive")
        russell_decision = russell.evaluate(
            StrategyContext(
                as_of="2026-04-06",
                market_data={"feature_snapshot": _normal_snapshot()},
                state={"current_holdings": {"BBB"}},
                runtime_config={
                    "holdings_count": 3,
                    "single_name_cap": 0.40,
                    "sector_cap": 0.50,
                    "hold_bonus": 5.0,
                },
            )
        )
        self.assertIn("BBB", {position.symbol for position in russell_decision.positions})
        self.assertEqual(russell_decision.diagnostics["signal_source"], "feature_snapshot")

        tech = get_strategy_entrypoint("qqq_tech_enhancement")
        tech_decision = tech.evaluate(
            StrategyContext(
                as_of="2026-04-01",
                market_data={"feature_snapshot": _feature_snapshot()},
                portfolio=PortfolioSnapshot(
                    as_of="2026-04-01",
                    total_equity=10_000.0,
                    buying_power=10_000.0,
                    cash_balance=10_000.0,
                    positions=(),
                ),
                state={"current_holdings": {"AAPL"}},
            )
            )
        self.assertIn("BOXX", {position.symbol for position in tech_decision.positions})
        self.assertNotIn("portfolio_rows", tech_decision.diagnostics)
        self.assertEqual(tech_decision.diagnostics["signal_source"], "feature_snapshot")
        self.assertEqual(tech_decision.diagnostics["effective_holdings_count"], 2)

    def test_ibkr_runtime_adapters_expose_unified_snapshot_runtime_metadata(self) -> None:
        global_adapter = get_platform_runtime_adapter("global_macro_etf_rotation", platform_id="ibkr")
        self.assertEqual(global_adapter.status_icon, "🐤")

        russell_adapter = get_platform_runtime_adapter(
            "r1000_multifactor_defensive",
            platform_id="ibkr",
        )
        self.assertEqual(russell_adapter.status_icon, "📏")
        self.assertEqual(
            set(russell_adapter.required_feature_columns),
            {"symbol", "sector", "mom_6_1", "mom_12_1", "sma200_gap", "vol_63", "maxdd_126"},
        )
        self.assertEqual(
            russell_adapter.managed_symbols_extractor(
                _normal_snapshot(),
                benchmark_symbol="SPY",
                safe_haven="BOXX",
            ),
            legacy_russell_managed_symbols(
                _normal_snapshot(),
                benchmark_symbol="SPY",
                safe_haven="BOXX",
            ),
        )
        for platform_id in ("schwab", "longbridge"):
            russell_value_native_adapter = get_platform_runtime_adapter(
                "russell_1000_multi_factor_defensive",
                platform_id=platform_id,
            )
            self.assertEqual(
                russell_value_native_adapter.available_inputs,
                frozenset({"feature_snapshot", "portfolio_snapshot"}),
            )
            self.assertEqual(russell_value_native_adapter.portfolio_input_name, "portfolio_snapshot")
            self.assertEqual(russell_value_native_adapter.status_icon, "📏")

        tech_adapter = get_platform_runtime_adapter("qqq_tech_enhancement", platform_id="ibkr")
        self.assertEqual(tech_adapter.status_icon, "🧲")
        self.assertEqual(tech_adapter.snapshot_date_columns, ("as_of", "snapshot_date"))
        self.assertTrue(tech_adapter.require_snapshot_manifest)
        self.assertEqual(
            tech_adapter.managed_symbols_extractor(
                _feature_snapshot(),
                benchmark_symbol="QQQ",
                safe_haven="BOXX",
            ),
            qqq_tech_managed_symbols(
                _feature_snapshot(),
                benchmark_symbol="QQQ",
                safe_haven="BOXX",
            ),
        )
        self.assertEqual(
            tech_adapter.runtime_parameter_loader(
                config_path=None,
                logger=lambda _message: None,
            )["runtime_config_name"],
            "tech_communication_pullback_enhancement",
        )
        longbridge_tech_adapter = get_platform_runtime_adapter("qqq_tech_enhancement", platform_id="longbridge")
        self.assertEqual(
            longbridge_tech_adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(longbridge_tech_adapter.portfolio_input_name, "portfolio_snapshot")
        schwab_tech_adapter = get_platform_runtime_adapter("qqq_tech_enhancement", platform_id="schwab")
        self.assertEqual(
            schwab_tech_adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(schwab_tech_adapter.portfolio_input_name, "portfolio_snapshot")

        semiconductor_ibkr_adapter = get_platform_runtime_adapter(
            "soxl_soxx_trend_income",
            platform_id="ibkr",
        )
        self.assertEqual(
            semiconductor_ibkr_adapter.available_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )
        self.assertEqual(semiconductor_ibkr_adapter.portfolio_input_name, "portfolio_snapshot")
