"""US leveraged equity combo — TQQQ (40%) + SOXL (20%) + BOXX (40%).

Combines two 3x leveraged ETFs (TQQQ, SOXL) with a cash/safe-haven ETF (BOXX)
for drawdown protection and rebalancing reserves.

Static mode
-----------
Fixed: TQQQ 40%, SOXL 20%, BOXX 40%.

Dynamic mode
------------
SPY 200-day MA regime signal:
- SPY above MA200 (bull): normal weights
- SPY below MA200 (bear): TQQQ cut 50% -> 20%, SOXL cut 50% -> 10%, BOXX -> 70%
"""

from __future__ import annotations

from typing import Any

from quant_platform_kit.common.strategies import compute_portfolio_drift

PROFILE_NAME = "us_equity_combo_leveraged"
SIGNAL_SOURCE = "combo"
STATUS_ICON = "\U0001f1fa\U0001f1f8"

DEFAULT_TQQQ_WEIGHT = 0.40
DEFAULT_SOXL_WEIGHT = 0.20
DEFAULT_BOXX_WEIGHT = 0.40
DEFAULT_REBALANCE_THRESHOLD = 0.05  # 5% drift triggers rebalance


def build_target_weights(
    market_data: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[dict[str, float], dict[str, object]]:
    """Compute combined target weights for leveraged US combo.

    Parameters
    ----------
    market_data : dict or None
        Contains price/regime info.  If it holds a 'spy_above_ma200' key,
        dynamic mode uses it.
    config : dict or None
        Override configuration.  Accepted keys:
        - tqqq_weight, soxl_weight, boxx_weight (overrides defaults)
        - dynamic (bool, default True)

    Returns
    -------
    tuple[dict[str, float], dict[str, object]]
    """
    cfg = dict(config or {})

    tqqq_weight = float(cfg.get("tqqq_weight", DEFAULT_TQQQ_WEIGHT))
    soxl_weight = float(cfg.get("soxl_weight", DEFAULT_SOXL_WEIGHT))
    boxx_weight = float(cfg.get("boxx_weight", DEFAULT_BOXX_WEIGHT))
    dynamic = bool(cfg.get("dynamic", True))

    # Dynamic mode: SPY MA200 risk-off
    spy_above_ma200 = True
    if isinstance(market_data, dict):
        spy_above_ma200 = bool(market_data.get("spy_above_ma200", True))

    if dynamic and not spy_above_ma200:
        tqqq_weight = DEFAULT_TQQQ_WEIGHT * 0.50
        soxl_weight = DEFAULT_SOXL_WEIGHT * 0.50
        boxx_weight = 1.0 - tqqq_weight - soxl_weight

    # Normalize
    total = tqqq_weight + soxl_weight + boxx_weight
    if abs(total - 1.0) > 1e-12 and total > 0:
        tqqq_weight /= total
        soxl_weight /= total
        boxx_weight /= total

    weights: dict[str, float] = {
        "TQQQ": tqqq_weight,
        "SOXL": soxl_weight,
        "BOXX": boxx_weight,
    }

    metadata: dict[str, object] = {
        "profile_name": PROFILE_NAME,
        "signal_source": SIGNAL_SOURCE,
        "status_icon": STATUS_ICON,
        "spy_above_ma200": spy_above_ma200,
        "effective_weights": {
            "TQQQ": tqqq_weight,
            "SOXL": soxl_weight,
            "BOXX": boxx_weight,
        },
        "dynamic": dynamic,
        "rebalance": compute_portfolio_drift(
            weights,
            holdings=cfg.get("current_holdings_quantities", {}),
            prices=cfg.get("current_prices", {}),
            threshold=float(cfg.get("rebalance_threshold", DEFAULT_REBALANCE_THRESHOLD)),
        ),
    }

    return weights, metadata


# _check_drift removed — use quant_platform_kit.common.strategies.compute_portfolio_drift


def extract_managed_symbols(*args: Any, **kwargs: Any) -> tuple[str, ...]:
    return ("TQQQ", "SOXL", "BOXX")


def compute_signals(
    market_data: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
):
    weights, metadata = build_target_weights(
        market_data=market_data,
        config=config,
        **kwargs,
    )
    ew = metadata.get("effective_weights", {})
    spy_status = "MA200_up" if metadata.get("spy_above_ma200", True) else "MA200_down"
    signal_desc = (
        f"leveraged combo {spy_status} "
        f"TQQQ={ew.get('TQQQ', 0):.0%} SOXL={ew.get('SOXL', 0):.0%} BOXX={ew.get('BOXX', 0):.0%}"
    )
    status_desc = (
        f"{spy_status} | "
        f"TQQQ={ew.get('TQQQ', 0):.0%} SOXL={ew.get('SOXL', 0):.0%} BOXX={ew.get('BOXX', 0):.0%}"
    )
    has_cash = ew.get("BOXX", 0) > 0.5
    return (
        weights,
        signal_desc,
        has_cash,
        status_desc,
        {
            **metadata,
            "managed_symbols": extract_managed_symbols(),
            "status_icon": STATUS_ICON,
            "actionable": True,
        },
    )
