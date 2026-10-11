"""Pre-registrations and real-data authorization for the RS-04 evaluator.

Real data are allowed **only** for the explicit allowlist ``{"SOXL", "TQQQ"}``
(user approval 2026-10-11) and only when the caller presents:

- the matching pre-registration id below, and
- a data manifest (SHA-256, source, license, calendar, window, observations)
  that equals the pre-registered data source and window exactly,

and the evaluator parameters equal the frozen ones. Every other key stays
``REAL_DATA_REQUIRES_APPROVAL``. Pre-registrations whose data source/window are
not yet confirmed by the user (``None``) refuse real data with
``PREREGISTRATION_PENDING_DATA_CONFIRMATION``; confirming them is a separate
reviewed change. All seen history is ``development``; results never raise any
budget, and SOXL remains gated by its 252-session shadow requirement.

Human-readable copy: ``docs/research/fractional_kelly_preregistration.zh-CN.md``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from us_equity_strategies.research.fractional_kelly_walk_forward_plan import (
    WalkForwardPlan,
)

REAL_DATA_ALLOWLIST = frozenset({"SOXL", "TQQQ"})
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class KellyPreregistration:
    preregistration_id: str
    strategy_key: str
    live_profile: str
    return_generator: str
    benchmark_symbol: str
    cash_series: str
    plan: WalkForwardPlan
    fractions: tuple[float, ...]
    base_cost_bps_per_side: float
    multipliers: tuple[float, ...]
    signal_cutoff: str
    execution: str
    calendar: str
    data_identity: str = "development"
    data_source: str | None = None  # pending user confirmation
    window_start: date | None = None
    window_end: date | None = None
    gates: tuple[str, ...] = ()

    @property
    def data_confirmed(self) -> bool:
        return self.data_source is not None and self.window_start is not None and self.window_end is not None


_COMMON: dict[str, Any] = {
    "plan": WalkForwardPlan(train_sessions=252, test_sessions=63, purge_sessions=5, anchored=False),
    "fractions": (0.25, 0.5),
    "base_cost_bps_per_side": 5.0,
    "multipliers": (1.0, 2.0, 3.0),
    "signal_cutoff": "completed_regular_session_close_t",
    "execution": "next_close",
    "calendar": "XNYS",
    "cash_series": "BOXX_total_return_same_source",
}

PREREGISTRATIONS: Mapping[str, KellyPreregistration] = {
    "rs04-kelly-soxl-v1": KellyPreregistration(
        preregistration_id="rs04-kelly-soxl-v1", strategy_key="SOXL",
        live_profile="soxl_soxx_trend_income",
        return_generator="backtest.soxl_trend_simulator.run_soxl_core_only_backtest@live_manifest_defaults_core_only",
        benchmark_symbol="SOXX",
        gates=("NO_BUDGET_INCREASE", "SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED", "DEVELOPMENT_ONLY"),
        **_COMMON,
    ),
    "rs04-kelly-tqqq-v1": KellyPreregistration(
        preregistration_id="rs04-kelly-tqqq-v1", strategy_key="TQQQ",
        live_profile="tqqq_growth_income",
        return_generator="tqqq_growth_income_core_replay@live_manifest_defaults_core_only (generator not yet built)",
        benchmark_symbol="QQQ",
        gates=("NO_BUDGET_INCREASE", "DEVELOPMENT_ONLY"),
        **_COMMON,
    ),
}


@dataclass(frozen=True)
class DataManifest:
    sha256: str
    source: str
    license: str
    calendar: str
    start_date: date
    end_date: date
    observations: int


@dataclass(frozen=True)
class RealDataAuthorization:
    strategy_key: str
    preregistration_id: str
    manifest: DataManifest


def authorize_real_data(
    authorization: object, *, plan: WalkForwardPlan, fractions: Sequence[float], cost_bps_per_side: float,
    multipliers: Sequence[float], dates: Sequence[date], report_context: Mapping[str, Any] | None,
) -> KellyPreregistration:
    """Return the matching pre-registration or raise; never relaxes any field."""
    if not isinstance(authorization, RealDataAuthorization):
        raise ValueError("REAL_DATA_REQUIRES_APPROVAL")  # noqa: TRY004 - one fail-closed approval code
    if authorization.strategy_key not in REAL_DATA_ALLOWLIST:
        raise ValueError("REAL_DATA_REQUIRES_APPROVAL")
    prereg = PREREGISTRATIONS.get(authorization.preregistration_id)
    if prereg is None or prereg.strategy_key != authorization.strategy_key:
        raise ValueError("PREREGISTRATION_ID_INVALID")
    if not prereg.data_confirmed:
        raise ValueError("PREREGISTRATION_PENDING_DATA_CONFIRMATION")
    if (plan != prereg.plan or tuple(float(c) for c in fractions) != prereg.fractions
            or float(cost_bps_per_side) != prereg.base_cost_bps_per_side
            or tuple(float(m) for m in multipliers) != prereg.multipliers):
        raise ValueError("PARAMETERS_DIFFER_FROM_PREREGISTRATION")
    m = authorization.manifest
    if not isinstance(m, DataManifest):
        raise ValueError("DATA_MANIFEST_REQUIRED")  # noqa: TRY004
    if not isinstance(m.sha256, str) or not _SHA256.fullmatch(m.sha256):
        raise ValueError("DATA_MANIFEST_SHA256_INVALID")
    for text in (m.source, m.license, m.calendar):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("DATA_MANIFEST_FIELD_MISSING")
    if m.source != prereg.data_source or m.calendar != prereg.calendar:
        raise ValueError("DATA_MANIFEST_DIFFERS_FROM_PREREGISTRATION")
    if (m.start_date, m.end_date) != (prereg.window_start, prereg.window_end):
        raise ValueError("DATA_WINDOW_DIFFERS_FROM_PREREGISTRATION")
    if not dates or (dates[0], dates[-1]) != (m.start_date, m.end_date) or len(dates) != m.observations:
        raise ValueError("DATA_MANIFEST_DOES_NOT_MATCH_SERIES")
    if report_context is None:
        raise ValueError("REPORT_CONTEXT_REQUIRED_FOR_REAL_DATA")
    expected = {"manifest_sha256": m.sha256, "source": m.source, "license": m.license,
                "signal_cutoff": prereg.signal_cutoff, "execution": prereg.execution}
    if any(report_context.get(k) != v for k, v in expected.items()):
        raise ValueError("REPORT_CONTEXT_DIFFERS_FROM_MANIFEST_OR_PREREGISTRATION")
    return prereg
