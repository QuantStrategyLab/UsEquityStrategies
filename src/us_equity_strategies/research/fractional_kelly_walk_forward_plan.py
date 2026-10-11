"""Walk-forward plan for the RS-04 fractional-Kelly evaluator (research-only)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardPlan:
    """Session-index walk-forward plan; test folds are contiguous and ordered."""

    train_sessions: int
    test_sessions: int
    purge_sessions: int
    anchored: bool = False

    def folds(self, n: int) -> list[tuple[int, int, int, int]]:
        for name in ("train_sessions", "test_sessions", "purge_sessions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("WALK_FORWARD_PLAN_INVALID")
        if self.train_sessions < 2 or self.test_sessions < 1 or self.purge_sessions < 1:
            raise ValueError("WALK_FORWARD_PLAN_INVALID")
        out = []
        train_start, train_end = 0, self.train_sessions
        while True:
            test_start = train_end + self.purge_sessions
            test_end = test_start + self.test_sessions
            if test_end > n:
                break
            out.append((train_start, train_end, test_start, test_end))
            train_end += self.test_sessions
            if not self.anchored:
                train_start += self.test_sessions
        # Contiguous test folds: each next test starts where the previous ended.
        return out
