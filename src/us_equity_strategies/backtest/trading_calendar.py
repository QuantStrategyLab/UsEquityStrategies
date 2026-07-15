"""Strict caller-owned XNAS TradingCalendarV1 artifact contract."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date,datetime,timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

SCHEMA="us_equity.trading_calendar.v1"; EXCHANGE="XNAS"; TZ="America/New_York"; _UTC_RE=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$'); _DATE_RE=re.compile(r'^\d{4}-\d{2}-\d{2}$'); UTC=timezone.utc; NY=ZoneInfo(TZ)
class TradingCalendarError(ValueError): pass
@dataclass(frozen=True,slots=True)
class CalendarSession:
 trading_date:date; session_kind:str; open_at_utc:str; close_at_utc:str
 def __post_init__(self):
  if not isinstance(self.trading_date,date) or isinstance(self.trading_date,datetime) or self.session_kind not in {'regular','approved_half_day'}: raise TradingCalendarError('invalid session')
  for field,value in [('open_at_utc',self.open_at_utc),('close_at_utc',self.close_at_utc)]:
   if not isinstance(value,str) or not _UTC_RE.fullmatch(value): raise TradingCalendarError('invalid session timestamp')
   try: datetime.strptime(value,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=UTC)
   except ValueError: raise TradingCalendarError('invalid session timestamp') from None
  op=datetime.strptime(self.open_at_utc,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=UTC); cl=datetime.strptime(self.close_at_utc,'%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=UTC)
  local_op,local_cl=op.astimezone(NY),cl.astimezone(NY)
  if local_op.date()!=self.trading_date or local_cl.date()!=self.trading_date or (local_op.hour,local_op.minute)!=(9,30): raise TradingCalendarError('invalid session boundary')
  expected=(13,0) if self.session_kind=='approved_half_day' else (16,0)
  if (local_cl.hour,local_cl.minute)!=expected: raise TradingCalendarError('invalid session close')
 def to_wire(self): return {'close_at_utc':self.close_at_utc,'open_at_utc':self.open_at_utc,'session_kind':self.session_kind,'trading_date':self.trading_date.isoformat()}
@dataclass(frozen=True,slots=True)
class TradingCalendarV1:
 revision:str; sessions:tuple[CalendarSession,...]; digest:str
 def __post_init__(self):
  if not isinstance(self.revision,str) or not self.revision or not self.sessions or tuple(sorted(self.sessions,key=lambda s:s.trading_date))!=self.sessions: raise TradingCalendarError('invalid calendar')
  dates=[s.trading_date for s in self.sessions]
  if len(set(dates))!=len(dates): raise TradingCalendarError('duplicate calendar session')
  expected=self._payload(self.revision,self.sessions); actual=hashlib.sha256(_bytes(expected)).hexdigest()
  if self.digest!=actual: raise TradingCalendarError('calendar digest mismatch')
  if len(dates)>1 and any((b-a).days>4 for a,b in zip(dates,dates[1:])): raise TradingCalendarError('calendar gap')
 @staticmethod
 def _payload(revision,sessions): return {'exchange':EXCHANGE,'revision':revision,'schema':SCHEMA,'sessions':[s.to_wire() for s in sessions],'timezone':TZ}
 def to_wire(self):
  p=self._payload(self.revision,self.sessions); p['digest']=self.digest; p['session_count']=len(self.sessions); p['coverage']={'end':self.sessions[-1].trading_date.isoformat(),'start':self.sessions[0].trading_date.isoformat()}; return p
 def canonical_bytes(self): return _bytes(self._payload(self.revision,self.sessions))
 def session(self,day):
  try:return next(s for s in self.sessions if s.trading_date==day)
  except StopIteration: raise TradingCalendarError('date outside calendar') from None
 @classmethod
 def from_wire(cls,payload:Mapping[str,object]):
  if not isinstance(payload,Mapping) or set(payload)!={'coverage','digest','exchange','revision','schema','session_count','sessions','timezone'}: raise TradingCalendarError('invalid calendar wire')
  if payload['schema']!=SCHEMA or payload['exchange']!=EXCHANGE or payload['timezone']!=TZ or payload['session_count']!=len(payload['sessions']): raise TradingCalendarError('invalid calendar metadata')
  sessions=[]
  for item in payload['sessions']:
   if not isinstance(item,Mapping) or set(item)!={'close_at_utc','open_at_utc','session_kind','trading_date'} or not _DATE_RE.fullmatch(str(item['trading_date'])): raise TradingCalendarError('invalid calendar session')
   try:d=date.fromisoformat(item['trading_date'])
   except ValueError: raise TradingCalendarError('invalid calendar date') from None
   sessions.append(CalendarSession(d,item['session_kind'],item['open_at_utc'],item['close_at_utc']))
  if payload['coverage']!={'start':sessions[0].trading_date.isoformat(),'end':sessions[-1].trading_date.isoformat()}: raise TradingCalendarError('invalid coverage')
  return cls(payload['revision'],tuple(sessions),payload['digest'])
def _bytes(payload):
 try:return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
 except (UnicodeEncodeError,TypeError,ValueError): raise TradingCalendarError('invalid canonical calendar') from None
def load_calendar(path: str|Path)->TradingCalendarV1:
 try: payload=json.loads(Path(path).read_text(encoding='utf-8'))
 except (OSError,UnicodeError,ValueError): raise TradingCalendarError('invalid calendar artifact') from None
 return TradingCalendarV1.from_wire(payload)

def validate_calendar_domain(calendar:TradingCalendarV1, *, bar_dates:tuple[date,...], observed_dates:tuple[date,...], requested_start:date, as_of:date)->None:
 if not isinstance(calendar,TradingCalendarV1) or requested_start>as_of: raise TradingCalendarError('invalid calendar domain')
 allowed={s.trading_date for s in calendar.sessions}; unique=tuple(sorted(set(bar_dates)))
 if len(bar_dates)!=len(set(bar_dates)) or tuple(bar_dates)!=tuple(sorted(bar_dates)): raise TradingCalendarError('bar dates must be sorted unique')
 if any(d not in allowed for d in unique): raise TradingCalendarError('bar date outside calendar')
 if any(d>as_of for d in unique): raise TradingCalendarError('bar date after as_of')
 expected=tuple(d for d in unique if requested_start<=d<=as_of)
 if expected!=observed_dates: raise TradingCalendarError('observed calendar mismatch')
 if calendar.session(as_of).trading_date!=as_of: raise TradingCalendarError('as_of outside calendar')
