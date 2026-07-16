"""Typed private snapshot to immutable TQQQ research baseline result.

No public raw JSON parser is provided; JSON bytes are internal evidence only.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from .tqqq_offline_input_contract import OfflineInput
PROFILE='tqqq_growth_income_research_baseline_v1'; VERSION='tqqq_growth_income_research_baseline.v1'; PARAMS={'sma_window':200,'execution_timing':'next_observed_open','commission_bps':0,'slippage_bps':0,'controls_disabled':True}
class BaselineResultContractError(ValueError): pass
def _fail(): raise BaselineResultContractError('invalid typed baseline result') from None
def _num(v):
 if type(v) not in (int,float) or isinstance(v,bool): _fail()
 try:n=float(v)
 except (TypeError,ValueError,OverflowError):_fail()
 if not math.isfinite(n):_fail()
 return 0.0 if n==0.0 else n
def _date(v):
 if type(v) is not str: _fail()
 try:d=date.fromisoformat(v)
 except (TypeError,ValueError):_fail()
 if d.isoformat()!=v:_fail()
 return v
def _positive(v):
 n=_num(v)
 if n<=0:_fail()
 return n
def _bytes(v):
 try:return (json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
 except (TypeError,ValueError,OverflowError):_fail()
@dataclass(frozen=True)
class EquityPoint:
 date:str; equity:float; cash:float; tqqq_quantity:float; tqqq_close:float
 def __post_init__(self):
  object.__setattr__(self,'date',_date(self.date))
  for n in ('equity','cash','tqqq_quantity','tqqq_close'):object.__setattr__(self,n,_num(getattr(self,n)))
@dataclass(frozen=True)
class ReturnPoint:
 date:str; daily_return:float
 def __post_init__(self):object.__setattr__(self,'date',_date(self.date));object.__setattr__(self,'daily_return',_num(self.daily_return))
@dataclass(frozen=True)
class BaselineResult:
 profile:str; version:str; input_digest:str; parameter_digest:str; equity_curve:tuple[EquityPoint,...]; daily_returns:tuple[ReturnPoint,...]; evaluation_count:int; trade_count:int; controls_disabled:bool=True; provider_completeness:str='unverified'; calendar_authority:str='unverified'
 def __post_init__(self):
  if type(self.profile) is not str or type(self.version) is not str or type(self.input_digest) is not str or type(self.parameter_digest) is not str:_fail()
  if type(self.equity_curve) is not tuple or type(self.daily_returns) is not tuple or any(type(x) is not EquityPoint for x in self.equity_curve) or any(type(x) is not ReturnPoint for x in self.daily_returns):_fail()
  if type(self.evaluation_count) is not int or isinstance(self.evaluation_count,bool) or self.evaluation_count<0 or type(self.trade_count) is not int or isinstance(self.trade_count,bool) or self.trade_count<0:_fail()
  if self.controls_disabled is not True or self.provider_completeness!='unverified' or self.calendar_authority!='unverified':_fail()
 def _snapshot(self):
  if type(self.equity_curve) is not tuple or type(self.daily_returns) is not tuple:_fail()
  return {'profile':self.profile,'version':self.version,'input_digest':self.input_digest,'parameter_digest':self.parameter_digest,'equity_curve':[{'date':p.date,'equity':_num(p.equity),'cash':_num(p.cash),'tqqq_quantity':_num(p.tqqq_quantity),'tqqq_close':_num(p.tqqq_close)} for p in self.equity_curve],'daily_returns':[{'date':p.date,'daily_return':_num(p.daily_return)} for p in self.daily_returns],'evaluation_count':self.evaluation_count,'trade_count':self.trade_count,'controls_disabled':True,'provider_completeness':'unverified','calendar_authority':'unverified'}
 def to_wire(self):return _bytes(self._snapshot())
 @property
 def result_digest(self):return hashlib.sha256(self.to_wire()).hexdigest()
def run_typed_baseline(source:OfflineInput)->BaselineResult:
 if not isinstance(source,OfflineInput) or len(source.rows)<400 or len({(r.symbol,r.as_of) for r in source.rows})!=len(source.rows):_fail()
 dates=sorted({r.as_of for r in source.rows}); by={(r.symbol,r.as_of):r for r in source.rows}
 if len(dates)<201 or any((s,d) not in by for d in dates for s in ('QQQ','TQQQ')):_fail()
 pd=hashlib.sha256(_bytes(PARAMS)).hexdigest(); cash=100000.;qty=0.;prev=None;curve=[];returns=[];evals=0;trades=0
 for i in range(199,len(dates)-1):
  sig,ex=dates[i],dates[i+1]; sma=_num(sum(_positive(by[('QQQ',d)].close) for d in dates[i-199:i+1])/200); target=1. if _positive(by[('QQQ',sig)].close)>=sma else 0.; op=_positive(by[('TQQQ',ex)].open)
  if op<=0:_fail()
  prior=_positive(cash+qty*op); new_qty=_num(prior*target/op); new_cash=_num(prior-new_qty*op); trades+=int(bool(new_qty>0)!=bool(qty>0));cash,qty=new_cash,new_qty; close=_positive(by[('TQQQ',ex)].close);eq=_num(cash+qty*close);curve.append(EquityPoint(ex,eq,cash,qty,close))
  if prev is not None:returns.append(ReturnPoint(ex,_num(eq/prev-1)))
  prev=eq;evals+=1
 if not curve:_fail()
 return BaselineResult(PROFILE,VERSION,source.input_digest,pd,tuple(curve),tuple(returns),evals,trades)
