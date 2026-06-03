from __future__ import annotations

import unittest

from us_equity_strategies.ai_extensions import (
    CRISIS_REGIME_GUARD_MODULE,
    TACO_PANIC_REBOUND_MODULE,
    build_ai_extension_diagnostics,
    build_default_ai_extension_config,
    get_enabled_ai_extension_modules,
    normalize_ai_extension_config,
)


class AIExtensionConfigTests(unittest.TestCase):
    def test_default_ai_extensions_are_disabled(self) -> None:
        config = build_default_ai_extension_config()

        self.assertFalse(config["enabled"])
        self.assertEqual(config["mode"], "off")
        self.assertFalse(config["modules"][TACO_PANIC_REBOUND_MODULE]["enabled"])
        self.assertFalse(config["modules"][CRISIS_REGIME_GUARD_MODULE]["enabled"])

    def test_enabled_modules_follow_guard_then_opportunity_order(self) -> None:
        config = normalize_ai_extension_config(
            {
                "enabled": True,
                "mode": "paper",
                "modules": {
                    TACO_PANIC_REBOUND_MODULE: {"enabled": True},
                    CRISIS_REGIME_GUARD_MODULE: {"enabled": True},
                },
            }
        )

        self.assertEqual(
            get_enabled_ai_extension_modules(config),
            (CRISIS_REGIME_GUARD_MODULE, TACO_PANIC_REBOUND_MODULE),
        )

    def test_ai_extension_diagnostics_reports_signal_modules_without_live_effects(self) -> None:
        diagnostics = build_ai_extension_diagnostics(
            {
                "enabled": True,
                "mode": "paper",
                "modules": {TACO_PANIC_REBOUND_MODULE: {"enabled": True}},
            },
            signals={TACO_PANIC_REBOUND_MODULE: {"confidence": 0.9}},
        )

        self.assertTrue(diagnostics["enabled"])
        self.assertEqual(diagnostics["mode"], "paper")
        self.assertEqual(diagnostics["enabled_modules"], (TACO_PANIC_REBOUND_MODULE,))
        self.assertEqual(diagnostics["signal_modules"], (TACO_PANIC_REBOUND_MODULE,))
        self.assertEqual(diagnostics["decision_effect"], "no_op_until_extension_engine_is_enabled")


if __name__ == "__main__":
    unittest.main()
