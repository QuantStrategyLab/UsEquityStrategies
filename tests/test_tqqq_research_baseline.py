from datetime import date, datetime, timezone, timedelta
import pandas as pd
import pytest
from quant_platform_kit.common.models import PortfolioSnapshot
from us_equity_strategies.backtest.session_asof_contract import SessionClose, RequestedObservedWindow
from us_equity_strategies.backtest.snapshot_numeric_contract import ValidatedSessionSnapshot
from us_equity_strategies.backtest.tqqq_research_baseline import run_tqqq_research_baseline, TqqqResearchBaselineError

def _case():
    dates = [date(2025,1,2)+timedelta(days=i) for i in range(202)]
    # weekdays are irrelevant to this pure contract; session list is the observed tail.
    observed = dates[-2:]
    sessions = tuple(SessionClose(d, datetime(d.year,d.month,d.day,20,tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000000Z')) for d in observed)
    window = RequestedObservedWindow(dates[-2], dates[-1], dates[-2], dates[-1], dates[-1])
    rows=[]
    for i,d in enumerate(dates):
        for s,base in [('BOXX',100+i*.01),('QQQ',100+i*.05),('TQQQ',50+i*.1)]:
            rows.append({'date':d,'symbol':s,'open':base,'close':base*1.001})
    bars=pd.DataFrame(rows).sort_values(['date','symbol'], kind='stable').reset_index(drop=True)
    final=PortfolioSnapshot(datetime(dates[-1].year,dates[-1].month,dates[-1].day,20,tzinfo=timezone.utc),100000.0,100000.0,100000.0,())
    snap=ValidatedSessionSnapshot.from_snapshot(sessions[-1],window,final)
    return bars,sessions,window,snap

def test_deterministic_and_explicit_controls():
    bars,sessions,window,snap=_case()
    a=run_tqqq_research_baseline(bars=bars,sessions=sessions,window=window,validated_snapshot=snap,source_revision='prices-v1',computed_at='2026-07-15T00:00:00.000000Z')
    b=run_tqqq_research_baseline(bars=bars.copy(),sessions=sessions,window=window,validated_snapshot=snap,source_revision='prices-v1',computed_at='2026-07-15T00:00:00.000000Z')
    assert a.canonical_bytes()==b.canonical_bytes()
    assert a.control_policy.to_wire()=={'crisis_defense':False,'macro_risk_governor':False,'market_regime':False,'production_equivalent':False,'retention':False,'taco':False,'volatility_delever':False}
    assert a.as_of==sessions[-1] and a.observation_count==2

def test_warmup_boundary_and_future_rows_do_not_fill():
    bars,sessions,window,snap=_case()
    pre=bars[(bars.symbol=="QQQ") & (bars.date < window.observed_start_date)]
    assert len(pre)==200
    short=bars.drop(pre.index[0])
    with pytest.raises(TqqqResearchBaselineError):
        run_tqqq_research_baseline(bars=short,sessions=sessions,window=window,validated_snapshot=snap,source_revision="v1",computed_at="2026-07-15T00:00:00.000000Z")
    future=bars.copy()
    extra=bars[ bars.date == window.observed_start_date ].copy()
    extra["date"]=window.observed_end_date + timedelta(days=1)
    future=pd.concat([future,extra], ignore_index=True).sort_values(["date","symbol"], kind="stable").reset_index(drop=True)
    with pytest.raises(TqqqResearchBaselineError):
        run_tqqq_research_baseline(bars=pd.concat([short, extra], ignore_index=True).sort_values(["date","symbol"], kind="stable").reset_index(drop=True),sessions=sessions,window=window,validated_snapshot=snap,source_revision="v1",computed_at="2026-07-15T00:00:00.000000Z")

def test_run_identity_includes_window_and_session():
    bars,sessions,window,snap=_case()
    a=run_tqqq_research_baseline(bars=bars,sessions=sessions,window=window,validated_snapshot=snap,source_revision="v1",computed_at="2026-07-15T00:00:00.000000Z")
    shifted_date=window.requested_start_date - timedelta(days=1)
    shifted=RequestedObservedWindow(shifted_date,window.requested_end_date,window.observed_start_date,window.observed_end_date,window.as_of)
    shifted_snap=ValidatedSessionSnapshot.from_snapshot(sessions[-1],shifted,PortfolioSnapshot(snap.session.close_datetime,100000.0,100000.0,100000.0,()))
    b=run_tqqq_research_baseline(bars=bars,sessions=sessions,window=shifted,validated_snapshot=shifted_snap,source_revision="v1",computed_at="2026-07-15T00:00:00.000000Z")
    assert a.run_id != b.run_id

def test_strict_order_and_calendar():
    bars,sessions,window,snap=_case()
    with pytest.raises(TqqqResearchBaselineError):
        run_tqqq_research_baseline(bars=bars.iloc[::-1],sessions=sessions,window=window,validated_snapshot=snap,source_revision='v1',computed_at='2026-07-15T00:00:00.000000Z')
    missing=bars[~((bars.date==window.observed_start_date)&(bars.symbol=='QQQ'))]
    with pytest.raises(TqqqResearchBaselineError):
        run_tqqq_research_baseline(bars=missing,sessions=sessions,window=window,validated_snapshot=snap,source_revision='v1',computed_at='2026-07-15T00:00:00.000000Z')

def test_bad_timestamp_rejected():
    bars,sessions,window,snap=_case()
    with pytest.raises(TqqqResearchBaselineError):
        run_tqqq_research_baseline(bars=bars,sessions=sessions,window=window,validated_snapshot=snap,source_revision='v1',computed_at='2026-07-15T00:00:00Z')
