"""Small offline consumer for a daily combo evidence snapshot.

The consumer only translates a JSON-shaped control-plane record into the
existing :func:`aggregate_combo_evidence` contract.  It does not fetch data,
recompute component evidence, schedule work, or create orders.  Malformed or
incomplete daily records are published as ``PARKED`` so a missing artifact
cannot turn into a retry loop or an execution decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .combo_evidence_aggregation import aggregate_combo_evidence
from .portfolio_risk_budget import PortfolioAssetRiskSpec, PortfolioRiskBudgetPolicy


def _parked(reason: str, combo_candidate_id: str = "") -> dict[str, object]:
    # Route through the aggregation contract so the output has the same
    # schema/digest shape as every other combo research result.
    result = aggregate_combo_evidence(
        combo_candidate_id=combo_candidate_id,
        combo_revision="daily-invalid",
        components=[],
        target_weights={},
        asset_risk_specs={},
        policy=PortfolioRiskBudgetPolicy(
            cash_symbol="CASH",
            max_effective_risk_exposure=0.0,
            max_symbol_weights={},
            max_underlying_effective_exposure={},
        ),
    )
    result["status"] = "PARKED"
    result["reason_codes"] = (reason,)
    result["evidence_digest"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in result.items() if key != "evidence_digest"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return result


def _risk_specs(value: object) -> dict[str, PortfolioAssetRiskSpec]:
    if not isinstance(value, Mapping):
        raise ValueError("ASSET_RISK_SPECS_REQUIRED")
    result: dict[str, PortfolioAssetRiskSpec] = {}
    for symbol, raw in value.items():
        if not isinstance(raw, Mapping):
            raise ValueError("ASSET_RISK_SPEC_INVALID")
        result[str(symbol)] = PortfolioAssetRiskSpec(
            symbol=str(raw["symbol"]),
            effective_exposure_factor=float(raw["effective_exposure_factor"]),
            underlying=str(raw["underlying"]),
            is_cash=bool(raw.get("is_cash", False)),
        )
    return result


def _policy(value: object) -> PortfolioRiskBudgetPolicy:
    if not isinstance(value, Mapping):
        raise ValueError("RISK_POLICY_REQUIRED")
    return PortfolioRiskBudgetPolicy(
        cash_symbol=str(value["cash_symbol"]),
        max_effective_risk_exposure=float(value["max_effective_risk_exposure"]),
        max_symbol_weights=dict(value["max_symbol_weights"]),
        max_underlying_effective_exposure=dict(value["max_underlying_effective_exposure"]),
        max_one_way_risk_turnover=(
            None
            if value.get("max_one_way_risk_turnover") is None
            else float(value["max_one_way_risk_turnover"])
        ),
    )


def consume_daily_combo_evidence(record: Mapping[str, Any]) -> dict[str, object]:
    """Consume one already-materialized daily combo record, offline.

    Required keys are the combo identity, component evidence references,
    target weights, static risk specs, and the risk policy.  The returned
    result is always research-only and always has both execution gates false.
    """
    try:
        if not isinstance(record, Mapping):
            raise ValueError("DAILY_RECORD_REQUIRED")
        combo_id = record["combo_candidate_id"]
        revision = record["combo_revision"]
        if not isinstance(combo_id, str) or not isinstance(revision, str):
            raise ValueError("COMBO_IDENTITY_INVALID")
        result = aggregate_combo_evidence(
            combo_candidate_id=combo_id,
            combo_revision=revision,
            components=record["components"],
            target_weights=record["target_weights"],
            asset_risk_specs=_risk_specs(record["asset_risk_specs"]),
            policy=_policy(record["risk_policy"]),
            current_weights=record.get("current_weights"),
        )
        result["consumer"] = "daily_combo_evidence.v1"
        result["execution_authorized"] = False
        result["promotion_authorized"] = False
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return _parked("DAILY_RECORD_INVALID")


__all__ = ["consume_daily_combo_evidence"]
