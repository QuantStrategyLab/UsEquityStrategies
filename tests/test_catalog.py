import unittest

from quant_platform_kit.common.strategies import get_strategy_component_map
from us_equity_strategies import get_strategy_definitions
from us_equity_strategies.catalog import (
    FULL_SHARED_PLATFORM_MATRIX,
    GLOBAL_ETF_ROTATION_PROFILE,
    IBIT_SMART_DCA_PROFILE,
    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE,
    NASDAQ_SP500_SMART_DCA_PROFILE,
    TQQQ_GROWTH_INCOME_PROFILE,
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
    SOXL_SOXX_TREND_INCOME_PROFILE,
    audit_smart_dca_runtime_default_contract,
    get_compatible_platforms,
    get_profile_aliases,
    get_runtime_enabled_profiles,
    get_strategy_index_rows,
    get_strategy_definition,
    get_strategy_metadata_map,
    get_strategy_platform_compatibility_map,
    resolve_canonical_profile,
)


class CatalogTest(unittest.TestCase):
    def test_catalog_contains_supported_profiles(self):
        catalog = get_strategy_definitions()
        self.assertIn(GLOBAL_ETF_ROTATION_PROFILE, catalog)
        self.assertEqual(catalog[GLOBAL_ETF_ROTATION_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(GLOBAL_ETF_ROTATION_PROFILE),
            FULL_SHARED_PLATFORM_MATRIX,
        )
        self.assertEqual(
            catalog[GLOBAL_ETF_ROTATION_PROFILE].required_inputs,
            frozenset({"market_history"}),
        )

        self.assertNotIn("global_etf_confidence_vol_gate", catalog)

        self.assertIn(TQQQ_GROWTH_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[TQQQ_GROWTH_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(TQQQ_GROWTH_INCOME_PROFILE),
            FULL_SHARED_PLATFORM_MATRIX,
        )
        self.assertEqual(
            catalog[TQQQ_GROWTH_INCOME_PROFILE].required_inputs,
            frozenset({"benchmark_history", "portfolio_snapshot"}),
        )

        self.assertIn(SOXL_SOXX_TREND_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[SOXL_SOXX_TREND_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(SOXL_SOXX_TREND_INCOME_PROFILE),
            FULL_SHARED_PLATFORM_MATRIX,
        )
        self.assertEqual(
            catalog[SOXL_SOXX_TREND_INCOME_PROFILE].required_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )

        self.assertIn(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE, catalog)
        self.assertEqual(catalog[RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE),
            FULL_SHARED_PLATFORM_MATRIX,
        )

        self.assertIn(NASDAQ_SP500_SMART_DCA_PROFILE, catalog)
        self.assertEqual(catalog[NASDAQ_SP500_SMART_DCA_PROFILE].domain, "us_equity")
        self.assertEqual(
            catalog[NASDAQ_SP500_SMART_DCA_PROFILE].required_inputs,
            frozenset({"market_history", "portfolio_snapshot"}),
        )

        self.assertIn(IBIT_SMART_DCA_PROFILE, catalog)
        self.assertEqual(catalog[IBIT_SMART_DCA_PROFILE].domain, "us_equity")
        self.assertEqual(
            catalog[IBIT_SMART_DCA_PROFILE].required_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )

    def test_supported_platforms_remains_only_a_compatibility_mirror(self):
        catalog = get_strategy_definitions()
        compatibility = get_strategy_platform_compatibility_map()
        for profile, definition in catalog.items():
            self.assertEqual(definition.supported_platforms, compatibility[profile])

    def test_known_profile_resolves(self):
        definition = get_strategy_definition("global_etf_rotation")
        self.assertEqual(definition.profile, GLOBAL_ETF_ROTATION_PROFILE)
        signal_module = get_strategy_component_map(definition)["signal_logic"]
        self.assertEqual(
            signal_module.module_path,
            "us_equity_strategies.strategies.global_etf_rotation",
        )
        self.assertEqual(definition.default_config["sma_period"], 250)
        self.assertTrue(definition.default_config["confidence_weighting_enabled"])
        self.assertTrue(definition.default_config["confidence_volatility_gate_enabled"])
        self.assertEqual(
            get_strategy_definition("global_etf_confidence_vol_gate").profile,
            GLOBAL_ETF_ROTATION_PROFILE,
        )

        schwab_definition = get_strategy_definition("tqqq_growth_income")
        self.assertEqual(schwab_definition.profile, TQQQ_GROWTH_INCOME_PROFILE)
        allocation_module = get_strategy_component_map(schwab_definition)["allocation"]
        self.assertEqual(
            allocation_module.module_path,
            "us_equity_strategies.strategies.tqqq_growth_income",
        )

        longbridge_definition = get_strategy_definition("soxl_soxx_trend_income")
        self.assertEqual(longbridge_definition.profile, SOXL_SOXX_TREND_INCOME_PROFILE)
        self.assertTrue(longbridge_definition.default_config["blend_gate_volatility_delever_enabled"])
        self.assertEqual(longbridge_definition.default_config["blend_gate_volatility_delever_window"], 10)
        self.assertEqual(longbridge_definition.default_config["blend_gate_volatility_delever_threshold"], 0.55)
        self.assertEqual(
            longbridge_definition.default_config["blend_gate_volatility_delever_threshold_mode"],
            "rolling_percentile",
        )
        self.assertEqual(longbridge_definition.default_config["blend_gate_volatility_delever_dynamic_lookback"], 252)
        self.assertEqual(longbridge_definition.default_config["blend_gate_volatility_delever_dynamic_percentile"], 0.95)
        self.assertEqual(longbridge_definition.default_config["blend_gate_volatility_delever_dynamic_floor"], 0.50)
        self.assertEqual(longbridge_definition.default_config["blend_gate_volatility_delever_dynamic_cap"], 0.75)
        self.assertEqual(longbridge_definition.default_config["blend_gate_volatility_delever_redirect_symbol"], "SOXX")
        longbridge_module = get_strategy_component_map(longbridge_definition)["allocation"]
        self.assertEqual(
            longbridge_module.module_path,
            "us_equity_strategies.strategies.soxl_soxx_trend_income",
        )

        ibkr_definition = get_strategy_definition("russell_1000_multi_factor_defensive")
        self.assertEqual(ibkr_definition.profile, RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE)
        ibkr_module = get_strategy_component_map(ibkr_definition)["signal_logic"]
        self.assertEqual(
            ibkr_module.module_path,
            "us_equity_strategies.strategies.russell_1000_multi_factor_defensive",
        )

        balanced_definition = get_strategy_definition("mega_cap_leader_rotation_top50_balanced")
        self.assertEqual(balanced_definition.profile, MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE)
        balanced_module = get_strategy_component_map(balanced_definition)["signal_logic"]
        self.assertEqual(
            balanced_module.module_path,
            "us_equity_strategies.strategies.mega_cap_leader_rotation",
        )

        smart_dca_definition = get_strategy_definition("nasdaq_sp500_smart_dca")
        self.assertEqual(smart_dca_definition.profile, NASDAQ_SP500_SMART_DCA_PROFILE)
        smart_dca_module = get_strategy_component_map(smart_dca_definition)["allocation"]
        self.assertEqual(
            smart_dca_module.module_path,
            "us_equity_strategies.strategies.nasdaq_sp500_smart_dca",
        )
        self.assertEqual(smart_dca_definition.target_mode, "value")

        ibit_dca_definition = get_strategy_definition("ibit_smart_dca")
        self.assertEqual(ibit_dca_definition.profile, IBIT_SMART_DCA_PROFILE)
        ibit_dca_module = get_strategy_component_map(ibit_dca_definition)["allocation"]
        self.assertEqual(
            ibit_dca_module.module_path,
            "us_equity_strategies.strategies.ibit_smart_dca",
        )
        self.assertEqual(ibit_dca_definition.target_mode, "value")

    def test_aliases_resolve_to_canonical_profiles(self):
        self.assertEqual(resolve_canonical_profile("global_macro_etf_rotation"), GLOBAL_ETF_ROTATION_PROFILE)
        self.assertEqual(resolve_canonical_profile("r1000_multifactor_defensive"), RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE)
        for legacy_profile in (
            "hybrid_growth_income",
            "qqq_tqqq_growth_income",
            "semiconductor_rotation_income",
            "semiconductor_trend_income",
            "tech_pullback_cash_buffer",
            "qqq_tech_enhancement",
        ):
            with self.subTest(profile=legacy_profile):
                with self.assertRaises(ValueError):
                    resolve_canonical_profile(legacy_profile)

    def test_metadata_map_exposes_display_names_and_roles(self):
        metadata_map = get_strategy_metadata_map()
        self.assertEqual(metadata_map[GLOBAL_ETF_ROTATION_PROFILE].benchmark, "VOO")
        aliases = get_profile_aliases()
        self.assertEqual(
            aliases["global_etf_confidence_vol_gate"],
            GLOBAL_ETF_ROTATION_PROFILE,
        )
        self.assertNotIn("tech_pullback_cash_buffer", aliases)
        self.assertNotIn("qqq_tech_enhancement", aliases)
        compatibility = get_strategy_platform_compatibility_map()
        self.assertEqual(
            compatibility[TQQQ_GROWTH_INCOME_PROFILE],
            FULL_SHARED_PLATFORM_MATRIX,
        )
        self.assertEqual(
            metadata_map[GLOBAL_ETF_ROTATION_PROFILE].status,
            "runtime_enabled",
        )
        self.assertEqual(
            metadata_map[MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE].role,
            "balanced_leader_rotation",
        )
        self.assertEqual(
            compatibility[MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE],
            FULL_SHARED_PLATFORM_MATRIX,
        )
        self.assertEqual(metadata_map[NASDAQ_SP500_SMART_DCA_PROFILE].role, "buy_only_smart_dca")
        self.assertEqual(
            metadata_map[NASDAQ_SP500_SMART_DCA_PROFILE].display_name,
            "Nasdaq 100 / S&P 500 Smart DCA",
        )
        self.assertEqual(
            metadata_map[NASDAQ_SP500_SMART_DCA_PROFILE].localized_display_names["zh"],
            "纳指100 / 标普500 智能定投",
        )
        self.assertEqual(
            compatibility[NASDAQ_SP500_SMART_DCA_PROFILE],
            FULL_SHARED_PLATFORM_MATRIX,
        )
        self.assertEqual(
            metadata_map[IBIT_SMART_DCA_PROFILE].role,
            "buy_only_bitcoin_etf_smart_dca",
        )
        self.assertEqual(
            metadata_map[IBIT_SMART_DCA_PROFILE].status,
            "runtime_enabled",
        )
        self.assertEqual(
            compatibility[IBIT_SMART_DCA_PROFILE],
            FULL_SHARED_PLATFORM_MATRIX,
        )

    def test_option_overlay_defaults_are_scoped_to_supported_profiles(self):
        tqqq = get_strategy_definition(TQQQ_GROWTH_INCOME_PROFILE).default_config
        self.assertIs(tqqq["option_growth_overlay_enabled"], True)
        self.assertEqual(tqqq["option_growth_overlay_recipe"], "tqqq_leaps_growth_v1")
        self.assertEqual(tqqq["option_growth_overlay_start_usd"], 250000.0)
        self.assertEqual(tqqq["option_growth_overlay_nav_budget_ratio"], 0.03)

        soxl = get_strategy_definition(SOXL_SOXX_TREND_INCOME_PROFILE).default_config
        self.assertIs(soxl["option_income_overlay_enabled"], True)
        self.assertEqual(soxl["option_income_overlay_recipe"], "soxx_put_credit_spread_income_v1")
        self.assertEqual(soxl["option_income_overlay_start_usd"], 1000000.0)
        self.assertEqual(soxl["option_income_overlay_nav_risk_ratio"], 0.01)

        mega = get_strategy_definition(MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE).default_config
        self.assertIs(mega["option_growth_overlay_enabled"], True)
        self.assertEqual(mega["option_growth_overlay_recipe"], "qqq_leaps_growth_v1")
        self.assertEqual(mega["option_growth_overlay_start_usd"], 1000000.0)
        self.assertEqual(mega["option_growth_overlay_nav_budget_ratio"], 0.03)

        self.assertNotIn(
            "option_growth_overlay_enabled",
            get_strategy_definition(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE).default_config,
        )
        self.assertNotIn(
            "option_growth_overlay_enabled",
            get_strategy_definition(NASDAQ_SP500_SMART_DCA_PROFILE).default_config,
        )
        self.assertNotIn(
            "option_growth_overlay_enabled",
            get_strategy_definition(IBIT_SMART_DCA_PROFILE).default_config,
        )

    def test_dca_profiles_default_to_ordinary_dca_with_optional_smart_sizing(self):
        for profile in (NASDAQ_SP500_SMART_DCA_PROFILE, IBIT_SMART_DCA_PROFILE):
            with self.subTest(profile=profile):
                config = get_strategy_definition(profile).default_config
                self.assertEqual(config["investment_amount_mode"], "fixed")
                self.assertNotIn("available_cash_investment_ratio", config)
                self.assertIs(config["smart_multiplier_enabled"], False)
                self.assertEqual(config["cadence"], "monthly")
                self.assertEqual(config["cash_reserve_usd"], 0.0)
                self.assertIsNone(config["max_investment_usd"])
                self.assertEqual(config["min_investment_usd"], 5.0)
                self.assertEqual(config["monthly_window_calendar_days"], 5)
                self.assertEqual(config["weekly_window_calendar_days"], 4)
                self.assertEqual(config["quarterly_months"], (1, 4, 7, 10))
                self.assertEqual(config["quarterly_day"], 25)
                self.assertEqual(config["quarterly_window_calendar_days"], 5)

        nasdaq_config = get_strategy_definition(NASDAQ_SP500_SMART_DCA_PROFILE).default_config
        self.assertEqual(nasdaq_config["mild_pullback_multiplier"], 1.10)
        self.assertEqual(nasdaq_config["deep_pullback_multiplier"], 1.25)
        self.assertEqual(nasdaq_config["severe_pullback_multiplier"], 1.50)
        self.assertEqual(nasdaq_config["expensive_multiplier"], 1.0)
        self.assertEqual(nasdaq_config["very_expensive_multiplier"], 1.0)

        ibit_config = get_strategy_definition(IBIT_SMART_DCA_PROFILE).default_config
        self.assertEqual(ibit_config["mild_pullback_multiplier"], 1.50)
        self.assertEqual(ibit_config["deep_pullback_multiplier"], 2.25)
        self.assertEqual(ibit_config["severe_pullback_multiplier"], 3.0)
        self.assertEqual(ibit_config["expensive_multiplier"], 1.0)
        self.assertEqual(ibit_config["very_expensive_multiplier"], 1.0)
        self.assertIs(ibit_config["cycle_indicator_enabled"], True)
        self.assertEqual(ibit_config["ahr999_bottom_threshold"], 0.45)
        self.assertEqual(ibit_config["ahr999_accumulation_threshold"], 0.80)
        self.assertEqual(ibit_config["ahr999_dca_threshold"], 1.20)
        self.assertEqual(ibit_config["ahr999_bottom_multiplier"], 3.0)
        self.assertEqual(ibit_config["ahr999_accumulation_multiplier"], 2.25)
        self.assertEqual(ibit_config["ahr999_dca_multiplier"], 1.50)
        self.assertEqual(ibit_config["ahr999_expensive_multiplier"], 0.0)

    def test_dca_runtime_default_contract_matches_catalog_defaults(self):
        summary = audit_smart_dca_runtime_default_contract()

        self.assertEqual(
            summary["schema_version"],
            "smart_dca_runtime_default_contract.v1",
        )
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["failure_reasons"], ())
        self.assertEqual(
            summary["profiles"],
            (NASDAQ_SP500_SMART_DCA_PROFILE, IBIT_SMART_DCA_PROFILE),
        )
        for profile_contract in summary["profile_contracts"]:
            with self.subTest(profile=profile_contract["profile"]):
                self.assertTrue(profile_contract["passed"])
                self.assertEqual(
                    profile_contract["actual_values"]["investment_amount_mode"],
                    "fixed",
                )
                self.assertIs(
                    profile_contract["actual_values"]["smart_multiplier_enabled"],
                    False,
                )
                self.assertTrue(profile_contract["available_cash_ratio_absent"])
                self.assertEqual(profile_contract["target_mode"], "value")

    def test_market_regime_control_position_defaults_match_strategy_consumption_policy(self):
        self.assertIs(
            get_strategy_definition(TQQQ_GROWTH_INCOME_PROFILE).default_config["market_regime_control_enabled"],
            True,
        )
        soxl = get_strategy_definition(SOXL_SOXX_TREND_INCOME_PROFILE).default_config
        self.assertIs(soxl["market_regime_control_enabled"], True)
        self.assertIs(soxl["market_regime_control_apply_risk_reduced"], False)
        self.assertIs(soxl["market_regime_control_apply_risk_off"], True)

        promotion_pending_profiles = (
            GLOBAL_ETF_ROTATION_PROFILE,
            RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
            MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE,
        )
        for profile in promotion_pending_profiles:
            config = get_strategy_definition(profile).default_config
            self.assertIs(config["market_regime_control_enabled"], True)
            self.assertIs(config["market_regime_control_apply_risk_reduced"], False)
            self.assertIs(config["market_regime_control_apply_risk_off"], False)
            self.assertEqual(config["market_regime_control_risk_reduced_scalar"], 0.50)
            self.assertEqual(config["market_regime_control_risk_off_scalar"], 0.0)

        self.assertNotIn(
            "market_regime_control_enabled",
            get_strategy_definition(NASDAQ_SP500_SMART_DCA_PROFILE).default_config,
        )
        self.assertNotIn(
            "market_regime_control_enabled",
            get_strategy_definition(IBIT_SMART_DCA_PROFILE).default_config,
        )

    def test_strategy_index_rows_are_human_readable(self):
        rows = get_strategy_index_rows()
        by_profile = {row["canonical_profile"]: row for row in rows}
        self.assertEqual(by_profile[TQQQ_GROWTH_INCOME_PROFILE]["aliases"], ())
        self.assertIn("signal_logic", by_profile[GLOBAL_ETF_ROTATION_PROFILE]["component_names"])
        self.assertNotIn("global_etf_confidence_vol_gate", by_profile)
        self.assertNotIn("tech_communication_pullback_enhancement", by_profile)

    def test_removed_research_profiles_are_not_cataloged(self):
        catalog = get_strategy_definitions()
        for profile in (
            "mega_cap_leader_rotation_dynamic_top20",
            "mega_cap_leader_rotation_aggressive",
            "dynamic_mega_leveraged_pullback",
            "tech_communication_pullback_enhancement",
            "qqq_tech_enhancement",
        ):
            with self.subTest(profile=profile):
                self.assertNotIn(profile, catalog)
                with self.assertRaises(ValueError):
                    get_strategy_definition(profile)

    def test_runtime_enabled_profiles_are_the_live_catalog(self):
        runtime_enabled = get_runtime_enabled_profiles()
        self.assertEqual(
            runtime_enabled,
            frozenset(
                {
                    GLOBAL_ETF_ROTATION_PROFILE,
                    TQQQ_GROWTH_INCOME_PROFILE,
                    SOXL_SOXX_TREND_INCOME_PROFILE,
                    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
                    MEGA_CAP_LEADER_ROTATION_TOP50_BALANCED_PROFILE,
                    NASDAQ_SP500_SMART_DCA_PROFILE,
                    IBIT_SMART_DCA_PROFILE,
                }
            ),
        )


class LegacyProfileCompatibilityTest(unittest.TestCase):
    def test_legacy_cash_buffer_profile_is_not_supported_anymore(self):
        with self.assertRaises(ValueError):
            get_strategy_definition("cash_buffer_branch_default")


if __name__ == "__main__":
    unittest.main()
