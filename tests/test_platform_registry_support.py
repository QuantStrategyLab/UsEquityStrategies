import unittest

from us_equity_strategies.platform_registry_support import (
    build_platform_profile_matrix,
    get_enabled_profiles_for_platform,
    resolve_platform_strategy_definition,
)


class PlatformRegistrySupportTest(unittest.TestCase):
    def test_get_enabled_profiles_for_platform_filters_by_platform(self):
        enabled = frozenset({"mega_cap_leader_rotation_top50_balanced"})
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
            enabled_profiles=frozenset({"mega_cap_leader_rotation_top50_balanced"}),
            default_profile="global_etf_rotation",
            rollback_profile="global_etf_rotation",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["canonical_profile"], "mega_cap_leader_rotation_top50_balanced")
        self.assertEqual(rows[0]["display_name"], "Mega Cap Leader Rotation Top50 Balanced")
        self.assertEqual(rows[0]["aliases"], ())
        self.assertFalse(rows[0]["is_default"])
        self.assertFalse(rows[0]["is_rollback"])

    def test_resolve_platform_strategy_definition_supports_canonical_profile(self):
        definition = resolve_platform_strategy_definition(
            "mega_cap_leader_rotation_top50_balanced",
            platform_id="ibkr",
            expected_platform_id="ibkr",
            enabled_profiles=frozenset({"mega_cap_leader_rotation_top50_balanced"}),
            platform_supported_domains={"ibkr": frozenset({"us_equity"})},
            require_explicit=True,
        )
        self.assertEqual(definition.profile, "mega_cap_leader_rotation_top50_balanced")


class PlatformRegistryAliasSupportTest(unittest.TestCase):
    def test_resolve_platform_strategy_definition_supports_legacy_alias(self):
        definition = resolve_platform_strategy_definition(
            "r1000_multifactor_defensive",
            platform_id="ibkr",
            expected_platform_id="ibkr",
            enabled_profiles=frozenset({"russell_1000_multi_factor_defensive"}),
            platform_supported_domains={"ibkr": frozenset({"us_equity"})},
            require_explicit=True,
        )
        self.assertEqual(definition.profile, "russell_1000_multi_factor_defensive")


if __name__ == "__main__":
    unittest.main()
