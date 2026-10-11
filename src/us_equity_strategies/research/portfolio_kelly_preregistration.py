"""Pre-registration and real-data authorization for the RS-06 portfolio fractional-Kelly study.

RS-06 (user request 2026-10-11) evaluates a **multi-strategy** fractional-Kelly
allocation across the two RS-04e ``no_plugin_core`` return streams (SOXL core,
TQQQ core) plus cash (BOXX), walk-forward, research-only.

Everything here is frozen before any RS-06 number is computed. Real data are
allowed only for ``rs06-kelly-portfolio-v1`` with a manifest that equals the
pre-registered data identity exactly (the same archive as RS-04e), and only when
both component pre-registrations are confirmed for ``no_plugin_core`` on the same
manifest. Results are ``DEVELOPMENT_ONLY`` and never raise any budget; SOXL stays
gated by its 252-session shadow requirement.

Human-readable copy: ``docs/research/portfolio_kelly_preregistration.zh-CN.md``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from us_equity_strategies.research import fractional_kelly_preregistration as single
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import (
    WalkForwardPlan,
)

# allocation methods (both pre-registered; "qp" is primary)
METHOD_QP = "qp"  # maximise w'mu - w'Sigma w / (2c) s.t. w >= 0, sum(w) <= 1 (exact KKT active sets)
METHOD_CLIP_SCALE = "clip_scale"  # w = c * Sigma^-1 mu, negatives -> 0, then scale down so sum(w) <= 1
METHODS = (METHOD_QP, METHOD_CLIP_SCALE)

# comparators on identical sessions / costs (names are report keys)
COMPARATORS = (
    "single_kelly.SOXL.c0.25", "single_kelly.SOXL.c0.5",
    "single_kelly.TQQQ.c0.25", "single_kelly.TQQQ.c0.5",
    "equal_weight_50_50",  # SOXL core 0.5 + TQQQ core 0.5, sum 1
    "equal_weight_25_25",  # 50% scaled: 0.25 + 0.25, rest cash
    "fixed_50.SOXL", "fixed_50.TQQQ",  # single asset 0.5, rest cash
    "full.SOXL", "full.TQQQ",  # single asset 1.0
    "benchmark.SOXX", "benchmark.QQQ",  # buy and hold
    "benchmark.SOXX_QQQ_50_50",  # rebalanced to 50/50 every session
)


@dataclass(frozen=True)
class PortfolioKellyPreregistration:
    preregistration_id: str
    components: tuple[tuple[str, str], ...]  # (asset key, component RS-04 preregistration id)
    variant: str
    cash_series: str
    plan: WalkForwardPlan
    fractions: tuple[float, ...]
    methods: tuple[str, ...]
    primary_method: str
    mean_estimator: str
    covariance_estimator: str
    min_train_observations: int
    base_cost_bps_per_side: float
    multipliers: tuple[float, ...]
    signal_cutoff: str
    execution: str
    calendar: str
    data_source: str
    data_manifest_sha256: str
    data_license: str
    data_retrieved_at: str
    window_start: date
    window_end: date
    comparators: tuple[str, ...]
    dsr_trial_set: str
    data_identity: str = "development"
    gates: tuple[str, ...] = ()


PREREGISTRATIONS: Mapping[str, PortfolioKellyPreregistration] = {
    "rs06-kelly-portfolio-v1": PortfolioKellyPreregistration(
        preregistration_id="rs06-kelly-portfolio-v1",
        components=(("SOXL", "rs04-kelly-soxl-v1"), ("TQQQ", "rs04-kelly-tqqq-v1")),
        variant=single.VARIANT_NO_PLUGIN,
        cash_series="BOXX_total_return_same_source",
        plan=WalkForwardPlan(train_sessions=252, test_sessions=63, purge_sessions=5, anchored=False),
        fractions=(0.25, 0.5),
        methods=METHODS,
        primary_method=METHOD_QP,
        mean_estimator=("TRAIN_ONLY_EXCESS_MEAN_PER_ASSET_POSITIVE_PART_SHRINK_MAX_0_1_MINUS_1_OVER_T_SQUARED "
                        "(same as RS-04)"),
        covariance_estimator=("LEDOIT_WOLF_2004_LINEAR_SHRINKAGE_TO_SCALED_IDENTITY_ANALYTIC_INTENSITY_"
                              "1_OVER_N_DEMEANED (as sklearn.covariance.ledoit_wolf, assume_centered=False) "
                              "on train excess returns"),
        min_train_observations=30,
        base_cost_bps_per_side=5.0,
        multipliers=(1.0, 2.0, 3.0),
        signal_cutoff="completed_regular_session_close_t",
        execution="next_close",
        calendar="XNYS",
        data_source="alpaca_sip_raw_bars_plus_corporate_actions_box_fetch_20261011",
        data_manifest_sha256="b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482",
        data_license="user_attested_non_commercial_private_research",
        data_retrieved_at="2026-10-11T02:07:07+00:00",
        window_start=date(2023, 3, 28),
        window_end=date(2026, 8, 25),
        comparators=COMPARATORS,
        dsr_trial_set=("ALL_SIZING_VARIANTS: 4 portfolio Kelly (2 methods x 2 c) + 4 single Kelly + 2 equal weight "
                       "+ 2 fixed 50 + 2 full = 14; net 1x excess over cash; benchmarks excluded"),
        gates=("NO_BUDGET_INCREASE", "SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED", "DEVELOPMENT_ONLY",
               "NO_LEVERAGE_NO_SHORT", "CAP_NOTE_ONLY"),
    ),
}


@dataclass(frozen=True)
class PortfolioRealDataAuthorization:
    preregistration_id: str
    manifest: single.DataManifest


def authorize_portfolio_real_data(
    authorization: object, *, plan: WalkForwardPlan, fractions: Sequence[float], methods: Sequence[str],
    cost_bps_per_side: float, multipliers: Sequence[float], assets: Sequence[str], dates: Sequence[date],
    report_context: Mapping[str, Any] | None,
) -> PortfolioKellyPreregistration:
    """Return the matching pre-registration or raise; never relaxes any field."""
    if not isinstance(authorization, PortfolioRealDataAuthorization):
        raise ValueError("REAL_DATA_REQUIRES_APPROVAL")  # noqa: TRY004 - one fail-closed approval code
    prereg = PREREGISTRATIONS.get(authorization.preregistration_id)
    if prereg is None:
        raise ValueError("PREREGISTRATION_ID_INVALID")
    if tuple(assets) != tuple(k for k, _ in prereg.components):
        raise ValueError("ASSETS_DIFFER_FROM_PREREGISTRATION")
    for key, component_id in prereg.components:
        comp = single.PREREGISTRATIONS.get(component_id)
        if (comp is None or comp.strategy_key != key or not comp.data_confirmed
                or prereg.variant not in comp.data_confirmed_variants
                or comp.data_manifest_sha256 != prereg.data_manifest_sha256
                or (comp.window_start, comp.window_end) != (prereg.window_start, prereg.window_end)):
            raise ValueError("COMPONENT_PREREGISTRATION_NOT_CONFIRMED:" + component_id)
    if (plan != prereg.plan or tuple(float(c) for c in fractions) != prereg.fractions
            or tuple(methods) != prereg.methods
            or float(cost_bps_per_side) != prereg.base_cost_bps_per_side
            or tuple(float(m) for m in multipliers) != prereg.multipliers):
        raise ValueError("PARAMETERS_DIFFER_FROM_PREREGISTRATION")
    m = authorization.manifest
    if not isinstance(m, single.DataManifest):
        raise ValueError("DATA_MANIFEST_REQUIRED")  # noqa: TRY004
    if (m.sha256 != prereg.data_manifest_sha256 or m.source != prereg.data_source
            or m.license != prereg.data_license or m.calendar != prereg.calendar):
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
