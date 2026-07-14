from datetime import date

import pytest

from us_equity_strategies.backtest.session_asof import (
    SessionClose,
    SessionContractError,
    build_close_snapshot,
    derive_window_metadata,
)
from quant_platform_kit.common.models import PortfolioSnapshot, Position


def session(day: str, close: str = "20:00:00.000000Z") -> SessionClose:
    return SessionClose(date.fromisoformat(day), f"{day}T{close}")


def test_session_close_preserves_us_session_date_and_snapshot_instant():
    item = session("2024-07-05", "20:00:00.000000Z")
    existing = PortfolioSnapshot(as_of=item.close_datetime, total_equity=123, buying_power=77, cash_balance=55, positions=(Position("TQQQ", 2, 200),), metadata={"existing": "yes"})
    snapshot = build_close_snapshot(item, existing, contract_metadata={"contract": "v1"})
    assert snapshot.as_of == item.close_datetime
    assert snapshot.total_equity == existing.total_equity
    assert snapshot.buying_power == existing.buying_power
    assert snapshot.positions == existing.positions
    assert snapshot.metadata == {"existing": "yes", "trading_date": "2024-07-05", "contract": "v1"}


def test_dst_close_instant_maps_to_same_new_york_date():
    assert session("2024-01-02", "21:00:00.000000Z").trading_date == date(2024, 1, 2)
    assert session("2024-07-02", "20:00:00.000000Z").trading_date == date(2024, 7, 2)


@pytest.mark.parametrize("value", ["2024-07-05T16:00:00.000000-05:00", "2024-07-05T21:00:00Z", "2024-07-05T20:30:00.000000Z", "2024-07-05T21:00:00.00000Z", "2024-07-05T21:00:00.000000Z "])
def test_close_timestamp_requires_canonical_utc(value):
    with pytest.raises(SessionContractError):
        SessionClose(date(2024, 7, 5), value)


def test_invalid_calendar_or_session_date_mismatch_fails_closed():
    with pytest.raises(SessionContractError):
        SessionClose("2024-02-30", "2024-02-30T21:00:00.000000Z")
    with pytest.raises(SessionContractError):
        SessionClose(date(2024, 7, 5), "2024-07-06T20:00:00.000000Z")


def test_requested_and_observed_window_metadata_are_separate():
    sessions = (session("2024-07-01"), session("2024-07-02"), session("2024-07-03"))
    metadata = derive_window_metadata(sessions, requested_start_date="2024-07-01", requested_end_date="2024-07-03")
    assert metadata.requested_end_date == date(2024, 7, 3)
    assert metadata.observed_end_date == metadata.as_of == date(2024, 7, 3)


def test_missing_requested_end_rejected_by_default_and_explicitly_allowed():
    sessions = (session("2024-07-01"), session("2024-07-02"))
    with pytest.raises(SessionContractError):
        derive_window_metadata(sessions, requested_start_date="2024-07-01", requested_end_date="2024-07-03")
    metadata = derive_window_metadata(sessions, requested_start_date="2024-07-01", requested_end_date="2024-07-03", require_end_observation=False)
    assert metadata.requested_end_date == date(2024, 7, 3)
    assert metadata.as_of == date(2024, 7, 2)


def test_unsorted_or_duplicate_sessions_fail_closed():
    with pytest.raises(SessionContractError):
        derive_window_metadata((session("2024-07-02"), session("2024-07-01")), requested_start_date="2024-07-01", requested_end_date="2024-07-02")
    with pytest.raises(SessionContractError):
        derive_window_metadata((session("2024-07-01"), session("2024-07-01")), requested_start_date="2024-07-01", requested_end_date="2024-07-01")
