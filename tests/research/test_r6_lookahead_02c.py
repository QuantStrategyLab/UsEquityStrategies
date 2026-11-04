"""R6 positive and deliberately leaky controls, using only artificial series."""
import socket

import pandas as pd
import pytest

from us_equity_strategies.research.lookahead_smoke import assert_no_lookahead
from us_equity_strategies.strategies.global_etf_rotation import compute_13612w_momentum


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def prices():
    index = pd.date_range("2030-01-01", periods=800, tz="UTC")
    return pd.Series([100.0 + .1 * i for i in range(len(index))], index=index)


@pytest.mark.parametrize("factor", [0.1, 10.0])
def test_r6_unanchored_native_momentum_is_caught(prices, factor):
    cutoff = prices.index[600]
    with pytest.raises(AssertionError, match="^LEAKAGE_DETECTED$"):
        assert_no_lookahead(compute_13612w_momentum, prices, cutoff=cutoff, future_factor=factor)


@pytest.mark.parametrize("factor", [0.1, 10.0])
@pytest.mark.parametrize("between_bars", [False, True])
def test_r6_anchored_native_momentum_preserves_cutoff_prefix(prices, factor, between_bars):
    cutoff = prices.index[600] + pd.Timedelta(hours=12 if between_bars else 0)
    original = prices.copy(deep=True)
    seen = []
    def signal(values):
        seen.append(values.copy(deep=True))
        return compute_13612w_momentum(values, as_of_date=cutoff)
    result = assert_no_lookahead(signal, prices, cutoff=cutoff, future_factor=factor)
    assert result.status == "NO_LEAKAGE" and result.baseline > 0
    assert result.baseline == result.mutated
    assert result.no_order is True and result.promotion_eligible is False
    pd.testing.assert_series_equal(seen[0].loc[:cutoff], seen[1].loc[:cutoff])
    future = prices.index > cutoff
    assert future.any()
    pd.testing.assert_series_equal(seen[1].loc[future], original.loc[future] * factor)
    pd.testing.assert_series_equal(prices, original)


def test_r6_centered_window_leak_is_caught(prices):
    cutoff = prices.index[600]
    with pytest.raises(AssertionError, match="^LEAKAGE_DETECTED$"):
        assert_no_lookahead(lambda s: s.rolling(3, center=True).mean().loc[cutoff],
                            prices, cutoff=cutoff)


@pytest.mark.parametrize("case", ["no_future", "zero_future", "no_signal", "no_op"])
def test_r6_vacuous_smoke_cannot_pass(prices, case):
    cutoff = prices.index[600]
    def signal(s):
        return compute_13612w_momentum(s, as_of_date=cutoff)
    factor = 10.0
    expected = "LOOKAHEAD_SMOKE_NO_FUTURE_MUTATION"
    error = AssertionError
    if case == "no_future":
        prices = prices.loc[:cutoff]
    elif case == "zero_future":
        prices = prices.copy()
        prices.loc[prices.index > cutoff] = 0.0
    elif case == "no_signal":
        def signal(s):
            return None
        expected = "NO_TRIGGER_FALSE_NEGATIVE_RISK"
    else:
        factor = 1.0
        expected = "LOOKAHEAD_SMOKE_MUTATION_INVALID"
        error = ValueError
    with pytest.raises(error, match=f"^{expected}$"):
        assert_no_lookahead(signal, prices, cutoff=cutoff, future_factor=factor)
