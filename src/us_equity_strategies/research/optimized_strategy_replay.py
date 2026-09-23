"""Fixture-only replay of pre-risk-gate SOXL and TQQQ decisions.

The live entrypoints pass these same builders through ``apply_risk_gate``.
This module calls the builders directly so a synthetic ledger can record the
strategy targets. It does not approve, execute, or bypass that live gate.
It is fixture-only and is not a complete optimized-profile historical backtest.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import pairwise
from time import perf_counter
from typing import Any

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.common.strategy_contracts import StrategyContext
from quant_platform_kit.strategy_lifecycle.contracts import (
    BacktestResult,
    PromotionCostModel,
)

from us_equity_strategies.entrypoints import (
    _build_soxl_soxx_trend_income_decision,
    _build_tqqq_growth_income_decision,
)
from us_equity_strategies.entrypoints._common import (
    default_signal_text_fn,
    default_translator,
)
from us_equity_strategies.manifests import (
    soxl_soxx_trend_income_manifest,
    tqqq_growth_income_manifest,
)
from us_equity_strategies.strategies.soxl_soxx_trend_income import (
    _as_bool as _control_enabled,
)
from us_equity_strategies.strategies.soxl_soxx_trend_income import (
    _as_positive_int as _control_window,
)

REPLAY_GAPS = (
    "PRE_RISK_GATE_NOT_LIVE_EXECUTABLE",
    "CALLER_SUPPLIED_INDICATORS_AND_BENCHMARK",
    "CASH_BPS_FEE_NOT_ADVERSE_FILL",
    "NO_SHARE_LOT_ROUNDING",
    "NO_CORPORATE_ACTIONS",
    "NO_MIN_TRADE_OR_SETTLEMENT_FILTER",
    "NO_OPTION_OVERLAY_FILLS",
    "NAV_MARKED_AT_CLOSE",
    "EXPLICIT_PERIODS_PER_YEAR",
    "INITIAL_SESSION_EXCLUDED_FROM_RETURNS",
    "WIN_RATE_NOT_OBSERVED",
    "FIXTURE_ONLY_NO_STATIC_PLUGIN_STATE",
    "NO_BENCHMARK_METRICS",
    "NO_SORTINO",
)
_PROFILES = {
    "soxl_soxx_trend_income": (
        soxl_soxx_trend_income_manifest,
        _build_soxl_soxx_trend_income_decision,
    ),
    "tqqq_growth_income": (
        tqqq_growth_income_manifest,
        _build_tqqq_growth_income_decision,
    ),
}
_EXTRA_CONFIG = frozenset(
    {"signal_effective_after_trading_days", "translator", "signal_text_fn"}
)
_MIN_TQQQ_BARS = 200
_DEFAULT_CALLABLES = {
    "translator": default_translator,
    "signal_text_fn": default_signal_text_fn,
}
_POSITIVE_METRICS = frozenset({"price", "ma_trend", "ma20", "bb_mid", "bb_upper", "bb_lower"})


class OptimizedStrategyReplayError(ValueError):
    """Rejected research replay input or decision."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise OptimizedStrategyReplayError(code)


def _text(value: object, code: str) -> str:
    if type(value) is not str or not value or value != value.strip() or any(ord(character) < 32 for character in value):
        _fail(code)
    return value


def _number(value: object, code: str, *, positive: bool = False) -> float:
    number = _signed(value, code)
    if number < 0.0 or (positive and number <= 0.0):
        _fail(code)
    return number


def _signed(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code)
    number = float(value)
    if not math.isfinite(number):
        _fail(code)
    return number


@dataclass(frozen=True, slots=True)
class ReplayIdentity:
    strategy_profile: str
    domain: str
    param_set_id: str
    source_revision: str


@dataclass(frozen=True, slots=True)
class DatedBar:
    session: date
    symbol: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    signal_effective_after_trading_days: int
    execution_timing_contract: str
    fill_price_field: str
    nav_mark_field: str


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    identity: ReplayIdentity
    runtime_config: Mapping[str, Any]
    calendar: tuple[date, ...]
    initial_cash: float
    initial_quantities: Mapping[str, float]
    prices: tuple[DatedBar, ...]
    execution: ExecutionAssumptions
    cost_model: PromotionCostModel
    computed_at: str
    evidence_use: str
    promotion_eligible: bool
    calendar_id: str
    periods_per_year: float
    derived_indicators: Mapping[date, Mapping[str, Mapping[str, float]]] | None = None
    benchmark_bars: tuple[DatedBar, ...] = ()
    portfolio_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DailyReplayPoint:
    session: date
    cash: float
    fees: float
    nav: float
    daily_return: float | None
    holdings: tuple[tuple[str, float], ...]
    market_values: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class OptimizedStrategyReplay:
    points: tuple[DailyReplayPoint, ...]
    backtest: BacktestResult
    live_executable: bool
    risk_gate_applied: bool
    gaps: tuple[str, ...]


def _symbols(config: Mapping[str, Any]) -> tuple[str, ...]:
    raw = config.get("managed_symbols")
    if type(raw) is not tuple or not raw or any(type(symbol) is not str or not symbol for symbol in raw):
        _fail("MISSING_FIELD:managed_symbols")
    if len(set(raw)) != len(raw):
        _fail("INVALID_FIELD:managed_symbols")
    return raw


def _config(profile: str, runtime_config: object) -> tuple[Any, Callable, dict[str, Any], tuple[str, ...]]:
    if profile not in _PROFILES:
        _fail("INVALID_FIELD:strategy_profile")
    manifest, builder = _PROFILES[profile]
    if not isinstance(runtime_config, Mapping):
        _fail("MISSING_FIELD:runtime_config")
    required = set(manifest.default_config) | {"signal_effective_after_trading_days", "translator"}
    if profile == "tqqq_growth_income":
        required.add("signal_text_fn")
    missing = sorted(required - set(runtime_config))
    if missing:
        _fail("MISSING_FIELD:" + ",".join(missing))
    allowed = set(manifest.default_config) | _EXTRA_CONFIG
    if profile == "tqqq_growth_income":
        allowed.add("dual_drive_macro_risk_governor_enabled")
    unknown = sorted(set(runtime_config) - allowed)
    if unknown:
        _fail("UNKNOWN_FIELD:" + ",".join(unknown))
    if type(runtime_config["signal_effective_after_trading_days"]) is not int or runtime_config["signal_effective_after_trading_days"] != 1:
        _fail("INVALID_FIELD:signal_effective_after_trading_days")
    _require_default_callable(runtime_config, "translator")
    if profile == "tqqq_growth_income" or "signal_text_fn" in runtime_config:
        _require_default_callable(runtime_config, "signal_text_fn")
    config = dict(runtime_config)
    _reject_unsimulated(profile, config)
    return manifest, builder, config, _symbols(config)


def _reject_unsimulated(profile: str, config: Mapping[str, Any]) -> None:
    """Reject controls this fixture runner would otherwise ignore."""

    if profile == "tqqq_growth_income" and config.get("benchmark_symbol") != "QQQ":
        _fail("UNSUPPORTED_BENCHMARK")
    if any(
        _control_enabled(config.get(key))
        for key in (
            "option_overlay_enabled",
            "option_growth_overlay_enabled",
            "option_income_overlay_enabled",
        )
    ):
        _fail("UNSIMULATED_OPTION_OVERLAY")
    retention_key = (
        "dual_drive_volatility_delever_retention_mode"
        if profile == "tqqq_growth_income"
        else "blend_gate_volatility_delever_retention_mode"
    )
    retention_mode = str(config.get(retention_key) or "").strip().lower()
    plugin_flags = ["market_regime_control_enabled"]
    if profile == "tqqq_growth_income":
        plugin_flags.extend(
            (
                "dual_drive_crisis_defense_enabled",
                "dual_drive_volatility_delever_taco_veto_enabled",
            )
        )
        macro_key = "dual_drive_macro_risk_governor_enabled"
        macro_enabled = macro_key not in config or _control_enabled(config.get(macro_key))
    else:
        macro_enabled = False
    if (
        retention_mode not in {"none", "fixed"}
        or macro_enabled
        or any(_control_enabled(config.get(key)) for key in plugin_flags)
    ):
        _fail("UNSIMULATED_TARGET_PLUGIN")


def _execution(value: object) -> ExecutionAssumptions:
    if type(value) is not ExecutionAssumptions:
        _fail("MISSING_FIELD:execution")
    if value.signal_effective_after_trading_days != 1 or type(value.signal_effective_after_trading_days) is not int:
        _fail("INVALID_FIELD:signal_effective_after_trading_days")
    if value.execution_timing_contract != "next_trading_day":
        _fail("INVALID_FIELD:execution_timing_contract")
    if value.fill_price_field not in {"open", "close"}:
        _fail("INVALID_FIELD:fill_price_field")
    if value.nav_mark_field != "close":
        _fail("INVALID_FIELD:nav_mark_field")
    return value


def _calendar(value: object) -> tuple[date, ...]:
    if type(value) is not tuple or len(value) < 2 or any(type(session) is not date for session in value):
        _fail("CALENDAR_INVALID")
    if any(left >= right for left, right in pairwise(value)):
        _fail("CALENDAR_INVALID")
    return value


def _bar_price(bar: DatedBar) -> None:
    for name in ("open", "high", "low", "close"):
        _number(getattr(bar, name), "NONFINITE_INPUT", positive=True)
    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
        _fail("INVALID_FIELD:ohlc")


def _prices(bars: object, calendar: tuple[date, ...], symbols: tuple[str, ...]) -> dict[tuple[date, str], DatedBar]:
    if type(bars) is not tuple:
        _fail("MISSING_FIELD:prices")
    indexed: dict[tuple[date, str], DatedBar] = {}
    for bar in bars:
        if type(bar) is not DatedBar or bar.session not in calendar or bar.symbol not in symbols:
            _fail("INPUT_GAP")
        _bar_price(bar)
        if (bar.session, bar.symbol) in indexed:
            _fail("INPUT_GAP")
        indexed[(bar.session, bar.symbol)] = bar
    expected = {(session, symbol) for session in calendar for symbol in symbols}
    if set(indexed) != expected:
        _fail("INPUT_GAP")
    return indexed


def _quantities(value: object, symbols: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(symbols):
        _fail("MISSING_FIELD:initial_quantities")
    return {symbol: _number(value[symbol], "NONFINITE_INPUT") for symbol in symbols}


def _metric(key: str, value: object) -> float:
    if key in _POSITIVE_METRICS or key.startswith("realized_volatility"):
        return _number(value, "NONFINITE_INPUT", positive=key in _POSITIVE_METRICS)
    return _signed(value, "NONFINITE_INPUT")


def _required_indicator_metrics(config: Mapping[str, Any]) -> dict[str, set[str]]:
    trend = str(config.get("blend_gate_trend_source") or "SOXX").strip().lower() or "soxx"
    required = {"soxl": {"price", "ma_trend"}, trend: {"price", "ma_trend"}}
    if _control_enabled(config.get("blend_gate_rsi_cap_enabled")):
        required[trend].add("rsi14")
    if _control_enabled(config.get("blend_gate_dynamic_rsi_threshold_enabled")):
        required[trend].add("rsi14_dynamic_threshold")
    if _control_enabled(config.get("blend_gate_bollinger_cap_enabled")):
        required[trend].update({"bb_mid", "bb_upper", "bb_lower"})
    if _control_enabled(config.get("blend_gate_volatility_delever_enabled")):
        symbol = str(config.get("blend_gate_volatility_delever_symbol") or trend).strip().lower() or trend
        window = _control_window(config.get("blend_gate_volatility_delever_window"), default=10)
        required.setdefault(symbol, set()).add(f"realized_volatility_{window}")
        mode = str(config.get("blend_gate_volatility_delever_threshold_mode") or "").strip().lower()
        if mode == "rolling_percentile":
            required[symbol].add(f"realized_volatility_{window}_dynamic_threshold")
            required[symbol].add(f"realized_volatility_{window}_dynamic_sample_count")
    return required


def _indicators(
    value: object,
    signals: tuple[date, ...],
    config: Mapping[str, Any],
) -> dict[date, dict[str, dict[str, float]]]:
    if not isinstance(value, Mapping) or set(value) != set(signals):
        _fail("MISSING_FIELD:derived_indicators")
    required = _required_indicator_metrics(config)
    parsed: dict[date, dict[str, dict[str, float]]] = {}
    for session in signals:
        payload = value[session]
        if not isinstance(payload, Mapping):
            _fail("MISSING_FIELD:derived_indicators")
        session_payload: dict[str, dict[str, float]] = {}
        for symbol, metrics in payload.items():
            if type(symbol) is not str or not isinstance(metrics, Mapping):
                _fail("MISSING_FIELD:derived_indicators")
            session_payload[symbol] = {
                key: _metric(key, metric)
                for key, metric in metrics.items()
                if type(key) is str
            }
            if set(session_payload[symbol]) != set(metrics):
                _fail("MISSING_FIELD:derived_indicators")
        missing = [
            f"{symbol}.{metric}"
            for symbol, metrics in sorted(required.items())
            for metric in sorted(metrics)
            if metric not in session_payload.get(symbol, {})
        ]
        if missing:
            _fail("MISSING_FIELD:derived_indicators." + ",".join(missing))
        parsed[session] = session_payload
    return parsed


def _required_benchmark_bars(config: Mapping[str, Any]) -> int:
    """Bars visible on the signal date for windows this config actually executes."""

    required = _MIN_TQQQ_BARS
    if not _control_enabled(config.get("dual_drive_volatility_delever_enabled")):
        return required
    window = _control_window(config.get("dual_drive_volatility_delever_window"), default=5)
    needed = window + 1
    mode = str(config.get("dual_drive_volatility_delever_threshold_mode") or "fixed").strip().lower()
    if mode == "rolling_percentile":
        lookback = _control_window(config.get("dual_drive_volatility_delever_dynamic_lookback"), default=252)
        min_periods = _control_window(
            config.get("dual_drive_volatility_delever_dynamic_min_periods"),
            default=min(126, lookback),
        )
        needed = window + max(1, min(lookback, min_periods))
    return max(required, needed)


def _benchmark(bars: object, calendar: tuple[date, ...], signals: tuple[date, ...]) -> tuple[DatedBar, ...]:
    if type(bars) is not tuple or not bars:
        _fail("MISSING_FIELD:benchmark_bars")
    parsed: list[DatedBar] = []
    for bar in bars:
        if type(bar) is not DatedBar or bar.symbol != "QQQ" or bar.session > calendar[-1]:
            _fail("INPUT_GAP")
        _bar_price(bar)
        if parsed and bar.session <= parsed[-1].session:
            _fail("INPUT_GAP")
        parsed.append(bar)
    sessions = {bar.session for bar in parsed}
    if any(signal not in sessions for signal in signals):
        _fail("INPUT_GAP")
    return tuple(parsed)


def _metadata(value: object) -> dict[str, Any]:
    if value is None or (isinstance(value, Mapping) and len(value) == 0):
        return {}
    _fail("NONEMPTY_PORTFOLIO_METADATA")


def _cost(value: object) -> tuple[PromotionCostModel, float]:
    if type(value) is not PromotionCostModel:
        _fail("MISSING_FIELD:cost_model")
    _text(value.model_id, "INVALID_FIELD:cost_model")
    rates = (
        _number(value.commission_bps, "NONFINITE_INPUT"),
        _number(value.slippage_bps, "NONFINITE_INPUT"),
        _number(value.market_impact_bps, "NONFINITE_INPUT"),
    )
    return value, sum(rates) / 10_000.0


def _field(bar: DatedBar, field_name: str) -> float:
    value = bar.open if field_name == "open" else bar.close
    return _number(value, "NONFINITE_INPUT", positive=True)


def _snapshot(session: date, cash: float, quantities: Mapping[str, float], closes: Mapping[str, float], metadata: Mapping[str, Any]) -> PortfolioSnapshot:
    positions = tuple(
        Position(symbol=symbol, quantity=quantity, market_value=quantity * closes[symbol])
        for symbol, quantity in sorted(quantities.items())
        if quantity != 0.0
    )
    payload = dict(metadata)
    payload["market_currency_cash"] = cash
    return PortfolioSnapshot(
        as_of=datetime(session.year, session.month, session.day, tzinfo=UTC),
        total_equity=cash + sum(quantity * closes[symbol] for symbol, quantity in quantities.items()),
        buying_power=cash,
        cash_balance=cash,
        positions=positions,
        metadata=payload,
    )


def _targets(decision: Any, symbols: tuple[str, ...]) -> dict[str, float]:
    if type(decision.positions) is not tuple or decision.risk_flags:
        _fail("DECISION_INVALID")
    found: dict[str, float] = {}
    for position in decision.positions:
        symbol = getattr(position, "symbol", None)
        if type(symbol) is not str or symbol in found:
            _fail("DECISION_INVALID")
        found[symbol] = _number(getattr(position, "target_value", None), "DECISION_INVALID")
    if set(found) != set(symbols):
        _fail("DECISION_INVALID")
    return found


def _check_timing(decision: Any, signal: date, effective: date) -> None:
    diagnostics = decision.diagnostics
    if (
        not isinstance(diagnostics, Mapping)
        or diagnostics.get("signal_date") != signal.isoformat()
        or diagnostics.get("effective_date") != effective.isoformat()
        or diagnostics.get("execution_timing_contract") != "next_trading_day"
        or diagnostics.get("signal_effective_after_trading_days") != 1
    ):
        _fail("TIMING_MISMATCH")


def _rebalance(
    cash: float,
    quantities: dict[str, float],
    targets: Mapping[str, float],
    fills: Mapping[str, float],
    cost_rate: float,
) -> tuple[float, dict[str, float], float]:
    updated = dict(quantities)
    traded = 0.0
    for symbol in sorted(targets):
        fill = fills[symbol]
        delta = targets[symbol] / fill - quantities[symbol]
        traded += abs(delta) * fill
        cash -= delta * fill
        updated[symbol] = quantities[symbol] + delta
    fee = traded * cost_rate
    cash -= fee
    if not math.isfinite(cash) or cash < -1e-8:
        _fail("CASH_INVALID")
    return (0.0 if abs(cash) <= 1e-8 else cash), updated, fee


def _metrics(
    points: tuple[DailyReplayPoint, ...],
    initial_nav: float,
    periods_per_year: float,
) -> dict[str, float | None]:
    returns = tuple(point.daily_return for point in points if point.daily_return is not None)
    count = len(returns)
    if count != len(points) - 1 or count < 1:
        _fail("CASH_INVALID")
    total_return = points[-1].nav / initial_nav - 1.0
    if not math.isfinite(total_return) or 1.0 + total_return <= 0.0:
        _fail("CASH_INVALID")
    peak = initial_nav
    max_drawdown = 0.0
    for point in points:
        peak = max(peak, point.nav)
        max_drawdown = min(max_drawdown, point.nav / peak - 1.0)
    cagr = (1.0 + total_return) ** (periods_per_year / count) - 1.0
    sharpe = None
    volatility = None
    if count > 1:
        mean = math.fsum(returns) / count
        variance = math.fsum((value - mean) ** 2 for value in returns) / (count - 1)
        if variance > 0.0:
            volatility = math.sqrt(variance) * math.sqrt(periods_per_year)
            sharpe = mean / math.sqrt(variance) * math.sqrt(periods_per_year)
        else:
            volatility = 0.0
    return {
        "sharpe_ratio": sharpe,
        "calmar_ratio": (cagr / abs(max_drawdown)) if max_drawdown < 0.0 else None,
        "max_drawdown": max_drawdown,
        "cagr": cagr,
        "volatility": volatility,
        "win_rate": None,
        "total_return": total_return,
        "observation_count": float(count),
    }


def _jsonable(value: object) -> Any:
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        return _signed(value, "NONSERIALIZABLE_CONFIG")
    if type(value) in (list, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping) and all(type(key) is str for key in value):
        return {key: _jsonable(item) for key, item in value.items()}
    _fail("NONSERIALIZABLE_CONFIG")
    return None


def _canonical(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _callable_name(function: Callable[..., Any]) -> str:
    return f"{function.__module__}.{function.__qualname__}"


def _require_default_callable(config: Mapping[str, Any], key: str) -> None:
    if config.get(key) is not _DEFAULT_CALLABLES[key]:
        _fail(f"INVALID_FIELD:{key}")


def _effective_config(config: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in config.items():
        if key in _DEFAULT_CALLABLES:
            payload[key] = _callable_name(_DEFAULT_CALLABLES[key])
        else:
            payload[key] = _jsonable(value)
    return payload


def replay_optimized_strategy(request: ReplayRequest) -> OptimizedStrategyReplay:
    """Replay explicit inputs through the pre-risk-gate strategy builders."""

    if type(request) is not ReplayRequest or type(request.identity) is not ReplayIdentity:
        _fail("MISSING_FIELD:identity")
    identity = request.identity
    profile = _text(identity.strategy_profile, "INVALID_FIELD:strategy_profile")
    if _text(identity.domain, "INVALID_FIELD:domain") != "us_equity":
        _fail("INVALID_FIELD:domain")
    param_set_id = _text(identity.param_set_id, "INVALID_FIELD:param_set_id")
    source_revision = _text(identity.source_revision, "INVALID_FIELD:source_revision")
    computed_at = _text(request.computed_at, "MISSING_FIELD:computed_at")
    if request.evidence_use != "fixture":
        _fail("REAL_ENTRY_PIT_REQUIRED")
    if type(request.promotion_eligible) is not bool or request.promotion_eligible:
        _fail("INVALID_FIELD:promotion_eligible")
    calendar_id = _text(request.calendar_id, "MISSING_FIELD:calendar_id")
    periods_per_year = _number(request.periods_per_year, "NONFINITE_INPUT", positive=True)
    _manifest, builder, config, symbols = _config(profile, request.runtime_config)
    execution = _execution(request.execution)
    if config["signal_effective_after_trading_days"] != execution.signal_effective_after_trading_days:
        _fail("TIMING_MISMATCH")
    calendar = _calendar(request.calendar)
    prices = _prices(request.prices, calendar, symbols)
    quantities = _quantities(request.initial_quantities, symbols)
    initial_quantities = dict(quantities)
    cash = _number(request.initial_cash, "NONFINITE_INPUT")
    initial_cash = cash
    metadata = _metadata(request.portfolio_metadata)
    cost_model, cost_rate = _cost(request.cost_model)
    signals = calendar[:-1]
    indicators = None
    benchmark: tuple[DatedBar, ...] = ()
    if profile == "soxl_soxx_trend_income":
        if request.benchmark_bars:
            _fail("UNKNOWN_FIELD:benchmark_bars")
        indicators = _indicators(request.derived_indicators, signals, config)
    else:
        if request.derived_indicators is not None:
            _fail("UNKNOWN_FIELD:derived_indicators")
        benchmark = _benchmark(request.benchmark_bars, calendar, signals)
    started = perf_counter()
    points: list[DailyReplayPoint] = []
    initial_closes = {symbol: _field(prices[(calendar[0], symbol)], "close") for symbol in symbols}
    initial_nav = cash + sum(quantities[symbol] * initial_closes[symbol] for symbol in symbols)
    if initial_nav <= 0.0:
        _fail("CASH_INVALID")
    previous_nav = initial_nav
    pending: tuple[date, dict[str, float]] | None = None
    for index, session in enumerate(calendar):
        closes = {symbol: _field(prices[(session, symbol)], "close") for symbol in symbols}
        fees = 0.0
        if pending is not None:
            if pending[0] != session:
                _fail("TIMING_MISMATCH")
            fills = {symbol: _field(prices[(session, symbol)], execution.fill_price_field) for symbol in symbols}
            cash, quantities, fees = _rebalance(cash, quantities, pending[1], fills, cost_rate)
            pending = None
        market_values = {symbol: quantities[symbol] * closes[symbol] for symbol in symbols}
        nav = cash + sum(market_values.values())
        if not math.isfinite(nav) or nav <= 0.0:
            _fail("CASH_INVALID")
        points.append(
            DailyReplayPoint(
                session=session,
                cash=cash,
                fees=fees,
                nav=nav,
                daily_return=None if index == 0 else nav / previous_nav - 1.0,
                holdings=tuple((symbol, quantities[symbol]) for symbol in sorted(symbols)),
                market_values=tuple((symbol, market_values[symbol]) for symbol in sorted(symbols)),
            )
        )
        previous_nav = nav
        if index == len(calendar) - 1:
            continue
        effective = calendar[index + 1]
        if profile == "soxl_soxx_trend_income":
            market_data: dict[str, Any] = {"derived_indicators": deepcopy(indicators[session])}
        else:
            history = [bar for bar in benchmark if bar.session <= session]
            if not history or history[-1].session != session:
                _fail("INPUT_GAP")
            if len(history) < _required_benchmark_bars(config):
                _fail("INSUFFICIENT_BENCHMARK")
            market_data = {
                "benchmark_history": [
                    {"close": bar.close, "high": bar.high, "low": bar.low}
                    for bar in history
                ]
            }
        try:
            decision = builder(
                StrategyContext(
                    as_of=session.isoformat(),
                    market_data=market_data,
                    portfolio=_snapshot(session, cash, quantities, closes, metadata),
                    runtime_config=deepcopy(config),
                )
            )
        except OptimizedStrategyReplayError:
            raise
        except Exception as exc:
            raise OptimizedStrategyReplayError("DECISION_INVALID") from exc
        _check_timing(decision, session, effective)
        pending = (effective, _targets(decision, symbols))
    if pending is not None:
        _fail("TIMING_MISMATCH")
    ledger = tuple(points)
    metrics = _metrics(ledger, initial_nav, periods_per_year)
    builder_name = "_build_soxl_soxx_trend_income_decision" if profile == "soxl_soxx_trend_income" else "_build_tqqq_growth_income_decision"
    effective_config = _effective_config(config)
    cost_identity = {
        "model_id": cost_model.model_id,
        "commission_bps": float(cost_model.commission_bps),
        "slippage_bps": float(cost_model.slippage_bps),
        "market_impact_bps": float(cost_model.market_impact_bps),
    }
    input_identity: dict[str, Any] = {
        "calendar": [session.isoformat() for session in calendar],
        "prices": [
            {
                "session": bar.session.isoformat(),
                "symbol": bar.symbol,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }
            for bar in request.prices
        ],
    }
    if indicators is not None:
        input_identity["derived_indicators"] = {
            session.isoformat(): payload for session, payload in indicators.items()
        }
    else:
        input_identity["benchmark_bars"] = [
            {
                "session": bar.session.isoformat(),
                "symbol": bar.symbol,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }
            for bar in benchmark
        ]
    run_identity = {
        "strategy_profile": profile,
        "param_set_id": param_set_id,
        "source_revision": source_revision,
        "calendar_id": calendar_id,
        "periods_per_year": periods_per_year,
        "effective_runtime_config": effective_config,
        "initial_cash": initial_cash,
        "initial_quantities": {symbol: initial_quantities[symbol] for symbol in sorted(symbols)},
        "inputs": input_identity,
        "cost": cost_identity,
        "execution": {
            "signal_effective_after_trading_days": execution.signal_effective_after_trading_days,
            "execution_timing_contract": execution.execution_timing_contract,
            "fill_price_field": execution.fill_price_field,
            "nav_mark_field": execution.nav_mark_field,
        },
    }
    backtest = BacktestResult(
        strategy_profile=profile,
        domain="us_equity",
        param_set_id=param_set_id,
        params={
            "research_only": True,
            "fixture_only": True,
            "evidence_use": "fixture",
            "promotion_eligible": False,
            "live_executable": False,
            "risk_gate_applied": False,
            "decision_builder": builder_name,
            "calendar_id": calendar_id,
            "periods_per_year": periods_per_year,
            "effective_runtime_config": effective_config,
            "effective_runtime_config_sha256": _digest(effective_config),
            "execution_timing_contract": execution.execution_timing_contract,
            "signal_effective_after_trading_days": execution.signal_effective_after_trading_days,
            "fill_price_field": execution.fill_price_field,
            "nav_mark_field": execution.nav_mark_field,
            "gaps": REPLAY_GAPS,
        },
        sharpe_ratio=metrics["sharpe_ratio"],
        calmar_ratio=metrics["calmar_ratio"],
        max_drawdown=metrics["max_drawdown"],
        cagr=metrics["cagr"],
        volatility=metrics["volatility"],
        win_rate=metrics["win_rate"],
        total_return=metrics["total_return"],
        start_date=calendar[0],
        end_date=calendar[-1],
        observation_count=int(metrics["observation_count"]),
        run_id=_digest(run_identity),
        run_duration_seconds=perf_counter() - started,
        source_script="us_equity_strategies.research.optimized_strategy_replay",
        computed_at=computed_at,
        source_revision=source_revision,
        calendar_id=calendar_id,
        periods_per_year=periods_per_year,
        cost_model=cost_model.model_id,
        cost_inputs={
            "commission_bps": float(cost_model.commission_bps),
            "slippage_bps": float(cost_model.slippage_bps),
            "market_impact_bps": float(cost_model.market_impact_bps),
        },
    )
    return OptimizedStrategyReplay(
        points=ledger,
        backtest=backtest,
        live_executable=False,
        risk_gate_applied=False,
        gaps=REPLAY_GAPS,
    )


__all__ = [
    "REPLAY_GAPS",
    "DailyReplayPoint",
    "DatedBar",
    "ExecutionAssumptions",
    "OptimizedStrategyReplay",
    "OptimizedStrategyReplayError",
    "ReplayIdentity",
    "ReplayRequest",
    "replay_optimized_strategy",
]
