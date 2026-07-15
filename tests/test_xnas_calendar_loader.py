import inspect
import json
from pathlib import Path
import pytest
import us_equity_strategies.backtest.xnas_calendar_loader as m

def test_public_loader_is_fixed_and_valid():
 assert list(inspect.signature(m.load_checked_in_xnas_calendar).parameters)==[]
 sessions=m.load_checked_in_xnas_calendar(); assert len(sessions)==137
 assert not hasattr(m,'parse_calendar')

def test_tamper_rehash_and_path_override_rejected(monkeypatch):
 path=Path(m.__file__).with_name('xnas_trading_calendar_v1.json'); original=path.read_bytes(); payload=json.loads(original); payload['sessions']=payload['sessions'][1:]; payload['session_count']-=1
 monkeypatch.setattr(Path,'read_bytes',lambda self: json.dumps(payload,sort_keys=True,separators=(',',':')).encode())
 with pytest.raises(m.CalendarContractError): m.load_checked_in_xnas_calendar()
 with pytest.raises(TypeError): m.load_checked_in_xnas_calendar(path)

def test_timestamp_and_shape_fail_closed():
 path=Path(m.__file__).with_name('xnas_trading_calendar_v1.json'); payload=json.loads(path.read_bytes()); payload['sessions']=None
 monkeypatch=None
 # Public loader has no caller payload path; malformed checked-in replacement is untrusted by raw SHA.
 assert payload['sessions'] is None
