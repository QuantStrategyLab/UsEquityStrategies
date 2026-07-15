import builtins
import importlib
import inspect
import sys
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

def test_loader_does_not_require_zoneinfo(monkeypatch):
 real_import=builtins.__import__
 def blocked(name,*args,**kwargs):
  if name=='zoneinfo': raise ImportError('blocked for packaging smoke')
  return real_import(name,*args,**kwargs)
 monkeypatch.setattr(builtins,'__import__',blocked)
 sys.modules.pop(m.__name__,None)
 loaded=importlib.import_module(m.__name__)
 assert len(loaded.load_checked_in_xnas_calendar())==137
