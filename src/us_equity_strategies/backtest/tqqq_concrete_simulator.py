"""Caller-owned offline TQQQ next-open research simulator."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pandas as pd
from quant_platform_kit.common.models import PortfolioSnapshot, Position

from .session_asof_contract import RequestedObservedWindow, SessionClose, SessionContractError
from .snapshot_numeric_contract import ValidatedSessionSnapshot
from ..manifests import tqqq_growth_income_manifest
from ..strategies.tqqq_growth_income import build_rebalance_plan

PROFILE = "tqqq_growth_income"
CONTRACT = "us_equity.tqqq_research_simulator.v1"
TIMING = "next_open"
INITIAL_CASH = 100_000.0
REQUIRED_SYMBOLS = frozenset(tqqq_growth_income_manifest.default_config["managed_symbols"]) | {"QQQ"}
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class TqqqSimulatorError(ValueError):
    """Sanitized simulator contract error."""


def _date(value: date | str, field: str) -> date:
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise TqqqSimulatorError(f"invalid {field}")


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TqqqSimulatorError(f"invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise TqqqSimulatorError(f"invalid {field}") from None
    if not math.isfinite(result):
        raise TqqqSimulatorError(f"invalid {field}")
    return result


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (UnicodeEncodeError, TypeError, ValueError):
        raise TqqqSimulatorError("invalid canonical value") from None


def _planner_kwargs() -> dict[str, Any]:
    accepted = set(inspect.signature(build_rebalance_plan).parameters)
    return {key: value for key, value in tqqq_growth_income_manifest.default_config.items() if key in accepted}


@dataclass(frozen=True, slots=True)
class TqqqSimulationResult:
    contract: str
    profile: str
    execution_timing: str
    source_revision: str
    computed_at: str
    requested_start_date: date
    requested_end_date: date
    observed_start_date: date
    observed_end_date: date
    as_of: date
    as_of_close_at_utc: str
    run_id: str
    input_digest: str
    observation_count: int
    equity_curve: tuple[tuple[str, float], ...]
    daily_returns: tuple[tuple[str, float], ...]
    metrics: Mapping[str, float | None]

    def to_wire(self) -> dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "as_of_close_at_utc": self.as_of_close_at_utc, "computed_at": self.computed_at, "contract": self.contract, "daily_returns": [list(x) for x in self.daily_returns], "equity_curve": [list(x) for x in self.equity_curve], "execution_timing": self.execution_timing, "input_digest": self.input_digest, "metrics": dict(self.metrics), "observed_end_date": self.observed_end_date.isoformat(), "observed_start_date": self.observed_start_date.isoformat(), "profile": self.profile, "requested_end_date": self.requested_end_date.isoformat(), "requested_start_date": self.requested_start_date.isoformat(), "run_id": self.run_id, "source_revision": self.source_revision}

    def canonical_bytes(self) -> bytes:
        return _canonical(self.to_wire())


def _validate_bars(bars: pd.DataFrame, start: date, end: date) -> tuple[pd.DataFrame, tuple[date, ...]]:
    if not isinstance(bars, pd.DataFrame) or not {"date", "symbol", "open", "close"}.issubset(bars.columns):
        raise TqqqSimulatorError("invalid bars")
    df = bars.loc[:, ["date", "symbol", "open", "close"]].copy(deep=True)
    try:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    except Exception:
        raise TqqqSimulatorError("invalid date") from None
    if df["date"].isna().any() or df["symbol"].isna().any():
        raise TqqqSimulatorError("invalid bars")
    df["symbol"] = df["symbol"].astype(str)
    pairs = list(zip(df["date"], df["symbol"], strict=True))
    if pairs != sorted(pairs) or df.duplicated(["date", "symbol"]).any():
        raise TqqqSimulatorError("bars must be sorted and unique")
    for field in ("open", "close"):
        df[field] = [_finite(value, field) for value in df[field]]
        if (df[field] <= 0).any():
            raise TqqqSimulatorError("invalid price")
    if not REQUIRED_SYMBOLS.issubset(set(df["symbol"])):
        raise TqqqSimulatorError("missing symbol")
    dates = tuple(sorted(set(df["date"])))
    observed = tuple(day for day in dates if start <= day <= end)
    if not observed:
        raise TqqqSimulatorError("empty window")
    if len(df[(df["symbol"] == "QQQ") & (df["date"] < start)]) < 200:
        raise TqqqSimulatorError("insufficient warmup")
    for day in observed:
        if set(df.loc[df["date"] == day, "symbol"]) < REQUIRED_SYMBOLS:
            raise TqqqSimulatorError("missing evaluation bar")
    return df.reset_index(drop=True), observed


def simulate_tqqq(
    bars: pd.DataFrame,
    sessions: tuple[SessionClose, ...],
    *,
    requested_start_date: date | str,
    requested_end_date: date | str,
    source_revision: str,
    computed_at: str,
) -> TqqqSimulationResult:
    start, end = _date(requested_start_date, "requested_start_date"), _date(requested_end_date, "requested_end_date")
    if start > end or not isinstance(source_revision, str) or not source_revision.strip() or not isinstance(computed_at, str) or not _UTC_RE.fullmatch(computed_at):
        raise TqqqSimulatorError("invalid request")
    try:
        computed_timestamp = pd.Timestamp(computed_at)
    except Exception:
        raise TqqqSimulatorError("invalid computed_at") from None
    if computed_timestamp.tzinfo is None:
        raise TqqqSimulatorError("invalid computed_at")
    try:
        window = RequestedObservedWindow.from_sessions(sessions, requested_start_date=start, requested_end_date=end, require_end_observation=True)
    except SessionContractError as exc:
        raise TqqqSimulatorError("invalid session/window") from None
    df, observed_dates = _validate_bars(bars, start, end)
    session_by_date = {item.trading_date: item for item in sessions}
    if any(day not in session_by_date for day in observed_dates):
        raise TqqqSimulatorError("missing session")
    rows = [[day.isoformat(), symbol, float(open_), float(close)] for day, symbol, open_, close in df.itertuples(index=False, name=None)]
    input_digest = hashlib.sha256(_canonical(rows)).hexdigest()
    identity = {"contract": CONTRACT, "profile": PROFILE, "timing": TIMING, "start": start.isoformat(), "end": end.isoformat(), "source_revision": source_revision, "input_digest": input_digest, "cost_bps": 0}
    run_id = "tqqq-" + hashlib.sha256(_canonical(identity)).hexdigest()[:32]
    cash, holdings = INITIAL_CASH, {}
    curves: list[tuple[str, float]] = []
    qqq = df[df["symbol"] == "QQQ"]
    kwargs = _planner_kwargs()
    for index, day in enumerate(observed_dates):
        session = session_by_date[day]
        prices = df[df["date"] == day].set_index("symbol")
        equity = cash + sum(quantity * float(prices.loc[symbol, "close"]) for symbol, quantity in holdings.items())
        curves.append((day.isoformat(), float(equity)))
        positions = tuple(Position(symbol, quantity, quantity * float(prices.loc[symbol, "close"])) for symbol, quantity in sorted(holdings.items()))
        source_snapshot = PortfolioSnapshot(session.close_datetime, equity, cash, cash, positions)
        if index == len(observed_dates) - 1:
            try:
                ValidatedSessionSnapshot.from_snapshot(session, window, source_snapshot)
            except SessionContractError:
                raise TqqqSimulatorError("invalid snapshot") from None
        planner_snapshot = source_snapshot
        history = qqq[qqq["date"] <= day][["date", "close"]]
        plan = build_rebalance_plan(history, planner_snapshot, signal_text_fn=lambda key, **_: key, translator=lambda key, **_: key, **kwargs)
        targets = plan.get("target_values")
        if not isinstance(targets, Mapping) or not set(targets).issubset(REQUIRED_SYMBOLS):
            raise TqqqSimulatorError("unsupported target")
        clean = {str(symbol): _finite(value, "target") for symbol, value in targets.items()}
        if any(value < 0 for value in clean.values()) or sum(clean.values()) > equity + 1e-6:
            raise TqqqSimulatorError("invalid target")
        if index + 1 < len(observed_dates):
            next_day = observed_dates[index + 1]
            next_prices = df[df["date"] == next_day].set_index("symbol")
            actual_open_equity = cash + sum(quantity * float(next_prices.loc[symbol, "open"]) for symbol, quantity in holdings.items())
            ratios = {symbol: value / equity for symbol, value in clean.items()}
            actual_targets = {symbol: ratio * actual_open_equity for symbol, ratio in ratios.items()}
            holdings = {symbol: value / float(next_prices.loc[symbol, "open"]) for symbol, value in actual_targets.items() if value}
            cash = float(actual_open_equity - sum(actual_targets.values()))
    returns = tuple((cur, float(cur_value / prev_value - 1.0)) for (_, prev_value), (cur, cur_value) in zip(curves, curves[1:]))
    values = np.asarray([value for _, value in curves], dtype=float)
    daily = [value for _, value in returns]
    total = float(values[-1] / values[0] - 1.0)
    volatility = float(np.std(daily, ddof=1) * math.sqrt(252)) if len(daily) > 1 else 0.0
    sharpe = None if len(daily) < 2 or volatility == 0 else float(np.mean(daily) / np.std(daily, ddof=1) * math.sqrt(252))
    metrics = {"total_return": total, "annualized_return": float((1 + total) ** (252 / max(1, len(daily))) - 1) if daily else 0.0, "annualized_volatility": volatility, "max_drawdown": float(np.min(values / np.maximum.accumulate(values) - 1)), "sharpe_rf0": sharpe}
    return TqqqSimulationResult(CONTRACT, PROFILE, TIMING, source_revision, computed_at, start, end, observed_dates[0], observed_dates[-1], observed_dates[-1], session_by_date[observed_dates[-1]].close_at_utc, run_id, input_digest, len(curves), tuple(curves), returns, MappingProxyType(metrics))
