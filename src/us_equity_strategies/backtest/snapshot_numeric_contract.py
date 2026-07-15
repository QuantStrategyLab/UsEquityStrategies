"""Strict numeric, immutable session-bound snapshot consumer."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from quant_platform_kit.common.models import PortfolioSnapshot, Position

from .session_asof_contract import RequestedObservedWindow, SessionClose, SessionContractError

MAX_SAFE_JSON_NUMBER = 2**53 - 1
_KEYS = frozenset({"as_of", "cash_balance", "buying_power", "positions", "session", "total_equity", "window"})
_POSITION_KEYS = frozenset({"account_id", "average_cost", "currency", "market_value", "quantity", "symbol"})


def _number(value: Any, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SessionContractError(f"invalid {field}")
    if isinstance(value, int) and abs(value) > MAX_SAFE_JSON_NUMBER:
        raise SessionContractError(f"invalid {field}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise SessionContractError(f"invalid {field}") from None
    if not math.isfinite(result) or abs(result) > MAX_SAFE_JSON_NUMBER or (result == 0.0 and math.copysign(1.0, result) < 0):
        raise SessionContractError(f"invalid {field}")
    return result


def _text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SessionContractError(f"invalid {field}")
    return value


def _validate_source_as_of(snapshot: PortfolioSnapshot, session: SessionClose) -> None:
    source_as_of = snapshot.as_of
    if not isinstance(source_as_of, datetime) or source_as_of.tzinfo is not timezone.utc or source_as_of != session.close_datetime:
        raise SessionContractError("snapshot/session as_of mismatch")


@dataclass(frozen=True, slots=True)
class ValidatedPosition:
    symbol: str
    quantity: float
    market_value: float
    average_cost: float | None
    currency: str
    account_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol"))
        object.__setattr__(self, "quantity", _number(self.quantity, "quantity"))
        object.__setattr__(self, "market_value", _number(self.market_value, "market_value"))
        object.__setattr__(self, "average_cost", _number(self.average_cost, "average_cost", nullable=True))
        object.__setattr__(self, "currency", _text(self.currency, "currency"))
        object.__setattr__(self, "account_id", _text(self.account_id, "account_id", nullable=True))

    @classmethod
    def from_position(cls, item: Position) -> "ValidatedPosition":
        if not isinstance(item, Position):
            raise SessionContractError("invalid position")
        return cls(item.symbol, item.quantity, item.market_value, item.average_cost, item.currency, item.account_id)


@dataclass(frozen=True, slots=True)
class ValidatedSessionSnapshot:
    session: SessionClose
    window: RequestedObservedWindow
    total_equity: float
    buying_power: float | None
    cash_balance: float | None
    positions: tuple[ValidatedPosition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.session, SessionClose) or not isinstance(self.window, RequestedObservedWindow):
            raise SessionContractError("invalid session/window")
        if self.window.as_of != self.session.trading_date:
            raise SessionContractError("session/window mismatch")
        object.__setattr__(self, "total_equity", _number(self.total_equity, "total_equity"))
        object.__setattr__(self, "buying_power", _number(self.buying_power, "buying_power", nullable=True))
        object.__setattr__(self, "cash_balance", _number(self.cash_balance, "cash_balance", nullable=True))
        if not isinstance(self.positions, tuple) or not all(isinstance(item, ValidatedPosition) for item in self.positions):
            raise SessionContractError("invalid positions")

    @classmethod
    def from_snapshot(cls, session: SessionClose, window: RequestedObservedWindow, snapshot: PortfolioSnapshot) -> "ValidatedSessionSnapshot":
        if not isinstance(session, SessionClose) or not isinstance(window, RequestedObservedWindow):
            raise SessionContractError("invalid session/window")
        if not isinstance(snapshot, PortfolioSnapshot):
            raise SessionContractError("invalid snapshot")
        _validate_source_as_of(snapshot, session)
        if not isinstance(snapshot.positions, (tuple, list)) or not all(isinstance(item, Position) for item in snapshot.positions):
            raise SessionContractError("invalid positions")
        return cls(session, window, _number(snapshot.total_equity, "total_equity"), _number(snapshot.buying_power, "buying_power", nullable=True), _number(snapshot.cash_balance, "cash_balance", nullable=True), tuple(ValidatedPosition.from_position(item) for item in snapshot.positions))

    def to_wire(self) -> dict[str, object]:
        return {"as_of": self.window.as_of.isoformat(), "cash_balance": self.cash_balance, "buying_power": self.buying_power, "positions": [{"account_id": p.account_id, "average_cost": p.average_cost, "currency": p.currency, "market_value": p.market_value, "quantity": p.quantity, "symbol": p.symbol} for p in self.positions], "session": self.session.to_wire(), "total_equity": self.total_equity, "window": self.window.to_wire()}

    @classmethod
    def from_wire(cls, payload: Mapping[str, object]) -> "ValidatedSessionSnapshot":
        if not isinstance(payload, Mapping) or set(payload) != _KEYS:
            raise SessionContractError("invalid snapshot wire shape")
        session = SessionClose.from_wire(payload["session"])
        window = RequestedObservedWindow.from_wire(payload["window"])
        if payload["as_of"] != window.as_of.isoformat() or not isinstance(payload["positions"], list):
            raise SessionContractError("invalid snapshot wire")
        positions = []
        for item in payload["positions"]:
            if not isinstance(item, Mapping) or set(item) != _POSITION_KEYS:
                raise SessionContractError("invalid position wire shape")
            positions.append(ValidatedPosition(item["symbol"], item["quantity"], item["market_value"], item["average_cost"], item["currency"], item["account_id"]))
        return cls(session, window, _number(payload["total_equity"], "total_equity"), _number(payload["buying_power"], "buying_power", nullable=True), _number(payload["cash_balance"], "cash_balance", nullable=True), tuple(positions))

    def canonical_bytes(self) -> bytes:
        try:
            return json.dumps(self.to_wire(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (UnicodeEncodeError, TypeError, ValueError):
            raise SessionContractError("invalid snapshot canonical wire") from None
