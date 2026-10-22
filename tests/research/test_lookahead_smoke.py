"""Synthetic-only lookahead smoke checks; no data downloads or orders."""

from dataclasses import FrozenInstanceError, asdict
import json
from types import MappingProxyType

import pandas as pd
import pytest

from us_equity_strategies.research.lookahead_smoke import (
    LOOKAHEAD_SMOKE_SCHEMA_VERSION,
    LookaheadSmokeResult,
    run_lookahead_smoke,
)
from us_equity_strategies.strategies.global_etf_rotation import compute_13612w_momentum


@pytest.fixture
def closes():
    index = pd.date_range("2022-01-01", "2024-03-31", freq="D")
    return pd.Series([100.0 + i * 0.1 for i in range(len(index))], index=index)


@pytest.fixture
def cutoff():
    return pd.Timestamp("2023-12-31")


def test_unanchored_momentum_detects_leakage(closes, cutoff):
    result = run_lookahead_smoke(
        lambda s: compute_13612w_momentum(s), closes, cutoff=cutoff
    )

    assert result.status == "LEAKAGE_DETECTED"
    assert result.baseline != result.mutated


def test_anchored_momentum_ignores_future_bars(closes, cutoff):
    result = run_lookahead_smoke(
        lambda s: compute_13612w_momentum(s, as_of_date=cutoff),
        closes,
        cutoff=cutoff,
    )

    assert result.status == "NO_LEAKAGE"
    assert result.baseline == result.mutated
    assert result.baseline > 0


@pytest.mark.parametrize("length", [10, 252])
def test_short_constant_momentum_cannot_pass(length):
    closes = pd.Series(100.0, index=pd.date_range("2023-01-01", periods=length))

    result = run_lookahead_smoke(
        compute_13612w_momentum, closes, cutoff=closes.index[length // 2]
    )

    assert pd.isna(result.baseline)
    assert pd.isna(result.mutated)
    assert result.status == "NO_TRIGGER_FALSE_NEGATIVE_RISK"
    assert "cannot prove anything without a triggered signal" in result.detail


@pytest.mark.parametrize(
    "baseline",
    [None, float("nan"), {}, {"SPY": 0.0, "QQQ": 0}, MappingProxyType({"SPY": 0})],
)
@pytest.mark.parametrize("change", [False, True])
def test_untriggered_baseline_cannot_pass_even_if_mutation_triggers(
    closes, cutoff, baseline, change
):
    def signal(s):
        if change and s.iloc[-1] != closes.iloc[-1]:
            return {"SPY": 1.0}
        return baseline

    result = run_lookahead_smoke(signal, closes, cutoff=cutoff)

    assert result.status == "NO_TRIGGER_FALSE_NEGATIVE_RISK"
    assert "cannot prove anything without a triggered signal" in result.detail


@pytest.mark.parametrize(
    ("baseline", "mutated", "expected"),
    [
        ({"SPY": 1.0, "QQQ": float("nan")},
         {"QQQ": float("nan"), "SPY": 1.0}, "NO_LEAKAGE"),
        ({"SPY": 1.0}, {"SPY": 0.0}, "LEAKAGE_DETECTED"),
        ({"SPY": 1.0}, {"QQQ": 1.0}, "LEAKAGE_DETECTED"),
        ({"SPY": float("nan")}, {"SPY": 1.0}, "LEAKAGE_DETECTED"),
        ({"SPY": 1.0}, None, "LEAKAGE_DETECTED"),
        ({"SPY": 0, "label": "hold"},
         {"label": "hold", "SPY": 0}, "NO_LEAKAGE"),
        (0.0, 0.0, "NO_LEAKAGE"),
        (False, False, "NO_LEAKAGE"),
    ],
)
def test_triggered_signals_use_nan_aware_key_value_equality(
    closes, cutoff, baseline, mutated, expected
):
    outputs = iter([baseline, mutated])

    result = run_lookahead_smoke(lambda s: next(outputs), closes, cutoff=cutoff)

    assert result.status == expected


@pytest.mark.parametrize("future_factor", [10.0, 2.5])
@pytest.mark.parametrize("between_bars", [False, True])
def test_only_strictly_future_bars_are_mutated(closes, cutoff, future_factor, between_bars):
    if between_bars:
        cutoff += pd.Timedelta(hours=12)
    original = closes.copy()
    seen = []

    def signal(s):
        seen.append(s.copy())
        # A signal is free to mutate its input; the caller's data stays intact.
        s.iloc[0] = -1.0
        return 1.0

    run_lookahead_smoke(signal, closes, cutoff=cutoff, future_factor=future_factor)

    assert len(seen) == 2
    pd.testing.assert_series_equal(seen[0], original)
    pd.testing.assert_series_equal(seen[1].loc[:cutoff], original.loc[:cutoff])
    future = original.index > cutoff
    assert future.any()
    pd.testing.assert_series_equal(seen[1].loc[future], original.loc[future] * future_factor)
    pd.testing.assert_series_equal(closes, original)


@pytest.mark.parametrize(
    "invalid",
    [
        [1.0, 2.0],
        pd.Series([1.0, 2.0]),
        pd.Series(dtype=float, index=pd.DatetimeIndex([])),
        pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-02", "2024-01-01"])),
        pd.Series([1.0], index=pd.DatetimeIndex([pd.NaT])),
    ],
)
def test_invalid_closes_rejected_before_signal_call(invalid):
    def signal(s):
        pytest.fail("Invalid input must not invoke the signal")

    with pytest.raises(ValueError, match="^LOOKAHEAD_SMOKE_INPUT_INVALID$"):
        run_lookahead_smoke(signal, invalid, cutoff=pd.Timestamp("2024-01-01"))


@pytest.mark.parametrize(
    "invalid_cutoff",
    ["2021-12-31", "2024-04-01", "not-a-date", pd.NaT, "2023-12-31T00:00:00Z"],
)
def test_invalid_cutoff_rejected_before_signal_call(closes, invalid_cutoff):
    def signal(s):
        pytest.fail("Invalid cutoff must not invoke the signal")

    with pytest.raises(ValueError, match="^LOOKAHEAD_SMOKE_CUTOFF_INVALID$"):
        run_lookahead_smoke(signal, closes, cutoff=invalid_cutoff)


def test_timezone_aware_comparable_cutoff(closes):
    closes.index = closes.index.tz_localize("UTC")
    result = run_lookahead_smoke(
        lambda s: 1.0, closes, cutoff="2023-12-31T08:00:00+08:00"
    )

    assert result.status == "NO_LEAKAGE"


def test_result_is_frozen_and_flags_are_json_inspectable(closes, cutoff):
    result = run_lookahead_smoke(lambda s: {"SPY": 1.0}, closes, cutoff=cutoff)

    assert isinstance(result, LookaheadSmokeResult)
    assert result.schema_version == LOOKAHEAD_SMOKE_SCHEMA_VERSION
    assert result.schema_version == "qsl.research.lookahead_smoke.v1"
    payload = json.loads(json.dumps(asdict(result)))
    assert set(payload) == {
        "status", "baseline", "mutated", "detail", "schema_version",
        "research_only", "no_order", "promotion_eligible",
    }
    assert payload["status"] == "NO_LEAKAGE"
    assert payload["baseline"] == payload["mutated"] == {"SPY": 1.0}
    assert payload["research_only"] is True
    assert payload["no_order"] is True
    assert payload["promotion_eligible"] is False
    with pytest.raises(FrozenInstanceError):
        result.status = "PASS"
