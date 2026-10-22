"""Research-only future-bar mutation smoke; unchanged signals are not proof."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from numbers import Number, Real

import pandas as pd


LOOKAHEAD_SMOKE_SCHEMA_VERSION = "qsl.research.lookahead_smoke.v1"


@dataclass(frozen=True)
class LookaheadSmokeResult:
    status: str
    baseline: object
    mutated: object
    detail: str
    schema_version: str = LOOKAHEAD_SMOKE_SCHEMA_VERSION
    research_only: bool = True
    no_order: bool = True
    promotion_eligible: bool = False


def _is_nan(value: object) -> bool:
    return isinstance(value, Real) and value != value


def _triggered(signal: object) -> bool:
    if signal is None or _is_nan(signal):
        return False
    if isinstance(signal, Mapping):
        return bool(signal) and not all(
            isinstance(value, Number) and value == 0 for value in signal.values()
        )
    return True


def _signals_equal(left: object, right: object) -> bool:
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        return (
            isinstance(left, Mapping)
            and isinstance(right, Mapping)
            and left.keys() == right.keys()
            and all(_signals_equal(left[key], right[key]) for key in left)
        )
    if _is_nan(left) and _is_nan(right):
        return True
    return bool(left == right)


def run_lookahead_smoke(
    signal_fn: Callable[[pd.Series], object],
    closes: pd.Series,
    *,
    cutoff: object,
    future_factor: float = 10.0,
) -> LookaheadSmokeResult:
    """Compare signals before/after mutating bars strictly beyond the cutoff.

    The cutoff must lie within the observed date range (it may be between bars).
    Only the baseline determines whether a signal triggered. A NO_LEAKAGE result
    describes this single smoke, not an absence of lookahead in general.
    """
    if (
        not isinstance(closes, pd.Series)
        or not isinstance(closes.index, pd.DatetimeIndex)
        or closes.empty
        or not closes.index.is_monotonic_increasing
        or closes.index.hasnans
    ):
        raise ValueError("LOOKAHEAD_SMOKE_INPUT_INVALID")

    try:
        cutoff_timestamp = pd.Timestamp(cutoff)
        if (
            pd.isna(cutoff_timestamp)
            or not (closes.index <= cutoff_timestamp).any()
            or cutoff_timestamp > closes.index[-1]
        ):
            raise ValueError("LOOKAHEAD_SMOKE_CUTOFF_INVALID")
        future = closes.index > cutoff_timestamp
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("LOOKAHEAD_SMOKE_CUTOFF_INVALID") from exc

    baseline = signal_fn(closes.copy())
    mutated = closes.copy()
    mutated.loc[future] = mutated.loc[future] * future_factor
    mutated_signal = signal_fn(mutated)

    if not _triggered(baseline):
        status = "NO_TRIGGER_FALSE_NEGATIVE_RISK"
        detail = "The smoke cannot prove anything without a triggered signal."
    elif _signals_equal(baseline, mutated_signal):
        status = "NO_LEAKAGE"
        detail = "No signal change under this future-bar mutation; smoke only, not proof."
    else:
        status = "LEAKAGE_DETECTED"
        detail = "The triggered signal changed when strictly-future bars were mutated."

    return LookaheadSmokeResult(
        status=status, baseline=baseline, mutated=mutated_signal, detail=detail
    )
