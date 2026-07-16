"""Typed private TQQQ baseline with preflight-safe ownership and derivation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from .tqqq_offline_input_contract import InputRow,OfflineInput

PROFILE='tqqq_growth_income_research_baseline_v1'; VERSION='tqqq_growth_income_research_baseline.v1'
class BaselineContractError(ValueError): pass
@dataclass(frozen=True)
class BaselineParameters:
 sma_window:int=200; execution_timing:str='next_observed_open'; commission_bps:int=0; slippage_bps:int=0; controls_disabled:bool=True

def _fail(): raise BaselineContractError('invalid typed baseline') from None
def _num(v):
 if type(v) not in (int,float) or isinstance(v,bool): _fail()
 try:n=float(v)
 except (TypeError,ValueError,OverflowError):_fail()
 if not math.isfinite(n):_fail()
 return 0.0 if n==0.0 else n
def _positive(v):
 n=_num(v)
 if n<=0:_fail()
 return n
def _date(v):
 if type(v) is not str:_fail()
 try:d=date.fromisoformat(v)
 except (TypeError,ValueError):_fail()
 if d.isoformat()!=v:_fail()
 return v
def _bytes(v):
 try:return (json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
 except (TypeError,ValueError,OverflowError):_fail()
def _validate_source(src):
 if type(src) is not OfflineInput or type(src.rows) is not tuple:_fail()
 rows=[]
 try:
  for row in src.rows:
   if type(row) is not InputRow:_fail()
   symbol=row.symbol; day=row.as_of; vals=(row.open,row.high,row.low,row.close,row.volume)
   if type(symbol) is not str or symbol not in ('QQQ','TQQQ'):_fail()
   day=_date(day); nums=tuple(_positive(v) if i<4 else _num(v) for i,v in enumerate(vals))
   if nums[4]<0 or not (nums[2]<=min(nums[0],nums[3])<=max(nums[0],nums[3])<=nums[1]):_fail()
   rows.append((symbol,day,*nums))
 except (AttributeError,TypeError,ValueError,OverflowError): _fail()
 rows=tuple(rows)
 if len({(r[0],r[1]) for r in rows})!=len(rows) or tuple((r[1],r[0]) for r in rows)!=tuple(sorted((r[1],r[0]) for r in rows)):_fail()
 dates=sorted({r[1] for r in rows})
 if len(dates)<201 or any((s,d) not in {(r[0],r[1]) for r in rows} for s in ('QQQ','TQQQ') for d in dates):_fail()
 return rows
@dataclass(frozen=True)
class EquityPoint:
 date:str; equity:float; cash:float; quantity:float; close:float
 def __post_init__(self): object.__setattr__(self,'date',_date(self.date)); [object.__setattr__(self,n,_num(getattr(self,n))) for n in ('equity','cash','quantity','close')]
@dataclass(frozen=True)
class ReturnPoint:
 date:str; value:float
 def __post_init__(self): object.__setattr__(self,'date',_date(self.date)); object.__setattr__(self,'value',_num(self.value))
@dataclass(frozen=True)
class BaselineResult:
 profile:str; version:str; input_identity:tuple; equity_curve:tuple[EquityPoint,...]; returns:tuple[ReturnPoint,...]; parameters:BaselineParameters
 def __post_init__(self):
  if type(self.equity_curve) is not tuple or type(self.returns) is not tuple or type(self.parameters) is not BaselineParameters:_fail()
  if any(type(x) is not EquityPoint for x in self.equity_curve) or any(type(x) is not ReturnPoint for x in self.returns):_fail()
  if type(self.input_identity) is not tuple:_fail()
  if len(self.input_identity)!=2 or type(self.input_identity[0]) is not str or type(self.input_identity[1]) is not tuple:_fail()
  if self.parameters != BaselineParameters(): _fail()
 @property
 def input_digest(self): return hashlib.sha256(_bytes({'source_revision':self.input_identity[0],'rows':self.input_identity[1]})).hexdigest()
 @property
 def parameter_digest(self): return hashlib.sha256(_bytes({'sma_window':self.parameters.sma_window,'execution_timing':self.parameters.execution_timing,'commission_bps':self.parameters.commission_bps,'slippage_bps':self.parameters.slippage_bps,'controls_disabled':self.parameters.controls_disabled})).hexdigest()
 @property
 def evaluation_count(self): return len(self.equity_curve)
 @property
 def trade_count(self): return int(bool(self.equity_curve and self.equity_curve[0].quantity>0)) + sum(1 for a,b in zip(self.equity_curve,self.equity_curve[1:]) if bool(a.quantity>0)!=bool(b.quantity>0))
 def to_wire(self):
  payload={'profile':self.profile,'version':self.version,'input_digest':self.input_digest,'parameter_digest':self.parameter_digest,'equity_curve':[{'date':x.date,'equity':x.equity,'cash':x.cash,'quantity':x.quantity,'close':x.close} for x in self.equity_curve],'returns':[{'date':x.date,'value':x.value} for x in self.returns],'evaluation_count':self.evaluation_count,'trade_count':self.trade_count,'controls_disabled':True}
  return _bytes(payload)
 @property
 def result_digest(self): return hashlib.sha256(self.to_wire()).hexdigest()
def run_baseline(src:OfflineInput)->BaselineResult:
 rows=_validate_source(src); p=BaselineParameters(); by={(r[0],r[1]):r for r in rows}; dates=sorted({r[1] for r in rows}); cash=100000.; qty=0.; prev=None; curve=[]; rets=[]
 for i in range(p.sma_window-1,len(dates)-1):
  sig,ex=dates[i],dates[i+1]; sma=_num(sum(_positive(by[('QQQ',d)][5]) for d in dates[i-p.sma_window+1:i+1])/p.sma_window); target=1. if _positive(by[('QQQ',sig)][5])>=sma else 0.; op=_positive(by[('TQQQ',ex)][2]); prior=_positive(cash+qty*op); new_qty=_num(prior*target/op); cash=_num(prior-new_qty*op); qty=new_qty; close=_positive(by[('TQQQ',ex)][5]); eq=_positive(cash+qty*close); curve.append(EquityPoint(ex,eq,cash,qty,close))
  if prev is not None: rets.append(ReturnPoint(ex,_num(eq/prev-1)))
  prev=eq
 if not curve:_fail()
 return BaselineResult(PROFILE,VERSION,(src.source_revision,rows),tuple(curve),tuple(rets),p)
