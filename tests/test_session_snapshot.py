from datetime import date

import pytest

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from us_equity_strategies.backtest.session_asof_contract import RequestedObservedWindow, SessionClose, SessionContractError
from us_equity_strategies.backtest.session_snapshot import SessionBoundSnapshot, SnapshotPosition


def test_snapshot_consumer_preserves_financial_fields_and_ignores_metadata():
    session = SessionClose(date(2024, 7, 2), "2024-07-02T20:00:00.000000Z")
    window = RequestedObservedWindow.from_sessions((session,), requested_start_date="2024-07-02", requested_end_date="2024-07-02")
    position = Position("TQQQ", 2.5, 500.0)
    source = PortfolioSnapshot(session.close_datetime, 1234.5, 900.0, 800.0, (position,), {"trading_date": "forged", "nested": {"x": 1}})
    result = SessionBoundSnapshot.from_snapshot(session, window, source)
    assert result.total_equity == 1234.5
    assert result.buying_power == 900.0 and result.cash_balance == 800.0
    assert result.positions == (SnapshotPosition("TQQQ", 2.5, 500.0, None, "USD", None),)
    assert "nested" not in result.to_wire()


def test_reserved_session_window_and_as_of_cannot_be_overridden():
    session = SessionClose(date(2024, 7, 2), "2024-07-02T20:00:00.000000Z")
    other = SessionClose(date(2024, 7, 3), "2024-07-03T20:00:00.000000Z")
    window = RequestedObservedWindow.from_sessions((session,), requested_start_date="2024-07-02", requested_end_date="2024-07-02")
    with pytest.raises(SessionContractError):
        SessionBoundSnapshot(other, window, 1, None, None, ())
    wire = SessionBoundSnapshot.from_snapshot(session, window, PortfolioSnapshot(session.close_datetime, 1)).to_wire()
    wire["as_of"] = "2024-07-03"
    with pytest.raises(SessionContractError):
        SessionBoundSnapshot.from_wire(wire)


def test_wire_round_trip_is_deterministic_and_mutation_isolated():
    session = SessionClose(date(2024, 7, 2), "2024-07-02T20:00:00.000000Z")
    window = RequestedObservedWindow.from_sessions((session,), requested_start_date="2024-07-02", requested_end_date="2024-07-02")
    source_positions = [Position("TQQQ", 1, 100)]
    source = PortfolioSnapshot(session.close_datetime, 1000.0, positions=source_positions)
    result = SessionBoundSnapshot.from_snapshot(session, window, source)
    source_positions[0] = Position("TQQQ", 99, 9900)
    wire = result.to_wire()
    assert SessionBoundSnapshot.from_wire(wire).canonical_bytes() == result.canonical_bytes()
    wire["positions"][0]["quantity"] = 999
    assert result.positions[0].quantity == 1
    with pytest.raises((AttributeError, TypeError)):
        result.positions[0].quantity = 999


def test_wrong_types_fail_closed_without_raw_attribute_errors():
    session = SessionClose(date(2024, 7, 2), "2024-07-02T20:00:00.000000Z")
    window = RequestedObservedWindow.from_sessions((session,), requested_start_date="2024-07-02", requested_end_date="2024-07-02")
    with pytest.raises(SessionContractError):
        SessionBoundSnapshot("bad", window, 1, None, None, ())
    with pytest.raises(SessionContractError):
        SessionBoundSnapshot(session, "bad", 1, None, None, ())
    with pytest.raises(SessionContractError):
        SessionBoundSnapshot.from_snapshot(session, window, PortfolioSnapshot(session.close_datetime, 1, positions=("bad",)))


@pytest.mark.parametrize("bad", [{"positions": {}}, {"positions": [{"symbol": "TQQQ"}]}])
def test_malformed_wire_fails_closed(bad):
    session = SessionClose(date(2024, 7, 2), "2024-07-02T20:00:00.000000Z")
    window = RequestedObservedWindow.from_sessions((session,), requested_start_date="2024-07-02", requested_end_date="2024-07-02")
    payload = SessionBoundSnapshot.from_snapshot(session, window, PortfolioSnapshot(session.close_datetime, 1)).to_wire()
    payload.update(bad)
    with pytest.raises(SessionContractError):
        SessionBoundSnapshot.from_wire(payload)
