"""Research-only TQQQ SMA200 inclusive-close baseline, v1 reslice."""
from __future__ import annotations
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime,timezone
import pandas as pd
from .session_asof_contract import RequestedObservedWindow,SessionClose
from .snapshot_numeric_contract import ValidatedSessionSnapshot
PROFILE="tqqq_growth_income_research_baseline_v1"; CONTRACT="us_equity.tqqq_research_baseline.sma200_inclusive_close.v1"; TIMING="next_open"; REQUIRED=("BOXX","QQQ","TQQQ"); MAX=1e308; UTC_RE=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$')
class TqqqBaselineError(ValueError): pass
def _finite(x,field,positive=False):
 if isinstance(x,bool) or not isinstance(x,(int,float)): raise TqqqBaselineError(f"invalid {field}")
 y=float(x)
 if not math.isfinite(y) or (positive and y<=0) or abs(y)>MAX: raise TqqqBaselineError(f"invalid {field}")
 return y
def _calc(x,field): return _finite(x,field)
def _computed(x):
 if not isinstance(x,str) or not UTC_RE.fullmatch(x): raise TqqqBaselineError("invalid computed_at")
 try: datetime.strptime(x,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
 except ValueError: raise TqqqBaselineError("invalid computed_at") from None
 return x
@dataclass(frozen=True,slots=True)
class Result:
 profile:str; contract:str; timing:str; source_revision:str; computed_at:str; window:RequestedObservedWindow; as_of:SessionClose; input_digest:str; params_digest:str; run_id:str; equity_curve:tuple[tuple[str,float],...]; daily_returns:tuple[tuple[str,float],...]; metrics:tuple[tuple[str,float|None],...]
 def to_wire(self): return {'as_of':self.as_of.to_wire(),'computed_at':self.computed_at,'contract':self.contract,'daily_returns':[[d,v] for d,v in self.daily_returns],'equity_curve':[[d,v] for d,v in self.equity_curve],'input_digest':self.input_digest,'metrics':dict(self.metrics),'params_digest':self.params_digest,'profile':self.profile,'run_id':self.run_id,'source_revision':self.source_revision,'timing':self.timing,'window':self.window.to_wire(),'controls':{'crisis_defense':False,'market_regime':False,'macro_risk_governor':False,'volatility_delever':False,'retention':False,'taco':False,'production_equivalent':False}}
 def canonical_bytes(self):
  try:return json.dumps(self.to_wire(),sort_keys=True,separators=(',',':'),allow_nan=False,ensure_ascii=False).encode()
  except (UnicodeEncodeError,TypeError,ValueError): raise TqqqBaselineError('invalid result wire') from None
def run(*,bars:pd.DataFrame,sessions:tuple[SessionClose,...],window:RequestedObservedWindow,snapshot:ValidatedSessionSnapshot,source_revision:str,computed_at:str)->Result:
 if not isinstance(window,RequestedObservedWindow) or not isinstance(snapshot,ValidatedSessionSnapshot) or snapshot.window!=window: raise TqqqBaselineError('invalid session/window')
 if any(not isinstance(s,SessionClose) for s in sessions) or tuple(sessions)!=tuple(sorted(sessions,key=lambda s:s.trading_date)): raise TqqqBaselineError('invalid sessions')
 final=next((s for s in sessions if s.trading_date==window.as_of),None)
 if snapshot.session!=final: raise TqqqBaselineError('invalid as_of')
 if not isinstance(source_revision,str) or not source_revision: raise TqqqBaselineError('invalid source_revision')
 _computed(computed_at)
 if not isinstance(bars,pd.DataFrame) or not {'date','symbol','open','close'}.issubset(bars.columns): raise TqqqBaselineError('invalid bars')
 d=bars.loc[:,['date','symbol','open','close']].copy(); parsed=pd.to_datetime(d.date,errors='coerce')
 if parsed.isna().any(): raise TqqqBaselineError('invalid bar date')
 d['date']=parsed.dt.date
 pairs=list(zip(d.date,d.symbol))
 if pairs!=sorted(pairs) or len(set(pairs))!=len(pairs): raise TqqqBaselineError('bars must be sorted unique')
 for c in ('open','close'):
  vals=pd.to_numeric(d[c],errors='coerce')
  if vals.isna().any() or not vals.map(math.isfinite).all() or (vals<=0).any(): raise TqqqBaselineError('invalid price')
  d[c]=vals.astype(float)
 dates=tuple(s.trading_date for s in sessions if window.observed_start_date<=s.trading_date<=window.observed_end_date); observed=tuple(sorted(set(x for x in d.date if window.observed_start_date<=x<=window.observed_end_date)))
 if observed!=dates or len(dates)<2: raise TqqqBaselineError('calendar mismatch')
 for day in dates:
  if set(d.loc[d.date==day,'symbol'])!=set(REQUIRED): raise TqqqBaselineError('missing symbol')
 pre=d[(d.symbol=='QQQ')&(d.date<window.observed_start_date)]
 if len(pre)<199: raise TqqqBaselineError('insufficient warmup')
 inp=hashlib.sha256(d.to_csv(index=False,lineterminator='\n').encode()).hexdigest(); params=b'{"sma":"inclusive_close_200","initial_cash":100000,"cost_bps":0,"controls":"disabled"}'; pdigest=hashlib.sha256(params).hexdigest(); identity={'as_of':final.to_wire(),'contract':CONTRACT,'input_digest':inp,'params_digest':pdigest,'profile':PROFILE,'source_revision':source_revision,'timing':TIMING,'window':window.to_wire()}; rid=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 groups={day:d[d.date==day].set_index('symbol') for day in dates}; cash=100000.0; qty={'BOXX':0.0,'TQQQ':0.0}; curve=[]
 for i,day in enumerate(dates):
  today=groups[day]; equity=_calc(cash+sum(_calc(qty[s]*float(today.loc[s,'close']),'holding_value') for s in qty),'equity'); curve.append((day.isoformat(),equity))
  if i==len(dates)-1: continue
  hist=d[(d.symbol=='QQQ')&(d.date<=day)].close.tail(200)
  if len(hist)<200: raise TqqqBaselineError('insufficient inclusive SMA')
  mean=_calc(hist.mean(),'sma'); target=0.9 if float(today.loc['QQQ','close'])>mean else 0.0; nxt=groups[dates[i+1]]; open_eq=_calc(cash+sum(_calc(qty[s]*float(nxt.loc[s,'open']),'open_revalue') for s in qty),'open_equity')
  qty['TQQQ']=_calc(open_eq*target/float(nxt.loc['TQQQ','open']),'tqqq_qty'); qty['BOXX']=_calc(open_eq*(1-target)/float(nxt.loc['BOXX','open']),'boxx_qty'); cash=0.0
 returns=tuple((curve[i][0],_calc(curve[i][1]/curve[i-1][1]-1,'return')) for i in range(1,len(curve))); vals=[v for _,v in curve]; daily=[v for _,v in returns]; total=_calc(vals[-1]/vals[0]-1,'total_return'); vol=float(pd.Series(daily).std(ddof=1)*math.sqrt(252)) if len(daily)>1 else 0.0; vol=_finite(vol,'volatility'); ann=_calc((1+total)**(252/max(1,len(daily)))-1,'annualized_return'); peak=vals[0]; dd=0.0
 for v in vals: peak=max(peak,v); dd=min(dd,_calc(v/peak-1,'drawdown'))
 sharpe=None if not daily or vol==0 else _calc(sum(daily)/len(daily)/(vol/math.sqrt(252))*math.sqrt(252),'sharpe'); metrics=(('total_return',total),('annualized_return',ann),('annualized_volatility',vol),('max_drawdown',dd),('sharpe_rf0',sharpe)); return Result(PROFILE,CONTRACT,TIMING,source_revision,computed_at,window,final,inp,pdigest,rid,tuple(curve),returns,metrics)
