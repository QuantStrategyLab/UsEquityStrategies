from __future__ import annotations

import unittest
from pathlib import Path

from us_equity_strategies import get_strategy_definitions
from us_equity_strategies.catalog import (
    STRATEGY_CATALOG,
    STRATEGY_DEFAULT_CONFIG,
    get_runtime_enabled_profiles,
)
from us_equity_strategies.manifests import dynamic_mega_leveraged_pullback_manifest
from us_equity_strategies.runtime_adapters import (
    BASE_RUNTIME_ADAPTERS,
    IBKR_PLATFORM,
    LONGBRIDGE_PLATFORM,
    PLATFORM_RUNTIME_ADAPTERS,
    SCHWAB_PLATFORM,
    get_platform_runtime_adapter,
)
from us_equity_strategies.strategies.dynamic_mega_leveraged_pullback import (
    DEFAULT_CONFIG as DYNAMIC_MEGA_LEVERAGED_PULLBACK_DEFAULT_CONFIG,
    PROFILE_NAME as DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE,
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
GOVERNED_REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_PROFILE_KEYS = (
    "hybrid_growth_income",
    "semiconductor_rotation_income",
    "tech_pullback_cash_buffer",
)
LEGACY_PROFILE_KEY_ALLOWED_PATHS = frozenset(
    {
        "docs/us_equity_portability_checklist.md",
        "tests/test_catalog.py",
        "tests/test_contract_governance.py",
    }
)
LIVE_PROFILE_LEGACY_ALIASES = {
    "tqqq_growth_income",
    "soxl_soxx_trend_income",
}
LIVE_PROFILE_TRANSITION_ALIASES = {
    "tech_communication_pullback_enhancement": ("qqq_tech_enhancement",),
}
LIVE_US_EQUITY_FULL_MATRIX_PROFILES = get_runtime_enabled_profiles()
ALLOWED_TARGET_MODES = frozenset({"weight", "value"})
PLATFORM_NATIVE_TARGET_MODES = {
    IBKR_PLATFORM: "weight",
    SCHWAB_PLATFORM: "value",
    LONGBRIDGE_PLATFORM: "value",
}
FULL_MATRIX_PLATFORMS = frozenset(
    {
        IBKR_PLATFORM,
        SCHWAB_PLATFORM,
        LONGBRIDGE_PLATFORM,
    }
)
GOVERNED_SOURCE_ROOTS = (
    Path(__file__).resolve().parents[1] / "src" / "us_equity_strategies" / "strategies",
    Path(__file__).resolve().parents[1] / "src" / "us_equity_strategies" / "entrypoints",
)


def _iter_governed_repo_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "external"} for part in path.parts):
            continue
        yield path


def _find_legacy_profile_key_offenders(root: Path, *, allowed_paths: frozenset[str]) -> dict[str, tuple[str, ...]]:
    offenders: dict[str, tuple[str, ...]] = {}
    for path in _iter_governed_repo_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = tuple(key for key in LEGACY_PROFILE_KEYS if key in text)
        if hits and rel not in allowed_paths:
            offenders[rel] = hits
    return offenders

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

    def test_base_runtime_adapter_specs_cover_all_strategy_profiles(self) -> None:
        self.assertEqual(
            frozenset(BASE_RUNTIME_ADAPTERS),
            frozenset(get_strategy_definitions()),
        )

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

    def test_dynamic_mega_leveraged_pullback_default_config_has_single_source(self) -> None:
        self.assertEqual(
            STRATEGY_DEFAULT_CONFIG[DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE],
            DYNAMIC_MEGA_LEVERAGED_PULLBACK_DEFAULT_CONFIG,
        )
        self.assertEqual(
            dynamic_mega_leveraged_pullback_manifest.default_config,
            DYNAMIC_MEGA_LEVERAGED_PULLBACK_DEFAULT_CONFIG,
        )

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

    def test_live_profiles_do_not_keep_unplanned_legacy_aliases(self) -> None:
        for profile in LIVE_PROFILE_LEGACY_ALIASES:
            with self.subTest(profile=profile):
                self.assertEqual(STRATEGY_CATALOG.metadata[profile].aliases, ())
        for profile, aliases in LIVE_PROFILE_TRANSITION_ALIASES.items():
            with self.subTest(profile=profile):
                self.assertEqual(STRATEGY_CATALOG.metadata[profile].aliases, aliases)

    def test_live_us_equity_profiles_now_cover_the_full_three_platform_matrix(self) -> None:
        for profile in LIVE_US_EQUITY_FULL_MATRIX_PROFILES:
            definition = STRATEGY_CATALOG.definitions[profile]
            with self.subTest(profile=profile):
                self.assertEqual(definition.supported_platforms, FULL_MATRIX_PLATFORMS)
                for platform_id in FULL_MATRIX_PLATFORMS:
                    adapter = get_platform_runtime_adapter(profile, platform_id=platform_id)
                    self.assertLessEqual(definition.required_inputs, adapter.available_inputs)

    def test_legacy_profile_keys_only_exist_in_explicit_rejection_tests(self) -> None:
        offenders = _find_legacy_profile_key_offenders(
            GOVERNED_REPO_ROOT,
            allowed_paths=LEGACY_PROFILE_KEY_ALLOWED_PATHS,
        )
        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()
