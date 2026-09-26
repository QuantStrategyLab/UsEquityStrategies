"""Research-only projections of existing strategy decisions for portfolio study.

An adapter preserves a builder's targets and status.  It never runs the
builder, chooses a member budget, or grants execution authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from quant_platform_kit.common.strategy_contracts import StrategyDecision

from us_equity_strategies.catalog import get_strategy_definition
GUARD_CANDIDATE = "tqqq_qqq_guard_cash_research_v1"


@dataclass(frozen=True)
class PortfolioCandidateDecision:
    candidate_id: str
    profile: str | None
    decision_date: date
    member_budget_usd: float
    required_inputs: frozenset[str]
    target_mode: str
    targets: tuple[tuple[str, float], ...]
    internal_state: Mapping[str, object]
    risk_flags: tuple[str, ...]
    evidence_status: str
    evidence_digest: str | None
    cost_model_id: str | None
    historical_return_eligible: bool = False
    research_only: bool = True


@dataclass(frozen=True)
class BuyHoldReference:
    reference_id: str
    symbol: str
    decision_date: date
    evidence_digest: str | None
    portfolio_member: bool = False
    research_only: bool = True


def _budget(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("member budget must be finite and nonnegative")
    return float(value)


def _evidence(status: str, digest: str | None) -> None:
    if status not in {"unvalidated", "development"}:
        raise ValueError("unsupported evidence status")
    if digest is not None and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("evidence digest must be SHA-256")


def adapt_catalog_decision(
    *, candidate_id: str, profile: str, decision_date: date,
    member_budget_usd: float, decision: StrategyDecision,
    evidence_status: str = "unvalidated", evidence_digest: str | None = None,
    cost_model_id: str | None = None,
) -> PortfolioCandidateDecision:
    """Project an existing UES catalog decision without changing target units."""
    if not isinstance(candidate_id, str) or not candidate_id or not isinstance(decision_date, date):
        raise ValueError("candidate identity and decision date required")
    if not isinstance(decision, StrategyDecision):
        raise TypeError("QPK StrategyDecision required")
    definition = get_strategy_definition(profile)
    if profile not in {"tqqq_growth_income", "soxl_soxx_trend_income"}:
        raise ValueError("profile is outside this research adapter's member scope")
    _evidence(evidence_status, evidence_digest)
    budget = _budget(member_budget_usd)
    target_mode = definition.target_mode
    targets: list[tuple[str, float]] = []
    for position in decision.positions:
        value = position.target_value if target_mode == "value" else position.target_weight
        if value is None or not math.isfinite(value) or value < 0:
            raise ValueError("decision target does not match catalog target mode")
        targets.append((position.symbol, float(value)))
    return PortfolioCandidateDecision(
        candidate_id=candidate_id, profile=profile, decision_date=decision_date,
        member_budget_usd=budget, required_inputs=definition.required_inputs,
        target_mode=target_mode, targets=tuple(targets),
        internal_state=decision.diagnostics, risk_flags=decision.risk_flags,
        evidence_status=evidence_status, evidence_digest=evidence_digest,
        cost_model_id=cost_model_id,
    )


def adapt_guard_research_decision(
    *, decision: Mapping[str, object], evidence_digest: str | None = None,
    cost_model_id: str | None = None,
) -> PortfolioCandidateDecision:
    """Project the frozen independent TQQQ research core's own daily result."""
    _evidence("development", evidence_digest)
    signal_date = date.fromisoformat(str(decision["signal_date"]))
    budget = _budget(decision["member_budget_usd"])
    target = _budget(decision["target_tqqq_usd"])
    if target > budget + 1e-8:
        raise ValueError("TQQQ target exceeds member budget")
    return PortfolioCandidateDecision(
        candidate_id=GUARD_CANDIDATE, profile=None, decision_date=signal_date,
        member_budget_usd=budget,
        required_inputs=frozenset({"QQQ raw close history", "TQQQ raw execution prices", "TQQQ corporate actions"}),
        target_mode="value", targets=(("TQQQ", target),),
        internal_state={"guard_route": decision["guard_route"], "applied_route": decision["applied_route"],
                        "core_state": decision["core_state"], "volatility_applied": decision["volatility_applied"]},
        risk_flags=(), evidence_status="development", evidence_digest=evidence_digest,
        cost_model_id=cost_model_id,
    )


def adapt_buy_hold_reference(
    *, symbol: str, decision_date: date, evidence_digest: str | None = None,
) -> BuyHoldReference:
    """Identify a published study baseline without making the ETF a member."""
    if symbol not in {"QQQM", "BOXX"} or not isinstance(decision_date, date):
        raise ValueError("unsupported buy-and-hold reference")
    _evidence("development", evidence_digest)
    return BuyHoldReference(
        reference_id=f"{symbol.lower()}_buy_hold_reference",
        symbol=symbol, decision_date=decision_date, evidence_digest=evidence_digest,
    )
