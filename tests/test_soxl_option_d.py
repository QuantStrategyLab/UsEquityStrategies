"""Synthetic SOXL Option D contract tests; never instantiate a broker."""
from dataclasses import replace
from functools import lru_cache

import pytest
from quant_platform_kit.common import StrategyRiskStateIdentity, StrategyRiskStateStore

from us_equity_strategies.research.soxl_option_d import (
    HaltEvidence,
    OptionDBinding,
    SessionFacts,
    advance_option_d,
    freeze_xnys_calendar,
    signed_daily_loss,
)


@lru_cache
def binding(start="2026-10-01", end="2026-11-30"):
    return OptionDBinding(
        StrategyRiskStateIdentity("soxl-option-d", "research-only", "synthetic", "a" * 64),
        "b" * 64, "c" * 64, freeze_xnys_calendar(start, end),
    )


def facts(day, opening=2000, closing=2000, **kwargs):
    return SessionFacts(
        session=day, equity_open=opening, equity_end=closing,
        deposits=0, withdrawals=0, cumulative_net_flows=0,
        marks_complete=True, ledger_complete=True, flows_verified=True,
        daily_breach_observed=False, caps_ok=True, caps_restorable=True,
        **kwargs,
    )


def evidence(day, equity=1700, loss=300):
    return HaltEvidence(day, equity, loss, "2026-10-01", day, "d" * 64, 0)


def chain(rows, b=None):
    b = b or binding()
    result = []
    for row in rows:
        result.append(advance_option_d(binding=b, facts=row, previous_transition=result[-1] if result else None))
    return result


def loss_cluster():
    return [facts("2026-10-01", 2000, 1900), facts("2026-10-02", 1900, 1800),
            facts("2026-10-05", 1800, 1700, halt_evidence=evidence("2026-10-05"))]


@pytest.mark.parametrize("end,deposit,withdrawal,expected", [(2100,100,0,0),(1900,0,100,0),(1980,100,0,120),(2020,0,0,-20)])
def test_signed_loss_excludes_cash_moves(end, deposit, withdrawal, expected):
    assert signed_daily_loss(2000, end, deposit, withdrawal) == expected


def test_latch_counter_independent_and_half_recovery_boundary():
    rows = loss_cluster() + [facts("2026-10-06",1700,1800), facts("2026-10-07",1800,1849),
                            facts("2026-10-08",1849,1850), facts("2026-10-09",1850,1850)]
    states = [t.state for t in chain(rows)]
    assert states[2]["step"] == "S0"
    assert states[3]["loss_streak"] == 0 and states[3]["step"] == "S0"
    assert states[4]["gates"]["G1"] and states[4]["gates"]["G2"]
    assert not states[4]["gates"]["G6"] and states[4]["step"] == "S0"
    assert states[5]["step"] == "S1" and states[5]["research_budget_fraction"] == .5
    assert states[6]["step"] == "S2" and states[6]["research_budget_fraction"] == 1
    assert all(s["no_order"] and not s["execution_authorized"] for s in states)


def test_deposit_cannot_fake_recovery():
    rows = loss_cluster() + [facts("2026-10-06",1700,1800),
                            replace(facts("2026-10-07",1800,1900), deposits=100, cumulative_net_flows=100)]
    s = chain(rows)[-1].state
    assert not s["gates"]["G6"] and s["step"] == "S0"


def test_s1_loss_restarts_cooldown_with_new_trigger_reference():
    rows = loss_cluster() + [facts("2026-10-06",1700,1850), facts("2026-10-07",1850,1850),
        facts("2026-10-08",1850,1840,halt_evidence=evidence("2026-10-08",1840,10)),
        facts("2026-10-09",1840,1850), facts("2026-10-12",1850,1850)]
    states = [t.state for t in chain(rows)]
    assert states[4]["step"] == "S1"
    assert states[5]["step"] == "S0" and states[5]["completed_since_halt"] == 0
    assert states[5]["halt_equity"] == 1840
    assert states[6]["step"] == "S0" and states[7]["step"] == "S1"


def test_absent_reference_does_not_invent_cluster():
    rows = loss_cluster(); rows[2] = replace(rows[2], halt_evidence=None)
    rows += [facts("2026-10-06",1700,2000),facts("2026-10-07",2000,2100)]
    assert chain(rows)[-1].state["step"] == "S0"
    assert not chain(rows)[-1].state["gates"]["G6"]


def test_daily_only_clear_at_next_open_is_reported_separately():
    rows = [facts("2026-10-01",2000,1880),
            facts("2026-10-02",1880,1880,open_data_ok=True,open_caps_ok=True)]
    ts = chain(rows)
    assert ts[0].state["daily_latched"] and ts[0].state["step"] == "S0"
    assert ts[1].state["daily_cleared_at_open"] and ts[1].state["step"] == "S2"
    assert not chain([rows[0], facts("2026-10-02",1880,1880)])[-1].state["daily_cleared_at_open"]


def test_daily_open_cannot_bypass_consecutive_latch():
    rows = loss_cluster() + [facts("2026-10-06",1700,2000,open_data_ok=True,open_caps_ok=True)]
    assert chain(rows)[-1].state["step"] == "S0"


def test_intraday_breach_still_counts_as_loss_after_close_recovery():
    rows = [replace(facts("2026-10-01"),daily_breach_observed=True),
            replace(facts("2026-10-02"),daily_breach_observed=True),
            replace(facts("2026-10-05"),daily_breach_observed=True,halt_evidence=evidence("2026-10-05",2000,1))]
    s = chain(rows)[-1].state
    assert s["loss_streak"] == 3 and s["recovery_latched"]


@pytest.mark.parametrize("field,value", [("marks_complete",False),("ledger_complete",False),("flows_verified",False),
    ("equity_end",None),("cumulative_net_flows",None),("daily_breach_observed",None)])
def test_unknown_financial_facts_never_count_as_nonloss(field,value):
    rows = loss_cluster() + [replace(facts("2026-10-06",1700,2000),**{field:value})]
    s = chain(rows)[-1].state
    assert s["data_gap_sessions"] == 1 and s["non_loss_streak"] == 0
    assert s["status"] == "INCONCLUSIVE" and s["research_budget_fraction"] == 0


def test_missing_sessions_are_gaps_and_h3_is_permanent():
    b = binding(); ts = chain(loss_cluster(), b)
    # Oct 6/7/8 are real sessions. Omitting Oct 6 cannot compress the clock.
    with pytest.raises(ValueError,match="next XNYS"):
        advance_option_d(binding=b,facts=facts("2026-10-07"),previous_transition=ts[-1])
    for day in ("2026-10-06","2026-10-07","2026-10-08"):
        ts.append(advance_option_d(binding=b,facts=SessionFacts(session=day),previous_transition=ts[-1]))
    assert ts[-1].state["step"] == "HARD" and ts[-1].state["data_gap_sessions"] == 3
    t = advance_option_d(binding=b,facts=facts("2026-10-09",1700,3000),previous_transition=ts[-1])
    assert t.state["step"] == "HARD" and not t.state["gates"]["G5"]


@pytest.mark.parametrize("field", ["integrity_crisis","manual_hard_stop","governance_violation"])
def test_hard_triggers_never_auto_clear(field):
    rows = [replace(facts("2026-10-01"),**{field:True}), facts("2026-10-02")]
    assert chain(rows)[-1].state["step"] == "HARD"


def test_unrestorable_caps_hard_restorable_caps_soft():
    assert chain([replace(facts("2026-10-01"),caps_ok=False,caps_restorable=False)])[-1].state["step"] == "HARD"
    assert chain([replace(facts("2026-10-01"),caps_ok=False)])[-1].state["step"] == "S0"


def test_calendar_holiday_weekend_halfday_and_strict_order():
    b = binding("2026-11-23","2026-11-30")
    assert b.calendar.sessions == ("2026-11-23","2026-11-24","2026-11-25","2026-11-27","2026-11-30")
    assert b.calendar.closes[3].endswith("18:00:00+00:00")
    ts = chain([facts(d) for d in b.calendar.sessions],b)
    for day in ("2026-11-30","2026-11-27","2026-11-26","2026-11-28"):
        with pytest.raises(ValueError): advance_option_d(binding=b,facts=facts(day),previous_transition=ts[-1])


@pytest.mark.parametrize("change", ["policy_sha256","overlay_sha256","identity","calendar"])
def test_chain_identity_and_frozen_sources_cannot_change(change):
    b = binding(); prior = chain([facts("2026-10-01")],b)[-1]
    values = {"policy_sha256":"e"*64,"overlay_sha256":"f"*64,
              "identity":StrategyRiskStateIdentity("soxl-option-d","research-only","other","a"*64),
              "calendar":freeze_xnys_calendar("2026-10-01","2026-12-01")}
    with pytest.raises(ValueError):
        advance_option_d(binding=replace(b,**{change:values[change]}),facts=facts("2026-10-02"),previous_transition=prior)


def test_content_addressed_determinism_and_create_only_restart(tmp_path):
    b=binding(); ts=chain(loss_cluster(),b)
    store=StrategyRiskStateStore(local_dir=tmp_path)
    for t in ts: store.append(t)
    loaded=store.load_chain(b.identity)
    assert loaded == tuple(ts)
    assert advance_option_d(binding=b,facts=facts("2026-10-06",1700,1850),previous_transition=loaded[-1]) == chain(loss_cluster()+[facts("2026-10-06",1700,1850)],b)[-1]
    changed=advance_option_d(binding=b,facts=facts("2026-10-06",1700,1849),previous_transition=loaded[-1])
    assert changed.input_sha256 != chain(loss_cluster()+[facts("2026-10-06",1700,1850)],b)[-1].input_sha256


@pytest.mark.parametrize("value", [float("nan"),float("inf"),True,"2000"])
def test_invalid_money_rejected(value):
    with pytest.raises(ValueError): chain([replace(facts("2026-10-01"),equity_end=value)])


def test_exact_limit_breaches_and_signed_gain_not_clipped():
    assert chain([facts("2026-10-01",2000,1900)])[-1].state["daily_latched"]
    assert chain([facts("2026-10-01",2000,2001)])[-1].state["signed_daily_loss"] == -1
    for opening,limit in ((500,25),(2000,100),(10000,100),(100,20)):
        assert chain([facts("2026-10-01",opening,opening)])[-1].state["daily_limit_usd"] == limit


def test_s1_intraday_breach_restarts_even_with_positive_close():
    rows = loss_cluster()+[facts("2026-10-06",1700,1850),facts("2026-10-07",1850,1850),
        replace(facts("2026-10-08",1850,1860,halt_evidence=evidence("2026-10-08",1800,50)),daily_breach_observed=True)]
    s=chain(rows)[-1].state
    assert s["step"] == "S0" and s["halt_equity"] == 1800


def test_missing_gap_breaks_g2_and_gap_escalation_counter_resets():
    rows=loss_cluster()+[SessionFacts("2026-10-06"),facts("2026-10-07",1700,1800),SessionFacts("2026-10-08")]
    s=chain(rows)[-1].state
    assert s["data_gap_sessions"] == 1 and s["non_loss_streak"] == 0 and s["step"] == "S0"


def test_caps_or_flow_unknown_blocks_recovery():
    for change in ({"caps_ok":False},{"caps_ok":None},{"flows_verified":False}):
        rows=loss_cluster()+[facts("2026-10-06",1700,1850),replace(facts("2026-10-07",1850,1850),**change)]
        assert chain(rows)[-1].state["research_budget_fraction"] == 0


def test_holidays_and_halfday_count_for_c_and_k():
    b=binding("2026-11-20","2026-11-30")
    rows=[facts("2026-11-20",2000,1900),facts("2026-11-23",1900,1800),
          facts("2026-11-24",1800,1700,halt_evidence=HaltEvidence("2026-11-24",1700,300,"2026-11-20","2026-11-24","d"*64,0)),
          facts("2026-11-25",1700,1850),facts("2026-11-27",1850,1850)]
    s=chain(rows,b)[-1].state
    assert s["completed_since_halt"] == 2 and s["non_loss_streak"] == 2 and s["step"] == "S1"


def test_repeated_three_loss_cluster_restarts_reference_and_clock():
    rows=loss_cluster()+[facts("2026-10-06",1700,1700),facts("2026-10-07",1700,1690),
        facts("2026-10-08",1690,1680),facts("2026-10-09",1680,1670,halt_evidence=evidence("2026-10-09",1670,30))]
    s=chain(rows)[-1].state
    assert s["halt_session"] == "2026-10-09" and s["halt_equity"] == 1670 and s["completed_since_halt"] == 0


def test_unknown_session_does_not_join_separated_losses_into_known_streak():
    rows=[facts("2026-10-01",2000,1990),facts("2026-10-02",1990,1980),SessionFacts("2026-10-05"),facts("2026-10-06",1980,1970)]
    assert chain(rows)[-1].state["loss_streak"] == 1


def test_trigger_day_deposit_after_halt_cannot_manufacture_g6_recovery():
    # Loss from 1800 to 1700 triggers, then a 100 deposit raises the close to 1800.
    rows=loss_cluster()
    rows[2]=replace(rows[2],equity_end=1800,deposits=100,cumulative_net_flows=100,
                    halt_evidence=replace(rows[2].halt_evidence,trigger_net_flows=0))
    rows += [replace(facts("2026-10-06",1800,1850),cumulative_net_flows=100),
             replace(facts("2026-10-07",1850,1850),cumulative_net_flows=100)]
    s=chain(rows)[-1].state
    assert s["recovery_equity"] == 1750 and not s["gates"]["G6"] and s["step"] == "S0"
