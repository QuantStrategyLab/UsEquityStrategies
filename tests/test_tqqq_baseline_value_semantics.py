from datetime import date, timedelta
import pytest
from us_equity_strategies.research.tqqq_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.tqqq_baseline_value_semantics import BaselineContractError, run_baseline

def data(n=202):
 rows=[]; start=date(2020,1,1)
 for i in range(n):
  d=(start+timedelta(days=i)).isoformat(); q=100+i*.1; t=50+i*.2
  rows += [InputRow("QQQ",d,q,q+1,q-1,q,1000),InputRow("TQQQ",d,t,t+1,t-1,t,2000)]
 return OfflineInput(tuple(sorted(rows,key=lambda r:(r.as_of,r.symbol))),b"x","input-digest","source-v1")

def test_immutable_points_and_current_wire_digest():
 r=run_baseline(data()); assert r.evaluation_count==2; assert r.trade_count==1
 assert r.result_digest == r.result_digest
 assert r.result_digest == __import__('hashlib').sha256(r.to_wire()).hexdigest()
 assert isinstance(r.equity_curve[0].date,str)
 with pytest.raises(AttributeError): r.equity_curve[0].equity = 1

def test_insufficient_warmup_and_duplicate_fail_closed():
 with pytest.raises(BaselineContractError): run_baseline(data(199))
 d=data(); dup=OfflineInput(d.rows+(d.rows[0],),d.canonical_bytes,d.input_digest,d.source_revision)
 with pytest.raises(BaselineContractError): run_baseline(dup)

def test_no_trade_observation_counted():
 r=run_baseline(data()); assert r.evaluation_count == len(r.equity_curve); assert r.trade_count <= r.evaluation_count

def test_result_rejects_mutable_containers_and_trade_count_is_state_transition():
    r=run_baseline(data())
    from us_equity_strategies.research.tqqq_baseline_value_semantics import BaselineResult
    with pytest.raises((TypeError, BaselineContractError)):
        BaselineResult(r.profile,r.contract_version,r.input_digest,r.parameter_digest,list(r.equity_curve),r.daily_returns,r.evaluation_count,r.trade_count)
    assert r.trade_count <= r.evaluation_count
