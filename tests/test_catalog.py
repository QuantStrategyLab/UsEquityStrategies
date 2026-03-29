import unittest

from us_equity_strategies import get_strategy_definitions
from us_equity_strategies.catalog import (
    GLOBAL_ETF_ROTATION_PROFILE,
    get_strategy_definition,
)


class CatalogTest(unittest.TestCase):
    def test_catalog_contains_global_etf_rotation(self):
        catalog = get_strategy_definitions()
        self.assertIn(GLOBAL_ETF_ROTATION_PROFILE, catalog)
        self.assertEqual(catalog[GLOBAL_ETF_ROTATION_PROFILE].domain, "us_equity")
        self.assertEqual(catalog[GLOBAL_ETF_ROTATION_PROFILE].supported_platforms, frozenset({"ibkr"}))

    def test_known_profile_resolves(self):
        definition = get_strategy_definition("global_etf_rotation")
        self.assertEqual(definition.profile, GLOBAL_ETF_ROTATION_PROFILE)


if __name__ == "__main__":
    unittest.main()
