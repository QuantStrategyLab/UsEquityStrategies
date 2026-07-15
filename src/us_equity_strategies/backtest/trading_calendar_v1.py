"""Bytes-only canonical caller-owned XNAS TradingCalendarV1 foundation."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date,datetime,timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo
SCHEMA='us_equity.trading_calendar.v1'; EXCHANGE='XNAS'; TZ='America/New_York'; UTC=timezone.utc; NY=ZoneInfo(TZ); _DATE=re.compile(r'^\d{4}-\d{2}-\d{2}$'); _UTC=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$')
class CalendarContractError(ValueError): pass
def _canonical(x):
 try:return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
 except (UnicodeEncodeError,TypeError,ValueError): raise CalendarContractError('invalid canonical calendar') from None
def _reject_pairs(pairs):
 out={}
 for k,v in pairs:
  if k in out: raise CalendarContractError('duplicate calendar key')
  out[k]=v
 return out
@dataclass(frozen=True,slots=True)
class Session:
 trading_date:date; session_kind:str; open_at_utc:str; close_at_utc:str
 def __post_init__(self):
  if not isinstance(self.trading_date,date) or isinstance(self.trading_date,datetime) or not isinstance(self.session_kind,str) or self.session_kind not in {'regular','approved_half_day'}: raise CalendarContractError('invalid session')
  if not isinstance(self.open_at_utc,str) or not isinstance(self.close_at_utc,str): raise CalendarContractError('invalid timestamp')
  try: op=datetime.strptime(self.open_at_utc,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=UTC); cl=datetime.strptime(self.close_at_utc,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=UTC)
  except (TypeError,ValueError): raise CalendarContractError('invalid timestamp') from None
  if not _UTC.fullmatch(self.open_at_utc) or not _UTC.fullmatch(self.close_at_utc): raise CalendarContractError('invalid timestamp')
  lo,lc=op.astimezone(NY),cl.astimezone(NY); close_hour=13 if self.session_kind=='approved_half_day' else 16
  if lo.date()!=self.trading_date or lc.date()!=self.trading_date or (lo.hour,lo.minute)!=(9,30) or (lc.hour,lc.minute)!=(close_hour,0): raise CalendarContractError('invalid session boundary')
 def wire(self): return {'close_at_utc':self.close_at_utc,'open_at_utc':self.open_at_utc,'session_kind':self.session_kind,'trading_date':self.trading_date.isoformat()}
@dataclass(frozen=True,slots=True)
class TradingCalendarV1:
 revision:str; source_generator_version:str; sessions:tuple[Session,...]; expected_session_count:int; inventory_digest:str; artifact_digest:str
 def _payload(self): return {'exchange':EXCHANGE,'expected_inventory_digest':self.inventory_digest,'expected_session_count':self.expected_session_count,'revision':self.revision,'schema':SCHEMA,'sessions':[s.wire() for s in self.sessions],'source_generator_version':self.source_generator_version,'timezone':TZ}
 def __post_init__(self):
  if not self.sessions or tuple(sorted(self.sessions,key=lambda s:s.trading_date))!=self.sessions or len({s.trading_date for s in self.sessions})!=len(self.sessions): raise CalendarContractError('invalid calendar order')
  if self.expected_session_count!=len(self.sessions): raise CalendarContractError('session count mismatch')
  if hashlib.sha256(_canonical([s.wire() for s in self.sessions])).hexdigest()!=self.inventory_digest: raise CalendarContractError('inventory mismatch')
  if hashlib.sha256(_canonical(self._payload())).hexdigest()!=self.artifact_digest: raise CalendarContractError('artifact digest mismatch')
 def to_wire(self):
  p=self._payload(); p.update({'artifact_digest':self.artifact_digest,'coverage':{'end':self.sessions[-1].trading_date.isoformat(),'start':self.sessions[0].trading_date.isoformat()},'session_count':len(self.sessions)}); return p
 def canonical_bytes(self): return _canonical(self.to_wire())
 @classmethod
 def from_bytes(cls,raw:bytes):
  if not isinstance(raw,bytes): raise CalendarContractError('calendar requires bytes')
  try: payload=json.loads(raw.decode('utf-8'),object_pairs_hook=_reject_pairs)
  except (UnicodeDecodeError,UnicodeError,json.JSONDecodeError): raise CalendarContractError('invalid calendar bytes') from None
  if not isinstance(payload,Mapping): raise CalendarContractError('invalid calendar shape')
  expected={'artifact_digest','coverage','exchange','expected_inventory_digest','expected_session_count','revision','schema','session_count','sessions','source_generator_version','timezone'}
  if set(payload)!=expected or payload['schema']!=SCHEMA or payload['exchange']!=EXCHANGE or payload['timezone']!=TZ: raise CalendarContractError('invalid calendar shape')
  if not isinstance(payload['sessions'],list) or not payload['sessions'] or not isinstance(payload['session_count'],int) or isinstance(payload['session_count'],bool): raise CalendarContractError('invalid sessions shape')
  sessions=[]
  for item in payload['sessions']:
   if not isinstance(item,Mapping) or set(item)!={'close_at_utc','open_at_utc','session_kind','trading_date'} or not isinstance(item['trading_date'],str) or not _DATE.fullmatch(item['trading_date']): raise CalendarContractError('invalid session wire')
   try: d=date.fromisoformat(item['trading_date'])
   except ValueError: raise CalendarContractError('invalid session date') from None
   sessions.append(Session(d,item['session_kind'],item['open_at_utc'],item['close_at_utc']))
  if payload['coverage']!={'start':sessions[0].trading_date.isoformat(),'end':sessions[-1].trading_date.isoformat()} or payload['session_count']!=len(sessions): raise CalendarContractError('invalid coverage')
  obj=cls(payload['revision'],payload['source_generator_version'],tuple(sessions),payload['expected_session_count'],payload['expected_inventory_digest'],payload['artifact_digest'])
  if obj.canonical_bytes()!=raw: raise CalendarContractError('noncanonical calendar bytes')
  return obj
def load_bytes(path: str|Path):
 try: return TradingCalendarV1.from_bytes(Path(path).read_bytes())
 except OSError: raise CalendarContractError('calendar unavailable') from None
