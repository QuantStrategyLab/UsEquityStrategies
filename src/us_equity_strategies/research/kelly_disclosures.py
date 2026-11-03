"""Shared RS-07 disclosures and paired bootstrap for the Kelly evaluators (research-only).

- ``coverage_disclosure``: which window sessions are train-only warm-up, tested,
  or dropped after the last complete fold (the tail is never evaluated).
- ``METRIC_BASIS``: which return basis each statistic uses (PSR/bootstrap now
  also on net 1x; gross kept as a labelled diagnostic; DSR on net 1x).
- ``COST_MODEL_DISCLOSURE``: the outer-layer cost approximation (see
  ``docs/research/kelly-security-level-ledger-design.zh-CN.md``).
- ``paired_stationary_bootstrap``: CI of the mean daily **net log-growth
  difference** between two paths on the same sessions, resampling the paired
  sessions jointly (Politis-Romano stationary bootstrap, circular, fixed seed).
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import date

METRIC_BASIS = {
    "psr_net1x_excess": "PRIMARY: net of 1x costs, excess over cash",
    "psr_gross_excess": "DIAGNOSTIC: gross of costs, excess over cash",
    "dsr": "net of 1x costs, excess over cash, against the declared trial set",
    "bootstrap_net1x": "PRIMARY: stationary bootstrap of net 1x returns",
    "bootstrap_gross": "DIAGNOSTIC: stationary bootstrap of gross returns",
    "paired_bootstrap": "net 1x daily log-growth difference vs baseline, paired sessions resampled jointly",
    "tiers": "net of cost tier multiplier",
}

COST_MODEL_DISCLOSURE = {
    "outer_layer": "STRATEGY_RETURN_STREAM_TREATED_AS_VIRTUAL_ASSET_REBALANCED_TO_TARGET_EVERY_SESSION",
    "traded_notional": "OUTER_ABS_WEIGHT_CHANGE_PLUS_WEIGHT_TIMES_INNER_TURNOVER",
    "netting": "NOT_SECURITY_LEVEL_NETTED: outer and inner trades in the same security are both charged",
    "outer_cash_leg": "FREE (cash series earns, never charged)",
    "inner_boxx_trades": "CHARGED (BOXX is a tradable asset inside the core path)",
    "design_note": "docs/research/kelly-security-level-ledger-design.zh-CN.md",
}


def coverage_disclosure(dates: Sequence[date], folds: Sequence[tuple[int, int, int, int]]) -> dict:
    n = len(dates)
    lo, hi = folds[0][2], folds[-1][3]
    tail = n - hi
    return {
        "window_start": dates[0].isoformat(), "window_end": dates[-1].isoformat(), "window_sessions": n,
        "train_only_warmup_sessions": lo, "tested_sessions": hi - lo,
        "test_start": dates[lo].isoformat(), "test_end": dates[hi - 1].isoformat(),
        "dropped_tail_sessions": tail,
        "dropped_tail_start": dates[hi].isoformat() if tail else None,
        "dropped_tail_end": dates[-1].isoformat() if tail else None,
        "dropped_tail_rule": "sessions after the last complete test fold are not evaluated (no partial fold)",
    }


def paired_stationary_bootstrap(a: Sequence[float], b: Sequence[float], *, seed: int, n_resamples: int = 2000,
                                mean_block_length: float = 5.0, confidence: float = 0.95) -> dict:
    n = len(a)
    base = {"method": "paired_stationary_bootstrap_circular", "statistic": "mean_daily_log_growth_difference",
            "seed": seed, "n_resamples": n_resamples, "mean_block_length": mean_block_length,
            "confidence": confidence, "observations": n}
    if n != len(b) or n < 2 or any(x <= -1.0 for x in (*a, *b)):
        return {**base, "status": "UNCOMPUTABLE", "reason_code": "PAIRED_SERIES_INVALID"}
    d = [math.log1p(x) - math.log1p(y) for x, y in zip(a, b)]
    rng = random.Random(seed)
    p_new = 1.0 / mean_block_length
    stats = []
    for _ in range(n_resamples):
        total, k, i = 0.0, 0, rng.randrange(n)
        while k < n:
            total += d[i]
            k += 1
            i = rng.randrange(n) if rng.random() < p_new else (i + 1) % n
        stats.append(total / n)
    stats.sort()
    alpha = (1.0 - confidence) / 2.0

    def q(p: float) -> float:
        pos = (len(stats) - 1) * p
        lo_i = int(pos)
        hi_i = min(lo_i + 1, len(stats) - 1)
        return stats[lo_i] + (stats[hi_i] - stats[lo_i]) * (pos - lo_i)

    return {**base, "status": "COMPUTED", "reason_code": None, "mean_diff_per_session": math.fsum(d) / n,
            "ci_low": q(alpha), "ci_high": q(1.0 - alpha),
            "share_resamples_le_zero": sum(1 for s in stats if s <= 0.0) / len(stats)}
