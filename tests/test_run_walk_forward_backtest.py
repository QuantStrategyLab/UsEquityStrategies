from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

import scripts.run_walk_forward_backtest as walk_forward
from scripts.run_walk_forward_backtest import DEFAULT_WINDOWS, _baseline_param_set_id, run_walk_forward
from scripts.run_walk_forward_backtest import _shared_market_history
from us_equity_strategies.strategies.global_etf_rotation import extract_managed_symbols_universe


def test_run_walk_forward_persists_lifecycle_baseline(tmp_path: Path) -> None:
    payload = run_walk_forward(
        profile="global_etf_rotation",
        synthetic_days=900,
        store_root=tmp_path,
    )

    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "backtest" / "us_equity" / "global_etf_rotation").glob("*.json")
    ]

    assert payload["baseline"]["sharpe_ratio"] is not None
    baseline_records = [record for record in records if "_baseline_" in record["param_set_id"]]
    assert baseline_records
    assert all(record["params"] == {"min_history_days": 260} for record in baseline_records)
    assert not any("_wf" in record["param_set_id"] for record in records)
    assert payload["orchestrator_full_window"]["sharpe_ratio"] is not None
    assert payload["walk_forward_folds"]


def test_baseline_param_set_id_tracks_synthetic_days_and_windows() -> None:
    first = _baseline_param_set_id(
        "global_etf_rotation",
        {"min_history_days": 260},
        synthetic_days=900,
        windows=DEFAULT_WINDOWS,
    )
    second = _baseline_param_set_id(
        "global_etf_rotation",
        {"min_history_days": 260},
        synthetic_days=1200,
        windows=DEFAULT_WINDOWS,
    )

    assert first != second

    shifted_windows = (
        (DEFAULT_WINDOWS[0][0], DEFAULT_WINDOWS[0][1]),
        (DEFAULT_WINDOWS[1][0], DEFAULT_WINDOWS[1][1].replace(year=2026)),
    )
    assert first != _baseline_param_set_id(
        "global_etf_rotation",
        {"min_history_days": 260},
        synthetic_days=900,
        windows=shifted_windows,
    )


def test_run_walk_forward_does_not_persist_partial_results_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(BacktestOrchestrator, "walk_forward", _raise)
    with pytest.raises(RuntimeError, match="boom"):
        run_walk_forward(
            profile="global_etf_rotation",
            synthetic_days=900,
            store_root=tmp_path,
        )
    assert not list(tmp_path.rglob("*.json"))


def test_run_walk_forward_rejects_too_short_synthetic_history(tmp_path: Path) -> None:
    payload = run_walk_forward(
        profile="global_etf_rotation",
        synthetic_days=220,
        store_root=tmp_path,
    )

    assert payload["baseline"]["sharpe_ratio"] is not None


def test_run_walk_forward_keeps_local_default_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(walk_forward, "DEFAULT_STORE_ROOT", tmp_path)

    run_walk_forward(profile="global_etf_rotation", synthetic_days=900)

    assert list(tmp_path.rglob("*.json"))


def test_run_walk_forward_uses_external_history_and_writes_return_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = pd.bdate_range("2022-01-03", "2024-12-31")
    rows = []
    for symbol_index, symbol in enumerate(extract_managed_symbols_universe()):
        for day_index, day in enumerate(dates):
            rows.append(
                {
                    "as_of": day,
                    "symbol": symbol,
                    "close": 20.0 + symbol_index + day_index * (0.01 + symbol_index / 10000),
                }
            )
    history = pd.DataFrame(rows)
    monkeypatch.setattr(
        walk_forward,
        "_runner_synthetic_market_history",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("synthetic history must not be used")),
    )
    returns_output = tmp_path / "returns" / "portfolio_and_tracker_returns.csv"

    payload = run_walk_forward(
        profile="global_etf_rotation",
        windows=(
            (pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-06-30").date()),
            (pd.Timestamp("2024-07-01").date(), pd.Timestamp("2024-12-31").date()),
        ),
        store_root=tmp_path / "store",
        market_history=history,
        returns_output=returns_output,
    )

    return_matrix = pd.read_csv(returns_output)
    assert payload["baseline"]["end_date"] == "2024-12-31"
    assert {"as_of", "global_etf_rotation", "buy_hold_SPY"} <= set(return_matrix.columns)
    assert return_matrix["global_etf_rotation"].notna().any()


def test_shared_market_history_rejects_stale_symbol_tail() -> None:
    dates = pd.bdate_range("2022-01-03", "2025-05-30")
    rows = [
        {"date": day, "symbol": symbol, "close": 100.0}
        for symbol in extract_managed_symbols_universe()
        for day in dates
        if not (symbol == "EWY" and day > pd.Timestamp("2025-03-31"))
    ]

    with pytest.raises(ValueError, match="incomplete symbol coverage: EWY"):
        _shared_market_history(
            "global_etf_rotation",
            {"min_history_days": 260},
            900,
            DEFAULT_WINDOWS,
            pd.DataFrame(rows),
        )
