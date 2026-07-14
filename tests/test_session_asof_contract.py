from datetime import date

import pytest

from us_equity_strategies.backtest.session_asof_contract import (
    RequestedObservedWindow,
    SessionClose,
    SessionContractError,
    canonical_bytes,
)


def session(day: str, utc: str = "20:00:00.000000Z") -> SessionClose:
    return SessionClose(date.fromisoformat(day), f"{day}T{utc}")


def test_regular_half_day_dst_and_round_trip_are_exact():
    regular = session("2024-07-02")
    winter = session("2024-01-02", "21:00:00.000000Z")
    half_day = session("2024-07-03", "17:00:00.000000Z")
    for item in (regular, winter, half_day):
        assert SessionClose.from_wire(item.to_wire()) == item
        assert canonical_bytes(item.to_wire()) == canonical_bytes(dict(reversed(tuple(item.to_wire().items()))))


@pytest.mark.parametrize("value", ["2024-07-02T19:59:00.000000Z", "2024-07-02T20:01:00.000000Z", "2024-07-02T20:00:01.000000Z", "2024-07-02T20:00:00.000001Z", "2024-07-02T20:00:00.000000+00:00", "2024-07-02T20:00:00Z"])
def test_non_close_timestamp_rejected(value):
    with pytest.raises(SessionContractError):
        SessionClose(date(2024, 7, 2), value)


def test_session_wire_exact_shape_and_no_alias():
    item = session("2024-07-02")
    with pytest.raises(SessionContractError):
        SessionClose.from_wire({**item.to_wire(), "extra": "x"})
    with pytest.raises(SessionContractError):
        SessionClose.from_wire({"trading_date": item.to_wire()["trading_date"], "close_at_utc": "bad"})


def test_window_as_of_is_last_observed_and_end_policy_is_explicit():
    sessions = (session("2024-07-01"), session("2024-07-02"))
    with pytest.raises(SessionContractError):
        RequestedObservedWindow.from_sessions(sessions, requested_start_date="2024-07-01", requested_end_date="2024-07-03")
    window = RequestedObservedWindow.from_sessions(sessions, requested_start_date="2024-07-01", requested_end_date="2024-07-03", require_end_observation=False)
    assert window.as_of == date(2024, 7, 2)
    assert RequestedObservedWindow.from_wire(window.to_wire()) == window


def test_window_order_duplicate_and_forged_as_of_rejected():
    with pytest.raises(SessionContractError):
        RequestedObservedWindow.from_sessions((session("2024-07-02"), session("2024-07-01")), requested_start_date="2024-07-01", requested_end_date="2024-07-02")
    with pytest.raises(SessionContractError):
        RequestedObservedWindow.from_wire({"requested_start_date": "2024-07-01", "requested_end_date": "2024-07-02", "observed_start_date": "2024-07-01", "observed_end_date": "2024-07-02", "as_of": "2024-07-01"})

