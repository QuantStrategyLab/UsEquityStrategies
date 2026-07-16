from datetime import date,timedelta
import hashlib
import pytest
from us_equity_strategies.research.tqqq_offline_input_contract import InputRow,OfflineInput
from us_equity_strategies.research.tqqq_typed_baseline_result import BaselineResultContractError,EquityPoint,ReturnPoint,run_typed_baseline

def inp(n=202):
 rows=[]; s=date(2020,1,1)
 for i in range(n):
  d=(s+timedelta(days=i)).isoformat(); q=100+i*.1; t=50+i*.2
  rows += [InputRow('QQQ',d,q,q+1,q-1,q,1000),InputRow('TQQQ',d,t,t+1,t-1,t,2000)]
 return OfflineInput(tuple(sorted(rows,key=lambda x:(x.as_of,x.symbol))),b'x','i'*64,'src')

def test_typed_result_current_wire_digest_and_immutability():
 r=run_typed_baseline(inp()); assert r.evaluation_count==2; assert r.result_digest==hashlib.sha256(r.to_wire()).hexdigest(); assert isinstance(r.equity_curve,tuple)
 with pytest.raises(AttributeError): r.equity_curve[0].equity=1

def test_bad_typed_input_and_points_fail_closed():
 with pytest.raises(BaselineResultContractError): run_typed_baseline(object())
 with pytest.raises(BaselineResultContractError): EquityPoint('2020-01-01',float('nan'),1,0,1)
 with pytest.raises(BaselineResultContractError): ReturnPoint('2020-01-01',True)

def test_no_public_raw_json_parser_api():
 import us_equity_strategies.research.tqqq_typed_baseline_result as m
 assert not hasattr(m,'from_wire')

def test_nonpositive_close_and_prior_equity_fail_closed():
 d=inp(); rows=tuple(InputRow(r.symbol,r.as_of,r.open,r.high,r.low,0 if r.symbol=='TQQQ' and r.as_of=='2020-07-19' else r.close,r.volume) for r in d.rows)
 with pytest.raises(BaselineResultContractError): run_typed_baseline(OfflineInput(rows,b'x','i'*64,'src'))
