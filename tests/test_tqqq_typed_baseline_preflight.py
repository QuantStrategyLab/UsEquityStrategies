from datetime import date,timedelta
import hashlib
import pytest
from us_equity_strategies.research.tqqq_offline_input_contract import InputRow,OfflineInput
from us_equity_strategies.research.tqqq_typed_baseline_preflight import BaselineContractError,run_baseline

def inp(n=202):
 rows=[]; s=date(2020,1,1)
 for i in range(n):
  d=(s+timedelta(days=i)).isoformat(); q=100+i*.1; t=50+i*.2
  rows += [InputRow('QQQ',d,q,q+1,q-1,q,1000),InputRow('TQQQ',d,t,t+1,t-1,t,2000)]
 return OfflineInput(tuple(sorted(rows,key=lambda x:(x.as_of,x.symbol))),b'forged', 'f'*64,'source-v1')

def test_factory_recomputes_digest_and_derives_counts():
 r=run_baseline(inp()); assert r.evaluation_count==len(r.equity_curve); assert r.result_digest==hashlib.sha256(r.to_wire()).hexdigest(); assert r.input_digest != 'f'*64

def test_malformed_rows_fail_before_dereference():
 d=inp(); rows=list(d.rows); rows[0]=object()
 with pytest.raises(BaselineContractError): run_baseline(OfflineInput(tuple(rows),b'x','x','s'))

def test_duplicate_and_insufficient_fail_closed():
 d=inp()
 with pytest.raises(BaselineContractError): run_baseline(OfflineInput(d.rows+(d.rows[0],),b'x','x','s'))
 with pytest.raises(BaselineContractError): run_baseline(inp(199))

def test_forged_digest_and_source_revision_do_not_control_result_identity():
 a=run_baseline(inp()); d=inp(); b=run_baseline(OfflineInput(d.rows,d.canonical_bytes,'different','other-source'))
 assert a.input_digest != b.input_digest

def test_counts_are_derived_properties():
 r=run_baseline(inp()); assert r.evaluation_count == len(r.equity_curve); assert r.trade_count >= 0
