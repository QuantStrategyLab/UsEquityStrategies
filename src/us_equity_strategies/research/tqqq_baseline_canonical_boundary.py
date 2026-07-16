"""Strict canonical value boundary for the controls-disabled TQQQ baseline."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from typing import Any

MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991

class BaselineBoundaryError(ValueError):
    """Sanitized canonical boundary failure."""

def _fail(): raise BaselineBoundaryError("invalid baseline value") from None

def _text(v):
    if type(v) is not str or not v or any(ord(c)<32 for c in v): _fail()
    return v

def _date(v):
    t=_text(v)
    try: parsed=date.fromisoformat(t)
    except (TypeError,ValueError): _fail()
    if parsed.isoformat()!=t: _fail()
    return t

def _num(v):
    if type(v) not in (int,float) or isinstance(v,bool): _fail()
    try: n=float(v)
    except (TypeError,ValueError,OverflowError): _fail()
    if not math.isfinite(n): _fail()
    if type(v) is int and (abs(v) > MAX_SAFE_JSON_INTEGER or int(n) != v): _fail()
    return 0.0 if n==0.0 else n

def _digest(v):
    if type(v) is not str or len(v)!=64 or any(c not in "0123456789abcdef" for c in v): _fail()
    return v

def _wire(v: Any)->bytes:
    try: return (json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
    except (TypeError,ValueError,OverflowError): _fail()

@dataclass(frozen=True)
class EquityPoint:
    date: str; equity: float; cash: float; tqqq_quantity: float; tqqq_close: float
    def __post_init__(self):
        object.__setattr__(self,"date",_date(self.date))
        for name in ("equity","cash","tqqq_quantity","tqqq_close"): object.__setattr__(self,name,_num(getattr(self,name)))

@dataclass(frozen=True)
class ReturnPoint:
    date: str; daily_return: float
    def __post_init__(self): object.__setattr__(self,"date",_date(self.date)); object.__setattr__(self,"daily_return",_num(self.daily_return))

@dataclass(frozen=True)
class BaselineResult:
    profile: str; contract_version: str; input_digest: str; parameter_digest: str
    equity_curve: tuple[EquityPoint,...]; daily_returns: tuple[ReturnPoint,...]
    evaluation_count: int; trade_count: int
    controls_disabled: bool=True; provider_completeness: str="unverified"; calendar_authority: str="unverified"
    def __post_init__(self):
        if type(self.profile) is not str or type(self.contract_version) is not str: _fail()
        _text(self.profile); _text(self.contract_version); _digest(self.input_digest); _digest(self.parameter_digest)
        if type(self.equity_curve) is not tuple or type(self.daily_returns) is not tuple: _fail()
        if any(type(x) is not EquityPoint for x in self.equity_curve) or any(type(x) is not ReturnPoint for x in self.daily_returns): _fail()
        if type(self.evaluation_count) is not int or isinstance(self.evaluation_count,bool) or not 0 <= self.evaluation_count <= MAX_SAFE_JSON_INTEGER: _fail()
        if type(self.trade_count) is not int or isinstance(self.trade_count,bool) or not 0 <= self.trade_count <= MAX_SAFE_JSON_INTEGER: _fail()
        if type(self.controls_disabled) is not bool or self.controls_disabled is not True: _fail()
        if self.provider_completeness != "unverified" or self.calendar_authority != "unverified": _fail()
    def _snapshot(self):
        if type(self.equity_curve) is not tuple or any(type(x) is not EquityPoint for x in self.equity_curve): _fail()
        if type(self.daily_returns) is not tuple or any(type(x) is not ReturnPoint for x in self.daily_returns): _fail()
        return {"profile":_text(self.profile),"contract_version":_text(self.contract_version),"input_digest":_digest(self.input_digest),"parameter_digest":_digest(self.parameter_digest),"equity_curve":[{"date":_date(p.date),"equity":_num(p.equity),"cash":_num(p.cash),"tqqq_quantity":_num(p.tqqq_quantity),"tqqq_close":_num(p.tqqq_close)} for p in self.equity_curve],"daily_returns":[{"date":_date(p.date),"daily_return":_num(p.daily_return)} for p in self.daily_returns],"evaluation_count":self.evaluation_count,"trade_count":self.trade_count,"controls_disabled":self.controls_disabled,"provider_completeness":"unverified","calendar_authority":"unverified"}
    def to_wire(self): return _wire(self._snapshot())
    @property
    def result_digest(self): return hashlib.sha256(self.to_wire()).hexdigest()
    @classmethod
    def from_wire(cls, raw):
        if type(raw) is bytes:
            try: value=json.loads(raw.decode())
            except (UnicodeError,json.JSONDecodeError): _fail()
        elif type(raw) is dict: value=raw
        else: _fail()
        if type(value) is not dict: _fail()
        expected={"profile","contract_version","input_digest","parameter_digest","equity_curve","daily_returns","evaluation_count","trade_count","controls_disabled","provider_completeness","calendar_authority"}
        if set(value)!=expected or type(value["equity_curve"]) is not list or type(value["daily_returns"]) is not list: _fail()
        def elem(item, keys):
            if type(item) is not dict or set(item)!=keys: _fail()
            return item
        equity=tuple(EquityPoint(_date(elem(x,{"date","equity","cash","tqqq_quantity","tqqq_close"})["date"]),_num(x["equity"]),_num(x["cash"]),_num(x["tqqq_quantity"]),_num(x["tqqq_close"])) for x in value["equity_curve"])
        returns=tuple(ReturnPoint(_date(elem(x,{"date","daily_return"})["date"]),_num(x["daily_return"])) for x in value["daily_returns"])
        return cls(value["profile"],value["contract_version"],value["input_digest"],value["parameter_digest"],equity,returns,value["evaluation_count"],value["trade_count"],value["controls_disabled"],value["provider_completeness"],value["calendar_authority"])
