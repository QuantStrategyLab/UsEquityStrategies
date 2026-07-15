from datetime import date,datetime,timedelta,timezone
import pandas as pd
import pytest
from quant_platform_kit.common.models import PortfolioSnapshot
from us_equity_strategies.backtest.session_asof_contract import SessionClose,RequestedObservedWindow
from us_equity_strategies.backtest.snapshot_numeric_contract import ValidatedSessionSnapshot
from us_equity_strategies.backtest.tqqq_baseline_sma_reslice import run,TqqqBaselineError

def case(nprior=199):
 start=date(2025,1,2); ds=[start+timedelta(days=i) for i in range(nprior+2)]; obs=ds[-2:]
 ss=tuple(SessionClose(x,datetime(x.year,x.month,x.day,20,tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000000Z')) for x in obs); w=RequestedObservedWindow(obs[0],obs[1],obs[0],obs[1],obs[1]); rows=[]
 for i,x in enumerate(ds):
  for s,v in [('BOXX',100+i*.01),('QQQ',100+i*.02),('TQQQ',50+i*.04)]: rows.append({'date':x,'symbol':s,'open':v,'close':v*1.001})
 b=pd.DataFrame(rows).sort_values(['date','symbol']).reset_index(drop=True); p=PortfolioSnapshot(ss[-1].close_datetime,100000.,100000.,100000.,()); snap=ValidatedSessionSnapshot.from_snapshot(ss[-1],w,p); return b,ss,w,snap

def test_199_prior_current_accepts_and_deterministic():
 b,s,w,p=case(); a=run(bars=b,sessions=s,window=w,snapshot=p,source_revision='v1',computed_at='2026-07-15T00:00:00.000000Z'); c=run(bars=b.copy(),sessions=s,window=w,snapshot=p,source_revision='v1',computed_at='2026-07-15T00:00:00.000000Z'); assert a.canonical_bytes()==c.canonical_bytes()

def test_198_prior_rejects():
 b,s,w,p=case(198)
 with pytest.raises(TqqqBaselineError): run(bars=b,sessions=s,window=w,snapshot=p,source_revision='v1',computed_at='2026-07-15T00:00:00.000000Z')

def test_future_bars_and_sessions_fail_closed():
 b,s,w,p=case()
 future=b[b.date==w.observed_end_date].copy(); future["date"]=w.observed_end_date+timedelta(days=1)
 with pytest.raises(TqqqBaselineError): run(bars=pd.concat([b,future]).sort_values(["date","symbol"]).reset_index(drop=True),sessions=s,window=w,snapshot=p,source_revision="v1",computed_at="2026-07-15T00:00:00.000000Z")
 future_session=SessionClose(w.observed_end_date+timedelta(days=1),"2025-07-22T20:00:00.000000Z")
 with pytest.raises(TqqqBaselineError): run(bars=b,sessions=s+(future_session,),window=w,snapshot=p,source_revision="v1",computed_at="2026-07-15T00:00:00.000000Z")

def test_tiny_open_overflow_rejects_before_result():
 b,s,w,p=case(); b.loc[(b.date==w.observed_end_date)&(b.symbol=='TQQQ'),'open']=1e-320
 with pytest.raises(TqqqBaselineError): run(bars=b,sessions=s,window=w,snapshot=p,source_revision='v1',computed_at='2026-07-15T00:00:00.000000Z')
