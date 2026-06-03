"""Optional AI extension configuration for strategy-level decision modifiers.

The helpers in this module are deliberately platform-neutral. They define how a
strategy can declare optional AI modules, but they do not call model APIs, read
secrets, fetch news, or place orders. Runtime repositories can feed structured
AI signals through ``StrategyContext.state`` / ``artifacts`` for diagnostics;
this module does not change portfolio targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

AI_EXTENSION_MODE_OFF = "off"
AI_EXTENSION_MODE_PAPER = "paper"
AI_EXTENSION_MODE_LIVE = "live"
AI_EXTENSION_MODES = frozenset({AI_EXTENSION_MODE_OFF, AI_EXTENSION_MODE_PAPER, AI_EXTENSION_MODE_LIVE})
AI_EXTENSION_SIGNAL_STATE_KEY = "ai_extension_signals"

TACO_PANIC_REBOUND_MODULE = "taco_panic_rebound"
CRISIS_REGIME_GUARD_MODULE = "crisis_regime_guard"

AI_EXTENSION_APPLY_ORDER = (
    CRISIS_REGIME_GUARD_MODULE,
    TACO_PANIC_REBOUND_MODULE,
)

AI_EXTENSION_DEFAULT_CONFIG: dict[str, object] = {
    "enabled": False,
    "mode": AI_EXTENSION_MODE_OFF,
    "apply_order": AI_EXTENSION_APPLY_ORDER,
    "fail_safe_action": "ignore_and_keep_base_plan",
    "modules": {
        TACO_PANIC_REBOUND_MODULE: {
            "enabled": False,
            "mode": AI_EXTENSION_MODE_PAPER,
            "module_type": "opportunity_overlay",
            "target_asset": "TQQQ",
            "safe_asset": "BOXX",
            "sleeve_ratio": 0.05,
            "funding_source": "safe_asset_and_cash_only",
            "trigger_mode": "price_stress_only",
            "ai_mode": "classify_only",
            "event_family": "tariff_trade_war",
            "min_confidence": 0.80,
            "use_vix_for_position": False,
            "use_macro_veto": False,
            "max_internal_exposure": 0.70,
            "runner_exposure": 0.35,
            "stop_loss_from_entry": -0.08,
            "max_hold_days": 63,
        },
        CRISIS_REGIME_GUARD_MODULE: {
            "enabled": False,
            "mode": AI_EXTENSION_MODE_PAPER,
            "module_type": "risk_guard",
            "risk_scope": "growth_layer",
            "max_risk_multiplier": 1.0,
            "min_risk_multiplier": 0.0,
            "ai_mode": "classify_only",
            "require_price_confirmation": True,
            "allow_taco_overlay_when_systemic": False,
        },
    },
}


def build_default_ai_extension_config() -> dict[str, object]:
    """Return a fresh default config with every AI extension disabled."""
    return deepcopy(AI_EXTENSION_DEFAULT_CONFIG)


def _as_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return default


def _normalize_mode(value: object, *, default: str = AI_EXTENSION_MODE_OFF) -> str:
    mode = str(value or default).strip().lower()
    return mode if mode in AI_EXTENSION_MODES else default


def normalize_ai_extension_config(raw_config: Mapping[str, object] | None) -> dict[str, object]:
    """Merge a partial runtime config over the disabled default AI extension config."""
    config = build_default_ai_extension_config()
    if not raw_config:
        return config

    config["enabled"] = _as_bool(raw_config.get("enabled"), default=bool(config["enabled"]))
    config["mode"] = _normalize_mode(raw_config.get("mode"), default=str(config["mode"]))
    if "apply_order" in raw_config:
        config["apply_order"] = tuple(
            str(item).strip()
            for item in raw_config.get("apply_order", ())
            if str(item).strip()
        )
    if "fail_safe_action" in raw_config:
        config["fail_safe_action"] = str(raw_config["fail_safe_action"])

    raw_modules = raw_config.get("modules", {})
    if isinstance(raw_modules, Mapping):
        modules = deepcopy(config["modules"])
        for module_name, raw_module_config in raw_modules.items():
            module_key = str(module_name).strip()
            if not module_key or not isinstance(raw_module_config, Mapping):
                continue
            current = dict(modules.get(module_key, {}))
            current.update(dict(raw_module_config))
            current["enabled"] = _as_bool(current.get("enabled"), default=False)
            current["mode"] = _normalize_mode(current.get("mode"), default=AI_EXTENSION_MODE_PAPER)
            modules[module_key] = current
        config["modules"] = modules
    return config


def get_enabled_ai_extension_modules(raw_config: Mapping[str, object] | None) -> tuple[str, ...]:
    """Return module ids that are globally enabled and individually enabled."""
    config = normalize_ai_extension_config(raw_config)
    if not _as_bool(config.get("enabled"), default=False):
        return ()
    modules = config.get("modules", {})
    if not isinstance(modules, Mapping):
        return ()
    apply_order = tuple(str(item) for item in config.get("apply_order", ()) if str(item))
    enabled = {
        str(module_name)
        for module_name, module_config in modules.items()
        if isinstance(module_config, Mapping) and _as_bool(module_config.get("enabled"), default=False)
    }
    ordered = tuple(module_name for module_name in apply_order if module_name in enabled)
    unordered = tuple(sorted(enabled - set(ordered)))
    return (*ordered, *unordered)


def build_ai_extension_diagnostics(
    raw_config: Mapping[str, object] | None,
    signals: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Summarize extension settings without changing the strategy decision."""
    config = normalize_ai_extension_config(raw_config)
    modules = config.get("modules", {}) if isinstance(config.get("modules", {}), Mapping) else {}
    module_modes = {
        str(module_name): str(module_config.get("mode", AI_EXTENSION_MODE_PAPER))
        for module_name, module_config in modules.items()
        if isinstance(module_config, Mapping)
    }
    signal_modules = tuple(sorted(str(key) for key in dict(signals or {}).keys()))
    return {
        "enabled": _as_bool(config.get("enabled"), default=False),
        "mode": _normalize_mode(config.get("mode"), default=AI_EXTENSION_MODE_OFF),
        "enabled_modules": get_enabled_ai_extension_modules(config),
        "apply_order": tuple(config.get("apply_order", ())),
        "module_modes": module_modes,
        "signal_modules": signal_modules,
        "decision_effect": "no_op_until_extension_engine_is_enabled",
    }


__all__ = [
    "AI_EXTENSION_APPLY_ORDER",
    "AI_EXTENSION_DEFAULT_CONFIG",
    "AI_EXTENSION_MODE_LIVE",
    "AI_EXTENSION_MODE_OFF",
    "AI_EXTENSION_MODE_PAPER",
    "AI_EXTENSION_MODES",
    "AI_EXTENSION_SIGNAL_STATE_KEY",
    "CRISIS_REGIME_GUARD_MODULE",
    "TACO_PANIC_REBOUND_MODULE",
    "build_ai_extension_diagnostics",
    "build_default_ai_extension_config",
    "get_enabled_ai_extension_modules",
    "normalize_ai_extension_config",
]
