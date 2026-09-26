"""Synthetic counterexamples for the R7 owner and execution contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _module():
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("r7_joint_account_compare", root / "r7_joint_account_compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_boxx_owners_are_distinct_and_aggregate_once() -> None:
    m = _module()
    books = {"outer": m._book(100, m.OWNER_SYMBOLS["outer"]),
             "tqqq": m._book(0, m.OWNER_SYMBOLS["tqqq"]),
             "soxl": m._book(50, m.OWNER_SYMBOLS["soxl"])}
    books["outer"]["shares"]["BOXX"] = 2
    books["soxl"]["shares"]["BOXX"] = 1
    prices = {symbol: 100.0 for symbol in m.SYMBOLS}
    assert sum(m._book_value(book, prices) for book in books.values()) == 450
    books["soxl"]["shares"]["BOXX"] = 0
    assert books["outer"]["shares"]["BOXX"] == 2


def test_fee_and_unsettled_sale_cannot_fund_purchase() -> None:
    m = _module()
    books = {"outer": m._book(100, m.OWNER_SYMBOLS["outer"]),
             "tqqq": m._book(0, m.OWNER_SYMBOLS["tqqq"]),
             "soxl": m._book(0, m.OWNER_SYMBOLS["soxl"])}
    targets = {"outer": {"QQQM": 100.0, "BOXX": 0.0},
               "tqqq": {"TQQQ": 0.0},
               "soxl": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0}}
    opens = {symbol: 100.0 for symbol in m.SYMBOLS}
    trades = {owner: {symbol: 0 for symbol in symbols} for owner, symbols in m.OWNER_SYMBOLS.items()}
    fees = {owner: {symbol: 0.0 for symbol in symbols} for owner, symbols in m.OWNER_SYMBOLS.items()}
    shortages = m._buy_to_targets(books, targets, opens, "2023-03-27", 0.001,
                                   {"tqqq": 0.0, "soxl": 0.0}, 0.0, None, trades, fees)
    assert books["outer"]["shares"]["QQQM"] == 0
    assert shortages[0]["filled_shares"] == 0
    books["outer"]["shares"]["BOXX"] = 1
    sell_targets = {**targets, "outer": {"QQQM": 0.0, "BOXX": 0.0}}
    m._sell_to_targets(books, sell_targets, opens, 0.001, 0)
    assert books["outer"]["pending"][0]["amount"] == pytest.approx(99.9)
    assert m._eligible_cash(books["outer"], "2023-03-27") == 100.0
    assert m._fund_owners(books, {"tqqq": 50.0, "soxl": 0.0},
                          {"tqqq": 0.0, "soxl": 0.0}, opens, "2023-03-27",
                          100.0, None, 0.0)["tqqq"] == 0.0
    assert m._release(books, "2023-03-28", 1)[0] == 0.0
    assert m._release(books, "2023-03-29", 2)[0] == pytest.approx(99.9)


def test_split_and_dividend_preserve_owner_wealth_and_pay_once() -> None:
    m = _module()
    books = {"outer": m._book(0, m.OWNER_SYMBOLS["outer"]),
             "tqqq": m._book(0, m.OWNER_SYMBOLS["tqqq"]),
             "soxl": m._book(0, m.OWNER_SYMBOLS["soxl"])}
    books["outer"]["shares"]["BOXX"] = 2
    books["soxl"]["shares"]["BOXX"] = 1
    events = {symbol: {"splits": {}, "dividends": {}} for symbol in m.SYMBOLS}
    events["BOXX"] = {"splits": {"2023-03-28": 2.0},
                      "dividends": {"2023-03-28": [{"rate": 1.0,
                         "process_date": "2023-03-29", "payable_date": "2023-03-30"}]}}
    before = sum(m._book_value(book, {symbol: 100.0 for symbol in m.SYMBOLS}) for book in books.values())
    ratios, accrued = m._corporate_actions(books, events, "2023-03-28")
    after = sum(m._book_value(book, {symbol: 50.0 for symbol in m.SYMBOLS}) for book in books.values())
    assert ratios["BOXX"] == 2.0
    assert (books["outer"]["shares"]["BOXX"], books["soxl"]["shares"]["BOXX"]) == (4.0, 2.0)
    assert (accrued, after) == pytest.approx((6.0, before + 6.0))
    assert m._release(books, "2023-03-29", 0)[1] == 0.0
    assert m._release(books, "2023-03-30", 1)[1] == 6.0
    assert m._release(books, "2023-03-31", 2)[1] == 0.0
    assert sum(m._book_value(book, {symbol: 50.0 for symbol in m.SYMBOLS}) for book in books.values()) == after


def test_member_equity_counts_pending_once_and_funding_does_not_double_count() -> None:
    m = _module()
    books = {"outer": m._book(1000, m.OWNER_SYMBOLS["outer"]),
             "tqqq": m._book(0, m.OWNER_SYMBOLS["tqqq"]),
             "soxl": m._book(0, m.OWNER_SYMBOLS["soxl"])}
    books["soxl"]["pending"].append({"amount": 500.0, "release": 2})
    prices = {symbol: 100.0 for symbol in m.SYMBOLS}
    assert m._book_decision_equity(books["soxl"], "2023-03-27", prices) == 500.0
    transfers = m._fund_owners(books, {"tqqq": 0.0, "soxl": 500.0},
                               {"tqqq": 0.0, "soxl": 500.0}, prices,
                               "2023-03-27", 100.0, {"reserved_cash": 15.0}, 0.0)
    assert transfers["soxl"] == 0.0
    assert books["outer"]["cash"] == 1000.0
