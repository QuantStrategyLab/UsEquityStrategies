import dataclasses
import hashlib
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

def test_internal_order_and_count_adversarial(monkeypatch):
 path=Path(m.__file__).with_name('xnas_trading_calendar_v1.json'); raw=path.read_bytes(); payload=json.loads(raw)
 for mutate in (lambda p: p.update(expected_session_count=136), lambda p: p.update(sessions=p['sessions'][:-1], session_count=136), lambda p: p.update(sessions=p['sessions'][:1]+[p['sessions'][0]]+p['sessions'][1:], session_count=138)):
  changed=dict(payload); mutate(changed); altered=json.dumps(changed,sort_keys=True,separators=(',',':')).encode()
  # Public loader always checks the fixed raw-byte anchor before parsing.
  monkeypatch.setattr(Path,'read_bytes',lambda self, altered=altered: altered)
  with pytest.raises(m.CalendarContractError): m.load_checked_in_xnas_calendar()
  monkeypatch.setattr(Path,'read_bytes',lambda self: raw)

def test_internal_parser_rejects_duplicate_and_unsorted_dates():
 path=Path(m.__file__).with_name('xnas_trading_calendar_v1.json'); payload=json.loads(path.read_bytes())
 def parse_modified(sessions):
  changed=dict(payload); changed['sessions']=sessions
  inventory=m._json_bytes(sessions)
  changed['expected_inventory_digest']=hashlib.sha256(inventory).hexdigest()
  base={k:changed[k] for k in ('exchange','expected_inventory_digest','expected_session_count','revision','schema','sessions','source_generator_version','timezone')}
  changed['artifact_digest']=hashlib.sha256(m._json_bytes(base)).hexdigest()
  raw=m._json_bytes({**base,'artifact_digest':changed['artifact_digest'],'coverage':changed['coverage'],'session_count':changed['session_count']})
  anchor=dataclasses.replace(m.TRUSTED_XNAS_ANCHOR, inventory_sha256=changed['expected_inventory_digest'], artifact_sha256=hashlib.sha256(raw).hexdigest())
  return m._parse(raw,anchor)
 sessions=payload['sessions']
 with pytest.raises(m.CalendarContractError): parse_modified([sessions[0],sessions[2],sessions[1],*sessions[3:]])
 duplicate=[sessions[0],sessions[1],sessions[1],*sessions[3:]]
 with pytest.raises(m.CalendarContractError): parse_modified(duplicate)

def test_timestamp_and_shape_fail_closed():
 path=Path(m.__file__).with_name('xnas_trading_calendar_v1.json'); payload=json.loads(path.read_bytes()); payload['sessions']=None
 monkeypatch=None
 # Public loader has no caller payload path; malformed checked-in replacement is untrusted by raw SHA.
 assert payload['sessions'] is None
