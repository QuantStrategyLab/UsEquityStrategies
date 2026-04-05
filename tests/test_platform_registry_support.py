import unittest

from us_equity_strategies.platform_registry_support import (
    build_platform_profile_matrix,
    get_enabled_profiles_for_platform,
    resolve_platform_strategy_definition,
)


class PlatformRegistrySupportTest(unittest.TestCase):
    def test_get_enabled_profiles_for_platform_filters_by_platform(self):
        enabled = frozenset({"cash_buffer_branch_default"})
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
            enabled_profiles=frozenset({"cash_buffer_branch_default"}),
            default_profile="global_etf_rotation",
            rollback_profile="global_etf_rotation",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["canonical_profile"], "cash_buffer_branch_default")
        self.assertEqual(rows[0]["display_name"], "Tech Pullback Cash Buffer")
        self.assertEqual(rows[0]["aliases"], ("tech_pullback_cash_buffer",))
        self.assertFalse(rows[0]["is_default"])
        self.assertFalse(rows[0]["is_rollback"])

    def test_resolve_platform_strategy_definition_supports_alias(self):
        definition = resolve_platform_strategy_definition(
            "tech_pullback_cash_buffer",
            platform_id="ibkr",
            expected_platform_id="ibkr",
            enabled_profiles=frozenset({"cash_buffer_branch_default"}),
            platform_supported_domains={"ibkr": frozenset({"us_equity"})},
            require_explicit=True,
        )
        self.assertEqual(definition.profile, "cash_buffer_branch_default")


if __name__ == "__main__":
    unittest.main()
