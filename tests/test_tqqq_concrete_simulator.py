from datetime import date, timedelta

import pandas as pd
import pytest

from us_equity_strategies.backtest.session_asof_contract import SessionClose
from us_equity_strategies.backtest.tqqq_concrete_simulator import TqqqSimulatorError, simulate_tqqq
import us_equity_strategies.backtest.tqqq_concrete_simulator as simulator

SYMBOLS = ["BOXX", "DGRO", "QQQ", "QQQI", "QQQM", "SCHD", "SGOV", "SPYI", "TQQQ"]


def bars(days=210):
    dates = pd.date_range("2024-01-02", periods=days, freq="B")
    rows = []
    for i, current in enumerate(dates):
        for j, symbol in enumerate(SYMBOLS):
            price = 100 + j + i * 0.02
            rows.append({"date": current.date(), "symbol": symbol, "open": price, "close": price + 0.1})
    return pd.DataFrame(rows)


def sessions_for(frame, start, end):
    dates = sorted(set(frame["date"]))
    return tuple(SessionClose(day, f"{day}T{'20' if 3 <= day.month <= 10 else '21'}:00:00.000000Z") for day in dates if start <= day <= end)


def run(frame=None, start=None, end=None):
    frame = bars() if frame is None else frame
    start = start or sorted(set(frame["date"]))[-5]
    end = end or sorted(set(frame["date"]))[-1]
    return simulate_tqqq(frame, sessions_for(frame, start, end), requested_start_date=start, requested_end_date=end, source_revision="prices-v1", computed_at="2024-01-01T00:00:00.000000Z")


def test_deterministic_result_and_mutation_isolation():
    frame = bars()
    result = run(frame)
    assert result.canonical_bytes() == run(frame.copy()).canonical_bytes()
    frame.iloc[0, frame.columns.get_loc("close")] = 9999
    assert result.equity_curve[0][1] != 9999
    assert result.as_of == result.observed_end_date


def test_window_end_and_metrics():
    result = run()
    assert result.requested_end_date == result.observed_end_date == result.as_of
    assert len(result.daily_returns) == result.observation_count - 1
    assert all(value is None or value == value and abs(value) != float("inf") for value in result.metrics.values())


def test_unsorted_duplicate_missing_nonfinite_and_negative_fail_closed():
    frame = bars()
    with pytest.raises(TqqqSimulatorError):
        run(frame.iloc[::-1].reset_index(drop=True))
    with pytest.raises(TqqqSimulatorError):
        run(pd.concat([frame, frame.iloc[[0]]], ignore_index=True))
    with pytest.raises(TqqqSimulatorError):
        run(frame.assign(close=float("nan")))
    with pytest.raises(TqqqSimulatorError):
        run(frame.assign(open=-1.0))
    with pytest.raises(TqqqSimulatorError):
        run(frame.drop(columns=["open"]))


def test_missing_requested_end_rejected():
    frame = bars()
    last = sorted(set(frame["date"]))[-1]
    with pytest.raises(TqqqSimulatorError):
        simulate_tqqq(frame, sessions_for(frame, sorted(set(frame["date"]))[-5], last - timedelta(days=1)), requested_start_date=sorted(set(frame["date"]))[-5], requested_end_date=last, source_revision="v1", computed_at="2024-01-01T00:00:00.000000Z")


def test_gap_revalues_existing_holdings_before_ratio_allocation(monkeypatch):
    frame = bars()
    days = sorted(set(frame["date"]))[-4:]
    frame.loc[(frame.date == days[1]) & (frame.symbol == "TQQQ"), ["open", "close"]] = (100.0, 100.0)
    frame.loc[(frame.date == days[2]) & (frame.symbol == "TQQQ"), ["open", "close"]] = (200.0, 200.0)
    frame.loc[(frame.date == days[3]) & (frame.symbol == "TQQQ"), ["open", "close"]] = (200.0, 300.0)
    monkeypatch.setattr(simulator, "build_rebalance_plan", lambda *args, **kwargs: {"target_values": {"TQQQ": args[1].total_equity * 0.5}})
    result = simulate_tqqq(frame, sessions_for(frame, days[0], days[3]), requested_start_date=days[0], requested_end_date=days[3], source_revision="v1", computed_at="2024-01-01T00:00:00.000000Z")
    assert result.equity_curve[-1][1] == pytest.approx(187_500.0)


def test_future_close_does_not_change_observed_curve():
    frame = bars()
    dates = sorted(set(frame["date"]))
    start, end = dates[-5], dates[-3]
    result = run(frame, start=start, end=end)
    changed = frame.copy()
    changed.loc[changed["date"] > end, "close"] *= 100
    assert run(changed, start=start, end=end).equity_curve == result.equity_curve
