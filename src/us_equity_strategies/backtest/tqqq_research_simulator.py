"""Offline, deterministic TQQQ research simulator (no persistence or live I/O)."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from us_equity_strategies.manifests import tqqq_growth_income_manifest
from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan

CONTRACT = "us_equity.tqqq_research_simulator.v1"
PROFILE = "tqqq_growth_income"
DOMAIN = "us_equity"
TIMING = "next_open"
INITIAL_CASH = 100_000.0
REQUIRED_SYMBOLS = frozenset(tqqq_growth_income_manifest.default_config["managed_symbols"]) | {"QQQ"}
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class TqqqResearchError(ValueError):
    """Sanitized contract/input error."""


def _error(message: str) -> TqqqResearchError:
    return TqqqResearchError(message)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise _error(f"invalid {label}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _error(f"invalid {label}") from None
    if not math.isfinite(result):
        raise _error(f"invalid {label}")
    return result


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53 - 1:
            raise _error("invalid parameter")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error("invalid parameter")
        return value
    raise _error("invalid parameter")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (UnicodeEncodeError, TypeError, ValueError):
        raise _error("invalid canonical value") from None


def _validate_timestamp(value: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise _error("invalid computed_at")
    try:
        pd.Timestamp(value)
    except Exception:
        raise _error("invalid computed_at") from None
    return value


def _as_date(value: date | str, label: str) -> date:
    if isinstance(value, pd.Timestamp):
        value = value.date()
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed
    raise _error(f"invalid {label}")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(value[k]) for k in sorted(value, key=str)})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _identity_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class TqqqResearchResult:
    contract: str
    profile: str
    domain: str
    execution_timing: str
    run_id: str
    param_set_id: str
    source_revision: str
    computed_at: str
    start_date: date
    end_date: date
    as_of: date
    input_digest: str
    params_digest: str
    observation_count: int
    equity_curve: tuple[tuple[str, float], ...]
    daily_returns: tuple[tuple[str, float], ...]
    metrics: Mapping[str, float | None]
    params: Mapping[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(), "computed_at": self.computed_at,
            "contract": self.contract, "daily_returns": [list(x) for x in self.daily_returns],
            "domain": self.domain, "end_date": self.end_date.isoformat(),
            "equity_curve": [list(x) for x in self.equity_curve], "execution_timing": self.execution_timing,
            "input_digest": self.input_digest, "metrics": dict(self.metrics),
            "observation_count": self.observation_count, "param_set_id": self.param_set_id,
            "params": _jsonable(self.params), "params_digest": self.params_digest,
            "profile": self.profile, "run_id": self.run_id, "source_revision": self.source_revision,
            "start_date": self.start_date.isoformat(),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_wire())


def _validate_frame(frame: pd.DataFrame, start: date, end: date) -> tuple[pd.DataFrame, tuple[date, ...]]:
    if not isinstance(frame, pd.DataFrame):
        raise _error("input must be DataFrame")
    required = {"date", "symbol", "open", "close"}
    if not required.issubset(frame.columns):
        raise _error("missing required columns")
    df = frame.loc[:, ["date", "symbol", "open", "close"]].copy(deep=True)
    try:
        df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=False).dt.date
    except Exception:
        raise _error("invalid date") from None
    if df["date"].isna().any() or df["symbol"].isna().any():
        raise _error("invalid input")
    df["symbol"] = df["symbol"].astype(str)
    pairs = list(zip(df["date"], df["symbol"], strict=True))
    if pairs != sorted(pairs):
        raise _error("input must be sorted")
    if df.duplicated(["date", "symbol"]).any():
        raise _error("duplicate bar")
    for col in ("open", "close"):
        df[col] = [_finite(v, col) for v in df[col]]
        if (df[col] <= 0).any():
            raise _error("invalid price")
    if not REQUIRED_SYMBOLS.issubset(set(df["symbol"])):
        raise _error("missing required symbol")
    dates = tuple(sorted(set(df["date"])))
    eval_dates = tuple(d for d in dates if start <= d <= end)
    if not eval_dates:
        raise _error("empty evaluation window")
    pre = df[(df["symbol"] == "QQQ") & (df["date"] < start)]
    if len(pre) < 200:
        raise _error("insufficient warmup")
    for d in eval_dates:
        if set(df.loc[df["date"] == d, "symbol"]) < REQUIRED_SYMBOLS:
            raise _error("missing evaluation bar")
    return df.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True), eval_dates


def _planner_kwargs() -> dict[str, Any]:
    accepted = set(inspect.signature(build_rebalance_plan).parameters)
    config = dict(tqqq_growth_income_manifest.default_config)
    return {k: v for k, v in config.items() if k in accepted and k not in {"signal_text_fn", "translator"}}


def simulate_tqqq_research(
    bars: pd.DataFrame,
    *,
    start_date: date | str,
    end_date: date | str,
    source_revision: str,
    computed_at: str,
) -> TqqqResearchResult:
    start, end = _as_date(start_date, "start_date"), _as_date(end_date, "end_date")
    if start > end:
        raise _error("invalid window")
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise _error("invalid source_revision")
    _validate_timestamp(computed_at)
    df, eval_dates = _validate_frame(bars, start, end)
    input_rows = [[d.isoformat(), s, float(o), float(c)] for d, s, o, c in df.itertuples(index=False, name=None)]
    input_digest = hashlib.sha256(_canonical_bytes(input_rows)).hexdigest()
    params = {"commission_bps": 0, "slippage_bps": 0, "initial_cash": INITIAL_CASH, "planner": _planner_kwargs()}
    params_digest = hashlib.sha256(_canonical_bytes(params)).hexdigest()
    identity = {"contract": CONTRACT, "profile": PROFILE, "domain": DOMAIN, "timing": TIMING,
                "start_date": start.isoformat(), "end_date": end.isoformat(), "source_revision": source_revision,
                "input_digest": input_digest, "params_digest": params_digest}
    run_id = "tqqq-" + _identity_digest(identity)[:32]
    param_set_id = "tqqq-params-" + params_digest[:32]
    cash, holdings = INITIAL_CASH, {}
    curves: list[tuple[str, float]] = []
    qqq = df[df["symbol"] == "QQQ"]
    kwargs = _planner_kwargs()
    for index, current in enumerate(eval_dates):
        prices = df[df["date"] == current].set_index("symbol")
        equity = cash + sum(qty * float(prices.loc[symbol, "close"]) for symbol, qty in holdings.items())
        curves.append((current.isoformat(), float(equity)))
        positions = tuple(Position(symbol=s, quantity=float(q), market_value=float(q * prices.loc[s, "close"])) for s, q in sorted(holdings.items()) if q)
        snapshot = PortfolioSnapshot(as_of=datetime.combine(current, datetime.min.time(), tzinfo=timezone.utc), total_equity=float(equity), buying_power=float(cash), cash_balance=float(cash), positions=positions, metadata={})
        history = qqq[qqq["date"] <= current][["date", "close"]]
        plan = build_rebalance_plan(history, snapshot, signal_text_fn=lambda key, **_: key, translator=lambda key, **_: key, **kwargs)
        targets = plan.get("target_values")
        if not isinstance(targets, Mapping) or not set(targets).issubset(REQUIRED_SYMBOLS):
            raise _error("unsupported target")
        clean_targets = {str(s): _finite(v, "target") for s, v in targets.items()}
        if any(v < 0 for v in clean_targets.values()) or sum(clean_targets.values()) > equity + 1e-6:
            raise _error("invalid target")
        if index + 1 < len(eval_dates):
            nxt = eval_dates[index + 1]
            next_prices = df[df["date"] == nxt].set_index("symbol")
            actual_open_equity = cash + sum(qty * float(next_prices.loc[symbol, "open"]) for symbol, qty in holdings.items())
            ratios = {symbol: value / equity for symbol, value in clean_targets.items()}
            actual_targets = {symbol: ratio * actual_open_equity for symbol, ratio in ratios.items()}
            if sum(actual_targets.values()) > actual_open_equity + 1e-6:
                raise _error("invalid target")
            holdings = {s: value / float(next_prices.loc[s, "open"]) for s, value in actual_targets.items() if value}
            cash = float(actual_open_equity - sum(actual_targets.values()))
    returns: list[tuple[str, float]] = []
    for (prev_date, prev), (cur_date, cur) in zip(curves, curves[1:]):
        returns.append((cur_date, float(cur / prev - 1.0)))
    values = np.asarray([v for _, v in curves], dtype=float)
    total_return = float(values[-1] / values[0] - 1.0)
    n = len(returns)
    annualized_return = float((1.0 + total_return) ** (252.0 / max(1, n)) - 1.0) if n else 0.0
    volatility = float(np.std([v for _, v in returns], ddof=1) * math.sqrt(252.0)) if n > 1 else 0.0
    running = np.maximum.accumulate(values)
    max_drawdown = float(np.min(values / running - 1.0))
    sharpe = None if n < 2 or volatility == 0.0 else float(np.mean([v for _, v in returns]) / np.std([v for _, v in returns], ddof=1) * math.sqrt(252.0))
    metrics = MappingProxyType({"total_return": total_return, "annualized_return": annualized_return,
                                "annualized_volatility": volatility, "max_drawdown": max_drawdown, "sharpe_rf0": sharpe})
    return TqqqResearchResult(CONTRACT, PROFILE, DOMAIN, TIMING, run_id, param_set_id, source_revision, computed_at,
                              start, end, end, input_digest, params_digest, len(curves), tuple(curves), tuple(returns), metrics, _freeze(params))
