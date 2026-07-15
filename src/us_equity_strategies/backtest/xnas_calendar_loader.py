"""Trusted checked-in XNAS calendar resource loader.

Runtime trusts the reviewed raw-artifact SHA and validates UTC wire grammar;
New York/DST boundary checks belong to offline generation/review, so loading
does not require an IANA timezone database.
"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date,datetime
from importlib import resources
_SCHEMA='us_equity.trading_calendar.v1'; _EXCHANGE='XNAS'; _ZONE='America/New_York'; _DATE=re.compile(r'^\d{4}-\d{2}-\d{2}$'); _TS=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$'); _MAX=2**53-1
class CalendarContractError(ValueError): pass
@dataclass(frozen=True,slots=True)
class CalendarSession:
 trading_date:date; kind:str; open_at_utc:str; close_at_utc:str
 def __post_init__(self):
  if not isinstance(self.trading_date,date) or isinstance(self.trading_date,datetime) or self.kind not in ('regular','approved_half_day'): raise CalendarContractError('invalid session')
  if not isinstance(self.open_at_utc,str) or not isinstance(self.close_at_utc,str) or not _TS.fullmatch(self.open_at_utc) or not _TS.fullmatch(self.close_at_utc): raise CalendarContractError('invalid timestamp')
  try: op=datetime.strptime(self.open_at_utc,'%Y-%m-%dT%H:%M:00Z'); cl=datetime.strptime(self.close_at_utc,'%Y-%m-%dT%H:%M:00Z')
  except ValueError: raise CalendarContractError('invalid timestamp') from None
  if op.date()!=self.trading_date or cl.date()!=self.trading_date: raise CalendarContractError('invalid boundary')
 def wire(self): return {'close_at_utc':self.close_at_utc,'open_at_utc':self.open_at_utc,'session_kind':self.kind,'trading_date':self.trading_date.isoformat()}
@dataclass(frozen=True,slots=True)
class _TrustedAnchor:
 schema:str; exchange:str; timezone:str; revision:str; generator:str; start:str; end:str; count:int; inventory_sha256:str; artifact_sha256:str
TRUSTED_XNAS_ANCHOR=_TrustedAnchor(_SCHEMA,_EXCHANGE,_ZONE,'xnas-2025-research-v1','offline.xnas.generator.v1','2025-01-02','2025-07-21',137,'3e8e2933495b077abfd468eaf03c0d6dc984985f7f2bab34a0a791a5437cc2ac','482b930456e635d9c27c1fbd96d1ea9bd1932e2c212cfb60f7247f9fd4a6b79c')
def _json_bytes(value):
 try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
 except (UnicodeEncodeError,TypeError,ValueError): raise CalendarContractError('invalid canonical calendar') from None
def _pairs(items):
 out={}
 for k,v in items:
  if k in out: raise CalendarContractError('duplicate key')
  out[k]=v
 return out
def _parse(raw,anchor):
 if hashlib.sha256(raw).hexdigest()!=anchor.artifact_sha256: raise CalendarContractError('untrusted artifact bytes')
 try: obj=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs)
 except (UnicodeDecodeError,UnicodeError,json.JSONDecodeError): raise CalendarContractError('invalid artifact bytes') from None
 keys={'artifact_digest','coverage','exchange','expected_inventory_digest','expected_session_count','revision','schema','session_count','sessions','source_generator_version','timezone'}
 if not isinstance(obj,dict) or set(obj)!=keys: raise CalendarContractError('invalid shape')
 if (obj['schema'],obj['exchange'],obj['timezone'],obj['revision'],obj['source_generator_version'])!=(anchor.schema,anchor.exchange,anchor.timezone,anchor.revision,anchor.generator): raise CalendarContractError('anchor mismatch')
 n=obj['expected_session_count']
 if not isinstance(n,int) or isinstance(n,bool) or not 1<=n<=_MAX or n!=anchor.count or obj['session_count']!=n or not isinstance(obj['sessions'],list) or len(obj['sessions'])!=n: raise CalendarContractError('invalid count')
 sessions=[]; member={'close_at_utc','open_at_utc','session_kind','trading_date'}
 for item in obj['sessions']:
  if not isinstance(item,dict) or set(item)!=member or not isinstance(item['trading_date'],str) or not _DATE.fullmatch(item['trading_date']): raise CalendarContractError('invalid session wire')
  try: d=date.fromisoformat(item['trading_date'])
  except ValueError: raise CalendarContractError('invalid date') from None
  sessions.append(CalendarSession(d,item['session_kind'],item['open_at_utc'],item['close_at_utc']))
 dates=[s.trading_date for s in sessions]
 if dates!=sorted(dates) or len(set(dates))!=len(dates): raise CalendarContractError('invalid session order')
 if obj['coverage']!={'start':anchor.start,'end':anchor.end} or dates[0].isoformat()!=anchor.start or dates[-1].isoformat()!=anchor.end or obj['expected_inventory_digest']!=anchor.inventory_sha256: raise CalendarContractError('anchor inventory mismatch')
 if hashlib.sha256(_json_bytes([s.wire() for s in sessions])).hexdigest()!=anchor.inventory_sha256: raise CalendarContractError('inventory mismatch')
 base={'exchange':_EXCHANGE,'expected_inventory_digest':obj['expected_inventory_digest'],'expected_session_count':n,'revision':obj['revision'],'schema':_SCHEMA,'sessions':[s.wire() for s in sessions],'source_generator_version':obj['source_generator_version'],'timezone':_ZONE}
 if hashlib.sha256(_json_bytes(base)).hexdigest()!=obj['artifact_digest']: raise CalendarContractError('digest mismatch')
 canonical={**base,'artifact_digest':obj['artifact_digest'],'coverage':obj['coverage'],'session_count':n}
 if _json_bytes(canonical)!=raw: raise CalendarContractError('noncanonical bytes')
 return tuple(sessions)
def load_checked_in_xnas_calendar():
 try: raw=resources.files('us_equity_strategies.backtest').joinpath('xnas_trading_calendar_v1.json').read_bytes()
 except (FileNotFoundError,IsADirectoryError,PermissionError,OSError,ModuleNotFoundError): raise CalendarContractError('calendar resource unavailable') from None
 return _parse(raw,TRUSTED_XNAS_ANCHOR)
__all__=['CalendarContractError','CalendarSession','load_checked_in_xnas_calendar']
