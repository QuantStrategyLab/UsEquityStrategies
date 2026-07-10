#!/usr/bin/env python3
"""Run walk-forward backtests via QuantPlatformKit BacktestOrchestrator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from us_equity_strategies.backtest.orchestrator_runner import SUPPORTED_PROFILES, build_backtest_runner
from us_equity_strategies.backtest.orchestrator_runner import _synthetic_market_history as _runner_synthetic_market_history
from us_equity_strategies.strategies.global_etf_rotation import DEFAULT_MIN_HISTORY_DAYS, PROFILE_NAME
from us_equity_strategies.strategies.us_equity_combo import PROFILE_NAME as US_EQUITY_COMBO_PROFILE

DEFAULT_WINDOWS: tuple[tuple[date, date], ...] = (
    (date(2023, 6, 1), date(2024, 5, 31)),
    (date(2024, 6, 1), date(2025, 5, 31)),
)
DEFAULT_STORE_ROOT = Path("/tmp/us_equity_wf_store")

PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    PROFILE_NAME: {"min_history_days": DEFAULT_MIN_HISTORY_DAYS},
    US_EQUITY_COMBO_PROFILE: {
        "min_history_days": DEFAULT_MIN_HISTORY_DAYS,
        "combo_mode": "dynamic",
    },
}


def _result_payload(item: Any) -> dict[str, Any]:
    return {
        "start_date": item.start_date.isoformat() if item.start_date else None,
        "end_date": item.end_date.isoformat() if item.end_date else None,
        "sharpe_ratio": item.sharpe_ratio,
        "max_drawdown": item.max_drawdown,
        "cagr": item.cagr,
        "total_return": item.total_return,
        "observation_count": item.observation_count,
        "run_id": getattr(item, "run_id", None),
    }


def _baseline_param_set_id(
    profile: str,
    params: dict[str, Any],
    *,
    synthetic_days: int,
    windows: tuple[tuple[date, date], ...],
) -> str:
    identity = {
        "params": params,
        "synthetic_days": synthetic_days,
        "windows": [(start.isoformat(), end.isoformat()) for start, end in windows],
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    return f"{profile}_baseline_{fingerprint}"


def _build_runner(*, profile: str, synthetic_days: int):
    return build_backtest_runner(profile, synthetic_days=synthetic_days)


def _clone_market_history(market_history: pd.DataFrame) -> pd.DataFrame:
    return market_history.copy(deep=True)


def _shared_market_history(
    profile: str,
    params: dict[str, Any],
    synthetic_days: int,
    windows: tuple[tuple[date, date], ...],
) -> tuple[pd.DataFrame, int]:
    min_history_days = int(params.get("min_history_days", DEFAULT_MIN_HISTORY_DAYS))
    earliest_window_start = min(start for start, _ in windows if start is not None)
    latest_window_end = max(end for _, end in windows if end is not None)
    lookback_start = earliest_window_start - pd.tseries.offsets.BDay(min_history_days + 5)
    required_window_days = len(pd.bdate_range(lookback_start, latest_window_end))
    effective_synthetic_days = max(int(synthetic_days), required_window_days)
    return _runner_synthetic_market_history(
        days=effective_synthetic_days,
        start=pd.Timestamp(lookback_start).date().isoformat(),
        include_combo_proxies=profile == US_EQUITY_COMBO_PROFILE,
    ), effective_synthetic_days


def run_walk_forward(
    *,
    profile: str,
    windows: tuple[tuple[date, date], ...] = DEFAULT_WINDOWS,
    synthetic_days: int = 900,
    store_root: Path | None = None,
) -> dict[str, Any]:
    from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported profile={profile!r}; supported={sorted(SUPPORTED_PROFILES)}")

    params = dict(PROFILE_DEFAULTS.get(profile, {"min_history_days": DEFAULT_MIN_HISTORY_DAYS}))
    target_root = store_root or DEFAULT_STORE_ROOT
    target_root.mkdir(parents=True, exist_ok=True)
    baseline_params = copy.deepcopy(params)
    shared_market_history, effective_synthetic_days = _shared_market_history(
        profile,
        baseline_params,
        synthetic_days,
        windows,
    )
    baseline_runner = build_backtest_runner(
        profile,
        synthetic_days=effective_synthetic_days,
        market_history=_clone_market_history(shared_market_history),
    )
    baseline_raw = baseline_runner.run(
        profile,
        copy.deepcopy(baseline_params),
        start_date=None,
        end_date=None,
    )
    with tempfile.TemporaryDirectory(prefix=f"{profile}_wf_", dir=target_root) as scratch_dir:
        scratch_store = PerformanceStore(local_root=Path(scratch_dir))
        scratch_orchestrator = BacktestOrchestrator(store=scratch_store)
        scratch_orchestrator.register_runner(
            "us_equity",
            build_backtest_runner(
                profile,
                synthetic_days=effective_synthetic_days,
                market_history=_clone_market_history(shared_market_history),
            ),
        )
        via_orch = scratch_orchestrator.run(
            profile,
            domain="us_equity",
            params=copy.deepcopy(baseline_params),
            param_set_id=f"{profile}_full_compare",
            start_date=None,
            end_date=None,
        )
        wf_params = copy.deepcopy(baseline_params)
        wf_results = scratch_orchestrator.walk_forward(
            profile,
            domain="us_equity",
            params=wf_params,
            windows=windows,
            param_set_id=f"{profile}_wf",
        )
    store = PerformanceStore(local_root=target_root)
    orchestrator = BacktestOrchestrator(store=store)
    baseline = orchestrator.persist_result(
        baseline_raw,
        strategy_profile=profile,
        domain="us_equity",
        params=baseline_params,
        param_set_id=_baseline_param_set_id(
            profile,
            baseline_params,
            synthetic_days=effective_synthetic_days,
            windows=windows,
        ),
    )
    return {
        "strategy_profile": profile,
        "domain": "us_equity",
        "baseline": _result_payload(baseline),
        "orchestrator_full_window": _result_payload(via_orch),
        "walk_forward_folds": [_result_payload(item) for item in wf_results],
        "source": "BacktestOrchestrator.walk_forward",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="US walk-forward backtest via BacktestOrchestrator.")
    parser.add_argument("--profile", default=PROFILE_NAME)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--synthetic-days", type=int, default=900)
    parser.add_argument("--store-root", type=Path)
    args = parser.parse_args()

    if args.list_profiles:
        print(json.dumps({"profiles": sorted(SUPPORTED_PROFILES)}, indent=2))
        return 0

    payload = run_walk_forward(
        profile=args.profile,
        synthetic_days=args.synthetic_days,
        store_root=args.store_root,
    )
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
