"""US equity combo strategy — 50/50 blend of Russell Top50 leader rotation and IBIT Smart DCA."""

from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

from quant_platform_kit.common.strategies import compute_portfolio_drift

from us_equity_strategies.strategies import (
    ibit_smart_dca,
    mega_cap_leader_rotation,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILE_NAME: str = "us_equity_combo"
SIGNAL_SOURCE: str = "combo"
STATUS_ICON: str = "\U0001f1fa\U0001f1f8"  # US flag

logger = logging.getLogger(__name__)

DEFAULT_STOCK_WEIGHT: float = 0.50
DEFAULT_ETF_WEIGHT: float = 0.50
DEFAULT_REBALANCE_THRESHOLD: float = 0.05  # 5% drift triggers rebalance

_COMBO_ONLY_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "execution_cash_reserve_ratio",
        "rebalance_frequency",
        "income_layer_enabled",
        "income_layer_start_usd",
        "income_layer_max_ratio",
        "income_layer_allocations",
        "option_overlay_enabled",
        "option_growth_overlay_enabled",
        "option_growth_overlay_recipe",
        "option_growth_overlay_start_usd",
        "option_growth_overlay_nav_budget_ratio",
    }
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_target_weights(
    russell_snapshot,
    current_holdings: Iterable[str] | None = None,
    config: dict | None = None,
) -> tuple[dict[str, float], pd.DataFrame, dict[str, object]]:
    """Build target weights as a blend of Russell Top50 and IBIT DCA.

    Parameters
    ----------
    russell_snapshot :
        Feature snapshot for the Russell Top50 universe (forwarded to
        mega_cap_leader_rotation).
    current_holdings :
        Iterable of currently held symbols.
    config :
        Optional override dictionary.  Accepted keys include:

        * ``stock_weight`` / ``etf_weight`` — blend ratios.  Runtime
          manifests may also pass the equivalent
          ``russell_weight`` / ``dca_weight`` aliases.
          (defaults to ``DEFAULT_STOCK_WEIGHT`` /
          ``DEFAULT_ETF_WEIGHT``).
        * ``dynamic`` — if truthy, SPY MA200 regime at runtime
          dynamically scales the blend (overrides the static weights).
          When SPY is below its 200-day moving average, the TQQQ leg is
          reduced by 50%.
        * All keyword arguments accepted by
          ``mega_cap_leader_rotation.build_target_weights``
          and ``ibit_smart_dca.build_rebalance_plan``.

    Returns
    -------
    tuple[dict[str, float], pd.DataFrame, dict[str, object]]
        ``(combined_weights, ranked_frame, metadata)`` following the
        same convention as ``mega_cap_leader_rotation.build_target_weights``.
    """
    cfg = {} if config is None else dict(config)

    stock_weight = float(cfg.pop("stock_weight", cfg.pop("russell_weight", DEFAULT_STOCK_WEIGHT)))
    etf_weight = float(cfg.pop("etf_weight", cfg.pop("dca_weight", DEFAULT_ETF_WEIGHT)))
    dynamic_enabled = bool(cfg.pop("dynamic", None))

    # Extract IBIT dependencies from config (caller must supply these)
    market_history = cfg.pop("market_history", None)
    portfolio = cfg.pop("portfolio", None)
    for key in _COMBO_ONLY_CONFIG_KEYS:
        cfg.pop(key, None)

    # --- Russell leg ----------------------------------------------------------
    russell_weights, ranked, russell_metadata = mega_cap_leader_rotation.build_target_weights(
        russell_snapshot,
        current_holdings,
        **cfg,
    )

    regime: str = str(russell_metadata.get("regime", "risk_on"))
    benchmark_trend_positive: bool = bool(russell_metadata.get("benchmark_trend_positive", True))

    # --- IBIT DCA leg --------------------------------------------------------
    target_values: dict[str, float] = {}
    dca_managed: tuple[str, ...] = ()
    dca_result: dict[str, object] = {}
    if market_history is not None and portfolio is not None:
        try:
            dca_result = ibit_smart_dca.build_rebalance_plan(
                market_history,
                portfolio,
                **cfg,
            )
            target_values = dca_result.get("target_values", {}) if dca_result else {}
            dca_managed = tuple(dca_result.get("managed_symbols", ())) if dca_result else ()
        except (ValueError, TypeError) as exc:
            logger.warning("IBIT leg unavailable (non-critical): %s", exc)
            # Fall back: IBIT weight defaults to 0 (only Russell leg active)
    else:
        logger.debug("IBIT leg skipped: market_history or portfolio not provided")

    # --- Dynamic regime scaling ----------------------------------------------
    # Priority: SPY MA200 (crash protection) > breadth regime (fine-tuning)
    if dynamic_enabled:
        if not benchmark_trend_positive:
            # SPY below MA200 — hard risk-off, cut stock exposure
            effective_stock_weight = stock_weight * 0.5
            effective_etf_weight = 1.0 - effective_stock_weight
        elif regime == "risk_on":
            # Breadth positive + SPY above MA200 — mild boost
            effective_stock_weight = min(1.0, stock_weight * 1.1)
            effective_etf_weight = 1.0 - effective_stock_weight
        elif regime in ("soft_defense",):
            effective_stock_weight = stock_weight
            effective_etf_weight = etf_weight
        else:  # hard_defense
            effective_stock_weight = stock_weight * 0.5
            effective_etf_weight = 1.0 - effective_stock_weight
    else:
        effective_stock_weight = stock_weight
        effective_etf_weight = etf_weight

    # --- Combine weights -----------------------------------------------------
    combined: dict[str, float] = {}
    for symbol, w in russell_weights.items():
        combined[symbol] = combined.get(symbol, 0.0) + w * effective_stock_weight
    for symbol, w in target_values.items():
        combined[symbol] = combined.get(symbol, 0.0) + w * effective_etf_weight

    # Ensure the portfolio sums to 1.0
    total = sum(combined.values())
    if total > 1e-12 and abs(total - 1.0) > 1e-12:
        combined = {k: v / total for k, v in combined.items()}

    # --- Metadata ------------------------------------------------------------
    metadata: dict[str, object] = {
        "profile_name": PROFILE_NAME,
        "signal_source": SIGNAL_SOURCE,
        "status_icon": STATUS_ICON,
        "regime": regime,
        "stock_weight": effective_stock_weight,
        "etf_weight": effective_etf_weight,
        "russell_regime": regime,
        "benchmark_trend_positive": benchmark_trend_positive,
        "russell_selected_count": russell_metadata.get("selected_count", 0),
        "dca_managed_symbols": dca_managed,
        "russell_metadata": russell_metadata,
        "dca_metadata": dca_result,
        "rebalance": compute_portfolio_drift(
            combined,
            holdings=cfg.get("current_holdings_quantities", {}),
            prices=cfg.get("current_prices", {}),
            threshold=float(cfg.get("rebalance_threshold", DEFAULT_REBALANCE_THRESHOLD)),
        ),
    }

    return combined, ranked, metadata


# _check_drift removed — use quant_platform_kit.common.strategies.compute_portfolio_drift


def compute_signals(
    russell_snapshot,
    current_holdings,
    *,
    run_as_of=None,
    config: dict | None = None,
    **kwargs,
) -> tuple[
    dict[str, float] | None,
    str,
    bool,
    str,
    dict[str, object],
]:
    """Wrapper that calls ``build_target_weights`` and formats signals.

    Parameters
    ----------
    russell_snapshot :
        Feature snapshot forwarded to the Russell sub-strategy.
    current_holdings :
        Iterable of currently held symbols.
    run_as_of : optional
        Evaluation timestamp.
    config :
        Optional override dictionary (see ``build_target_weights``).
    **kwargs :
        Additional keyword arguments forwarded to ``build_target_weights``.

    Returns
    -------
    tuple
        ``(weights, signal_description, is_emergency, status_description, diagnostics)``
        matching the convention of ``mega_cap_leader_rotation.compute_signals``.
    """
    # run_as_of acknowledged but not used in this strategy version
    logger.debug("run_as_of=%s ignored (strategy does not time-travel)", run_as_of)

    cfg = {} if config is None else dict(config)
    weights, ranked, metadata = build_target_weights(
        russell_snapshot,
        current_holdings,
        config=cfg,
        **kwargs,
    )

    regime = str(metadata.get("regime", "risk_on"))
    stock_weight = float(metadata.get("stock_weight", 0.0))
    etf_weight = float(metadata.get("etf_weight", 0.0))
    benchmark_trend_positive = bool(metadata.get("benchmark_trend_positive", True))

    top_preview = ""
    if ranked is not None and not ranked.empty:
        top5 = ranked.head(5)
        top_preview = ", ".join(
            f"{row.symbol}({row.score:.2f})"
            for row in top5.itertuples(index=False)
        )

    trend_tag = "MA200_up" if benchmark_trend_positive else "MA200_down"
    signal_desc = (
        f"regime={regime} "
        f"stock={stock_weight:.0%} etf={etf_weight:.0%} "
        f"{trend_tag} "
        f"top={top_preview}"
    )

    is_emergency = regime == "hard_defense"

    status_desc = (
        f"regime={regime} | "
        f"stock={stock_weight:.0%} etf={etf_weight:.0%} | "
        f"{trend_tag}"
    )

    managed = extract_managed_symbols(russell_snapshot)
    diagnostics: dict[str, object] = {
        **metadata,
        "managed_symbols": managed,
        "status_icon": STATUS_ICON,
    }

    return weights, signal_desc, is_emergency, status_desc, diagnostics


def extract_managed_symbols(
    russell_snapshot,
    *,
    benchmark_symbol: str = mega_cap_leader_rotation.BENCHMARK_SYMBOL,
    broad_benchmark_symbol: str = mega_cap_leader_rotation.BROAD_BENCHMARK_SYMBOL,
    safe_haven: str = mega_cap_leader_rotation.SAFE_HAVEN,
) -> tuple[str, ...]:
    """Extract the union of managed symbols from both sub-strategies.

    Parameters
    ----------
    russell_snapshot :
        Feature snapshot for the Russell Top50 universe.
    benchmark_symbol :
        Benchmark symbol override.
    broad_benchmark_symbol :
        Broad benchmark symbol override.
    safe_haven :
        Safe haven symbol override.

    Returns
    -------
    tuple[str, ...]
        Deduplicated tuple of all managed symbols.
    """
    russell_symbols = mega_cap_leader_rotation.extract_managed_symbols(
        russell_snapshot,
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=safe_haven,
    )
    dca_symbols = ibit_smart_dca.DEFAULT_MANAGED_SYMBOLS
    seen: set[str] = set()
    merged: list[str] = []
    for symbol in (*russell_symbols, *dca_symbols):
        if symbol not in seen:
            seen.add(symbol)
            merged.append(symbol)
    return tuple(merged)
