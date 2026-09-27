"""Direct acceptance for the post-R9 active capital study."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from us_equity_strategies.research.c3_capital_path import smooth_bounded_capital_risk_ratio


M1_SUMMARY_SHA256 = "e0e5c2e51836edb246e70cd9ea7f4b64def713928aafd4d6933b403a69f793cc"
M1_DYNAMIC_LEDGER_SHA256 = "9ab7b28d0fa9a024c389d49d4eaa3f79adae591cd100d80411f125bf03da832e"
_CAPITAL_ONLY = {
    "wealth_reference_usd", "curve_ratio", "aggregate_member_cap_usd", "capital_binding",
    "overcap_residual_usd", "investment_index", "investment_high_water", "external_flow_usd",
    "capital_policy_id", "capital_variant",
}
_ROW_FIELDS = {
    "decision_equity_usd", "wealth_reference_usd", "curve_ratio", "aggregate_member_cap_usd",
    "member_budget_usd", "capital_binding", "path", "owner_nav_usd", "owner_security_values_usd",
    "outer_boxx_value_usd", "soxl_internal_boxx_value_usd", "owner_settled_cash_usd",
    "owner_pending_sale_usd", "owner_receivable_usd", "overcap_residual_usd", "shortages",
    "total_cost_usd", "trade_cost_usd", "owner_shares",
}


def _modules():
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(root))
    modules = {}
    for name in ("r7_joint_account_compare", "r8_joint_allocation_compare",
                 "r9_frozen_policy_validation", "post_r9_settlement_study",
                 "post_r9_capital_study"):
        spec = importlib.util.spec_from_file_location(name, root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


def _rows(days: tuple[str, ...], *, close: dict[str, float] | None = None) -> list[dict]:
    rows = []
    for day in days:
        row = {"date": day}
        for symbol in ("QQQM", "TQQQ", "SOXL", "SOXX", "BOXX"):
            row[symbol.lower() + "_open"] = 100.0
            row[symbol.lower() + "_close"] = 100.0 if close is None else close.get(day, 100.0)
        rows.append(row)
    return rows


def _account(days: tuple[str, ...], initial: float) -> dict:
    path = {
        "B0": {"qqqm": 0.50, "tqqq_cap": 0.0, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01},
        "B1": {"qqqm": 0.45, "tqqq_cap": 0.05, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01},
        "B2": {"qqqm": 0.45, "tqqq_cap": 0.0, "soxl_cap": 0.05, "outer_boxx": 0.49, "outer_cash": 0.01},
        "B3": {"qqqm": 0.45, "tqqq_cap": 0.025, "soxl_cap": 0.025, "outer_boxx": 0.49, "outer_cash": 0.01},
    }
    return {"candidate_id": "synthetic", "paths": path, "initial_research_nav_usd": initial,
            "data": {"first_signal": days[0], "first_trade": days[1],
                     "last_session": days[-1], "expected_replay_sessions": len(days) - 1}}


def _actions(symbols: tuple[str, ...]) -> dict:
    return {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in symbols}


def _selector(action: str, seen: list | None = None):
    def choose(rows, index, books, decision_equity, prior_closes, previous_action, cost_bps,
               capital_freeze=None):
        if seen is not None:
            seen.append({"books": {owner: books[owner]["cash"] for owner in books},
                         "freeze": capital_freeze, "decision_equity": decision_equity})
        return {"selected_action": action}

    return choose


def _strip(rows: list[dict]) -> list[dict]:
    return [{key: value for key, value in row.items() if key not in _CAPITAL_ONLY} for row in rows]


def test_c0_matches_legacy_dynamic_and_starts_with_all_cash_in_outer(monkeypatch) -> None:
    modules = _modules()
    r7 = modules["r7_joint_account_compare"]
    study = modules["post_r9_capital_study"]

    def _decision(rows, signal_index, **kwargs):
        return {"signal_date": rows[signal_index]["date"],
                "target_tqqq_usd": kwargs["member_budget_usd"],
                "guard_route": "synthetic", "core_state": "synthetic"}

    monkeypatch.setattr(r7, "_decision", _decision)
    days = ("2024-05-23", "2024-05-24", "2024-05-28")
    rows = _rows(days)
    actions = _actions(r7.SYMBOLS)
    account = _account(days, 10_000.0)
    seen = []
    selector = _selector("B1", seen)
    hook = study.capital_hook("C0", 10_000.0)
    _metrics, hooked = r7._replay(
        rows, actions, {}, {}, account, path_name="B0", cost_bps=10, action_selector=selector,
        capital_hook=hook)
    _metrics, legacy = r7._replay(
        rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
        action_selector=_selector("B1"))
    assert _strip(hooked) == legacy
    assert seen[0]["books"]["tqqq"] == 0.0
    assert seen[0]["books"]["soxl"] == 0.0
    assert seen[0]["books"]["outer"] == 10_000.0
    assert hooked[0]["member_budget_usd"]["tqqq"] == pytest.approx(
        hooked[0]["decision_equity_usd"] * 0.05)
    assert hooked[0]["aggregate_member_cap_usd"] == pytest.approx(
        hooked[0]["decision_equity_usd"] * 0.05)
    assert _ROW_FIELDS <= set(hooked[0])
    assert hooked[0]["external_flow_usd"] == 0.0


def test_three_scales_use_curve_ratio_and_cap_is_nav_not_wealth() -> None:
    study = _modules()["post_r9_capital_study"]
    policy = study._policy()
    params = policy["parameters"]
    assert params == {"a0": 10_000.0, "lower": 0.01, "upper": 0.05, "curvature": 1.0}
    for scale in (1_000.0, 10_000.0, 100_000.0):
        curve = smooth_bounded_capital_risk_ratio(
            capital=scale, a0=params["a0"], lower=params["lower"],
            upper=params["upper"], curvature=params["curvature"])
        frozen = study.capital_hook("C2", scale).freeze_actual(scale)
        assert frozen.curve_ratio == pytest.approx(curve["risk_ratio"])
        assert frozen.aggregate_member_cap_usd == pytest.approx(scale * curve["risk_ratio"])
        weights = frozen.weights("B1")
        assert weights["tqqq_cap"] == pytest.approx(curve["risk_ratio"])
        assert weights["outer_boxx"] == pytest.approx(0.54 - curve["risk_ratio"])
        assert math_is_one(weights)
    legacy = study.action_weights(0.05, "B3")
    assert legacy["tqqq_cap"] == pytest.approx(0.025)
    assert legacy["soxl_cap"] == pytest.approx(0.025)
    assert legacy["outer_boxx"] == pytest.approx(0.49)
    hook = study.capital_hook("C2", 1_000.0)
    hook.freeze_actual(10_000.0)
    later = hook.freeze_actual(1_000.0)
    assert later.wealth_reference_usd == pytest.approx(10_000.0)
    assert later.aggregate_member_cap_usd == pytest.approx(1_000.0 * 0.03)
    assert later.aggregate_member_cap_usd != pytest.approx(10_000.0 * 0.03)
    fixed = study.capital_hook("C1", 1_000.0)
    first = fixed.freeze_actual(1_000.0)
    second = fixed.freeze_actual(100_000.0)
    assert first.curve_ratio == pytest.approx(second.curve_ratio)
    assert second.aggregate_member_cap_usd == pytest.approx(100_000.0 * first.curve_ratio)


def math_is_one(weights: dict) -> bool:
    return abs(sum(weights.values()) - 1.0) < 1e-12


def test_wealth_reference_ignores_future_decision_equity() -> None:
    modules = _modules()
    r7 = modules["r7_joint_account_compare"]
    study = modules["post_r9_capital_study"]
    days = ("2024-05-23", "2024-05-24", "2024-05-28", "2024-05-29")
    closes = {"2024-05-24": 100.0, "2024-05-28": 100.0, "2024-05-29": 500.0}
    rows = _rows(days, close=closes)
    actions = _actions(r7.SYMBOLS)
    account = _account(days, 1_000.0)
    seen = []
    original = study.CapitalResearchHook.freeze_actual

    def _spy(self, decision_equity):
        seen.append(decision_equity)
        return original(self, decision_equity)

    study.CapitalResearchHook.freeze_actual = _spy
    try:
        hook = study.capital_hook("C2", 1_000.0)
        _metrics, ledger = r7._replay(
            rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
            action_selector=_selector("B0"), capital_hook=hook, short_sessions=2)
    finally:
        study.CapitalResearchHook.freeze_actual = original
    assert len(seen) == 2
    assert rows[-1]["qqqm_close"] == 500.0
    assert seen[0] == pytest.approx(1_000.0)
    assert max(seen) < 1_500.0
    assert hook.w == pytest.approx(ledger[-1]["wealth_reference_usd"])
    assert all(row["wealth_reference_usd"] <= max(seen) + 1e-6 for row in ledger)


def test_selector_scenarios_reuse_the_frozen_cap_without_updating_wealth(monkeypatch) -> None:
    modules = _modules()
    r7 = modules["r7_joint_account_compare"]
    r8 = modules["r8_joint_allocation_compare"]
    study = modules["post_r9_capital_study"]
    days = ("2024-05-23", "2024-05-24", "2024-05-28")
    rows = _rows(days)
    actions = _actions(r7.SYMBOLS)
    account = _account(days, 1_000.0)
    hook = study.capital_hook("C2", 1_000.0)
    captured = []

    def choose(rows, index, books, decision_equity, prior_closes, previous_action, cost_bps,
               capital_freeze=None):
        before = hook.w
        for _ in range(60):
            captured.append(capital_freeze.aggregate_member_cap_usd)
            assert capital_freeze.wealth_reference_usd == before
        assert hook.w == before
        return {"selected_action": "B1"}

    def _decision(rows, signal_index, **kwargs):
        return {"signal_date": rows[signal_index]["date"],
                "target_tqqq_usd": 0.0, "guard_route": "synthetic", "core_state": "synthetic"}

    monkeypatch.setattr(r7, "_decision", _decision)
    monkeypatch.setattr(r8, "_decision", _decision)
    caps = []

    def _fund(*args, **kwargs):
        caps.append(kwargs["aggregate_member_cap_usd"])
        return {"tqqq": 0.0, "soxl": 0.0}

    monkeypatch.setattr(r7, "_fund_owners", _fund)
    _metrics, ledger = r7._replay(
        rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
        action_selector=choose, capital_hook=hook, short_sessions=1)
    frozen = ledger[0]
    assert captured
    assert caps and caps[0] == pytest.approx(frozen["aggregate_member_cap_usd"])
    assert set(captured) == {frozen["aggregate_member_cap_usd"]}
    assert frozen["aggregate_member_cap_usd"] == pytest.approx(
        frozen["decision_equity_usd"] * frozen["curve_ratio"])
    assert frozen["aggregate_member_cap_usd"] != pytest.approx(
        frozen["wealth_reference_usd"] * frozen["curve_ratio"]) or \
        frozen["decision_equity_usd"] == pytest.approx(frozen["wealth_reference_usd"])
    plan = r8._targets_for_action(
        rows, 1, {owner: r7._book(0.0, r7.OWNER_SYMBOLS[owner]) for owner in r7.OWNERS},
        frozen["decision_equity_usd"], {symbol: 100.0 for symbol in r7.SYMBOLS},
        "B1", {}, {}, account, capital_freeze=hook.freeze_actual(frozen["decision_equity_usd"]))
    assert plan["budgets"]["tqqq"] == pytest.approx(frozen["aggregate_member_cap_usd"])


def test_checkpoint_restores_wealth_and_rejects_duplicate_events() -> None:
    modules = _modules()
    r7 = modules["r7_joint_account_compare"]
    study = modules["post_r9_capital_study"]
    days = ("2024-05-23", "2024-05-24", "2024-05-28", "2024-05-29")
    rows = _rows(days, close={"2024-05-24": 180.0, "2024-05-28": 180.0, "2024-05-29": 180.0})
    actions = _actions(r7.SYMBOLS)
    account = _account(days, 10_000.0)
    event = {"event_id": "flow-1", "kind": "deposit", "amount": 25.0}

    def _prepared() -> object:
        prepared = study.capital_hook("C2", 10_000.0)
        prepared.apply_period_reference(
            previous_equity=10_000.0, ending_equity=10_025.0, events=(event,))
        return prepared

    hook = _prepared()
    _metrics, full = r7._replay(
        rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
        action_selector=_selector("B0"), capital_hook=hook, short_sessions=3)
    checkpoint = {}
    r7._replay(rows, actions, {}, {}, account, path_name="B0", cost_bps=10,
               action_selector=_selector("B0"), capital_hook=_prepared(), short_sessions=2,
               checkpoint_out=checkpoint)
    assert checkpoint["capital_policy_id"] == study._policy()["policy_id"]
    assert checkpoint["capital_w_usd"] > 10_000.0
    assert "flow-1" in checkpoint["capital_seen_event_ids"]
    restored = study.capital_hook("C2", 10_000.0)
    assert restored.w == 10_000.0
    resumed_account = {**account, "data": {**account["data"], "last_session": checkpoint["last_date"]}}
    _metrics, resumed = r7._replay(
        rows, actions, {}, {}, resumed_account, path_name="B0", cost_bps=10,
        action_selector=_selector("B0"), capital_hook=restored,
        continuation_from_session=checkpoint["last_date"], continuation_last_session=days[-1],
        continuation_checkpoint=checkpoint)
    assert restored.w >= checkpoint["capital_w_usd"]
    assert resumed == full[-len(resumed):]
    with pytest.raises(ValueError, match="DUPLICATE_EVENT"):
        restored.apply_period_reference(
            previous_equity=10_000.0, ending_equity=10_025.0, events=(event,))
    assert restored.w >= checkpoint["capital_w_usd"]


def test_external_flows_are_not_dollar_wealth_or_real_contributions() -> None:
    study = _modules()["post_r9_capital_study"]
    hook = study.capital_hook("C2", 100.0)
    deposit = hook.apply_period_reference(
        previous_equity=100.0, ending_equity=110.0,
        events=({"event_id": "in", "kind": "deposit", "amount": 10.0},))
    assert deposit["investment_index"] == pytest.approx(1.0)
    assert hook.w == 100.0
    withdrawal = study.capital_hook("C2", 100.0)
    out = withdrawal.apply_period_reference(
        previous_equity=100.0, ending_equity=90.0,
        events=({"event_id": "out", "kind": "withdrawal", "amount": 10.0},))
    assert out["investment_index"] == pytest.approx(1.0)
    mixed = study.capital_hook("C2", 100.0)
    mixed.apply_period_reference(previous_equity=100.0, ending_equity=80.0, events=())
    after = mixed.apply_period_reference(
        previous_equity=80.0, ending_equity=100.0,
        events=(
            {"event_id": "topup", "kind": "deposit", "amount": 20.0},
            {"event_id": "div", "kind": "dividend", "amount": 1.0},
            {"event_id": "pend", "kind": "unsettled", "amount": 5.0},
            {"event_id": "xfer", "kind": "owner_transfer", "amount": 3.0},
        ))
    assert mixed.investment_index == pytest.approx(0.8)
    assert mixed.investment_high_water == pytest.approx(1.0)
    assert after["dollar_w_usd"] == 100.0
    assert after["dollar_w_is_investment_index"] is False
    dividend = study.capital_hook("C2", 100.0)
    marked = dividend.apply_period_reference(
        previous_equity=100.0, ending_equity=101.0,
        events=(
            {"event_id": "div2", "kind": "dividend", "amount": 1.0},
            {"event_id": "pend2", "kind": "unsettled", "amount": 4.0},
            {"event_id": "xfer2", "kind": "owner_transfer", "amount": 2.0},
        ))
    assert marked["investment_index"] == pytest.approx(1.01)
    unchanged = study.capital_hook("C2", 100.0)
    before = (unchanged.w, unchanged.investment_index, list(unchanged.seen_event_ids))
    for events in (None, (None,), ({"event_id": "bad", "kind": "deposit", "amount": None},)):
        with pytest.raises(ValueError):
            unchanged.apply_period_reference(previous_equity=100.0, ending_equity=100.0, events=events)
    assert (unchanged.w, unchanged.investment_index, list(unchanged.seen_event_ids)) == before


def test_runner_reads_m1_and_writes_only_eight_new_ledgers(tmp_path: Path, monkeypatch) -> None:
    modules = _modules()
    study = modules["post_r9_capital_study"]
    assert study.M1_SUMMARY_SHA256 == M1_SUMMARY_SHA256
    assert study.M1_DYNAMIC_LEDGER_SHA256 == M1_DYNAMIC_LEDGER_SHA256
    m1 = tmp_path / "m1"
    m1.mkdir()
    (m1 / "summary.json").write_bytes(b"not-the-authoritative-summary")
    (m1 / "private_daily_dynamic_10bps.json").write_bytes(b"not-the-ledger")
    with pytest.raises(ValueError, match="M1_SUMMARY"):
        study._load_m1(m1)
    seen = {"m1": None, "formal": [], "recovery": 0}

    def _load(root: Path):
        seen["m1"] = root
        ledger = [_formal_row(study.FIRST_SESSION if index == 0 else
                              study.LAST_SESSION if index == 855 else "2024-05-28",
                              110.0, "B0") for index in range(856)]
        return {"private_ledger_sha256": {"dynamic_10bps": M1_DYNAMIC_LEDGER_SHA256}}, ledger

    def _replay(*_args, **kwargs):
        hook = kwargs["capital_hook"]
        if (hook.variant == "C0" and hook.initial_nav == 10_000.0
                and kwargs.get("short_sessions") is None
                and kwargs.get("continuation_checkpoint") is None):
            raise AssertionError("replayed 10000 C0")
        if kwargs.get("continuation_checkpoint") is not None:
            hook.restore(kwargs["continuation_checkpoint"])
        short = kwargs.get("short_sessions")
        if short == 2:
            ledger = [_formal_row(day, 100.0, "B0") for day in ("2024-05-24", "2024-05-28")]
        elif short == 3:
            ledger = [_formal_row(day, 100.0, "B0")
                      for day in ("2024-05-24", "2024-05-28", "2024-05-29")]
        elif kwargs.get("continuation_checkpoint") is not None:
            ledger = [_formal_row("2024-05-29", 100.0, "B0")]
        else:
            nav = {"C0": 110.0, "C1": 100.0, "C2": 90.0}[hook.variant]
            seen["formal"].append((hook.initial_nav, hook.variant))
            ledger = [_formal_row(study.FIRST_SESSION if index == 0 else
                                  study.LAST_SESSION if index == 855 else "2024-05-28",
                                  nav, "B1") for index in range(856)]
        if kwargs.get("checkpoint_out") is not None:
            hook.freeze_actual(hook.initial_nav)
            kwargs["checkpoint_out"].update({
                "last_date": ledger[-1]["date"], "last_global_index": 1,
                "prior_nav_usd": hook.initial_nav, "previous_action": "B0", "books": {},
                **hook.export_state()})
        return {}, ledger

    def _inputs(*_args, **_kwargs):
        rows = [{"date": day} for day in ("2023-03-27", "2023-03-28", "2023-03-29", "2023-03-30")]
        return rows, {}, {}, {"tqqq_contract": {}}

    def _future(_root, rows, actions, _indicators):
        return rows, actions, {"last_extension_session": "2024-05-29"}

    monkeypatch.setattr(study, "_load_m1", _load)
    monkeypatch.setattr(modules["r7_joint_account_compare"], "_load_inputs", _inputs)
    monkeypatch.setattr(modules["r7_joint_account_compare"], "_replay", _replay)
    monkeypatch.setattr(modules["r9_frozen_policy_validation"], "_verified_future", _future)
    monkeypatch.setattr(modules["r8_joint_allocation_compare"], "_selector",
                        lambda *_args, **_kwargs: _selector("B0"))
    output = tmp_path / "out"
    result = study.run(tmp_path / "raw", tmp_path / "r6", tmp_path / "mat.json",
                       tmp_path / "r7", tmp_path / "r8", tmp_path / "r9", tmp_path / "future",
                       m1, output)
    assert seen["m1"] == m1
    assert sorted(seen["formal"]) == [(scale, variant)
                                      for scale in (1_000.0, 10_000.0, 100_000.0)
                                      for variant in ("C0", "C1", "C2")
                                      if not (scale == 10_000.0 and variant == "C0")]
    names = sorted(path.name for path in output.iterdir())
    assert names == [
        "private_daily_100000_C0_10bps.json", "private_daily_100000_C1_10bps.json",
        "private_daily_100000_C2_10bps.json", "private_daily_10000_C1_10bps.json",
        "private_daily_10000_C2_10bps.json", "private_daily_1000_C0_10bps.json",
        "private_daily_1000_C1_10bps.json", "private_daily_1000_C2_10bps.json",
        "summary.json"]
    assert "private_daily_10000_C0_10bps.json" not in names
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["research_only"] is True
    assert summary["paper_authorized"] is False
    assert summary["shadow_authorized"] is False
    assert summary["live_authorized"] is False
    assert summary["reused_m1_10000_c0"]["read_only"] is True
    assert summary["external_flows"] == "none"
    assert summary["recovery"]["status"] == "matched"
    assert summary["recovery"]["short_history_checks"] <= 2
    for scale in ("1000", "10000", "100000"):
        pair = summary["comparisons"][scale]
        assert pair["C1_minus_C0"]["cumulative_return"] < 0
        assert pair["C2_minus_C1"]["cumulative_return"] < 0
    assert result["path_count"] == 8
    with pytest.raises(FileExistsError):
        study.run(tmp_path / "raw", tmp_path / "r6", tmp_path / "mat.json",
                  tmp_path / "r7", tmp_path / "r8", tmp_path / "r9", tmp_path / "future",
                  m1, output)


def _formal_row(day: str, nav: float, action: str) -> dict:
    return {"date": day, "path": action, "economic_nav_close_usd": nav, "total_cost_usd": 1.0,
            "decision_equity_usd": nav, "member_budget_usd": {"tqqq": 0.0, "soxl": 0.0},
            "shortages": [], "owner_nav_usd": {"outer": nav, "tqqq": 0.0, "soxl": 0.0},
            "owner_security_values_usd": {"outer": {"QQQM": nav / 2, "BOXX": 0.0},
                                                           "tqqq": {"TQQQ": 0.0},
                                                           "soxl": {"SOXL": 0.0, "SOXX": 0.0,
                                                                    "BOXX": 0.0}},
            "owner_settled_cash_usd": {"outer": nav / 2, "tqqq": 0.0, "soxl": 0.0},
            "owner_pending_sale_usd": {"outer": 0.0, "tqqq": 0.0, "soxl": 0.0},
            "owner_receivable_usd": {"outer": 0.0, "tqqq": 0.0, "soxl": 0.0},
            "owner_shares": {"outer": {"QQQM": 0, "BOXX": 0}, "tqqq": {"TQQQ": 0},
                             "soxl": {"SOXL": 0, "SOXX": 0, "BOXX": 0}},
            "settled_cash_usd": nav / 2, "pending_sale_usd": 0.0, "receivable_usd": 0.0,
            "qqqm_value_usd": nav / 2, "outer_boxx_value_usd": 0.0,
            "soxl_internal_boxx_value_usd": 0.0,
            "tqqq_value_usd": 0.0, "soxl_value_usd": 0.0, "soxx_value_usd": 0.0}
