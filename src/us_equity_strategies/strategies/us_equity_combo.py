"""US equity combo strategy — live core combo without the legacy IBIT leg."""

from __future__ import annotations

import logging
from typing import Iterable, Mapping

import pandas as pd

from us_equity_strategies.strategies import mega_cap_leader_rotation
from us_equity_strategies.strategies import us_equity_combo_core

PROFILE_NAME: str = "us_equity_combo"
SIGNAL_SOURCE: str = "combo"
STATUS_ICON: str = "\U0001f1fa\U0001f1f8"

logger = logging.getLogger(__name__)

DEFAULT_RUSSELL_WEIGHT: float = us_equity_combo_core.DEFAULT_RUSSELL_WEIGHT
DEFAULT_DCA_WEIGHT: float = us_equity_combo_core.DEFAULT_DCA_WEIGHT
DEFAULT_SAFE_WEIGHT: float = us_equity_combo_core.DEFAULT_SAFE_WEIGHT
DEFAULT_SOFT_RUSSELL_WEIGHT: float = us_equity_combo_core.DEFAULT_SOFT_RUSSELL_WEIGHT
DEFAULT_SOFT_DCA_WEIGHT: float = us_equity_combo_core.DEFAULT_SOFT_DCA_WEIGHT
DEFAULT_SOFT_SAFE_WEIGHT: float = us_equity_combo_core.DEFAULT_SOFT_SAFE_WEIGHT
DEFAULT_HARD_RUSSELL_WEIGHT: float = us_equity_combo_core.DEFAULT_HARD_RUSSELL_WEIGHT
DEFAULT_HARD_DCA_WEIGHT: float = us_equity_combo_core.DEFAULT_HARD_DCA_WEIGHT
DEFAULT_HARD_SAFE_WEIGHT: float = us_equity_combo_core.DEFAULT_HARD_SAFE_WEIGHT
DEFAULT_REBALANCE_THRESHOLD: float = us_equity_combo_core.DEFAULT_REBALANCE_THRESHOLD
DEFAULT_DCA_ALLOCATIONS: Mapping[str, float] = us_equity_combo_core.DEFAULT_DCA_ALLOCATIONS

_LEGACY_DROP_CONFIG_KEYS: frozenset[str] = frozenset({"market_history", "portfolio"})


def _prepare_core_config(config: dict | None) -> dict[str, object]:
    cfg: dict[str, object] = {} if config is None else dict(config)
    for key in _LEGACY_DROP_CONFIG_KEYS:
        cfg.pop(key, None)

    has_legacy_stock = "stock_weight" in cfg
    has_legacy_etf = "etf_weight" in cfg
    if has_legacy_stock and "russell_weight" not in cfg:
        cfg["russell_weight"] = cfg.pop("stock_weight")
    else:
        cfg.pop("stock_weight", None)
    if has_legacy_etf and "dca_weight" not in cfg:
        cfg["dca_weight"] = cfg.pop("etf_weight")
    else:
        cfg.pop("etf_weight", None)
    if (has_legacy_stock or has_legacy_etf) and "safe_weight" not in cfg:
        russell = float(cfg.get("russell_weight", DEFAULT_RUSSELL_WEIGHT))
        dca = float(cfg.get("dca_weight", DEFAULT_DCA_WEIGHT))
        cfg["safe_weight"] = max(0.0, 1.0 - russell - dca)
    return cfg


def _as_live_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    live_metadata = dict(metadata)
    live_metadata.update(
        {
            "profile_name": PROFILE_NAME,
            "signal_source": SIGNAL_SOURCE,
            "status_icon": STATUS_ICON,
            "stock_weight": live_metadata.get("effective_russell_weight", 0.0),
            "etf_weight": live_metadata.get("effective_dca_weight", 0.0),
        }
    )
    return live_metadata


def build_target_weights(
    russell_snapshot,
    current_holdings: Iterable[str] | None = None,
    config: dict | None = None,
) -> tuple[dict[str, float], pd.DataFrame, dict[str, object]]:
    """Build live US core combo weights under the stable ``us_equity_combo`` profile."""

    weights, ranked, metadata = us_equity_combo_core.build_target_weights(
        russell_snapshot,
        current_holdings,
        config=_prepare_core_config(config),
    )
    return weights, ranked, _as_live_metadata(metadata)


def compute_signals(
    russell_snapshot,
    current_holdings,
    *,
    run_as_of=None,
    config: dict | None = None,
    **kwargs,
) -> tuple[dict[str, float] | None, str, bool, str, dict[str, object]]:
    logger.debug("run_as_of=%s ignored (strategy does not time-travel)", run_as_of)
    cfg = _prepare_core_config(config)
    cfg.update(kwargs)
    cfg = _prepare_core_config(cfg)
    weights, signal_desc, is_emergency, status_desc, metadata = us_equity_combo_core.compute_signals(
        russell_snapshot,
        current_holdings,
        config=cfg,
    )
    return weights, signal_desc, is_emergency, status_desc, _as_live_metadata(metadata)


def extract_managed_symbols(
    russell_snapshot,
    *,
    config: Mapping[str, object] | None = None,
    benchmark_symbol: str = mega_cap_leader_rotation.BENCHMARK_SYMBOL,
    broad_benchmark_symbol: str = mega_cap_leader_rotation.BROAD_BENCHMARK_SYMBOL,
    safe_haven: str = mega_cap_leader_rotation.SAFE_HAVEN,
) -> tuple[str, ...]:
    return us_equity_combo_core.extract_managed_symbols(
        russell_snapshot,
        config=_prepare_core_config(None if config is None else dict(config)),
        benchmark_symbol=benchmark_symbol,
        broad_benchmark_symbol=broad_benchmark_symbol,
        safe_haven=safe_haven,
    )
