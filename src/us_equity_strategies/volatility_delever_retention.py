"""Deterministic retention policy helpers for volatility deleveraging."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

RETENTION_MODE_NONE = "none"
RETENTION_MODE_FIXED = "fixed"
RETENTION_MODE_ENVIRONMENT = "environment"
RETENTION_MODES = frozenset({RETENTION_MODE_NONE, RETENTION_MODE_FIXED, RETENTION_MODE_ENVIRONMENT})

POLICY_TQQQ_STEP_SOFTZERO_025_050 = "tqqq_step_softzero_0.25_0.50"
POLICY_TQQQ_STEP_SOFTZERO_035_050 = "tqqq_step_softzero_0.35_0.50"
POLICY_SOXL_STEP_REBOUND_025_050 = "soxl_step_rebound_0.25_0.50"
POLICY_SOXL_STEP_SOFTZERO_REBOUND_025_050 = "soxl_step_softzero_rebound_0.25_0.50"
POLICY_TECL_STEP_REBOUND_025_050 = "tecl_step_rebound_0.25_0.50"
POLICY_TECL_STEP_SOFTZERO_REBOUND_025_050 = "tecl_step_softzero_rebound_0.25_0.50"


def _as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(default)


def _as_float(value: object, *, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_ratio(value: object, *, default: float = 0.0, upper: float = 1.0) -> float:
    result = _as_float(value, default=default)
    if result is None:
        result = float(default)
    return max(0.0, min(float(upper), float(result)))


def _normalized_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _context_from_market_regime(market_regime_context: Mapping[str, object] | None) -> Mapping[str, object]:
    if not isinstance(market_regime_context, Mapping):
        return {}
    context = market_regime_context.get("volatility_delever_context")
    return context if isinstance(context, Mapping) else {}


def _profile_ratio(context: Mapping[str, object], policy: str) -> tuple[float | None, tuple[str, ...]]:
    profiles = context.get("retention_profiles")
    if not isinstance(profiles, Mapping):
        return None, ()
    profile = profiles.get(policy)
    if not isinstance(profile, Mapping):
        return None, ()
    ratio = _as_float(profile.get("retention_ratio"), default=None)
    if ratio is None:
        return None, ()
    return float(ratio), _normalized_text_tuple(profile.get("reason_codes"))


def _levered_price_rebound_candidate(context: Mapping[str, object]) -> bool | None:
    price_context = context.get("price_rebound_context")
    if not isinstance(price_context, Mapping):
        return None
    return bool(
        _as_bool(price_context.get("volatility_triggered"), default=False)
        and _as_bool(price_context.get("trend_ok"), default=False)
        and (
            _as_bool(price_context.get("rebound_1d"), default=False)
            or _as_bool(price_context.get("rebound_nd"), default=False)
            or _as_bool(price_context.get("confirmed"), default=False)
        )
        and not _as_bool(price_context.get("hard_filter"), default=False)
    )


def _levered_rebound_available(context: Mapping[str, object], rebound: bool) -> bool:
    price_candidate = _levered_price_rebound_candidate(context)
    if price_candidate is not None:
        return price_candidate
    rebound_sources = _normalized_text_tuple(context.get("rebound_sources"))
    if rebound_sources:
        return bool(rebound and "price_rebound" in rebound_sources)
    return rebound


def _policy_ratio(context: Mapping[str, object], policy: str) -> tuple[float, tuple[str, ...]]:
    hard = _as_bool(context.get("hard_risk"), default=False)
    soft = _as_bool(context.get("soft_risk"), default=False)
    constructive = _as_bool(context.get("constructive"), default=False)
    rebound = _as_bool(context.get("rebound_confirm"), default=False)
    if hard:
        return 0.0, ("hard_risk",)
    if policy == POLICY_TQQQ_STEP_SOFTZERO_025_050:
        if soft:
            return 0.0, ("soft_risk",)
        if constructive and rebound:
            return 0.50, ("constructive", "rebound_confirm")
        return 0.25, ("non_soft_risk",)
    if policy == POLICY_TQQQ_STEP_SOFTZERO_035_050:
        if soft:
            return 0.0, ("soft_risk",)
        if constructive and rebound:
            return 0.50, ("constructive", "rebound_confirm")
        return 0.35, ("non_soft_risk",)
    if policy in {POLICY_SOXL_STEP_REBOUND_025_050, POLICY_TECL_STEP_REBOUND_025_050}:
        if not _levered_rebound_available(context, rebound):
            return 0.0, ("rebound_not_confirmed",)
        if constructive:
            return 0.50, ("constructive", "rebound_confirm")
        return 0.25, ("rebound_confirm",)
    if policy in {POLICY_SOXL_STEP_SOFTZERO_REBOUND_025_050, POLICY_TECL_STEP_SOFTZERO_REBOUND_025_050}:
        if soft:
            return 0.0, ("soft_risk",)
        if not _levered_rebound_available(context, rebound):
            return 0.0, ("rebound_not_confirmed",)
        if constructive:
            return 0.50, ("constructive", "rebound_confirm")
        return 0.25, ("rebound_confirm",)
    return 0.0, ("unknown_policy",)


def resolve_volatility_delever_retention(
    *,
    mode: object,
    fixed_ratio: object,
    policy: object,
    max_ratio: object,
    context_required: object,
    market_regime_context: Mapping[str, object] | None,
) -> dict[str, object]:
    """Resolve the levered sleeve fraction to retain after local vol triggers.

    The helper is deliberately deterministic and only reads market-data context.
    AI, OSINT, and event narrative fields are ignored by construction.
    """

    normalized_mode = str(mode or RETENTION_MODE_NONE).strip().lower()
    if normalized_mode not in RETENTION_MODES:
        normalized_mode = RETENTION_MODE_NONE
    max_retention = _clamp_ratio(max_ratio, default=1.0, upper=1.0)
    if normalized_mode == RETENTION_MODE_NONE:
        return {
            "mode": normalized_mode,
            "policy": str(policy or "").strip(),
            "retention_ratio": 0.0,
            "source": "disabled",
            "context_found": False,
            "reason_codes": ("mode_none",),
        }
    if normalized_mode == RETENTION_MODE_FIXED:
        return {
            "mode": normalized_mode,
            "policy": str(policy or "").strip(),
            "retention_ratio": _clamp_ratio(fixed_ratio, default=0.0, upper=max_retention),
            "source": "fixed",
            "context_found": False,
            "reason_codes": ("fixed_ratio",),
        }

    context = _context_from_market_regime(market_regime_context)
    found = bool(context)
    if _as_bool(context_required, default=True) and not found:
        return {
            "mode": normalized_mode,
            "policy": str(policy or "").strip(),
            "retention_ratio": 0.0,
            "source": "missing_context",
            "context_found": False,
            "reason_codes": ("missing_context",),
        }
    if isinstance(market_regime_context, Mapping) and (
        str(market_regime_context.get("route") or "").strip().lower() == "risk_off"
        or _as_bool(market_regime_context.get("crisis_defense_required"), default=False)
    ):
        return {
            "mode": normalized_mode,
            "policy": str(policy or "").strip(),
            "retention_ratio": 0.0,
            "source": "market_regime_risk_off",
            "context_found": found,
            "reason_codes": ("market_regime_risk_off",),
        }
    if found and not _as_bool(context.get("actionable_for_position_control"), default=True):
        return {
            "mode": normalized_mode,
            "policy": str(policy or "").strip(),
            "retention_ratio": 0.0,
            "source": "not_actionable",
            "context_found": True,
            "reason_codes": ("not_actionable",),
        }

    normalized_policy = str(policy or "").strip()
    profile_ratio, profile_reasons = _profile_ratio(context, normalized_policy)
    if profile_ratio is not None:
        ratio = profile_ratio
        reasons = profile_reasons or ("profile_ratio",)
        source = "context_profile"
    else:
        ratio, reasons = _policy_ratio(context, normalized_policy)
        source = "local_policy"
    return {
        "mode": normalized_mode,
        "policy": normalized_policy,
        "retention_ratio": _clamp_ratio(ratio, default=0.0, upper=max_retention),
        "source": source,
        "context_found": found,
        "reason_codes": reasons,
    }


__all__ = [
    "POLICY_SOXL_STEP_REBOUND_025_050",
    "POLICY_SOXL_STEP_SOFTZERO_REBOUND_025_050",
    "POLICY_TECL_STEP_REBOUND_025_050",
    "POLICY_TECL_STEP_SOFTZERO_REBOUND_025_050",
    "POLICY_TQQQ_STEP_SOFTZERO_025_050",
    "POLICY_TQQQ_STEP_SOFTZERO_035_050",
    "RETENTION_MODE_ENVIRONMENT",
    "RETENTION_MODE_FIXED",
    "RETENTION_MODE_NONE",
    "RETENTION_MODES",
    "resolve_volatility_delever_retention",
]
