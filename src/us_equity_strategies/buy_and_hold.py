"""Pure, buy-only baseline previews. Nothing here grants execution authority."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException, localcontext
from types import MappingProxyType

from quant_platform_kit.common.strategy_contracts import (
    CallableStrategyEntrypoint, PositionTarget, StrategyContext, StrategyDecision,
    StrategyManifest,
)


@dataclass(frozen=True)
class Family:
    display_name: str
    reference_symbol: str
    # Candidate -> current proposed symbol. This is metadata, not a broker lookup.
    candidates: Mapping[str, str]


NASDAQ100_BUY_AND_HOLD_PROFILE = "nasdaq100_buy_and_hold"
SP500_BUY_AND_HOLD_PROFILE = "sp500_buy_and_hold"

FAMILIES = MappingProxyType({
    NASDAQ100_BUY_AND_HOLD_PROFILE: Family(
        "Nasdaq-100 Buy and Hold", "QQQ",
        MappingProxyType({"QQQ": "QQQ", "QQQM": "QQQM", "QNDX": "QNDX"}),
    ),
    SP500_BUY_AND_HOLD_PROFILE: Family(
        "S&P 500 Buy and Hold", "SPY",
        MappingProxyType({"SPY": "SPY", "VOO": "VOO", "SPLG": "SPYM"}),
    ),
})
FUNDING_POLICY = "buy_and_hold_funding.v1"
TIER_SCALES = MappingProxyType({
    "CAPITAL_PRESERVATION": Decimal("0.50"),
    "BALANCED_COMPOUNDING": Decimal("0.75"),
    "GROWTH_COMPOUNDING": Decimal("1.00"),
})
REQUIRED_INPUTS = frozenset({"portfolio_snapshot"})


@dataclass(frozen=True)
class InstrumentBinding:
    candidate: str
    symbol: str
    identity: str  # Verified ISIN or exact broker contract reference, never ticker alone.
    mic: str
    currency: str
    quantity_step: Decimal
    minimum_notional: Decimal
    verified: bool
    alias_verified: bool = False
    product_type: str = "cash_equity_etf"
    leverage: Decimal = Decimal("1")


@dataclass(frozen=True)
class PlanBinding:
    profile: str
    instrument: InstrumentBinding
    account_ref: str
    mandate_revision: str
    release: str
    owner_ref: str
    environment: str


@dataclass(frozen=True)
class Mandate:
    binding: PlanBinding
    starts_at: datetime
    expires_at: datetime
    reserve: Decimal
    principal_ceiling: Decimal
    preference: str
    active: bool
    dividend_policy: str = "cash"


@dataclass(frozen=True)
class Allowance:
    binding: PlanBinding
    contribution_ref: str
    revision: int  # Caller-owned durable allowance-state revision, not a local counter.
    starts_at: datetime
    expires_at: datetime
    final_amount: Decimal
    spent: Decimal  # Reconciled fill cost including actual fees.
    reserved: Decimal  # Live AND uncertain reservations, counted once.
    base_amount: Decimal | None = None  # None means directly approved final dollars.
    applied_scale: Decimal | None = None  # Stored once with a derived allowance.
    policy_version: str = FUNDING_POLICY


@dataclass(frozen=True)
class PortfolioFacts:
    binding: PlanBinding
    observed_at: datetime
    valid_until: datetime
    reconciled: bool
    exclusive_ownership: bool
    unrelated_exposure: bool
    holdings_adopted: bool
    held_quantity: Decimal
    # Already net of settled debits and deduplicated outstanding reservations.
    settled_cash_after_reservations: Decimal
    account_risk_capital: Decimal  # Independent risk denominator; never sizing equity.
    risk_headroom: Decimal  # Final spend headroom after all mandatory risk vetoes.
    risk_approved: bool
    principal_spent_and_reserved: Decimal
    contribution_ref: str | None
    allowance_revision: int | None


@dataclass(frozen=True)
class Quote:
    instrument: InstrumentBinding
    observed_at: datetime
    valid_until: datetime
    mark_price: Decimal
    limit_price: Decimal
    fee_buffer: Decimal  # Upper bound for the proposed increment, not historical fees.


@dataclass(frozen=True)
class Capabilities:
    binding: PlanBinding
    verified: bool  # Exact instrument/account/API qualification, provided by caller.
    fractional_verified: bool = False


def _money(value: object, *, positive: bool = False) -> bool:
    # Bound scale/precision so arithmetic remains exact within the local context.
    return (isinstance(value, Decimal) and value.is_finite()
            and (value > 0 if positive else value >= 0)
            and value <= Decimal("1e18") and value.as_tuple().exponent >= -12)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware(value: object) -> bool:
    return isinstance(value, _DATETIME_TYPE) and value.utcoffset() is not None


# Stable type check even when tests replace the ambient clock class.
_DATETIME_TYPE = datetime


def _window(start: object, end: object, now: datetime) -> bool:
    return _aware(start) and _aware(end) and start <= now < end


def _fresh(start: object, end: object, now: datetime) -> bool:
    return _window(start, end, now) and now-start <= timedelta(minutes=5)


def _decision(status: str, reason: str, **details: object) -> StrategyDecision:
    # Only static reason codes, known symbols and numeric results cross this boundary.
    # No input identities, object reprs, exception text or logging need redaction.
    return StrategyDecision(
        risk_flags=("no_execute", "no_order", "preview_only"),
        diagnostics={"status": status, "reason": reason, "no_order": True,
                     "live_ready": False, **details},
    )


def _valid_binding(binding: object, family: str) -> bool:
    if not isinstance(binding, PlanBinding) or binding.profile != family:
        return False
    if not all(_text(value) for value in (
        binding.account_ref, binding.mandate_revision, binding.release, binding.owner_ref,
    )) or binding.environment not in ("offline_preview", "paper"):
        return False
    instrument = binding.instrument
    if not isinstance(instrument, InstrumentBinding) or not isinstance(instrument.candidate, str):
        return False
    symbol = FAMILIES[family].candidates.get(instrument.candidate)
    if symbol is None or instrument.symbol != symbol:
        return False
    if instrument.candidate == "SPLG" and instrument.alias_verified is not True:
        return False
    if not (_text(instrument.identity) and _text(instrument.mic)
            and len(instrument.mic) == 4 and instrument.mic.isascii()
            and instrument.mic.isalnum() and instrument.mic.isupper()
            and instrument.currency == "USD" and instrument.verified is True
            and instrument.product_type == "cash_equity_etf"
            and _money(instrument.leverage, positive=True) and instrument.leverage == 1
            and _money(instrument.quantity_step, positive=True)
            and _money(instrument.minimum_notional, positive=True)):
        return False
    step = instrument.quantity_step
    # Whole lots are integer; fractional steps must divide a whole share exactly.
    return step == step.to_integral_value() if step >= 1 else Decimal("1") % step == 0


def plan_buy_and_hold(
    *, family: str, mandate: Mandate | None, facts: PortfolioFacts | None,
    quote: Quote | None, allowance: Allowance | None, capabilities: Capabilities | None,
    now: datetime,
) -> StrategyDecision:
    """Build one disposable preview from explicit facts; never reserve or submit.

    Repeat calls do not consume an allowance. Consumers MUST re-read state and
    reserve atomically before any future execution; these targets are not orders.
    """
    try:
        with localcontext() as ctx:
            ctx.prec = 80
            return _plan(family, mandate, facts, quote, allowance, capabilities, now)
    except (TypeError, ValueError, AttributeError, DecimalException, OverflowError):
        # Malformed typed facts remain blocked, with no user-controlled error text.
        return _decision("BLOCKED", "invalid_input")


def _plan(family, mandate, facts, quote, allowance, capabilities, now):
    if not isinstance(family, str) or family not in FAMILIES or not _aware(now):
        return _decision("BLOCKED", "invalid_family_or_time")
    if not (isinstance(mandate, Mandate) and isinstance(facts, PortfolioFacts)
            and isinstance(quote, Quote) and isinstance(capabilities, Capabilities)):
        return _decision("BLOCKED", "missing_facts")
    binding = mandate.binding
    if not _valid_binding(binding, family) or not all(
        obj.binding == binding for obj in (facts, capabilities)
    ) or quote.instrument != binding.instrument:
        return _decision("BLOCKED", "binding_mismatch")
    if not (mandate.active is True and mandate.dividend_policy == "cash"
            and isinstance(mandate.preference, str) and mandate.preference in TIER_SCALES
            and _window(mandate.starts_at, mandate.expires_at, now)
            and _money(mandate.reserve) and _money(mandate.principal_ceiling, positive=True)):
        return _decision("BLOCKED", "invalid_mandate")
    if not (facts.reconciled is True and facts.exclusive_ownership is True
            and facts.unrelated_exposure is False and facts.holdings_adopted is True
            and facts.risk_approved is True
            and _fresh(facts.observed_at, facts.valid_until, now)
            and _fresh(quote.observed_at, quote.valid_until, now)
            and capabilities.verified is True
            and type(capabilities.fractional_verified) is bool):
        return _decision("BLOCKED", "unqualified_facts")
    if not (all(_money(value) for value in (
        facts.held_quantity, facts.settled_cash_after_reservations,
        facts.risk_headroom, facts.principal_spent_and_reserved, quote.fee_buffer,
    )) and _money(facts.account_risk_capital, positive=True)
            and _money(quote.mark_price, positive=True)
            and _money(quote.limit_price, positive=True)
            and quote.limit_price >= quote.mark_price):
        return _decision("BLOCKED", "invalid_amounts")
    step = binding.instrument.quantity_step
    if step < 1 and capabilities.fractional_verified is not True:
        return _decision("BLOCKED", "fractional_capability_unverified")
    if allowance is None:
        return _decision("HOLD", "no_allowance")
    if not (isinstance(allowance, Allowance) and allowance.binding == binding
            and _text(allowance.contribution_ref)
            and type(allowance.revision) is int and allowance.revision > 0
            and type(facts.allowance_revision) is int
            and allowance.revision == facts.allowance_revision
            and allowance.contribution_ref == facts.contribution_ref
            and allowance.policy_version == FUNDING_POLICY
            and _window(allowance.starts_at, allowance.expires_at, now)
            and all(_money(value) for value in (
                allowance.final_amount, allowance.spent, allowance.reserved,
            ))):
        return _decision("BLOCKED", "invalid_allowance")
    if allowance.base_amount is None:
        funding_valid = allowance.applied_scale is None
    else:
        funding_valid = (
            _money(allowance.base_amount) and _money(allowance.applied_scale, positive=True)
            and allowance.applied_scale == TIER_SCALES[mandate.preference]
            and allowance.final_amount == allowance.base_amount * allowance.applied_scale
        )
    if not funding_valid:
        return _decision("BLOCKED", "funding_policy_mismatch")
    if facts.principal_spent_and_reserved < allowance.spent + allowance.reserved:
        return _decision("BLOCKED", "inconsistent_principal")
    remaining = allowance.final_amount - allowance.spent - allowance.reserved
    if remaining < 0:
        return _decision("BLOCKED", "allowance_overdrawn")
    if remaining == 0:
        return _decision("HOLD", "allowance_consumed_or_reserved")
    eligible = min(
        remaining,
        max(Decimal("0"), facts.settled_cash_after_reservations - mandate.reserve),
        facts.risk_headroom,
        max(Decimal("0"), mandate.principal_ceiling - facts.principal_spent_and_reserved),
    )
    quantity = max(Decimal("0"), eligible - quote.fee_buffer) // (quote.limit_price * step) * step
    notional = quantity * quote.limit_price
    if quantity == 0 or notional < binding.instrument.minimum_notional:
        return _decision("DEFER", "insufficient_unit_or_headroom")
    current_value = facts.held_quantity * quote.mark_price
    target_value = float(current_value + notional)
    if target_value <= float(current_value):
        return _decision("BLOCKED", "preview_precision")
    decision = _decision(
        "BUY_PREVIEW", "approved_increment", family=family,
        symbol=binding.instrument.symbol, quantity=str(quantity),
        incremental_notional=str(notional), maximum_spend=str(notional + quote.fee_buffer),
        remaining_allowance=str(remaining), funding_policy=FUNDING_POLICY,
    )
    return StrategyDecision(
        positions=(PositionTarget(symbol=binding.instrument.symbol, target_value=target_value,
                                  role="buy_and_hold_preview", order_preference="limit"),),
        risk_flags=decision.risk_flags, diagnostics=decision.diagnostics,
    )


MANIFESTS = MappingProxyType({
    profile: StrategyManifest(
        profile=profile, domain="us_equity", display_name=family.display_name,
        description="Cash-only, selected-instrument buy-and-hold preview; no execution authority.",
        required_inputs=REQUIRED_INPUTS,
        default_config=MappingProxyType({
            "selected_instrument": None, "budgets": (), "preview_only": True,
            "dividend_policy": "cash", "funding_policy": FUNDING_POLICY,
        }),
    ) for profile, family in FAMILIES.items()
})


def _evaluate(profile: str, ctx: StrategyContext) -> StrategyDecision:
    if not isinstance(ctx, StrategyContext) or not all(
        isinstance(value, Mapping) for value in (ctx.state, ctx.market_data, ctx.capabilities)
    ):
        return _decision("BLOCKED", "missing_preview_inputs")
    return plan_buy_and_hold(
        family=profile, mandate=ctx.state.get("mandate"), allowance=ctx.state.get("allowance"),
        facts=ctx.portfolio, quote=ctx.market_data.get("quote"),
        capabilities=ctx.capabilities.get("buy_and_hold"), now=ctx.as_of,
    )


nasdaq100_buy_and_hold_entrypoint = CallableStrategyEntrypoint(
    MANIFESTS["nasdaq100_buy_and_hold"], lambda ctx: _evaluate("nasdaq100_buy_and_hold", ctx),
)
sp500_buy_and_hold_entrypoint = CallableStrategyEntrypoint(
    MANIFESTS["sp500_buy_and_hold"], lambda ctx: _evaluate("sp500_buy_and_hold", ctx),
)
