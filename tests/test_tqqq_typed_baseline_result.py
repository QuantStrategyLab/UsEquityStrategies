from dataclasses import FrozenInstanceError, replace
from datetime import date, timedelta

import pytest

from us_equity_strategies.research.tqqq_offline_input_contract import (
    InputRow,
    OfflineInput,
)
from us_equity_strategies.research.tqqq_typed_baseline_result import (
    BaselineResult,
    BaselineResultContractError,
    run_typed_baseline,
)


def _typed_input(
    qqq_closes: list[float],
    *,
    tqqq_opens: list[float] | None = None,
    tqqq_closes: list[float] | None = None,
) -> OfflineInput:
    count = len(qqq_closes)
    opens = tqqq_opens or [50.0] * count
    closes = tqqq_closes or opens
    rows: list[InputRow] = []
    for index, qqq_close in enumerate(qqq_closes):
        as_of = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
        tqqq_open = opens[index]
        tqqq_close = closes[index]
        rows.extend(
            (
                InputRow("QQQ", as_of, qqq_close, qqq_close, qqq_close, qqq_close, 1.0),
                InputRow(
                    "TQQQ",
                    as_of,
                    tqqq_open,
                    max(tqqq_open, tqqq_close),
                    min(tqqq_open, tqqq_close),
                    tqqq_close,
                    1.0,
                ),
            )
        )
    return OfflineInput(tuple(rows), b"typed-test-fixture", "a" * 64, "typed_test_v1")


def test_inclusive_sma_executes_at_next_open_and_derives_first_period() -> None:
    qqq_closes = [200.0] * 200 + [1.0]
    tqqq_opens = [50.0] * 201
    tqqq_closes = [50.0] * 201
    tqqq_opens[199] = 1_000.0
    tqqq_opens[200] = 50.0
    tqqq_closes[200] = 55.0

    result = run_typed_baseline(
        _typed_input(
            qqq_closes,
            tqqq_opens=tqqq_opens,
            tqqq_closes=tqqq_closes,
        )
    )

    point = result.equity_curve[0]
    assert result.profile == "tqqq_growth_income_research_baseline_v1"
    assert result.version == "qsl.research.tqqq_typed_baseline_result.v1"
    assert result.signal_timing == "SMA200_INCLUSIVE_CLOSE_V1"
    assert result.evaluation_count == 1
    assert point.date == (date(2025, 1, 1) + timedelta(days=200)).isoformat()
    assert point.tqqq_quantity == pytest.approx(2_000.0)
    assert point.cash == 0.0
    assert point.equity == pytest.approx(110_000.0)
    assert result.daily_returns[0].date == point.date
    assert result.daily_returns[0].daily_return == pytest.approx(0.10)
    assert result.trade_count == 1


def test_curve_returns_and_trade_count_share_zero_cost_execution() -> None:
    qqq_closes = [100.0] * 199 + [200.0, 1.0, 300.0, 1.0]
    tqqq_opens = [50.0] * 203
    tqqq_closes = [50.0] * 203
    tqqq_opens[200:203] = [50.0, 60.0, 40.0]
    tqqq_closes[200:203] = [55.0, 60.0, 44.0]

    result = run_typed_baseline(
        _typed_input(
            qqq_closes,
            tqqq_opens=tqqq_opens,
            tqqq_closes=tqqq_closes,
        )
    )

    assert result.controls_disabled is True
    assert result.transaction_cost_rate == 0.0
    assert result.evaluation_count == 3
    assert len(result.daily_returns) == result.evaluation_count
    assert result.trade_count == 3
    assert [point.equity for point in result.equity_curve] == pytest.approx(
        [110_000.0, 120_000.0, 132_000.0]
    )
    assert [point.cash for point in result.equity_curve] == pytest.approx(
        [0.0, 120_000.0, 0.0]
    )
    assert [point.tqqq_quantity for point in result.equity_curve] == pytest.approx(
        [2_000.0, 0.0, 3_000.0]
    )
    assert [point.daily_return for point in result.daily_returns] == pytest.approx(
        [0.10, 120_000.0 / 110_000.0 - 1.0, 0.10]
    )


def test_no_trade_evaluations_and_result_immutability() -> None:
    result = run_typed_baseline(_typed_input([200.0] * 203))

    assert result.evaluation_count == 3
    assert result.trade_count == 1
    assert isinstance(result.equity_curve, tuple)
    assert isinstance(result.daily_returns, tuple)
    with pytest.raises(FrozenInstanceError):
        result.input_digest = "b" * 64
    with pytest.raises(FrozenInstanceError):
        result.equity_curve[0].equity = 0.0
    with pytest.raises(TypeError):
        BaselineResult(
            input_digest=result.input_digest,
            equity_curve=result.equity_curve,
            daily_returns=(),
        )


def test_typed_boundary_fails_closed_without_an_executable_signal() -> None:
    with pytest.raises(BaselineResultContractError):
        run_typed_baseline(None)
    with pytest.raises(BaselineResultContractError):
        run_typed_baseline(_typed_input([200.0] * 200))
    with pytest.raises(BaselineResultContractError):
        run_typed_baseline(replace(_typed_input([200.0] * 201), source_revision=""))
