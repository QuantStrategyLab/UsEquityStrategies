"""Invented fixtures only; no market data, optional artifacts or broker path."""
from copy import deepcopy
from datetime import date, timedelta
import math
import random

import pytest

from us_equity_strategies.research.rs04_b3_live import default_config
from us_equity_strategies.research.rs04_b3_runner import Ledger, Session
from us_equity_strategies.research.soxl_v7_derisk import (
    assert_exposure,
    book_weights,
    paired_synthetic_replay,
    strict_rebalance,
    variant_config,
)


@pytest.mark.parametrize("cost_multiplier", [1., 2.])
def test_same_native_config_without_optional_plugins_preserves_paired_books(cost_multiplier):
    # This comparison isolates the ledger: both rules receive the same native
    # derisk configuration. It does not assert equality to the legacy config.
    config = variant_config(default_config("SOXL"))
    assert variant_config(config) == config
    indicators = {s: {
        "price": 120., "ma_trend": 100., "ma20": 110., "ma20_slope": 1.,
        "rsi14": 50., "bb_upper": 150., "realized_volatility_10": .1,
    } for s in ("soxl", "soxx")}
    symbols = ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI")
    rows = [Session(
        session=date(2001, 1, 1) + timedelta(days=i),
        prices={s: 100. for s in symbols}, features=None,
        live_indicators=indicators, context={},
    ) for i in range(6)]
    points = paired_synthetic_replay(rows, config, cost_multiplier=cost_multiplier)
    for point in points:
        # Includes NAV, cash, holdings weights, net return and each fee leg.
        assert point["baseline"] == point["candidate"]


@pytest.mark.parametrize("cost_multiplier", [1., 2.])
@pytest.mark.parametrize("initial_nav", [10000., 100000., 500000.])
def test_identical_books_preserve_every_share_cash_receivable_and_fee(cost_multiplier, initial_nav):
    rng = random.Random(2305)
    baseline = Ledger(initial_nav, {}, {})
    candidate = deepcopy(baseline)
    symbols = ("SOXL", "SOXX", "SCHD", "DGRO", "SPYI", "QQQI", "BOXX")
    prices = {s: 100. for s in symbols}
    for i in range(60):
        day = date(2001, 1, 1)+timedelta(days=i)
        if i == 20:
            prices["SOXL"] /= 2
        for book in (baseline, candidate):
            if i == 20:
                book.split("SOXL", 2.)
            if i % 10 == 5:
                book.accrue("SOXL", .25, day+timedelta(days=2))
            book.pay(day)
        prices = {s: p*rng.uniform(.85, 1.15) for s, p in prices.items()}
        targets = {s: rng.uniform(0., .1) for s in symbols}
        targets["SOXL"] = rng.uniform(0., .35)
        targets["BOXX"] += .97-math.fsum(targets.values())
        expected_fees = baseline.rebalance(targets, prices, profile="SOXL", cost_multiplier=cost_multiplier)
        actual_fees = strict_rebalance(candidate, targets, baseline, prices, cost_multiplier=cost_multiplier)
        assert candidate == baseline
        assert actual_fees == expected_fees
        assert candidate.cash >= .03*candidate.nav(prices)-1e-7


@pytest.mark.parametrize("cost_multiplier", [1., 2.])
@pytest.mark.parametrize("candidate_cash", [35001., 200003.])
def test_constrained_refill_keeps_component_dollar_caps_and_charges_only_final_trades(
        cost_multiplier, candidate_cash):
    prices = {"SOXL": 71.17, "SOXX": 203.13, "BOXX": 103.18, "SCHD": 37.21}
    baseline = Ledger(100003., {}, {})
    baseline.rebalance({"SOXL": .2, "SOXX": .1, "BOXX": .4}, prices,
                       profile="SOXL", cost_multiplier=cost_multiplier)
    candidate = Ledger(candidate_cash, {}, {date(2001, 1, 3): 111.})
    before = deepcopy(candidate)
    fees = strict_rebalance(candidate, {"SOXL": .35, "SOXX": .25, "SCHD": .1, "BOXX": .27},
                            baseline, prices, cost_multiplier=cost_multiplier)
    bn, cn = baseline.nav(prices), candidate.nav(prices)
    assert_exposure(book_weights(candidate, prices), book_weights(baseline, prices),
                    baseline_nav=bn, candidate_nav=cn)
    assert candidate.quantity.get("SCHD", 0) == 0
    for symbol in ("SOXL", "SOXX"):
        assert candidate.quantity.get(symbol, 0) <= baseline.quantity[symbol]
    assert candidate.cash >= .03*cn-1e-7
    assert candidate.receivables == before.receivables
    assert fees == {k: math.fsum(f[k] for f in candidate.last_fills) for k in ("commission", "slippage")}
    assert cn == pytest.approx(before.nav(prices)-math.fsum(fees.values()), abs=1e-8)
    assert candidate.cash == pytest.approx(
        before.cash-math.fsum(f["change"]*prices[f["symbol"]] for f in candidate.last_fills)
        -math.fsum(fees.values()), abs=1e-8)


@pytest.mark.parametrize("cost_multiplier", [1., 2.])
def test_stronger_settled_cash_reserve_is_preserved(cost_multiplier):
    prices = {"SOXL": 71.17, "SOXX": 203.13, "BOXX": 103.18}
    baseline = Ledger(100003., {}, {})
    baseline.rebalance({"SOXL": .35, "SOXX": .2, "BOXX": .42}, prices,
                       profile="SOXL", cost_multiplier=cost_multiplier)
    candidate = Ledger(100003., {}, {date(2001, 1, 3): 111.})
    strict_rebalance(candidate, {"SOXL": .35, "SOXX": .2, "BOXX": .42}, baseline, prices,
                     cost_multiplier=cost_multiplier, reserve=.2)
    assert candidate.cash >= .2*candidate.nav(prices)-1e-7


def test_failed_post_fill_guard_does_not_commit_provisional_trades():
    # This book cannot provide a 90% settled-cash reserve: most wealth is an
    # unpaid receivable. A rejected research path must leave its book intact.
    candidate = Ledger(100., {}, {date(2001, 1, 3): 900.})
    before = deepcopy(candidate)
    with pytest.raises(ValueError, match="CASH_RESERVE_INVARIANT"):
        strict_rebalance(candidate, {}, Ledger(1000., {}, {}), {"BOXX": 100.},
                         cost_multiplier=1., reserve=.9)
    assert candidate == before
