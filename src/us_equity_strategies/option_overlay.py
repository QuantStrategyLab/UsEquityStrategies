"""Retired production defaults with lazy compatibility for historical research.

The selector is loaded only when a research caller explicitly requests the
legacy research API; normal manifests import only the empty-default function.
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .option_overlay_retirement import OPTION_OVERLAY_CONFIG_KEYS

if TYPE_CHECKING:
    from .research.option_overlay_replay import (
        OPTION_OVERLAY_RECIPE_DETAILS,
        OPTION_OVERLAY_RESEARCH_CANDIDATES,
        build_option_overlay_diagnostics,
    )

OPTION_OVERLAY_DEFAULT_CONFIGS: dict[str, dict[str, object]] = {}


def option_overlay_default_config(profile: str) -> dict[str, object]:
    return {}


def __getattr__(name: str):
    if name in {"OPTION_OVERLAY_RESEARCH_CANDIDATES", "OPTION_OVERLAY_RECIPE_DETAILS",
                "build_option_overlay_diagnostics"}:
        module = import_module(".research.option_overlay_replay", __package__)
        return getattr(module, name)
    raise AttributeError(name)


__all__ = [
    "OPTION_OVERLAY_CONFIG_KEYS",
    "OPTION_OVERLAY_DEFAULT_CONFIGS",
    "OPTION_OVERLAY_RECIPE_DETAILS",
    "OPTION_OVERLAY_RESEARCH_CANDIDATES",
    "build_option_overlay_diagnostics",
    "option_overlay_default_config",
]
