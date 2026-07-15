"""Closed, immutable consumer of the pure session/as-of contracts."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from quant_platform_kit.common.models import PortfolioSnapshot, Position

from .session_asof_contract import (
    RequestedObservedWindow,
    SessionClose,
    SessionContractError,
)

_KEYS = frozenset({"as_of", "cash_balance", "buying_power", "positions", "session", "total_equity", "window"})
_POSITION_KEYS = frozenset({"account_id", "average_cost", "currency", "market_value", "quantity", "symbol"})


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise SessionContractError(f"invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise SessionContractError(f"invalid {field}") from None
    if not math.isfinite(result):
        raise SessionContractError(f"invalid {field}")
    return result


def _optional_finite(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _finite(value, field)


def _text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SessionContractError(f"invalid {field}")
    return value


@dataclass(frozen=True)
class SessionBoundSnapshot:
    """Typed snapshot with no user metadata surface."""

    session: SessionClose
    window: RequestedObservedWindow
    total_equity: float
    buying_power: float | None
    cash_balance: float | None
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        if self.window.as_of != self.session.trading_date:
            raise SessionContractError("session/window mismatch")
        object.__setattr__(self, "total_equity", _finite(self.total_equity, "total_equity"))
        object.__setattr__(self, "buying_power", _optional_finite(self.buying_power, "buying_power"))
        object.__setattr__(self, "cash_balance", _optional_finite(self.cash_balance, "cash_balance"))
        if not isinstance(self.positions, tuple) or not all(isinstance(item, Position) for item in self.positions):
            raise SessionContractError("invalid positions")
        normalized = tuple(Position(
            symbol=_text(item.symbol, "symbol"), quantity=_finite(item.quantity, "quantity"),
            market_value=_finite(item.market_value, "market_value"), average_cost=_optional_finite(item.average_cost, "average_cost"),
            currency=_text(item.currency, "currency"), account_id=_text(item.account_id, "account_id", nullable=True),
        ) for item in self.positions)
        object.__setattr__(self, "positions", normalized)

    @classmethod
    def from_snapshot(cls, session: SessionClose, window: RequestedObservedWindow, snapshot: PortfolioSnapshot) -> "SessionBoundSnapshot":
        if not isinstance(snapshot, PortfolioSnapshot):
            raise SessionContractError("invalid snapshot")
        return cls(session, window, snapshot.total_equity, snapshot.buying_power, snapshot.cash_balance, tuple(snapshot.positions or ()))

    def to_wire(self) -> dict[str, object]:
        return {
            "as_of": self.window.as_of.isoformat(),
            "cash_balance": self.cash_balance,
            "buying_power": self.buying_power,
            "positions": [
                {"account_id": item.account_id, "average_cost": item.average_cost, "currency": item.currency,
                 "market_value": item.market_value, "quantity": item.quantity, "symbol": item.symbol}
                for item in self.positions
            ],
            "session": self.session.to_wire(),
            "total_equity": self.total_equity,
            "window": self.window.to_wire(),
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, object]) -> "SessionBoundSnapshot":
        if not isinstance(payload, Mapping) or set(payload) != _KEYS:
            raise SessionContractError("invalid snapshot wire shape")
        session = SessionClose.from_wire(payload["session"])
        window = RequestedObservedWindow.from_wire(payload["window"])
        positions_payload = payload["positions"]
        if not isinstance(positions_payload, list):
            raise SessionContractError("invalid positions")
        positions = []
        for item in positions_payload:
            if not isinstance(item, Mapping) or set(item) != _POSITION_KEYS:
                raise SessionContractError("invalid position wire shape")
            positions.append(Position(
                symbol=_text(item["symbol"], "symbol"), quantity=_finite(item["quantity"], "quantity"),
                market_value=_finite(item["market_value"], "market_value"), average_cost=_optional_finite(item["average_cost"], "average_cost"),
                currency=_text(item["currency"], "currency"), account_id=_text(item["account_id"], "account_id", nullable=True),
            ))
        if payload["as_of"] != window.as_of.isoformat():
            raise SessionContractError("as_of mismatch")
        return cls(session, window, _finite(payload["total_equity"], "total_equity"), _optional_finite(payload["buying_power"], "buying_power"), _optional_finite(payload["cash_balance"], "cash_balance"), tuple(positions))

    def canonical_bytes(self) -> bytes:
        try:
            return json.dumps(self.to_wire(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (UnicodeEncodeError, TypeError, ValueError):
            raise SessionContractError("invalid snapshot canonical wire") from None
