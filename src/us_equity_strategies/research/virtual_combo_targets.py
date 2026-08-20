"""Pure P2 construction of virtual targets for a multi-strategy portfolio.

The module consumes only frozen single-strategy virtual targets and a frozen
policy.  It has no broker, account, credentials, market-data, scheduler, or
execution dependency.  Its output is target construction, never performance
evidence or P4/P5/P6 authorization.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from us_equity_strategies.portfolio_risk_budget import (
    SCHEMA_VERSION as PORTFOLIO_RISK_BUDGET_SCHEMA,
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
    assess_portfolio_risk_budget,
)


VIRTUAL_COMBO_POLICY_SCHEMA = "qsl.us-equity-virtual-combo-policy.v1"
VIRTUAL_STRATEGY_TARGET_SCHEMA = "qsl.us-equity-frozen-virtual-strategy-target.v1"
VIRTUAL_COMBO_BASELINE_SCHEMA = "qsl.us-equity-frozen-virtual-combo-baseline.v1"
VIRTUAL_COMBO_TARGET_SCHEMA = "qsl.us-equity-virtual-combo-target.v1"
EVIDENCE_SCOPE = "VIRTUAL_TARGET_CONSTRUCTION_ONLY"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_EPSILON = 1e-12


@dataclass(frozen=True)
class FrozenStrategyVirtualTarget:
    """One fully funded virtual sleeve target, bound to a P1 input digest."""

    strategy_id: str
    source_p1_sha256: str
    target_weights: Mapping[str, float]
    target_sha256: str


@dataclass(frozen=True)
class FrozenVirtualComboBaseline:
    """A prior virtual combo target; never an account or broker position."""

    source_combo_target_sha256: str
    target_weights: Mapping[str, float]
    baseline_sha256: str


@dataclass(frozen=True)
class CorrelationRiskGroup:
    """A declared correlated-risk cluster and deterministic exposure cap."""

    group_id: str
    symbols: tuple[str, ...]
    max_effective_exposure: float


@dataclass(frozen=True)
class VirtualComboPolicy:
    """Frozen P2 policy for virtual target construction."""

    asset_risk_specs: Mapping[str, PortfolioAssetRiskSpec]
    portfolio_risk_budget: PortfolioRiskBudgetPolicy
    max_gross_risk_weight: float
    max_strategy_weights: Mapping[str, float]
    correlation_groups: tuple[CorrelationRiskGroup, ...]
    policy_sha256: str


class VirtualComboTargetError(ValueError):
    """Raised for malformed or mutable offline virtual-combo inputs."""


def _fail(message: str) -> None:
    raise VirtualComboTargetError(message)


def _identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        _fail(f"invalid {label}")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail(f"invalid {label}")
    return value


def _symbol(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or value.upper() != value:
        _fail(f"invalid {label}")
    return value


def _number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"invalid {label}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0 or (positive and numeric <= _EPSILON):
        _fail(f"invalid {label}")
    return numeric


def _fully_funded_weights(value: object, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        _fail(f"invalid {label}")
    weights = {
        _symbol(symbol, f"{label} symbol"): _number(weight, f"{label} weight", positive=True)
        for symbol, weight in value.items()
    }
    if not math.isclose(math.fsum(weights.values()), 1.0, rel_tol=0.0, abs_tol=_EPSILON):
        _fail(f"{label} must be fully funded")
    return dict(sorted(weights.items()))


def _hash(value: Mapping[str, Any], *, digest_field: str) -> str:
    material = dict(value)
    material.pop(digest_field, None)
    try:
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VirtualComboTargetError("invalid virtual combo input") from exc
    return hashlib.sha256(encoded).hexdigest()


def _frozen_target_payload(value: FrozenStrategyVirtualTarget) -> dict[str, object]:
    return {
        "schema_version": VIRTUAL_STRATEGY_TARGET_SCHEMA,
        "strategy_id": _identity(value.strategy_id, "strategy id"),
        "source_p1_sha256": _digest(value.source_p1_sha256, "source P1 digest"),
        "target_weights": _fully_funded_weights(value.target_weights, "strategy virtual target"),
        "target_sha256": _digest(value.target_sha256, "strategy target digest"),
    }


def build_frozen_strategy_virtual_target(
    *, strategy_id: object, source_p1_sha256: object, target_weights: object
) -> FrozenStrategyVirtualTarget:
    """Build a self-digested, fully funded virtual target for one strategy."""
    provisional = FrozenStrategyVirtualTarget(
        strategy_id=_identity(strategy_id, "strategy id"),
        source_p1_sha256=_digest(source_p1_sha256, "source P1 digest"),
        target_weights=_fully_funded_weights(target_weights, "strategy virtual target"),
        target_sha256="0" * 64,
    )
    return validate_frozen_strategy_virtual_target(
        replace(provisional, target_sha256=_hash(_frozen_target_payload(provisional), digest_field="target_sha256"))
    )


def validate_frozen_strategy_virtual_target(value: object) -> FrozenStrategyVirtualTarget:
    """Validate a target without reading the P1 artifact it names."""
    if not isinstance(value, FrozenStrategyVirtualTarget):
        _fail("invalid frozen strategy virtual target")
    payload = _frozen_target_payload(value)
    if payload["target_sha256"] != _hash(payload, digest_field="target_sha256"):
        _fail("frozen strategy virtual target digest mismatch")
    return FrozenStrategyVirtualTarget(
        strategy_id=str(payload["strategy_id"]),
        source_p1_sha256=str(payload["source_p1_sha256"]),
        target_weights=dict(payload["target_weights"]),
        target_sha256=str(payload["target_sha256"]),
    )


def _baseline_payload(value: FrozenVirtualComboBaseline) -> dict[str, object]:
    return {
        "schema_version": VIRTUAL_COMBO_BASELINE_SCHEMA,
        "source_combo_target_sha256": _digest(value.source_combo_target_sha256, "source combo target digest"),
        "target_weights": _fully_funded_weights(value.target_weights, "virtual combo baseline"),
        "baseline_sha256": _digest(value.baseline_sha256, "virtual combo baseline digest"),
    }


def build_frozen_virtual_combo_baseline(
    *, source_combo_target_sha256: object, target_weights: object
) -> FrozenVirtualComboBaseline:
    """Build a self-digested previous virtual target for turnover comparison."""
    provisional = FrozenVirtualComboBaseline(
        source_combo_target_sha256=_digest(source_combo_target_sha256, "source combo target digest"),
        target_weights=_fully_funded_weights(target_weights, "virtual combo baseline"),
        baseline_sha256="0" * 64,
    )
    return validate_frozen_virtual_combo_baseline(
        replace(provisional, baseline_sha256=_hash(_baseline_payload(provisional), digest_field="baseline_sha256"))
    )


def validate_frozen_virtual_combo_baseline(value: object) -> FrozenVirtualComboBaseline:
    if not isinstance(value, FrozenVirtualComboBaseline):
        _fail("invalid frozen virtual combo baseline")
    payload = _baseline_payload(value)
    if payload["baseline_sha256"] != _hash(payload, digest_field="baseline_sha256"):
        _fail("frozen virtual combo baseline digest mismatch")
    return FrozenVirtualComboBaseline(
        source_combo_target_sha256=str(payload["source_combo_target_sha256"]),
        target_weights=dict(payload["target_weights"]),
        baseline_sha256=str(payload["baseline_sha256"]),
    )


def _specs(value: object) -> dict[str, PortfolioAssetRiskSpec]:
    if not isinstance(value, Mapping) or not value:
        _fail("invalid asset risk specs")
    result: dict[str, PortfolioAssetRiskSpec] = {}
    for raw_symbol, spec in value.items():
        symbol = _symbol(raw_symbol, "asset risk spec symbol")
        if not isinstance(spec, PortfolioAssetRiskSpec) or spec.symbol != symbol:
            _fail("invalid asset risk spec")
        if not isinstance(spec.underlying, str) or not spec.underlying or spec.underlying != spec.underlying.strip():
            _fail("invalid asset underlying")
        result[symbol] = PortfolioAssetRiskSpec(
            symbol=symbol,
            effective_exposure_factor=_number(
                spec.effective_exposure_factor, "effective exposure factor", positive=True
            ),
            underlying=spec.underlying,
            is_cash=spec.is_cash,
        )
    return dict(sorted(result.items()))


def _risk_policy(value: object) -> PortfolioRiskBudgetPolicy:
    if not isinstance(value, PortfolioRiskBudgetPolicy):
        _fail("invalid portfolio risk budget")
    if not isinstance(value.max_symbol_weights, Mapping) or not isinstance(
        value.max_underlying_effective_exposure, Mapping
    ):
        _fail("invalid portfolio risk budget")
    return PortfolioRiskBudgetPolicy(
        cash_symbol=_symbol(value.cash_symbol, "cash symbol"),
        max_effective_risk_exposure=_number(
            value.max_effective_risk_exposure, "maximum effective risk exposure", positive=True
        ),
        max_symbol_weights={
            _symbol(symbol, "symbol limit symbol"): _number(limit, "symbol weight limit", positive=True)
            for symbol, limit in value.max_symbol_weights.items()
        },
        max_underlying_effective_exposure={
            str(underlying): _number(limit, "underlying exposure limit", positive=True)
            for underlying, limit in value.max_underlying_effective_exposure.items()
        },
        max_one_way_risk_turnover=(
            None
            if value.max_one_way_risk_turnover is None
            else _number(value.max_one_way_risk_turnover, "maximum one-way risk turnover", positive=True)
        ),
    )


def _strategy_limits(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        _fail("invalid strategy weight limits")
    limits = {
        _identity(strategy_id, "strategy limit id"): _number(
            limit, "strategy weight limit", positive=True
        )
        for strategy_id, limit in value.items()
    }
    if any(limit > 1.0 for limit in limits.values()):
        _fail("invalid strategy weight limit")
    return dict(sorted(limits.items()))


def _groups(value: object, *, specs: Mapping[str, PortfolioAssetRiskSpec]) -> tuple[CorrelationRiskGroup, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        _fail("missing correlation risk groups")
    result: list[CorrelationRiskGroup] = []
    for group in value:
        if not isinstance(group, CorrelationRiskGroup) or not isinstance(group.symbols, tuple):
            _fail("invalid correlation risk group")
        group_id = _identity(group.group_id, "correlation group id")
        symbols = tuple(_symbol(symbol, "correlation risk group symbol") for symbol in group.symbols)
        if len(symbols) < 2 or tuple(sorted(set(symbols))) != symbols:
            _fail("correlation risk group symbols must be uniquely sorted")
        if any(symbol not in specs or specs[symbol].is_cash for symbol in symbols):
            _fail("unknown correlation risk group symbol")
        result.append(
            CorrelationRiskGroup(
                group_id=group_id,
                symbols=symbols,
                max_effective_exposure=_number(
                    group.max_effective_exposure, "correlation group exposure limit", positive=True
                ),
            )
        )
    group_ids = tuple(group.group_id for group in result)
    if group_ids != tuple(sorted(group_ids)) or len(set(group_ids)) != len(group_ids):
        _fail("correlation risk groups must be uniquely sorted")
    return tuple(result)


def _policy_parts(value: object) -> tuple[
    dict[str, object],
    dict[str, PortfolioAssetRiskSpec],
    PortfolioRiskBudgetPolicy,
    dict[str, float],
    tuple[CorrelationRiskGroup, ...],
]:
    if not isinstance(value, VirtualComboPolicy):
        _fail("invalid virtual combo policy")
    specs = _specs(value.asset_risk_specs)
    risk_policy = _risk_policy(value.portfolio_risk_budget)
    max_gross = _number(value.max_gross_risk_weight, "maximum gross risk weight", positive=True)
    if max_gross > 1.0:
        _fail("invalid maximum gross risk weight")
    limits = _strategy_limits(value.max_strategy_weights)
    groups = _groups(value.correlation_groups, specs=specs)
    probe = assess_portfolio_risk_budget(
        target_weights={risk_policy.cash_symbol: 1.0},
        asset_risk_specs=specs,
        policy=risk_policy,
    )
    if probe["status"] == "PARKED":
        _fail("invalid portfolio risk budget")
    payload: dict[str, object] = {
        "schema_version": VIRTUAL_COMBO_POLICY_SCHEMA,
        "portfolio_risk_budget_schema": PORTFOLIO_RISK_BUDGET_SCHEMA,
        "asset_risk_specs": {
            symbol: {
                "effective_exposure_factor": spec.effective_exposure_factor,
                "underlying": spec.underlying,
                "is_cash": spec.is_cash,
            }
            for symbol, spec in specs.items()
        },
        "portfolio_risk_budget": {
            "cash_symbol": risk_policy.cash_symbol,
            "max_effective_risk_exposure": risk_policy.max_effective_risk_exposure,
            "max_symbol_weights": dict(sorted(risk_policy.max_symbol_weights.items())),
            "max_underlying_effective_exposure": dict(
                sorted(risk_policy.max_underlying_effective_exposure.items())
            ),
            "max_one_way_risk_turnover": risk_policy.max_one_way_risk_turnover,
        },
        "max_gross_risk_weight": max_gross,
        "max_strategy_weights": limits,
        "correlation_groups": [
            {
                "group_id": group.group_id,
                "symbols": list(group.symbols),
                "max_effective_exposure": group.max_effective_exposure,
            }
            for group in groups
        ],
        "policy_sha256": _digest(value.policy_sha256, "virtual combo policy digest"),
    }
    return payload, specs, risk_policy, limits, groups


def build_virtual_combo_policy(
    *,
    asset_risk_specs: object,
    portfolio_risk_budget: object,
    max_gross_risk_weight: object,
    max_strategy_weights: object,
    correlation_groups: object,
) -> VirtualComboPolicy:
    """Build a self-digested policy containing all P2 allocation constraints."""
    provisional = VirtualComboPolicy(
        asset_risk_specs=asset_risk_specs if isinstance(asset_risk_specs, Mapping) else {},
        portfolio_risk_budget=(
            portfolio_risk_budget
            if isinstance(portfolio_risk_budget, PortfolioRiskBudgetPolicy)
            else PortfolioRiskBudgetPolicy("", 0.0, {}, {})
        ),
        max_gross_risk_weight=max_gross_risk_weight,
        max_strategy_weights=max_strategy_weights if isinstance(max_strategy_weights, Mapping) else {},
        correlation_groups=(
            tuple(correlation_groups)
            if isinstance(correlation_groups, Sequence) and not isinstance(correlation_groups, (str, bytes))
            else ()
        ),
        policy_sha256="0" * 64,
    )
    payload, _, _, _, _ = _policy_parts(provisional)
    return validate_virtual_combo_policy(
        replace(provisional, policy_sha256=_hash(payload, digest_field="policy_sha256"))
    )


def validate_virtual_combo_policy(value: object) -> VirtualComboPolicy:
    """Validate a policy and fail closed if its frozen digest no longer matches."""
    payload, specs, risk_policy, limits, groups = _policy_parts(value)
    if payload["policy_sha256"] != _hash(payload, digest_field="policy_sha256"):
        _fail("virtual combo policy digest mismatch")
    return VirtualComboPolicy(
        asset_risk_specs=specs,
        portfolio_risk_budget=risk_policy,
        max_gross_risk_weight=float(payload["max_gross_risk_weight"]),
        max_strategy_weights=limits,
        correlation_groups=groups,
        policy_sha256=str(payload["policy_sha256"]),
    )


def _targets(value: object) -> tuple[FrozenStrategyVirtualTarget, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        _fail("virtual combo requires at least two frozen strategy targets")
    targets = tuple(validate_frozen_strategy_virtual_target(target) for target in value)
    ids = tuple(target.strategy_id for target in targets)
    if ids != tuple(sorted(set(ids))):
        _fail("frozen strategy targets must be uniquely sorted")
    return targets


def _budgets(
    value: object, *, targets: Sequence[FrozenStrategyVirtualTarget], policy: VirtualComboPolicy
) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {target.strategy_id for target in targets}:
        _fail("strategy budget weights must match frozen strategy targets")
    budgets = {
        target.strategy_id: _number(value[target.strategy_id], "strategy budget weight", positive=True)
        for target in targets
    }
    if any(weight > 1.0 or weight > policy.max_strategy_weights.get(strategy_id, 0.0) + _EPSILON for strategy_id, weight in budgets.items()):
        _fail("strategy budget exceeds frozen limit")
    if not math.isclose(math.fsum(budgets.values()), 1.0, rel_tol=0.0, abs_tol=_EPSILON):
        _fail("strategy budget weights must be fully funded")
    return dict(sorted(budgets.items()))


def _combined_weights(
    *, targets: Sequence[FrozenStrategyVirtualTarget], budgets: Mapping[str, float]
) -> dict[str, float]:
    combined: dict[str, float] = {}
    for target in targets:
        for symbol, weight in target.target_weights.items():
            combined[symbol] = combined.get(symbol, 0.0) + budgets[target.strategy_id] * weight
    return dict(sorted(combined.items()))


def _combo_scalar(
    *, target: Mapping[str, float], policy: VirtualComboPolicy
) -> tuple[float, tuple[str, ...]]:
    risk_symbols = [symbol for symbol, spec in policy.asset_risk_specs.items() if not spec.is_cash]
    gross = math.fsum(target.get(symbol, 0.0) for symbol in risk_symbols)
    scalar, reasons = 1.0, []
    if gross > policy.max_gross_risk_weight + _EPSILON:
        scalar = policy.max_gross_risk_weight / gross
        reasons.append("COMBO_GROSS_RISK_WEIGHT_REDUCED")
    for group in policy.correlation_groups:
        exposure = math.fsum(
            target.get(symbol, 0.0) * policy.asset_risk_specs[symbol].effective_exposure_factor
            for symbol in group.symbols
        )
        if exposure > group.max_effective_exposure + _EPSILON:
            scalar = min(scalar, group.max_effective_exposure / exposure)
            reasons.append("COMBO_CORRELATION_GROUP_REDUCED")
    return scalar, tuple(reasons)


def _apply_scalar(*, target: Mapping[str, float], policy: VirtualComboPolicy, scalar: float) -> dict[str, float]:
    result, removed = dict(target), 0.0
    for symbol, spec in policy.asset_risk_specs.items():
        if spec.is_cash:
            continue
        original = result.get(symbol, 0.0)
        result[symbol] = original * scalar
        removed += original - result[symbol]
    cash = policy.portfolio_risk_budget.cash_symbol
    result[cash] = result.get(cash, 0.0) + removed
    return {symbol: weight for symbol, weight in sorted(result.items()) if weight > _EPSILON}


def _input_hash(
    *, targets: Sequence[FrozenStrategyVirtualTarget], budgets: Mapping[str, float], baseline: FrozenVirtualComboBaseline | None
) -> str:
    material = {
        "strategy_targets": [
            {"strategy_id": target.strategy_id, "target_sha256": target.target_sha256}
            for target in targets
        ],
        "strategy_budget_weights": dict(sorted(budgets.items())),
        "baseline_sha256": None if baseline is None else baseline.baseline_sha256,
    }
    return _hash(material, digest_field="unused_digest_field")


def _summary(
    *,
    target: Mapping[str, float],
    budgets: Mapping[str, float],
    policy: VirtualComboPolicy,
    risk_metrics: Mapping[str, object],
    baseline: FrozenVirtualComboBaseline | None,
) -> dict[str, object]:
    group_exposure = {
        group.group_id: math.fsum(
            target.get(symbol, 0.0) * policy.asset_risk_specs[symbol].effective_exposure_factor
            for symbol in group.symbols
        )
        for group in policy.correlation_groups
    }
    return {
        "gross_risk_weight": math.fsum(
            target.get(symbol, 0.0)
            for symbol, spec in policy.asset_risk_specs.items()
            if not spec.is_cash
        ),
        "strategy_budget_weights": dict(sorted(budgets.items())),
        "effective_risk_exposure": risk_metrics["effective_risk_exposure"],
        "underlying_effective_exposure": risk_metrics["underlying_effective_exposure"],
        "correlation_group_effective_exposure": dict(sorted(group_exposure.items())),
        "rebalancing": {
            "basis": "FROZEN_VIRTUAL_COMBO_BASELINE" if baseline else "NONE",
            "turnover_limit_enabled": policy.portfolio_risk_budget.max_one_way_risk_turnover is not None,
            "one_way_risk_turnover": risk_metrics["one_way_risk_turnover"],
        },
    }


def _parked(reason: str) -> dict[str, object]:
    return {
        "schema_version": VIRTUAL_COMBO_TARGET_SCHEMA,
        "research_only": True,
        "execution_authorized": False,
        "evidence_scope": EVIDENCE_SCOPE,
        "status": "PARKED",
        "reason_codes": (reason,),
        "policy_sha256": None,
        "input_sha256": None,
        "combo_target_weights": {},
        "summary": {},
        "combo_target_sha256": None,
    }


def construct_virtual_combo_target(
    *,
    strategy_targets: Sequence[FrozenStrategyVirtualTarget],
    strategy_budget_weights: Mapping[str, float],
    policy: VirtualComboPolicy,
    rebalance_baseline: FrozenVirtualComboBaseline | None = None,
) -> dict[str, object]:
    """Combine frozen virtual sleeves into one fail-closed research target."""
    try:
        policy = validate_virtual_combo_policy(policy)
        targets = _targets(strategy_targets)
        budgets = _budgets(strategy_budget_weights, targets=targets, policy=policy)
        baseline = (
            None if rebalance_baseline is None else validate_frozen_virtual_combo_baseline(rebalance_baseline)
        )
        if policy.portfolio_risk_budget.max_one_way_risk_turnover is not None and baseline is None:
            _fail("missing frozen virtual combo rebalance baseline")
        raw_target = _combined_weights(targets=targets, budgets=budgets)
        scalar, reasons = _combo_scalar(target=raw_target, policy=policy)
        risk = assess_portfolio_risk_budget(
            target_weights=_apply_scalar(
                target=raw_target, policy=policy, scalar=scalar
            ),
            asset_risk_specs=policy.asset_risk_specs,
            policy=policy.portfolio_risk_budget,
            current_weights=None if baseline is None else baseline.target_weights,
        )
        if risk["status"] == "PARKED":
            _fail("portfolio risk budget rejected virtual combo target")
        final_target = dict(risk["recommended_target_weights"])
        reason_codes = reasons + tuple(risk["reason_codes"])
        result: dict[str, object] = {
            "schema_version": VIRTUAL_COMBO_TARGET_SCHEMA,
            "research_only": True,
            "execution_authorized": False,
            "evidence_scope": EVIDENCE_SCOPE,
            "status": "REDUCE" if reason_codes else "APPROVE",
            "reason_codes": reason_codes,
            "policy_sha256": policy.policy_sha256,
            "input_sha256": _input_hash(targets=targets, budgets=budgets, baseline=baseline),
            "combo_target_weights": final_target,
            "summary": _summary(
                target=final_target,
                budgets=budgets,
                policy=policy,
                risk_metrics=dict(risk["metrics"]),
                baseline=baseline,
            ),
            "combo_target_sha256": "",
        }
        result["combo_target_sha256"] = _hash(result, digest_field="combo_target_sha256")
        return result
    except VirtualComboTargetError as exc:
        return _parked(str(exc))


__all__ = [
    "EVIDENCE_SCOPE",
    "PORTFOLIO_RISK_BUDGET_SCHEMA",
    "VIRTUAL_COMBO_BASELINE_SCHEMA",
    "VIRTUAL_COMBO_POLICY_SCHEMA",
    "VIRTUAL_COMBO_TARGET_SCHEMA",
    "VIRTUAL_STRATEGY_TARGET_SCHEMA",
    "CorrelationRiskGroup",
    "FrozenStrategyVirtualTarget",
    "FrozenVirtualComboBaseline",
    "VirtualComboPolicy",
    "VirtualComboTargetError",
    "build_frozen_strategy_virtual_target",
    "build_frozen_virtual_combo_baseline",
    "build_virtual_combo_policy",
    "construct_virtual_combo_target",
    "validate_frozen_strategy_virtual_target",
    "validate_frozen_virtual_combo_baseline",
    "validate_virtual_combo_policy",
]
