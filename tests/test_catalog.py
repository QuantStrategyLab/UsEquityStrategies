import unittest

from quant_platform_kit.common.strategies import get_strategy_component_map
from us_equity_strategies import get_strategy_definitions
from us_equity_strategies.catalog import (
    GLOBAL_ETF_ROTATION_PROFILE,
    HYBRID_GROWTH_INCOME_PROFILE,
    SEMICONDUCTOR_ROTATION_INCOME_PROFILE,
    get_strategy_definition,
)


class CatalogTest(unittest.TestCase):
    def test_catalog_contains_supported_profiles(self):
        catalog = get_strategy_definitions()
        self.assertIn(GLOBAL_ETF_ROTATION_PROFILE, catalog)
        self.assertEqual(catalog[GLOBAL_ETF_ROTATION_PROFILE].domain, "us_equity")
        self.assertEqual(catalog[GLOBAL_ETF_ROTATION_PROFILE].supported_platforms, frozenset({"ibkr"}))

        self.assertIn(HYBRID_GROWTH_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[HYBRID_GROWTH_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(catalog[HYBRID_GROWTH_INCOME_PROFILE].supported_platforms, frozenset({"schwab"}))

        self.assertIn(SEMICONDUCTOR_ROTATION_INCOME_PROFILE, catalog)
        self.assertEqual(catalog[SEMICONDUCTOR_ROTATION_INCOME_PROFILE].domain, "us_equity")
        self.assertEqual(catalog[SEMICONDUCTOR_ROTATION_INCOME_PROFILE].supported_platforms, frozenset({"longbridge"}))

    def test_known_profile_resolves(self):
        definition = get_strategy_definition("global_etf_rotation")
        self.assertEqual(definition.profile, GLOBAL_ETF_ROTATION_PROFILE)
        signal_module = get_strategy_component_map(definition)["signal_logic"]
        self.assertEqual(
            signal_module.module_path,
            "us_equity_strategies.strategies.global_etf_rotation",
        )

        schwab_definition = get_strategy_definition("hybrid_growth_income")
        self.assertEqual(schwab_definition.profile, HYBRID_GROWTH_INCOME_PROFILE)
        allocation_module = get_strategy_component_map(schwab_definition)["allocation"]
        self.assertEqual(
            allocation_module.module_path,
            "us_equity_strategies.strategies.hybrid_growth_income",
        )

        longbridge_definition = get_strategy_definition("semiconductor_rotation_income")
        self.assertEqual(longbridge_definition.profile, SEMICONDUCTOR_ROTATION_INCOME_PROFILE)
        longbridge_module = get_strategy_component_map(longbridge_definition)["allocation"]
        self.assertEqual(
            longbridge_module.module_path,
            "us_equity_strategies.strategies.semiconductor_rotation_income",
        )


if __name__ == "__main__":
    unittest.main()
