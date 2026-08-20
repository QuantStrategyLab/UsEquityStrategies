"""Pure research risk budgeting for a multi-sleeve portfolio candidate.

This module is deliberately upstream of broker, paper, shadow, and live
execution.  It accepts a proposed long-only target allocation plus explicit
asset look-through data, and returns a deterministic research recommendation:
``APPROVE``, ``REDUCE``, or ``PARKED``.  It neither reads account data nor
submits, stores, or schedules anything.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

SCHEMA_VERSION = "qsl.portfolio-risk-budget-research.v1"
_EPSILON = 1e-12
_ITERATIONS = 64


@dataclass(frozen=True)
class PortfolioAssetRiskSpec:
    """Static look-through information for one candidate asset."""

    symbol: str
    effective_exposure_factor: float
    underlying: str
    is_cash: bool = False


@dataclass(frozen=True)
class PortfolioRiskBudgetPolicy:
    """Explicit P2/P3 research limits; this is not an execution mandate."""

    cash_symbol: str
    max_effective_risk_exposure: float
    max_symbol_weights: Mapping[str, float]
    max_underlying_effective_exposure: Mapping[str, float]
    max_one_way_risk_turnover: float | None = None


class PortfolioRiskBudgetError(ValueError):
    """Raised internally for a malformed research allocation or policy."""


def _fail(message: str) -> None:
    raise PortfolioRiskBudgetError(message)


def _number(value: object, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"invalid {label}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < minimum:
        _fail(f"invalid {label}")
    return numeric


def _symbol(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or value.upper() != value:
        _fail(f"invalid {label}")
    return value


def _underlying(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"invalid {label}")
    return value


def _asset_specs(value: Mapping[str, PortfolioAssetRiskSpec]) -> dict[str, PortfolioAssetRiskSpec]:
    if not isinstance(value, Mapping) or not value:
        _fail("invalid asset risk specs")
    result: dict[str, PortfolioAssetRiskSpec] = {}
    for mapping_symbol, spec in value.items():
        symbol = _symbol(mapping_symbol, "asset symbol")
        if not isinstance(spec, PortfolioAssetRiskSpec) or spec.symbol != symbol:
            _fail("invalid asset risk spec")
        factor = _number(spec.effective_exposure_factor, "effective exposure factor")
        if factor <= 0.0:
            _fail("invalid effective exposure factor")
        _underlying(spec.underlying, "underlying")
        if not isinstance(spec.is_cash, bool):
            _fail("invalid cash classification")
        result[symbol] = PortfolioAssetRiskSpec(
            symbol=symbol,
            effective_exposure_factor=factor,
            underlying=spec.underlying,
            is_cash=spec.is_cash,
        )
    return result


def _weights(
    value: Mapping[str, float] | None,
    *,
    specs: Mapping[str, PortfolioAssetRiskSpec],
    label: str,
) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _fail(f"invalid {label}")
    result: dict[str, float] = {}
    for raw_symbol, raw_weight in value.items():
        symbol = _symbol(raw_symbol, f"{label} symbol")
        if symbol not in specs:
            _fail(f"unknown {label} symbol")
        result[symbol] = _number(raw_weight, f"{label} weight")
    if sum(result.values()) > 1.0 + _EPSILON:
        _fail(f"{label} exceeds fully-funded portfolio")
    return result


def _policy(
    value: PortfolioRiskBudgetPolicy,
    *,
    specs: Mapping[str, PortfolioAssetRiskSpec],
) -> PortfolioRiskBudgetPolicy:
    if not isinstance(value, PortfolioRiskBudgetPolicy):
        _fail("invalid portfolio risk policy")
    cash_symbol = _symbol(value.cash_symbol, "cash symbol")
    cash_spec = specs.get(cash_symbol)
    if cash_spec is None or not cash_spec.is_cash:
        _fail("cash symbol is not a cash asset")
    max_effective = _number(
        value.max_effective_risk_exposure, "maximum effective risk exposure"
    )
    if max_effective <= 0.0:
        _fail("invalid maximum effective risk exposure")
    if not isinstance(value.max_symbol_weights, Mapping):
        _fail("invalid symbol weight limits")
    symbol_limits: dict[str, float] = {}
    for raw_symbol, raw_limit in value.max_symbol_weights.items():
        symbol = _symbol(raw_symbol, "symbol weight limit symbol")
        if symbol not in specs:
            _fail("unknown symbol weight limit")
        limit = _number(raw_limit, "symbol weight limit")
        if limit <= 0.0 or limit > 1.0:
            _fail("invalid symbol weight limit")
        symbol_limits[symbol] = limit
    if not isinstance(value.max_underlying_effective_exposure, Mapping):
        _fail("invalid underlying exposure limits")
    underlying_limits: dict[str, float] = {}
    known_underlyings = {spec.underlying for spec in specs.values() if not spec.is_cash}
    for raw_underlying, raw_limit in value.max_underlying_effective_exposure.items():
        underlying = _underlying(raw_underlying, "underlying exposure limit underlying")
        if underlying not in known_underlyings:
            _fail("unknown underlying exposure limit")
        limit = _number(raw_limit, "underlying exposure limit")
        if limit <= 0.0:
            _fail("invalid underlying exposure limit")
        underlying_limits[underlying] = limit
    turnover = value.max_one_way_risk_turnover
    if turnover is not None:
        turnover = _number(turnover, "maximum one-way risk turnover")
        if turnover <= 0.0 or turnover > 1.0:
            _fail("invalid maximum one-way risk turnover")
    return PortfolioRiskBudgetPolicy(
        cash_symbol=cash_symbol,
        max_effective_risk_exposure=max_effective,
        max_symbol_weights=symbol_limits,
        max_underlying_effective_exposure=underlying_limits,
        max_one_way_risk_turnover=turnover,
    )


def _risk_symbols(specs: Mapping[str, PortfolioAssetRiskSpec]) -> tuple[str, ...]:
    return tuple(sorted(symbol for symbol, spec in specs.items() if not spec.is_cash))


def _scale_limit(
    *,
    target_weights: Mapping[str, float],
    current_weights: Mapping[str, float],
    specs: Mapping[str, PortfolioAssetRiskSpec],
    policy: PortfolioRiskBudgetPolicy,
) -> float:
    risk_symbols = _risk_symbols(specs)
    scalar = 1.0
    effective_exposure = sum(
        target_weights.get(symbol, 0.0) * specs[symbol].effective_exposure_factor
        for symbol in risk_symbols
    )
    if effective_exposure > policy.max_effective_risk_exposure:
        scalar = min(scalar, policy.max_effective_risk_exposure / effective_exposure)
    for symbol, limit in policy.max_symbol_weights.items():
        if specs[symbol].is_cash:
            continue
        weight = target_weights.get(symbol, 0.0)
        if weight > limit:
            scalar = min(scalar, limit / weight)
    for underlying, limit in policy.max_underlying_effective_exposure.items():
        exposure = sum(
            target_weights.get(symbol, 0.0) * spec.effective_exposure_factor
            for symbol, spec in specs.items()
            if not spec.is_cash and spec.underlying == underlying
        )
        if exposure > limit:
            scalar = min(scalar, limit / exposure)
    turnover_limit = policy.max_one_way_risk_turnover
    if turnover_limit is not None:
        def one_way_turnover(candidate_scalar: float) -> float:
            return sum(
                max(candidate_scalar * target_weights.get(symbol, 0.0) - current_weights.get(symbol, 0.0), 0.0)
                for symbol in risk_symbols
            )

        if one_way_turnover(scalar) > turnover_limit + _EPSILON:
            lower = 0.0
            upper = scalar
            for _ in range(_ITERATIONS):
                midpoint = (lower + upper) / 2.0
                if one_way_turnover(midpoint) <= turnover_limit:
                    lower = midpoint
                else:
                    upper = midpoint
            scalar = lower
    if not math.isfinite(scalar) or scalar < 0.0:
        _fail("invalid risk scalar")
    return min(1.0, scalar)


def _recommended_weights(
    *,
    target_weights: Mapping[str, float],
    specs: Mapping[str, PortfolioAssetRiskSpec],
    cash_symbol: str,
    scalar: float,
) -> dict[str, float]:
    result = dict(target_weights)
    removed_weight = 0.0
    for symbol, spec in specs.items():
        if spec.is_cash:
            continue
        original = result.get(symbol, 0.0)
        reduced = original * scalar
        result[symbol] = reduced
        removed_weight += original - reduced
    result[cash_symbol] = result.get(cash_symbol, 0.0) + removed_weight
    return {symbol: weight for symbol, weight in sorted(result.items()) if weight > _EPSILON}


def _metrics(
    *,
    weights: Mapping[str, float],
    current_weights: Mapping[str, float],
    specs: Mapping[str, PortfolioAssetRiskSpec],
) -> dict[str, object]:
    risk_symbols = _risk_symbols(specs)
    underlying_effective: dict[str, float] = {}
    for symbol in risk_symbols:
        spec = specs[symbol]
        underlying_effective[spec.underlying] = (
            underlying_effective.get(spec.underlying, 0.0)
            + weights.get(symbol, 0.0) * spec.effective_exposure_factor
        )
    return {
        "nominal_weight": {symbol: weights.get(symbol, 0.0) for symbol in sorted(weights)},
        "effective_risk_exposure": sum(
            weights.get(symbol, 0.0) * specs[symbol].effective_exposure_factor
            for symbol in risk_symbols
        ),
        "underlying_effective_exposure": dict(sorted(underlying_effective.items())),
        "one_way_risk_turnover": sum(
            max(weights.get(symbol, 0.0) - current_weights.get(symbol, 0.0), 0.0)
            for symbol in risk_symbols
        ),
    }


def assess_portfolio_risk_budget(
    *,
    target_weights: Mapping[str, float],
    asset_risk_specs: Mapping[str, PortfolioAssetRiskSpec],
    policy: PortfolioRiskBudgetPolicy,
    current_weights: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Assess one fully specified portfolio target without granting execution.

    Bad/missing inputs are represented as ``PARKED`` rather than throwing so a
    research driver can publish a bounded status and avoid retry loops.  A
    valid over-budget target is proportionally reduced only across non-cash
    assets; the removed allocation is directed to the declared cash asset.
    """
    try:
        specs = _asset_specs(asset_risk_specs)
        validated_policy = _policy(policy, specs=specs)
        target = _weights(target_weights, specs=specs, label="target allocation")
        current = _weights(current_weights, specs=specs, label="current allocation")
        if not target:
            _fail("empty target allocation")
        scalar = _scale_limit(
            target_weights=target,
            current_weights=current,
            specs=specs,
            policy=validated_policy,
        )
        recommended = _recommended_weights(
            target_weights=target,
            specs=specs,
            cash_symbol=validated_policy.cash_symbol,
            scalar=scalar,
        )
        metrics = _metrics(weights=recommended, current_weights=current, specs=specs)
        if scalar >= 1.0 - _EPSILON:
            status = "APPROVE"
            reason_codes: tuple[str, ...] = ()
        else:
            status = "REDUCE"
            reason_codes = ("PORTFOLIO_RISK_BUDGET_REDUCED",)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "execution_authorized": False,
            "risk_scalar": scalar,
            "reason_codes": reason_codes,
            "recommended_target_weights": recommended,
            "metrics": metrics,
        }
    except PortfolioRiskBudgetError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "PARKED",
            "execution_authorized": False,
            "risk_scalar": 0.0,
            "reason_codes": (str(exc),),
            "recommended_target_weights": {},
            "metrics": {},
        }


__all__ = [
    "SCHEMA_VERSION",
    "PortfolioAssetRiskSpec",
    "PortfolioRiskBudgetError",
    "PortfolioRiskBudgetPolicy",
    "assess_portfolio_risk_budget",
]
