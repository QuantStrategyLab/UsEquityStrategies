"""Explicit US trading-session and requested/observed-window contract."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from quant_platform_kit.common.models import PortfolioSnapshot, Position

UTC = timezone.utc
US_MARKET_ZONE = ZoneInfo("America/New_York")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


class SessionContractError(ValueError):
    """Sanitized session/window contract error."""


def _as_date(value: date | str, field: str) -> date:
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


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise SessionContractError("invalid close_at_utc")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        raise SessionContractError("invalid close_at_utc") from None
    return parsed


@dataclass(frozen=True)
class SessionClose:
    """Caller-supplied market close; no calendar inference is performed."""

    trading_date: date
    close_at_utc: str

    def __post_init__(self) -> None:
        trading_date = _as_date(self.trading_date, "trading_date")
        parsed = _parse_utc(self.close_at_utc)
        local = parsed.astimezone(US_MARKET_ZONE)
        if local.date() != trading_date or (local.hour, local.minute) not in {(16, 0), (13, 0)} or local.second or local.microsecond:
            raise SessionContractError("session date mismatch")
        object.__setattr__(self, "trading_date", trading_date)

    @property
    def close_datetime(self) -> datetime:
        return _parse_utc(self.close_at_utc)


@dataclass(frozen=True)
class WindowMetadata:
    requested_start_date: date
    requested_end_date: date
    observed_start_date: date
    observed_end_date: date
    as_of: date


def derive_window_metadata(sessions: tuple[SessionClose, ...], *, requested_start_date: date | str, requested_end_date: date | str, require_end_observation: bool = True) -> WindowMetadata:
    start = _as_date(requested_start_date, "requested_start_date")
    end = _as_date(requested_end_date, "requested_end_date")
    if start > end or not isinstance(require_end_observation, bool):
        raise SessionContractError("invalid requested window")
    if not sessions or tuple(sorted(sessions, key=lambda item: item.trading_date)) != sessions:
        raise SessionContractError("sessions must be sorted")
    if len({item.trading_date for item in sessions}) != len(sessions):
        raise SessionContractError("duplicate session")
    observed = tuple(item for item in sessions if start <= item.trading_date <= end)
    if not observed or (require_end_observation and observed[-1].trading_date != end):
        raise SessionContractError("requested end not observed")
    return WindowMetadata(start, end, observed[0].trading_date, observed[-1].trading_date, observed[-1].trading_date)


def build_close_snapshot(session: SessionClose, existing_snapshot: PortfolioSnapshot, *, contract_metadata: dict[str, object] | None = None) -> PortfolioSnapshot:
    """Update only session timestamp and merged contract metadata."""
    if not isinstance(existing_snapshot, PortfolioSnapshot):
        raise SessionContractError("invalid existing snapshot")
    metadata = dict(existing_snapshot.metadata or {})
    metadata["trading_date"] = session.trading_date.isoformat()
    if contract_metadata:
        metadata.update(contract_metadata)
    return replace(existing_snapshot, as_of=session.close_datetime, metadata=metadata)
