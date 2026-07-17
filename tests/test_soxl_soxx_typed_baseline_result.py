from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta

import pytest

from us_equity_strategies.research.soxl_soxx_offline_input_contract import InputRow, OfflineInput
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import (
    BaselineResult,
    BaselineResultContractError,
    run_typed_baseline,
)


def _typed_input(
    soxx_closes: list[float],
    *,
    soxl_opens: list[float] | None = None,
    soxl_closes: list[float] | None = None,
) -> OfflineInput:
    count = len(soxx_closes)
    opens = soxl_opens or [50.0] * count
    closes = soxl_closes or opens
    rows: list[InputRow] = []
    for index, soxx_close in enumerate(soxx_closes):
        as_of = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        soxl_open = opens[index]
        soxl_close = closes[index]
        rows.extend(
            (
                InputRow("SOXL", as_of, soxl_open, max(soxl_open, soxl_close), min(soxl_open, soxl_close), soxl_close, 1.0),
                InputRow("SOXX", as_of, soxx_close, soxx_close, soxx_close, soxx_close, 1.0),
            )
        )
    return OfflineInput(tuple(rows), b"typed-test-fixture", "a" * 64, "typed_test_v1")


def test_inclusive_soxx_sma_executes_at_next_soxl_open() -> None:
    soxx_closes = [200.0] * 200 + [1.0]
    soxl_opens = [50.0] * 201
    soxl_closes = [50.0] * 201
    soxl_opens[199] = 1_000.0
    soxl_opens[200] = 50.0
    soxl_closes[200] = 55.0

    result = run_typed_baseline(_typed_input(soxx_closes, soxl_opens=soxl_opens, soxl_closes=soxl_closes))

    point = result.equity_curve[0]
    assert result.signal_timing == "SOXX_SMA200_INCLUSIVE_CLOSE_NEXT_SOXL_OPEN_V1"
    assert result.controls_disabled is True
    assert result.transaction_cost_rate == 0.0
    assert point.date == (date(2025, 1, 1) + timedelta(days=200)).isoformat()
    assert point.soxl_quantity == pytest.approx(2_000.0)
    assert point.equity == pytest.approx(110_000.0)


def test_curve_is_immutable_and_derived_invariants_share_zero_cost_execution() -> None:
    soxx_closes = [100.0] * 199 + [200.0, 1.0, 300.0, 1.0]
    soxl_opens = [50.0] * 203
    soxl_closes = [50.0] * 203
    soxl_opens[200:203] = [50.0, 60.0, 40.0]
    soxl_closes[200:203] = [55.0, 60.0, 44.0]

    result = run_typed_baseline(_typed_input(soxx_closes, soxl_opens=soxl_opens, soxl_closes=soxl_closes))

    assert result.evaluation_count == len(result.equity_curve) == len(result.daily_returns) == 3
    assert result.trade_count == 3
    assert [point.equity for point in result.equity_curve] == pytest.approx([110_000.0, 120_000.0, 132_000.0])
    with pytest.raises(FrozenInstanceError):
        result.input_digest = "b" * 64
    with pytest.raises(FrozenInstanceError):
        result.equity_curve[0].equity = 0.0
    with pytest.raises(TypeError):
        BaselineResult(input_digest=result.input_digest, equity_curve=result.equity_curve, daily_returns=())
    with pytest.raises(BaselineResultContractError):
        run_typed_baseline(replace(_typed_input([200.0] * 201), source_revision=""))
