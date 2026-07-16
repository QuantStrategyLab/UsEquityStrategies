from datetime import date, timedelta

import pytest

from us_equity_strategies.research.tqqq_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.tqqq_controls_disabled_baseline import (
    BaselineContractError,
    run_controls_disabled_baseline,
)


def data(n=202):
    rows=[]; start=date(2020,1,1)
    for i in range(n):
        d=(start+timedelta(days=i)).isoformat()
        q=100.0 + i*0.1
        t=50.0 + i*0.2
        rows.extend([InputRow("QQQ",d,q,q+1,q-1,q,1000.0),InputRow("TQQQ",d,t,t+1,t-1,t,2000.0)])
    rows=tuple(sorted(rows,key=lambda r:(r.as_of,r.symbol)))
    return OfflineInput(rows,b"x", "input-digest", "source-v1")


def test_sma200_inclusive_and_next_open_boundary():
    result=run_controls_disabled_baseline(data())
    assert result.controls_disabled is True
    assert result.execution_count == 2
    assert result.equity_curve[0]["date"] == "2020-07-19"
    assert result.input_digest == "input-digest"
    assert result.result_digest == run_controls_disabled_baseline(data()).result_digest


def test_insufficient_warmup_fails_closed():
    with pytest.raises(BaselineContractError):
        run_controls_disabled_baseline(data(199))


def test_result_wire_is_deterministic():
    result=run_controls_disabled_baseline(data())
    assert result.to_wire() == result.to_wire()
    assert result.result_digest == result.result_digest

def test_duplicate_symbol_date_and_nonpositive_execution_open_fail_closed():
    bad = data()
    duplicate = OfflineInput(bad.rows + (bad.rows[0],), bad.canonical_bytes, bad.input_digest, bad.source_revision)
    with pytest.raises(BaselineContractError):
        run_controls_disabled_baseline(duplicate)
    rows = tuple(InputRow(r.symbol, r.as_of, 0.0 if r.symbol == "TQQQ" and r.as_of == "2020-07-19" else r.open, r.high, r.low, r.close, r.volume) for r in bad.rows)
    with pytest.raises(BaselineContractError):
        run_controls_disabled_baseline(OfflineInput(rows, b"x", "d", "s"))


def test_execution_count_includes_no_trade_observations():
    result = run_controls_disabled_baseline(data())
    assert result.execution_count == len(result.equity_curve)
