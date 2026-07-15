import json
from datetime import date
from pathlib import Path
import pytest
from us_equity_strategies.backtest.trading_calendar_v1 import CalendarContractError,TradingCalendarV1,load_bytes
PATH=Path(__file__).parents[1]/'src/us_equity_strategies/backtest/xnas_trading_calendar_v1.json'
def test_exact_roundtrip_and_metadata():
 raw=PATH.read_bytes(); c=TradingCalendarV1.from_bytes(raw); assert c.canonical_bytes()==raw; assert c.expected_session_count==137; assert c.sessions[0].trading_date==date(2025,1,2); assert any(s.session_kind=='approved_half_day' for s in c.sessions)
def test_duplicate_unknown_noncanonical_and_tamper_reject():
 raw=PATH.read_bytes()
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(raw.replace(b'"schema":',b'"schema":"bad", "schema":',1))
 p=json.loads(raw); p['unknown']=1
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,separators=(',',':')).encode())
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(raw.replace(b'{',b'{ ',1))
 p=json.loads(raw); p['sessions']=p['sessions'][1:]; p['session_count']-=1
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode())
def test_digest_inventory_and_session_values():
 raw=PATH.read_bytes(); p=json.loads(raw); p['artifact_digest']='0'*64
 with pytest.raises(CalendarContractError): TradingCalendarV1.from_bytes(json.dumps(p,sort_keys=True,separators=(',',':')).encode())
 assert load_bytes(PATH).sessions[-1].trading_date==date(2025,7,21)
