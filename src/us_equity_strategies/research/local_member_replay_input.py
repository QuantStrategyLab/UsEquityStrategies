"""Load an explicit local member fixture into the existing replay and ledger path.

This adapter does not establish that a file contains real optimized history.
Unsupported strategy controls still fail in ``replay_optimized_strategy``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from quant_platform_kit.strategy_lifecycle.contracts import (
    PromotionCostModel,
    ResearchTrialRecord,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

from us_equity_strategies.entrypoints._common import (
    default_signal_text_fn,
    default_translator,
)
from us_equity_strategies.research.optimized_member_identity import (
    validate_optimized_member_identity,
)
from us_equity_strategies.research.optimized_strategy_replay import (
    DatedBar,
    ExecutionAssumptions,
    ReplayIdentity,
    ReplayRequest,
    persist_optimized_strategy_trial,
)


class LocalMemberInputError(ValueError):
    """Invalid local input; never include file contents in the error."""


def _bar(value: dict[str, Any]) -> DatedBar:
    return DatedBar(date.fromisoformat(value["session"]), value["symbol"], value["open"], value["high"], value["low"], value["close"])


def _canonical_sha256(value: object) -> str:
    """SHA-256 of one value using ``_canonical_json`` rules, digest field kept."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_local_member_fixture(path: Path) -> tuple[dict[str, object], ReplayRequest]:
    """Read one explicit JSON file; return its checked identity and replay input."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(payload) is not dict or set(payload) != {"identity", "input"}:
            raise ValueError()
        identity = validate_optimized_member_identity(payload["identity"])
        source = payload["input"]
        if type(source) is not dict or set(source) != {
            "runtime_config", "calendar", "initial_cash", "initial_quantities", "prices",
            "computed_at", "derived_indicators", "benchmark_bars",
        }:
            raise ValueError()
        if _canonical_sha256(source) != identity["input_sha256"]:
            raise ValueError()
        config = source["runtime_config"]
        if (type(config) is not dict or _canonical_sha256(config) != identity["config_sha256"]
                or _canonical_sha256(identity["actual_params"]) != identity["config_sha256"]
                or "translator" in config or "signal_text_fn" in config):
            raise ValueError()
        config = dict(config)
        if type(config.get("managed_symbols")) is not list:
            raise ValueError()
        config["managed_symbols"] = tuple(config["managed_symbols"])
        config["translator"] = default_translator
        if identity["strategy_profile"] == "tqqq_growth_income" or "signal_text_fn" in config:
            config["signal_text_fn"] = default_signal_text_fn
        sessions = tuple(date.fromisoformat(item) for item in source["calendar"])
        if sessions[0].isoformat() != identity["window_start"] or sessions[-1].isoformat() != identity["window_end"]:
            raise ValueError()
        indicators = source["derived_indicators"]
        if indicators is not None and type(indicators) is not dict:
            raise ValueError()
        execution = identity["execution"]
        costs = identity["cost_inputs"]
        request = ReplayRequest(
            identity=ReplayIdentity(identity["strategy_profile"], "us_equity", identity["param_set_id"], identity["ues_revision"]),
            runtime_config=config,
            calendar=sessions,
            initial_cash=source["initial_cash"],
            initial_quantities=source["initial_quantities"],
            prices=tuple(_bar(item) for item in source["prices"]),
            execution=ExecutionAssumptions(**execution),
            cost_model=PromotionCostModel(identity["cost_source"], costs["commission_bps"], costs["slippage_bps"], costs["market_impact_bps"]),
            computed_at=source["computed_at"],
            evidence_use="fixture",
            promotion_eligible=False,
            calendar_id=identity["calendar_id"],
            periods_per_year=identity["periods_per_year"],
            derived_indicators=None if indicators is None else {date.fromisoformat(day): values for day, values in indicators.items()},
            benchmark_bars=tuple(_bar(item) for item in source["benchmark_bars"]),
        )
        return identity, request
    except (OSError, KeyError, TypeError, ValueError, IndexError):
        raise LocalMemberInputError("LOCAL_MEMBER_INPUT_INVALID") from None


def produce_local_member_fixture(path: Path, store: PerformanceStore, *, trial_id: str) -> tuple[dict[str, object], ResearchTrialRecord]:
    """Persist only a synthetic fixture trial through the existing QPK ledger."""
    if not isinstance(store, PerformanceStore) or store.cloud_bucket:
        raise LocalMemberInputError("LOCAL_STORE_REQUIRED")
    identity, request = load_local_member_fixture(path)
    return identity, persist_optimized_strategy_trial(request, store, trial_id=trial_id)


__all__ = ["LocalMemberInputError", "load_local_member_fixture", "produce_local_member_fixture"]
