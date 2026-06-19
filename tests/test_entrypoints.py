from __future__ import annotations

import unittest

import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies import get_platform_runtime_adapter, get_strategy_entrypoint
from us_equity_strategies.catalog import get_runtime_enabled_profiles
from us_equity_strategies.entrypoints._common import OPTION_OVERLAY_CONFIG_KEYS, build_option_overlay_diagnostics
from us_equity_strategies.runtime_adapters import describe_platform_runtime_requirements
from us_equity_strategies.strategies.global_etf_rotation import compute_signals as legacy_global_compute_signals
from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan as tqqq_growth_build_rebalance_plan
from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan as soxl_soxx_trend_build_rebalance_plan
from us_equity_strategies.strategies.mega_cap_leader_rotation import extract_managed_symbols as mega_cap_managed_symbols

from tests.test_mega_cap_leader_rotation import _mega_snapshot


class StrategyEntrypointTests(unittest.TestCase):
    def test_option_overlay_diagnostics_respect_start_threshold(self) -> None:
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
            total_equity=300000.0,
            positions=(),
        )

        diagnostics = build_option_overlay_diagnostics(
            {
                "option_growth_overlay_enabled": True,
                "option_growth_overlay_recipe": "tqqq_leaps_growth_v1",
                "option_growth_overlay_start_usd": 250000.0,
            },
            StrategyContext(
                as_of="2026-04-06",
                portfolio=snapshot,
                market_data={
                    "option_chains": {
                        "TQQQ": {
                            "contracts": (
                                {
                                    "right": "C",
                                    "expiration": "2028-01-21",
                                    "strike": 70.0,
                                    "delta": 0.74,
                                    "bid": 29.0,
                                    "ask": 31.0,
                                },
                            ),
                        },
                    },
                },
            ),
        )

        self.assertIs(diagnostics["option_growth_overlay_active"], True)
        self.assertNotIn("option_growth_overlay_skip_reason", diagnostics)
        self.assertEqual(
            diagnostics["option_growth_overlay_recipe_detail"]["premium_budget_ratio"],
            0.03,
        )
        option_intents = diagnostics["option_order_intents"]["intents"]
        self.assertEqual(len(option_intents), 1)
        self.assertEqual(option_intents[0]["action"], "buy_to_open")
        self.assertEqual(option_intents[0]["underlier"], "TQQQ")
        self.assertEqual(option_intents[0]["quantity"], 2)

    def test_option_overlay_nav_budget_ratio_is_the_only_exposed_growth_knob(self) -> None:
        snapshot = PortfolioSnapshot(
            as_of=pd.Timestamp("2026-04-06").to_pydatetime(),
            total_equity=300000.0,
            positions=(),
        )

        diagnostics = build_option_overlay_diagnostics(
            {
                "option_growth_overlay_enabled": True,
                "option_growth_overlay_recipe": "tqqq_leaps_growth_v1",
                "option_growth_overlay_start_usd": 250000.0,
                "option_growth_overlay_nav_budget_ratio": 0.06,
            },
            StrategyContext(
                as_of="2026-04-06",
                portfolio=snapshot,
                market_data={
                    "option_chains": {
                        "TQQQ": {
                            "contracts": (
                                {
                                    "right": "C",
                                    "expiration": "2028-01-21",
                                    "strike": 70.0,
                                    "delta": 0.74,
                                    "bid": 29.0,
                                    "ask": 31.0,
                                },
                            ),
                        },
                    },
                },
            ),
        )

        self.assertEqual(
            diagnostics["option_growth_overlay_recipe_detail"]["premium_budget_ratio"],
            0.06,
        )
        self.assertEqual(diagnostics["option_order_intents"]["intents"][0]["quantity"], 5)

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
            sma_period=entrypoint.manifest.default_config["sma_period"],
            confidence_weighting_enabled=entrypoint.manifest.default_config["confidence_weighting_enabled"],
            confidence_threshold=entrypoint.manifest.default_config["confidence_threshold"],
            confidence_top1_weight=entrypoint.manifest.default_config["confidence_top1_weight"],
            confidence_volatility_gate_enabled=entrypoint.manifest.default_config["confidence_volatility_gate_enabled"],
            confidence_volatility_window=entrypoint.manifest.default_config["confidence_volatility_window"],
            confidence_volatility_max_ratio=entrypoint.manifest.default_config["confidence_volatility_max_ratio"],
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
        confidence_adapter = get_platform_runtime_adapter("global_etf_confidence_vol_gate", platform_id="ibkr")
        self.assertEqual(confidence_adapter, adapter)
        confidence_entrypoint = get_strategy_entrypoint("global_etf_confidence_vol_gate")
        self.assertEqual(confidence_entrypoint.manifest.profile, "global_etf_rotation")
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
        for platform_id in ("schwab", "longbridge", "firstrade"):
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
                if key not in {
                    "benchmark_symbol",
                    "managed_symbols",
                    "execution_cash_reserve_ratio",
                    "ai_extensions",
                    *OPTION_OVERLAY_CONFIG_KEYS,
                }
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
        strategy_equity = snapshot.total_equity - 3200.0
        self.assertAlmostEqual(target_values["QQQM"], strategy_equity * 0.45)
        self.assertAlmostEqual(target_values["TQQQ"], strategy_equity * 0.45)
        self.assertAlmostEqual(target_values["BOXX"], strategy_equity * 0.08)
        self.assertEqual(target_values["SCHD"], 0.0)
        self.assertEqual(target_values["DGRO"], 0.0)
        self.assertEqual(target_values["SGOV"], 0.0)
        self.assertEqual(target_values["SPYI"], 1500.0)
        self.assertEqual(target_values["QQQI"], 1700.0)
        self.assertNotIn("sell_order_symbols", decision.diagnostics)
        self.assertNotIn("portfolio_rows", decision.diagnostics)
        self.assertEqual(decision.diagnostics["threshold"], legacy_plan["threshold"])
        self.assertIs(decision.diagnostics["option_growth_overlay_enabled"], True)
        self.assertEqual(decision.diagnostics["option_growth_overlay_recipe"], "tqqq_leaps_growth_v1")
        self.assertEqual(decision.diagnostics["option_growth_overlay_start_usd"], 250000.0)
        self.assertIs(decision.diagnostics["option_growth_overlay_active"], False)
        self.assertEqual(decision.diagnostics["option_growth_overlay_skip_reason"], "below_start_usd")
        self.assertFalse(decision.diagnostics["ai_extensions"]["enabled"])
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
            ("TQQQ", "QQQM", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
        )

    def test_tqqq_growth_income_defaults_to_dynamic_dual_drive_live_profile(self) -> None:
        config = get_strategy_entrypoint("tqqq_growth_income").manifest.default_config

        self.assertEqual(config["attack_allocation_mode"], "fixed_qqq_tqqq_pullback")
        self.assertEqual(config["dual_drive_qqq_weight"], 0.45)
        self.assertEqual(config["dual_drive_tqqq_weight"], 0.45)
        self.assertEqual(config["dual_drive_unlevered_symbol"], "QQQM")
        self.assertEqual(config["dual_drive_cash_reserve_ratio"], 0.02)
        self.assertEqual(config["dual_drive_pullback_rebound_window"], 20)
        self.assertEqual(config["dual_drive_pullback_rebound_threshold_mode"], "volatility_scaled")
        self.assertEqual(config["dual_drive_pullback_rebound_threshold"], 0.0)
        self.assertEqual(config["dual_drive_pullback_rebound_volatility_multiplier"], 2.0)
        self.assertIs(config["dual_drive_volatility_delever_enabled"], True)
        self.assertEqual(config["dual_drive_volatility_delever_window"], 5)
        self.assertEqual(config["dual_drive_volatility_delever_threshold"], 0.28)
        self.assertEqual(config["dual_drive_volatility_delever_exit_threshold"], 0.28)
        self.assertEqual(config["dual_drive_volatility_delever_threshold_mode"], "rolling_percentile")
        self.assertEqual(config["dual_drive_volatility_delever_dynamic_lookback"], 252)
        self.assertEqual(config["dual_drive_volatility_delever_dynamic_percentile"], 0.90)
        self.assertEqual(config["dual_drive_volatility_delever_dynamic_min_periods"], 126)
        self.assertEqual(config["dual_drive_volatility_delever_dynamic_floor"], 0.24)
        self.assertEqual(config["dual_drive_volatility_delever_dynamic_cap"], 0.36)
        self.assertIs(config["dual_drive_volatility_delever_taco_veto_enabled"], True)
        self.assertIs(config["market_regime_control_enabled"], True)
        self.assertEqual(config["cash_reserve_ratio"], 0.02)
        self.assertEqual(config["income_threshold_usd"], 250000.0)
        self.assertIs(config["income_layer_enabled"], True)
        self.assertEqual(config["income_layer_start_usd"], 250000.0)
        self.assertEqual(config["income_layer_max_ratio"], 0.55)
        self.assertEqual(config["income_layer_activation_band_ratio"], 0.20)
        self.assertEqual(config["income_layer_ratio_mode"], "log_total_drawdown_budget")
        self.assertEqual(config["income_layer_core_stress_drawdown_ratio"], 0.45)
        self.assertEqual(config["income_layer_income_stress_drawdown_ratio"], 0.08)
        self.assertEqual(config["income_layer_base_drawdown_budget_ratio"], 0.45)
        self.assertEqual(config["income_layer_min_drawdown_budget_ratio"], 0.25)
        self.assertEqual(config["income_layer_drawdown_budget_decay_per_double"], 0.05)
        self.assertEqual(
            config["income_layer_allocations"],
            {"SCHD": 0.30, "DGRO": 0.20, "SGOV": 0.40, "SPYI": 0.08, "QQQI": 0.02},
        )
        self.assertEqual(config["execution_cash_reserve_ratio"], 0.0)
        self.assertIs(config["option_growth_overlay_enabled"], True)
        self.assertEqual(config["option_growth_overlay_recipe"], "tqqq_leaps_growth_v1")
        self.assertEqual(config["option_growth_overlay_start_usd"], 250000.0)
        self.assertEqual(config["option_growth_overlay_nav_budget_ratio"], 0.03)
        self.assertFalse(config["ai_extensions"]["enabled"])
        self.assertFalse(config["ai_extensions"]["modules"]["taco_panic_rebound"]["enabled"])
        self.assertFalse(config["ai_extensions"]["modules"]["crisis_regime_guard"]["enabled"])
        self.assertIn("QQQM", config["managed_symbols"])
        self.assertNotIn("QQQ", config["managed_symbols"])

    def test_weight_mode_profiles_default_to_income_layer_config(self) -> None:
        expected = {
            "global_etf_rotation": (
                500000.0,
                0.15,
                0.10,
                {"SCHD": 0.40, "DGRO": 0.25, "SGOV": 0.30, "SPYI": 0.05},
            ),
            "russell_top50_leader_rotation_aggressive": (
                300000.0,
                0.25,
                0.15,
                {"SCHD": 0.45, "DGRO": 0.30, "SGOV": 0.25},
            ),
        }
        for profile, (start_usd, max_ratio, activation_band_ratio, allocations) in expected.items():
            config = get_strategy_entrypoint(profile).manifest.default_config
            self.assertIs(config["income_layer_enabled"], True)
            self.assertEqual(config["income_layer_start_usd"], start_usd)
            self.assertEqual(config["income_layer_max_ratio"], max_ratio)
            self.assertEqual(config["income_layer_activation_band_ratio"], activation_band_ratio)
            self.assertEqual(config["income_layer_ratio_mode"], "log_total_drawdown_budget")
            self.assertEqual(config["income_layer_allocations"], allocations)

    def test_value_mode_hybrid_runtime_adapters_use_canonical_inputs(self) -> None:
        for platform_id in ("ibkr", "schwab", "longbridge", "firstrade", "paper_signal"):
            adapter = get_platform_runtime_adapter("tqqq_growth_income", platform_id=platform_id)
            self.assertEqual(
                adapter.available_inputs,
                frozenset({"benchmark_history", "portfolio_snapshot"}),
            )
            self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

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
                Position(symbol="QQQM", quantity=0, market_value=0.0),
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
        self.assertIn("QQQM", target_values)
        self.assertGreater(target_values["QQQM"], 0.0)

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
        mega = describe_platform_runtime_requirements("russell_top50_leader_rotation_aggressive", platform_id="ibkr")
        self.assertEqual(mega["profile_group"], "snapshot_backed")
        self.assertEqual(mega["input_mode"], "feature_snapshot")
        self.assertTrue(mega["requires_snapshot_artifacts"])
        self.assertTrue(mega["requires_snapshot_manifest_path"])
        self.assertFalse(mega["requires_strategy_config_path"])
        self.assertEqual(mega["config_source_policy"], "none")
        self.assertEqual(
            mega["snapshot_contract_version"],
            "russell_top50_leader_rotation_aggressive.feature_snapshot.v1",
        )

        tqqq = describe_platform_runtime_requirements("tqqq_growth_income", platform_id="ibkr")
        self.assertEqual(tqqq["profile_group"], "direct_runtime_inputs")
        self.assertEqual(tqqq["input_mode"], "benchmark_history+portfolio_snapshot")
        self.assertFalse(tqqq["requires_snapshot_artifacts"])
        self.assertFalse(tqqq["requires_strategy_config_path"])
        self.assertEqual(tqqq["signal_effective_after_trading_days"], 1)

        for removed_profile in (
            "tech_communication_pullback_enhancement",
            "qqq_tech_enhancement",
            "russell_1000_multi_factor_defensive",
            "r1000_multifactor_defensive",
            "mega_cap_leader_rotation_top50_balanced",
        ):
            with self.subTest(profile=removed_profile):
                with self.assertRaises(ValueError):
                    describe_platform_runtime_requirements(removed_profile, platform_id="schwab")

    def test_soxl_soxx_trend_income_entrypoint_maps_target_values_without_execution_fields(self) -> None:
        entrypoint = get_strategy_entrypoint("soxl_soxx_trend_income")
        indicators = {
            "soxl": {"price": 80.0, "ma_trend": 75.0},
            "soxx": {
                "price": 80.0,
                "ma_trend": 75.0,
                "realized_volatility_10": 0.20,
                "realized_volatility_10_dynamic_threshold": 0.50,
                "realized_volatility_10_dynamic_sample_count": 252.0,
            },
        }
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
                if key != "managed_symbols" and key not in OPTION_OVERLAY_CONFIG_KEYS
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
        self.assertIs(decision.diagnostics["option_income_overlay_enabled"], True)
        self.assertEqual(decision.diagnostics["option_income_overlay_recipe"], "soxx_put_credit_spread_income_v1")
        self.assertEqual(decision.diagnostics["option_income_overlay_start_usd"], 1000000.0)
        self.assertEqual(
            decision.diagnostics["option_income_overlay_recipe_detail"]["max_loss_budget_ratio"],
            0.01,
        )
        self.assertIs(decision.diagnostics["option_income_overlay_active"], False)
        self.assertEqual(decision.diagnostics["option_income_overlay_skip_reason"], "below_start_usd")
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
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["blend_gate_volatility_delever_threshold_mode"],
            "rolling_percentile",
        )
        self.assertEqual(
            decision.diagnostics["execution_annotations"]["blend_gate_volatility_delever_dynamic_threshold"],
            0.50,
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
            ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
        )
        self.assertIs(entrypoint.manifest.default_config["income_layer_enabled"], True)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_ratio_mode"], "log_total_drawdown_budget")
        self.assertEqual(entrypoint.manifest.default_config["income_layer_start_usd"], 150000.0)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_max_ratio"], 0.95)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_activation_band_ratio"], 0.20)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_core_stress_drawdown_ratio"], 0.45)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_income_stress_drawdown_ratio"], 0.06)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_base_drawdown_budget_ratio"], 0.45)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_min_drawdown_budget_ratio"], 0.25)
        self.assertEqual(entrypoint.manifest.default_config["income_layer_drawdown_budget_decay_per_double"], 0.05)
        self.assertIs(entrypoint.manifest.default_config["market_regime_control_enabled"], True)
        self.assertIs(entrypoint.manifest.default_config["market_regime_control_apply_risk_reduced"], False)
        self.assertIs(entrypoint.manifest.default_config["market_regime_control_apply_risk_off"], True)
        self.assertEqual(
            entrypoint.manifest.default_config["income_layer_allocations"],
            {"SCHD": 0.15, "DGRO": 0.10, "SGOV": 0.70, "SPYI": 0.04, "QQQI": 0.01},
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
        for platform_id in ("schwab", "longbridge", "firstrade", "paper_signal"):
            adapter = get_platform_runtime_adapter("soxl_soxx_trend_income", platform_id=platform_id)
            self.assertEqual(
                adapter.available_inputs,
                frozenset({"derived_indicators", "portfolio_snapshot"}),
            )
            self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_snapshot_entrypoints_match_legacy_weight_outputs(self) -> None:
        mega = get_strategy_entrypoint("russell_top50_leader_rotation_aggressive")
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
        self.assertIn("blend_sleeves", mega_decision.diagnostics)
        self.assertNotIn("SPY", {position.symbol for position in mega_decision.positions})

    def test_weight_mode_income_layer_scales_core_weights_when_portfolio_is_large(self) -> None:
        mega = get_strategy_entrypoint("russell_top50_leader_rotation_aggressive")
        decision = mega.evaluate(
            StrategyContext(
                as_of="2026-04-01",
                market_data={"feature_snapshot": _mega_snapshot()},
                portfolio=PortfolioSnapshot(
                    as_of="2026-04-01",
                    total_equity=1_000_000.0,
                    buying_power=1_000_000.0,
                    cash_balance=1_000_000.0,
                    positions=(),
                ),
            )
        )

        weights = {position.symbol: position.target_weight for position in decision.positions}
        self.assertTrue(decision.diagnostics["income_layer_applied"])
        self.assertGreater(decision.diagnostics["income_layer_ratio"], 0.0)
        self.assertIn("SCHD", weights)
        self.assertIn("DGRO", weights)
        self.assertIn("SGOV", weights)
        income_symbols = {"SCHD", "DGRO", "SGOV", "SPYI", "QQQI"}
        self.assertLess(
            sum(weight for symbol, weight in weights.items() if symbol not in income_symbols),
            1.0,
        )

    def test_removed_tech_profile_has_no_runtime_entrypoint(self) -> None:
        for removed_profile in (
            "tech_communication_pullback_enhancement",
            "qqq_tech_enhancement",
            "russell_1000_multi_factor_defensive",
            "r1000_multifactor_defensive",
            "mega_cap_leader_rotation_top50_balanced",
        ):
            with self.subTest(profile=removed_profile):
                with self.assertRaises(ValueError):
                    get_strategy_entrypoint(removed_profile)
                with self.assertRaises(ValueError):
                    get_platform_runtime_adapter(removed_profile, platform_id="ibkr")

    def test_ibkr_runtime_adapters_expose_unified_snapshot_runtime_metadata(self) -> None:
        global_adapter = get_platform_runtime_adapter("global_macro_etf_rotation", platform_id="ibkr")
        self.assertEqual(global_adapter.status_icon, "🐤")

        mega_adapter = get_platform_runtime_adapter("russell_top50_leader_rotation_aggressive", platform_id="ibkr")
        self.assertEqual(mega_adapter.status_icon, "👑")
        self.assertEqual(mega_adapter.snapshot_date_columns, ("as_of", "snapshot_date"))
        self.assertTrue(mega_adapter.require_snapshot_manifest)
        self.assertEqual(
            mega_adapter.snapshot_contract_version,
            "russell_top50_leader_rotation_aggressive.feature_snapshot.v1",
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
        longbridge_mega_adapter = get_platform_runtime_adapter("russell_top50_leader_rotation_aggressive", platform_id="longbridge")
        self.assertEqual(
            longbridge_mega_adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(longbridge_mega_adapter.portfolio_input_name, "portfolio_snapshot")
        firstrade_mega_adapter = get_platform_runtime_adapter(
            "russell_top50_leader_rotation_aggressive",
            platform_id="firstrade",
        )
        self.assertEqual(
            firstrade_mega_adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(firstrade_mega_adapter.portfolio_input_name, "portfolio_snapshot")
        paper_mega_adapter = get_platform_runtime_adapter("russell_top50_leader_rotation_aggressive", platform_id="paper_signal")
        self.assertEqual(
            paper_mega_adapter.available_inputs,
            frozenset({"feature_snapshot"}),
        )
        self.assertIsNone(paper_mega_adapter.portfolio_input_name)

        semiconductor_ibkr_adapter = get_platform_runtime_adapter(
            "soxl_soxx_trend_income",
            platform_id="ibkr",
        )
        self.assertEqual(
            semiconductor_ibkr_adapter.available_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )
        self.assertEqual(semiconductor_ibkr_adapter.portfolio_input_name, "portfolio_snapshot")
