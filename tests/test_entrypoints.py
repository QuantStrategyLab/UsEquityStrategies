from __future__ import annotations

import unittest

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_platform_runtime_adapter, get_strategy_entrypoint
from us_equity_strategies.catalog import get_runtime_enabled_profiles
from us_equity_strategies.runtime_adapters import describe_platform_runtime_requirements
from us_equity_strategies.strategies.global_etf_rotation import compute_signals as legacy_global_compute_signals
from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan as tqqq_growth_build_rebalance_plan
from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan as soxl_soxx_trend_build_rebalance_plan
from us_equity_strategies.strategies.russell_1000_multi_factor_defensive import extract_managed_symbols as legacy_russell_managed_symbols
from us_equity_strategies.strategies.qqq_tech_enhancement import extract_managed_symbols as qqq_tech_managed_symbols
from us_equity_strategies.strategies.mega_cap_leader_rotation_dynamic_top20 import extract_managed_symbols as mega_cap_managed_symbols

from tests.test_russell_1000_multi_factor_defensive import _normal_snapshot
from tests.test_qqq_tech_enhancement import _feature_snapshot
from tests.test_mega_cap_leader_rotation_dynamic_top20 import _mega_snapshot


class StrategyEntrypointTests(unittest.TestCase):
    def test_all_live_profiles_expose_unified_entrypoints(self) -> None:
        for profile in get_runtime_enabled_profiles():
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
                    "signal_effective_after_trading_days": 1,
                },
            )
        )

        self.assertTrue(legacy_is_emergency)
        self.assertEqual(decision.risk_flags, ("emergency",))
        self.assertEqual({p.symbol: p.target_weight for p in decision.positions}, legacy_weights)
        self.assertEqual(decision.diagnostics["signal_description"], legacy_signal)
        self.assertEqual(decision.diagnostics["canary_status"], legacy_canary)
        self.assertEqual(decision.diagnostics["signal_date"], "2026-04-06")
        self.assertEqual(decision.diagnostics["effective_date"], "2026-04-07")
        self.assertEqual(decision.diagnostics["execution_timing_contract"], "next_trading_day")
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["effective_date"],
            "2026-04-07",
        )

    def test_global_etf_runtime_adapter_uses_canonical_market_history(self) -> None:
        adapter = get_platform_runtime_adapter("global_etf_rotation", platform_id="ibkr")
        self.assertEqual(adapter.available_inputs, frozenset({"market_history"}))
        self.assertEqual(adapter.available_capabilities, frozenset({"broker_client"}))
        self.assertEqual(adapter.runtime_policy.signal_effective_after_trading_days, 1)
        paper_signal_adapter = get_platform_runtime_adapter("global_etf_rotation", platform_id="paper_signal")
        self.assertEqual(paper_signal_adapter.available_inputs, frozenset({"market_history"}))
        self.assertEqual(paper_signal_adapter.available_capabilities, frozenset())
        self.assertEqual(paper_signal_adapter.runtime_policy.signal_effective_after_trading_days, 1)

    def test_global_etf_rotation_entrypoint_accepts_timestamp_as_of(self) -> None:
        entrypoint = get_strategy_entrypoint("global_etf_rotation")
        index = pd.date_range("2025-01-01", periods=320, freq="B")
        price_series = pd.Series([100.0 + (i * 0.1) for i in range(len(index))], index=index)

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of=pd.Timestamp("2026-03-31"),
                market_data={"market_history": lambda _ib, _ticker: price_series},
                state={"current_holdings": ()},
                runtime_config={
                    "translator": lambda key, **kwargs: key,
                    "pacing_sec": 0.0,
                    "signal_effective_after_trading_days": 1,
                },
            )
        )

        self.assertEqual(decision.diagnostics["signal_date"], "2026-03-31")

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
                if key not in {"benchmark_symbol", "managed_symbols", "execution_cash_reserve_ratio"}
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
                runtime_config={
                    "signal_text_fn": str,
                    "translator": lambda key, **kwargs: key,
                    "signal_effective_after_trading_days": 1,
                },
            )
        )

        target_values = {position.symbol: position.target_value for position in decision.positions}
        self.assertEqual(target_values, legacy_plan["target_values"])
        self.assertAlmostEqual(target_values["QQQ"] / snapshot.total_equity, 0.45)
        self.assertAlmostEqual(target_values["TQQQ"] / snapshot.total_equity, 0.45)
        self.assertAlmostEqual(target_values["BOXX"] / snapshot.total_equity, 0.08)
        self.assertNotIn("sell_order_symbols", decision.diagnostics)
        self.assertNotIn("portfolio_rows", decision.diagnostics)
        self.assertEqual(decision.diagnostics["threshold"], legacy_plan["threshold"])
        self.assertEqual(decision.diagnostics["notification_context"]["benchmark"]["symbol"], "QQQ")
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["notification_context"]["signal"]["state"],
            legacy_plan["notification_context"]["signal"]["state"],
        )
        self.assertEqual(decision.diagnostics["signal_date"], "2026-04-06")
        self.assertEqual(decision.diagnostics["effective_date"], "2026-04-07")
        self.assertEqual(decision.diagnostics["execution_timing_contract"], "next_trading_day")
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["signal_effective_after_trading_days"],
            1,
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["raw_buying_power"],
            legacy_plan["real_buying_power"],
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["reserved_cash"],
            legacy_plan["reserved"],
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["investable_cash"],
            legacy_plan["investable_buying_power"],
        )
        self.assertIn(
            f"Buying power: ${legacy_plan['real_buying_power']:,.2f}",
            decision.diagnostics["dashboard"],
        )
        self.assertIn(
            f"Reserved cash: ${legacy_plan['reserved']:,.2f}",
            decision.diagnostics["dashboard"],
        )
        self.assertIn(
            f"Investable cash: ${legacy_plan['investable_buying_power']:,.2f}",
            decision.diagnostics["dashboard"],
        )
        self.assertEqual(
            entrypoint.manifest.default_config["managed_symbols"],
            ("TQQQ", "QQQ", "BOXX", "SPYI", "QQQI"),
        )

    def test_tqqq_growth_income_defaults_to_fixed_dual_drive_live_profile(self) -> None:
        config = get_strategy_entrypoint("tqqq_growth_income").manifest.default_config

        self.assertEqual(config["attack_allocation_mode"], "fixed_qqq_tqqq_pullback")
        self.assertEqual(config["dual_drive_qqq_weight"], 0.45)
        self.assertEqual(config["dual_drive_tqqq_weight"], 0.45)
        self.assertEqual(config["dual_drive_unlevered_symbol"], "QQQ")
        self.assertEqual(config["dual_drive_cash_reserve_ratio"], 0.02)
        self.assertEqual(config["dual_drive_pullback_rebound_window"], 20)
        self.assertEqual(config["dual_drive_pullback_rebound_threshold_mode"], "volatility_scaled")
        self.assertEqual(config["dual_drive_pullback_rebound_threshold"], 0.0)
        self.assertEqual(config["dual_drive_pullback_rebound_volatility_multiplier"], 2.0)
        self.assertEqual(config["cash_reserve_ratio"], 0.02)
        self.assertEqual(config["income_threshold_usd"], 1_000_000_000.0)
        self.assertEqual(config["execution_cash_reserve_ratio"], 0.0)
        self.assertIn("QQQ", config["managed_symbols"])

    def test_value_mode_hybrid_runtime_adapters_use_canonical_inputs(self) -> None:
        for platform_id in ("ibkr", "schwab", "longbridge", "paper_signal"):
            adapter = get_platform_runtime_adapter("tqqq_growth_income", platform_id=platform_id)
            self.assertEqual(
                adapter.available_inputs,
                frozenset({"benchmark_history", "portfolio_snapshot"}),
            )
            self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_russell_snapshot_entrypoint_ignores_signal_timing_runtime_hint(self) -> None:
        entrypoint = get_strategy_entrypoint("russell_1000_multi_factor_defensive")

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-06",
                market_data={"feature_snapshot": _normal_snapshot()},
                state={"current_holdings": ()},
                runtime_config={
                    "signal_effective_after_trading_days": 1,
                },
            )
        )

        self.assertTrue(decision.positions)
        self.assertIn("signal_description", decision.diagnostics)

    def test_tqqq_growth_income_entrypoint_uses_live_dual_drive_config(self) -> None:
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
                Position(symbol="QQQ", quantity=0, market_value=0.0),
                Position(symbol="SPYI", quantity=30, market_value=1500.0),
                Position(symbol="QQQI", quantity=30, market_value=1700.0),
            ),
            metadata={"account_hash": "demo"},
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-06",
                market_data={
                    "benchmark_history": qqq_history,
                    "portfolio_snapshot": snapshot,
                },
                portfolio=snapshot,
                runtime_config={
                    "signal_text_fn": str,
                    "translator": lambda key, **kwargs: key,
                    "signal_effective_after_trading_days": 1,
                },
            )
        )

        target_values = {position.symbol: position.target_value for position in decision.positions}
        self.assertIn("QQQ", target_values)
        self.assertGreater(target_values["QQQ"], 0.0)

    def test_tqqq_growth_income_entrypoint_accepts_qqqm_unlevered_sleeve(self) -> None:
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
                Position(symbol="QQQM", quantity=0, market_value=0.0),
                Position(symbol="BOXX", quantity=20, market_value=4000.0),
                Position(symbol="SPYI", quantity=30, market_value=1500.0),
                Position(symbol="QQQI", quantity=30, market_value=1700.0),
            ),
            metadata={"account_hash": "demo"},
        )

        decision = entrypoint.evaluate(
            StrategyContext(
                as_of="2026-04-06",
                market_data={
                    "benchmark_history": qqq_history,
                    "portfolio_snapshot": snapshot,
                },
                portfolio=snapshot,
                runtime_config={
                    "dual_drive_unlevered_symbol": "QQQM",
                    "managed_symbols": ("TQQQ", "QQQM", "BOXX", "SPYI", "QQQI"),
                    "signal_text_fn": str,
                    "translator": lambda key, **kwargs: key,
                },
            )
        )

        target_values = {position.symbol: position.target_value for position in decision.positions}
        self.assertIn("QQQM", target_values)
        self.assertNotIn("QQQ", target_values)
        self.assertGreater(target_values["QQQM"], 0.0)
        self.assertIn("QQQM: $", decision.diagnostics["dashboard"])
        self.assertIn("QQQ: ", decision.diagnostics["dashboard"])

    def test_runtime_requirements_classify_snapshot_and_non_snapshot_profiles(self) -> None:
        tech = describe_platform_runtime_requirements("qqq_tech_enhancement", platform_id="schwab")
        self.assertEqual(tech["profile_group"], "snapshot_backed")
        self.assertEqual(tech["input_mode"], "feature_snapshot")
        self.assertTrue(tech["requires_snapshot_artifacts"])
        self.assertTrue(tech["requires_snapshot_manifest_path"])
        self.assertTrue(tech["requires_strategy_config_path"])
        self.assertEqual(tech["config_source_policy"], "bundled_or_env")
        self.assertEqual(tech["reconciliation_output_policy"], "optional")
        self.assertIsNone(tech["runtime_execution_window_trading_days"])

        longbridge_tech = describe_platform_runtime_requirements(
            "qqq_tech_enhancement",
            platform_id="longbridge",
        )
        self.assertEqual(longbridge_tech["runtime_execution_window_trading_days"], 1)

        mega = describe_platform_runtime_requirements("mega_cap_leader_rotation_dynamic_top20", platform_id="ibkr")
        self.assertEqual(mega["profile_group"], "snapshot_backed")
        self.assertEqual(mega["input_mode"], "feature_snapshot")
        self.assertTrue(mega["requires_snapshot_artifacts"])
        self.assertTrue(mega["requires_snapshot_manifest_path"])
        self.assertFalse(mega["requires_strategy_config_path"])
        self.assertEqual(mega["config_source_policy"], "none")
        self.assertEqual(
            mega["snapshot_contract_version"],
            "mega_cap_leader_rotation_dynamic_top20.feature_snapshot.v1",
        )

        mega_aggressive = describe_platform_runtime_requirements("mega_cap_leader_rotation_aggressive", platform_id="ibkr")
        self.assertEqual(mega_aggressive["profile_group"], "snapshot_backed")
        self.assertEqual(mega_aggressive["input_mode"], "feature_snapshot")
        self.assertTrue(mega_aggressive["requires_snapshot_artifacts"])
        self.assertTrue(mega_aggressive["requires_snapshot_manifest_path"])
        self.assertFalse(mega_aggressive["requires_strategy_config_path"])

        leveraged = describe_platform_runtime_requirements("dynamic_mega_leveraged_pullback", platform_id="ibkr")
        self.assertEqual(leveraged["profile_group"], "snapshot_backed")
        self.assertEqual(
            leveraged["input_mode"],
            "feature_snapshot+market_history+benchmark_history+portfolio_snapshot",
        )
        self.assertTrue(leveraged["requires_snapshot_artifacts"])
        self.assertTrue(leveraged["requires_snapshot_manifest_path"])
        self.assertFalse(leveraged["requires_strategy_config_path"])

        tqqq = describe_platform_runtime_requirements("tqqq_growth_income", platform_id="ibkr")
        self.assertEqual(tqqq["profile_group"], "direct_runtime_inputs")
        self.assertEqual(tqqq["input_mode"], "benchmark_history+portfolio_snapshot")
        self.assertFalse(tqqq["requires_snapshot_artifacts"])
        self.assertFalse(tqqq["requires_strategy_config_path"])
        self.assertEqual(tqqq["signal_effective_after_trading_days"], 1)

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
                    "signal_effective_after_trading_days": 1,
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
            decision.diagnostics["notification_context"]["status"]["code"],
            legacy_plan["notification_context"]["status"]["code"],
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["notification_context"]["signal"]["code"],
            legacy_plan["notification_context"]["signal"]["code"],
        )
        self.assertEqual(decision.diagnostics["signal_date"], "2026-04-06")
        self.assertEqual(decision.diagnostics["effective_date"], "2026-04-07")
        self.assertEqual(decision.diagnostics["execution_timing_contract"], "next_trading_day")
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["signal_effective_after_trading_days"],
            1,
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["raw_buying_power"],
            legacy_plan["available_cash"],
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["reserved_cash"],
            legacy_plan["reserved_cash"],
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["investable_cash"],
            legacy_plan["investable_cash"],
        )
        self.assertIn(
            f"Buying power: ${legacy_plan['available_cash']:,.2f}",
            decision.diagnostics["dashboard"],
        )
        self.assertIn(
            f"Reserved cash: ${legacy_plan['reserved_cash']:,.2f}",
            decision.diagnostics["dashboard"],
        )
        self.assertIn(
            f"Investable cash: ${legacy_plan['investable_cash']:,.2f}",
            decision.diagnostics["dashboard"],
        )
        self.assertEqual(
            entrypoint.manifest.default_config["managed_symbols"],
            ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        )

    def test_soxl_soxx_trend_income_entrypoint_rejects_retired_fixed_dual_drive_runtime_config(self) -> None:
        entrypoint = get_strategy_entrypoint("soxl_soxx_trend_income")
        with self.assertRaisesRegex(ValueError, "soxx_gate_tiered_blend"):
            entrypoint.evaluate(
                StrategyContext(
                    as_of="2026-04-06",
                    market_data={
                        "derived_indicators": {
                            "soxl": {"price": 50.0, "ma_trend": 45.0},
                            "soxx": {
                                "price": 110.0,
                                "ma_trend": 100.0,
                                "ma20": 105.0,
                                "ma20_slope": 0.4,
                            },
                        },
                        "portfolio_snapshot": PortfolioSnapshot(
                            as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
                            total_equity=100000.0,
                            buying_power=10000.0,
                            positions=(Position(symbol="BOXX", quantity=1000, market_value=100000.0),),
                            metadata={"account_hash": "demo"},
                        ),
                    },
                    portfolio=PortfolioSnapshot(
                        as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
                        total_equity=100000.0,
                        buying_power=10000.0,
                        positions=(Position(symbol="BOXX", quantity=1000, market_value=100000.0),),
                        metadata={"account_hash": "demo"},
                    ),
                    runtime_config={
                        "translator": lambda key, **kwargs: key,
                        "attack_allocation_mode": "fixed_soxx_soxl_pullback",
                        "income_layer_start_usd": 1_000_000_000.0,
                    },
                )
            )

    def test_value_mode_semiconductor_runtime_adapters_use_canonical_inputs(self) -> None:
        for platform_id in ("schwab", "longbridge", "paper_signal"):
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

        mega = get_strategy_entrypoint("mega_cap_leader_rotation_dynamic_top20")
        mega_decision = mega.evaluate(
            StrategyContext(
                as_of="2026-04-01",
                market_data={"feature_snapshot": _mega_snapshot()},
                portfolio=PortfolioSnapshot(
                    as_of="2026-04-01",
                    total_equity=100_000.0,
                    buying_power=100_000.0,
                    cash_balance=100_000.0,
                    positions=(),
                ),
            )
        )
        self.assertEqual(mega_decision.diagnostics["signal_source"], "feature_snapshot")
        self.assertEqual(mega_decision.diagnostics["selected_count"], 4)
        self.assertNotIn("SPY", {position.symbol for position in mega_decision.positions})

        aggressive = get_strategy_entrypoint("mega_cap_leader_rotation_aggressive")
        aggressive_decision = aggressive.evaluate(
            StrategyContext(
                as_of="2026-04-01",
                market_data={"feature_snapshot": _mega_snapshot(qqq_sma200_gap=-0.02)},
                portfolio=PortfolioSnapshot(
                    as_of="2026-04-01",
                    total_equity=100_000.0,
                    buying_power=100_000.0,
                    cash_balance=100_000.0,
                    positions=(),
                ),
            )
        )
        self.assertEqual(aggressive_decision.diagnostics["signal_source"], "feature_snapshot")
        self.assertEqual(aggressive_decision.diagnostics["selected_count"], 3)
        self.assertEqual(aggressive_decision.diagnostics["target_stock_weight"], 1.0)
        self.assertNotIn("BOXX", {position.symbol for position in aggressive_decision.positions})

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
        paper_russell_adapter = get_platform_runtime_adapter(
            "russell_1000_multi_factor_defensive",
            platform_id="paper_signal",
        )
        self.assertEqual(
            paper_russell_adapter.available_inputs,
            frozenset({"feature_snapshot"}),
        )
        self.assertIsNone(paper_russell_adapter.portfolio_input_name)
        self.assertEqual(paper_russell_adapter.status_icon, "📏")

        tech_adapter = get_platform_runtime_adapter("qqq_tech_enhancement", platform_id="ibkr")
        self.assertEqual(tech_adapter.status_icon, "🧲")
        self.assertEqual(tech_adapter.snapshot_date_columns, ("as_of", "snapshot_date"))
        self.assertTrue(tech_adapter.require_snapshot_manifest)
        self.assertIsNotNone(tech_adapter.artifact_contract)
        self.assertTrue(tech_adapter.artifact_contract.requires_strategy_config_path)
        self.assertEqual(tech_adapter.artifact_contract.config_source_policy, "bundled_or_env")
        self.assertEqual(tech_adapter.runtime_policy.reconciliation_output_policy, "optional")
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
        self.assertEqual(longbridge_tech_adapter.runtime_policy.runtime_execution_window_trading_days, 1)
        schwab_tech_adapter = get_platform_runtime_adapter("qqq_tech_enhancement", platform_id="schwab")
        self.assertEqual(
            schwab_tech_adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(schwab_tech_adapter.portfolio_input_name, "portfolio_snapshot")
        paper_tech_adapter = get_platform_runtime_adapter("qqq_tech_enhancement", platform_id="paper_signal")
        self.assertEqual(
            paper_tech_adapter.available_inputs,
            frozenset({"feature_snapshot"}),
        )
        self.assertIsNone(paper_tech_adapter.portfolio_input_name)

        mega_adapter = get_platform_runtime_adapter("mega_cap_leader_rotation_dynamic_top20", platform_id="ibkr")
        self.assertEqual(mega_adapter.status_icon, "👑")
        self.assertEqual(mega_adapter.snapshot_date_columns, ("as_of", "snapshot_date"))
        self.assertTrue(mega_adapter.require_snapshot_manifest)
        self.assertEqual(
            mega_adapter.snapshot_contract_version,
            "mega_cap_leader_rotation_dynamic_top20.feature_snapshot.v1",
        )
        self.assertEqual(
            mega_adapter.managed_symbols_extractor(
                _mega_snapshot(),
                benchmark_symbol="QQQ",
                safe_haven="BOXX",
            ),
            mega_cap_managed_symbols(
                _mega_snapshot(),
                benchmark_symbol="QQQ",
                safe_haven="BOXX",
            ),
        )
        longbridge_mega_adapter = get_platform_runtime_adapter("mega_cap_leader_rotation_dynamic_top20", platform_id="longbridge")
        self.assertEqual(
            longbridge_mega_adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(longbridge_mega_adapter.portfolio_input_name, "portfolio_snapshot")
        paper_mega_adapter = get_platform_runtime_adapter("mega_cap_leader_rotation_dynamic_top20", platform_id="paper_signal")
        self.assertEqual(
            paper_mega_adapter.available_inputs,
            frozenset({"feature_snapshot"}),
        )
        self.assertIsNone(paper_mega_adapter.portfolio_input_name)

        aggressive_adapter = get_platform_runtime_adapter("mega_cap_leader_rotation_aggressive", platform_id="ibkr")
        self.assertEqual(aggressive_adapter.status_icon, "👑")
        self.assertTrue(aggressive_adapter.require_snapshot_manifest)
        self.assertEqual(
            aggressive_adapter.snapshot_contract_version,
            "mega_cap_leader_rotation_aggressive.feature_snapshot.v1",
        )
        balanced_adapter = get_platform_runtime_adapter("mega_cap_leader_rotation_top50_balanced", platform_id="ibkr")
        self.assertEqual(balanced_adapter.status_icon, "👑")
        self.assertTrue(balanced_adapter.require_snapshot_manifest)
        self.assertEqual(
            balanced_adapter.snapshot_contract_version,
            "mega_cap_leader_rotation_top50_balanced.feature_snapshot.v1",
        )

        for platform_id in ("schwab", "longbridge", "paper_signal"):
            dynamic_leveraged_adapter = get_platform_runtime_adapter(
                "dynamic_mega_leveraged_pullback",
                platform_id=platform_id,
            )
            self.assertEqual(dynamic_leveraged_adapter.status_icon, "2x")
            self.assertEqual(
                dynamic_leveraged_adapter.available_inputs,
                frozenset({"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}),
            )
            self.assertEqual(dynamic_leveraged_adapter.portfolio_input_name, "portfolio_snapshot")

        semiconductor_ibkr_adapter = get_platform_runtime_adapter(
            "soxl_soxx_trend_income",
            platform_id="ibkr",
        )
        self.assertEqual(
            semiconductor_ibkr_adapter.available_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )
        self.assertEqual(semiconductor_ibkr_adapter.portfolio_input_name, "portfolio_snapshot")
