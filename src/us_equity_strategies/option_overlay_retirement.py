"""Reject retired production option controls without altering research replay.

The caller must pass the original, unfiltered runtime configuration and any
known option intents and positions. Missing account evidence is not a proof of
an empty position set; validating that evidence belongs to the caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OPTION_OVERLAY_CONFIG_KEYS = frozenset({
    "option_overlay_enabled", "option_growth_overlay_enabled", "option_growth_overlay_recipe",
    "option_growth_overlay_start_usd", "option_growth_overlay_nav_budget_ratio",
    "option_income_overlay_enabled", "option_income_overlay_recipe",
    "option_income_overlay_start_usd", "option_income_overlay_nav_risk_ratio",
})


class OptionOverlayRetiredError(ValueError):
    """An option control or obligation requires explicit manual migration."""


_OPTION_SWITCHES = frozenset({
    "option_overlay_enabled",
    "option_growth_overlay_enabled",
    "option_income_overlay_enabled",
})


def validate_retired_option_inputs(
    config: Mapping[str, Any],
    *,
    option_order_intents: object = None,
    option_positions: object = None,
) -> None:
    """Reject explicit retired settings and known option obligations.

    This check does not inspect broker accounts, close positions, grant order
    authority, or validate research-only option replay contracts.
    """
    if not isinstance(config, Mapping):
        raise OptionOverlayRetiredError("option overlay retired; configuration requires manual migration")
    for key, value in config.items():
        if not isinstance(key, str) or not key.startswith("option_"):
            continue
        if key in _OPTION_SWITCHES and value is False:
            continue
        if key.endswith("_recipe") and (value is None or (isinstance(value, str) and value == "")):
            continue
        raise OptionOverlayRetiredError(f"option overlay retired; migrate configuration field {key}")
    for label, value in (("intents", option_order_intents), ("positions", option_positions)):
        if value is None:
            continue
        if isinstance(value, (list, tuple, dict)) and not value:
            continue
        raise OptionOverlayRetiredError(f"option overlay retired; reconcile option {label} before migration")
