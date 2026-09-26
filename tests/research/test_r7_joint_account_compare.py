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


def _settlement_policy():
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    return _module()._load_settlement_policy(
        root / "post_r9_date_effective_settlement_policy.v1.json")


def test_date_effective_settlement_uses_weekday_dtc_calendar_not_row_index() -> None:
    m = _module()
    policy = _settlement_policy()
    assert policy["policy_id"] != "r9"
    assert "2023-11-10" not in policy["dtc_non_settlement_dates"]
    carter = next(item for item in policy["known_settlement_only_dates"]
                  if item["date"] == "2025-01-09")
    assert carter["exchange_closed"] is True and carter["settlement_closed"] is False
    source_ids = {item["id"]: item["url"] for item in policy["official_sources"]}
    assert source_ids == {
        "sec_t1_faq": "https://www.sec.gov/exams/educationhelpguidesfaqs/t1-faq",
        "sec_t1_final_transition": "https://www.sec.gov/files/rules/final/2023/34-96930.pdf",
        "dtcc_2023_anticipated_holidays": "https://www.dtcc.com/Globals/PDFs/2022/November/22/17669-22",
        "dtcc_2024_anticipated_holidays": "https://www.dtcc.com/Globals/PDFs/2023/December/12/19404-23",
        "dtcc_2025_anticipated_holidays": "https://www.dtcc.com/Globals/PDFs/2024/October/30/20972-24",
        "dtcc_2026_anticipated_holidays": "https://www.dtcc.com/Globals/PDFs/2025/October/15/23036-25",
        "carter_2025_01_09_settlement_open": "https://www.dtcc.com/-/media/Files/pdf/2024/12/30/0275.pdf",
        "dtcc_2026_07_03_independence_day_observed":
            "https://www.dtcc.com/-/media/Files/pdf/2026/6/5/24351-26.pdf",
    }
    assert m._is_settlement_day("2024-05-25", policy) is False
    assert m._is_settlement_day("2024-05-26", policy) is False
    assert m._is_settlement_day("2024-03-29", policy) is False
    assert m._is_settlement_day("2023-10-09", policy) is False
    assert m._is_settlement_day("2024-11-11", policy) is False
    assert m._is_settlement_day("2023-11-10", policy) is True
    assert m._is_settlement_day("2025-01-09", policy) is True
    assert m._settlement_date("2024-05-24", policy) == "2024-05-29"
    assert m._settlement_date("2024-05-28", policy) == "2024-05-29"
    assert m._settlement_date("2024-03-28", policy) == "2024-04-02"
    assert m._settlement_date("2023-10-06", policy) == "2023-10-11"
    assert m._settlement_date("2024-11-08", policy) == "2024-11-12"
    assert m._settlement_date("2023-11-09", policy) == "2023-11-13"
    assert m._settlement_date("2025-01-08", policy) == "2025-01-09"
    assert m._settlement_date("2026-07-02", policy) == "2026-07-06"


def test_missing_settlement_coverage_fails_closed() -> None:
    m = _module()
    policy = _settlement_policy()
    with pytest.raises(ValueError, match="COVERAGE"):
        m._settlement_date("2023-03-27", policy)
    with pytest.raises(ValueError, match="COVERAGE"):
        m._settlement_date("2026-08-27", policy)


def test_legacy_sale_and_checkpoint_keep_release_index_only() -> None:
    m = _module()
    books = {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    books["outer"]["shares"]["QQQM"] = 1
    targets = {"outer": {"QQQM": 0.0, "BOXX": 0.0}, "tqqq": {"TQQQ": 0.0},
               "soxl": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0}}
    opens = {symbol: 100.0 for symbol in m.SYMBOLS}
    m._sell_to_targets(books, targets, opens, 0.001, 4)
    pending = books["outer"]["pending"][0]
    assert set(pending) == {"symbol", "amount", "release"}
    assert pending["release"] == 6
    rows = []
    for day in ("2024-12-27", "2024-12-30", "2024-12-31"):
        row = {"date": day}
        for symbol in m.SYMBOLS:
            row[symbol.lower() + "_open"] = 100.0
            row[symbol.lower() + "_close"] = 100.0
        rows.append(row)
    path = {"qqqm": 0.5, "tqqq_cap": 0.0, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01}
    policy = {"candidate_id": "synthetic", "paths": {"B0": path}, "initial_research_nav_usd": 10000.0,
              "data": {"first_signal": "2024-12-27", "first_trade": "2024-12-30",
                       "last_session": "2024-12-31", "expected_replay_sessions": 2}}
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in m.SYMBOLS}
    checkpoint = {}
    m._replay(rows, actions, {}, {}, policy, path_name="B0", cost_bps=10, checkpoint_out=checkpoint)
    assert set(checkpoint) == {"last_date", "last_global_index", "prior_nav_usd",
                               "previous_action", "books"}
    for book in checkpoint["books"].values():
        for item in book["pending"]:
            assert "settlement_date" not in item
            assert "release" in item


def test_new_pending_rejects_mixed_fields_and_releases_on_observation_once() -> None:
    m = _module()
    policy = _settlement_policy()
    books = {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    books["outer"]["cash"] = 50.0
    mixed = {"symbol": "QQQM", "amount": 10.0, "release": 1, "settlement_date": "2025-01-09",
             "settlement_policy_id": policy["policy_id"],
             "settlement_calendar_id": policy["calendar_id"]}
    books["outer"]["pending"].append(dict(mixed))
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        m._release(books, "2025-01-10", 3, policy)
    assert books["outer"]["cash"] == 50.0
    books["outer"]["pending"] = [{"symbol": "QQQM", "amount": 10.0, "release": 1}]
    with pytest.raises(ValueError, match="INVALID"):
        m._release(books, "2025-01-10", 3, policy)
    books["outer"]["pending"] = [{"symbol": "QQQM", "amount": 10.0,
                                   "settlement_date": "2025-01-09",
                                   "settlement_policy_id": policy["policy_id"],
                                   "settlement_calendar_id": policy["calendar_id"]}]
    with pytest.raises(ValueError, match="INVALID"):
        m._release(books, "2025-01-10", 3)
    assert m._release(books, "2025-01-08", 2, policy)[0] == 0.0
    released, _paid = m._release(books, "2025-01-10", 4, policy, released_out := [])
    assert released == pytest.approx(10.0)
    assert released_out == [{"settlement_date": "2025-01-09", "observation_date": "2025-01-10",
                             "amount": 10.0}]
    assert m._release(books, "2025-01-10", 4, policy)[0] == 0.0
    targets = {"outer": {"QQQM": 0.0, "BOXX": 0.0}, "tqqq": {"TQQQ": 0.0},
               "soxl": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0}}
    books["outer"]["shares"]["QQQM"] = 1
    m._sell_to_targets(books, targets, {symbol: 100.0 for symbol in m.SYMBOLS},
                       0.001, 7, policy, trade_day="2024-05-28")
    item = books["outer"]["pending"][0]
    assert set(item) == {"symbol", "amount", "settlement_date", "settlement_policy_id",
                         "settlement_calendar_id"}
    assert item["settlement_date"] == "2024-05-29"
    assert "release" not in item


def test_malformed_new_pending_and_checkpoint_fail_closed_without_recompute() -> None:
    m = _module()
    policy = _settlement_policy()

    def pending(**overrides) -> dict:
        item = {"symbol": "QQQM", "amount": 10.0, "settlement_date": "2024-05-30",
                "settlement_policy_id": policy["policy_id"],
                "settlement_calendar_id": policy["calendar_id"]}
        item.update(overrides)
        return item

    books = {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    books["outer"]["cash"] = 50.0
    books["outer"]["pending"] = [pending()]
    assert m._release(books, "2024-05-29", 1, policy)[0] == 0.0
    assert books["outer"]["pending"][0]["settlement_date"] == "2024-05-30"
    assert books["outer"]["cash"] == 50.0
    for bad_date in ("not-a-date", "2024/05/29", "2026-08-28", "2023-03-27"):
        books["outer"]["pending"] = [pending(settlement_date=bad_date)]
        with pytest.raises(ValueError):
            m._release(books, "2024-05-29", 1, policy)
        assert books["outer"]["pending"][0]["settlement_date"] == bad_date
        assert books["outer"]["cash"] == 50.0
    for bad_amount in (-0.01, float("nan"), float("inf"), "10", True):
        books["outer"]["pending"] = [pending(amount=bad_amount)]
        with pytest.raises(ValueError, match="INVALID"):
            m._release(books, "2024-05-29", 1, policy)
        assert books["outer"]["cash"] == 50.0
    legacy = {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    legacy["outer"]["pending"] = [{"amount": -1.0, "release": 0}]
    assert m._release(legacy, "2024-05-29", 0)[0] == -1.0
    rows = []
    for day in ("2024-05-24", "2024-05-28", "2024-05-29"):
        row = {"date": day}
        for symbol in m.SYMBOLS:
            row[symbol.lower() + "_open"] = 100.0
            row[symbol.lower() + "_close"] = 100.0
        rows.append(row)
    account = {"candidate_id": "synthetic", "paths": {"B0": {
        "qqqm": 0.5, "tqqq_cap": 0.0, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01}},
        "initial_research_nav_usd": 10000.0,
        "data": {"first_signal": "2024-05-24", "first_trade": "2024-05-28",
                 "last_session": "2024-05-28", "expected_replay_sessions": 1}}
    checkpoint = {
        "last_date": "2024-05-28", "last_global_index": 1, "prior_nav_usd": 1000.0,
        "previous_action": "B0",
        "settlement_policy_id": policy["policy_id"],
        "settlement_calendar_id": policy["calendar_id"],
        "books": {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}}
    checkpoint["books"]["outer"]["pending"] = [pending(settlement_date="not-a-date")]
    with pytest.raises(ValueError, match="INVALID"):
        m._replay(rows, {}, {}, {}, account, path_name="B0", cost_bps=10,
                  settlement_policy=policy, continuation_from_session="2024-05-28",
                  continuation_last_session="2024-05-29", continuation_checkpoint=checkpoint)
    assert checkpoint["books"]["outer"]["pending"][0]["settlement_date"] == "not-a-date"


def test_new_pending_rejects_weekend_and_frozen_non_settlement_day() -> None:
    m = _module()
    policy = _settlement_policy()

    def pending(settlement_date: str) -> dict:
        return {"symbol": "QQQM", "amount": 10.0, "settlement_date": settlement_date,
                "settlement_policy_id": policy["policy_id"],
                "settlement_calendar_id": policy["calendar_id"]}

    books = {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    books["outer"]["cash"] = 50.0
    for bad_date in ("2024-05-27", "2024-05-25"):
        item = pending(bad_date)
        with pytest.raises(ValueError, match="INVALID"):
            m._require_pending_settlement(item, policy)
        assert item["settlement_date"] == bad_date
        books["outer"]["pending"] = [item]
        with pytest.raises(ValueError, match="INVALID"):
            m._release(books, "2024-05-28", 1, policy)
        assert books["outer"]["pending"][0]["settlement_date"] == bad_date
        assert books["outer"]["cash"] == 50.0
    carter = pending("2025-01-09")
    m._require_pending_settlement(carter, policy)
    assert carter["settlement_date"] == "2025-01-09"
    books["outer"]["pending"] = [carter]
    assert m._release(books, "2025-01-08", 1, policy)[0] == 0.0
    assert books["outer"]["pending"][0]["settlement_date"] == "2025-01-09"
    assert books["outer"]["cash"] == 50.0
    legacy = {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    legacy["outer"]["pending"] = [{"amount": 4.0, "release": 0}]
    assert m._release(legacy, "2024-05-25", 0)[0] == 4.0


def test_checkpoint_restores_frozen_settlement_date_without_recompute() -> None:
    m = _module()
    policy = _settlement_policy()
    rows = []
    for day in ("2023-10-05", "2023-10-06", "2023-10-09", "2023-10-10", "2023-10-11"):
        row = {"date": day}
        for symbol in m.SYMBOLS:
            row[symbol.lower() + "_open"] = 200.0 if symbol == "QQQM" and day == "2023-10-09" else 100.0
            row[symbol.lower() + "_close"] = row[symbol.lower() + "_open"]
        rows.append(row)
    path = {"qqqm": 0.5, "tqqq_cap": 0.0, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01}
    replay_policy = {"candidate_id": "synthetic", "paths": {"B0": path},
                     "initial_research_nav_usd": 10000.0,
                     "data": {"first_signal": "2023-10-05", "first_trade": "2023-10-06",
                              "last_session": "2023-10-11", "expected_replay_sessions": 4}}
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in m.SYMBOLS}
    _metrics, full = m._replay(rows, actions, {}, {}, replay_policy, path_name="B0", cost_bps=10,
                               settlement_policy=policy)
    partial_out = {}
    m._replay(rows, actions, {}, {}, replay_policy, path_name="B0", cost_bps=10,
              settlement_policy=policy, short_sessions=3, checkpoint_out=partial_out)
    assert partial_out["settlement_policy_id"] == policy["policy_id"]
    assert partial_out["settlement_calendar_id"] == policy["calendar_id"]
    pending = [item for book in partial_out["books"].values() for item in book["pending"]]
    assert pending and all(item["settlement_date"] == "2023-10-11" and "release" not in item
                           for item in pending)
    original = m._settlement_date

    def _wrong(trade_date, settlement_policy):
        return "2099-01-01"

    m._settlement_date = _wrong
    resume_policy = {**replay_policy, "data": {**replay_policy["data"], "last_session": "2023-10-10"}}
    try:
        _metrics, resumed = m._replay(
            rows, actions, {}, {}, resume_policy, path_name="B0", cost_bps=10,
            settlement_policy=policy, continuation_from_session="2023-10-10",
            continuation_last_session="2023-10-11", continuation_checkpoint=partial_out)
    finally:
        m._settlement_date = original
    frozen_amount = sum(item["amount"] for item in pending)
    assert resumed[-1]["sale_proceeds_released_usd"] == pytest.approx(frozen_amount)
    assert any(item["settlement_date"] == "2023-10-11"
               for item in resumed[-1]["settlement_cash_releases"])
    assert all(item["settlement_date"] != "2099-01-01"
               for item in resumed[-1]["settlement_cash_releases"])
    assert resumed == full[-len(resumed):]
    books = {owner: m._book(0, symbols) for owner, symbols in m.OWNER_SYMBOLS.items()}
    for owner, book in partial_out["books"].items():
        books[owner]["pending"] = [dict(item) for item in book["pending"]]
        books[owner]["cash"] = book["cash"]
        books[owner]["shares"] = dict(book["shares"])
    assert m._release(books, "2023-10-11", 4, policy)[0] == pytest.approx(
        sum(item["amount"] for item in pending))
    assert m._release(books, "2023-10-11", 4, policy)[0] == 0.0
