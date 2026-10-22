"""Walk-forward plan for the RS-04 fractional-Kelly evaluator (research-only)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NestedWalkForwardFold:
    """Outer fold and inner folds confined to its training window."""

    outer: tuple[int, int, int, int]
    inner: tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True)
class WalkForwardPlan:
    """Ordered session-index folds, contiguous when embargo is zero."""

    train_sessions: int
    test_sessions: int
    purge_sessions: int
    anchored: bool = False
    label_horizon_sessions: int = 1
    embargo_sessions: int = 0

    def folds(self, n: int) -> list[tuple[int, int, int, int]]:
        for name in ("train_sessions", "test_sessions", "purge_sessions",
                     "label_horizon_sessions", "embargo_sessions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("WALK_FORWARD_PLAN_INVALID")
        if (self.train_sessions < 2 or self.test_sessions < 1 or self.purge_sessions < 1
                or self.label_horizon_sessions < 1 or self.embargo_sessions < 0
                or self.purge_sessions < self.label_horizon_sessions):
            raise ValueError("WALK_FORWARD_PLAN_INVALID")
        out = []
        step = self.test_sessions + self.embargo_sessions
        train_start, train_end = 0, self.train_sessions
        while True:
            test_start = train_end + self.purge_sessions
            test_end = test_start + self.test_sessions
            if test_end > n:
                break
            out.append((train_start, train_end, test_start, test_end))
            train_end += step
            if not self.anchored:
                train_start += step
        return out

    def nested_folds(self, n: int, *, inner_splits: int = 3) -> list[NestedWalkForwardFold]:
        if isinstance(inner_splits, bool) or not isinstance(inner_splits, int) or inner_splits < 1:
            raise ValueError("WALK_FORWARD_PLAN_INVALID")
        out = []
        for outer in self.folds(n):
            a, b, _, _ = outer
            width = max(1, (b - a) // (inner_splits + 1))
            inner = []
            for i in range(inner_splits):
                val_start = b - (inner_splits - i) * width
                val_end = val_start + width
                train_end = val_start - self.purge_sessions
                if train_end - a >= 2:
                    inner.append((a, train_end, val_start, val_end))
            out.append(NestedWalkForwardFold(outer=outer, inner=tuple(inner)))
        return out
