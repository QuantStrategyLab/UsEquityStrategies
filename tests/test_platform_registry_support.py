import unittest

from us_equity_strategies.platform_registry_support import (
    build_platform_profile_matrix,
    get_enabled_profiles_for_platform,
    resolve_platform_strategy_definition,
)


class PlatformRegistrySupportTest(unittest.TestCase):
    def test_get_enabled_profiles_for_platform_filters_by_platform(self):
        enabled = frozenset({"qqq_tech_enhancement"})
        self.assertEqual(
            get_enabled_profiles_for_platform(
                "ibkr",
                expected_platform_id="ibkr",
                enabled_profiles=enabled,
            ),
            enabled,
        )
        self.assertEqual(
            get_enabled_profiles_for_platform(
                "schwab",
                expected_platform_id="ibkr",
                enabled_profiles=enabled,
            ),
            frozenset(),
        )

    def test_build_platform_profile_matrix_uses_metadata(self):
        rows = build_platform_profile_matrix(
            platform_id="ibkr",
            enabled_profiles=frozenset({"qqq_tech_enhancement"}),
            default_profile="global_etf_rotation",
            rollback_profile="global_etf_rotation",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["canonical_profile"], "qqq_tech_enhancement")
        self.assertEqual(rows[0]["display_name"], "QQQ Tech Enhancement")
        self.assertEqual(rows[0]["aliases"], ("tech_pullback_cash_buffer",))
        self.assertFalse(rows[0]["is_default"])
        self.assertFalse(rows[0]["is_rollback"])

    def test_resolve_platform_strategy_definition_supports_canonical_profile(self):
        definition = resolve_platform_strategy_definition(
            "qqq_tech_enhancement",
            platform_id="ibkr",
            expected_platform_id="ibkr",
            enabled_profiles=frozenset({"qqq_tech_enhancement"}),
            platform_supported_domains={"ibkr": frozenset({"us_equity"})},
            require_explicit=True,
        )
        self.assertEqual(definition.profile, "qqq_tech_enhancement")


if __name__ == "__main__":
    unittest.main()
