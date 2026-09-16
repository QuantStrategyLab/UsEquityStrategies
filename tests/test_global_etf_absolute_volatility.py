from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from us_equity_strategies.research import global_etf_absolute_volatility as candidate


def history(amplitude=0.03):
    dates = pd.bdate_range(end="2024-06-28", periods=127)
    returns = np.resize([-amplitude, amplitude], 126)
    prices = 100 * np.cumprod(np.r_[1.0, 1 + returns])
    return pd.DataFrame([
        {"date": day, "symbol": symbol, "close": price}
        for symbol in ("AAA", "BBB", "BIL")
        for day, price in zip(dates, prices if symbol != "BIL" else np.ones(127) * 100)
    ])


def run(frame, weights=None, mode="rebalance", **kwargs):
    with patch.object(candidate, "build_target_weights", return_value=(
        weights if weights is not None else {"AAA": 0.6, "BBB": 0.2, "BIL": 0.2}, {"mode": mode}
    )):
        return candidate.build_research_target_weights(frame, **kwargs)


def test_high_volatility_matches_hand_calculation_and_preserves_weights():
    weights, meta = run(history())
    sigma = np.std(np.resize([-0.03, 0.03], 126) * 0.8, ddof=1) * np.sqrt(252)
    scale = 0.15 / sigma
    assert weights == pytest.approx({"AAA": 0.6 * scale, "BBB": 0.2 * scale, "BIL": 1 - 0.8 * scale})
    assert sum(weights.values()) == pytest.approx(1)
    assert meta["absolute_volatility_scale"] == pytest.approx(scale)


@pytest.mark.parametrize("amplitude", [0, 0.001])
def test_low_or_zero_volatility_does_not_increase_exposure(amplitude):
    assert run(history(amplitude))[0] == {"AAA": 0.6, "BBB": 0.2, "BIL": 0.2}


@pytest.mark.parametrize("mode,weights", [("hold", {}), ("emergency", {"BIL": 1}), ("safe_haven", {"BIL": 1})])
def test_non_rebalance_is_unchanged(mode, weights):
    assert run(pd.DataFrame(), weights, mode) == (weights, {"mode": mode})


def test_one_risky_asset_and_future_rows():
    frame = history()
    cutoff = frame.date.max()
    expected = run(frame, {"AAA": 1}, as_of_date=cutoff)
    future = frame.tail(1).assign(date=pd.Timestamp("2025-01-01"), close=1e9)
    actual = run(pd.concat([frame, future]), {"AAA": 1}, as_of_date=cutoff)
    assert actual == expected
    assert 0 < actual[0]["AAA"] < 1


@pytest.mark.parametrize("bad", ["short", "missing", "duplicate", "nan", "inf", "zero", "negative"])
def test_incomplete_recent_history_is_rejected(bad):
    frame = history()
    index = frame.index[frame.symbol == "AAA"][-2]
    if bad == "short":
        frame = frame[frame.date > frame.date.min()]
    elif bad == "missing":
        frame = frame.drop(index)
    elif bad == "duplicate":
        frame = pd.concat([frame, frame.loc[[index]]])
    else:
        frame.loc[index, "close"] = {"nan": np.nan, "inf": np.inf, "zero": 0, "negative": -1}[bad]
    with pytest.raises(ValueError):
        run(frame)
