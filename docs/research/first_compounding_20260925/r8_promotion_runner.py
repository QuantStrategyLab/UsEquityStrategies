"""Bounded in-memory adapter from the R7/R8 account ledger to QPK.

``runner_kind = "real"`` means that each call computes the account ledger and
metrics; it does not attest that caller-supplied inputs, costs, or dates are
real, licensed, point-in-time, or promotion-qualified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from quant_platform_kit.strategy_lifecycle.contracts import (
    BacktestResult,
    PromotionCostModel,
    PurgedWalkForwardFold,
)
from quant_platform_kit.strategy_lifecycle.performance_metrics import compute_window_metrics

import r7_joint_account_compare as r7_engine
import r8_joint_allocation_compare as r8_engine


_HERE = Path(__file__).resolve().parent
_PROFILES = frozenset({"B0", "B1", "B2", "B3", "R8"})
_COSTS = frozenset({5.0, 10.0, 15.0})
_SHA256 = frozenset("0123456789abcdef")


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _finite_price(value: object, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) <= 0):
        raise ValueError("R8_PROMOTION_PRICE_INVALID:" + label)
    return float(value)


class R8PromotionBacktestRunner:
    """Compute bounded folds/OOS from caller-provided, already-admitted memory inputs."""

    runner_kind = "real"

    def __init__(self, *, rows: list[dict], actions: dict, indicators: dict,
                 contract: dict, research_identity: str, source_revision: str,
                 input_binding_sha256: str, session_dates: list[date],
                 calendar_id: str = "XNYS", evidence_kind: str = "synthetic") -> None:
        if not research_identity or not isinstance(research_identity, str):
            raise ValueError("R8_PROMOTION_RESEARCH_IDENTITY_REQUIRED")
        if (not isinstance(source_revision, str) or len(source_revision) != 40
                or any(char not in _SHA256 for char in source_revision.lower())):
            raise ValueError("R8_PROMOTION_SOURCE_REVISION_INVALID")
        if (not isinstance(input_binding_sha256, str) or len(input_binding_sha256) != 64
                or any(char not in _SHA256 for char in input_binding_sha256.lower())):
            raise ValueError("R8_PROMOTION_INPUT_BINDING_INVALID")
        if calendar_id != "XNYS":
            raise ValueError("R8_PROMOTION_CALENDAR_UNSUPPORTED")
        if evidence_kind not in {"synthetic", "research_input_unqualified"}:
            raise ValueError("R8_PROMOTION_EVIDENCE_KIND_INVALID")
        if not rows or len(rows) != len(session_dates):
            raise ValueError("R8_PROMOTION_SESSION_COVERAGE_INVALID")
        row_dates = []
        for row, day in zip(rows, session_dates, strict=True):
            if not isinstance(row, dict) or type(day) is not date:
                raise ValueError("R8_PROMOTION_SESSION_COVERAGE_INVALID")
            parsed = date.fromisoformat(row.get("date", ""))
            if parsed != day:
                raise ValueError("R8_PROMOTION_SESSION_COVERAGE_INVALID")
            row_dates.append(parsed)
        if row_dates != sorted(set(row_dates)):
            raise ValueError("R8_PROMOTION_SESSION_ORDER_INVALID")
        self._rows = copy.deepcopy(rows)
        self._actions = copy.deepcopy(actions)
        self._indicators = copy.deepcopy(indicators)
        self._contract = copy.deepcopy(contract)
        self.research_identity = research_identity
        self.source_revision = source_revision.lower()
        self.input_binding_sha256 = input_binding_sha256.lower()
        self.session_dates = tuple(row_dates)
        self.calendar_id = calendar_id
        self.evidence_kind = evidence_kind
        self._r7 = r7_engine._policy()
        self._r8 = r8_engine._policy()
        if self._r7["initial_research_nav_usd"] != 10_000:
            raise ValueError("R8_PROMOTION_INITIAL_NAV_POLICY_MISMATCH")
        self._input_digest = _digest({
            "research_identity": research_identity,
            "source_revision": self.source_revision,
            "input_binding_sha256": self.input_binding_sha256,
            "rows": self._rows,
            "actions": self._actions,
            "indicators": self._indicators,
            "contract": self._contract,
            "calendar_id": calendar_id,
            "session_dates": [item.isoformat() for item in row_dates],
        })
        self._checkpoints: dict[str, dict] = {}
        self._last_ledgers: dict[str, dict[str, dict]] = {}

    @staticmethod
    def _cost(cost_model: PromotionCostModel) -> tuple[float, dict[str, float]]:
        if not isinstance(cost_model, PromotionCostModel):
            raise TypeError("cost_model must be PromotionCostModel")
        if not isinstance(cost_model.model_id, str) or not cost_model.model_id.strip():
            raise ValueError("R8_PROMOTION_COST_ID_REQUIRED")
        values = (cost_model.commission_bps, cost_model.slippage_bps,
                  cost_model.market_impact_bps)
        if any(isinstance(item, bool) or not isinstance(item, (int, float))
               or not math.isfinite(float(item)) or float(item) < 0 for item in values):
            raise ValueError("R8_PROMOTION_COST_INVALID")
        if float(cost_model.market_impact_bps) != 0.0:
            raise ValueError("R8_PROMOTION_MARKET_IMPACT_UNSUPPORTED")
        total = math.fsum(float(item) for item in values[:2])
        if total not in _COSTS:
            raise ValueError("R8_PROMOTION_COST_SCENARIO_UNSUPPORTED")
        return total, {"commission_bps": float(values[0]),
                       "slippage_bps": float(values[1]),
                       "market_impact_bps": float(values[2])}

    def _profile(self, profile: str, params: Mapping[str, Any]) -> tuple[str, str | None]:
        if profile not in _PROFILES:
            raise ValueError("R8_PROMOTION_PROFILE_UNSUPPORTED")
        if not isinstance(params, Mapping) or params:
            raise ValueError("R8_PROMOTION_PARAMS_ARE_FROZEN")
        if profile == "R8":
            return "B0", "r8"
        return profile, None

    def _bounded_inputs(self, end: date) -> tuple[list[dict], dict, dict, int]:
        if type(end) is not date or end not in self.session_dates:
            raise ValueError("R8_PROMOTION_END_NOT_SESSION")
        end_index = self.session_dates.index(end)
        start_date = date.fromisoformat(self._r7["data"]["first_trade"])
        if end < start_date:
            raise ValueError("R8_PROMOTION_WINDOW_BEFORE_REPLAY_START")
        first_trade_index = self.session_dates.index(start_date) if start_date in self.session_dates else -1
        if first_trade_index < 1:
            raise ValueError("R8_PROMOTION_R7_ANCHOR_MISSING")
        for row in self._rows[first_trade_index - 1:end_index + 1]:
            for symbol in r7_engine.SYMBOLS:
                _finite_price(row.get(symbol.lower() + "_close"), row["date"] + ":" + symbol + ":close")
                if row["date"] >= self._r7["data"]["first_trade"]:
                    _finite_price(row.get(symbol.lower() + "_open"), row["date"] + ":" + symbol + ":open")
        rows = copy.deepcopy(self._rows[:end_index + 1])
        actions = {}
        for symbol in r7_engine.SYMBOLS:
            source = self._actions.get(symbol)
            if not isinstance(source, dict):
                raise ValueError("R8_PROMOTION_ACTIONS_MISSING:" + symbol)
            actions[symbol] = {
                "forward_splits": [copy.deepcopy(event) for event in source.get("forward_splits", [])
                                   if event.get("ex_date", "") <= end.isoformat()],
                "cash_dividends": [copy.deepcopy(event) for event in source.get("cash_dividends", [])
                                   if event.get("ex_date", "") <= end.isoformat()],
            }
        indicators = {key: copy.deepcopy(value) for key, value in self._indicators.items()
                      if key <= end.isoformat()}
        return rows, actions, indicators, end_index

    def _run_window(self, profile: str, params: Mapping[str, Any], start: date, end: date,
                    cost_model: PromotionCostModel, *, stage: str,
                    purge_days: int = 0, embargo_days: int = 0) -> BacktestResult:
        checkpoints_before = copy.deepcopy(self._checkpoints)
        ledgers_before = copy.deepcopy(self._last_ledgers)
        try:
            return self._run_window_impl(profile, params, start, end, cost_model,
                                         stage=stage, purge_days=purge_days,
                                         embargo_days=embargo_days)
        except Exception:
            self._checkpoints = checkpoints_before
            self._last_ledgers = ledgers_before
            raise

    def _run_window_impl(self, profile: str, params: Mapping[str, Any], start: date, end: date,
                         cost_model: PromotionCostModel, *, stage: str,
                         purge_days: int = 0, embargo_days: int = 0) -> BacktestResult:
        path_name, mode = self._profile(profile, params)
        cost_bps, cost_inputs = self._cost(cost_model)
        if type(start) is not date or type(end) is not date or start > end:
            raise ValueError("R8_PROMOTION_WINDOW_INVALID")
        if start not in self.session_dates or end not in self.session_dates:
            raise ValueError("R8_PROMOTION_WINDOW_NOT_SESSIONS")
        rows, actions, indicators, end_index = self._bounded_inputs(end)
        window_days = [day for day in self.session_dates if start <= day <= end]
        if not window_days or len(window_days) != self.session_dates.index(end) - self.session_dates.index(start) + 1:
            raise ValueError("R8_PROMOTION_WINDOW_SESSION_GAP")
        if stage == "fold" and (type(purge_days) is not int or purge_days <= 0
                                 or type(embargo_days) is not int or embargo_days <= 0):
            raise ValueError("R8_PROMOTION_PURGE_EMBARGO_INVALID")
        state_key = self._state_key(profile, params, cost_model)
        prior_state = self._checkpoints.get(state_key)
        checkpoint_out: dict = {}
        kwargs = {}
        reuse_cached_b0 = False
        if prior_state is not None:
            checkpoint = prior_state["checkpoint"]
            checkpoint_date = date.fromisoformat(checkpoint["last_date"])
            if checkpoint_date >= start and profile == "B0":
                if checkpoint_date >= end:
                    reuse_cached_b0 = True
                    cached = self._last_ledgers.get(state_key, {})
                    required = {start.isoformat(), end.isoformat()}
                    if start != date.fromisoformat(self._r7["data"]["first_trade"]):
                        required.add(self.session_dates[self.session_dates.index(start) - 1].isoformat())
                    if not required.issubset(cached):
                        raise ValueError("R8_PROMOTION_B0_CACHED_WINDOW_INCOMPLETE")
                else:
                    kwargs = {"continuation_from_session": checkpoint["last_date"],
                              "continuation_checkpoint": checkpoint}
            elif checkpoint_date >= start:
                raise ValueError("R8_PROMOTION_WINDOW_OVERLAPS_CHECKPOINT")
            else:
                kwargs = {"continuation_from_session": checkpoint["last_date"],
                          "continuation_checkpoint": checkpoint}
        if mode == "r8":
            selector = r8_engine._selector(indicators, self._contract, actions,
                                           self._r7, self._r8)
        else:
            selector = None
        records = dict(self._last_ledgers.get(state_key, {}))
        if not reuse_cached_b0:
            _, ledger = r7_engine._replay(
                rows, actions, indicators, self._contract, self._r7,
                path_name=path_name, cost_bps=int(cost_bps),
                action_selector=selector, candidate_id=self._r8["candidate_id"] if mode else None,
                replay_end_session=end.isoformat(), checkpoint_out=checkpoint_out,
                **kwargs,
            )
            if not ledger or ledger[-1]["date"] != end.isoformat():
                raise ValueError("R8_PROMOTION_REPLAY_END_MISMATCH")
            self._checkpoints[state_key] = {"checkpoint": copy.deepcopy(checkpoint_out)}
            records.update({row["date"]: row for row in ledger})
            self._last_ledgers[state_key] = records
        if not all(day.isoformat() in records for day in window_days):
            raise ValueError("R8_PROMOTION_WINDOW_LEDGER_INCOMPLETE")
        b0_rows = records if path_name == "B0" and mode is None else None
        if b0_rows is None:
            b0_rows, b0_ledger = self._benchmark_rows(start, end, cost_model)
        nav_series = pd.Series(
            [float(records[day.isoformat()]["economic_nav_close_usd"]) for day in window_days],
            index=pd.to_datetime([day.isoformat() for day in window_days]), dtype="float64")
        benchmark_series = pd.Series(
            [float(b0_rows[day.isoformat()]["economic_nav_close_usd"]) for day in window_days],
            index=nav_series.index, dtype="float64")
        previous_day = self.session_dates[self.session_dates.index(start) - 1]
        prior_nav = (float(self._r7["initial_research_nav_usd"])
                     if start == date.fromisoformat(self._r7["data"]["first_trade"])
                     else self._nav_before(profile, params, cost_model, previous_day))
        if prior_nav <= 0 or not math.isfinite(prior_nav):
            raise ValueError("R8_PROMOTION_PRIOR_NAV_INVALID")
        returns = nav_series.pct_change().iloc[1:]
        returns.loc[nav_series.index[0]] = float(nav_series.iloc[0] / prior_nav - 1.0)
        returns = returns.sort_index()
        benchmark_prior = (float(self._r7["initial_research_nav_usd"])
                           if start == date.fromisoformat(self._r7["data"]["first_trade"])
                           else self._nav_before("B0", {}, cost_model, previous_day))
        benchmark_returns = benchmark_series.pct_change().iloc[1:]
        benchmark_returns.loc[benchmark_series.index[0]] = float(benchmark_series.iloc[0] / benchmark_prior - 1.0)
        benchmark_returns = benchmark_returns.sort_index()
        perf = compute_window_metrics(
            returns, benchmark_returns=benchmark_returns, benchmark_symbol="B0",
            risk_free_rate=0.0, periods_per_year=252, calendar_id=self.calendar_id,
        )
        sharpe = float(perf.sharpe_ratio) if math.isfinite(float(perf.sharpe_ratio)) else None
        max_drawdown = float(perf.max_drawdown) if math.isfinite(float(perf.max_drawdown)) else None
        cagr = float(perf.cagr) if math.isfinite(float(perf.cagr)) else None
        if max_drawdown is None or cagr is None:
            raise ValueError("R8_PROMOTION_REQUIRED_METRIC_UNDEFINED")
        def finite_metric(value: object) -> float | None:
            if value is None:
                return None
            number = float(value)
            return number if math.isfinite(number) else None

        return BacktestResult(
            strategy_profile=profile, domain="us_equity",
            param_set_id=(f"{self.research_identity}_{self.evidence_kind}_{profile}_"
                          f"{self.input_binding_sha256}_{self._input_digest}"),
            params={}, sharpe_ratio=sharpe, max_drawdown=max_drawdown, cagr=cagr,
            sortino_ratio=finite_metric(perf.sortino_ratio),
            calmar_ratio=finite_metric(perf.calmar_ratio),
            volatility=float(perf.volatility) if math.isfinite(float(perf.volatility)) else None,
            win_rate=None, total_return=float(perf.total_return),
            start_date=start, end_date=end, observation_count=len(returns),
            benchmark_symbol="B0", benchmark_cagr=(float(perf.benchmark_cagr)
                if perf.benchmark_cagr is not None and math.isfinite(float(perf.benchmark_cagr)) else None),
            benchmark_max_drawdown=(float(perf.benchmark_max_drawdown)
                if perf.benchmark_max_drawdown is not None and math.isfinite(float(perf.benchmark_max_drawdown)) else None),
            excess_cagr=(float(perf.excess_cagr)
                if perf.excess_cagr is not None and math.isfinite(float(perf.excess_cagr)) else None),
            source_script=str(Path(__file__).resolve().relative_to(_HERE.parents[2])),
            computed_at=datetime.now(timezone.utc).isoformat(),
            source_revision=self.source_revision, cost_model=cost_model.model_id,
            cost_inputs=cost_inputs, periods_per_year=252.0, calendar_id=self.calendar_id,
        )

    def _benchmark_rows(self, start: date, end: date, cost_model: PromotionCostModel) -> tuple[dict, list[dict]]:
        key = self._state_key("B0", {}, cost_model)
        rows = self._last_ledgers.get(key, {})
        required = {start.isoformat(), end.isoformat()}
        if start != date.fromisoformat(self._r7["data"]["first_trade"]):
            required.add(self.session_dates[self.session_dates.index(start) - 1].isoformat())
        if not required.issubset(rows):
            checkpoint = self._checkpoints.get(key, {}).get("checkpoint")
            if checkpoint is None:
                replay_start = date.fromisoformat(self._r7["data"]["first_trade"])
            else:
                checkpoint_date = date.fromisoformat(checkpoint["last_date"])
                if checkpoint_date >= end:
                    raise ValueError("R8_PROMOTION_B0_BENCHMARK_COVERAGE_INCOMPLETE")
                checkpoint_index = self.session_dates.index(checkpoint_date)
                replay_start = self.session_dates[checkpoint_index + 1]
            self._run_window("B0", {}, replay_start, end, cost_model, stage="benchmark")
            rows = self._last_ledgers.get(key, {})
        if not required.issubset(rows):
            raise ValueError("R8_PROMOTION_B0_BENCHMARK_COVERAGE_INCOMPLETE")
        return rows, list(rows.values())

    def _state_key(self, profile: str, params: Mapping[str, Any],
                   cost_model: PromotionCostModel) -> str:
        return _digest({"input_digest": self._input_digest, "profile": profile,
                        "params": dict(params), "cost_model": cost_model.to_dict(),
                        "calendar_id": self.calendar_id})

    def _nav_before(self, profile: str, params: Mapping[str, Any], cost_model: PromotionCostModel,
                    day: date) -> float:
        # Daily marks are stored in the runner ledger cache, bound to the same profile/cost.
        key = self._state_key(profile, params, cost_model)
        ledger = self._last_ledgers.get(key, {})
        if day.isoformat() in ledger:
            return float(ledger[day.isoformat()]["economic_nav_close_usd"])
        checkpoint = self._checkpoints.get(key, {}).get("checkpoint", {})
        if checkpoint.get("last_date") == day.isoformat():
            return float(checkpoint["prior_nav_usd"])
        raise ValueError("R8_PROMOTION_PREWINDOW_NAV_UNAVAILABLE")

    def run_purged_fold(self, strategy_profile: str, params: Mapping[str, Any], *,
                        fold: PurgedWalkForwardFold, purge_days: int, embargo_days: int,
                        cost_model: PromotionCostModel) -> BacktestResult:
        if not isinstance(fold, PurgedWalkForwardFold):
            raise TypeError("fold must be PurgedWalkForwardFold")
        if (any(type(item) is not date for item in
                (fold.train_start, fold.train_end, fold.test_start, fold.test_end))
                or fold.train_start > fold.train_end
                or type(purge_days) is not int or type(embargo_days) is not int
                or purge_days <= 0 or embargo_days <= 0
                or fold.train_end + timedelta(days=purge_days) >= fold.test_start
                or fold.test_end < fold.test_start):
            raise ValueError("R8_PROMOTION_FOLD_BOUNDARIES_INVALID")
        return self._run_window(strategy_profile, params, fold.test_start, fold.test_end,
                                cost_model, stage="fold", purge_days=purge_days,
                                embargo_days=embargo_days)

    def run_locked_oos(self, strategy_profile: str, params: Mapping[str, Any], *,
                       start_date: date, end_date: date,
                       cost_model: PromotionCostModel) -> BacktestResult:
        return self._run_window(strategy_profile, params, start_date, end_date,
                                cost_model, stage="oos")
