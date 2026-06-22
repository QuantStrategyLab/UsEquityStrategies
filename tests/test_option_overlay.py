from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quant_platform_kit.strategy_contracts import StrategyContext
from us_equity_strategies.option_overlay import (
    OPTION_OVERLAY_CONFIG_KEYS,
    OPTION_OVERLAY_DEFAULT_CONFIGS,
    OPTION_OVERLAY_RECIPE_DETAILS,
    OPTION_OVERLAY_RESEARCH_CANDIDATES,
    build_option_overlay_diagnostics,
    option_overlay_default_config,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class OptionOverlayArchitectureTest(unittest.TestCase):
    def test_option_overlay_config_keys_stay_out_of_income_layer_namespace(self) -> None:
        for key in OPTION_OVERLAY_CONFIG_KEYS:
            self.assertFalse(key.startswith("income_layer_"))

    def test_option_overlay_recipes_are_research_candidates_until_promoted(self) -> None:
        self.assertEqual(set(OPTION_OVERLAY_RESEARCH_CANDIDATES), set(OPTION_OVERLAY_RECIPE_DETAILS))
        for candidate in OPTION_OVERLAY_RESEARCH_CANDIDATES.values():
            self.assertEqual(candidate["status"], "research")
            self.assertIs(candidate["promotion_evidence"], False)

    def test_default_option_overlay_configs_are_enabled_but_scoped(self) -> None:
        self.assertEqual(
            set(OPTION_OVERLAY_DEFAULT_CONFIGS),
            {
                "global_etf_rotation",
                "tqqq_growth_income",
                "soxl_soxx_trend_income",
                "russell_top50_leader_rotation",
            },
        )
        tqqq = option_overlay_default_config("tqqq_growth_income")
        self.assertIs(tqqq["option_overlay_enabled"], True)
        self.assertIs(tqqq["option_growth_overlay_enabled"], True)
        self.assertEqual(tqqq["option_growth_overlay_recipe"], "tqqq_leaps_growth_v1")

        cloned = option_overlay_default_config("tqqq_growth_income")
        cloned["option_growth_overlay_nav_budget_ratio"] = 0.01
        self.assertEqual(
            option_overlay_default_config("tqqq_growth_income")["option_growth_overlay_nav_budget_ratio"],
            0.03,
        )
        self.assertEqual(option_overlay_default_config("nasdaq_sp500_smart_dca"), {})

    def test_option_overlay_module_has_no_income_layer_dependency(self) -> None:
        source = (REPO_ROOT / "src/us_equity_strategies/option_overlay.py").read_text(encoding="utf-8")
        self.assertNotIn("income_layer", source)

    def test_entrypoint_common_reexports_option_overlay_contract_by_import(self) -> None:
        source = (REPO_ROOT / "src/us_equity_strategies/entrypoints/_common.py").read_text(encoding="utf-8")
        self.assertIn("from us_equity_strategies.option_overlay import", source)
        self.assertIn("OPTION_OVERLAY_CONFIG_KEYS", source)
        self.assertIn("build_option_overlay_diagnostics", source)

    def test_master_option_overlay_switch_disables_all_option_intents(self) -> None:
        snapshot = SimpleNamespace(total_equity=2_000_000.0, positions=(), metadata={})

        diagnostics = build_option_overlay_diagnostics(
            {
                "option_overlay_enabled": False,
                "option_growth_overlay_enabled": True,
                "option_growth_overlay_recipe": "spy_leaps_growth_v1",
                "option_growth_overlay_start_usd": 250000.0,
            },
            StrategyContext(
                as_of="2026-04-06",
                portfolio=snapshot,
                market_data={
                    "option_chains": {
                        "SPY": {
                            "contracts": (
                                {
                                    "right": "C",
                                    "expiration": "2028-01-21",
                                    "strike": 620.0,
                                    "delta": 0.76,
                                    "bid": 148.0,
                                    "ask": 152.0,
                                },
                            ),
                        },
                    },
                },
            ),
        )

        self.assertIs(diagnostics["option_overlay_enabled"], False)
        self.assertIs(diagnostics["option_growth_overlay_enabled"], True)
        self.assertIs(diagnostics["option_growth_overlay_active"], False)
        self.assertEqual(diagnostics["option_growth_overlay_skip_reason"], "option_overlay_disabled")
        self.assertNotIn("option_order_intents", diagnostics)

    def test_research_recipe_blocks_live_intents_even_when_default_enabled(self) -> None:
        snapshot = SimpleNamespace(total_equity=2_000_000.0, positions=(), metadata={})

        diagnostics = build_option_overlay_diagnostics(
            option_overlay_default_config("global_etf_rotation"),
            StrategyContext(
                as_of="2026-04-06",
                portfolio=snapshot,
                market_data={
                    "underlier_indicators": {
                        "SPY": {
                            "sma200_pass": True,
                            "momentum_63d": 0.04,
                        },
                    },
                    "option_chains": {
                        "SPY": {
                            "contracts": (
                                {
                                    "right": "C",
                                    "expiration": "2028-01-21",
                                    "strike": 620.0,
                                    "delta": 0.76,
                                    "bid": 148.0,
                                    "ask": 152.0,
                                },
                            ),
                        },
                    },
                },
            ),
            base_diagnostics={"regime": "risk_on"},
        )

        self.assertIs(diagnostics["option_overlay_enabled"], True)
        self.assertIs(diagnostics["option_growth_overlay_enabled"], True)
        self.assertIs(diagnostics["option_growth_overlay_live_allowed"], False)
        self.assertEqual(diagnostics["option_growth_overlay_promotion_status"], "research")
        self.assertIs(diagnostics["option_growth_overlay_active"], False)
        self.assertEqual(diagnostics["option_growth_overlay_skip_reason"], "research_only_recipe")
        self.assertNotIn("option_order_intents", diagnostics)

    def test_index_leaps_growth_recipe_respects_risk_off_regime_gate(self) -> None:
        snapshot = SimpleNamespace(total_equity=2_000_000.0, positions=(), metadata={})

        with patch.dict(
            OPTION_OVERLAY_RESEARCH_CANDIDATES,
            {"spy_leaps_growth_v1": {"status": "live", "promotion_evidence": True, "reason": "test"}},
        ):
            diagnostics = build_option_overlay_diagnostics(
                {
                    "option_growth_overlay_enabled": True,
                    "option_growth_overlay_recipe": "spy_leaps_growth_v1",
                    "option_growth_overlay_start_usd": 250000.0,
                },
                StrategyContext(
                    as_of="2026-04-06",
                    portfolio=snapshot,
                    market_data={
                        "option_chains": {
                            "SPY": {
                                "contracts": (
                                    {
                                        "right": "C",
                                        "expiration": "2028-01-21",
                                        "strike": 620.0,
                                        "delta": 0.76,
                                        "bid": 148.0,
                                        "ask": 152.0,
                                    },
                                ),
                            },
                        },
                    },
                ),
                base_diagnostics={"regime": "risk_off"},
            )

        self.assertIs(diagnostics["option_growth_overlay_active"], True)
        self.assertEqual(diagnostics["option_order_intent_count"], 0)
        self.assertEqual(
            diagnostics["option_order_intents"]["skipped"],
            ({"recipe": "spy_leaps_growth_v1", "underlier": "SPY", "reason": "entry_gate_not_met"},),
        )

    def test_index_leaps_growth_recipe_requires_positive_underlier_trend_when_indicators_exist(self) -> None:
        snapshot = SimpleNamespace(total_equity=2_000_000.0, positions=(), metadata={})

        with patch.dict(
            OPTION_OVERLAY_RESEARCH_CANDIDATES,
            {"qqq_leaps_growth_v1": {"status": "live", "promotion_evidence": True, "reason": "test"}},
        ):
            diagnostics = build_option_overlay_diagnostics(
                {
                    "option_overlay_enabled": True,
                    "option_growth_overlay_enabled": True,
                    "option_growth_overlay_recipe": "qqq_leaps_growth_v1",
                    "option_growth_overlay_start_usd": 250000.0,
                },
                StrategyContext(
                    as_of="2026-04-06",
                    portfolio=snapshot,
                    market_data={
                        "underlier_indicators": {
                            "QQQ": {
                                "above_200dma": True,
                                "momentum_63d": -0.02,
                            },
                        },
                        "option_chains": {
                            "QQQ": {
                                "contracts": (
                                    {
                                        "right": "C",
                                        "expiration": "2028-01-21",
                                        "strike": 500.0,
                                        "delta": 0.75,
                                        "bid": 120.0,
                                        "ask": 124.0,
                                    },
                                ),
                            },
                        },
                    },
                ),
                base_diagnostics={"regime": "risk_on"},
            )

        self.assertIs(diagnostics["option_growth_overlay_active"], True)
        self.assertEqual(diagnostics["option_order_intent_count"], 0)
        self.assertEqual(
            diagnostics["option_order_intents"]["skipped"],
            ({"recipe": "qqq_leaps_growth_v1", "underlier": "QQQ", "reason": "entry_gate_not_met"},),
        )

    def test_index_leaps_growth_recipe_allows_entry_when_regime_and_indicators_pass(self) -> None:
        snapshot = SimpleNamespace(total_equity=2_000_000.0, positions=(), metadata={})

        with patch.dict(
            OPTION_OVERLAY_RESEARCH_CANDIDATES,
            {"spy_leaps_growth_v1": {"status": "live", "promotion_evidence": True, "reason": "test"}},
        ):
            diagnostics = build_option_overlay_diagnostics(
                {
                    "option_overlay_enabled": True,
                    "option_growth_overlay_enabled": True,
                    "option_growth_overlay_recipe": "spy_leaps_growth_v1",
                    "option_growth_overlay_start_usd": 250000.0,
                    "option_growth_overlay_nav_budget_ratio": 0.015,
                },
                StrategyContext(
                    as_of="2026-04-06",
                    portfolio=snapshot,
                    market_data={
                        "underlier_indicators": {
                            "SPY": {
                                "sma200_pass": True,
                                "momentum_63d": 0.04,
                            },
                        },
                        "option_chains": {
                            "SPY": {
                                "contracts": (
                                    {
                                        "right": "C",
                                        "expiration": "2028-01-21",
                                        "strike": 620.0,
                                        "delta": 0.76,
                                        "bid": 148.0,
                                        "ask": 152.0,
                                    },
                                ),
                            },
                        },
                    },
                ),
                base_diagnostics={"regime": "risk_on"},
            )

        self.assertEqual(diagnostics["option_order_intent_count"], 1)
        intent = diagnostics["option_order_intents"]["intents"][0]
        self.assertEqual(intent["underlier"], "SPY")
        self.assertEqual(intent["action"], "buy_to_open")


if __name__ == "__main__":
    unittest.main()
