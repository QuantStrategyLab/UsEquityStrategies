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
import re
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
    ResearchLedgerEvent,
    ResearchDailyLedger,
    ResearchLedgerDay,
    ResearchPositionMark,
    ResearchTrialRecord,
    ResearchTrialStatus,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

from us_equity_strategies.entrypoints import (
    _research_only_market_regime_context,
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
from us_equity_strategies.research.optimized_member_identity import (
    validate_optimized_member_identity,
)

REPLAY_GAPS = (
    "PRE_RISK_GATE_NOT_LIVE_EXECUTABLE",
    "CALLER_SUPPLIED_INDICATORS_AND_BENCHMARK",
    "SYNTHETIC_BPS_NOT_LIVE_FEES",
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
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
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
    {"signal_effective_after_trading_days", "translator", "signal_text_fn",
     "option_overlay_enabled", "option_growth_overlay_enabled", "option_growth_overlay_recipe",
     "option_growth_overlay_start_usd", "option_growth_overlay_nav_budget_ratio",
     "option_income_overlay_enabled", "option_income_overlay_recipe"}
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
    source_id: str | None = None
    available_at: str | None = None


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
    research_identity: Mapping[str, Any] | None = None
    state_inputs: tuple[Mapping[str, Any], ...] | None = None
    income_cashflow: Mapping[date, float] | None = None
    indicator_sources: Mapping[date, Mapping[str, Mapping[str, Mapping[str, Any]]]] | None = None
    external_cashflow: Mapping[date, float] | None = None
    ledger_events: Mapping[date, Mapping[str, Any]] | None = None
    option_market_inputs: tuple[Mapping[str, Any], ...] | None = None
    whole_share_execution: bool = False


@dataclass(frozen=True, slots=True)
class DailyReplayPoint:
    session: date
    cash: float
    fees: float
    nav: float
    daily_return: float | None
    holdings: tuple[tuple[str, float], ...]
    market_values: tuple[tuple[str, float], ...]
    trade_net_cashflow: float
    income_cashflow: float = 0.0
    external_cashflow: float = 0.0
    dividend_receivable: float = 0.0
    declared_event_ids: tuple[str, ...] | None = None
    events: tuple[ResearchLedgerEvent, ...] = ()
    option_positions: tuple[ResearchPositionMark, ...] = ()
    option_settlement_cashflow: float = 0.0
    restricted_cash: float = 0.0
    equity_trade_cashflow: float | None = None
    equity_trade_quantities: Mapping[str, float] | None = None
    equity_trade_phase: str | None = None


@dataclass(frozen=True, slots=True)
class OptimizedStrategyReplay:
    points: tuple[DailyReplayPoint, ...]
    backtest: BacktestResult
    live_executable: bool
    risk_gate_applied: bool
    gaps: tuple[str, ...]
    decision_targets: tuple[tuple[date, date, tuple[tuple[str, float], ...]], ...] = ()


def _symbols(config: Mapping[str, Any]) -> tuple[str, ...]:
    raw = config.get("managed_symbols")
    if type(raw) is not tuple or not raw or any(type(symbol) is not str or not symbol for symbol in raw):
        _fail("MISSING_FIELD:managed_symbols")
    if len(set(raw)) != len(raw):
        _fail("INVALID_FIELD:managed_symbols")
    return raw


def _config(
    profile: str,
    runtime_config: object,
    *,
    allow_research_market_regime: bool = False,
    allow_research_options: bool = False,
) -> tuple[Any, Callable, dict[str, Any], tuple[str, ...]]:
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
    _reject_unsimulated(
        profile, config, allow_research_market_regime=allow_research_market_regime,
        allow_research_options=allow_research_options,
    )
    return manifest, builder, config, _symbols(config)


def _reject_unsimulated(
    profile: str,
    config: Mapping[str, Any],
    *,
    allow_research_market_regime: bool = False,
    allow_research_options: bool = False,
) -> None:
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
    ) and not allow_research_options:
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
        retention_mode not in {"none", "fixed", "environment"}
        or (
            retention_mode == "environment"
            and not (
                allow_research_market_regime
                and profile in {"soxl_soxx_trend_income", "tqqq_growth_income"}
            )
        )
        or (macro_enabled and not allow_research_market_regime)
        or any(
            _control_enabled(config.get(key))
            for key in plugin_flags
            if not (profile in {"soxl_soxx_trend_income", "tqqq_growth_income"}
                    and allow_research_market_regime)
        )
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


def _prices(
    bars: object, calendar: tuple[date, ...], symbols: tuple[str, ...],
    required_symbols: set[str],
) -> dict[tuple[date, str], DatedBar]:
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
    required = {(session, symbol) for session in calendar for symbol in required_symbols}
    if not required.issubset(indexed):
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


def _option_unit_lot_id(contract_id: str, campaign_id: str, lot_index: int) -> str:
    digest = hashlib.sha256(f"{contract_id}|{campaign_id}|{lot_index}".encode("utf-8")).hexdigest()
    return f"OPT-{digest[:28]}"


def _tqqq_leaps_candidate(
    quotes: Mapping[str, Mapping[str, Any]], session: date, config: Mapping[str, Any],
    excluded_contracts: set[str] | None = None,
) -> Mapping[str, Any] | None:
    target_dte = int(float(config.get("option_growth_overlay_target_dte_months", 24.0)) * 30.4375)
    target_delta = float(config.get("option_growth_overlay_target_delta", 0.75))
    min_dte = int(config.get("option_growth_overlay_min_dte_days", 540))
    max_dte = int(config.get("option_growth_overlay_max_dte_days", 930))
    max_spread = float(config.get("option_growth_overlay_max_bid_ask_spread_ratio", 0.12))
    excluded = excluded_contracts or set()
    candidates: list[tuple[int, float, float, Mapping[str, Any]]] = []
    for quote in quotes.values():
        dte = (date.fromisoformat(quote["expiration"]) - session).days
        mid = (quote["bid"] + quote["ask"]) / 2.0
        spread_ratio = (quote["ask"] - quote["bid"]) / mid if quote["bid"] > 0.0 and mid > 0.0 else 0.0
        if (
            quote["contract_id"] not in excluded
            and quote["right"] == "call"
            and quote["bid"] > 0.0
            and quote["ask"] > 0.0
            and mid > 0.0
            and quote["strike"] > 0.0
            and min_dte <= dte <= max_dte
            and quote["delta"] > 0.0
            and spread_ratio <= max_spread
        ):
            candidates.append((abs(dte - target_dte), abs(quote["delta"] - target_delta), quote["strike"], quote))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3] if candidates else None


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


def _cost(value: object) -> tuple[PromotionCostModel, float, float]:
    if type(value) is not PromotionCostModel:
        _fail("MISSING_FIELD:cost_model")
    _text(value.model_id, "INVALID_FIELD:cost_model")
    commission_bps = _number(value.commission_bps, "NONFINITE_INPUT")
    slippage_bps = _number(value.slippage_bps, "NONFINITE_INPUT")
    impact_bps = _number(value.market_impact_bps, "NONFINITE_INPUT")
    adverse_bps = slippage_bps + impact_bps
    if not math.isfinite(adverse_bps):
        _fail("NONFINITE_INPUT")
    if adverse_bps >= 10_000.0:
        _fail("INVALID_FILL")
    return value, commission_bps / 10_000.0, adverse_bps / 10_000.0


def _field(bar: DatedBar, field_name: str) -> float:
    value = bar.open if field_name == "open" else bar.close
    return _number(value, "NONFINITE_INPUT", positive=True)


def _snapshot(
    session: date,
    cash: float,
    quantities: Mapping[str, float],
    closes: Mapping[str, float],
    metadata: Mapping[str, Any],
    dividend_receivable: float = 0.0,
    option_market_value: float = 0.0,
) -> PortfolioSnapshot:
    positions = tuple(
        Position(symbol=symbol, quantity=quantity, market_value=quantity * closes[symbol])
        for symbol, quantity in sorted(quantities.items())
        if quantity != 0.0
    )
    payload = dict(metadata)
    payload["market_currency_cash"] = cash
    if dividend_receivable != 0.0:
        payload["dividend_receivable"] = dividend_receivable
    if option_market_value != 0.0:
        payload["research_option_market_value"] = option_market_value
    return PortfolioSnapshot(
        as_of=datetime(session.year, session.month, session.day, tzinfo=UTC),
        total_equity=cash + dividend_receivable + option_market_value + sum(
            quantity * closes[symbol] for symbol, quantity in quantities.items() if quantity != 0.0
        ),
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


def _unit_rate(value: object) -> float:
    return _number(value, "NONFINITE_INPUT")


def _execution_price(reference: float, delta: float, adverse_rate: float) -> float:
    if delta == 0.0 or adverse_rate == 0.0:
        return reference
    price = reference * (1.0 + adverse_rate) if delta > 0.0 else reference * (1.0 - adverse_rate)
    if not math.isfinite(price) or price <= 0.0:
        _fail("INVALID_FILL")
    return price


def _rebalance(
    cash: float,
    quantities: dict[str, float],
    targets: Mapping[str, float],
    fills: Mapping[str, float],
    commission_rate: float,
    adverse_rate: float = 0.0,
    whole_shares: bool = False,
) -> tuple[float, dict[str, float], float, float]:
    """Size shares at the reference price. Commission is cash; slippage and impact worsen the fill."""

    commission_rate = _unit_rate(commission_rate)
    adverse_rate = _unit_rate(adverse_rate)
    opening_cash = cash
    updated = dict(quantities)
    traded = 0.0
    trade_net = 0.0
    active = {symbol for symbol in targets if targets[symbol] != 0.0 or quantities[symbol] != 0.0}
    if not active.issubset(fills):
        _fail("INPUT_GAP")
    if whole_shares:
        desired = {symbol: math.floor(targets[symbol] / fills[symbol] + 1e-9)
                   if symbol in active else 0 for symbol in targets}
        for selling in (True, False):
            for symbol in sorted(targets):
                delta = desired[symbol] - updated[symbol]
                if (delta < 0.0) != selling or delta == 0.0:
                    continue
                reference = fills[symbol]
                fill = _execution_price(reference, delta, adverse_rate)
                fee = abs(delta * reference) * commission_rate
                cash -= delta * fill + fee
                traded += abs(delta * reference)
                trade_net -= delta * fill
                updated[symbol] += delta
        fee = traded * commission_rate
        if not math.isfinite(cash) or cash < -1e-8:
            _fail("PLAN_UNFUNDED")
        return cash, updated, fee, trade_net
    for symbol in sorted(targets):
        if symbol not in active:
            continue
        reference = fills[symbol]
        delta = targets[symbol] / reference - quantities[symbol]
        fill = _execution_price(reference, delta, adverse_rate)
        traded += abs(delta * reference)
        trade_net -= delta * fill
        updated[symbol] = quantities[symbol] + delta
    fee = traded * commission_rate
    cash = opening_cash + trade_net - fee
    scale = max(abs(opening_cash), abs(trade_net), 1.0)
    if not math.isfinite(cash) or not math.isfinite(fee) or cash < -4 * math.ulp(scale) or not math.isfinite(trade_net):
        _fail("CASH_INVALID")
    return cash, updated, fee, trade_net


def _metrics(
    points: tuple[DailyReplayPoint, ...],
    initial_nav: float,
    periods_per_year: float,
) -> dict[str, float | None]:
    returns = tuple(point.daily_return for point in points if point.daily_return is not None)
    count = len(returns)
    if count != len(points) - 1 or count < 1:
        _fail("CASH_INVALID")
    adjusted_nav = initial_nav
    peak = initial_nav
    max_drawdown = 0.0
    for point in points[1:]:
        if point.daily_return is None:
            _fail("CASH_INVALID")
        adjusted_nav *= 1.0 + point.daily_return
        peak = max(peak, adjusted_nav)
        max_drawdown = min(max_drawdown, adjusted_nav / peak - 1.0)
    total_return = adjusted_nav / initial_nav - 1.0
    if not math.isfinite(total_return) or 1.0 + total_return <= 0.0:
        _fail("CASH_INVALID")
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


def _member_identity_digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("RESEARCH_IDENTITY_INVALID")
    return hashlib.sha256(encoded).hexdigest()


def _checked_research_identity(request: ReplayRequest) -> dict[str, Any] | None:
    value = request.research_identity
    if value is None:
        return None
    try:
        identity = validate_optimized_member_identity(value)
        config = {key: item for key, item in request.runtime_config.items()
                  if key not in {"translator", "signal_text_fn"}}
        if type(config.get("managed_symbols")) is tuple:
            config["managed_symbols"] = list(config["managed_symbols"])
        def source_bar(bar: DatedBar) -> dict[str, Any]:
            result = {
                "session": bar.session.isoformat(), "symbol": bar.symbol,
                "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
            }
            if bar.source_id is not None or bar.available_at is not None:
                result.update(source_id=bar.source_id, available_at=bar.available_at)
            return result
        source = {
            "runtime_config": config,
            "calendar": [day.isoformat() for day in request.calendar],
            "initial_cash": request.initial_cash,
            "initial_quantities": dict(request.initial_quantities),
            "prices": [source_bar(bar) for bar in request.prices],
            "computed_at": request.computed_at,
            "derived_indicators": None if request.derived_indicators is None else {
                day.isoformat(): payload for day, payload in request.derived_indicators.items()
            },
            "indicator_sources": None if request.indicator_sources is None else {
                day.isoformat(): payload for day, payload in request.indicator_sources.items()
            },
            "benchmark_bars": [source_bar(bar) for bar in request.benchmark_bars],
        }
        if request.state_inputs is not None:
            source["state_inputs"] = list(request.state_inputs)
        if request.income_cashflow is not None:
            source["income_cashflow"] = {
                day.isoformat(): amount for day, amount in request.income_cashflow.items()
            }
        if request.external_cashflow is not None:
            source["external_cashflow"] = {
                day.isoformat(): amount for day, amount in request.external_cashflow.items()
            }
        if request.ledger_events is not None:
            source["ledger_events"] = {
                day.isoformat(): {
                    "declared_event_ids": list(row["declared_event_ids"]),
                    "events": [event.to_dict() for event in row["events"]],
                }
                for day, row in _validated_ledger_events(
                    request.ledger_events, _calendar(request.calendar)
                ).items()
            }
        if request.option_market_inputs is not None:
            source["option_market_inputs"] = list(request.option_market_inputs)
        if request.whole_share_execution:
            source["whole_share_execution"] = True
        execution = {
            "signal_effective_after_trading_days": request.execution.signal_effective_after_trading_days,
            "execution_timing_contract": request.execution.execution_timing_contract,
            "fill_price_field": request.execution.fill_price_field,
            "nav_mark_field": request.execution.nav_mark_field,
        }
        cost_inputs = {
            "commission_bps": float(request.cost_model.commission_bps),
            "slippage_bps": float(request.cost_model.slippage_bps),
            "market_impact_bps": float(request.cost_model.market_impact_bps),
        }
        if (
            identity["strategy_profile"] != request.identity.strategy_profile
            or identity["domain"] != request.identity.domain
            or identity["param_set_id"] != request.identity.param_set_id
            or identity["ues_revision"] != request.identity.source_revision
            or identity["actual_params"] != config
            or identity["config_sha256"] != _member_identity_digest(config)
            or identity["input_sha256"] != _member_identity_digest(source)
            or identity["window_start"] != request.calendar[0].isoformat()
            or identity["window_end"] != request.calendar[-1].isoformat()
            or identity["calendar_id"] != request.calendar_id
            or identity["periods_per_year"] != request.periods_per_year
            or identity["cost_source"] != request.cost_model.model_id
            or identity["cost_inputs"] != cost_inputs
            or identity["execution"] != execution
        ):
            _fail("RESEARCH_IDENTITY_MISMATCH")
        return identity
    except OptimizedStrategyReplayError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        _fail("RESEARCH_IDENTITY_INVALID")


def replay_optimized_strategy(request: ReplayRequest) -> OptimizedStrategyReplay:
    """Replay explicit inputs through the pre-risk-gate strategy builders."""

    if type(request) is not ReplayRequest or type(request.identity) is not ReplayIdentity:
        _fail("MISSING_FIELD:identity")
    identity = request.identity
    research_identity = _checked_research_identity(request)
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
    calendar = _calendar(request.calendar)
    signals = calendar[:-1]
    state_inputs = _validated_state_inputs(request.state_inputs, signals)
    _validate_member_input_provenance(request, calendar)
    allow_research_options = _research_option_config_enabled(request, research_identity)
    option_rows = _validated_option_market_inputs(request, calendar, state_inputs) if allow_research_options else None
    if not allow_research_options and request.option_market_inputs is not None:
        _fail("UNSIMULATED_OPTION_OVERLAY")
    states_by_date = (
        {date.fromisoformat(item["signal_date"]): item["state"] for item in state_inputs}
        if state_inputs is not None else {}
    )
    market_regime_enabled = (
        profile == "soxl_soxx_trend_income"
        and isinstance(request.runtime_config, Mapping)
        and _control_enabled(request.runtime_config.get("market_regime_control_enabled"))
    )
    retention_mode = (
        str(request.runtime_config.get("blend_gate_volatility_delever_retention_mode") or "").strip().lower()
        if isinstance(request.runtime_config, Mapping) else ""
    )
    allow_research_market_regime = False
    if market_regime_enabled and retention_mode == "environment":
        _validate_research_market_regime_states(state_inputs)
        allow_research_market_regime = True
    elif profile == "tqqq_growth_income" and isinstance(request.runtime_config, Mapping):
        config = request.runtime_config
        required_plugin_flags = (
            "market_regime_control_enabled",
            "dual_drive_macro_risk_governor_enabled",
            "dual_drive_crisis_defense_enabled",
            "dual_drive_volatility_delever_taco_veto_enabled",
        )
        if (
            all(config.get(key) is True for key in required_plugin_flags)
            and str(config.get("dual_drive_volatility_delever_retention_mode") or "").strip().lower()
            == "environment"
        ):
            _validate_research_market_regime_states(state_inputs)
            _validate_tqqq_research_market_regime_states(state_inputs)
            allow_research_market_regime = True
    _manifest, builder, config, symbols = _config(
        profile,
        request.runtime_config,
        allow_research_market_regime=allow_research_market_regime,
        allow_research_options=allow_research_options,
    )
    execution = _execution(request.execution)
    if config["signal_effective_after_trading_days"] != execution.signal_effective_after_trading_days:
        _fail("TIMING_MISMATCH")
    required_symbols = (
        {"SOXL", "SOXX", "BOXX"} if profile == "soxl_soxx_trend_income"
        else {"TQQQ", str(config.get("dual_drive_unlevered_symbol") or "QQQM").strip().upper(), "BOXX"}
    )
    if not required_symbols.issubset(symbols):
        _fail("MISSING_FIELD:managed_symbols")
    prices = _prices(request.prices, calendar, symbols, required_symbols)
    quantities = _quantities(request.initial_quantities, symbols)
    if type(request.whole_share_execution) is not bool:
        _fail("INVALID_FIELD:whole_share_execution")
    if request.whole_share_execution:
        if (research_identity is None or research_identity["declared_contracts"]["share_quantity"]
                != "synthetic whole equity shares and integer option lots"):
            _fail("SHARE_QUANTITY_CONTRACT_MISMATCH")
        if any(abs(quantity - round(quantity)) > 1e-9 for quantity in quantities.values()):
            _fail("FRACTIONAL_INITIAL_HOLDINGS_UNSUPPORTED")
    initial_quantities = dict(quantities)
    cash = _number(request.initial_cash, "NONFINITE_INPUT")
    initial_cash = cash
    metadata = _metadata(request.portfolio_metadata)
    cost_model, commission_rate, adverse_rate = _cost(request.cost_model)
    income_cashflows = _validated_income_cashflows(request.income_cashflow, calendar)
    external_cashflows = _validated_external_cashflows(request.external_cashflow, calendar)
    ledger_events = _validated_ledger_events(request.ledger_events, calendar)
    option_enabled = option_rows is not None
    soxx_credit_enabled = option_enabled and profile == "soxl_soxx_trend_income"
    if ledger_events is not None:
        for session, event_row in ledger_events.items():
            payment_total = sum(
                event.amount for event in event_row["events"]
                if event.event_type == "dividend_payment"
            )
            if not math.isclose(payment_total, income_cashflows.get(session, 0.0), rel_tol=0.0, abs_tol=1e-9):
                _fail("LEDGER_DIVIDEND_CASHFLOW")
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
    initial_closes = {
        symbol: _field(prices[(calendar[0], symbol)], "close")
        for symbol in symbols if (calendar[0], symbol) in prices
    }
    if any(quantity != 0.0 and symbol not in initial_closes for symbol, quantity in quantities.items()):
        _fail("INPUT_GAP")
    initial_option_value = 0.0
    initial_restricted_cash = 0.0
    if option_rows is not None:
        first_option_row = option_rows[calendar[0]]
        first_quotes = {item["contract_id"]: item for item in first_option_row["quotes"]}
        if soxx_credit_enabled:
            initial_restricted_cash = _soxx_initial_collateral(first_option_row["positions"], first_quotes)
            for position in first_option_row["positions"]:
                quote = first_quotes.get(position["contract_id"])
                if quote is None:
                    _fail("OPTION_QUOTE_MISSING")
                mark_price = quote["ask"] if position["quantity"] < 0 else quote["bid"]
                initial_option_value += mark_price * quote["multiplier"] * position["quantity"]
            if cash + 1e-9 < initial_restricted_cash:
                _fail("OPTION_COLLATERAL_UNFUNDED")
        else:
            for position in first_option_row["positions"]:
                quote = first_quotes.get(position["contract_id"])
                if quote is None:
                    _fail("OPTION_QUOTE_MISSING")
                initial_option_value += quote["bid"] * quote["multiplier"]
    initial_nav = cash + sum(quantities[symbol] * initial_closes[symbol] for symbol in symbols
                             if quantities[symbol] != 0.0) + initial_option_value
    if initial_nav <= 0.0:
        _fail("CASH_INVALID")
    previous_nav = initial_nav
    pending: tuple[date, dict[str, float]] | None = None
    decision_targets: list[tuple[date, date, tuple[tuple[str, float], ...]]] = []
    dividend_receivable = 0.0
    open_dividends: dict[str, tuple[str, float]] = {}
    option_lots: dict[str, dict[str, Any]] = {}
    option_terms: dict[str, dict[str, Any]] = {}
    pending_option_open: tuple[str, float, dict[str, Any]] | None = None
    pending_option_roll: tuple[str, str, str] | None = None
    pending_soxx_spread: tuple[str, str] | None = None
    pending_soxx_close: tuple[date, str] | None = None
    restricted_cash = initial_restricted_cash
    for index, session in enumerate(calendar):
        closes = {symbol: _field(prices[(session, symbol)], "close")
                  for symbol in symbols if (session, symbol) in prices}
        if any(quantity != 0.0 and symbol not in closes for symbol, quantity in quantities.items()):
            _fail("INPUT_GAP")
        fees = 0.0
        trade_net_cashflow = 0.0
        external_cashflow = external_cashflows.get(session, 0.0)
        cash += external_cashflow
        day_event_row = ledger_events.get(session) if ledger_events is not None else None
        day_events = () if day_event_row is None else day_event_row["events"]
        if option_enabled and any(
            event.event_type in {"option_trade", "option_settlement"} for event in day_events
        ):
            _fail("OPTION_SETTLEMENT_DUPLICATE")
        if not option_enabled and any(
            event.event_type in {"option_trade", "option_settlement"} for event in day_events
        ):
            _fail("UNSIMULATED_OPTION_OVERLAY")
        option_events: list[ResearchLedgerEvent] = []
        settlement_events: list[ResearchLedgerEvent] = []
        option_cashflow = 0.0
        option_settlement_cashflow = 0.0
        equity_trade_cashflow: float | None = None
        equity_trade_quantities: dict[str, float] | None = None
        equity_trade_phase: str | None = None
        option_marks: tuple[ResearchPositionMark, ...] = ()
        if option_enabled and not soxx_credit_enabled:
            option_row = option_rows[session]
            reported = (None if option_row["positions"] is None else
                        {item["ledger_position_id"]: item for item in option_row["positions"]})
            if index == 0:
                option_lots = dict(reported)
            elif reported is not None and reported != option_lots:
                _fail("OPTION_POSITION_STATE_MISMATCH")
            quotes = {item["contract_id"]: item for item in option_row["quotes"]}
            for quote in quotes.values():
                previous_terms = option_terms.get(quote["contract_id"])
                if previous_terms is not None and any(
                    previous_terms[field] != quote[field]
                    for field in ("underlying", "right", "strike", "expiration", "multiplier")
                ):
                    _fail("OPTION_CONTRACT_TERMS_MISMATCH")
                option_terms[quote["contract_id"]] = quote
            for lot in option_lots.values():
                terms = option_terms.get(lot["contract_id"])
                if terms is None:
                    _fail("OPTION_CONTRACT_TERMS_MISSING")
                if date.fromisoformat(terms["expiration"]) < session:
                    _fail("OPTION_EXPIRATION_SESSION_MISSING")
            roll_protected_campaigns: set[str] = set()
            if pending_option_roll is not None and date.fromisoformat(
                option_terms[pending_option_roll[1]]["expiration"]
            ) > session:
                campaign_id, old_contract_id, new_contract_id = pending_option_roll
                roll_protected_campaigns.add(campaign_id)
                campaign_lots = [lot for lot in option_lots.values() if lot["campaign_id"] == campaign_id]
                old_quote = quotes.get(old_contract_id)
                new_quote = quotes.get(new_contract_id)
                if campaign_lots and all(lot["contract_id"] == old_contract_id for lot in campaign_lots):
                    executable_new = _tqqq_leaps_candidate(
                        {new_contract_id: new_quote} if new_quote is not None else {},
                        session, config,
                    )
                    if old_quote is not None and old_quote["bid"] > 0.0 and executable_new is not None and new_quote["ask"] > 0.0:
                        old_terms = option_terms[old_contract_id]
                        old_proceeds = old_quote["bid"] * old_terms["multiplier"] * len(campaign_lots)
                        mid = (new_quote["bid"] + new_quote["ask"]) / 2.0
                        limit_price = round(min(new_quote["ask"], mid * 1.03), 2)
                        unit_cost = limit_price * new_quote["multiplier"]
                        budget = previous_nav * float(config.get("option_growth_overlay_nav_budget_ratio", 0.03))
                        quantity = math.floor(min(budget, cash + old_proceeds) / unit_cost)
                        if quantity > 0:
                            for lot in campaign_lots:
                                del option_lots[lot["ledger_position_id"]]
                                cashflow = old_quote["bid"] * old_terms["multiplier"]
                                option_cashflow += cashflow
                                option_events.append(ResearchLedgerEvent(
                                    event_id=f"option-roll-close:{lot['ledger_position_id']}:{session.isoformat()}",
                                    event_type="option_trade", symbol=lot["ledger_position_id"], amount=cashflow,
                                ))
                            new_campaign_id = f"tqqq-leaps-roll-{session.isoformat()}"
                            for lot_number in range(1, quantity + 1):
                                lot_id = _option_unit_lot_id(new_contract_id, new_campaign_id, lot_number)
                                lot = {
                                    "ledger_position_id": lot_id, "contract_id": new_contract_id,
                                    "campaign_id": new_campaign_id, "lot_index": lot_number,
                                    "cost_basis_per_contract": unit_cost,
                                    "campaign_basis": unit_cost * quantity, "recovered_proceeds": 0.0,
                                }
                                option_lots[lot_id] = lot
                                cashflow = -unit_cost
                                option_cashflow += cashflow
                                option_events.append(ResearchLedgerEvent(
                                    event_id=f"option-roll-open:{lot_id}:{session.isoformat()}",
                                    event_type="option_trade", symbol=lot_id, amount=cashflow,
                                ))
                pending_option_roll = None
            elif pending_option_roll is not None:
                pending_option_roll = None
            if pending_option_open is not None:
                contract_id, quantity, signal_quote = pending_option_open
                execution_quote = quotes.get(contract_id)
                if execution_quote is None:
                    _fail("OPTION_QUOTE_MISSING")
                mid = (execution_quote["bid"] + execution_quote["ask"]) / 2.0
                limit_price = round(min(execution_quote["ask"], mid * 1.03), 2)
                unit_cost = limit_price * execution_quote["multiplier"]
                quantity = min(quantity, math.floor(cash / unit_cost))
                for lot_number in range(1, quantity + 1):
                    campaign_id = f"tqqq-leaps-{session.isoformat()}"
                    lot_id = _option_unit_lot_id(contract_id, campaign_id, lot_number)
                    lot = {
                        "ledger_position_id": lot_id, "contract_id": contract_id,
                        "campaign_id": campaign_id,
                        "lot_index": lot_number, "cost_basis_per_contract": unit_cost,
                        "campaign_basis": unit_cost * quantity, "recovered_proceeds": 0.0,
                    }
                    option_lots[lot_id] = lot
                    option_terms[contract_id] = execution_quote
                    cashflow = -unit_cost
                    option_cashflow += cashflow
                    option_events.append(ResearchLedgerEvent(
                        event_id=f"option-open:{lot_id}", event_type="option_trade",
                        symbol=lot_id, amount=cashflow,
                    ))
                pending_option_open = None
            qqq = option_row["qqq_indicator"]
            gate = (
                states_by_date.get(session, {}).get("market_regime_control", {})
                .get("position_control", {}).get("final_route") == "risk_on"
                and qqq["above_200dma"] and qqq["momentum_63d"] > 0.0
            )
            if gate and pending_option_roll is None and index < len(calendar) - 1:
                roll_dte = int(12 * 30.4375)
                held_contracts = {lot["contract_id"] for lot in option_lots.values()}
                for campaign_id in sorted({lot["campaign_id"] for lot in option_lots.values()}):
                    campaign_lots = [lot for lot in option_lots.values() if lot["campaign_id"] == campaign_id]
                    contract_ids = {lot["contract_id"] for lot in campaign_lots}
                    if len(contract_ids) != 1:
                        _fail("OPTION_POSITION_STATE_MISMATCH")
                    old_contract_id = next(iter(contract_ids))
                    old_quote = quotes.get(old_contract_id)
                    terms = option_terms.get(old_contract_id)
                    if old_quote is None or terms is None:
                        _fail("OPTION_QUOTE_MISSING")
                    expiration = date.fromisoformat(terms["expiration"])
                    remaining_dte = (expiration - session).days
                    if (
                        not (0 < remaining_dte <= roll_dte)
                        or expiration <= calendar[index + 1]
                        or old_quote["bid"] <= 0.0
                    ):
                        continue
                    replacement = _tqqq_leaps_candidate(quotes, session, config, held_contracts)
                    if replacement is None:
                        continue
                    pending_option_roll = (campaign_id, old_contract_id, replacement["contract_id"])
                    roll_protected_campaigns.add(campaign_id)
                    break
            # Sell only enough held units to recover the campaign's unrecovered premium.
            for campaign_id in sorted({lot["campaign_id"] for lot in option_lots.values()}):
                if campaign_id in roll_protected_campaigns:
                    continue
                campaign_lots = [lot for lot in option_lots.values() if lot["campaign_id"] == campaign_id]
                basis = campaign_lots[0]["campaign_basis"]
                recovered = campaign_lots[0]["recovered_proceeds"]
                contract_id = campaign_lots[0]["contract_id"]
                quote = quotes.get(contract_id)
                if option_terms.get(contract_id, {}).get("expiration") == session.isoformat():
                    continue
                if quote is None:
                    _fail("OPTION_QUOTE_MISSING")
                if index > 0 and quote["bid"] > 0.0 and len(campaign_lots) > 1 and quote["bid"] * quote["multiplier"] * len(campaign_lots) >= 2 * basis:
                    required = max(0.0, basis - recovered)
                    close_count = min(len(campaign_lots) - 1, math.ceil(required / (quote["bid"] * quote["multiplier"])))
                    close_lots = sorted(campaign_lots, key=lambda item: item["ledger_position_id"])[:close_count]
                    proceeds_per_lot = quote["bid"] * quote["multiplier"]
                    for lot in close_lots:
                        del option_lots[lot["ledger_position_id"]]
                        option_cashflow += proceeds_per_lot
                        option_events.append(ResearchLedgerEvent(
                            event_id=f"option-close:{lot['ledger_position_id']}:{session.isoformat()}",
                            event_type="option_trade", symbol=lot["ledger_position_id"],
                            amount=proceeds_per_lot,
                        ))
                    for lot in option_lots.values():
                        if lot["campaign_id"] == campaign_id:
                            lot["recovered_proceeds"] = recovered + close_count * proceeds_per_lot
            marks: list[ResearchPositionMark] = []
            for lot_id, lot in sorted(option_lots.items()):
                quote = quotes.get(lot["contract_id"])
                terms = option_terms.get(lot["contract_id"])
                if terms is None:
                    _fail("OPTION_CONTRACT_TERMS_MISSING")
                if terms["expiration"] == session.isoformat():
                    continue
                if quote is None:
                    _fail("OPTION_QUOTE_MISSING")
                marks.append(ResearchPositionMark(
                    symbol=lot_id, quantity=1.0,
                    valuation=quote["bid"] * quote["multiplier"],
                    option_underlying=quote["underlying"], option_right=quote["right"],
                    option_strike=quote["strike"], option_expiration=date.fromisoformat(quote["expiration"]),
                    option_multiplier=quote["multiplier"],
                    option_premium_cashflow=-lot["cost_basis_per_contract"],
                ))
            option_marks = tuple(marks)
            cash += option_cashflow
        elif soxx_credit_enabled:
            option_row = option_rows[session]
            reported = (None if option_row["positions"] is None else
                        {item["ledger_position_id"]: item for item in option_row["positions"]})
            if index == 0:
                option_lots = dict(reported)
            elif reported is not None and reported != option_lots:
                _fail("OPTION_POSITION_STATE_MISMATCH")
            quotes = {item["contract_id"]: item for item in option_row["quotes"]}
            for quote in quotes.values():
                previous_terms = option_terms.get(quote["contract_id"])
                if previous_terms is not None and any(
                    previous_terms[field] != quote[field]
                    for field in ("underlying", "right", "strike", "expiration", "multiplier")
                ):
                    _fail("OPTION_CONTRACT_TERMS_MISMATCH")
                option_terms[quote["contract_id"]] = quote
            for lot in option_lots.values():
                if lot["contract_id"] not in quotes:
                    _fail("OPTION_QUOTE_MISSING")
            closed_campaign_today = False
            if pending_soxx_close is not None:
                execution_session, campaign_id = pending_soxx_close
                if execution_session != session:
                    _fail("OPTION_CLOSE_SESSION_MISMATCH")
                campaign_lots = [lot for lot in option_lots.values() if lot["campaign_id"] == campaign_id]
                if len(campaign_lots) != 2 or {lot["leg"] for lot in campaign_lots} != {"short", "long"}:
                    _fail("OPTION_CLOSE_CAMPAIGN_MISSING")
                if any(date.fromisoformat(option_terms[lot["contract_id"]]["expiration"]) <= session for lot in campaign_lots):
                    _fail("OPTION_CLOSE_EXPIRATION_CONFLICT")
                collateral_release = next(lot["campaign_basis"] for lot in campaign_lots if lot["leg"] == "short")
                for lot in campaign_lots:
                    quote = quotes[lot["contract_id"]]
                    amount = (
                        -quote["ask"] * quote["multiplier"]
                        if lot["leg"] == "short"
                        else quote["bid"] * quote["multiplier"]
                    )
                    option_cashflow += amount
                    option_events.append(ResearchLedgerEvent(
                        event_id=f"soxx-close:{lot['ledger_position_id']}:{session.isoformat()}",
                        event_type="option_trade", symbol=lot["ledger_position_id"], amount=amount,
                    ))
                    del option_lots[lot["ledger_position_id"]]
                if collateral_release <= 0.0 or restricted_cash + 1e-9 < collateral_release:
                    _fail("OPTION_COLLATERAL_INVALID")
                restricted_cash -= collateral_release
                option_events.append(ResearchLedgerEvent(
                    event_id=f"soxx-collateral-release:{campaign_id}:{session.isoformat()}",
                    event_type="collateral_change", symbol="CASH", amount=-collateral_release,
                ))
                pending_soxx_close = None
                closed_campaign_today = True
            if pending_soxx_spread is not None:
                if pending_soxx_close is not None:
                    _fail("OPTION_MANAGEMENT_CONFLICT")
                short_id, long_id = pending_soxx_spread
                short_quote, long_quote = quotes.get(short_id), quotes.get(long_id)
                if short_quote is None or long_quote is None:
                    _fail("OPTION_QUOTE_MISSING")
                risk = (short_quote["strike"] - long_quote["strike"]) * 100.0 - (
                    short_quote["bid"] - long_quote["ask"]
                ) * 100.0
                net_credit = short_quote["bid"] - long_quote["ask"]
                if (net_credit > 0.05 and short_quote["strike"] > long_quote["strike"]
                        and risk > 0 and risk + restricted_cash <= previous_nav * 0.01
                        and cash - restricted_cash >= risk):
                    campaign_id = f"soxx-put-credit-{session.isoformat()}"
                    for leg, quote, quantity, premium in (
                        ("short", short_quote, -1.0, short_quote["bid"]),
                        ("long", long_quote, 1.0, long_quote["ask"]),
                    ):
                        lot_id = f"{campaign_id}-{leg}"
                        lot = {
                            "ledger_position_id": lot_id, "contract_id": quote["contract_id"],
                            "campaign_id": campaign_id, "leg": leg, "quantity": quantity,
                            "cost_basis_per_contract": premium * 100.0,
                            "campaign_basis": risk, "recovered_proceeds": 0.0,
                        }
                        option_lots[lot_id] = lot
                        amount = premium * 100.0 * -quantity
                        option_cashflow += amount
                        option_events.append(ResearchLedgerEvent(
                            event_id=f"option-open:{lot_id}", event_type="option_trade",
                            symbol=lot_id, amount=amount,
                        ))
                    required_collateral = risk
                    restricted_cash += required_collateral
                    option_events.append(ResearchLedgerEvent(
                        event_id=f"soxx-collateral:{campaign_id}", event_type="collateral_change",
                        symbol="CASH", amount=required_collateral,
                    ))
                pending_soxx_spread = None
            management = option_row["management"]
            if management is not None:
                if pending_soxx_close is not None or pending_soxx_spread is not None:
                    _fail("OPTION_MANAGEMENT_CONFLICT")
                campaign_id = management["campaign_id"]
                campaign_lots = [lot for lot in option_lots.values() if lot["campaign_id"] == campaign_id]
                if len(campaign_lots) != 2 or {lot["leg"] for lot in campaign_lots} != {"short", "long"}:
                    _fail("OPTION_CLOSE_CAMPAIGN_MISSING")
                if index + 1 >= len(calendar):
                    _fail("OPTION_CLOSE_EXECUTION_SESSION_MISSING")
                next_session = calendar[index + 1]
                if any(date.fromisoformat(option_terms[lot["contract_id"]]["expiration"]) <= next_session for lot in campaign_lots):
                    _fail("OPTION_CLOSE_EXPIRATION_CONFLICT")
                pending_soxx_close = (next_session, campaign_id)
            marks = []
            for lot_id, lot in sorted(option_lots.items()):
                quote = quotes.get(lot["contract_id"])
                if quote is None:
                    _fail("OPTION_QUOTE_MISSING")
                if quote["expiration"] == session.isoformat():
                    continue
                price = quote["ask"] if lot["quantity"] < 0 else quote["bid"]
                marks.append(ResearchPositionMark(
                    symbol=lot_id, quantity=lot["quantity"],
                    valuation=price * quote["multiplier"] * lot["quantity"],
                    option_underlying="SOXX", option_right="put", option_strike=quote["strike"],
                    option_expiration=date.fromisoformat(quote["expiration"]),
                    option_multiplier=quote["multiplier"],
                    option_premium_cashflow=(
                        lot["cost_basis_per_contract"] if lot["quantity"] < 0
                        else -lot["cost_basis_per_contract"]
                    ),
                ))
            option_marks = tuple(marks)
            cash += option_cashflow
        for event in day_events:
            if event.event_type == "split":
                old_quantity = quantities.get(event.symbol, 0.0)
                if old_quantity <= 0.0:
                    _fail("LEDGER_SPLIT_QUANTITY")
                quantities[event.symbol] = old_quantity * event.ratio
                if (request.whole_share_execution
                        and abs(quantities[event.symbol] - round(quantities[event.symbol])) > 1e-9):
                    _fail("FRACTIONAL_SPLIT_UNSUPPORTED")
            elif event.event_type == "dividend_accrual":
                quantity = quantities.get(event.symbol, 0.0)
                if quantity <= 0.0:
                    _fail("LEDGER_DIVIDEND_QUANTITY")
                amount = quantity * event.per_share
                dividend_receivable += amount
                open_dividends[event.event_id] = (event.symbol, amount)
            elif event.event_type == "dividend_payment":
                accrued = open_dividends.get(event.reference_event_id)
                if accrued is None or accrued[0] != event.symbol or not math.isclose(
                    accrued[1], event.amount, rel_tol=0.0, abs_tol=1e-9
                ):
                    _fail("LEDGER_DIVIDEND_PAIR")
                del open_dividends[event.reference_event_id]
                dividend_receivable -= event.amount
                cash += event.amount
        if pending is not None:
            if pending[0] != session:
                _fail("TIMING_MISMATCH")
            fills = {symbol: _field(prices[(session, symbol)], execution.fill_price_field)
                     for symbol in symbols if (session, symbol) in prices}
            before_equity_trade = dict(quantities)
            cash, quantities, fees, equity_trade_cashflow = _rebalance(
                cash, quantities, pending[1], fills, commission_rate, adverse_rate,
                request.whole_share_execution,
            )
            if soxx_credit_enabled and cash + 1e-9 < restricted_cash:
                _fail("OPTION_COLLATERAL_SPENT")
            trade_net_cashflow = equity_trade_cashflow
            changed_quantities = {
                symbol: quantities[symbol] - before_equity_trade[symbol]
                for symbol in symbols
                if quantities[symbol] != before_equity_trade[symbol]
            }
            if changed_quantities:
                equity_trade_quantities = changed_quantities
                equity_trade_phase = "before_settlement"
            if any(event.event_type == "split" for event in day_events) and (
                trade_net_cashflow != 0.0 or fees != 0.0
            ):
                _fail("LEDGER_SPLIT_TRADE")
            pending = None
        trade_net_cashflow += option_cashflow
        if option_enabled:
            expired_contracts = sorted({
                lot["contract_id"] for lot in option_lots.values()
                if option_terms.get(lot["contract_id"], {}).get("expiration") == session.isoformat()
            })
            expiring_by_underlying: dict[str, list[str]] = {}
            for contract_id in expired_contracts:
                terms = option_terms.get(contract_id)
                if terms is None or (
                    terms["underlying"] == "TQQQ" and terms["right"] != "call"
                ) or (
                    terms["underlying"] == "SOXX" and terms["right"] != "put"
                ) or terms["underlying"] not in {"TQQQ", "SOXX"}:
                    _fail("OPTION_CONTRACT_TERMS_MISSING")
                expiring_by_underlying.setdefault(terms["underlying"], []).append(contract_id)
            for underlying, contract_ids in sorted(expiring_by_underlying.items()):
                spot = _field(prices[(session, underlying)], "close")
                expiring_lots = [
                    lot for lot in option_lots.values() if lot["contract_id"] in contract_ids
                ]
                required_put_assignment_cash = sum(
                    -lot["quantity"] * option_terms[contract_id]["multiplier"]
                    * option_terms[contract_id]["strike"]
                    for contract_id in contract_ids
                    for lot in option_lots.values()
                    if lot["contract_id"] == contract_id
                    and lot.get("quantity", 0.0) < 0
                    and option_terms[contract_id]["right"] == "put"
                    and spot < option_terms[contract_id]["strike"]
                )
                if cash < required_put_assignment_cash:
                    _fail("OPTION_EXERCISE_CASH_INSUFFICIENT")
                settled_any = False
                for contract_id in contract_ids:
                    terms = option_terms[contract_id]
                    if terms["expiration"] != session.isoformat():
                        _fail("OPTION_EXPIRATION_SESSION_MISSING")
                    settling_lots = [lot for lot in option_lots.values() if lot["contract_id"] == contract_id]
                    if not settling_lots:
                        _fail("OPTION_SETTLEMENT_DUPLICATE")
                    intrinsic = (
                        max(spot - terms["strike"], 0.0)
                        if terms["right"] == "call"
                        else max(terms["strike"] - spot, 0.0)
                    )
                    if intrinsic > 0.0:
                        signed_contracts = sum(
                            lot.get("quantity", 1.0) for lot in settling_lots
                        ) * terms["multiplier"]
                        if terms["right"] == "call":
                            call_cashflow = -signed_contracts * terms["strike"]
                            if call_cashflow < 0.0 and cash < -call_cashflow:
                                _fail("OPTION_EXERCISE_CASH_INSUFFICIENT")
                            cash += call_cashflow
                            quantities[underlying] += signed_contracts
                            option_settlement_cashflow += call_cashflow
                        else:
                            short_put_debit = sum(
                                -lot.get("quantity", 0.0) * terms["multiplier"] * terms["strike"]
                                for lot in settling_lots if lot.get("quantity", 0.0) < 0
                            )
                            if cash < short_put_debit:
                                _fail("OPTION_EXERCISE_CASH_INSUFFICIENT")
                            put_cashflow = sum(
                                lot["quantity"] * terms["multiplier"] * terms["strike"]
                                for lot in settling_lots
                            )
                            cash += put_cashflow
                            quantities[underlying] -= signed_contracts
                            option_settlement_cashflow += put_cashflow
                    for lot in settling_lots:
                        del option_lots[lot["ledger_position_id"]]
                    settled_any = True
                if not settled_any:
                    _fail("OPTION_SETTLEMENT_DUPLICATE")
                settlement_events.append(ResearchLedgerEvent(
                    event_id=f"option-settlement:{underlying}:{session.isoformat()}",
                    event_type="option_settlement", symbol=underlying,
                    settlement_price=spot,
                ))
                if soxx_credit_enabled and underlying == "SOXX":
                    settling_campaigns = {
                        position["campaign_id"] for position in expiring_lots
                        if position["contract_id"] in contract_ids
                    }
                    released = 0.0
                    for campaign_id in settling_campaigns:
                        campaign_basis = next(
                            position["campaign_basis"] for position in expiring_lots
                            if position["campaign_id"] == campaign_id
                        )
                        released += campaign_basis
                        settlement_events.append(ResearchLedgerEvent(
                            event_id=f"soxx-collateral-expiry:{campaign_id}:{session.isoformat()}",
                            event_type="collateral_change", symbol="CASH", amount=-campaign_basis,
                        ))
                    if restricted_cash + 1e-9 < released:
                        _fail("OPTION_COLLATERAL_INVALID")
                    restricted_cash -= released
        if option_settlement_cashflow != 0.0:
            # Settlement is a separate cashflow; it must not be financed by an implicit margin balance.
            if cash < 0.0:
                _fail("OPTION_EXERCISE_CASH_INSUFFICIENT")
        income_cashflow = income_cashflows.get(session, 0.0)
        if ledger_events is None:
            cash += income_cashflow
        cash_scale = max(
            abs(cash - external_cashflow - income_cashflow),
            abs(external_cashflow), abs(income_cashflow), 1.0,
        )
        if not math.isfinite(cash) or cash < -4 * math.ulp(cash_scale):
            _fail("CASH_INVALID")
        market_values = {symbol: quantities[symbol] * closes[symbol]
                         if quantities[symbol] != 0.0 else 0.0 for symbol in symbols}
        nav = cash + sum(market_values.values()) + dividend_receivable + sum(mark.valuation for mark in option_marks)
        if not math.isfinite(nav) or nav <= 0.0:
            _fail("CASH_INVALID")
        # New option entries are decided from this session's funded, marked NAV.
        # Held lots and pending exits remain managed regardless of entry threshold.
        if option_enabled and index < len(calendar) - 1 and not option_lots:
            if soxx_credit_enabled:
                if (nav >= float(config["option_income_overlay_start_usd"])
                        and option_row["soxx_trend"]["positive"]
                        and not closed_campaign_today):
                    if option_row["iv_rank"] is None:
                        _fail("OPTION_IV_RANK_REQUIRED")
                    if option_row["iv_rank"]["value"] <= 0.80:
                        if not quotes:
                            _fail("OPTION_QUOTE_MISSING")
                        candidate = _soxx_put_credit_candidate(tuple(quotes.values()), session, closes["SOXX"])
                        if candidate is not None:
                            pending_soxx_spread = (candidate[0]["contract_id"], candidate[1]["contract_id"])
            elif gate and nav >= float(config["option_growth_overlay_start_usd"]):
                if not quotes:
                    _fail("OPTION_QUOTE_MISSING")
                selected = _tqqq_leaps_candidate(quotes, session, config)
                if selected is not None:
                    budget = nav * float(config.get("option_growth_overlay_nav_budget_ratio", 0.03))
                    mid = (selected["bid"] + selected["ask"]) / 2.0
                    limit_price = round(min(selected["ask"], mid * 1.03), 2)
                    contracts = math.floor(budget / (limit_price * selected["multiplier"]))
                    if contracts > 0:
                        pending_option_open = (selected["contract_id"], contracts, selected)
        return_basis = previous_nav + external_cashflow
        if return_basis <= 0.0:
            _fail("CASH_INVALID")
        points.append(
            DailyReplayPoint(
                session=session,
                cash=cash,
                fees=fees,
                nav=nav,
                daily_return=None if index == 0 else nav / return_basis - 1.0,
                holdings=tuple((symbol, quantities[symbol]) for symbol in sorted(symbols)),
                market_values=tuple((symbol, market_values[symbol]) for symbol in sorted(symbols)),
                trade_net_cashflow=trade_net_cashflow,
                income_cashflow=income_cashflow,
                external_cashflow=external_cashflow,
                dividend_receivable=dividend_receivable,
                declared_event_ids=(
                    None if day_event_row is None and not option_enabled else tuple(
                        (day_event_row["declared_event_ids"] if day_event_row is not None else ())
                        + tuple(event.event_id for event in option_events)
                        + tuple(event.event_id for event in settlement_events)
                    )
                ),
                events=day_events + tuple(option_events) + tuple(settlement_events),
                option_positions=option_marks,
                option_settlement_cashflow=option_settlement_cashflow,
                restricted_cash=restricted_cash,
                equity_trade_cashflow=equity_trade_cashflow if equity_trade_quantities else None,
                equity_trade_quantities=equity_trade_quantities if settlement_events else None,
                equity_trade_phase=equity_trade_phase if settlement_events else None,
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
        snapshot_metadata = dict(metadata)
        if session in states_by_date:
            state = states_by_date[session]
            if allow_research_market_regime:
                snapshot_metadata["market_regime_control"] = deepcopy(
                    state["market_regime_control"]
                )
            else:
                snapshot_metadata.update(deepcopy(state))
        try:
            context = StrategyContext(
                as_of=session.isoformat(),
                market_data=market_data,
                portfolio=_snapshot(
                    session, cash, quantities, closes, snapshot_metadata, dividend_receivable,
                    sum(mark.valuation for mark in option_marks),
                ),
                runtime_config=deepcopy(config),
            )
            if allow_research_market_regime:
                context = _research_only_market_regime_context(context)
            decision = builder(context)
        except OptimizedStrategyReplayError:
            raise
        except Exception as exc:
            raise OptimizedStrategyReplayError("DECISION_INVALID") from exc
        _check_timing(decision, session, effective)
        targets = _targets(decision, symbols)
        pending = (effective, targets)
        decision_targets.append((session, effective, tuple(sorted(targets.items()))))
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
        "prices": [_canonical_bar(bar) for bar in request.prices],
    }
    if indicators is not None:
        input_identity["derived_indicators"] = {
            session.isoformat(): payload for session, payload in indicators.items()
        }
        if request.indicator_sources is not None:
            input_identity["indicator_sources"] = {
                day.isoformat(): values for day, values in request.indicator_sources.items()
            }
    else:
        input_identity["benchmark_bars"] = [_canonical_bar(bar) for bar in benchmark]
    if state_inputs is not None:
        input_identity["state_inputs_sha256"] = _member_identity_digest(list(state_inputs))
    if request.income_cashflow is not None:
        input_identity["income_cashflow"] = {
            day.isoformat(): amount for day, amount in income_cashflows.items()
        }
    if request.external_cashflow is not None:
        input_identity["external_cashflow"] = {
            day.isoformat(): amount for day, amount in external_cashflows.items()
        }
        if ledger_events is not None:
            input_identity["ledger_events"] = {
            day.isoformat(): {
                "declared_event_ids": list(row["declared_event_ids"]),
                "events": [event.to_dict() for event in row["events"]],
            }
                for day, row in ledger_events.items()
            }
        if option_rows is not None:
            input_identity["option_market_inputs"] = list(request.option_market_inputs or ())
        if option_rows is not None:
            input_identity["option_market_inputs"] = list(request.option_market_inputs or ())
    run_identity = {
        "strategy_profile": profile,
        "param_set_id": param_set_id,
        "source_revision": source_revision,
        "calendar_id": calendar_id,
        "periods_per_year": periods_per_year,
        "effective_runtime_config": effective_config,
        "initial_cash": initial_cash,
        "initial_quantities": {symbol: initial_quantities[symbol] for symbol in sorted(symbols)},
        "whole_share_execution": request.whole_share_execution,
        "inputs": input_identity,
        "cost": cost_identity,
        "execution": {
            "signal_effective_after_trading_days": execution.signal_effective_after_trading_days,
            "execution_timing_contract": execution.execution_timing_contract,
            "fill_price_field": execution.fill_price_field,
            "nav_mark_field": execution.nav_mark_field,
        },
    }
    if research_identity is not None:
        run_identity["research_identity"] = research_identity
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
            "gaps": list(REPLAY_GAPS),
            **({"research_identity": research_identity} if research_identity is not None else {}),
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
        decision_targets=tuple(decision_targets),
    )


def _safe_reason(code: str) -> str:
    head = code.split(":", 1)[0].lower()
    cleaned = "".join(char if char in "abcdefghijklmnopqrstuvwxyz0123456789_" else "_" for char in head).strip("_")
    if not cleaned or cleaned[0] not in "abcdefghijklmnopqrstuvwxyz" or len(cleaned) > 64:
        return "rejected"
    return cleaned


def _canonical_number(value: object) -> float:
    return _signed(value, "NONFINITE_INPUT")


def _canonical_calendar(value: object) -> list[str]:
    if type(value) is not tuple or len(value) < 2 or any(type(item) is not date for item in value):
        _fail("CALENDAR_INVALID")
    return [item.isoformat() for item in value]


def _canonical_bar(bar: object) -> dict[str, object]:
    if type(bar) is not DatedBar or type(bar.session) is not date or type(bar.symbol) is not str or not bar.symbol:
        _fail("INPUT_GAP")
    result: dict[str, object] = {
        "session": bar.session.isoformat(),
        "symbol": bar.symbol,
        "open": _canonical_number(bar.open),
        "high": _canonical_number(bar.high),
        "low": _canonical_number(bar.low),
        "close": _canonical_number(bar.close),
    }
    if bar.source_id is not None or bar.available_at is not None:
        result["source_id"] = _text(bar.source_id, "INVALID_FIELD:bar.source_id")
        result["available_at"] = _utc_timestamp(
            bar.available_at, "INVALID_FIELD:bar.available_at"
        ).isoformat().replace("+00:00", "Z")
    return result


def _canonical_bars(value: object) -> list[dict[str, object]]:
    if type(value) is not tuple:
        _fail("INPUT_GAP")
    parsed = [_canonical_bar(bar) for bar in value]
    keys = [(item["session"], item["symbol"]) for item in parsed]
    if len(keys) != len(set(keys)):
        _fail("INPUT_GAP")
    return sorted(parsed, key=lambda item: (str(item["session"]), str(item["symbol"])))


def _canonical_indicators(value: object) -> list[list[object]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _fail("MISSING_FIELD:derived_indicators")
    sessions: list[list[object]] = []
    for session, payload in value.items():
        if type(session) is not date or not isinstance(payload, Mapping):
            _fail("MISSING_FIELD:derived_indicators")
        symbols: list[list[object]] = []
        for symbol, metrics in payload.items():
            if type(symbol) is not str or not symbol or not isinstance(metrics, Mapping):
                _fail("MISSING_FIELD:derived_indicators")
            rows = []
            for key, metric in metrics.items():
                if type(key) is not str or not key:
                    _fail("MISSING_FIELD:derived_indicators")
                rows.append([key, _canonical_number(metric)])
            rows.sort()
            if len(rows) != len({row[0] for row in rows}):
                _fail("MISSING_FIELD:derived_indicators")
            symbols.append([symbol, rows])
        symbols.sort()
        if len(symbols) != len({row[0] for row in symbols}):
            _fail("MISSING_FIELD:derived_indicators")
        sessions.append([session.isoformat(), symbols])
    sessions.sort()
    if len(sessions) != len({row[0] for row in sessions}):
        _fail("MISSING_FIELD:derived_indicators")
    return sessions


def _canonical_quantities(value: object) -> list[list[object]]:
    if not isinstance(value, Mapping):
        _fail("MISSING_FIELD:initial_quantities")
    rows: list[list[object]] = []
    for symbol, quantity in value.items():
        if type(symbol) is not str or not symbol:
            _fail("MISSING_FIELD:initial_quantities")
        rows.append([symbol, _canonical_number(quantity)])
    rows.sort()
    if len(rows) != len({row[0] for row in rows}):
        _fail("MISSING_FIELD:initial_quantities")
    return rows


def _utc_timestamp(value: object, code: str) -> datetime:
    if type(value) is not str or not _UTC_TIMESTAMP.fullmatch(value):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        _fail(code)
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        _fail(code)
    return parsed.astimezone(UTC)


def _validated_state_inputs(
    value: object,
    signals: tuple[date, ...],
) -> tuple[dict[str, Any], ...] | None:
    if value is None:
        return None
    if type(value) is not tuple:
        _fail("INVALID_FIELD:state_inputs")
    expected = {"signal_date", "decision_at", "available_at", "state"}
    rows: list[dict[str, Any]] = []
    sessions: list[date] = []
    seen: set[date] = set()
    for item in value:
        item_expected = expected | ({"value_sources"} if isinstance(item, Mapping) and "value_sources" in item else set())
        if not isinstance(item, Mapping) or set(item) != item_expected:
            _fail("INVALID_FIELD:state_inputs")
        raw_session = item["signal_date"]
        if type(raw_session) is not str:
            _fail("INVALID_FIELD:state_inputs.signal_date")
        try:
            session = date.fromisoformat(raw_session)
        except ValueError:
            _fail("INVALID_FIELD:state_inputs.signal_date")
        if session.isoformat() != raw_session:
            _fail("INVALID_FIELD:state_inputs.signal_date")
        if session in seen:
            _fail("DUPLICATE_FIELD:state_inputs.signal_date")
        seen.add(session)
        decision_at = _utc_timestamp(item["decision_at"], "INVALID_FIELD:state_inputs.decision_at")
        available_at = _utc_timestamp(item["available_at"], "INVALID_FIELD:state_inputs.available_at")
        if decision_at.date() != session:
            _fail("STATE_DECISION_DATE_MISMATCH")
        if available_at > decision_at:
            _fail("STATE_NOT_AVAILABLE_AT_DECISION")
        if type(item["state"]) is not dict:
            _fail("INVALID_FIELD:state_inputs.state")
        try:
            canonical = json.loads(
                json.dumps(item, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError):
            _fail("INVALID_FIELD:state_inputs.state")
        rows.append(canonical)
        sessions.append(session)
    if set(sessions) != set(signals):
        _fail("STATE_INPUT_GAP")
    if tuple(sessions) != signals:
        _fail("STATE_INPUT_ORDER")
    return tuple(rows)


def _validate_research_market_regime_states(
    state_inputs: tuple[dict[str, Any], ...] | None,
) -> None:
    if state_inputs is None:
        _fail("RESEARCH_MARKET_REGIME_ARTIFACT_REQUIRED")
    required_context_flags = (
        "actionable_for_position_control",
        "hard_risk",
        "soft_risk",
        "constructive",
        "rebound_confirm",
    )
    for row in state_inputs:
        artifact = row["state"].get("market_regime_control")
        if not isinstance(artifact, Mapping):
            _fail("RESEARCH_MARKET_REGIME_ARTIFACT_REQUIRED")
        if (
            str(artifact.get("plugin") or artifact.get("profile") or "").strip().lower()
            != "market_regime_control"
            or type(artifact.get("schema_version")) is not str
            or not artifact["schema_version"].strip()
            or artifact.get("as_of") != row["signal_date"]
        ):
            _fail("RESEARCH_MARKET_REGIME_ARTIFACT_INVALID")
        controls = artifact.get("execution_controls")
        position = artifact.get("position_control")
        if (
            not isinstance(controls, Mapping)
            or controls.get("position_control_allowed") is not True
            or str(controls.get("consumption_evidence_status") or "").strip().lower()
            != "research_backtest_approved"
            or not isinstance(position, Mapping)
            or position.get("final_route") not in {"no_action", "risk_on", "risk_reduced", "risk_off"}
            or type(position.get("route_source")) is not str
            or not position["route_source"].strip()
            or type(position.get("suggested_action")) is not str
            or not position["suggested_action"].strip()
        ):
            _fail("RESEARCH_MARKET_REGIME_ARTIFACT_UNAPPROVED")
        for key in ("risk_budget_scalar", "leverage_scalar", "risk_asset_scalar"):
            scalar = position.get(key)
            if (
                isinstance(scalar, bool)
                or not isinstance(scalar, (int, float))
                or not math.isfinite(float(scalar))
                or not 0.0 <= float(scalar) <= 1.0
            ):
                _fail("RESEARCH_MARKET_REGIME_ARTIFACT_INVALID")
        context = position.get("volatility_delever_context")
        if not isinstance(context, Mapping) or any(
            type(context.get(key)) is not bool for key in required_context_flags
        ):
            _fail("RESEARCH_MARKET_REGIME_CONTEXT_REQUIRED")


def _validate_tqqq_research_market_regime_states(
    state_inputs: tuple[dict[str, Any], ...] | None,
) -> None:
    if state_inputs is None:
        _fail("RESEARCH_MARKET_REGIME_ARTIFACT_REQUIRED")
    for row in state_inputs:
        artifact = row["state"].get("market_regime_control")
        position = artifact.get("position_control") if isinstance(artifact, Mapping) else None
        if not isinstance(position, Mapping) or any(
            type(position.get(key)) is not bool
            for key in ("taco_allowed", "local_delever_veto_allowed", "crisis_defense_required")
        ):
            _fail("RESEARCH_MARKET_REGIME_ARTIFACT_INVALID")


def _validate_member_input_provenance(request: ReplayRequest, calendar: tuple[date, ...]) -> None:
    # Member fixtures without per-day state use this explicit synthetic decision time;
    # it is not evidence of historical point-in-time availability.
    synthetic_fixture_decision_hour_utc = 21
    strict_member_input = request.research_identity is not None
    decisions = {
        day: datetime.fromisoformat(
            f"{day.isoformat()}T{synthetic_fixture_decision_hour_utc:02d}:00:00+00:00"
        )
        for day in calendar
    }
    if request.state_inputs is not None:
        for row in request.state_inputs:
            signal_day = date.fromisoformat(row["signal_date"])
            decisions[signal_day] = _utc_timestamp(
                row["decision_at"], "INVALID_FIELD:state_inputs.decision_at"
            )
    all_bars = (*request.prices, *request.benchmark_bars)
    if strict_member_input:
        signal_decisions = [(day, decisions[day]) for day in calendar[:-1]]
        for bar in request.prices:
            if type(bar.source_id) is not str or not bar.source_id.strip() or type(bar.available_at) is not str:
                _fail("INPUT_PROVENANCE_REQUIRED:bar")
            available_at = _utc_timestamp(bar.available_at, "INVALID_FIELD:bar.available_at")
            if bar.session in decisions and available_at > decisions[bar.session]:
                _fail("INPUT_NOT_AVAILABLE_AT_DECISION:bar")
        for bar in request.benchmark_bars:
            if type(bar.source_id) is not str or not bar.source_id.strip() or type(bar.available_at) is not str:
                _fail("INPUT_PROVENANCE_REQUIRED:bar")
            available_at = _utc_timestamp(bar.available_at, "INVALID_FIELD:bar.available_at")
            consumers = [decision for signal_day, decision in signal_decisions if bar.session <= signal_day]
            if consumers and available_at > consumers[0]:
                _fail("INPUT_NOT_AVAILABLE_AT_DECISION:bar")
    if strict_member_input and request.derived_indicators is not None:
        sources = request.indicator_sources
        if not isinstance(sources, Mapping):
            _fail("INPUT_PROVENANCE_REQUIRED:derived_indicators")
        for day, symbols in request.derived_indicators.items():
            day_sources = sources.get(day)
            if not isinstance(day_sources, Mapping):
                _fail("INPUT_PROVENANCE_REQUIRED:derived_indicators")
            for symbol, metrics in symbols.items():
                metric_sources = day_sources.get(symbol)
                if not isinstance(metric_sources, Mapping) or set(metric_sources) != set(metrics):
                    _fail("INPUT_PROVENANCE_REQUIRED:derived_indicators")
                for metric in metrics:
                    evidence = metric_sources[metric]
                    if (not isinstance(evidence, Mapping)
                            or set(evidence) != {"source_id", "available_at", "derivation", "source_bars"}
                            or type(evidence["source_id"]) is not str or not evidence["source_id"].strip()
                            or evidence["derivation"] not in {
                                "deterministic_from_known_bars.v1", "synthetic_fixture_literal.v1"
                            }
                            or type(evidence["source_bars"]) is not list
                            or (evidence["derivation"] == "deterministic_from_known_bars.v1"
                                and not evidence["source_bars"])):
                        _fail("INPUT_PROVENANCE_REQUIRED:derived_indicators")
                    if (evidence["derivation"] == "deterministic_from_known_bars.v1"
                            and metric != "price"):
                        _fail("INDICATOR_RECOMPUTATION_UNSUPPORTED")
                    if (evidence["derivation"] == "deterministic_from_known_bars.v1"
                            and len(evidence["source_bars"]) != 1):
                        _fail("INPUT_PROVENANCE_REQUIRED:derived_indicators")
                    available_at = _utc_timestamp(evidence["available_at"], "INVALID_FIELD:indicator.available_at")
                    if day not in decisions or available_at > decisions[day]:
                        _fail("INPUT_NOT_AVAILABLE_AT_DECISION:derived_indicators")
                    for ref in evidence["source_bars"]:
                        if not isinstance(ref, Mapping) or set(ref) != {"session", "symbol", "source_id", "available_at"}:
                            _fail("INPUT_PROVENANCE_REQUIRED:derived_indicators")
                        try:
                            source_day = date.fromisoformat(ref["session"])
                        except (TypeError, ValueError):
                            _fail("INPUT_PROVENANCE_REQUIRED:derived_indicators")
                        if source_day > day:
                            _fail("FUTURE_INDICATOR_SOURCE")
                        matching = [bar for bar in all_bars if
                            bar.session == source_day and bar.symbol == ref["symbol"]
                            and bar.source_id == ref["source_id"] and bar.available_at == ref["available_at"]
                        ]
                        if not matching:
                            _fail("INPUT_PROVENANCE_SOURCE_MISMATCH:derived_indicators")
                        if evidence["derivation"] == "deterministic_from_known_bars.v1":
                            source_bar = matching[0]
                            if (source_day != day or source_bar.symbol.lower() != symbol.lower()
                                    or metric != "price"
                                    or _canonical_number(metrics[metric]) != _canonical_number(source_bar.close)):
                                _fail("INDICATOR_RECOMPUTATION_MISMATCH")
                        if _utc_timestamp(ref["available_at"], "INVALID_FIELD:indicator.source_bar.available_at") > available_at:
                            _fail("INPUT_NOT_AVAILABLE_AT_DECISION:derived_indicators")
    if request.state_inputs is not None and (
        strict_member_input or any("value_sources" in row for row in request.state_inputs)
    ):
        for row in request.state_inputs:
            state, sources = row.get("state"), row.get("value_sources")
            if not isinstance(state, Mapping) or not isinstance(sources, Mapping) or set(sources) != set(state):
                _fail("INPUT_PROVENANCE_REQUIRED:state")
            decision_at = _utc_timestamp(row["decision_at"], "INVALID_FIELD:state_inputs.decision_at")
            for evidence in sources.values():
                if (not isinstance(evidence, Mapping) or set(evidence) != {"source_id", "available_at"}
                        or type(evidence["source_id"]) is not str or not evidence["source_id"].strip()):
                    _fail("INPUT_PROVENANCE_REQUIRED:state")
                if _utc_timestamp(evidence["available_at"], "INVALID_FIELD:state.available_at") > decision_at:
                    _fail("INPUT_NOT_AVAILABLE_AT_DECISION:state")


def _research_option_config_enabled(
    request: ReplayRequest, identity: Mapping[str, Any] | None
) -> bool:
    config = request.runtime_config
    if not isinstance(config, Mapping) or not any(
        _control_enabled(config.get(key))
        for key in ("option_overlay_enabled", "option_growth_overlay_enabled", "option_income_overlay_enabled")
    ):
        return False
    if identity is None or not str(identity.get("schema_version", "")).endswith(".v2") or request.option_market_inputs is None:
        return False
    if request.identity.strategy_profile == "soxl_soxx_trend_income":
        return (
            config.get("option_overlay_enabled") is True
            and config.get("option_growth_overlay_enabled") is False
            and config.get("option_income_overlay_enabled") is True
            and config.get("option_income_overlay_recipe") == "soxx_put_credit_spread_income_v1"
        )
    if (
        request.identity.strategy_profile != "tqqq_growth_income"
        or config.get("option_overlay_enabled") is not True
        or config.get("option_growth_overlay_enabled") is not True
        or config.get("option_growth_overlay_recipe") != "tqqq_leaps_growth_v1"
        or config.get("option_income_overlay_enabled") is True
    ):
        return False
    return True


def _soxx_put_credit_candidate(
    quotes: tuple[dict[str, Any], ...] | tuple[Mapping[str, Any], ...], session: date, spot: float
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    puts = [quote for quote in quotes if quote["right"] == "put" and quote["underlying"] == "SOXX"]
    target_short, target_long = spot * 0.92, spot * 0.82
    expirations = sorted(
        {date.fromisoformat(quote["expiration"]) for quote in puts},
        key=lambda expiry: (abs((expiry - session).days - 45), expiry),
    )
    for expiration in expirations:
        dte = (expiration - session).days
        if not 25 <= dte <= 65:
            continue
        chain = [quote for quote in puts if quote["expiration"] == expiration.isoformat()]
        short = min(chain, key=lambda quote: abs(quote["strike"] - target_short))
        long_rows = [quote for quote in chain if quote["strike"] < short["strike"]]
        if not long_rows:
            continue
        long = min(long_rows, key=lambda quote: abs(quote["strike"] - target_long))
        credit = short["bid"] - long["ask"]
        width = short["strike"] - long["strike"]
        if credit > 0.05 and width > credit:
            return short, long
    return None


def _soxx_initial_collateral(
    positions: list[dict[str, Any]], quotes: Mapping[str, Mapping[str, Any]]
) -> float:
    campaigns: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        campaigns.setdefault(position["campaign_id"], []).append(position)
    collateral = 0.0
    for campaign_id, legs in campaigns.items():
        if len(legs) != 2 or {leg["leg"] for leg in legs} != {"short", "long"}:
            _fail("OPTION_INITIAL_POSITION_INCOMPLETE")
        short = next(leg for leg in legs if leg["leg"] == "short")
        long = next(leg for leg in legs if leg["leg"] == "long")
        short_quote, long_quote = quotes.get(short["contract_id"]), quotes.get(long["contract_id"])
        if short_quote is None or long_quote is None:
            _fail("OPTION_QUOTE_MISSING")
        if any(
            short_quote[field] != long_quote[field]
            for field in ("underlying", "right", "expiration", "multiplier")
        ) or short_quote["right"] != "put" or short_quote["strike"] <= long_quote["strike"]:
            _fail("OPTION_INITIAL_POSITION_INCOMPLETE")
        if short["quantity"] != -1.0 or long["quantity"] != 1.0:
            _fail("OPTION_INITIAL_POSITION_INCOMPLETE")
        credit = (
            short["cost_basis_per_contract"] - long["cost_basis_per_contract"]
        ) / short_quote["multiplier"]
        width = short_quote["strike"] - long_quote["strike"]
        if credit <= 0.05 or width <= credit:
            _fail("OPTION_INITIAL_POSITION_INCOMPLETE")
        required = width * short_quote["multiplier"] - credit * short_quote["multiplier"]
        if not math.isclose(short["campaign_basis"], required, rel_tol=0.0, abs_tol=1e-9) or not math.isclose(
            long["campaign_basis"], required, rel_tol=0.0, abs_tol=1e-9
        ):
            _fail("OPTION_INITIAL_POSITION_INCOMPLETE")
        if short["cost_basis_per_contract"] <= long["cost_basis_per_contract"]:
            _fail("OPTION_INITIAL_POSITION_INCOMPLETE")
        collateral += required
    return collateral


def _validated_option_market_inputs(
    request: ReplayRequest,
    calendar: tuple[date, ...],
    state_inputs: tuple[dict[str, Any], ...] | None,
) -> dict[date, dict[str, Any]] | None:
    value = request.option_market_inputs
    if value is None:
        return None
    if type(value) is not tuple or len(value) != len(calendar):
        _fail("OPTION_INPUT_GAP")
    state_by_day = {date.fromisoformat(row["signal_date"]): row for row in state_inputs or ()}
    result: dict[date, dict[str, Any]] = {}
    position_fields = {
        "ledger_position_id", "contract_id", "campaign_id", "lot_index",
        "cost_basis_per_contract", "campaign_basis", "recovered_proceeds",
    }
    quote_fields = {
        "contract_id", "underlying", "right", "strike", "expiration", "multiplier",
        "bid", "ask", "delta", "source_id", "available_at",
    }
    for index, (expected_day, row) in enumerate(zip(calendar, value, strict=True)):
        if request.identity.strategy_profile == "soxl_soxx_trend_income":
            required = {"session", "decision_at", "soxx_trend", "quotes"}
            if (not isinstance(row, Mapping) or not required <= set(row)
                    or set(row) - required - {"iv_rank", "management", "positions_source", "positions"}
                    or ("positions" in row) != ("positions_source" in row)
                    or (index == 0 and "positions" not in row)
                    or row["session"] != expected_day.isoformat()):
                _fail("OPTION_INPUT_INVALID")
            decision_at = _utc_timestamp(row["decision_at"], "OPTION_INPUT_INVALID")
            if decision_at.date() != expected_day:
                _fail("OPTION_INPUT_SESSION_MISMATCH")
            state_row = state_by_day.get(expected_day)
            if state_row is not None and decision_at != _utc_timestamp(state_row["decision_at"], "OPTION_INPUT_INVALID"):
                _fail("OPTION_INPUT_DECISION_MISMATCH")
            trend, iv_rank = row["soxx_trend"], row.get("iv_rank")
            for evidence, required_value in ((trend, "positive"),):
                if not isinstance(evidence, Mapping) or set(evidence) != {required_value, "source_id", "available_at"}:
                    _fail("OPTION_INPUT_INVALID")
                if type(evidence["source_id"]) is not str or not evidence["source_id"].strip():
                    _fail("OPTION_INPUT_INVALID")
                if _utc_timestamp(evidence["available_at"], "OPTION_INPUT_INVALID") > decision_at:
                    _fail("OPTION_INPUT_NOT_AVAILABLE")
            if type(trend["positive"]) is not bool:
                _fail("OPTION_INPUT_INVALID")
            if "positions_source" in row:
                position_source = row["positions_source"]
                if not isinstance(position_source, Mapping) or set(position_source) != {"source_id", "available_at"} or type(position_source["source_id"]) is not str or not position_source["source_id"].strip():
                    _fail("OPTION_POSITION_SOURCE_REQUIRED")
                if _utc_timestamp(position_source["available_at"], "OPTION_INPUT_INVALID") > decision_at:
                    _fail("OPTION_INPUT_NOT_AVAILABLE")
            if ("positions" in row and type(row["positions"]) is not list) or type(row["quotes"]) is not list:
                _fail("OPTION_INPUT_INVALID")
            management = row.get("management")
            checked_management = None
            if management is not None:
                if not isinstance(management, Mapping) or set(management) != {
                    "action", "campaign_id", "source_id", "available_at"
                } or management["action"] != "close_spread":
                    _fail("OPTION_MANAGEMENT_INVALID")
                if any(
                    type(management[field]) is not str or not management[field].strip()
                    for field in ("campaign_id", "source_id")
                ):
                    _fail("OPTION_MANAGEMENT_INVALID")
                available_at = _utc_timestamp(management["available_at"], "OPTION_MANAGEMENT_INVALID")
                if available_at > decision_at:
                    _fail("OPTION_INPUT_NOT_AVAILABLE")
                checked_management = dict(management)
            checked_positions = []
            seen_position_ids: set[str] = set()
            for position in row.get("positions", []):
                fields = {"ledger_position_id", "contract_id", "campaign_id", "leg", "quantity", "cost_basis_per_contract", "campaign_basis", "recovered_proceeds"}
                if not isinstance(position, Mapping) or set(position) != fields or position["leg"] not in {"short", "long"}:
                    _fail("OPTION_POSITION_INVALID")
                checked = dict(position)
                if any(type(checked[key]) is not str or not checked[key].strip() for key in (
                    "ledger_position_id", "contract_id", "campaign_id"
                )):
                    _fail("OPTION_POSITION_INVALID")
                if checked["ledger_position_id"] in seen_position_ids:
                    _fail("OPTION_POSITION_INVALID")
                seen_position_ids.add(checked["ledger_position_id"])
                checked["quantity"] = _signed(checked["quantity"], "OPTION_POSITION_INVALID")
                if checked["quantity"] != (-1.0 if checked["leg"] == "short" else 1.0):
                    _fail("OPTION_POSITION_INVALID")
                checked["cost_basis_per_contract"] = _number(
                    checked["cost_basis_per_contract"], "OPTION_POSITION_INVALID", positive=True
                )
                checked["campaign_basis"] = _number(
                    checked["campaign_basis"], "OPTION_POSITION_INVALID", positive=True
                )
                checked["recovered_proceeds"] = _number(
                    checked["recovered_proceeds"], "OPTION_POSITION_INVALID"
                )
                checked_positions.append(checked)
            iv_rank_value = None
            if iv_rank is not None:
                if not isinstance(iv_rank, Mapping) or set(iv_rank) != {"value", "source_id", "available_at"}:
                    _fail("OPTION_INPUT_INVALID")
                if type(iv_rank["source_id"]) is not str or not iv_rank["source_id"].strip():
                    _fail("OPTION_INPUT_INVALID")
                if _utc_timestamp(iv_rank["available_at"], "OPTION_INPUT_INVALID") > decision_at:
                    _fail("OPTION_INPUT_NOT_AVAILABLE")
                iv_rank_value = _number(iv_rank["value"], "OPTION_INPUT_INVALID")
                if not 0.0 <= iv_rank_value <= 1.0:
                    _fail("OPTION_INPUT_INVALID")
            if iv_rank_value is None and "positions" in row and not checked_positions:
                _fail("OPTION_IV_RANK_REQUIRED")
            checked_quotes = []
            seen = set()
            for quote in row["quotes"]:
                if not isinstance(quote, Mapping) or set(quote) != quote_fields:
                    _fail("OPTION_QUOTE_INVALID")
                if quote["contract_id"] in seen or quote["underlying"] != "SOXX" or quote["right"] != "put":
                    _fail("OPTION_QUOTE_INVALID")
                seen.add(quote["contract_id"])
                expiration = date.fromisoformat(quote["expiration"])
                if expiration.isoformat() != quote["expiration"] or expiration < expected_day:
                    _fail("OPTION_QUOTE_INVALID")
                checked = dict(quote)
                for key in ("strike", "multiplier", "ask"):
                    checked[key] = _number(quote[key], "OPTION_QUOTE_INVALID", positive=True)
                checked["bid"] = _number(quote["bid"], "OPTION_QUOTE_INVALID")
                checked["delta"] = _signed(quote["delta"], "OPTION_QUOTE_INVALID")
                if checked["multiplier"] != 100.0:
                    _fail("OPTION_QUOTE_INVALID")
                if checked["bid"] > checked["ask"] or type(quote["source_id"]) is not str or not quote["source_id"].strip():
                    _fail("OPTION_QUOTE_INVALID")
                if _utc_timestamp(quote["available_at"], "OPTION_QUOTE_INVALID") > decision_at:
                    _fail("OPTION_INPUT_NOT_AVAILABLE")
                checked_quotes.append(checked)
            result[expected_day] = {
                "soxx_trend": dict(trend),
                "iv_rank": None if iv_rank is None else {**iv_rank, "value": iv_rank_value},
                "positions": checked_positions if "positions" in row else None,
                "quotes": checked_quotes,
                "management": checked_management,
            }
            continue
        required = {"session", "decision_at", "qqq_indicator", "quotes"}
        if (not isinstance(row, Mapping) or not required <= set(row)
                or set(row) - required - {"positions_source", "positions"}
                or ("positions" in row) != ("positions_source" in row)
                or (index == 0 and "positions" not in row)):
            _fail("OPTION_INPUT_INVALID")
        if row["session"] != expected_day.isoformat():
            _fail("OPTION_INPUT_GAP")
        decision_at = _utc_timestamp(row["decision_at"], "OPTION_INPUT_INVALID")
        if decision_at.date() != expected_day:
            _fail("OPTION_INPUT_SESSION_MISMATCH")
        state_row = state_by_day.get(expected_day)
        if state_row is not None and decision_at != _utc_timestamp(state_row["decision_at"], "OPTION_INPUT_INVALID"):
            _fail("OPTION_INPUT_DECISION_MISMATCH")
        indicator = row["qqq_indicator"]
        if not isinstance(indicator, Mapping) or set(indicator) != {
            "above_200dma", "momentum_63d", "source_id", "available_at"
        } or type(indicator["above_200dma"]) is not bool or type(indicator["source_id"]) is not str or not indicator["source_id"].strip():
            _fail("OPTION_INDICATOR_INVALID")
        momentum = _signed(indicator["momentum_63d"], "OPTION_INDICATOR_INVALID")
        if _utc_timestamp(indicator["available_at"], "OPTION_INDICATOR_INVALID") > decision_at:
            _fail("OPTION_INPUT_NOT_AVAILABLE")
        if "positions_source" in row:
            position_source = row["positions_source"]
            if not isinstance(position_source, Mapping) or set(position_source) != {"source_id", "available_at"} or type(position_source["source_id"]) is not str or not position_source["source_id"].strip():
                _fail("OPTION_POSITION_SOURCE_REQUIRED")
            if _utc_timestamp(position_source["available_at"], "OPTION_POSITION_SOURCE_REQUIRED") > decision_at:
                _fail("OPTION_INPUT_NOT_AVAILABLE")
        if ("positions" in row and type(row["positions"]) is not list) or type(row["quotes"]) is not list:
            _fail("OPTION_INPUT_INVALID")
        positions: list[dict[str, Any]] = []
        seen_positions: set[str] = set()
        for position in row.get("positions", []):
            if not isinstance(position, Mapping) or set(position) != position_fields:
                _fail("OPTION_POSITION_INVALID")
            if type(position["ledger_position_id"]) is not str or not position["ledger_position_id"] or position["ledger_position_id"] in seen_positions:
                _fail("OPTION_POSITION_INVALID")
            seen_positions.add(position["ledger_position_id"])
            if type(position["contract_id"]) is not str or type(position["campaign_id"]) is not str or type(position["lot_index"]) is not int or position["lot_index"] <= 0:
                _fail("OPTION_POSITION_INVALID")
            checked = dict(position)
            for key in ("cost_basis_per_contract", "campaign_basis"):
                checked[key] = _number(position[key], "OPTION_POSITION_INVALID", positive=True)
            checked["recovered_proceeds"] = _number(position["recovered_proceeds"], "OPTION_POSITION_INVALID")
            positions.append(checked)
        quotes: list[dict[str, Any]] = []
        seen_quotes: set[str] = set()
        for quote in row["quotes"]:
            if not isinstance(quote, Mapping) or set(quote) != quote_fields:
                _fail("OPTION_QUOTE_INVALID")
            if type(quote["contract_id"]) is not str or not quote["contract_id"] or quote["contract_id"] in seen_quotes:
                _fail("OPTION_QUOTE_INVALID")
            seen_quotes.add(quote["contract_id"])
            if quote["underlying"] != "TQQQ" or quote["right"] != "call" or type(quote["expiration"]) is not str:
                _fail("OPTION_QUOTE_INVALID")
            try:
                expiration = date.fromisoformat(quote["expiration"])
            except ValueError:
                _fail("OPTION_QUOTE_INVALID")
            if expiration.isoformat() != quote["expiration"] or expiration < expected_day:
                _fail("OPTION_QUOTE_INVALID")
            checked_quote = dict(quote)
            for key in ("strike", "multiplier", "ask"):
                checked_quote[key] = _number(quote[key], "OPTION_QUOTE_INVALID", positive=True)
            checked_quote["bid"] = _number(quote["bid"], "OPTION_QUOTE_INVALID")
            checked_quote["delta"] = _number(quote["delta"], "OPTION_QUOTE_INVALID")
            if checked_quote["ask"] < checked_quote["bid"] or not 0.0 <= checked_quote["delta"] <= 1.0:
                _fail("OPTION_QUOTE_INVALID")
            if type(quote["source_id"]) is not str or not quote["source_id"].strip():
                _fail("OPTION_QUOTE_SOURCE_REQUIRED")
            if _utc_timestamp(quote["available_at"], "OPTION_QUOTE_SOURCE_REQUIRED") > decision_at:
                _fail("OPTION_INPUT_NOT_AVAILABLE")
            quotes.append(checked_quote)
        result[expected_day] = {
            "qqq_indicator": {**indicator, "momentum_63d": momentum},
            "positions": positions if "positions" in row else None,
            "quotes": quotes,
        }
    return result


def _input_id(request: ReplayRequest) -> str:
    material = {
        "benchmark_bars": _canonical_bars(request.benchmark_bars),
        "calendar": _canonical_calendar(request.calendar),
        "calendar_id": _text(request.calendar_id, "MISSING_FIELD:calendar_id"),
        "derived_indicators": _canonical_indicators(request.derived_indicators),
        "evidence_use": _text(request.evidence_use, "MISSING_FIELD:evidence_use"),
        "initial_cash": _canonical_number(request.initial_cash),
        "initial_quantities": _canonical_quantities(request.initial_quantities),
        "prices": _canonical_bars(request.prices),
    }
    if request.state_inputs is not None:
        calendar = _calendar(request.calendar)
        material["state_inputs"] = list(
            _validated_state_inputs(request.state_inputs, calendar[:-1]) or ()
        )
    if request.option_market_inputs is not None:
        material["option_market_inputs"] = list(request.option_market_inputs)
    if request.option_market_inputs is not None:
        material["option_market_inputs"] = list(request.option_market_inputs)
    if request.indicator_sources is not None:
        material["indicator_sources"] = {
            day.isoformat(): payload for day, payload in request.indicator_sources.items()
        }
    if request.income_cashflow is not None:
        material["income_cashflow"] = {
            day.isoformat(): amount
            for day, amount in _validated_income_cashflows(
                request.income_cashflow, _calendar(request.calendar)
            ).items()
        }
    if request.external_cashflow is not None:
        material["external_cashflow"] = {
            day.isoformat(): amount for day, amount in _validated_external_cashflows(
                request.external_cashflow, _calendar(request.calendar)
            ).items()
        }
    if request.ledger_events is not None:
        material["ledger_events"] = {
            day.isoformat(): {
                "declared_event_ids": list(row["declared_event_ids"]),
                "events": [event.to_dict() for event in row["events"]],
            }
            for day, row in _validated_ledger_events(
                request.ledger_events, _calendar(request.calendar)
            ).items()
        }
    try:
        encoded = json.dumps(
            material, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError):
        _fail("NONFINITE_INPUT")
    return "fixture-" + hashlib.sha256(encoded.encode()).hexdigest()


def _validated_income_cashflows(
    value: object,
    calendar: tuple[date, ...],
) -> dict[date, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _fail("INVALID_FIELD:income_cashflow")
    allowed = set(calendar[1:])
    result: dict[date, float] = {}
    for raw_day, raw_amount in value.items():
        if type(raw_day) is date:
            day = raw_day
        elif type(raw_day) is str:
            try:
                day = date.fromisoformat(raw_day)
            except ValueError:
                _fail("INVALID_FIELD:income_cashflow.date")
            if day.isoformat() != raw_day:
                _fail("INVALID_FIELD:income_cashflow.date")
        else:
            _fail("INVALID_FIELD:income_cashflow.date")
        if day not in allowed:
            _fail("INVALID_FIELD:income_cashflow.date")
        if day in result:
            _fail("DUPLICATE_FIELD:income_cashflow.date")
        result[day] = _canonical_number(raw_amount)
    return result


def _validated_external_cashflows(value: object, calendar: tuple[date, ...]) -> dict[date, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _fail("INVALID_FIELD:external_cashflow")
    allowed = set(calendar[1:])
    result: dict[date, float] = {}
    for raw_day, raw_amount in value.items():
        if type(raw_day) is date:
            day = raw_day
        elif type(raw_day) is str:
            try:
                day = date.fromisoformat(raw_day)
            except ValueError:
                _fail("INVALID_FIELD:external_cashflow.date")
            if day.isoformat() != raw_day:
                _fail("INVALID_FIELD:external_cashflow.date")
        else:
            _fail("INVALID_FIELD:external_cashflow.date")
        if day not in allowed:
            _fail("INVALID_FIELD:external_cashflow.date")
        if day in result:
            _fail("DUPLICATE_FIELD:external_cashflow.date")
        result[day] = _canonical_number(raw_amount)
    return result


def _validated_ledger_events(
    value: object,
    calendar: tuple[date, ...],
) -> dict[date, dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _fail("INVALID_FIELD:ledger_events")
    expected_days = set(calendar[1:])
    parsed_rows: dict[date, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for raw_day, raw_row in value.items():
        if type(raw_day) is date:
            day = raw_day
        elif type(raw_day) is str:
            try:
                day = date.fromisoformat(raw_day)
            except ValueError:
                _fail("INVALID_FIELD:ledger_events.date")
            if day.isoformat() != raw_day:
                _fail("INVALID_FIELD:ledger_events.date")
        else:
            _fail("INVALID_FIELD:ledger_events.date")
        if day not in expected_days or day in parsed_rows:
            _fail("INVALID_FIELD:ledger_events.date")
        if not isinstance(raw_row, Mapping) or set(raw_row) != {"declared_event_ids", "events"}:
            _fail("INVALID_FIELD:ledger_events.row")
        declared, raw_events = raw_row["declared_event_ids"], raw_row["events"]
        if (not isinstance(declared, (list, tuple)) or isinstance(declared, (str, bytes))
                or not isinstance(raw_events, (list, tuple)) or isinstance(raw_events, (str, bytes))):
            _fail("INVALID_FIELD:ledger_events.row")
        try:
            events = tuple(
                event if isinstance(event, ResearchLedgerEvent) else ResearchLedgerEvent(**event)
                for event in raw_events
            )
        except (TypeError, ValueError):
            _fail("INVALID_FIELD:ledger_events.event")
        ids = tuple(declared)
        event_ids = tuple(event.event_id for event in events)
        if (any(type(event_id) is not str for event_id in ids)
                or len(set(ids)) != len(ids) or set(ids) != set(event_ids)):
            _fail("LEDGER_EVENT_SET")
        if seen_ids.intersection(event_ids):
            _fail("LEDGER_EVENT_DUPLICATE")
        seen_ids.update(event_ids)
        phases = {
            "split": 0, "dividend_accrual": 1, "dividend_payment": 2,
            "option_trade": 3, "option_settlement": 4, "collateral_change": 5,
        }
        phase_sequence = [phases[event.event_type] for event in events]
        if phase_sequence != sorted(phase_sequence):
            _fail("LEDGER_EVENT_ORDER")
        parsed_rows[day] = {"declared_event_ids": ids, "events": events}
    if set(parsed_rows) != expected_days:
        _fail("LEDGER_EVENT_DAY_GAP")
    return parsed_rows


def _started_trial(request: ReplayRequest, trial_id: str) -> ResearchTrialRecord:
    if type(request) is not ReplayRequest or type(request.identity) is not ReplayIdentity:
        _fail("MISSING_FIELD:identity")
    calendar = request.calendar
    if type(calendar) is not tuple or len(calendar) < 2 or any(type(item) is not date for item in calendar):
        _fail("CALENDAR_INVALID")
    if type(request.cost_model) is not PromotionCostModel:
        _fail("MISSING_FIELD:cost_model")
    identity = request.identity
    research_identity = _checked_research_identity(request)
    cost = request.cost_model
    try:
        return ResearchTrialRecord(
            trial_id=trial_id,
            domain=identity.domain,
            strategy_profile=identity.strategy_profile,
            status=ResearchTrialStatus.STARTED,
            candidate_config_id=identity.param_set_id,
            actual_params=None,
            param_set_id=identity.param_set_id,
            source_revision=identity.source_revision,
            input_id=_input_id(request),
            window_start=calendar[0],
            window_end=calendar[-1],
            calendar_id=request.calendar_id,
            periods_per_year=request.periods_per_year,
            cost_source=cost.model_id,
            cost_inputs={
                "commission_bps": float(cost.commission_bps),
                "slippage_bps": float(cost.slippage_bps),
                "market_impact_bps": float(cost.market_impact_bps),
            },
            reason_code="",
            synthetic=True,
            run_id=None,
            param_version=None,
            research_identity=research_identity,
        )
    except OptimizedStrategyReplayError:
        raise
    except ValueError as exc:
        _fail("INVALID_FIELD:" + _safe_reason(str(exc)))


def _terminal(
    started: ResearchTrialRecord,
    status: ResearchTrialStatus,
    reason_code: str,
    *,
    actual_params: dict[str, Any] | None = None,
    run_id: str | None = None,
    param_version: int | None = None,
) -> ResearchTrialRecord:
    return ResearchTrialRecord(
        trial_id=started.trial_id,
        domain=started.domain,
        strategy_profile=started.strategy_profile,
        status=status,
        candidate_config_id=started.candidate_config_id,
        actual_params=actual_params,
        param_set_id=started.param_set_id,
        source_revision=started.source_revision,
        input_id=started.input_id,
        window_start=started.window_start,
        window_end=started.window_end,
        calendar_id=started.calendar_id,
        periods_per_year=started.periods_per_year,
        cost_source=started.cost_source,
        cost_inputs=dict(started.cost_inputs),
        reason_code=reason_code,
        synthetic=started.synthetic,
        run_id=run_id,
        param_version=param_version,
        research_identity=started.research_identity,
    )


def _marks(point: DailyReplayPoint) -> tuple[ResearchPositionMark, ...]:
    values = dict(point.market_values)
    marks: list[ResearchPositionMark] = []
    for symbol, quantity in point.holdings:
        valuation = values[symbol]
        if quantity == 0.0 and valuation == 0.0:
            continue
        marks.append(ResearchPositionMark(symbol=symbol, quantity=quantity, valuation=valuation))
    return tuple(marks) + point.option_positions


def _research_ledger(replay: OptimizedStrategyReplay, started: ResearchTrialRecord) -> ResearchDailyLedger:
    points = replay.points
    if len(points) < 2 or points[0].daily_return is not None or points[0].trade_net_cashflow != 0.0:
        _fail("CASH_INVALID")
    days: list[ResearchLedgerDay] = []
    for point in points[1:]:
        if point.daily_return is None:
            _fail("CASH_INVALID")
        days.append(
            ResearchLedgerDay(
                session_date=point.session,
                cash=point.cash,
                positions=_marks(point),
                trade_net_cashflow=point.trade_net_cashflow,
                income_cashflow=point.income_cashflow,
                external_cashflow=point.external_cashflow,
                dividend_receivable=point.dividend_receivable,
                declared_event_ids=point.declared_event_ids,
                events=point.events,
                fees=point.fees,
                nav=point.nav,
                daily_return=point.daily_return,
                option_settlement_cashflow=point.option_settlement_cashflow,
                restricted_cash=point.restricted_cash,
                equity_trade_cashflow=point.equity_trade_cashflow,
                equity_trade_quantities=point.equity_trade_quantities,
                equity_trade_phase=point.equity_trade_phase,
            )
        )
    backtest = replay.backtest
    return ResearchDailyLedger(
        trial_id=started.trial_id,
        domain=started.domain,
        strategy_profile=started.strategy_profile,
        run_id=str(backtest.run_id),
        param_version=backtest.param_version,
        input_id=started.input_id,
        calendar_id=str(backtest.calendar_id),
        periods_per_year=float(backtest.periods_per_year),
        cost_source=backtest.cost_model,
        cost_inputs=dict(backtest.cost_inputs),
        initial_session_date=points[0].session,
        initial_nav=points[0].nav,
        initial_cash=points[0].cash,
        initial_positions=_marks(points[0]),
        days=tuple(days),
        synthetic=True,
        initial_restricted_cash=points[0].restricted_cash,
    )


def _executed_params(replay: OptimizedStrategyReplay) -> dict[str, Any]:
    raw = dict(replay.backtest.params)
    try:
        parsed = json.loads(json.dumps(raw, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError):
        _fail("NONSERIALIZABLE_CONFIG")
    if parsed != raw:
        _fail("NONSERIALIZABLE_CONFIG")
    parsed.pop("research_identity", None)
    return parsed


def persist_optimized_strategy_trial(
    request: ReplayRequest,
    store: PerformanceStore,
    *,
    trial_id: str,
) -> ResearchTrialRecord:
    """Save one fixture trial. Rejection stores only a terminal reason code."""

    if not isinstance(store, PerformanceStore):
        _fail("MISSING_FIELD:performance_store")
    started_record = _started_trial(request, trial_id)
    store.save_research_trial(started_record)
    try:
        replay = replay_optimized_strategy(request)
    except OptimizedStrategyReplayError as exc:
        status = ResearchTrialStatus.FAILED if exc.code == "DECISION_INVALID" else ResearchTrialStatus.REJECTED
        terminal = _terminal(started_record, status, _safe_reason(exc.code))
        store.save_research_trial(terminal)
        return terminal
    except Exception:
        store.save_research_trial(_terminal(started_record, ResearchTrialStatus.FAILED, "replay_failed"))
        raise
    try:
        ledger = _research_ledger(replay, started_record)
        terminal = _terminal(
            started_record,
            ResearchTrialStatus.SUCCEEDED,
            "",
            actual_params=_executed_params(replay),
            run_id=str(replay.backtest.run_id),
            param_version=replay.backtest.param_version,
        )
    except (OptimizedStrategyReplayError, ValueError):
        store.save_research_trial(_terminal(started_record, ResearchTrialStatus.FAILED, "ledger_rejected"))
        raise
    store.save_backtest_result(replay.backtest)
    store.save_research_ledger(ledger)
    store.save_research_trial(terminal)
    return terminal


__all__ = [
    "REPLAY_GAPS",
    "DailyReplayPoint",
    "DatedBar",
    "ExecutionAssumptions",
    "OptimizedStrategyReplay",
    "OptimizedStrategyReplayError",
    "ReplayIdentity",
    "ReplayRequest",
    "persist_optimized_strategy_trial",
    "replay_optimized_strategy",
]
