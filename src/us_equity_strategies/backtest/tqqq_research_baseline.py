"""Caller-owned offline TQQQ research baseline; not production-equivalent."""
from __future__ import annotations
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
import pandas as pd
from .session_asof_contract import RequestedObservedWindow, SessionClose
from .snapshot_numeric_contract import ValidatedSessionSnapshot

PROFILE = "tqqq_growth_income_research_baseline_v1"
DOMAIN = "us_equity"
TIMING = "next_open"
CONTRACT_VERSION = "us_equity.tqqq_research_baseline.v1"
REQUIRED_SYMBOLS = ("BOXX", "QQQ", "TQQQ")
INITIAL_CASH = 100_000.0
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

class TqqqResearchBaselineError(ValueError):
    """Sanitized baseline contract error."""

@dataclass(frozen=True, slots=True)
class BaselineControlPolicy:
    market_regime: bool = False
    crisis_defense: bool = False
    macro_risk_governor: bool = False
    volatility_delever: bool = False
    retention: bool = False
    taco: bool = False
    production_equivalent: bool = False
    def __post_init__(self) -> None:
        if any(not isinstance(v, bool) for v in (self.market_regime, self.crisis_defense, self.macro_risk_governor, self.volatility_delever, self.retention, self.taco, self.production_equivalent)) or self.production_equivalent:
            raise TqqqResearchBaselineError("invalid baseline control policy")
    def to_wire(self) -> dict[str, bool]:
        return {"crisis_defense": False, "macro_risk_governor": False, "market_regime": False, "production_equivalent": False, "retention": False, "taco": False, "volatility_delever": False}

@dataclass(frozen=True, slots=True)
class TqqqResearchBaselineResult:
    contract_version: str
    profile: str
    domain: str
    execution_timing: str
    source_revision: str
    computed_at: str
    window: RequestedObservedWindow
    as_of: SessionClose
    input_digest: str
    params_digest: str
    run_id: str
    param_set_id: str
    observation_count: int
    equity_curve: tuple[tuple[str, float], ...]
    daily_returns: tuple[tuple[str, float], ...]
    metrics: tuple[tuple[str, float | None], ...]
    control_policy: BaselineControlPolicy
    def to_wire(self) -> dict[str, object]:
        return {"as_of": self.as_of.to_wire(), "computed_at": self.computed_at, "contract_version": self.contract_version, "control_policy": self.control_policy.to_wire(), "daily_returns": [[d, v] for d, v in self.daily_returns], "domain": self.domain, "equity_curve": [[d, v] for d, v in self.equity_curve], "execution_timing": self.execution_timing, "input_digest": self.input_digest, "metrics": {k: v for k, v in self.metrics}, "observation_count": self.observation_count, "param_set_id": self.param_set_id, "params_digest": self.params_digest, "profile": self.profile, "run_id": self.run_id, "source_revision": self.source_revision, "window": self.window.to_wire()}
    def canonical_bytes(self) -> bytes:
        try:
            return json.dumps(self.to_wire(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (UnicodeEncodeError, TypeError, ValueError):
            raise TqqqResearchBaselineError("invalid result wire") from None

def _positive(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TqqqResearchBaselineError(f"invalid {field}")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise TqqqResearchBaselineError(f"invalid {field}")
    return result

def _computed_at(value: object) -> str:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise TqqqResearchBaselineError("invalid computed_at")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise TqqqResearchBaselineError("invalid computed_at") from None
    return value

def _validate_bars(frame: pd.DataFrame, sessions: tuple[SessionClose, ...], window: RequestedObservedWindow) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or not {"date", "symbol", "open", "close"}.issubset(frame.columns) or frame.empty:
        raise TqqqResearchBaselineError("invalid bars")
    data = frame.loc[:, ["date", "symbol", "open", "close"]].copy()
    parsed = pd.to_datetime(data["date"], errors="coerce")
    if parsed.isna().any():
        raise TqqqResearchBaselineError("invalid bar date")
    data["date"] = parsed.dt.date
    if data["symbol"].map(lambda x: not isinstance(x, str) or not x).any():
        raise TqqqResearchBaselineError("invalid symbol")
    pairs = list(zip(data["date"], data["symbol"]))
    if pairs != sorted(pairs) or len(set(pairs)) != len(pairs):
        raise TqqqResearchBaselineError("bars must be strictly sorted and unique")
    for column in ("open", "close"):
        values = pd.to_numeric(data[column], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all() or (values <= 0).any():
            raise TqqqResearchBaselineError("invalid bar price")
        data[column] = values.astype(float)
    expected = tuple(s.trading_date for s in sessions if window.observed_start_date <= s.trading_date <= window.observed_end_date)
    observed = tuple(sorted({d for d in data["date"] if window.observed_start_date <= d <= window.observed_end_date}))
    if observed != expected:
        raise TqqqResearchBaselineError("bars/session calendar mismatch")
    for d in expected:
        if set(data.loc[data["date"] == d, "symbol"]) != set(REQUIRED_SYMBOLS):
            raise TqqqResearchBaselineError("missing required symbol")
    return data

def run_tqqq_research_baseline(*, bars: pd.DataFrame, sessions: tuple[SessionClose, ...], window: RequestedObservedWindow, validated_snapshot: ValidatedSessionSnapshot, source_revision: str, computed_at: str) -> TqqqResearchBaselineResult:
    if not isinstance(window, RequestedObservedWindow) or not isinstance(validated_snapshot, ValidatedSessionSnapshot) or validated_snapshot.window != window:
        raise TqqqResearchBaselineError("invalid session/window snapshot")
    if any(not isinstance(s, SessionClose) for s in sessions) or tuple(sessions) != tuple(sorted(sessions, key=lambda s: s.trading_date)):
        raise TqqqResearchBaselineError("invalid sessions")
    final_session = next((s for s in sessions if s.trading_date == window.as_of), None)
    if validated_snapshot.session != final_session:
        raise TqqqResearchBaselineError("snapshot/as_of mismatch")
    _computed_at(computed_at)
    if not isinstance(source_revision, str) or not source_revision:
        raise TqqqResearchBaselineError("invalid source_revision")
    data = _validate_bars(bars, sessions, window)
    dates = tuple(s.trading_date for s in sessions if window.observed_start_date <= s.trading_date <= window.observed_end_date)
    if len(dates) < 2 or len(data[data["symbol"] == "QQQ"]) < 200:
        raise TqqqResearchBaselineError("insufficient warmup/observations")
    input_digest = hashlib.sha256(data.to_csv(index=False, lineterminator="\n").encode()).hexdigest()
    params = b'{"controls":"disabled","policy":"fixed_qqq_sma200","initial_cash":100000,"cost_bps":0}'
    params_digest = hashlib.sha256(params).hexdigest()
    run_id = hashlib.sha256((PROFILE + "|" + source_revision + "|" + input_digest + "|" + params_digest).encode()).hexdigest()
    grouped = {d: data[data["date"] == d].set_index("symbol") for d in dates}
    cash, holdings = INITIAL_CASH, {"BOXX": 0.0, "TQQQ": 0.0}
    curve: list[tuple[str, float]] = []
    for index, d in enumerate(dates):
        today = grouped[d]
        equity = cash + sum(holdings[s] * float(today.loc[s, "close"]) for s in holdings)
        curve.append((d.isoformat(), float(equity)))
        if index == len(dates) - 1:
            continue
        qqq_history = data[(data["symbol"] == "QQQ") & (data["date"] <= d)]["close"]
        target_tqqq = 0.90 if len(qqq_history) >= 200 and float(qqq_history.tail(200).mean()) < float(today.loc["QQQ", "close"]) else 0.0
        next_day = grouped[dates[index + 1]]
        actual_open_equity = cash + sum(holdings[s] * float(next_day.loc[s, "open"]) for s in holdings)
        holdings["TQQQ"] = actual_open_equity * target_tqqq / float(next_day.loc["TQQQ", "open"])
        holdings["BOXX"] = actual_open_equity * (1.0 - target_tqqq) / float(next_day.loc["BOXX", "open"])
        cash = 0.0
    returns = tuple((curve[i][0], curve[i][1] / curve[i - 1][1] - 1.0) for i in range(1, len(curve)))
    values, daily = [v for _, v in curve], [v for _, v in returns]
    total = values[-1] / values[0] - 1.0
    annualized = (1.0 + total) ** (252 / max(1, len(daily))) - 1.0
    std = float(pd.Series(daily).std(ddof=1)) if len(daily) > 1 else 0.0
    peak, drawdown = values[0], 0.0
    for value in values:
        peak = max(peak, value); drawdown = min(drawdown, value / peak - 1.0)
    metrics = (("annualized_return", float(annualized)), ("annualized_volatility", float(std * math.sqrt(252))), ("max_drawdown", float(drawdown)), ("sharpe_rf0", None if std == 0 else float(sum(daily) / len(daily) / std * math.sqrt(252))), ("total_return", float(total)))
    return TqqqResearchBaselineResult(CONTRACT_VERSION, PROFILE, DOMAIN, TIMING, source_revision, computed_at, window, final_session, input_digest, params_digest, run_id, params_digest, len(curve), tuple(curve), returns, metrics, BaselineControlPolicy())
