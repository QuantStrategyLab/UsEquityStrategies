from datetime import date

import pandas as pd
import pytest

from us_equity_strategies.backtest.tqqq_research_simulator import (
    CONTRACT,
    TqqqResearchError,
    simulate_tqqq_research,
)
import us_equity_strategies.backtest.tqqq_research_simulator as simulator


SYMBOLS = ["BOXX", "DGRO", "QQQ", "QQQI", "QQQM", "SCHD", "SGOV", "SPYI", "TQQQ"]


def bars(days=230):
    dates = pd.date_range("2023-01-02", periods=days, freq="B")
    rows = []
    for i, current in enumerate(dates):
        for j, symbol in enumerate(SYMBOLS):
            price = 100.0 + j + i * 0.03
            rows.append({"date": current.date(), "symbol": symbol, "open": price, "close": price + 0.1})
    return pd.DataFrame(rows)


def run(frame=None, **kwargs):
    return simulate_tqqq_research(
        bars() if frame is None else frame,
        start_date=date(2023, 11, 6),
        end_date=date(2023, 11, 17),
        source_revision="prices-v1",
        computed_at="2024-01-01T00:00:00.000000Z",
        **kwargs,
    )


def test_deterministic_wire_and_column_permutation_and_mutation_isolation():
    frame = bars()
    result = run(frame)
    repeat = run(frame.copy())
    assert result.canonical_bytes() == repeat.canonical_bytes()
    assert result.contract == CONTRACT
    assert result.observation_count == 10
    frame.iloc[0, frame.columns.get_loc("close")] = 9999.0
    assert result.equity_curve[0][1] != 9999.0


def test_boundary_and_metrics_are_finite():
    result = run()
    assert result.as_of == date(2023, 11, 17)
    assert len(result.daily_returns) == result.observation_count - 1
    assert all(value is None or value == value and abs(value) != float("inf") for value in result.metrics.values())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.drop(columns=["open"]),
        lambda f: f.assign(close=float("nan")),
        lambda f: f.assign(open=-1.0),
        lambda f: pd.concat([f, f.iloc[[0]]], ignore_index=True),
        lambda f: f.iloc[::-1].reset_index(drop=True),
    ],
)
def test_invalid_bars_fail_closed(mutate):
    with pytest.raises(TqqqResearchError):
        run(mutate(bars()))


def test_missing_warmup_and_invalid_contract_metadata_fail_closed():
    with pytest.raises(TqqqResearchError):
        run(bars(100))
    with pytest.raises(TqqqResearchError):
        simulate_tqqq_research(bars(), start_date="2023-11-06", end_date="2023-11-17", source_revision="", computed_at="2024-01-01T00:00:00.000000Z")
    with pytest.raises(TqqqResearchError):
        simulate_tqqq_research(bars(), start_date="2023-11-06", end_date="2023-11-17", source_revision="v1", computed_at="2024-01-01T00:00:00Z")


def test_same_date_symbol_unsorted_fails_closed():
    frame = bars()
    current = frame["date"].iloc[0]
    rows = frame.index[frame["date"] == current].tolist()
    frame.iloc[rows] = frame.iloc[list(reversed(rows))].to_numpy()
    with pytest.raises(TqqqResearchError):
        run(frame)


def test_next_open_revalues_existing_holdings_before_target_allocation(monkeypatch):
    frame = bars()
    dates = sorted(frame["date"].unique())[-4:]
    frame.loc[(frame["date"] == dates[1]) & (frame["symbol"] == "TQQQ"), ["open", "close"]] = (100.0, 100.0)
    frame.loc[(frame["date"] == dates[2]) & (frame["symbol"] == "TQQQ"), ["open", "close"]] = (200.0, 200.0)
    frame.loc[(frame["date"] == dates[3]) & (frame["symbol"] == "TQQQ"), ["open", "close"]] = (200.0, 300.0)
    calls = []

    def fixed_plan(*args, **kwargs):
        calls.append(1)
        return {"target_values": {"TQQQ": args[1].total_equity * 0.5}}

    monkeypatch.setattr(simulator, "build_rebalance_plan", fixed_plan)
    result = simulate_tqqq_research(frame, start_date=pd.Timestamp(dates[0]).date(), end_date=pd.Timestamp(dates[3]).date(), source_revision="v1", computed_at="2024-01-01T00:00:00.000000Z")
    assert len(calls) == 4
    assert result.equity_curve[-1][1] == pytest.approx(187_500.0)


def test_future_close_after_window_does_not_change_result():
    first = run()
    frame = bars()
    frame.loc[frame["date"] > date(2023, 11, 17), "close"] *= 100
    changed = run(frame)
    assert changed.equity_curve == first.equity_curve
    assert changed.metrics == first.metrics
