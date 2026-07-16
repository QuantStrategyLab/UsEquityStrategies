"""Immutable value semantics for the controls-disabled TQQQ research baseline."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any
from .tqqq_offline_input_contract import OfflineInput

PROFILE="tqqq_growth_income_research_baseline_v1"
CONTRACT_VERSION="tqqq_growth_income_research_baseline.v1"
PARAMETERS={"sma_window":200,"execution_timing":"next_observed_open","commission_bps":0,"slippage_bps":0,"controls_disabled":True}
class BaselineContractError(ValueError): pass

def _fail(): raise BaselineContractError("invalid research baseline result") from None

def _finite(v):
 try: n=float(v)
 except (TypeError,ValueError,OverflowError): _fail()
 if not math.isfinite(n): _fail()
 return 0.0 if n==0.0 else n

def _positive(v):
 n=_finite(v)
 if n<=0: _fail()
 return n

def _bytes(v: Any)->bytes:
 try: return (json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
 except (TypeError,ValueError,OverflowError): _fail()

@dataclass(frozen=True)
class EquityPoint:
 date: str; equity: float; cash: float; tqqq_quantity: float; tqqq_close: float
 def __post_init__(self):
  for v in (self.equity,self.cash,self.tqqq_quantity,self.tqqq_close): _finite(v)

@dataclass(frozen=True)
class ReturnPoint:
 date: str; daily_return: float
 def __post_init__(self): _finite(self.daily_return)

@dataclass(frozen=True)
class BaselineResult:
 profile: str; contract_version: str; input_digest: str; parameter_digest: str
 equity_curve: tuple[EquityPoint,...]; daily_returns: tuple[ReturnPoint,...]
 evaluation_count: int; trade_count: int
 controls_disabled: bool=True; provider_completeness: str="unverified"; calendar_authority: str="unverified"
 def __post_init__(self):
  if type(self.equity_curve) is not tuple or type(self.daily_returns) is not tuple:
   _fail()
  if any(type(p) is not EquityPoint for p in self.equity_curve) or any(type(p) is not ReturnPoint for p in self.daily_returns):
   _fail()
  if type(self.evaluation_count) is not int or type(self.trade_count) is not int or self.evaluation_count < 0 or self.trade_count < 0:
   _fail()
 def _snapshot(self):
  return {"profile":self.profile,"contract_version":self.contract_version,"input_digest":self.input_digest,"parameter_digest":self.parameter_digest,"equity_curve":[{"date":p.date,"equity":p.equity,"cash":p.cash,"tqqq_quantity":p.tqqq_quantity,"tqqq_close":p.tqqq_close} for p in self.equity_curve],"daily_returns":[{"date":p.date,"daily_return":p.daily_return} for p in self.daily_returns],"evaluation_count":self.evaluation_count,"trade_count":self.trade_count,"controls_disabled":self.controls_disabled,"provider_completeness":self.provider_completeness,"calendar_authority":self.calendar_authority}
 def to_wire(self): return _bytes(self._snapshot())
 @property
 def result_digest(self): return hashlib.sha256(self.to_wire()).hexdigest()

def run_baseline(input_data: OfflineInput)->BaselineResult:
 if not isinstance(input_data,OfflineInput): _fail()
 rows=input_data.rows
 if len(rows)<400 or len({(r.symbol,r.as_of) for r in rows})!=len(rows): _fail()
 dates=sorted({r.as_of for r in rows}); by={(r.symbol,r.as_of):r for r in rows}
 if len(dates)<201 or any((s,d) not in by for d in dates for s in ("QQQ","TQQQ")): _fail()
 parameter_digest=hashlib.sha256(_bytes(PARAMETERS)).hexdigest(); cash=100000.; qty=0.; prev=None; curve=[]; returns=[]; evaluations=0; trades=0
 for i in range(199,len(dates)-1):
  signal=dates[i]; execution=dates[i+1]; sma=_finite(sum(_finite(by[("QQQ",d)].close) for d in dates[i-199:i+1])/200); target=1. if _finite(by[("QQQ",signal)].close)>=sma else 0.
  open_price=_positive(by[("TQQQ",execution)].open); prior=_finite(cash+qty*open_price); new_qty=_finite(prior*target/open_price); new_cash=_finite(prior-new_qty*open_price)
  if (bool(new_qty > 0) != bool(qty > 0)): trades+=1
  cash,qty=new_cash,new_qty; close=_finite(by[("TQQQ",execution)].close); equity=_finite(cash+qty*close); curve.append(EquityPoint(execution,equity,cash,qty,close))
  if prev is not None: returns.append(ReturnPoint(execution,_finite(equity/prev-1)))
  prev=equity; evaluations+=1
 if not curve: _fail()
 return BaselineResult(PROFILE,CONTRACT_VERSION,input_data.input_digest,parameter_digest,tuple(curve),tuple(returns),evaluations,trades)
