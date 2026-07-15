import json
from datetime import date
from pathlib import Path
import pytest
from us_equity_strategies.backtest.trading_calendar_schema_first import CalendarContractError,TradingCalendarV1,TRUSTED_XNAS_ANCHOR,TrustedAnchor,load
P=Path(__file__).parents[1]/'src/us_equity_strategies/backtest/xnas_trading_calendar_v1.json'
def test_bytes_exact_and_metadata():
 raw=P.read_bytes(); c=TradingCalendarV1.from_bytes(raw,anchor=TRUSTED_XNAS_ANCHOR); assert c.canonical_bytes()==raw; assert c.expected_session_count==137; assert any(x.kind=='approved_half_day' for x in c.sessions)
def test_types_duplicate_noncanonical_tamper():
 raw=P.read_bytes(); base=json.loads(raw)
 for key,val in [('expected_session_count',True),('expected_session_count','137'),('expected_session_count',1.0),('expected_session_count',None)]:
  p=dict(base); p[key]=val
  with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode(),anchor=TRUSTED_XNAS_ANCHOR)
 p=dict(base); p['unknown']=1
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode(),anchor=TRUSTED_XNAS_ANCHOR)
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(raw.replace(b'{',b'{ ',1),anchor=TRUSTED_XNAS_ANCHOR)
 p=dict(base); p['artifact_digest']='0'*64
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode(),anchor=TRUSTED_XNAS_ANCHOR)
 dup=raw.replace(b'"schema":',b'"schema":"us_equity.trading_calendar.v1", "schema":',1)
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(dup,anchor=TRUSTED_XNAS_ANCHOR)
def test_anchor_is_required_and_external():
 raw=P.read_bytes()
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(raw,anchor=TrustedAnchor if False else None)
 wrong=TrustedAnchor(TRUSTED_XNAS_ANCHOR.schema,TRUSTED_XNAS_ANCHOR.exchange,TRUSTED_XNAS_ANCHOR.timezone,TRUSTED_XNAS_ANCHOR.revision,TRUSTED_XNAS_ANCHOR.source_generator_version,TRUSTED_XNAS_ANCHOR.coverage_start,TRUSTED_XNAS_ANCHOR.coverage_end,TRUSTED_XNAS_ANCHOR.expected_session_count,TRUSTED_XNAS_ANCHOR.inventory_digest,"0"*64)
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(raw,anchor=wrong)

def test_timestamp_and_sessions_fail_closed():
 base=json.loads(P.read_bytes())
 for value in ('2025-01-02T14:30:01Z','2025-01-02T14:30:00.000000Z','2025-01-02T09:30:00+00:00','2025-01-02T14:30:00z'):
  p=dict(base); p['sessions']=list(base['sessions']); p['sessions'][0]=dict(p['sessions'][0]); p['sessions'][0]['open_at_utc']=value
  with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode(),anchor=TRUSTED_XNAS_ANCHOR)
 for value in (None,'bad',{},''):
  p=dict(base); p['sessions']=value
  with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode(),anchor=TRUSTED_XNAS_ANCHOR)
 p=dict(base); p['sessions']=list(base['sessions'])[1:]; p['session_count']-=1
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode(),anchor=TRUSTED_XNAS_ANCHOR)
 assert load(P,anchor=TRUSTED_XNAS_ANCHOR).sessions[-1].trading_date==date(2025,7,21)
