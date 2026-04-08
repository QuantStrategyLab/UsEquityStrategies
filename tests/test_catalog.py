import unittest

from quant_platform_kit.common.strategies import get_strategy_component_map
from us_equity_strategies import get_strategy_definitions
from us_equity_strategies.catalog import (
    TECH_PULLBACK_CASH_BUFFER_PROFILE,
    GLOBAL_ETF_ROTATION_PROFILE,
    HYBRID_GROWTH_INCOME_PROFILE,
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
    SEMICONDUCTOR_ROTATION_INCOME_PROFILE,
    get_compatible_platforms,
    get_profile_aliases,
    get_strategy_index_rows,
    get_strategy_definition,
    get_strategy_metadata,
    get_strategy_metadata_map,
    get_strategy_platform_compatibility_map,
    resolve_canonical_profile,
)


class CatalogTest(unittest.TestCase):
    def test_catalog_contains_supported_profiles(self):
        catalog = get_strategy_definitions()
        self.assertIn(GLOBAL_ETF_ROTATION_PROFILE, catalog)
        self.assertEqual(catalog[GLOBAL_ETF_ROTATION_PROFILE].domain, "us_equity")
        self.assertEqual(get_compatible_platforms(GLOBAL_ETF_ROTATION_PROFILE), frozenset({"ibkr"}))
        self.assertEqual(
            catalog[GLOBAL_ETF_ROTATION_PROFILE].required_inputs,
            frozenset({"market_history"}),
        )

        self.assertIn(HYBRID_GROWTH_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[HYBRID_GROWTH_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(HYBRID_GROWTH_INCOME_PROFILE),
            frozenset({"schwab", "longbridge"}),
        )
        self.assertEqual(
            catalog[HYBRID_GROWTH_INCOME_PROFILE].required_inputs,
            frozenset({"benchmark_history", "portfolio_snapshot"}),
        )

        self.assertIn(SEMICONDUCTOR_ROTATION_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[SEMICONDUCTOR_ROTATION_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(SEMICONDUCTOR_ROTATION_INCOME_PROFILE),
            frozenset({"ibkr", "longbridge", "schwab"}),
        )
        self.assertEqual(
            catalog[SEMICONDUCTOR_ROTATION_INCOME_PROFILE].required_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )

        self.assertIn(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE, catalog)
        self.assertEqual(catalog[RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE].domain, "us_equity")
        self.assertEqual(get_compatible_platforms(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE), frozenset({"ibkr"}))

        self.assertIn(TECH_PULLBACK_CASH_BUFFER_PROFILE, catalog)
        self.assertEqual(catalog[TECH_PULLBACK_CASH_BUFFER_PROFILE].domain, "us_equity")
        self.assertEqual(get_compatible_platforms(TECH_PULLBACK_CASH_BUFFER_PROFILE), frozenset({"ibkr", "longbridge"}))

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

        schwab_definition = get_strategy_definition("tqqq_growth_income")
        self.assertEqual(schwab_definition.profile, HYBRID_GROWTH_INCOME_PROFILE)
        allocation_module = get_strategy_component_map(schwab_definition)["allocation"]
        self.assertEqual(
            allocation_module.module_path,
            "us_equity_strategies.strategies.hybrid_growth_income",
        )

        longbridge_definition = get_strategy_definition("soxl_soxx_trend_income")
        self.assertEqual(longbridge_definition.profile, SEMICONDUCTOR_ROTATION_INCOME_PROFILE)
        longbridge_module = get_strategy_component_map(longbridge_definition)["allocation"]
        self.assertEqual(
            longbridge_module.module_path,
            "us_equity_strategies.strategies.semiconductor_rotation_income",
        )

        ibkr_definition = get_strategy_definition("russell_1000_multi_factor_defensive")
        self.assertEqual(ibkr_definition.profile, RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE)
        ibkr_module = get_strategy_component_map(ibkr_definition)["signal_logic"]
        self.assertEqual(
            ibkr_module.module_path,
            "us_equity_strategies.strategies.russell_1000_multi_factor_defensive",
        )

        cash_buffer_definition = get_strategy_definition("qqq_tech_enhancement")
        self.assertEqual(cash_buffer_definition.profile, TECH_PULLBACK_CASH_BUFFER_PROFILE)
        cash_buffer_module = get_strategy_component_map(cash_buffer_definition)["signal_logic"]
        self.assertEqual(
            cash_buffer_module.module_path,
            "us_equity_strategies.strategies.tech_pullback_cash_buffer",
        )

    def test_aliases_resolve_to_canonical_profiles(self):
        self.assertEqual(resolve_canonical_profile("global_macro_etf_rotation"), GLOBAL_ETF_ROTATION_PROFILE)
        self.assertEqual(resolve_canonical_profile("r1000_multifactor_defensive"), RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE)
        self.assertEqual(resolve_canonical_profile("hybrid_growth_income"), HYBRID_GROWTH_INCOME_PROFILE)
        self.assertEqual(resolve_canonical_profile("qqq_tqqq_growth_income"), HYBRID_GROWTH_INCOME_PROFILE)
        self.assertEqual(resolve_canonical_profile("semiconductor_rotation_income"), SEMICONDUCTOR_ROTATION_INCOME_PROFILE)
        self.assertEqual(resolve_canonical_profile("semiconductor_trend_income"), SEMICONDUCTOR_ROTATION_INCOME_PROFILE)
        self.assertEqual(resolve_canonical_profile("tech_pullback_cash_buffer"), TECH_PULLBACK_CASH_BUFFER_PROFILE)
        self.assertEqual(get_strategy_definition("qqq_tech_enhancement").profile, TECH_PULLBACK_CASH_BUFFER_PROFILE)

    def test_metadata_map_exposes_display_names_and_roles(self):
        metadata_map = get_strategy_metadata_map()
        self.assertEqual(metadata_map[TECH_PULLBACK_CASH_BUFFER_PROFILE].display_name, "QQQ Tech Enhancement")
        self.assertEqual(metadata_map[TECH_PULLBACK_CASH_BUFFER_PROFILE].role, "parallel_cash_buffer_branch")
        self.assertEqual(metadata_map[GLOBAL_ETF_ROTATION_PROFILE].benchmark, "VOO")
        self.assertEqual(get_strategy_metadata("qqq_tech_enhancement").canonical_profile, TECH_PULLBACK_CASH_BUFFER_PROFILE)
        aliases = get_profile_aliases()
        self.assertNotIn("qqq_tech_enhancement", aliases)
        self.assertEqual(aliases["tech_pullback_cash_buffer"], TECH_PULLBACK_CASH_BUFFER_PROFILE)
        compatibility = get_strategy_platform_compatibility_map()
        self.assertEqual(compatibility[TECH_PULLBACK_CASH_BUFFER_PROFILE], frozenset({"ibkr", "longbridge"}))
        self.assertEqual(metadata_map[TECH_PULLBACK_CASH_BUFFER_PROFILE].status, "runtime_enabled")
        self.assertEqual(get_strategy_definition("qqq_tech_enhancement").target_mode, "weight")

    def test_strategy_index_rows_are_human_readable(self):
        rows = get_strategy_index_rows()
        by_profile = {row["canonical_profile"]: row for row in rows}
        self.assertEqual(by_profile[TECH_PULLBACK_CASH_BUFFER_PROFILE]["display_name"], "QQQ Tech Enhancement")
        self.assertEqual(
            by_profile[HYBRID_GROWTH_INCOME_PROFILE]["aliases"],
            ("hybrid_growth_income", "qqq_tqqq_growth_income"),
        )
        self.assertIn("signal_logic", by_profile[GLOBAL_ETF_ROTATION_PROFILE]["component_names"])
        self.assertEqual(
            by_profile[TECH_PULLBACK_CASH_BUFFER_PROFILE]["compatible_platforms"],
            frozenset({"ibkr", "longbridge"}),
        )


class LegacyProfileCompatibilityTest(unittest.TestCase):
    def test_legacy_cash_buffer_profile_is_not_supported_anymore(self):
        with self.assertRaises(ValueError):
            get_strategy_definition("cash_buffer_branch_default")


if __name__ == "__main__":
    unittest.main()
