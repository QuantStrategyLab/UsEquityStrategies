"""Pure plain-DCA composition over the unchanged buy-and-hold preview API.

Calendar completeness, funded carry and period records are caller attestations,
not discovered facts. This module neither claims a period nor consumes money.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal, DecimalException, ROUND_DOWN, localcontext

from quant_platform_kit.common.strategy_contracts import StrategyDecision

from .buy_and_hold import (
    Allowance, Capabilities, Mandate, PlanBinding, PortfolioFacts, Quote,
    TIER_SCALES, _aware, _decision, _fresh, _money, _text, _window,
    plan_buy_and_hold,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class ScheduleSpec:
    cadence: str = "monthly"
    anchor: int = 5  # Monthly day 1..31; weekly weekday Monday=0 .. Sunday=6.
    timezone: str = "America/New_York"
    open_delay_minutes: int = 30
    close_buffer_minutes: int = 30
    catch_up: bool = False  # Only false is supported.


@dataclass(frozen=True)
class ExchangeSession:
    day: date
    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True)
class ExchangeCalendar:
    version: str
    mic: str
    timezone: str
    coverage_start: date  # Inclusive: absent dates are confirmed closures.
    coverage_end: date
    observed_at: datetime
    valid_until: datetime
    complete: bool
    sessions: tuple[ExchangeSession, ...]
    zone: tzinfo  # Already loaded exchange timezone; evaluation never reads tzdb.


@dataclass(frozen=True)
class DcaPlan:
    mandate: Mandate
    stable_plan_id: str
    revision: int
    schedule: ScheduleSpec
    final_tranche: Decimal  # Approved B including fees, already tier-adjusted.
    carry_cap: Decimal
    gross_period_ceiling: Decimal
    reference_base: Decimal | None = None
    applied_scale: Decimal | None = None


@dataclass(frozen=True)
class PeriodRecord:
    period_key: str
    state: str  # Any existing state prevents a new preview grant for that key.


@dataclass(frozen=True)
class PeriodFacts:
    binding: PlanBinding
    stable_plan_id: str
    plan_revision: int
    nominal_period_key: str
    observed_at: datetime
    valid_until: datetime
    records: tuple[PeriodRecord, ...]
    funded_carry: Decimal  # Unreserved, backed authority; not a missed tranche.
    carry_verified: bool
    year_new_headroom: Decimal
    lifetime_new_headroom: Decimal
    year_spend_headroom: Decimal
    lifetime_spend_headroom: Decimal


@dataclass(frozen=True)
class PeriodPreview:
    status: str
    reason: str
    decision: StrategyDecision
    period_key: str | None = None
    nominal_anchor: date | None = None
    due_session: date | None = None
    expired_period_keys: tuple[str, ...] = ()
    collision_period_keys: tuple[str, ...] = ()
    new_tranche: Decimal = ZERO
    carry_in: Decimal = ZERO
    envelope: Decimal = ZERO
    maximum_spend: Decimal = ZERO
    carry_out: Decimal = ZERO
    released_authority: Decimal = ZERO
    no_order: bool = True
    no_execute: bool = True
    preview_only: bool = True
    live_ready: bool = False


def evaluate_dca_period(
    *, plan: DcaPlan, calendar: ExchangeCalendar | None, period: PeriodFacts | None,
    facts: PortfolioFacts | None, quote: Quote | None,
    capabilities: Capabilities | None, now: datetime,
) -> PeriodPreview:
    """Return a disposable latest-only preview, never an execution permission.

    Every call needs an injected fresh snapshot. A future consumer must perform
    atomic reread/claim/fencing at its order writer; repeat previews do not lock,
    persist terminal states, reconcile cash, or provide exactly-once execution.
    """
    try:
        with localcontext() as context:
            context.prec = 80
            return _evaluate(plan, calendar, period, facts, quote, capabilities, now)
    except (TypeError, ValueError, AttributeError, DecimalException, OverflowError):
        return PeriodPreview("BLOCKED", "invalid_input", _decision("BLOCKED", "invalid_input"))


def _calendar_valid(calendar, schedule, binding, now, start):
    if not (isinstance(calendar, ExchangeCalendar) and calendar.complete is True
            and _text(calendar.version) and calendar.mic == binding.instrument.mic
            and calendar.timezone == schedule.timezone
            and type(calendar.coverage_start) is date and type(calendar.coverage_end) is date
            and calendar.coverage_start <= start <= now.astimezone(calendar.zone).date()
            <= calendar.coverage_end
            and _window(calendar.observed_at, calendar.valid_until, now)
            and type(calendar.sessions) is tuple and bool(calendar.sessions)):
        return False
    zone = calendar.zone
    previous = None
    for session in calendar.sessions:
        if not (isinstance(session, ExchangeSession) and type(session.day) is date
                and calendar.coverage_start <= session.day <= calendar.coverage_end
                and (previous is None or session.day > previous)
                and _aware(session.opens_at) and _aware(session.closes_at)
                and session.opens_at.astimezone(zone).date() == session.day
                and session.closes_at.astimezone(zone).date() == session.day
                and session.opens_at.astimezone(timezone.utc)
                < session.closes_at.astimezone(timezone.utc)):
            return False
        previous = session.day
    return True


def _anchors(schedule, start, today):
    if schedule.cadence == "weekly":
        anchor = start + timedelta(days=(schedule.anchor-start.weekday()) % 7)
        while anchor <= today:
            iso = anchor.isocalendar()
            yield anchor, f"{iso.year:04d}-W{iso.week:02d}"
            anchor += timedelta(days=7)
    else:
        year, month = start.year, start.month
        while (year, month) <= (today.year, today.month):
            anchor = date(year, month, min(schedule.anchor, monthrange(year, month)[1]))
            if start <= anchor <= today:
                yield anchor, f"{year:04d}-{month:02d}"
            year, month = (year+1, 1) if month == 12 else (year, month+1)


def _evaluate(plan, calendar, period, facts, quote, capabilities, now):
    def blocked(reason):
        return PeriodPreview("BLOCKED", reason, _decision("BLOCKED", reason))

    if not (isinstance(plan, DcaPlan) and isinstance(plan.mandate, Mandate) and _aware(now)):
        return blocked("invalid_plan_or_time")
    mandate, schedule = plan.mandate, plan.schedule
    if not (isinstance(schedule, ScheduleSpec) and schedule.cadence in ("weekly", "monthly")
            and type(schedule.anchor) is int
            and (0 <= schedule.anchor <= 6 if schedule.cadence == "weekly" else 1 <= schedule.anchor <= 31)
            and schedule.catch_up is False
            and type(schedule.open_delay_minutes) is int and schedule.open_delay_minutes >= 0
            and type(schedule.close_buffer_minutes) is int and schedule.close_buffer_minutes >= 0
            and _text(plan.stable_plan_id) and type(plan.revision) is int and plan.revision > 0
            and _window(mandate.starts_at, mandate.expires_at, now)
            and all(_money(value) for value in (plan.carry_cap, plan.gross_period_ceiling))
            and _money(plan.final_tranche, positive=True)
            and plan.final_tranche == plan.final_tranche.quantize(CENT)
            and plan.carry_cap <= plan.final_tranche):
        return blocked("invalid_plan")
    if plan.reference_base is None:
        if plan.applied_scale is not None:
            return blocked("funding_policy_mismatch")
    elif not (_money(plan.reference_base, positive=True) and _money(plan.applied_scale, positive=True)
              and plan.applied_scale == TIER_SCALES.get(mandate.preference)
              and plan.final_tranche == (plan.reference_base*plan.applied_scale).quantize(CENT, rounding=ROUND_DOWN)):
        return blocked("funding_policy_mismatch")
    if not (isinstance(calendar, ExchangeCalendar) and isinstance(calendar.zone, tzinfo)
            and getattr(calendar.zone, "key", None) == schedule.timezone):
        return blocked("invalid_calendar")
    zone = calendar.zone
    start, today = mandate.starts_at.astimezone(zone).date(), now.astimezone(zone).date()
    if not _calendar_valid(calendar, schedule, mandate.binding, now, start):
        return blocked("invalid_calendar")
    # Validate P1's full binding, cash/risk, quote and capability vetoes before
    # interpreting a known shortage. No synthetic healthy replacement facts.
    probe = plan_buy_and_hold(family=mandate.binding.profile, mandate=mandate, facts=facts,
                             quote=quote, allowance=None, capabilities=capabilities, now=now)
    if probe.diagnostics["status"] == "BLOCKED":
        return blocked(probe.diagnostics["reason"])
    if not (isinstance(period, PeriodFacts) and period.binding == mandate.binding
            and period.stable_plan_id == plan.stable_plan_id
            and type(period.plan_revision) is int and period.plan_revision == plan.revision
            and _fresh(period.observed_at, period.valid_until, now)
            and period.carry_verified is True
            and type(period.records) is tuple
            and all(isinstance(record, PeriodRecord) and _text(record.period_key)
                    and record.state in ("DUE", "PREPARED", "SUBMITTING", "ACKNOWLEDGED", "PARTIAL",
                                         "FILLED", "RECONCILED_TERMINAL", "SKIPPED", "DEFERRED", "UNKNOWN")
                    for record in period.records)
            and len({r.period_key for r in period.records}) == len(period.records)
            and all(_money(value) for value in (
                period.funded_carry, period.year_new_headroom, period.lifetime_new_headroom,
                period.year_spend_headroom, period.lifetime_spend_headroom))
            and period.funded_carry <= plan.carry_cap):
        return blocked("invalid_period_facts")
    if any(r.state in ("PREPARED", "SUBMITTING", "ACKNOWLEDGED", "PARTIAL", "UNKNOWN")
           for r in period.records):
        return blocked("outstanding_period")
    due = []
    for anchor, key in _anchors(schedule, start, today):
        session = next((s for s in calendar.sessions if s.day >= anchor), None)
        if session is None:
            return blocked("calendar_rollover_uncovered")
        begins = session.opens_at.astimezone(timezone.utc) + timedelta(minutes=schedule.open_delay_minutes)
        ends = session.closes_at.astimezone(timezone.utc) - timedelta(minutes=schedule.close_buffer_minutes)
        if begins >= ends:
            return blocked("invalid_session_window")
        due.append((anchor, key, session, begins, ends))
    if not due:
        return PeriodPreview("NOT_DUE", "no_anchor",
                             _decision("HOLD", "not_due"), carry_in=period.funded_carry,
                             carry_out=period.funded_carry)
    anchor, key, session, begins, ends = due[-1]
    recorded = {record.period_key for record in period.records}
    missed = [item for item in due[:-1] if item[1] not in recorded]
    expired = tuple(item[1] for item in missed if item[2].day != session.day)
    collisions = tuple(item[1] for item in missed if item[2].day == session.day)
    details = dict(period_key=key, nominal_anchor=anchor, due_session=session.day,
                   expired_period_keys=expired, collision_period_keys=collisions,
                   carry_in=period.funded_carry, carry_out=period.funded_carry)

    def result(status, reason):
        return PeriodPreview(status, reason, _decision(status, reason), **details)

    if period.nominal_period_key != key:
        return result("BLOCKED", "period_key_mismatch")
    if any(r.period_key == key for r in period.records):
        return result("BLOCKED", "period_already_recorded")
    if now < begins:
        return result("NOT_DUE", "before_window")
    if now >= ends:
        return result("SKIPPED", "expired")
    tranche, carry = plan.final_tranche, period.funded_carry
    headroom = min(plan.gross_period_ceiling, period.year_spend_headroom,
                   period.lifetime_spend_headroom, facts.risk_headroom,
                   max(ZERO, mandate.principal_ceiling-facts.principal_spent_and_reserved))
    if min(period.year_new_headroom, period.lifetime_new_headroom, headroom) < tranche:
        return result("SKIPPED", "budget_exhausted")
    # Carry and new tranche must both remain independently backed after reserve.
    if facts.settled_cash_after_reservations-mandate.reserve < tranche+carry:
        return result("SKIPPED", "shortfall")
    envelope = min(tranche+carry, headroom)
    allowance = Allowance(mandate.binding, facts.contribution_ref, facts.allowance_revision,
                          begins, min(ends, mandate.expires_at), envelope, ZERO, ZERO)
    decision = plan_buy_and_hold(family=mandate.binding.profile, mandate=mandate, facts=facts,
                                quote=quote, allowance=allowance, capabilities=capabilities, now=now)
    if decision.diagnostics["status"] == "BLOCKED":
        return result("BLOCKED", decision.diagnostics["reason"])
    if decision.diagnostics["status"] not in ("BUY_PREVIEW", "DEFER"):
        return result("BLOCKED", "unexpected_planner_status")
    spend = Decimal(decision.diagnostics["maximum_spend"]) if decision.positions else ZERO
    # Amount excluded by H retains no additional buy authority beyond Cmax.
    unspent = tranche+carry-spend
    details["carry_out"] = min(unspent, plan.carry_cap)
    return PeriodPreview("DUE" if decision.positions else "DEFERRED",
                         "buy_preview" if decision.positions else "whole_unit_or_minimum",
                         decision, new_tranche=tranche, envelope=envelope, maximum_spend=spend,
                         released_authority=max(ZERO, unspent-plan.carry_cap), **details)
