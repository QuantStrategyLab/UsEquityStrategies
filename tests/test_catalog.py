import unittest

from quant_platform_kit.common.strategies import get_strategy_component_map
from us_equity_strategies import get_strategy_definitions
from us_equity_strategies.catalog import (
    GLOBAL_ETF_ROTATION_PROFILE,
    MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE,
    MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE,
    QQQ_TECH_ENHANCEMENT_PROFILE,
    TQQQ_GROWTH_INCOME_PROFILE,
    RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE,
    SOXL_SOXX_TREND_INCOME_PROFILE,
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
        self.assertEqual(
            get_compatible_platforms(GLOBAL_ETF_ROTATION_PROFILE),
            frozenset({"ibkr", "schwab", "longbridge"}),
        )
        self.assertEqual(
            catalog[GLOBAL_ETF_ROTATION_PROFILE].required_inputs,
            frozenset({"market_history"}),
        )

        self.assertIn(TQQQ_GROWTH_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[TQQQ_GROWTH_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(TQQQ_GROWTH_INCOME_PROFILE),
            frozenset({"ibkr", "schwab", "longbridge"}),
        )
        self.assertEqual(
            catalog[TQQQ_GROWTH_INCOME_PROFILE].required_inputs,
            frozenset({"benchmark_history", "portfolio_snapshot"}),
        )

        self.assertIn(SOXL_SOXX_TREND_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[SOXL_SOXX_TREND_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(SOXL_SOXX_TREND_INCOME_PROFILE),
            frozenset({"ibkr", "longbridge", "schwab"}),
        )
        self.assertEqual(
            catalog[SOXL_SOXX_TREND_INCOME_PROFILE].required_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )

        self.assertIn(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE, catalog)
        self.assertEqual(catalog[RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE),
            frozenset({"ibkr", "schwab", "longbridge"}),
        )

        self.assertIn(QQQ_TECH_ENHANCEMENT_PROFILE, catalog)
        self.assertEqual(catalog[QQQ_TECH_ENHANCEMENT_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(QQQ_TECH_ENHANCEMENT_PROFILE),
            frozenset({"ibkr", "schwab", "longbridge"}),
        )

        self.assertIn(MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE, catalog)
        self.assertEqual(catalog[MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE),
            frozenset({"ibkr", "schwab", "longbridge"}),
        )

        self.assertIn(MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE, catalog)
        self.assertEqual(catalog[MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE].domain, "us_equity")
        self.assertEqual(
            get_compatible_platforms(MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE),
            frozenset({"ibkr", "schwab", "longbridge"}),
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

        schwab_definition = get_strategy_definition("tqqq_growth_income")
        self.assertEqual(schwab_definition.profile, TQQQ_GROWTH_INCOME_PROFILE)
        allocation_module = get_strategy_component_map(schwab_definition)["allocation"]
        self.assertEqual(
            allocation_module.module_path,
            "us_equity_strategies.strategies.tqqq_growth_income",
        )

        longbridge_definition = get_strategy_definition("soxl_soxx_trend_income")
        self.assertEqual(longbridge_definition.profile, SOXL_SOXX_TREND_INCOME_PROFILE)
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

        cash_buffer_definition = get_strategy_definition("qqq_tech_enhancement")
        self.assertEqual(cash_buffer_definition.profile, QQQ_TECH_ENHANCEMENT_PROFILE)
        cash_buffer_module = get_strategy_component_map(cash_buffer_definition)["signal_logic"]
        self.assertEqual(
            cash_buffer_module.module_path,
            "us_equity_strategies.strategies.qqq_tech_enhancement",
        )

        mega_definition = get_strategy_definition("mega_cap_leader_rotation_dynamic_top20")
        self.assertEqual(mega_definition.profile, MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE)
        mega_module = get_strategy_component_map(mega_definition)["signal_logic"]
        self.assertEqual(
            mega_module.module_path,
            "us_equity_strategies.strategies.mega_cap_leader_rotation_dynamic_top20",
        )

        aggressive_definition = get_strategy_definition("mega_cap_leader_rotation_aggressive")
        self.assertEqual(aggressive_definition.profile, MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE)
        aggressive_module = get_strategy_component_map(aggressive_definition)["signal_logic"]
        self.assertEqual(
            aggressive_module.module_path,
            "us_equity_strategies.strategies.mega_cap_leader_rotation_dynamic_top20",
        )

    def test_aliases_resolve_to_canonical_profiles(self):
        self.assertEqual(resolve_canonical_profile("global_macro_etf_rotation"), GLOBAL_ETF_ROTATION_PROFILE)
        self.assertEqual(resolve_canonical_profile("r1000_multifactor_defensive"), RUSSELL_1000_MULTI_FACTOR_DEFENSIVE_PROFILE)
        self.assertEqual(get_strategy_definition("qqq_tech_enhancement").profile, QQQ_TECH_ENHANCEMENT_PROFILE)
        for legacy_profile in (
            "hybrid_growth_income",
            "qqq_tqqq_growth_income",
            "semiconductor_rotation_income",
            "semiconductor_trend_income",
            "tech_pullback_cash_buffer",
        ):
            with self.subTest(profile=legacy_profile):
                with self.assertRaises(ValueError):
                    resolve_canonical_profile(legacy_profile)

    def test_metadata_map_exposes_display_names_and_roles(self):
        metadata_map = get_strategy_metadata_map()
        self.assertEqual(metadata_map[QQQ_TECH_ENHANCEMENT_PROFILE].display_name, "Tech/Communication Pullback Enhancement")
        self.assertEqual(metadata_map[QQQ_TECH_ENHANCEMENT_PROFILE].role, "parallel_cash_buffer_branch")
        self.assertEqual(metadata_map[GLOBAL_ETF_ROTATION_PROFILE].benchmark, "VOO")
        self.assertEqual(get_strategy_metadata("qqq_tech_enhancement").canonical_profile, QQQ_TECH_ENHANCEMENT_PROFILE)
        aliases = get_profile_aliases()
        self.assertEqual(aliases["qqq_tech_enhancement"], QQQ_TECH_ENHANCEMENT_PROFILE)
        self.assertNotIn("tech_pullback_cash_buffer", aliases)
        compatibility = get_strategy_platform_compatibility_map()
        self.assertEqual(
            compatibility[QQQ_TECH_ENHANCEMENT_PROFILE],
            frozenset({"ibkr", "schwab", "longbridge"}),
        )
        self.assertEqual(
            compatibility[TQQQ_GROWTH_INCOME_PROFILE],
            frozenset({"ibkr", "schwab", "longbridge"}),
        )
        self.assertEqual(metadata_map[QQQ_TECH_ENHANCEMENT_PROFILE].status, "runtime_enabled")
        self.assertEqual(get_strategy_definition("qqq_tech_enhancement").target_mode, "weight")
        self.assertEqual(
            metadata_map[MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE].role,
            "concentrated_leader_rotation",
        )
        self.assertEqual(
            compatibility[MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE],
            frozenset({"ibkr", "schwab", "longbridge"}),
        )
        self.assertEqual(
            metadata_map[MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE].role,
            "aggressive_leader_rotation",
        )
        self.assertEqual(
            compatibility[MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE],
            frozenset({"ibkr", "schwab", "longbridge"}),
        )

    def test_strategy_index_rows_are_human_readable(self):
        rows = get_strategy_index_rows()
        by_profile = {row["canonical_profile"]: row for row in rows}
        self.assertEqual(by_profile[QQQ_TECH_ENHANCEMENT_PROFILE]["display_name"], "Tech/Communication Pullback Enhancement")
        self.assertEqual(by_profile[TQQQ_GROWTH_INCOME_PROFILE]["aliases"], ())
        self.assertIn("signal_logic", by_profile[GLOBAL_ETF_ROTATION_PROFILE]["component_names"])
        self.assertEqual(
            by_profile[QQQ_TECH_ENHANCEMENT_PROFILE]["compatible_platforms"],
            frozenset({"ibkr", "schwab", "longbridge"}),
        )
        self.assertEqual(
            by_profile[MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE]["display_name"],
            "Mega Cap Leader Rotation Dynamic Top20",
        )
        self.assertEqual(
            by_profile[MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE]["display_name"],
            "Mega Cap Leader Rotation Aggressive",
        )


class LegacyProfileCompatibilityTest(unittest.TestCase):
    def test_legacy_cash_buffer_profile_is_not_supported_anymore(self):
        with self.assertRaises(ValueError):
            get_strategy_definition("cash_buffer_branch_default")


if __name__ == "__main__":
    unittest.main()
