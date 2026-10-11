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
reviewed change.

Each strategy has two pre-registered return-path variants: ``live_plugins_as_live``
(primary: every live plugin/overlay exactly as configured live) and
``no_plugin_core`` (comparison). The live-plugin variant refuses real data with
``LIVE_PLUGIN_VARIANT_NOT_REPRODUCIBLE:<plugins>`` while any plugin in the
inventory cannot be reproduced on the research path. All seen history is ``development``; results never raise any
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


VARIANT_LIVE_PLUGINS = "live_plugins_as_live"
VARIANT_NO_PLUGIN = "no_plugin_core"
VARIANTS = (VARIANT_LIVE_PLUGINS, VARIANT_NO_PLUGIN)

# reproducibility of a plugin on the research path
REPRO_YES = "YES"  # deterministic from price history; research path can reproduce
REPRO_NEEDS_ADAPTER = "NEEDS_ADAPTER"  # deterministic, but the existing research path does not implement it yet
REPRO_NO = "NO"  # needs historical inputs that do not exist / are unverified
REPRO_OFF_IN_LIVE = "OFF_IN_LIVE"  # configured but has no live position effect; pre-registered as off


@dataclass(frozen=True)
class PluginSpec:
    name: str
    config_source: str
    live_state: str
    reproducible: str
    note: str = ""

    @property
    def blocks_live_variant(self) -> bool:
        return self.reproducible in (REPRO_NO, REPRO_NEEDS_ADAPTER)


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
    plugins: tuple[PluginSpec, ...] = ()
    variants: tuple[str, ...] = VARIANTS
    primary_variant: str = VARIANT_LIVE_PLUGINS

    def blocking_plugins(self, variant: str) -> tuple[str, ...]:
        if variant != VARIANT_LIVE_PLUGINS:
            return ()
        return tuple(p.name for p in self.plugins if p.blocks_live_variant)

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

_MANIFEST = "UES manifest default_config (platform pin UES 9d1544d1)"
_REGIME = ("market_regime_control signal artifact (gs://qsl-runtime-logs-shared/strategy-artifacts/us_equity/"
           "<profile>/plugins/market_regime_control/latest_signal.json); Schwab mount expected_mode=shadow")

SOXL_PLUGINS = (
    PluginSpec("blend_gate_volatility_delever", _MANIFEST, "ON (rolling_percentile, redirect SOXX)", REPRO_YES,
               "core rule; in soxl_trend_simulator"),
    PluginSpec("blend_gate_rsi_bollinger_caps", _MANIFEST, "ON (dynamic RSI threshold 70, Bollinger cap)", REPRO_YES,
               "core rule; in soxl_trend_simulator"),
    PluginSpec("market_regime_control", _MANIFEST + "; " + _REGIME,
               "ON in config (apply_risk_off=true, apply_risk_reduced=false); external signal mounted as shadow "
               "on Schwab; IBKR mount not verified", REPRO_NO,
               "position effect depends on per-day signal payload authorization; no verified historical "
               "signal archive for the window"),
    PluginSpec("volatility_delever_retention", _MANIFEST, "mode=environment, context_required=true", REPRO_NO,
               "reads market_regime_control context; without verified history only 'missing_context' (0 retention) "
               "is reproducible"),
    PluginSpec("income_layer", _MANIFEST + "; QRS platform-config income_layer_defaults",
               "ON, start 150000 USD, max ratio 0.95", REPRO_NEEDS_ADAPTER,
               "deterministic from account equity; needs a dollar-equity replay (core simulator disables it)"),
    PluginSpec("runtime_risk_gate", "IBKR RUNTIME_TARGET strategy_release / runtime_risk_limits; "
               "Schwab SCHWAB_RESERVED_CASH_RATIO / SCHWAB_MIN_RESERVED_CASH_USD / CASH_ONLY_EXECUTION",
               "IBKR caps SOXL 0.679, SOXX 0.873, total nominal 0.97, reserved cash 0.03, cash-only; "
               "Schwab reserved cash 0.03 (min 150 USD), cash-only", REPRO_NEEDS_ADAPTER,
               "deterministic caps; not in core simulator; Schwab and IBKR differ"),
    PluginSpec("option_income_overlay (soxx_put_credit_spread_income_v1)", _MANIFEST + "; QRS option_overlay_defaults",
               "configured; live_status=research_only, IBKR options_enabled=false", REPRO_OFF_IN_LIVE,
               "no live orders; no point-in-time option chains"),
)

TQQQ_PLUGINS = (
    PluginSpec("dual_drive_core (QQQ/TQQQ pullback, MA20 slope)", _MANIFEST, "ON", REPRO_YES, "core rule"),
    PluginSpec("dual_drive_volatility_delever", _MANIFEST, "ON (rolling_percentile 0.9, window 5)", REPRO_YES,
               "core rule; TACO veto is inert while retention_mode=environment"),
    PluginSpec("market_regime_control", _MANIFEST + "; " + _REGIME,
               "ON in config; IBKR plugin mount not verified", REPRO_NO,
               "no verified historical signal archive"),
    PluginSpec("volatility_delever_retention", _MANIFEST, "mode=environment, context_required=true", REPRO_NO,
               "reads market_regime_control context"),
    PluginSpec("dual_drive_crisis_defense", _MANIFEST, "ON", REPRO_NO,
               "needs true_crisis_active from regime/portfolio metadata"),
    PluginSpec("income_layer", _MANIFEST + "; QRS platform-config income_layer_defaults",
               "ON, start 250000 USD, max ratio 0.55", REPRO_NEEDS_ADAPTER, "deterministic from account equity"),
    PluginSpec("option_growth_overlay (tqqq_leaps_growth_v1)", _MANIFEST + "; QRS option_overlay_defaults",
               "configured; live_status=research_only, live_gate=promotion_required", REPRO_OFF_IN_LIVE,
               "no live orders; no point-in-time option chains"),
    PluginSpec("ai_extensions (taco_panic_rebound, crisis_regime_guard)", _MANIFEST, "OFF (enabled=false)",
               REPRO_OFF_IN_LIVE),
)

PREREGISTRATIONS: Mapping[str, KellyPreregistration] = {
    "rs04-kelly-soxl-v1": KellyPreregistration(
        preregistration_id="rs04-kelly-soxl-v1", strategy_key="SOXL",
        live_profile="soxl_soxx_trend_income",
        return_generator=("live_plugins_as_live: live entrypoint decision replay with every plugin as live (not built); "
                          "no_plugin_core: backtest.soxl_trend_simulator.run_soxl_core_only_backtest@live defaults"),
        benchmark_symbol="SOXX",
        gates=("NO_BUDGET_INCREASE", "SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED", "DEVELOPMENT_ONLY"),
        plugins=SOXL_PLUGINS,
        **_COMMON,
    ),
    "rs04-kelly-tqqq-v1": KellyPreregistration(
        preregistration_id="rs04-kelly-tqqq-v1", strategy_key="TQQQ",
        live_profile="tqqq_growth_income",
        return_generator=("live_plugins_as_live: live entrypoint decision replay with every plugin as live (not built); "
                          "no_plugin_core: tqqq_growth_income core replay with plugins off (not built)"),
        benchmark_symbol="QQQ",
        gates=("NO_BUDGET_INCREASE", "DEVELOPMENT_ONLY"),
        plugins=TQQQ_PLUGINS,
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
    variant: str = VARIANT_LIVE_PLUGINS


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
    if authorization.variant not in prereg.variants:
        raise ValueError("VARIANT_NOT_PREREGISTERED")
    if not prereg.data_confirmed:
        raise ValueError("PREREGISTRATION_PENDING_DATA_CONFIRMATION")
    blocking = prereg.blocking_plugins(authorization.variant)
    if blocking:
        raise ValueError("LIVE_PLUGIN_VARIANT_NOT_REPRODUCIBLE:" + ",".join(blocking))
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
