"""Schema-first bytes-only XNAS TradingCalendarV1 contract."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date,datetime,timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo
MAX_SAFE=2**53-1; SCHEMA='us_equity.trading_calendar.v1'; EXCHANGE='XNAS'; ZONE='America/New_York'; UTC=timezone.utc; NY=ZoneInfo(ZONE); DATE_RE=re.compile(r'^\d{4}-\d{2}-\d{2}$'); TS_RE=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$'); SAFE_RE=re.compile(r'^[A-Za-z0-9._-]{1,64}$')
class CalendarContractError(ValueError): pass
def _pairs(items):
 result={}
 for key,value in items:
  if key in result: raise CalendarContractError('duplicate key')
  result[key]=value
 return result
def _bytes(value):
 try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
 except (UnicodeEncodeError,TypeError,ValueError): raise CalendarContractError('invalid canonical bytes') from None
def _safe_text(value,field):
 if not isinstance(value,str) or not SAFE_RE.fullmatch(value) or any(ord(c)>127 for c in value): raise CalendarContractError(f'invalid {field}')
 return value
def _timestamp(value):
 if not isinstance(value,str) or not TS_RE.fullmatch(value): raise CalendarContractError('invalid timestamp')
 try:return datetime.strptime(value,'%Y-%m-%dT%H:%M:00Z').replace(tzinfo=UTC)
 except ValueError: raise CalendarContractError('invalid timestamp') from None
@dataclass(frozen=True,slots=True)
class Session:
 trading_date:date; kind:str; open_at_utc:str; close_at_utc:str
 def __post_init__(self):
  if self.kind not in ('regular','approved_half_day'): raise CalendarContractError('invalid session kind')
  op,cl=_timestamp(self.open_at_utc),_timestamp(self.close_at_utc); lo,lc=op.astimezone(NY),cl.astimezone(NY); close_hour=13 if self.kind=='approved_half_day' else 16
  if lo.date()!=self.trading_date or lc.date()!=self.trading_date or (lo.hour,lo.minute)!=(9,30) or (lc.hour,lc.minute)!=(close_hour,0): raise CalendarContractError('invalid session boundary')
 def wire(self): return {'close_at_utc':self.close_at_utc,'open_at_utc':self.open_at_utc,'session_kind':self.kind,'trading_date':self.trading_date.isoformat()}
@dataclass(frozen=True,slots=True)
class TradingCalendarV1:
 revision:str; source_generator_version:str; sessions:tuple[Session,...]; expected_session_count:int; inventory_digest:str; artifact_digest:str
 def _base(self): return {'exchange':EXCHANGE,'expected_inventory_digest':self.inventory_digest,'expected_session_count':self.expected_session_count,'revision':self.revision,'schema':SCHEMA,'sessions':[x.wire() for x in self.sessions],'source_generator_version':self.source_generator_version,'timezone':ZONE}
 def __post_init__(self):
  _safe_text(self.revision,'revision'); _safe_text(self.source_generator_version,'source_generator_version')
  if not isinstance(self.expected_session_count,int) or isinstance(self.expected_session_count,bool) or not 1<=self.expected_session_count<=MAX_SAFE or self.expected_session_count!=len(self.sessions): raise CalendarContractError('invalid session count')
  dates=[x.trading_date for x in self.sessions]
  if dates!=sorted(dates) or len(set(dates))!=len(dates): raise CalendarContractError('invalid session order')
  if hashlib.sha256(_bytes([x.wire() for x in self.sessions])).hexdigest()!=self.inventory_digest: raise CalendarContractError('inventory digest mismatch')
  if hashlib.sha256(_bytes(self._base())).hexdigest()!=self.artifact_digest: raise CalendarContractError('artifact digest mismatch')
 def to_wire(self):
  p=self._base(); p.update({'artifact_digest':self.artifact_digest,'coverage':{'end':self.sessions[-1].trading_date.isoformat(),'start':self.sessions[0].trading_date.isoformat()},'session_count':len(self.sessions)}); return p
 def canonical_bytes(self): return _bytes(self.to_wire())
 @classmethod
 def from_bytes(cls,raw:bytes):
  if not isinstance(raw,bytes): raise CalendarContractError('bytes required')
  try: obj=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs)
  except (UnicodeDecodeError,UnicodeError,json.JSONDecodeError): raise CalendarContractError('invalid calendar bytes') from None
  top={'artifact_digest','coverage','exchange','expected_inventory_digest','expected_session_count','revision','schema','session_count','sessions','source_generator_version','timezone'}
  if not isinstance(obj,dict) or set(obj)!=top or obj['schema']!=SCHEMA or obj['exchange']!=EXCHANGE or obj['timezone']!=ZONE: raise CalendarContractError('invalid top-level shape')
  _safe_text(obj['revision'],'revision'); _safe_text(obj['source_generator_version'],'source_generator_version')
  n=obj['expected_session_count']; count=obj['session_count']
  if not isinstance(n,int) or isinstance(n,bool) or not 1<=n<=MAX_SAFE or count!=n or not isinstance(obj['sessions'],list) or not obj['sessions']: raise CalendarContractError('invalid count/sessions')
  sessions=[]; member={'close_at_utc','open_at_utc','session_kind','trading_date'}
  for raw_session in obj['sessions']:
   if not isinstance(raw_session,dict) or set(raw_session)!=member or not isinstance(raw_session['trading_date'],str) or not DATE_RE.fullmatch(raw_session['trading_date']): raise CalendarContractError('invalid session wire')
   try: d=date.fromisoformat(raw_session['trading_date'])
   except ValueError: raise CalendarContractError('invalid session date') from None
   if not isinstance(raw_session['session_kind'],str) or not isinstance(raw_session['open_at_utc'],str) or not isinstance(raw_session['close_at_utc'],str): raise CalendarContractError('invalid session types')
   sessions.append(Session(d,raw_session['session_kind'],raw_session['open_at_utc'],raw_session['close_at_utc']))
  if obj['coverage']!={'start':sessions[0].trading_date.isoformat(),'end':sessions[-1].trading_date.isoformat()}: raise CalendarContractError('invalid coverage')
  result=cls(obj['revision'],obj['source_generator_version'],tuple(sessions),n,obj['expected_inventory_digest'],obj['artifact_digest'])
  if result.canonical_bytes()!=raw: raise CalendarContractError('noncanonical bytes')
  return result
def load(path: str|Path):
 try:return TradingCalendarV1.from_bytes(Path(path).read_bytes())
 except OSError: raise CalendarContractError('calendar unavailable') from None
