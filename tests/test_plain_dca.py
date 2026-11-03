"""Synthetic exchange sessions and cash facts; no runtime authority."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo
import importlib

import pytest

from tests.test_buy_and_hold import case, FAMILIES

NY = ZoneInfo("America/New_York")


def fixture(family, day=date(2026, 11, 3), *, anchor=3, cadence="monthly", holidays=()):
    api = importlib.import_module("us_equity_strategies.plain_dca")
    _, p1 = case(family)
    now = datetime.combine(day, time(11), NY)
    start, end = date(day.year, 1, 1), date(day.year+1, 2, 1)
    sessions = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in holidays:
            sessions.append(api.ExchangeSession(cursor, datetime.combine(cursor, time(9, 30), NY),
                                                datetime.combine(cursor, time(16), NY)))
        cursor += timedelta(days=1)
    mandate = replace(p1["mandate"], starts_at=datetime.combine(start, time(), NY),
                      expires_at=datetime.combine(end, time(), NY), reserve=D("100"))
    schedule = api.ScheduleSpec(cadence=cadence, anchor=anchor)
    plan = api.DcaPlan(mandate, "synthetic-plan", 1, schedule, D("100"), D("100"), D("200"))
    calendar = api.ExchangeCalendar("synthetic-calendar-v1", "XNAS", "America/New_York",
                                    start, end, now-timedelta(days=1), now+timedelta(days=1),
                                    True, tuple(sessions), NY)
    key = day.strftime("%Y-%m") if cadence == "monthly" else f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
    period = api.PeriodFacts(mandate.binding, "synthetic-plan", 1, key, now,
                            now+timedelta(minutes=1), (), D("0"), True,
                            D("1000"), D("1000"), D("1000"), D("1000"))
    facts = replace(p1["facts"], observed_at=now, valid_until=now+timedelta(minutes=1))
    quote = replace(p1["quote"], observed_at=now, valid_until=now+timedelta(minutes=1),
                    mark_price=D("70"), limit_price=D("70"), fee_buffer=D("0"))
    return api, dict(plan=plan, calendar=calendar, period=period, facts=facts,
                     quote=quote, capabilities=p1["capabilities"], now=now)


def run(args):
    return importlib.import_module("us_equity_strategies.plain_dca").evaluate_dca_period(**args)


def safe(result):
    assert result.decision.budgets == ()
    assert {"no_order", "no_execute", "preview_only"} <= set(result.decision.risk_flags)
    assert result.decision.diagnostics["live_ready"] is False
    assert result.preview_only and result.no_order and result.no_execute and not result.live_ready


@pytest.mark.parametrize("family", FAMILIES)
def test_plain_tranche_carry_and_p1_contract(family):
    _, args = fixture(family)
    result = run(args)
    safe(result)
    assert result.status == "DUE"
    assert result.period_key == "2026-11"
    assert result.new_tranche == D("100") and result.maximum_spend == D("70")
    assert result.carry_out == D("30") and result.envelope == D("100")
    assert result.decision.positions[0].symbol == args["plan"].mandate.binding.instrument.symbol
    assert result.decision.positions[0].target_value > 700
    with pytest.raises(FrozenInstanceError):
        args["period"].funded_carry = D("200")


@pytest.mark.parametrize("family", FAMILIES)
def test_fail_closed_missing_invalid_calendar(family):
    _, args = fixture(family)
    for calendar in (None, replace(args["calendar"], complete=False),
                     replace(args["calendar"], sessions=()),
                     replace(args["calendar"], timezone="invalid"),
                     replace(args["calendar"], version=""),
                     replace(args["calendar"], mic="XNYS"),
                     replace(args["calendar"], valid_until=args["now"]),
                     replace(args["calendar"], sessions=args["calendar"].sessions[::-1])):
        result = run({**args, "calendar": calendar})
        safe(result)
        assert result.status == "BLOCKED" and not result.decision.positions


@pytest.mark.parametrize("family", FAMILIES)
def test_shortfall_skips_whole_tranche_not_partial_or_carry_only(family):
    _, args = fixture(family)
    args["period"] = replace(args["period"], funded_carry=D("30"))
    args["facts"] = replace(args["facts"], settled_cash_after_reservations=D("229"))
    result = run(args)
    assert result.status == "SKIPPED" and result.reason == "shortfall"
    assert result.new_tranche == result.maximum_spend == 0
    assert result.carry_out == D("30") and not result.decision.positions


@pytest.mark.parametrize("family", FAMILIES)
def test_replay_stale_revision_and_unknown_sources_block(family):
    api, args = fixture(family)
    for patch in ({"records": (api.PeriodRecord("2026-11", "SKIPPED"),)},
                  {"records": (api.PeriodRecord("2026-11", "PREPARED"),)},
                  {"observed_at": args["now"]-timedelta(minutes=6)},
                  {"plan_revision": 2}, {"nominal_period_key": "2026-10"},
                  {"carry_verified": False}, {"funded_carry": D("101")},
                  {"year_spend_headroom": None}):
        result = run({**args, "period": replace(args["period"], **patch)})
        safe(result)
        assert result.status == "BLOCKED" and not result.decision.positions


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("day,anchor,cadence,holidays,nominal,due,key", [
    (date(2026, 11, 2), 31, "monthly", (), date(2026, 10, 31), date(2026, 11, 2), "2026-10"),
    (date(2026, 3, 2), 31, "monthly", (), date(2026, 2, 28), date(2026, 3, 2), "2026-02"),
    (date(2026, 7, 6), 3, "monthly", (date(2026, 7, 3),), date(2026, 7, 3), date(2026, 7, 6), "2026-07"),
    (date(2026, 11, 2), 6, "weekly", (), date(2026, 11, 1), date(2026, 11, 2), "2026-W44"),
    (date(2026, 3, 9), 6, "weekly", (), date(2026, 3, 8), date(2026, 3, 9), "2026-W10"),
    (date(2027, 1, 4), 31, "monthly", (date(2026, 12, 31), date(2027, 1, 1)),
     date(2026, 12, 31), date(2027, 1, 4), "2026-12"),
])
def test_nominal_identity_month_end_dst_holidays_and_year_rollover(family, day, anchor, cadence,
                                                                  holidays, nominal, due, key):
    api, args = fixture(family, day, anchor=anchor, cadence=cadence, holidays=holidays)
    if day.year == 2027:
        # Include the original nominal period and its confirmed closed dates.
        _, old = fixture(family, date(2026, 12, 30), anchor=anchor, holidays=holidays)
        args["plan"] = replace(args["plan"], mandate=replace(args["plan"].mandate,
                               starts_at=old["plan"].mandate.starts_at))
        args["calendar"] = replace(args["calendar"], coverage_start=old["calendar"].coverage_start,
                                    sessions=old["calendar"].sessions)
    args["period"] = replace(args["period"], nominal_period_key=key)
    result = run(args)
    safe(result)
    assert result.status == "DUE"
    assert (result.nominal_anchor, result.due_session, result.period_key) == (nominal, due, key)


@pytest.mark.parametrize("family", FAMILIES)
def test_no_catch_up_and_no_due_before_window(family):
    _, args = fixture(family, date(2026, 11, 4))
    result = run(args)
    assert result.status == "SKIPPED" and result.reason == "expired"
    assert result.new_tranche == result.maximum_spend == 0
    # Before today's window: old missed periods are never bought.
    _, args = fixture(family)
    now = args["now"].replace(hour=9)
    args["now"] = now
    args["period"] = replace(args["period"], observed_at=now, valid_until=now+timedelta(minutes=1))
    args["facts"] = replace(args["facts"], observed_at=now, valid_until=now+timedelta(minutes=1))
    args["quote"] = replace(args["quote"], observed_at=now, valid_until=now+timedelta(minutes=1))
    result = run(args)
    assert result.status == "NOT_DUE" and not result.decision.positions
    assert result.new_tranche == 0


@pytest.mark.parametrize("family", FAMILIES)
def test_latest_only_collision_does_not_sum_missed_tranches(family):
    _, args = fixture(family, date(2026, 11, 3), cadence="weekly", anchor=1)
    calendar = args["calendar"]
    args["calendar"] = replace(calendar, sessions=tuple(s for s in calendar.sessions
                               if not date(2026, 10, 20) <= s.day < date(2026, 11, 3)))
    result = run(args)
    assert result.status == "DUE" and result.period_key == "2026-W45"
    assert result.collision_period_keys == ("2026-W43", "2026-W44")
    assert result.new_tranche == D("100") and result.envelope == D("100")


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("preference,scale", [("CAPITAL_PRESERVATION", D("0.5")),
                                              ("BALANCED_COMPOUNDING", D("0.75")),
                                              ("GROWTH_COMPOUNDING", D("1"))])
def test_tier_is_validated_once_and_carry_not_scaled(family, preference, scale):
    _, args = fixture(family)
    args["plan"] = replace(args["plan"], mandate=replace(args["plan"].mandate, preference=preference),
                            final_tranche=D("200")*scale, reference_base=D("200"), applied_scale=scale)
    args["period"] = replace(args["period"], funded_carry=D("40"))
    result = run(args)
    assert result.new_tranche == D("200")*scale
    assert result.envelope == min(D("200")*scale+D("40"), D("200"))
    assert result.maximum_spend <= result.envelope
    bad = replace(args["plan"], final_tranche=args["plan"].final_tranche-D("1"), carry_cap=D("0"))
    assert run({**args, "plan": bad}).reason == "funding_policy_mismatch"


@pytest.mark.parametrize("family", FAMILIES)
def test_whole_fraction_fees_caps_and_carry_release(family):
    _, args = fixture(family)
    args["quote"] = replace(args["quote"], mark_price=D("250"), limit_price=D("250"))
    args["period"] = replace(args["period"], funded_carry=D("80"))
    result = run(args)
    assert result.status == "DEFERRED" and result.maximum_spend == 0
    assert result.carry_out == D("100") and result.released_authority == D("80")
    # Fractional path retains the exact binding at every P1 boundary.
    binding = replace(args["plan"].mandate.binding, instrument=replace(
        args["plan"].mandate.binding.instrument, quantity_step=D("0.01")))
    args["plan"] = replace(args["plan"], mandate=replace(args["plan"].mandate, binding=binding))
    args["facts"] = replace(args["facts"], binding=binding)
    args["period"] = replace(args["period"], binding=binding)
    args["capabilities"] = replace(args["capabilities"], binding=binding, fractional_verified=True)
    args["quote"] = replace(args["quote"], instrument=binding.instrument, fee_buffer=D("1"))
    result = run(args)
    assert result.status == "DUE" and result.maximum_spend == D("178.5")
    assert D(result.decision.diagnostics["quantity"]) == D("0.71")
    assert result.carry_out == D("1.5")
    assert run({**args, "capabilities": replace(args["capabilities"], fractional_verified=False)}).status == "BLOCKED"
    for field in ("year_new_headroom", "lifetime_new_headroom", "year_spend_headroom", "lifetime_spend_headroom"):
        result = run({**args, "period": replace(args["period"], **{field: D("99")})})
        assert result.reason == "budget_exhausted" and result.new_tranche == 0


@pytest.mark.parametrize("family", FAMILIES)
def test_half_day_close_and_exact_window_end(family):
    _, args = fixture(family)
    args["calendar"] = replace(args["calendar"], sessions=tuple(
        replace(s, closes_at=s.closes_at.replace(hour=13)) if s.day == date(2026, 11, 3) else s
        for s in args["calendar"].sessions))
    assert run(args).status == "DUE"
    now = args["now"].replace(hour=12, minute=30)
    args["now"] = now
    for field in ("period", "facts", "quote"):
        args[field] = replace(args[field], observed_at=now, valid_until=now+timedelta(minutes=1))
    assert run(args).reason == "expired"


@pytest.mark.parametrize("family", FAMILIES)
def test_no_sell_and_no_identifier_echo_or_io(family, monkeypatch, capsys):
    _, args = fixture(family)
    import builtins
    import socket
    def forbidden(*a, **kw):
        raise AssertionError("unexpected I/O")
    # Warm timezone cache before blocking filesystem access.
    run(args)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    for price in (D("10"), D("1000")):
        quote = replace(args["quote"], mark_price=price, limit_price=price)
        result = run({**args, "quote": quote})
        safe(result)
        assert all(p.target_value >= float(args["facts"].held_quantity*price)
                   for p in result.decision.positions)
    private = "synthetic-private-value"
    result = run({**args, "calendar": replace(args["calendar"], version=private),
                  "period": replace(args["period"], stable_plan_id=private)})
    assert result.status == "BLOCKED" and private not in repr(result)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("family", FAMILIES)
def test_calendar_integrity_and_rollover_coverage(family):
    _, args = fixture(family, date(2026, 11, 1), anchor=31)
    calendar = args["calendar"]
    closed_tail = replace(calendar, coverage_end=date(2026, 11, 1),
                          sessions=tuple(s for s in calendar.sessions if s.day <= date(2026, 11, 1)))
    result = run({**args, "calendar": closed_tail})
    assert result.status == "BLOCKED" and result.reason == "calendar_rollover_uncovered"
    for bad in (replace(calendar, coverage_start=date(2026, 2, 1)),
                replace(calendar, sessions=(calendar.sessions[0],)*2),
                replace(calendar, sessions=(replace(calendar.sessions[0],
                        opens_at=calendar.sessions[0].opens_at.replace(tzinfo=None)),)),
                replace(calendar, sessions=list(calendar.sessions)),
                replace(calendar, observed_at=args["now"]+timedelta(seconds=1))):
        assert run({**args, "calendar": bad}).status == "BLOCKED"


@pytest.mark.parametrize("family", FAMILIES)
def test_invalid_plan_amounts_and_unknown_cash_are_not_shortfall(family):
    _, args = fixture(family)
    for bad in (replace(args["plan"], final_tranche=D("NaN")),
                replace(args["plan"], final_tranche=100.0),
                replace(args["plan"], final_tranche=D("100.001")),
                replace(args["plan"], carry_cap=D("101")),
                replace(args["plan"], schedule=replace(args["plan"].schedule, catch_up=True)),
                replace(args["plan"], schedule=replace(args["plan"].schedule, anchor=0)),
                replace(args["plan"], schedule=replace(args["plan"].schedule, anchor=True))):
        assert run({**args, "plan": bad}).status == "BLOCKED"
    for patch in ({"settled_cash_after_reservations": None}, {"risk_approved": None},
                  {"reconciled": False}, {"observed_at": args["now"]-timedelta(minutes=6)}):
        result = run({**args, "facts": replace(args["facts"], **patch)})
        assert result.status == "BLOCKED" and result.new_tranche == 0


@pytest.mark.parametrize("family", FAMILIES)
def test_repeated_previews_do_not_consume_and_new_snapshot_controls_result(family):
    api, args = fixture(family)
    first = run(args)
    assert run(args) == first
    changed = replace(args["period"], records=(api.PeriodRecord(first.period_key, "DEFERRED"),))
    assert run({**args, "period": changed}).reason == "period_already_recorded"
    changed = replace(args["period"], year_spend_headroom=D("0"))
    assert run({**args, "period": changed}).reason == "budget_exhausted"
    assert run(args) == first  # No ambient cache or hidden mutation.


@pytest.mark.parametrize("family", FAMILIES)
def test_completed_periods_are_not_reported_as_missed(family):
    api, args = fixture(family)
    args["period"] = replace(args["period"], records=(api.PeriodRecord("2026-10", "FILLED"),))
    result = run(args)
    assert result.status == "DUE"
    assert "2026-10" not in result.expired_period_keys


@pytest.mark.parametrize("family", FAMILIES)
def test_evaluation_does_not_load_ambient_timezone_database(family, monkeypatch):
    api, args = fixture(family)
    def forbidden(*a, **kw):
        raise AssertionError("ambient timezone database lookup")
    monkeypatch.setattr(api, "ZoneInfo", forbidden, raising=False)
    assert run(args).status == "DUE"
