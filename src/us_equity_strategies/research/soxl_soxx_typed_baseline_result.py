"""Deterministic controls-disabled SOXL parity baseline over typed offline input."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import NoReturn

from .soxl_soxx_offline_input_contract import InputRow, OfflineInput

PROFILE = "soxl_soxx_trend_income_parity_baseline_v1"
VERSION = "qsl.research.soxl_soxx_typed_baseline_result.v1"
SIGNAL_TIMING = "SOXX_SMA200_INCLUSIVE_CLOSE_NEXT_SOXL_OPEN_V1"
SMA_WINDOW = 200
INITIAL_EQUITY = 100_000.0
TRANSACTION_COST_RATE = 0.0


class BaselineResultContractError(ValueError):
    """Sanitized typed baseline boundary error."""


def _fail() -> NoReturn:
    raise BaselineResultContractError("invalid typed baseline") from None


def _date_text(value: object) -> str:
    if type(value) is not str:
        _fail()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail()
    if parsed.isoformat() != value:
        _fail()
    return value


def _positive(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        _fail()
    return value


def _nonnegative(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        _fail()
    return 0.0 if value == 0.0 else value


def _digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail()
    return value


@dataclass(frozen=True, slots=True)
class EquityPoint:
    date: str
    equity: float
    cash: float
    soxl_quantity: float
    soxl_close: float

    def __post_init__(self) -> None:
        _date_text(self.date)
        equity = _positive(self.equity)
        cash = _nonnegative(self.cash)
        quantity = _nonnegative(self.soxl_quantity)
        close = _positive(self.soxl_close)
        if not math.isclose(equity, cash + quantity * close, rel_tol=1e-12, abs_tol=1e-8):
            _fail()


@dataclass(frozen=True, slots=True)
class ReturnPoint:
    date: str
    daily_return: float

    def __post_init__(self) -> None:
        _date_text(self.date)
        if type(self.daily_return) is not float or not math.isfinite(self.daily_return):
            _fail()


@dataclass(frozen=True, slots=True)
class BaselineResult:
    input_digest: str
    equity_curve: tuple[EquityPoint, ...]

    def __post_init__(self) -> None:
        _digest(self.input_digest)
        if (
            type(self.equity_curve) is not tuple
            or not self.equity_curve
            or any(type(point) is not EquityPoint for point in self.equity_curve)
            or tuple(point.date for point in self.equity_curve) != tuple(sorted(point.date for point in self.equity_curve))
            or len({point.date for point in self.equity_curve}) != len(self.equity_curve)
        ):
            _fail()

    @property
    def profile(self) -> str:
        return PROFILE

    @property
    def version(self) -> str:
        return VERSION

    @property
    def signal_timing(self) -> str:
        return SIGNAL_TIMING

    @property
    def controls_disabled(self) -> bool:
        return True

    @property
    def transaction_cost_rate(self) -> float:
        return TRANSACTION_COST_RATE

    @property
    def evaluation_count(self) -> int:
        return len(self.equity_curve)

    @property
    def daily_returns(self) -> tuple[ReturnPoint, ...]:
        previous_equity = INITIAL_EQUITY
        returns: list[ReturnPoint] = []
        for point in self.equity_curve:
            returns.append(ReturnPoint(point.date, point.equity / previous_equity - 1.0))
            previous_equity = point.equity
        return tuple(returns)

    @property
    def trade_count(self) -> int:
        previous_exposure = False
        transitions = 0
        for point in self.equity_curve:
            exposure = point.soxl_quantity > 0.0
            transitions += int(exposure != previous_exposure)
            previous_exposure = exposure
        return transitions


def _typed_rows(source: object) -> tuple[tuple[InputRow, ...], tuple[InputRow, ...]]:
    if type(source) is not OfflineInput:
        _fail()
    if (
        type(source.rows) is not tuple
        or type(source.canonical_bytes) is not bytes
        or not source.canonical_bytes
        or type(source.source_revision) is not str
        or not source.source_revision
        or any(ord(character) < 0x20 for character in source.source_revision)
    ):
        _fail()
    _digest(source.input_digest)
    if not source.rows or any(type(row) is not InputRow for row in source.rows):
        _fail()
    keys: list[tuple[str, str]] = []
    for row in source.rows:
        if row.symbol not in ("SOXX", "SOXL"):
            _fail()
        keys.append((_date_text(row.as_of), row.symbol))
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        _fail()
    soxx_rows = tuple(row for row in source.rows if row.symbol == "SOXX")
    soxl_rows = tuple(row for row in source.rows if row.symbol == "SOXL")
    if (
        len(soxx_rows) <= SMA_WINDOW
        or len(soxx_rows) != len(soxl_rows)
        or tuple(row.as_of for row in soxx_rows) != tuple(row.as_of for row in soxl_rows)
    ):
        _fail()
    for row in soxx_rows:
        _positive(row.close)
    for row in soxl_rows:
        _positive(row.open)
        _positive(row.close)
    return soxx_rows, soxl_rows


def run_typed_baseline(source: OfflineInput) -> BaselineResult:
    """Evaluate the fixed SOXX signal/SOXL next-open parity baseline."""
    soxx_rows, soxl_rows = _typed_rows(source)
    cash = INITIAL_EQUITY
    quantity = 0.0
    curve: list[EquityPoint] = []
    for signal_index in range(SMA_WINDOW - 1, len(soxx_rows) - 1):
        window = soxx_rows[signal_index - SMA_WINDOW + 1 : signal_index + 1]
        risk_on = soxx_rows[signal_index].close >= math.fsum(row.close for row in window) / SMA_WINDOW
        execution = soxl_rows[signal_index + 1]
        opening_equity = cash + quantity * execution.open
        _positive(opening_equity)
        if risk_on and quantity == 0.0:
            quantity = opening_equity / execution.open
            cash = 0.0
        elif not risk_on and quantity > 0.0:
            cash = opening_equity
            quantity = 0.0
        equity = cash + quantity * execution.close
        curve.append(
            EquityPoint(
                date=execution.as_of,
                equity=float(equity),
                cash=float(cash),
                soxl_quantity=float(quantity),
                soxl_close=execution.close,
            )
        )
    return BaselineResult(input_digest=source.input_digest, equity_curve=tuple(curve))
