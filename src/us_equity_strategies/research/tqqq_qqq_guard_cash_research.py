"""Private, research-only TQQQ/QQQ guard and cash replay.

This is a separate candidate. QQQ is a signal, TQQQ is the sole security,
and every residual dollar remains cash. Input paths must point at a locally
retained, hash-checked raw research bundle; this module never fetches data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialRecord, ResearchTrialStatus
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from us_equity_strategies.research.tqqq_guard_cash_trial_ledger import build_guard_cash_trial_ledger

from quant_strategy_plugins.benchmark_drawdown_guard import build_benchmark_drawdown_guard_signal
from quant_strategy_plugins.market_regime_control_plugin import build_market_regime_control_signal
from us_equity_strategies.strategies.tqqq_dual_drive_core import (
    DualDriveCoreInput,
    decide_tqqq_dual_drive,
)
from us_equity_strategies.strategies.tqqq_growth_income import (
    _resolve_pullback_rebound_threshold,
    _resolve_volatility_delever_thresholds,
)

CANDIDATE = "tqqq_qqq_guard_cash_research_v1"
CONTRACT_NAME = "tqqq_qqq_guard_cash_contract.v1.json"
FROZEN_CONTRACT_SHA256 = "7b603312762262ebb2fe90c86bef2ed0b7914ab26586b0b9db93ee39b4d1e60b"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_bundle(root: Path) -> tuple[dict, list[dict], list[dict], dict]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("PRIVATE_ROOT_INVALID")
    contract_path = root / CONTRACT_NAME
    if _sha256(contract_path) != FROZEN_CONTRACT_SHA256:
        raise ValueError("FROZEN_CANDIDATE_CONTRACT_CHANGED")
    contract = json.loads(contract_path.read_text())
    if contract.get("candidate_id") != CANDIDATE or contract.get("execution_authorized") is not False:
        raise ValueError("CANDIDATE_CONTRACT_INVALID")
    if _sha256(root / "manifest.json") != contract["source_manifest_sha256"]:
        raise ValueError("MANIFEST_DIGEST_MISMATCH")
    required_names = {"bars/QQQ/page-001.json", "bars/TQQQ/page-001.json",
                      "actions/QQQ/page-001.json", "actions/TQQQ/page-001.json"}
    if {item["name"] for item in contract["inputs"]} != required_names or len(contract["inputs"]) != len(required_names):
        raise ValueError("FROZEN_INPUT_SET_INCOMPLETE")
    for item in contract["inputs"]:
        path = root / item["name"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item["bytes"]:
            raise ValueError("PRIVATE_INPUT_MISSING_OR_SIZE_MISMATCH")
        if _sha256(path) != item["sha256"]:
            raise ValueError("PRIVATE_INPUT_DIGEST_MISMATCH")
    qqq = json.loads((root / "bars/QQQ/page-001.json").read_text())
    tqqq = json.loads((root / "bars/TQQQ/page-001.json").read_text())
    actions = json.loads((root / "actions/TQQQ/page-001.json").read_text())
    qqq_actions = json.loads((root / "actions/QQQ/page-001.json").read_text())
    if qqq.get("symbol") != "QQQ" or tqqq.get("symbol") != "TQQQ":
        raise ValueError("SYMBOL_IDENTITY_MISMATCH")
    if qqq_actions["corporate_actions"].get("forward_splits"):
        raise ValueError("QQQ_SPLIT_ADJUSTMENT_NOT_IMPLEMENTED")
    return contract, qqq["bars"], tqqq["bars"], actions["corporate_actions"]


def _is_positive_finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _validated_bars(qqq: list[dict], tqqq: list[dict]) -> list[dict]:
    if len(qqq) != len(tqqq):
        raise ValueError("COMMON_DAILY_COVERAGE_MISMATCH")
    result = []
    previous = ""
    for q, t in zip(qqq, tqqq, strict=True):
        day = str(q["t"])[:10]
        if day != str(t["t"])[:10] or day <= previous:
            raise ValueError("MISSING_DUPLICATE_OR_MISALIGNED_SESSION")
        date.fromisoformat(day)
        prices = (q["o"], q["h"], q["l"], q["c"], t["o"], t["h"], t["l"], t["c"])
        if any(not _is_positive_finite_number(x) for x in prices):
            raise ValueError("RAW_PRICE_INVALID")
        if (q["h"] < max(q["o"], q["c"], q["l"]) or q["l"] > min(q["o"], q["c"])
                or t["h"] < max(t["o"], t["c"], t["l"]) or t["l"] > min(t["o"], t["c"])
                or not _is_positive_finite_number(q["v"])
                or not _is_positive_finite_number(t["v"])):
            raise ValueError("RAW_OHLCV_INVALID")
        result.append({"date": day, "qqq_close": float(q["c"]),
                       "tqqq_open": float(t["o"]), "tqqq_close": float(t["c"])})
        previous = day
    return result


def _decision(rows: list[dict], signal_index: int, *, shares: float, nav: float,
              use_guard: bool, config: dict, member_budget_usd: float | None = None) -> dict:
    if signal_index < config["core"]["first_signal_min_qqq_bars"] - 1:
        raise ValueError("QQQ_WARMUP_INCOMPLETE")
    prefix = rows[:signal_index + 1]
    close = pd.Series([row["qqq_close"] for row in prefix],
                      index=pd.to_datetime([row["date"] for row in prefix]), dtype=float)
    signal_date = prefix[-1]["date"]
    guard_config = config["qqq_guard"]
    guard = build_benchmark_drawdown_guard_signal(
        pd.DataFrame({"QQQ": close}), benchmark_symbol="QQQ", as_of=signal_date,
        drawdown_lookback_sessions=guard_config["lookback_sessions"],
        soft_drawdown_threshold=guard_config["soft_drawdown"],
        hard_drawdown_threshold=guard_config["hard_drawdown"],
        soft_risk_asset_scalar=guard_config["soft_scalar"],
        hard_risk_asset_scalar=guard_config["hard_scalar"],
        max_price_age_days=guard_config["max_price_age_days"],
    )
    if guard["canonical_route"] == "blocked" or guard["data_quality"]["status"] != "READY":
        raise ValueError("QQQ_GUARD_BLOCKED")
    regime = build_market_regime_control_signal(
        {"benchmark_guard": guard}, strategy_policy=CANDIDATE, as_of=signal_date,
    )
    control = regime["position_control"]
    if control["final_route"] == "blocked" or control["final_route"] not in {
        "no_action", "risk_reduced", "risk_off"
    }:
        raise ValueError("REGIME_ROUTE_UNSUPPORTED")
    if not math.isclose(float(control["leverage_scalar"]), float(guard["leverage_scalar"])) or not math.isclose(
        float(control["risk_asset_scalar"]), float(guard["risk_asset_scalar"])
    ):
        raise ValueError("GUARD_ARBITER_SCALAR_MISMATCH")

    ma200 = float(close.rolling(200).mean().iloc[-1])
    ma20 = close.rolling(20).mean()
    rebound_threshold, _ = _resolve_pullback_rebound_threshold(
        close, window=20, mode="volatility_scaled", fixed_threshold=0,
        volatility_multiplier=2.0,
    )
    pullback_low = float(close.rolling(20).min().iloc[-1])
    vol = _resolve_volatility_delever_thresholds(
        close, volatility_window=5, mode="rolling_percentile",
        fixed_entry_threshold=0.28, fixed_exit_threshold=0.28,
        percentile_lookback=252, percentile=0.90, min_periods=126,
        floor=0.24, cap=0.36,
    )
    if vol["dynamic_sample_count"] < 252 or vol["metric"] is None:
        raise ValueError("QQQ_VOLATILITY_WARMUP_INCOMPLETE")
    route = control["final_route"] if use_guard else "no_action"
    risk_reduced = route in {"risk_reduced", "risk_off"}
    budget = nav if member_budget_usd is None else float(member_budget_usd)
    if not math.isfinite(budget) or budget < 0 or budget > nav + 1e-8:
        raise ValueError("MEMBER_BUDGET_OUTSIDE_NAV")
    core = decide_tqqq_dual_drive(DualDriveCoreInput(
        qqq_price=float(close.iloc[-1]), ma200=ma200,
        latest_ma20=float(ma20.iloc[-1]), ma20_slope=float(ma20.diff().iloc[-1]),
        pullback_rebound=float(close.iloc[-1]) / pullback_low - 1.0,
        pullback_rebound_threshold=rebound_threshold,
        current_tqqq_quantity=shares, current_unlevered_quantity=0.0,
        require_ma20_slope=True, allow_pullback=True,
        strategy_equity=budget, initial_reserved=budget * 0.02,
        cash_reserve_floor=0.0, risk_on_cash_reserve_ratio=0.02,
        tqqq_weight=0.45, unlevered_weight=0.45,
        macro_active=risk_reduced,
        macro_route="delever" if route == "risk_reduced" else "crisis" if route == "risk_off" else None,
        macro_leverage_scalar=float(control["leverage_scalar"]) if use_guard else 1.0,
        macro_risk_asset_scalar=float(control["risk_asset_scalar"]) if use_guard else 1.0,
        crisis_defense_enabled=False, true_crisis_active=False,
        volatility_enabled=True, volatility_metric=float(vol["metric"]),
        volatility_entry_threshold=float(vol["entry_threshold"]),
        volatility_exit_threshold=float(vol["exit_threshold"]),
        taco_veto_enabled=False, taco_rebound_context_active=False,
        retention_mode="none", retention_ratio=0.0,
    ))
    target = float(core.target_tqqq_value)
    if target < 0 or target > nav + 1e-8:
        raise ValueError("TARGET_OUTSIDE_BUDGET")
    return {"signal_date": signal_date, "guard_route": guard["canonical_route"],
            "applied_route": route, "drawdown": guard.get("metrics", {}).get("rolling_drawdown"),
            "core_state": core.state, "volatility_applied": core.volatility_applied,
            "member_budget_usd": budget, "member_budget_ratio": budget / nav if nav else 0.0,
            "target_tqqq_usd": target, "target_cash_usd": nav - target,
            "target_tqqq_ratio": target / nav if nav else 0.0}


def _action_schedule(actions: dict, row_dates: set[str]) -> tuple[dict, dict]:
    """Reconstruct effective-date accounting and consequent future sizing/state.

    Provider process_date is not an announcement/known-at timestamp, so this
    accounting is retrospective and does not certify historical event foresight.
    """
    splits: dict[str, float] = {}
    dividends: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for event in actions.get("forward_splits", []):
        day = event["ex_date"]
        ratio = float(event["new_rate"]) / float(event["old_rate"])
        if ratio <= 0 or day in splits:
            raise ValueError("SPLIT_INVALID_OR_DUPLICATE")
        if day in row_dates:
            splits[day] = ratio
    for event in actions.get("cash_dividends", []):
        ex, pay, rate = event["ex_date"], event["payable_date"], float(event["rate"])
        if rate < 0 or pay < ex:
            raise ValueError("DIVIDEND_INVALID")
        if ex in row_dates:
            dividends[ex].append((pay, rate))
    return splits, dividends


def _metrics(ledger: list[dict], initial: float) -> dict:
    nav = [initial, *[x["nav_close"] for x in ledger]]
    peak = initial
    max_drawdown = 0.0
    for value in nav[1:]:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1.0)
    return {"start": ledger[0]["date"], "end": ledger[-1]["date"], "sessions": len(ledger),
            "start_nav_usd": initial, "end_nav_usd": nav[-1],
            "total_return": nav[-1] / initial - 1.0,
            "annualized_return_252_sessions": (nav[-1] / initial) ** (252 / len(ledger)) - 1.0,
            "max_drawdown": max_drawdown,
            "trade_count": sum(x["trade_shares"] != 0 for x in ledger),
            "total_cost_usd": sum(x["cost_usd"] for x in ledger),
            "turnover_usd": sum(abs(x["trade_shares"] * x["tqqq_open"]) for x in ledger),
            "min_cash_usd": min(x["cash_usd"] for x in ledger),
            "max_ledger_identity_error_usd": max(x["identity_error_usd"] for x in ledger)}


def _simulate(rows: list[dict], actions: dict, contract: dict, *, cost_bps: int,
              use_guard: bool, sessions: int | None = None,
              budget_selector=None) -> tuple[dict, list[dict]]:
    start_signal = contract["window"]["first_signal_rule"]
    if start_signal != "index 256 after 257 complete common bars":
        raise ValueError("UNEXPECTED_WINDOW_CONTRACT")
    start = contract["core"]["first_signal_min_qqq_bars"]
    end = len(rows) if sessions is None else min(len(rows), start + sessions)
    if end <= start:
        raise ValueError("INSUFFICIENT_REPLAY_WINDOW")
    shares = 0.0
    cash = float(contract["portfolio"]["research_initial_usd"])
    receivable = 0.0
    pending: dict[str, float] = defaultdict(float)
    dates = {r["date"] for r in rows}
    splits, dividends = _action_schedule(actions, dates)
    def choose_budget(signal_index: int) -> float:
        if budget_selector is None:
            return cash + shares * rows[signal_index]["tqqq_close"] + receivable
        as_of = rows[signal_index]["date"]
        known_actions = {
            key: [event for event in events if event["ex_date"] <= as_of]
            for key, events in actions.items()
        }
        return float(budget_selector(rows[:signal_index + 1], shares=shares,
                                     cash=cash, receivable=receivable,
                                     nav=cash + shares * rows[signal_index]["tqqq_close"] + receivable,
                                     cost_bps=cost_bps, past_actions=known_actions,
                                     contract=contract))

    previous = _decision(rows, start - 1, shares=0, nav=cash, use_guard=use_guard,
                         config=contract, member_budget_usd=choose_budget(start - 1))
    ledger = []
    factor = float(cost_bps) / 10000.0
    prior_nav = cash
    prior_close = rows[start - 1]["tqqq_close"]
    for i in range(start, end):
        row = rows[i]
        day = row["date"]
        old_shares = shares
        split = splits.get(day, 1.0)
        shares *= split
        accrued = 0.0
        for pay, rate in dividends.get(day, []):
            amount = shares * rate
            receivable += amount
            accrued += amount
            pending[pay] += amount
        # A payable date may be a market holiday: settle on the next session.
        due = sum(amount for pay, amount in list(pending.items()) if pay <= day)
        for pay in [pay for pay in pending if pay <= day]:
            del pending[pay]
        if due:
            cash += due
            receivable -= due
        target_qty = math.floor(previous["target_tqqq_usd"] / row["tqqq_open"])
        if target_qty > shares:
            affordable_qty = shares + math.floor(cash / (row["tqqq_open"] * (1 + factor)))
            if target_qty > affordable_qty:
                raise ValueError("TARGET_NOT_SELF_FINANCING")
        trade = target_qty - shares
        cost = abs(trade) * row["tqqq_open"] * factor
        cash -= trade * row["tqqq_open"] + cost
        if cash < -1e-7 or receivable < -1e-7:
            raise ValueError("NEGATIVE_CASH_OR_RECEIVABLE")
        shares = float(target_qty)
        nav = cash + shares * row["tqqq_close"] + receivable
        expected_nav = (prior_nav + old_shares * (split * row["tqqq_close"] - prior_close)
                        + trade * (row["tqqq_close"] - row["tqqq_open"]) - cost + accrued)
        identity_error = abs(nav - expected_nav)
        if identity_error > 1e-6:
            raise ValueError("DAILY_LEDGER_IDENTITY_MISMATCH")
        ledger.append({"date": day, "signal_date": previous["signal_date"],
                       "signal_guard_route": previous["guard_route"],
                       "signal_applied_route": previous["applied_route"],
                       "member_budget_ratio": previous["member_budget_ratio"],
                       "member_budget_usd": previous["member_budget_usd"],
                       "target_tqqq_ratio": previous["target_tqqq_ratio"],
                       "tqqq_open": row["tqqq_open"], "tqqq_close": row["tqqq_close"],
                       "split_ratio": split, "dividend_accrued_usd": accrued,
                       "trade_shares": trade, "cost_usd": cost, "shares": shares,
                       "cash_usd": cash, "receivable_usd": receivable,
                       "nav_close": nav, "cash_ratio": cash / nav,
                       "identity_error_usd": identity_error})
        prior_nav = nav
        prior_close = row["tqqq_close"]
        if i + 1 < end:
            previous = _decision(rows, i, shares=shares, nav=nav, use_guard=use_guard,
                                 config=contract, member_budget_usd=choose_budget(i))
    return _metrics(ledger, float(contract["portfolio"]["research_initial_usd"])), ledger


def _canonical_digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def _load_synthetic_fixture(path: Path) -> tuple[dict, list[dict], list[dict], dict, str]:
    """Read explicit source inputs only; fixture labels never establish qualification."""
    try:
        if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
            raise ValueError()
        raw = path.read_bytes()
        payload = json.loads(raw)
        if (type(payload) is not dict
                or set(payload) != {"synthetic", "contract", "qqq", "tqqq", "actions"}
                or payload["synthetic"] is not True):
            raise ValueError()
        contract = payload["contract"]
        if (type(contract) is not dict
                or set(contract) != {"candidate_id", "core", "qqq_guard", "portfolio", "window", "limitations"}
                or type(contract["limitations"]) is not list
                or any(type(item) is not str for item in contract["limitations"])
                or contract.get("candidate_id") != CANDIDATE
                or contract["core"]["first_signal_min_qqq_bars"] != 257
                or type(contract["core"]["first_signal_min_qqq_bars"]) is not int
                or contract["window"]["first_signal_rule"] != "index 256 after 257 complete common bars"
                or not _is_positive_finite_number(contract["portfolio"]["research_initial_usd"])
                or type(payload["qqq"]) is not list or type(payload["tqqq"]) is not list
                or type(payload["actions"]) is not dict):
            raise ValueError()
        costs = contract["portfolio"]["cost_bps"]
        short = contract["window"]["short_window_sessions"]
        if (type(costs) is not list or not costs or len(set(costs)) != len(costs)
                or any(type(cost) is not int or cost < 0 or not _is_positive_finite_number(cost + 1) for cost in costs)
                or type(short) is not int or short <= 0):
            raise ValueError()
        _canonical_digest(payload)  # Reject all non-finite/non-JSON source values.
        return contract, payload["qqq"], payload["tqqq"], payload["actions"], hashlib.sha256(raw).hexdigest()
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("SYNTHETIC_FIXTURE_INVALID") from exc


def _record_trial_failure(store: PerformanceStore, started: ResearchTrialRecord,
                          status: ResearchTrialStatus, reason: str,
                          error: BaseException, *, terminal_attempted: bool) -> None:
    """Best effort only; never replace an exception or an existing immutable terminal."""
    try:
        if terminal_attempted:
            current = store.load_research_trial(started.domain, started.strategy_profile, started.trial_id)
            if current is None or current.status is not ResearchTrialStatus.STARTED:
                error.add_note("research_terminal_state_unverified" if current is None else "research_terminal_already_recorded")
                return
        terminal = replace(started, status=status, reason_code=reason, run_id=None, param_version=None)
        store.save_research_trial(terminal)
        if store.load_research_trial(started.domain, started.strategy_profile, started.trial_id) != terminal:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
    except BaseException:
        error.add_note("research_terminal_write_failed")


def _journal_scenario(rows: list[dict], actions: dict, frozen_params: str,
                      started: ResearchTrialRecord, store: PerformanceStore,
                      computed_at: str) -> tuple[ResearchTrialRecord, dict | None, list[dict] | None]:
    """Called only by the input-owning entrypoint, with no external output arguments.

    This is single-caller replay protection, not a distributed claim lock. The
    immutable JSON snapshot supplies STARTED, simulation and typed adaptation.
    """
    previous = store.load_research_trial(started.domain, started.strategy_profile, started.trial_id)
    # A start write that raises may already have committed. Preserve uncertainty:
    # no evaluation or compensating terminal; a later read can report incomplete.
    store.save_research_trial(started)
    stage = "start_readback"
    terminal_attempted = False
    try:
        readback = store.load_research_trial(started.domain, started.strategy_profile, started.trial_id)
        if readback is None:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        if previous is not None or readback.status is not ResearchTrialStatus.STARTED:
            return readback, None, None
        if readback != started:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        params = json.loads(frozen_params)
        if readback.actual_params != params:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        stage = "simulation"
        metrics, daily_rows = _simulate(
            rows, actions, params["contract"], cost_bps=params["cost_bps"],
            use_guard=params["use_guard"], sessions=params["sessions"], budget_selector=None)
        if params != json.loads(frozen_params) or readback.actual_params != json.loads(frozen_params):
            raise ValueError("SIMULATOR_PARAMS_CHANGED")
        stage = "ledger"
        result, ledger = build_guard_cash_trial_ledger(
            rows=rows, actions=actions, contract=params["contract"], metrics=metrics,
            daily_rows=daily_rows, started=readback, run_id=started.trial_id,
            param_version=1, computed_at=computed_at)
        terminal = replace(readback, status=ResearchTrialStatus.SUCCEEDED, reason_code="",
                           run_id=result.run_id, param_version=result.param_version)
        stage = "persistence"
        store.save_backtest_result(result)
        store.save_research_ledger(ledger)
        terminal_attempted = True
        store.save_research_trial(terminal)
        if store.load_research_trial(started.domain, started.strategy_profile, started.trial_id,
                                     run_id=result.run_id, param_version=result.param_version) != terminal:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        return terminal, metrics, daily_rows
    except (KeyboardInterrupt, SystemExit) as exc:
        _record_trial_failure(store, started, ResearchTrialStatus.ABORTED, "trial_interrupted", exc,
                              terminal_attempted=terminal_attempted)
        raise
    except Exception as exc:
        rejected = isinstance(exc, ValueError) and stage in {"simulation", "ledger"}
        status = ResearchTrialStatus.REJECTED if rejected else ResearchTrialStatus.FAILED
        reason = {"start_readback": "journal_readback_failed", "simulation": "simulation_rejected" if rejected else "simulation_failed",
                  "ledger": "ledger_rejected" if rejected else "ledger_failed", "persistence": "persistence_failed"}[stage]
        _record_trial_failure(store, started, status, reason, exc, terminal_attempted=terminal_attempted)
        raise


def run_private(root: Path, *, synthetic_fixture: Path | None = None,
                store: PerformanceStore | None = None, trial_namespace: str | None = None) -> dict:
    """Keep the legacy call shape, refusing unqualified real runs before any read.

    Explicit local synthetic source files exercise the same trusted caller and
    journal, always synthetic and unqualified. No caller-provided output or
    qualification flag is accepted. A new namespace is required for a new attempt.
    Exports are presentation only; the validated QPK journal is authoritative.
    source_revision fingerprints this module only, not the full dependency graph.
    """
    if synthetic_fixture is None:
        raise ValueError("REAL_RESEARCH_DATA_UNQUALIFIED")
    if (not isinstance(store, PerformanceStore) or store.cloud_bucket
            or not isinstance(store.local_root, Path) or store.local_root.is_symlink()):
        raise ValueError("LOCAL_RESEARCH_STORE_REQUIRED")
    if (type(trial_namespace) is not str or not trial_namespace or len(trial_namespace) > 300
            or any(char.isspace() or ord(char) < 32 for char in trial_namespace)):
        raise ValueError("TRIAL_NAMESPACE_INVALID")
    if not isinstance(root, Path) or root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("PRIVATE_ROOT_INVALID")
    contract, qqq, tqqq, actions, source_digest = _load_synthetic_fixture(synthetic_fixture)
    rows = _validated_bars(qqq, tqqq)
    start = contract["core"]["first_signal_min_qqq_bars"]
    if len(rows) - start < contract["window"]["short_window_sessions"]:
        raise ValueError("INSUFFICIENT_REPLAY_WINDOW")
    store.local_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    store.local_root.chmod(0o700)
    implementation = {
        "research_adapter": _sha256(Path(__file__)),
        "ues_core": _sha256(Path(decide_tqqq_dual_drive.__code__.co_filename)),
        "ues_indicators": _sha256(Path(_resolve_volatility_delever_thresholds.__code__.co_filename)),
        "qsp_guard": _sha256(Path(build_benchmark_drawdown_guard_signal.__code__.co_filename)),
        "qsp_arbiter": _sha256(Path(build_market_regime_control_signal.__code__.co_filename)),
    }
    input_digest = _canonical_digest({"rows": rows, "actions": actions, "contract": contract})
    now = datetime.now(timezone.utc).isoformat()
    initial = float(contract["portfolio"]["research_initial_usd"])
    report = {"candidate_id": CANDIDATE, "research_only": True, "synthetic": True,
              "data_qualified": False, "execution_authorized": False, "promotion_authorized": False,
              "no_order": True, "status": "succeeded",
              "contract_sha256": _canonical_digest(contract), "implementation_sha256": implementation,
              "source_manifest_sha256": None, "input_sha256": {"synthetic_fixture": source_digest},
              "data_coverage": {"first_bar": rows[0]["date"], "last_bar": rows[-1]["date"],
                                "common_bars": len(rows), "first_signal": rows[start - 1]["date"],
                                "first_trade": rows[start]["date"]},
              "cash_only": {"start": rows[start]["date"], "end": rows[-1]["date"],
                            "sessions": len(rows) - start, "start_nav_usd": initial,
                            "end_nav_usd": initial, "total_return": 0.0},
              "scenarios": {}, "computed_at": now,
              "limitations": contract.get("limitations", []) + ["SYNTHETIC_CALLER_ONLY_NOT_DATA_QUALIFICATION"],
              "journal": {"trial_namespace": trial_namespace, "trial_ids": [], "statuses": {}}}
    fresh_daily = {}
    reused = False
    for cost in contract["portfolio"]["cost_bps"]:
        for guarded, label in ((True, "dynamic_guard_cash"), (False, "fixed_core_cash")):
            for short, window in ((True, "short"), (False, "full")):
                key = f"{label}_{cost}bps_{window}"
                params = {"scenario_id": key, "cost_bps": cost, "use_guard": guarded,
                          "sessions": contract["window"]["short_window_sessions"] if short else None,
                          "contract": contract, "budget_selector": None}
                frozen_params = json.dumps(params, sort_keys=True, separators=(",", ":"), allow_nan=False)
                started = ResearchTrialRecord(
                    trial_id=f"{trial_namespace}:{key}", domain="us_equity", strategy_profile=CANDIDATE,
                    status=ResearchTrialStatus.STARTED, candidate_config_id="sha256:" + _canonical_digest(contract),
                    actual_params=json.loads(frozen_params), param_set_id=key,
                    source_revision="sha256:" + implementation["research_adapter"], input_id="sha256:" + input_digest,
                    window_start=date.fromisoformat(rows[start - 1]["date"]),
                    window_end=date.fromisoformat(rows[start + params["sessions"] - 1]["date"] if short else rows[-1]["date"]),
                    calendar_id="XNYS", periods_per_year=252.0, cost_source="synthetic_declared_bps",
                    cost_inputs={"cost_bps": float(cost)}, reason_code="", synthetic=True,
                    run_id=None, param_version=None,
                    research_identity={"guard_cash_input_sha256": input_digest,
                        "actions_source_id": f"sha256:{source_digest}:synthetic_fixture:TQQQ_actions",
                        "external_cashflow_scope": "closed_research_no_external_flows"})
                terminal, metrics, daily = _journal_scenario(rows, actions, frozen_params, started, store, now)
                report["journal"]["trial_ids"].append(terminal.trial_id)
                report["journal"]["statuses"][key] = terminal.status.value
                if metrics is None:
                    reused = True
                    report["scenarios"][key] = {"status": terminal.status.value, "trial_id": terminal.trial_id,
                                                "run_id": terminal.run_id, "param_version": terminal.param_version}
                else:
                    report["scenarios"][key] = metrics
                    fresh_daily[key] = daily
                if terminal.status is not ResearchTrialStatus.SUCCEEDED:
                    report["status"] = "incomplete" if terminal.status is ResearchTrialStatus.STARTED else terminal.status.value
                    return report
    if reused:
        report["status"] = "stored"
        return report
    # Export failures must not rewrite already-validated immutable SUCCEEDED trials.
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for key, daily in fresh_daily.items():
        path = root / f"private_daily_{key}.json"
        path.write_text(json.dumps(daily, separators=(",", ":")) + "\n")
        path.chmod(0o600)
    path = root / "research_summary.v1.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    path.chmod(0o600)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--synthetic-fixture", type=Path)
    parser.add_argument("--journal-root", type=Path)
    parser.add_argument("--trial-namespace")
    args = parser.parse_args()
    store = PerformanceStore(local_root=args.journal_root, cloud_bucket="") if args.journal_root else None
    report = run_private(args.private_root, synthetic_fixture=args.synthetic_fixture,
                         store=store, trial_namespace=args.trial_namespace)
    print(json.dumps({"candidate_id": report["candidate_id"], "status": report["status"],
                      "synthetic": report["synthetic"], "contract_sha256": report["contract_sha256"],
                      "coverage": report["data_coverage"], "journal": report["journal"],
                      "private_summary": str(args.private_root / "research_summary.v1.json")
                          if report["status"] == "succeeded" else None}, indent=2))
    statuses = report["journal"]["statuses"]
    if (report["status"] not in {"succeeded", "stored"}
            or not statuses or any(status != "succeeded" for status in statuses.values())):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
