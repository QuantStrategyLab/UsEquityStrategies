from datetime import date, datetime, timedelta, timezone

import pytest

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from us_equity_strategies.backtest.session_asof_contract import RequestedObservedWindow, SessionClose, SessionContractError
from us_equity_strategies.backtest.snapshot_numeric_contract import MAX_SAFE_JSON_NUMBER, ValidatedSessionSnapshot


def fixture_snapshot():
    session = SessionClose(date(2024, 7, 2), "2024-07-02T20:00:00.000000Z")
    window = RequestedObservedWindow.from_sessions((session,), requested_start_date="2024-07-02", requested_end_date="2024-07-02")
    source = PortfolioSnapshot(session.close_datetime, 1000.0, 900.0, 800.0, [Position("TQQQ", 1.5, 100.0)])
    return session, window, source


def test_roundtrip_canonical_and_source_mutation_isolation():
    session, window, source = fixture_snapshot()
    result = ValidatedSessionSnapshot.from_snapshot(session, window, source)
    wire = result.to_wire()
    assert ValidatedSessionSnapshot.from_wire(wire).canonical_bytes() == result.canonical_bytes()
    source.positions[0] = Position("TQQQ", 9, 900)
    wire["positions"][0]["quantity"] = 9
    assert result.positions[0].quantity == 1.5


@pytest.mark.parametrize("value", ["1000", True, None, float("nan"), float("inf"), -0.0, MAX_SAFE_JSON_NUMBER + 1])
def test_numeric_strings_bool_null_nonfinite_negative_zero_and_unsafe_rejected(value):
    session, window, _ = fixture_snapshot()
    payload = ValidatedSessionSnapshot.from_snapshot(session, window, PortfolioSnapshot(session.close_datetime, 1000.0)).to_wire()
    payload["total_equity"] = value
    with pytest.raises(SessionContractError):
        ValidatedSessionSnapshot.from_wire(payload)


def test_boundary_numeric_accepted_and_optional_null_preserved():
    session, window, _ = fixture_snapshot()
    payload = ValidatedSessionSnapshot.from_snapshot(session, window, PortfolioSnapshot(session.close_datetime, float(MAX_SAFE_JSON_NUMBER))).to_wire()
    payload["buying_power"] = None
    payload["cash_balance"] = None
    result = ValidatedSessionSnapshot.from_wire(payload)
    assert result.total_equity == float(MAX_SAFE_JSON_NUMBER)
    assert result.buying_power is None and result.cash_balance is None


def test_invalid_types_and_reserved_consistency_fail_closed():
    session, window, source = fixture_snapshot()
    with pytest.raises(SessionContractError):
        ValidatedSessionSnapshot.from_snapshot("bad", window, source)
    with pytest.raises(SessionContractError):
        ValidatedSessionSnapshot.from_snapshot(session, "bad", source)
    payload = ValidatedSessionSnapshot.from_snapshot(session, window, source).to_wire()
    payload["as_of"] = "2024-07-03"
    with pytest.raises(SessionContractError):
        ValidatedSessionSnapshot.from_wire(payload)


@pytest.mark.parametrize("as_of", [
    datetime(2024, 7, 2, 19, 59, tzinfo=timezone.utc),
    datetime(2024, 7, 2, 20, 1, tzinfo=timezone.utc),
    datetime(2024, 7, 2, 20, tzinfo=timezone(timedelta(hours=-4))),
    datetime(2024, 7, 2, 20),
    None,
])
def test_source_as_of_must_be_exact_canonical_session_instant(as_of):
    session, window, source = fixture_snapshot()
    invalid = PortfolioSnapshot(as_of, source.total_equity, source.buying_power, source.cash_balance, source.positions)
    with pytest.raises(SessionContractError):
        ValidatedSessionSnapshot.from_snapshot(session, window, invalid)
