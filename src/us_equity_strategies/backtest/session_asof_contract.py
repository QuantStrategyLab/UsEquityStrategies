"""Pure immutable session/as-of contract; no portfolio or metadata integration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

UTC = timezone.utc
US_MARKET_ZONE = ZoneInfo("America/New_York")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_SESSION_KEYS = frozenset({"trading_date", "close_at_utc"})
_WINDOW_KEYS = frozenset({"requested_start_date", "requested_end_date", "observed_start_date", "observed_end_date", "as_of"})


class SessionContractError(ValueError):
    """Sanitized contract/parser error."""


def _date(value: date | str, field: str) -> date:
    if isinstance(value, datetime):
        raise SessionContractError(f"invalid {field}")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise SessionContractError(f"invalid {field}")


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise SessionContractError("invalid close_at_utc")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        raise SessionContractError("invalid close_at_utc") from None


def _check_keys(payload: Mapping[str, object], expected: frozenset[str]) -> None:
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise SessionContractError("invalid wire shape")


@dataclass(frozen=True)
class SessionClose:
    trading_date: date
    close_at_utc: str

    def __post_init__(self) -> None:
        trading_date = _date(self.trading_date, "trading_date")
        parsed = _utc(self.close_at_utc)
        local = parsed.astimezone(US_MARKET_ZONE)
        if local.date() != trading_date or (local.hour, local.minute) not in {(16, 0), (13, 0)} or local.second or local.microsecond:
            raise SessionContractError("invalid session close")
        object.__setattr__(self, "trading_date", trading_date)

    @property
    def close_datetime(self) -> datetime:
        return _utc(self.close_at_utc)

    def to_wire(self) -> dict[str, str]:
        return {"close_at_utc": self.close_at_utc, "trading_date": self.trading_date.isoformat()}

    @classmethod
    def from_wire(cls, payload: Mapping[str, object]) -> "SessionClose":
        _check_keys(payload, _SESSION_KEYS)
        if not all(isinstance(payload[key], str) for key in _SESSION_KEYS):
            raise SessionContractError("invalid wire value")
        return cls(payload["trading_date"], payload["close_at_utc"])


@dataclass(frozen=True)
class RequestedObservedWindow:
    requested_start_date: date
    requested_end_date: date
    observed_start_date: date
    observed_end_date: date
    as_of: date

    def __post_init__(self) -> None:
        values = {field: _date(getattr(self, field), field) for field in _WINDOW_KEYS}
        if values["requested_start_date"] > values["requested_end_date"] or values["observed_start_date"] > values["observed_end_date"]:
            raise SessionContractError("invalid window")
        if not (values["requested_start_date"] <= values["observed_start_date"] <= values["observed_end_date"] <= values["requested_end_date"]):
            raise SessionContractError("invalid observed window")
        if values["as_of"] != values["observed_end_date"]:
            raise SessionContractError("invalid as_of")
        for field, value in values.items():
            object.__setattr__(self, field, value)

    @classmethod
    def from_sessions(cls, sessions: tuple[SessionClose, ...], *, requested_start_date: date | str, requested_end_date: date | str, require_end_observation: bool = True) -> "RequestedObservedWindow":
        start, end = _date(requested_start_date, "requested_start_date"), _date(requested_end_date, "requested_end_date")
        if not isinstance(require_end_observation, bool) or start > end or not sessions or tuple(sorted(sessions, key=lambda item: item.trading_date)) != sessions:
            raise SessionContractError("invalid sessions/window")
        dates = tuple(item.trading_date for item in sessions)
        if len(set(dates)) != len(dates):
            raise SessionContractError("duplicate session")
        observed = tuple(item for item in sessions if start <= item.trading_date <= end)
        if not observed or (require_end_observation and observed[-1].trading_date != end):
            raise SessionContractError("requested end not observed")
        return cls(start, end, observed[0].trading_date, observed[-1].trading_date, observed[-1].trading_date)

    def to_wire(self) -> dict[str, str]:
        return {field: getattr(self, field).isoformat() for field in sorted(_WINDOW_KEYS)}

    @classmethod
    def from_wire(cls, payload: Mapping[str, object]) -> "RequestedObservedWindow":
        _check_keys(payload, _WINDOW_KEYS)
        if not all(isinstance(payload[key], str) for key in _WINDOW_KEYS):
            raise SessionContractError("invalid wire value")
        return cls(**{field: payload[field] for field in _WINDOW_KEYS})


def canonical_bytes(value: Mapping[str, object]) -> bytes:
    """Canonical JSON for the two exact contract wire shapes."""
    if set(value) == _SESSION_KEYS:
        normalized = SessionClose.from_wire(value).to_wire()
    elif set(value) == _WINDOW_KEYS:
        normalized = RequestedObservedWindow.from_wire(value).to_wire()
    else:
        raise SessionContractError("invalid wire shape")
    try:
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (UnicodeEncodeError, TypeError, ValueError):
        raise SessionContractError("invalid canonical wire") from None
