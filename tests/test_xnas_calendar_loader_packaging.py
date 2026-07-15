import inspect
from importlib import resources
import pytest
import us_equity_strategies.backtest.xnas_calendar_loader as m

def test_fixed_loader_and_resource():
 assert list(inspect.signature(m.load_checked_in_xnas_calendar).parameters)==[]
 assert len(m.load_checked_in_xnas_calendar())==137
 assert resources.files('us_equity_strategies.backtest').joinpath('xnas_trading_calendar_v1.json').is_file()

def test_resource_failures_are_sanitized(monkeypatch):
 class Broken:
  def read_bytes(self): raise OSError('secret path')
 monkeypatch.setattr(m.resources,'files',lambda _: type('P',(),{'joinpath':lambda *_: Broken()})())
 with pytest.raises(m.CalendarContractError,match='resource unavailable'):
  m.load_checked_in_xnas_calendar()

def test_missing_timezone_is_lazy_and_sanitized(monkeypatch):
 original=m.ZoneInfo
 def missing(name): raise m.ZoneInfoNotFoundError(name)
 monkeypatch.setattr(m,'ZoneInfo',missing)
 assert m.CalendarContractError
 with pytest.raises(m.CalendarContractError):
  m.CalendarSession(__import__('datetime').date(2025,1,2),'regular','2025-01-02T14:30:00Z','2025-01-02T21:00:00Z')
 monkeypatch.setattr(m,'ZoneInfo',original)
