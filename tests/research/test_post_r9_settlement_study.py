"""Synthetic acceptance for the post-R9 date-effective settlement study."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


def _modules():
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(root))
    modules = {}
    for name in ("r7_joint_account_compare", "r8_joint_allocation_compare",
                 "r9_frozen_policy_validation", "post_r9_settlement_study"):
        spec = importlib.util.spec_from_file_location(name, root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


def _flat_rows(days: tuple[str, ...], *, qqqm_open: dict[str, float] | None = None) -> list[dict]:
    modules = _modules()
    rows = []
    for day in days:
        row = {"date": day}
        for symbol in modules["r7_joint_account_compare"].SYMBOLS:
            price = 100.0
            if symbol == "QQQM" and qqqm_open and day in qqqm_open:
                price = qqqm_open[day]
            row[symbol.lower() + "_open"] = price
            row[symbol.lower() + "_close"] = 50.0 if symbol == "BOXX" and day >= "2024-05-28" else price
        rows.append(row)
    return rows


def _state(day: str, nav: float) -> dict:
    return {"date": day, "path": "B0", "economic_nav_close_usd": nav, "total_cost_usd": 0.0,
            "shortages": [], "trade_shares": {}, "owner_shares": {}, "owner_settled_cash_usd": {},
            "owner_pending_sale_usd": {}, "owner_transfers_usd": {}, "dividend_paid_usd": 0.0,
            "sale_proceeds_released_usd": 0.0}


def test_mapping_trade_date_and_account_state_date_are_reported_separately() -> None:
    study = _modules()["post_r9_settlement_study"]
    policy = study._settlement_policy()
    holiday_rows = [{"date": day} for day in ("2023-10-05", "2023-10-06", "2023-10-09",
                                              "2023-10-10", "2023-10-11")]
    holiday = [_state(day, 1.0) for day in ("2023-10-06", "2023-10-09", "2023-10-10", "2023-10-11")]
    changed = [dict(row) for row in holiday]
    changed[2]["economic_nav_close_usd"] = 2.0
    report = study._common_settlement_prefix(holiday, changed, holiday_rows, policy)
    assert report["first_release_observation_mapping_difference_trade_date"] == "2023-10-06"
    assert report["first_account_state_difference_date"] == "2023-10-10"
    assert (report["first_release_observation_mapping_difference_trade_date"]
            != report["first_account_state_difference_date"])
    assert report["ledger_sessions_before_release_observation_mapping_difference"] == 0
    assert report["ledger_sessions_before_account_state_difference"] == 2
    assert report["joint_prefix_sessions"] == 0
    assert "first_fork_date" not in report and "reason" not in report
    early_rows = [{"date": day} for day in ("2023-03-27", "2023-03-28", "2023-03-29", "2023-03-30",
                                            "2023-03-31", "2023-04-03")]
    left = [_state(day, 1.0) for day in ("2023-03-28", "2023-03-29", "2023-03-30")]
    right = [dict(row) for row in left]
    right[1]["economic_nav_close_usd"] = 2.0
    state = study._common_settlement_prefix(left, right, early_rows, policy)
    matched = study._common_settlement_prefix(left, [dict(row) for row in left], early_rows, policy)
    assert state["first_release_observation_mapping_difference_trade_date"] is None
    assert state["first_account_state_difference_date"] == "2023-03-29"
    assert state["ledger_sessions_before_account_state_difference"] == 1
    assert state["joint_prefix_sessions"] == 1
    assert matched["first_release_observation_mapping_difference_trade_date"] is None
    assert matched["first_account_state_difference_date"] is None
    assert matched["ledger_sessions_before_release_observation_mapping_difference"] == len(left)
    assert matched["ledger_sessions_before_account_state_difference"] == len(left)
    assert matched["joint_prefix_sessions"] == len(left)


def test_suffix_price_change_does_not_change_completed_sessions() -> None:
    modules = _modules()
    r7 = modules["r7_joint_account_compare"]
    study = modules["post_r9_settlement_study"]
    policy = study._settlement_policy()
    days = ("2024-05-23", "2024-05-24", "2024-05-28", "2024-05-29")
    rows = _flat_rows(days)
    path = {"qqqm": 0.5, "tqqq_cap": 0.0, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01}
    account = {"candidate_id": "synthetic", "paths": {"B0": path}, "initial_research_nav_usd": 10000.0,
               "data": {"first_signal": "2024-05-23", "first_trade": "2024-05-24",
                        "last_session": "2024-05-29", "expected_replay_sessions": 3}}
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in r7.SYMBOLS}
    _metrics, before = r7._replay(rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
                                  settlement_policy=policy)
    rows[-1]["qqqm_close"] = 130.0
    _metrics, after = r7._replay(rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
                                 settlement_policy=policy)
    assert after[:-1] == before[:-1]
    assert after[-1]["economic_nav_close_usd"] != before[-1]["economic_nav_close_usd"]


def test_runner_writes_only_two_new_ledgers_and_can_report_a_worse_result(tmp_path: Path, monkeypatch) -> None:
    modules = _modules()
    study = modules["post_r9_settlement_study"]
    r9 = modules["r9_frozen_policy_validation"]

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("r9.analyze")

    monkeypatch.setattr(r9, "analyze", _forbidden)
    seen = {}

    def _inputs(*_args, **_kwargs):
        rows = [{"date": day} for day in ("2024-05-23", "2024-05-24", "2024-05-28")]
        return rows, {}, {}, {"tqqq_contract": {}}

    def _future(_root, rows, actions, _indicators):
        merged = [*rows, {"date": "2024-05-29"}]
        return merged, actions, {"last_extension_session": "2024-05-29"}

    def _selector(*_args, **kwargs):
        seen["selector_policy"] = kwargs.get("settlement_policy", _args[-1] if _args else None)
        return lambda *_a, **_k: {"selected_action": "B0"}

    def _replay(*_args, **kwargs):
        seen.setdefault("replay_policies", []).append(kwargs["settlement_policy"])
        seen.setdefault("selectors", []).append(kwargs.get("action_selector"))
        nav = 90.0 if kwargs.get("action_selector") else 80.0
        ledger = []
        for day in ("2024-05-24", "2024-05-28", "2024-05-29"):
            ledger.append({
                "date": day, "path": "B0", "economic_nav_close_usd": nav,
                "total_cost_usd": 1.0, "shortages": [], "trade_shares": {},
                "owner_shares": {}, "owner_settled_cash_usd": {},
                "owner_pending_sale_usd": {}, "owner_transfers_usd": {},
                "owner_security_values_usd": {"outer": {"QQQM": nav / 2}},
                "dividend_paid_usd": 0.0, "sale_proceeds_released_usd": 0.0,
                "settlement_cash_releases": [
                    {"settlement_date": day, "observation_date": day, "amount": 1.0}],
            })
        return {"cumulative_return": nav / 100.0 - 1.0}, ledger

    monkeypatch.setattr(modules["r7_joint_account_compare"], "_load_inputs", _inputs)
    monkeypatch.setattr(modules["r7_joint_account_compare"], "_replay", _replay)
    monkeypatch.setattr(modules["r8_joint_allocation_compare"], "_selector", _selector)
    monkeypatch.setattr(r9, "_verified_future", _future)
    def _legacy(day: str, nav: float, path_name: str) -> dict:
        row = _state(day, nav)
        row["path"] = path_name
        row["owner_security_values_usd"] = {"outer": {"QQQM": nav / 2}}
        return row

    def _archive(_root, _expected_sha, keys):
        if keys == {"B0_10bps"}:
            return {}, {"B0_10bps": [_legacy("2024-05-24", 100.0, "B0"),
                                      _legacy("2024-05-28", 100.0, "B0")]}
        if keys == {"dynamic_10bps"}:
            return {}, {"dynamic_10bps": [_legacy("2024-05-24", 100.0, "B1"),
                                           _legacy("2024-05-28", 100.0, "B0")]}
        return {}, {"B0_10bps": [_legacy("2024-05-29", 100.0, "B0")],
                    "dynamic_10bps": [_legacy("2024-05-29", 100.0, "B0")]}

    monkeypatch.setattr(r9, "_load_archive", _archive)
    output = tmp_path / "out"
    result = study.run(tmp_path / "raw", tmp_path / "r6", tmp_path / "materialized.json",
                       tmp_path / "r7", tmp_path / "r8", tmp_path / "r9", tmp_path / "future",
                       output)
    assert seen["selector_policy"] is seen["replay_policies"][0]
    assert seen["replay_policies"][0] is seen["replay_policies"][1]
    assert seen["selectors"][0] is None and seen["selectors"][1] is not None
    assert result["status"] == "COMPLETE"
    assert sorted(path.name for path in output.iterdir()) == [
        "private_daily_B0_10bps.json", "private_daily_dynamic_10bps.json", "summary.json"]
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["attribution"] == "settlement_policy_only"
    assert summary["broker_buying_power_inferred"] is False
    assert set(summary["paths"]) == {"B0_10bps", "dynamic_10bps"}
    b0 = summary["paths"]["B0_10bps"]
    assert b0["cumulative_return"] < 0
    assert b0["versus_legacy"]["cumulative_return_difference"] < 0
    for item in summary["paths"].values():
        assert {"settlement_observation_counts", "ending_securities", "shortage_event_count",
                "cumulative_return", "max_drawdown", "drawdown_recovery", "total_fees_usd",
                "action_counts", "versus_legacy"} <= set(item)
    with pytest.raises(FileExistsError):
        study.run(tmp_path / "raw", tmp_path / "r6", tmp_path / "materialized.json",
                  tmp_path / "r7", tmp_path / "r8", tmp_path / "r9", tmp_path / "future",
                  output)


def test_owner_fee_split_dividend_settlement_and_checkpoint_restore() -> None:
    modules = _modules()
    r7 = modules["r7_joint_account_compare"]
    study = modules["post_r9_settlement_study"]
    policy = study._settlement_policy()
    days = ("2024-05-23", "2024-05-24", "2024-05-28", "2024-05-29")
    rows = _flat_rows(days, qqqm_open={"2024-05-28": 200.0, "2024-05-29": 200.0})
    for row in rows:
        if row["date"] >= "2024-05-28":
            row["boxx_open"] = 50.0
            row["boxx_close"] = 50.0
    path = {"qqqm": 0.5, "tqqq_cap": 0.0, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01}
    account = {"candidate_id": "synthetic", "paths": {"B0": path}, "initial_research_nav_usd": 10000.0,
               "data": {"first_signal": "2024-05-23", "first_trade": "2024-05-24",
                        "last_session": "2024-05-29", "expected_replay_sessions": 3}}
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in r7.SYMBOLS}
    actions["BOXX"] = {
        "forward_splits": [{"symbol": "BOXX", "ex_date": "2024-05-28", "old_rate": 1, "new_rate": 2}],
        "cash_dividends": [{"symbol": "BOXX", "ex_date": "2024-05-28", "rate": 1.0,
                            "process_date": "2024-05-29", "payable_date": "2024-05-29"}],
    }
    _metrics, full = r7._replay(rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
                                settlement_policy=policy)
    assert sum(row["total_cost_usd"] for row in full) > 0
    assert full[1]["split_ratios"]["BOXX"] == 2.0
    assert full[1]["dividend_accrued_usd"] > 0
    assert sum(row["dividend_paid_usd"] for row in full) == pytest.approx(full[1]["dividend_accrued_usd"])
    assert sum(row["dividend_paid_usd"] > 0 for row in full) == 1
    assert full[0]["owner_settled_cash_usd"]["tqqq"] == 0.0
    assert full[0]["owner_settled_cash_usd"]["soxl"] == 0.0
    checkpoint = {}
    r7._replay(rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
               settlement_policy=policy, short_sessions=2, checkpoint_out=checkpoint)
    pending = [item for book in checkpoint["books"].values() for item in book["pending"]]
    assert pending
    assert all(item["settlement_date"] == "2024-05-29" and "release" not in item for item in pending)
    assert full[1]["dividend_accrued_usd"] == pytest.approx(
        2 * full[0]["owner_shares"]["outer"]["BOXX"])
    assert checkpoint["books"]["outer"]["shares"]["BOXX"] >= 2 * full[0]["owner_shares"]["outer"]["BOXX"]
    resume_account = {**account, "data": {**account["data"], "last_session": checkpoint["last_date"]}}
    _metrics, resumed = r7._replay(
        rows, actions, {}, {}, resume_account, path_name="B0", cost_bps=10, settlement_policy=policy,
        continuation_from_session=checkpoint["last_date"], continuation_last_session="2024-05-29",
        continuation_checkpoint=checkpoint)
    assert resumed == full[-len(resumed):]
    again = {owner: r7._book(book["cash"], r7.OWNER_SYMBOLS[owner])
             for owner, book in checkpoint["books"].items()}
    for owner, book in checkpoint["books"].items():
        again[owner]["shares"] = dict(book["shares"])
        again[owner]["pending"] = [dict(item) for item in book["pending"]]
        again[owner]["claims"] = [dict(item) for item in book["claims"]]
    assert r7._release(again, "2024-05-29", 3, policy)[0] == pytest.approx(
        sum(item["amount"] for item in pending))
    assert r7._release(again, "2024-05-29", 3, policy)[0] == 0.0
