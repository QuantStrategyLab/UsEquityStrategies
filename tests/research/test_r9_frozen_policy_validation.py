"""Synthetic-only checks for the R9 continuation boundary and accounting metrics."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest


def _module():
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("r9_frozen_policy_validation",
                                                  root / "r9_frozen_policy_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prefix_comparison_only_normalizes_historical_risk_key() -> None:
    m = _module()
    old = [{"date": "2024-06-01",
            "nasdaq_lookthrough_to_nav": 0.5, "nav": 100.0} for index in range(444)]
    old[0]["date"], old[-1]["date"] = "2023-03-28", "2024-12-31"
    rebuilt = [{"date": row["date"], "nominal_equity_index_exposure_to_nav":
                row["nasdaq_lookthrough_to_nav"], "nav": row["nav"]} for row in old]
    assert m._compare_prefix({"B0_5bps": rebuilt}, {"B0_5bps": old})["B0_5bps"]["rows"] == 444
    rebuilt[-1]["nav"] = 99.0
    with pytest.raises(ValueError, match="PREFIX_LEDGER_MISMATCH"):
        m._compare_prefix({"B0_5bps": rebuilt}, {"B0_5bps": old})


def test_extension_metrics_include_cross_year_first_day_return_and_inherited_high() -> None:
    m = _module()
    prefix = [{"date": "2024-12-31", "economic_nav_close_usd": 120.0}]
    prefix *= 444
    prefix[-1] = {"date": "2024-12-31", "economic_nav_close_usd": 120.0}
    ledger = []
    for day, nav in (("2025-01-02", 110.0), ("2026-08-25", 130.0)):
        ledger.append({"date": day, "economic_nav_close_usd": nav, "total_cost_usd": 1.0,
                       "traded_notional_usd": 100.0, "shortages": [], "path": "B0",
                           "qqqm_value_usd": 50.0, "tqqq_value_usd": 0.0,
                           "soxl_value_usd": 0.0, "soxx_value_usd": 0.0,
                           "owner_nav_usd": {"outer": nav, "tqqq": 0.0, "soxl": 0.0},
                           "owner_security_values_usd": {
                               "outer": {"QQQM": 50.0, "BOXX": 0.0},
                               "tqqq": {"TQQQ": 0.0},
                               "soxl": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0}},
                           "owner_settled_cash_usd": {},
                       "owner_pending_sale_usd": {}, "owner_receivable_usd": {}})
    metrics, returns = m._metrics(ledger, prefix)
    assert returns[0] == pytest.approx(110 / 120 - 1)
    assert metrics["drawdown_extension_including_2024_12_31"][
        "drawdown_usd_at_max_drawdown_fraction"] == pytest.approx(10)
    assert metrics["drawdown_inherited_high_water"][
        "drawdown_usd_at_max_drawdown_fraction"] == pytest.approx(10)
    assert metrics["drawdown_extension_including_2024_12_31"][
        "max_drawdown_fraction_recovery_date"] == "2026-08-25"


def test_percentage_and_dollar_drawdown_can_peak_on_different_events() -> None:
    m = _module()
    result = m._drawdown([
        {"date": "2025-01-02", "economic_nav_close_usd": 900.0},
        {"date": "2025-02-03", "economic_nav_close_usd": 2000.0},
        {"date": "2026-08-25", "economic_nav_close_usd": 1850.0},
    ], 1000.0, "2024-12-31")
    assert result["max_drawdown_fraction"] == pytest.approx(0.1)
    assert result["drawdown_usd_at_max_drawdown_fraction"] == pytest.approx(100.0)
    assert result["max_dollar_drawdown_usd"] == pytest.approx(150.0)
    assert result["max_drawdown_fraction_trough_date"] == "2025-01-02"
    assert result["max_dollar_drawdown_trough_date"] == "2026-08-25"


def test_missing_future_data_is_fail_closed() -> None:
    m = _module()
    m.LICENSE_BASIS_SHA256 = "a" * 64  # Isolate missing-root check from license gate.
    with pytest.raises(ValueError, match="FUTURE_ROOT_INVALID"):
        m._manifest_entries(Path("/definitely/not/an/r9-input"))


def test_unverified_license_blocks_future_input_before_any_read(tmp_path: Path) -> None:
    m = _module()
    with pytest.raises(ValueError, match="LICENSE_RECORD_UNVERIFIED"):
        m._manifest_entries(tmp_path)


def test_synthetic_future_pages_append_without_touching_prefix(tmp_path: Path) -> None:
    m = _module()
    m.LICENSE_BASIS_SHA256 = "a" * 64  # Synthetic-only checked entitlement fixture.
    days = []
    day = date(2025, 1, 2)
    last = date(2026, 8, 25)
    while day <= last:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    future_root = tmp_path / "future"
    entries = []
    for symbol in m.SYMBOLS:
        bar_path = future_root / "bars" / symbol / "page-001.json"
        bar_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"next_page_token": None,
                   "bars": [{"t": f"{session}T00:00:00Z", "o": 100.0,
                             "h": 101.0, "l": 99.0, "c": 100.0,
                             "v": 0.0 if session == days[0] else 1000.0}
                            for session in days]}
        content = json.dumps(payload, separators=(",", ":")).encode()
        bar_path.write_bytes(content)
        entries.append({"symbol": symbol, "kind": "bars", "request": m._expected_request(symbol, "bars"),
                        "count": len(days), "first_bar_time": f"{days[0]}T00:00:00Z",
                        "last_bar_time": f"{days[-1]}T00:00:00Z", "complete_pagination": True,
                        "pages": [{"uri": f"gs://{m.PRIVATE_BUCKET}/{m.PRIVATE_PREFIX}bars/{symbol}/page-001.json",
                                   "generation": "1", "bytes": len(content),
                                   "sha256": m.hashlib.sha256(content).hexdigest()}]})
        action_path = future_root / "actions" / symbol / "page-001.json"
        action_path.parent.mkdir(parents=True, exist_ok=True)
        new_dividends = ([{"symbol": symbol, "ex_date": "2025-02-03",
                           "payable_date": "2025-02-04", "process_date": "2025-02-03",
                           "rate": 0.1}] if symbol == "QQQM" else [])
        action_content = json.dumps({"symbol": symbol, "next_page_token": None,
                                     "corporate_actions": {"forward_splits": [],
                                                           "cash_dividends": new_dividends}},
                                    separators=(",", ":")).encode()
        action_path.write_bytes(action_content)
        entries.append({"symbol": symbol, "kind": "actions",
                        "request": m._expected_request(symbol, "actions"),
                        "count": len(new_dividends), "first_bar_time": None,
                        "last_bar_time": None,
                        "complete_pagination": True,
                        "pages": [{"uri": f"gs://{m.PRIVATE_BUCKET}/{m.PRIVATE_PREFIX}actions/{symbol}/page-001.json",
                                   "generation": "1", "bytes": len(action_content),
                                   "sha256": m.hashlib.sha256(action_content).hexdigest()}]})
    future_root.joinpath("manifest.json").write_text(json.dumps({
        "schema_version": "qsl.research.raw_sip_input.v1",
        "source": "alpaca.stocks.bars.v2_and_corporate_actions.v1",
        "feed": "sip", "price_adjustment": "raw", "calendar": "XNYS",
        "timezone": "America/New_York", "currency": "USD",
        "scope": m.PRIVATE_PREFIX,
        "license_basis_record_sha256": m.LICENSE_BASIS_SHA256,
        "retrieved_at": "2026-09-26T00:00:00Z",
        "write_probe": {"uri": f"gs://{m.PRIVATE_BUCKET}/{m.PRIVATE_PREFIX}_write_probe.json",
                        "generation": "1", "bytes": 10, "sha256": "a" * 64},
        "provider_page_requests": 12,
        "provider_response_bytes": sum(page["bytes"] for item in entries for page in item["pages"]),
        "no_order": True, "research_only": True, "execution_authorized": False,
        "inputs": entries}))
    prefix = [{"date": "2024-12-31", **{symbol.lower() + "_close": 99.0
                                        for symbol in m.EXECUTION_SYMBOLS}}]
    original = json.loads(json.dumps(prefix))
    actions = {symbol: {"forward_splits": [], "cash_dividends": []}
               for symbol in m.EXECUTION_SYMBOLS}
    original_actions = json.loads(json.dumps(actions))
    rows, merged_actions, metadata = m._verified_future(
        future_root, prefix, actions, {"2024-12-31": {}, **{session: {} for session in days}})
    assert prefix == original
    assert actions == original_actions
    assert len(rows) == len(days) + 1
    assert rows[1]["qqq_close"] == 100.0
    assert rows[-1]["date"] == "2026-08-25"
    assert len(merged_actions["QQQM"]["cash_dividends"]) == 1
    assert merged_actions["SOXL"]["cash_dividends"] == []
    assert metadata["future_session_count"] == len(days)


def test_bootstrap_is_deterministic_and_uses_frozen_draw_count() -> None:
    m = _module()
    first = m._bootstrap([0.001, -0.002, 0.004] * 12)
    second = m._bootstrap([0.001, -0.002, 0.004] * 12)
    assert first == second
    assert first["draws"] == 2000
    assert first["block_sessions"] == 20


def test_replay_checkpoint_resumes_at_next_global_row_without_restarting() -> None:
    m = _module()
    sys.path.insert(0, str(Path(__file__).parents[2] / "docs/research/first_compounding_20260925"))
    import r7_joint_account_compare as r7

    rows = []
    for day in ("2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02"):
        row = {"date": day}
        for symbol in r7.SYMBOLS:
            row[symbol.lower() + "_open"] = 100.0
            row[symbol.lower() + "_close"] = 100.0
        rows.append(row)
    path = {"qqqm": 0.5, "tqqq_cap": 0.0, "soxl_cap": 0.0,
            "outer_boxx": 0.49, "outer_cash": 0.01}
    policy = {"candidate_id": "synthetic", "paths": {"B0": path}, "initial_research_nav_usd": 10000.0,
              "data": {"first_signal": "2024-12-27", "first_trade": "2024-12-30",
                       "last_session": "2024-12-31", "expected_replay_sessions": 2}}
    actions = {symbol: {"forward_splits": [], "cash_dividends": []} for symbol in r7.SYMBOLS}
    release_indexes = []
    original_release = r7._release

    def capture_release(books, day, index):
        release_indexes.append(index)
        return original_release(books, day, index)

    original_metrics = r7._metrics
    r7._release = capture_release
    r7._metrics = lambda *_args, **_kwargs: {
        "average_nasdaq_lookthrough_to_nav": 0.0,
        "maximum_nasdaq_lookthrough_to_nav": 0.0,
    }
    try:
        checkpoint = {}
        r7._replay(rows[:3], actions, {}, {}, policy, path_name="B0", cost_bps=10,
                   checkpoint_out=checkpoint)
        assert checkpoint["last_date"] == "2024-12-31"
        assert checkpoint["last_global_index"] == 2
        r7._replay(rows, actions, {}, {}, policy, path_name="B0", cost_bps=10,
                   continuation_last_session="2025-01-02",
                   continuation_from_session="2024-12-31",
                   continuation_checkpoint=checkpoint)
    finally:
        r7._release = original_release
        r7._metrics = original_metrics
    assert release_indexes == [1, 2, 3]
