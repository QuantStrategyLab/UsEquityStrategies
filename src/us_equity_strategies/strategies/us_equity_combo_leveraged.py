"""US leveraged equity combo — TQQQ (40%) + SOXL (20%) + BOXX (40%).

Combines two 3x leveraged ETFs (TQQQ, SOXL) with a cash/safe-haven ETF (BOXX)
for drawdown protection and rebalancing reserves.

Static mode
-----------
Fixed: TQQQ 40%, SOXL 20%, BOXX 40%.

Dynamic mode
------------
Multi-asset 200-day MA regime signal:
- SPY/QQQ/SOXX all above MA200: normal weights
- any of SPY/QQQ/SOXX below MA200: risk legs retain 50% by default, BOXX receives the rest
- if only 20-day MA slope weakens, keep current weights and mark soft defense
"""

from __future__ import annotations

import json
import math
from importlib import resources
from pathlib import Path
from typing import Any

from quant_platform_kit.common.strategies import compute_portfolio_drift

PROFILE_NAME = "us_equity_combo_leveraged"
SIGNAL_SOURCE = "combo"
STATUS_ICON = "\U0001f1fa\U0001f1f8"

DEFAULT_TQQQ_WEIGHT = 0.40
DEFAULT_SOXL_WEIGHT = 0.20
DEFAULT_BOXX_WEIGHT = 0.40
DEFAULT_REBALANCE_THRESHOLD = 0.05  # 5% drift triggers rebalance
DEFAULT_HARD_DEFENSE_RISK_EXPOSURE = 0.50
DEFAULT_SOFT_DEFENSE_RISK_EXPOSURE = 1.00
REGIME_SYMBOLS = ("SPY", "QQQ", "SOXX")


def _noop_logger(_message: str) -> None:
    return None


def _read_runtime_config_text(config_path: str | Path) -> tuple[str, str]:
    raw_path = str(config_path).strip()
    if raw_path.startswith("package://"):
        package_resource = raw_path.removeprefix("package://")
        package_name, separator, resource_name = package_resource.partition("/")
        if not separator or not package_name or not resource_name:
            raise ValueError(f"Invalid package runtime config path: {raw_path!r}")
        resource = resources.files(package_name).joinpath(resource_name)
        if not resource.is_file():
            raise FileNotFoundError(f"Runtime strategy config not found: {raw_path}")
        return resource.read_text(encoding="utf-8"), raw_path

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Runtime strategy config not found: {config_file}")
    return config_file.read_text(encoding="utf-8"), str(config_file)


def load_runtime_parameters(
    *,
    config_path: str | Path | None = None,
    logger=None,
) -> dict[str, object]:
    if logger is None:
        logger = _noop_logger
    if config_path is None:
        return {}

    config_text, resolved_config_path = _read_runtime_config_text(config_path)
    payload = json.loads(config_text)
    if not isinstance(payload, dict):
        raise ValueError("Runtime strategy config JSON root must be an object")
    profile = str(payload.get("strategy_profile") or PROFILE_NAME).strip()
    if profile != PROFILE_NAME:
        raise ValueError(f"Runtime strategy config strategy_profile must be {PROFILE_NAME!r}")

    raw_runtime_config = payload.get("runtime_config", payload)
    if not isinstance(raw_runtime_config, dict):
        raise ValueError("Runtime strategy config runtime_config must be an object")

    runtime_config = dict(raw_runtime_config)
    for key in (
        "tqqq_weight",
        "soxl_weight",
        "boxx_weight",
        "hard_defense_risk_exposure",
        "soft_defense_risk_exposure",
    ):
        if key not in runtime_config:
            continue
        value = float(runtime_config[key])
        if not math.isfinite(value):
            raise ValueError(f"Runtime strategy config {key} must be finite")
        if value < 0.0:
            raise ValueError(f"Runtime strategy config {key} must be non-negative")
        runtime_config[key] = value
    if "hard_defense_risk_exposure" in runtime_config and float(runtime_config["hard_defense_risk_exposure"]) > 1.0:
        raise ValueError("Runtime strategy config hard_defense_risk_exposure must be in [0, 1]")
    if "soft_defense_risk_exposure" in runtime_config and float(runtime_config["soft_defense_risk_exposure"]) > 1.0:
        raise ValueError("Runtime strategy config soft_defense_risk_exposure must be in [0, 1]")
    if all(key in runtime_config for key in ("tqqq_weight", "soxl_weight", "boxx_weight")):
        total = sum(float(runtime_config[key]) for key in ("tqqq_weight", "soxl_weight", "boxx_weight"))
        if total <= 0.0:
            raise ValueError("Runtime strategy config weights must sum to a positive value")

    runtime_config["runtime_config_name"] = str(payload.get("name") or Path(resolved_config_path).stem)
    runtime_config["runtime_config_path"] = resolved_config_path
    runtime_config["runtime_config_source"] = "external_config"
    logger(f"[{PROFILE_NAME}] runtime config source=external_config path={resolved_config_path}")
    return runtime_config


def _market_bool(market_data: dict[str, Any], key: str, default: bool) -> bool:
    value = market_data.get(key, default)
    return bool(value)


def _resolve_regime(market_data: dict[str, Any] | None) -> dict[str, object]:
    raw = market_data if isinstance(market_data, dict) else {}
    above_ma200 = {
        symbol: _market_bool(raw, f"{symbol.lower()}_above_ma200", True)
        for symbol in REGIME_SYMBOLS
    }
    ma20_slope_positive = {
        symbol: _market_bool(raw, f"{symbol.lower()}_ma20_slope_positive", True)
        for symbol in REGIME_SYMBOLS
    }

    if not all(above_ma200.values()):
        regime_state = "hard_defense"
    elif not all(ma20_slope_positive.values()):
        regime_state = "soft_defense"
    else:
        regime_state = "risk_on"

    return {
        "regime_state": regime_state,
        "above_ma200": above_ma200,
        "ma20_slope_positive": ma20_slope_positive,
        "spy_above_ma200": above_ma200["SPY"],
    }


def build_target_weights(
    market_data: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[dict[str, float], dict[str, object]]:
    """Compute combined target weights for leveraged US combo.

    Parameters
    ----------
    market_data : dict or None
        Contains price/regime info.  Dynamic mode uses SPY/QQQ/SOXX
        '<symbol>_above_ma200' and '<symbol>_ma20_slope_positive' keys when
        available. Missing non-SPY keys default to risk-on for backwards
        compatibility.
    config : dict or None
        Override configuration.  Accepted keys:
        - tqqq_weight, soxl_weight, boxx_weight (overrides defaults)
        - dynamic (bool, default True)
        - hard_defense_risk_exposure (float, default 0.50): retained
          TQQQ/SOXL fraction when SPY is below MA200.  Use 0.0 for a
          full BOXX hard-defense shadow.
        - soft_defense_risk_exposure (float, default 1.00): retained
          TQQQ/SOXL fraction when MA20 slope weakens while MA200 stays intact.

    Returns
    -------
    tuple[dict[str, float], dict[str, object]]
    """
    cfg = dict(config or {})

    tqqq_weight = float(cfg.get("tqqq_weight", DEFAULT_TQQQ_WEIGHT))
    soxl_weight = float(cfg.get("soxl_weight", DEFAULT_SOXL_WEIGHT))
    boxx_weight = float(cfg.get("boxx_weight", DEFAULT_BOXX_WEIGHT))
    dynamic = bool(cfg.get("dynamic", True))
    hard_defense_risk_exposure = min(
        1.0,
        max(0.0, float(cfg.get("hard_defense_risk_exposure", DEFAULT_HARD_DEFENSE_RISK_EXPOSURE))),
    )
    soft_defense_risk_exposure = min(
        1.0,
        max(0.0, float(cfg.get("soft_defense_risk_exposure", DEFAULT_SOFT_DEFENSE_RISK_EXPOSURE))),
    )

    regime = _resolve_regime(market_data)
    regime_state = str(regime["regime_state"])

    if dynamic and regime_state == "hard_defense":
        risk_exposure = hard_defense_risk_exposure
        tqqq_weight *= risk_exposure
        soxl_weight *= risk_exposure
        boxx_weight = 1.0 - tqqq_weight - soxl_weight
    elif dynamic and regime_state == "soft_defense":
        risk_exposure = soft_defense_risk_exposure
        tqqq_weight *= risk_exposure
        soxl_weight *= risk_exposure
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
        "regime_state": regime_state if dynamic else "risk_on",
        "spy_above_ma200": regime["spy_above_ma200"],
        "above_ma200": regime["above_ma200"],
        "ma20_slope_positive": regime["ma20_slope_positive"],
        "effective_weights": {
            "TQQQ": tqqq_weight,
            "SOXL": soxl_weight,
            "BOXX": boxx_weight,
        },
        "dynamic": dynamic,
        "hard_defense_risk_exposure": hard_defense_risk_exposure,
        "soft_defense_risk_exposure": soft_defense_risk_exposure,
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
    regime_state = str(metadata.get("regime_state") or "risk_on")
    spy_status = "MA200_up" if metadata.get("spy_above_ma200", True) else "MA200_down"
    signal_desc = (
        f"leveraged combo {regime_state} {spy_status} "
        f"TQQQ={ew.get('TQQQ', 0):.0%} SOXL={ew.get('SOXL', 0):.0%} BOXX={ew.get('BOXX', 0):.0%}"
    )
    status_desc = (
        f"{regime_state} {spy_status} | "
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
