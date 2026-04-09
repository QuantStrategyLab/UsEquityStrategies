from __future__ import annotations

import unittest
from pathlib import Path

from us_equity_strategies import get_strategy_definitions
from us_equity_strategies.catalog import STRATEGY_CATALOG
from us_equity_strategies.runtime_adapters import (
    IBKR_PLATFORM,
    LONGBRIDGE_PLATFORM,
    PLATFORM_RUNTIME_ADAPTERS,
    SCHWAB_PLATFORM,
    get_platform_runtime_adapter,
)


CANONICAL_REQUIRED_INPUTS = frozenset(
    {
        "market_history",
        "benchmark_history",
        "portfolio_snapshot",
        "derived_indicators",
        "feature_snapshot",
    }
)
LIVE_PROFILE_LEGACY_ALIASES = {
    "tqqq_growth_income",
    "soxl_soxx_trend_income",
    "qqq_tech_enhancement",
}
ALLOWED_TARGET_MODES = frozenset({"weight", "value"})
PLATFORM_NATIVE_TARGET_MODES = {
    IBKR_PLATFORM: "weight",
    SCHWAB_PLATFORM: "value",
    LONGBRIDGE_PLATFORM: "value",
}
GOVERNED_SOURCE_ROOTS = (
    Path(__file__).resolve().parents[1] / "src" / "us_equity_strategies" / "strategies",
    Path(__file__).resolve().parents[1] / "src" / "us_equity_strategies" / "entrypoints",
)
BANNED_SOURCE_SNIPPETS = (
    "os.getenv(",
    "os.environ",
    "environ.get(",
    "ibkr",
    "schwab",
    "longbridge",
    "ACCOUNT_GROUP",
    "ACCOUNT_REGION",
    "IBKR_",
    "SCHWAB_",
    "LONGBRIDGE_",
    "LONGPORT_",
)


class ContractGovernanceTests(unittest.TestCase):
    def test_required_inputs_are_canonical(self) -> None:
        for profile, definition in get_strategy_definitions().items():
            with self.subTest(profile=profile):
                self.assertTrue(definition.required_inputs)
                self.assertLessEqual(definition.required_inputs, CANONICAL_REQUIRED_INPUTS)

    def test_target_modes_are_explicit_and_supported(self) -> None:
        for profile, definition in get_strategy_definitions().items():
            with self.subTest(profile=profile):
                self.assertIn(definition.target_mode, ALLOWED_TARGET_MODES)

    def test_every_compatible_platform_has_runtime_adapter_coverage(self) -> None:
        for profile, definition in get_strategy_definitions().items():
            for platform_id in definition.supported_platforms:
                with self.subTest(profile=profile, platform_id=platform_id):
                    adapter = get_platform_runtime_adapter(profile, platform_id=platform_id)
                    self.assertLessEqual(definition.required_inputs, adapter.available_inputs)
                    if adapter.portfolio_input_name:
                        self.assertIn(adapter.portfolio_input_name, adapter.available_inputs)
                    if definition.target_mode != PLATFORM_NATIVE_TARGET_MODES[platform_id]:
                        self.assertTrue(
                            adapter.portfolio_input_name,
                            msg="cross-target-mode translation must declare a portfolio input",
                        )

    def test_runtime_adapter_map_matches_catalog_compatibility(self) -> None:
        compatibility_map = {
            profile: frozenset(definition.supported_platforms)
            for profile, definition in get_strategy_definitions().items()
        }
        for platform_id, adapters in PLATFORM_RUNTIME_ADAPTERS.items():
            supported_profiles = frozenset(
                profile for profile, platforms in compatibility_map.items() if platform_id in platforms
            )
            with self.subTest(platform_id=platform_id):
                self.assertEqual(frozenset(adapters), supported_profiles)

    def test_feature_snapshot_profiles_declare_snapshot_contract_metadata(self) -> None:
        for profile, definition in get_strategy_definitions().items():
            if "feature_snapshot" not in definition.required_inputs:
                continue
            for platform_id in definition.supported_platforms:
                adapter = get_platform_runtime_adapter(profile, platform_id=platform_id)
                with self.subTest(profile=profile, platform_id=platform_id):
                    self.assertIn("feature_snapshot", adapter.available_inputs)
                    self.assertTrue(adapter.required_feature_columns)
                    if adapter.require_snapshot_manifest:
                        self.assertTrue(adapter.snapshot_contract_version)
                    if definition.bundled_config_relpath:
                        self.assertTrue(callable(adapter.runtime_parameter_loader))

    def test_strategy_and_entrypoint_sources_do_not_branch_on_platform_or_env(self) -> None:
        for root in GOVERNED_SOURCE_ROOTS:
            for path in sorted(root.rglob("*.py")):
                text = path.read_text(encoding="utf-8").lower()
                for snippet in BANNED_SOURCE_SNIPPETS:
                    with self.subTest(path=str(path.relative_to(root.parent.parent)), snippet=snippet):
                        self.assertNotIn(snippet.lower(), text)

    def test_portability_smoke_resolves_every_catalog_profile(self) -> None:
        for profile in STRATEGY_CATALOG.definitions:
            definition = STRATEGY_CATALOG.definitions[profile]
            self.assertEqual(definition.profile, profile)

    def test_live_profiles_do_not_keep_legacy_aliases(self) -> None:
        for profile in LIVE_PROFILE_LEGACY_ALIASES:
            with self.subTest(profile=profile):
                self.assertEqual(STRATEGY_CATALOG.metadata[profile].aliases, ())


if __name__ == "__main__":
    unittest.main()
