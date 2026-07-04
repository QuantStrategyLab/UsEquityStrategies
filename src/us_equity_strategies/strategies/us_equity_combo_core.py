"""US core combo shadow candidate — Russell leaders + Nasdaq/S&P sleeve + cash defense."""

from __future__ import annotations

import logging
from typing import Iterable, Mapping

import pandas as pd

from quant_platform_kit.common.strategies import compute_portfolio_drift

from us_equity_strategies.strategies import mega_cap_leader_rotation

PROFILE_NAME: str = "us_equity_combo_core"
SIGNAL_SOURCE: str = "combo_core_shadow"
STATUS_ICON: str = "\U0001f1fa\U0001f1f8"

logger = logging.getLogger(__name__)

DEFAULT_RUSSELL_WEIGHT: float = 0.40
DEFAULT_DCA_WEIGHT: float = 0.40
DEFAULT_SAFE_WEIGHT: float = 0.20
DEFAULT_SOFT_RUSSELL_WEIGHT: float = 0.35
DEFAULT_SOFT_DCA_WEIGHT: float = 0.35
DEFAULT_SOFT_SAFE_WEIGHT: float = 0.30
DEFAULT_HARD_RUSSELL_WEIGHT: float = 0.20
DEFAULT_HARD_DCA_WEIGHT: float = 0.05
DEFAULT_HARD_SAFE_WEIGHT: float = 0.75
DEFAULT_REBALANCE_THRESHOLD: float = 0.05
DEFAULT_DCA_ALLOCATIONS: Mapping[str, float] = {
    "QQQM": 0.50,
    "SPLG": 0.50,
}

_COMBO_ONLY_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "dca_allocations",
        "dca_weight",
        "dynamic",
        "execution_cash_reserve_ratio",
        "hard_dca_weight",
        "hard_russell_weight",
        "hard_safe_weight",
        "income_layer_allocations",
        "income_layer_enabled",
        "income_layer_max_ratio",
        "income_layer_start_usd",
        "option_growth_overlay_enabled",
        "option_growth_overlay_nav_budget_ratio",
        "option_growth_overlay_recipe",
        "option_growth_overlay_start_usd",
        "option_overlay_enabled",
        "rebalance_frequency",
        "russell_weight",
        "safe_weight",
        "shadow_candidate",
        "soft_dca_weight",
        "soft_russell_weight",
        "soft_safe_weight",
    }
)


def _normalize_weights(raw: Mapping[str, float]) -> dict[str, float]:
    cleaned = {
        str(symbol).strip().upper(): max(0.0, float(weight))
        for symbol, weight in raw.items()
        if str(symbol).strip()
    }
    total = sum(cleaned.values())
    if total <= 1e-12:
        return {}
    return {symbol: weight / total for symbol, weight in cleaned.items()}


def _normalize_ratios(raw: Mapping[str, float]) -> dict[str, float]:
    cleaned = {
        str(name).strip(): max(0.0, float(weight))
        for name, weight in raw.items()
        if str(name).strip()
    }
    total = sum(cleaned.values())
    if total <= 1e-12:
        return {}
    return {name: weight / total for name, weight in cleaned.items()}


def _pop_sleeve_weight_sets(cfg: dict[str, object]) -> dict[str, dict[str, float]]:
    risk_on = {
        "russell": float(cfg.pop("russell_weight", DEFAULT_RUSSELL_WEIGHT)),
        "dca": float(cfg.pop("dca_weight", DEFAULT_DCA_WEIGHT)),
        "safe": float(cfg.pop("safe_weight", DEFAULT_SAFE_WEIGHT)),
    }
    soft = {
        "russell": float(cfg.pop("soft_russell_weight", DEFAULT_SOFT_RUSSELL_WEIGHT)),
        "dca": float(cfg.pop("soft_dca_weight", DEFAULT_SOFT_DCA_WEIGHT)),
        "safe": float(cfg.pop("soft_safe_weight", DEFAULT_SOFT_SAFE_WEIGHT)),
    }
    hard = {
        "russell": float(cfg.pop("hard_russell_weight", DEFAULT_HARD_RUSSELL_WEIGHT)),
        "dca": float(cfg.pop("hard_dca_weight", DEFAULT_HARD_DCA_WEIGHT)),
        "safe": float(cfg.pop("hard_safe_weight", DEFAULT_HARD_SAFE_WEIGHT)),
    }
    return {
        "risk_on": _normalize_ratios(risk_on),
        "soft_defense": _normalize_ratios(soft),
        "hard_defense": _normalize_ratios(hard),
    }


def _resolve_sleeve_weights(
    *,
    sleeve_weight_sets: Mapping[str, Mapping[str, float]],
    regime: str,
    benchmark_trend_positive: bool,
    dynamic_enabled: bool,
) -> tuple[str, dict[str, float]]:
    if not dynamic_enabled:
        return "risk_on", dict(sleeve_weight_sets["risk_on"])
    if not benchmark_trend_positive or regime == "hard_defense":
        return "hard_defense", dict(sleeve_weight_sets["hard_defense"])
    if regime == "soft_defense":
        return "soft_defense", dict(sleeve_weight_sets["soft_defense"])
    return "risk_on", dict(sleeve_weight_sets["risk_on"])


def build_target_weights(
    russell_snapshot,
    current_holdings: Iterable[str] | None = None,
    config: dict | None = None,
) -> tuple[dict[str, float], pd.DataFrame, dict[str, object]]:
    """Build target weights for the US core combo shadow candidate."""

    cfg = {} if config is None else dict(config)
    dynamic_enabled = bool(cfg.get("dynamic", True))
    sleeve_weight_sets = _pop_sleeve_weight_sets(cfg)
    dca_allocations = _normalize_weights(
        cfg.get("dca_allocations", DEFAULT_DCA_ALLOCATIONS)  # type: ignore[arg-type]
    )
    safe_haven = str(cfg.get("safe_haven", mega_cap_leader_rotation.SAFE_HAVEN)).strip().upper()
    for key in _COMBO_ONLY_CONFIG_KEYS:
        if key not in {
            "dca_weight",
            "hard_dca_weight",
            "hard_russell_weight",
            "hard_safe_weight",
            "russell_weight",
            "safe_weight",
            "soft_dca_weight",
            "soft_russell_weight",
            "soft_safe_weight",
        }:
            cfg.pop(key, None)

    cfg.setdefault("risk_on_exposure", 1.0)
    cfg.setdefault("soft_defense_exposure", 1.0)
    cfg.setdefault("hard_defense_exposure", 1.0)

    russell_weights, ranked, russell_metadata = mega_cap_leader_rotation.build_target_weights(
        russell_snapshot,
        current_holdings,
        **cfg,
    )
    regime = str(russell_metadata.get("regime", "risk_on"))
    benchmark_trend_positive = bool(russell_metadata.get("benchmark_trend_positive", True))
    regime_state, sleeve_weights = _resolve_sleeve_weights(
        sleeve_weight_sets=sleeve_weight_sets,
        regime=regime,
        benchmark_trend_positive=benchmark_trend_positive,
        dynamic_enabled=dynamic_enabled,
    )

    combined: dict[str, float] = {}
    for symbol, weight in russell_weights.items():
        combined[str(symbol)] = combined.get(str(symbol), 0.0) + float(weight) * sleeve_weights["russell"]
    for symbol, weight in dca_allocations.items():
        combined[symbol] = combined.get(symbol, 0.0) + float(weight) * sleeve_weights["dca"]
    if sleeve_weights["safe"] > 1e-12:
        combined[safe_haven] = combined.get(safe_haven, 0.0) + sleeve_weights["safe"]

    total = sum(combined.values())
    if total > 1e-12 and abs(total - 1.0) > 1e-12:
        combined = {symbol: weight / total for symbol, weight in combined.items()}

    metadata: dict[str, object] = {
        "profile_name": PROFILE_NAME,
        "signal_source": SIGNAL_SOURCE,
        "status_icon": STATUS_ICON,
        "regime": regime,
        "regime_state": regime_state,
        "dynamic_enabled": dynamic_enabled,
        "effective_russell_weight": sleeve_weights["russell"],
        "effective_dca_weight": sleeve_weights["dca"],
        "effective_safe_weight": sleeve_weights["safe"],
        "dca_allocations": dca_allocations,
        "dca_managed_symbols": tuple(dca_allocations),
        "safe_haven": safe_haven,
        "russell_regime": regime,
        "benchmark_trend_positive": benchmark_trend_positive,
        "russell_selected_count": russell_metadata.get("selected_count", 0),
        "russell_metadata": russell_metadata,
        "rebalance": compute_portfolio_drift(
            combined,
            holdings=cfg.get("current_holdings_quantities", {}),
            prices=cfg.get("current_prices", {}),
            threshold=float(cfg.get("rebalance_threshold", DEFAULT_REBALANCE_THRESHOLD)),
        ),
    }
    return combined, ranked, metadata


def compute_signals(
    russell_snapshot,
    current_holdings,
    *,
    run_as_of=None,
    config: dict | None = None,
    **kwargs,
) -> tuple[dict[str, float] | None, str, bool, str, dict[str, object]]:
    logger.debug("run_as_of=%s ignored (strategy does not time-travel)", run_as_of)
    cfg = {} if config is None else dict(config)
    cfg.update(kwargs)
    weights, ranked, metadata = build_target_weights(
        russell_snapshot,
        current_holdings,
        config=cfg,
    )

    regime_state = str(metadata.get("regime_state", "risk_on"))
    trend_tag = "MA200_up" if bool(metadata.get("benchmark_trend_positive", True)) else "MA200_down"
    top_preview = ""
    if ranked is not None and not ranked.empty:
        top_preview = ", ".join(
            f"{row.symbol}({row.score:.2f})"
            for row in ranked.head(5).itertuples(index=False)
        )
    signal_desc = (
        f"regime={regime_state} "
        f"russell={float(metadata['effective_russell_weight']):.0%} "
        f"dca={float(metadata['effective_dca_weight']):.0%} "
        f"safe={float(metadata['effective_safe_weight']):.0%} "
        f"{trend_tag} top={top_preview}"
    )
    status_desc = (
        f"regime={regime_state} | "
        f"russell={float(metadata['effective_russell_weight']):.0%} "
        f"dca={float(metadata['effective_dca_weight']):.0%} "
        f"safe={float(metadata['effective_safe_weight']):.0%} | "
        f"{trend_tag}"
    )
    diagnostics: dict[str, object] = {
        **metadata,
        "managed_symbols": extract_managed_symbols(russell_snapshot, config=cfg),
        "status_icon": STATUS_ICON,
    }
    return weights, signal_desc, regime_state == "hard_defense", status_desc, diagnostics


def extract_managed_symbols(
    russell_snapshot,
    *,
    config: Mapping[str, object] | None = None,
    benchmark_symbol: str = mega_cap_leader_rotation.BENCHMARK_SYMBOL,
    broad_benchmark_symbol: str = mega_cap_leader_rotation.BROAD_BENCHMARK_SYMBOL,
    safe_haven: str = mega_cap_leader_rotation.SAFE_HAVEN,
) -> tuple[str, ...]:
    cfg = {} if config is None else dict(config)
    normalized_safe = str(cfg.get("safe_haven", safe_haven)).strip().upper()
    dca_symbols = tuple(_normalize_weights(cfg.get("dca_allocations", DEFAULT_DCA_ALLOCATIONS)).keys())  # type: ignore[arg-type]
    russell_symbols = mega_cap_leader_rotation.extract_managed_symbols(
        russell_snapshot,
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=normalized_safe,
    )
    seen: set[str] = set()
    merged: list[str] = []
    for symbol in (*russell_symbols, *dca_symbols, normalized_safe):
        if symbol and symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
    return tuple(merged)
