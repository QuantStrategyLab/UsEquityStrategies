"""Research-only RS-04 B1/B2 preparation contracts. Not a freeze and not live parity.

The functions below bind synthetic counterexamples for causality, variance
forecast failure, drawdown state, fee-role separation, original-cap priority,
measurement nulls, and trial integrity. They do not read market data, attest a
fee schedule or risk cap, open a holdout, or write a journal.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import stdev

STATUS_DEVELOPMENT_ONLY = "DEVELOPMENT_ONLY"
BUDGET_EFFECT_NONE = "NONE"

RECOMMENDED_PROPOSAL = {
    "state": "RECOMMENDED_NOT_FROZEN",
    "scope": "RS-04 SOXL/TQQQ research core",
    "variant": "no_plugin_core",
    "live_ready": False,
    "no_order": True,
    "budget_effect": BUDGET_EFFECT_NONE,
    "research_frozen": False,
    "b1_rows": (
        "fixed_50_subject_to_original_cap",
        "kelly_c_0.25_once",
        "kelly_c_0.5_once",
        "ewma_vol_target_and_kelly_cap",
    ),
    "har_rv": "PARKED",
    "b2_rows_not_crossed_with_b1": ("original_ladder", "continuous_drawdown"),
    "b2_withheld_until_separate_registration": ("normalized_cushion",),
    "cartesian_product": False,
    "primary_timing": "next_close",
    "sensitivity_timings": ("next_open", "same_close"),
    "same_close_role": "hypothetical_sensitivity_not_primary",
    "cost_tier_role_until_d10": "REPORT_SCENARIO_NOT_SELECTION_TRIAL",
    "ewma_form": "v_next=lambda*v_current+(1-lambda)*r_t^2",
    "ewma_lambda": {
        "recommendation": 0.94,
        "status": "RECOMMENDED_NOT_FROZEN",
        "scope_attestation": "NOT_ATTESTED",
        "source": "RiskMetrics daily convention cited by the QPK B1/B2 proposal; not attested for this scope",
    },
    "ewma_initial_variance": "UNSPECIFIED",
    "ewma_warmup_sessions": "UNSPECIFIED",
    "target_vol": "NOT_ATTESTED",
    "forecast_unit": "simple_return_per_observation_not_annualized",
    "existing_cap": "NOT_ATTESTED",
    "d_max": "NOT_ATTESTED",
    "fee_bps": "NOT_ATTESTED",
    "pit": "NOT_ATTESTED",
    "holdout": "NOT_ATTESTED",
    "journal": "NOT_ATTESTED",
    "trial_unit": (
        "one attempt = RS-04 research-core path + candidate row + parameter binding"
        " + cost role + timing role + attempt ordinal"
    ),
    "counted_statuses": ("COMPUTED", "PARKED", "REJECTED", "INTERRUPTED"),
}

SYNTHETIC_COUNTEREXAMPLE = {
    "seed": 20261014,
    "absolute_tolerance": 1e-12,
    "research_frozen": False,
    "fee_schedule": "NOT_A_FEE_SCHEDULE",
    "risk_cap": "NOT_A_RISK_CAP",
}

RUNNER_DIFFERENTIAL = {
    "private_runner_consumes": ("gross_returns", "turnover"),
    "private_runner_omits": ("close_fill_turnover", "measure_close_fill_costs"),
    "private_runner_cash": "outer_cash_leg_boxx_total_return_when_actually_run",
    "inner_residual_cash": "earns_zero_inside_core_path",
    "inner_boxx": "tradable_core_asset_charged_when_the_core_trades_it",
    "a2_cash": "zero_return_residual_single_name_overlay",
    "close_fill_measurement": "opt_in_nav_overlay_no_weight_feedback",
    "live_parity": False,
    "runner_adoption": "NOT_ATTESTED",
}

QPK_INTERFACE_GAPS = (
    {
        "repo": "QuantPlatformKit",
        "symbol": "research_stats.execution_models.CostModel.net_return",
        "observed_overlay_sha": "5c0693c8735f5ed04c8943ab1fab00c82496b22c",
        "missing_capability": "inner_versus_outer_cash_role",
        "invent_qpk_api": False,
    },
    {
        "repo": "QuantPlatformKit",
        "symbol": "research_stats.execution_models.FillModel.resolve",
        "observed_overlay_sha": "5c0693c8735f5ed04c8943ab1fab00c82496b22c",
        "missing_capability": "multi_asset_cash_balance",
        "invent_qpk_api": False,
    },
    {
        "repo": "QuantPlatformKit",
        "symbol": "research_stats.cost_stress_recompute",
        "observed_overlay_sha": "442b729dcc4be5100bc2cba5b67850ad676bfbc0",
        "missing_capability": "security_level_netting_of_inner_weights_and_outer_fraction",
        "invent_qpk_api": False,
    },
    {
        "repo": "QuantPlatformKit",
        "symbol": "research_stats.unified_report.build_unified_net_report",
        "observed_overlay_sha": "b6b51d5c32594c47cc1b3ebc0c1ed226e9db887f",
        "missing_capability": "rs04_inner_outer_role_argument",
        "invent_qpk_api": False,
    },
)


class PreparationStop(ValueError):
    """Fail-closed preparation error. Carries a reason code, never market data."""


# OWNER_APPROVED 2026-10-14: the immutable contract's D03/D04/D06/D07
# (lines 35-39, 135-140). This is separate from RECOMMENDED_PROPOSAL.
# A matching reference identifies the document; it does not authenticate a caller
# or attest the scope, input source, actual initializer, runner or research gates.
_APPROVED_SUBSET_SOURCE = (
    ("repository", "QuantStrategyLab/UsEquityStrategies"),
    ("contract_path", "docs/research/b1_b2_freeze_decision_contract.zh-CN.md"),
    ("contract_blob", "3cd71037ea463e4921324d5996995fcc73eb2e8e"),
    ("approval_date", "2026-10-14"),
    ("decisions", ("D03", "D04", "D06", "D07")),
    ("scope", "RS-04 SOXL/TQQQ research core"),
)
_APPROVED_SUBSET_PARAMETERS = (
    ("b1_rows", ("fixed_50_subject_to_original_cap", "kelly_c_0.25_once",
                 "kelly_c_0.5_once", "ewma_vol_target_and_kelly_cap")),
    ("kelly_primary_c", 0.25), ("kelly_comparison_c", 0.5), ("ewma_kelly_c", 0.25),
    ("fractional_application_count", 1), ("cap_priority", "ORIGINAL_CAP_AFTER_FRACTIONAL"),
    ("b2_rows", ("original_ladder", "continuous_drawdown")),
    ("b2_base_row", "kelly_c_0.25_once"), ("cartesian_product", False),
    ("ewma_form", "v_next=lambda*v_current+(1-lambda)*r_t^2"),
    ("return_unit", "daily_simple_return"),
    ("variance_unit", "simple_return_squared_per_observation"),
    ("target_vol_annualized", 0.20), ("annualization_sessions", 252), ("ewma_lambda", 0.94),
    ("initial_variance_rule", "sample_variance_first_63_training_sessions"),
    ("warmup_sessions", 63), ("warmup_status", "PARKED"), ("warmup_exposure", 0.0),
    ("latest_observation", "t-1"), ("invalid_observation_rule", "PARKED_ZERO_NO_PREDICTOR_SWITCH"),
    ("sensitivity_target_vol_annualized", 0.30), ("sensitivity_ewma_lambda", 0.97),
    ("sensitivity_role", "REPORT_ONLY"),
    ("har_rv", "PARKED"), ("har_registered", False), ("har_run", False),
)
_UNVERIFIED_RESEARCH_PREREQUISITES = (
    "D01_SCOPE", "D02_RUNNER", "D05_RETURN_CASH_BASIS", "D08_CAPS_AND_RECOVERY",
    "D09_EXECUTION_TIMING", "D10_FEES", "D11_SOURCE_LICENSE_PIT", "D12_HOLDOUT_FOLDS",
    "D13_STATISTICAL_ROLES", "D14_TAIL_BUDGET", "D15_JOURNAL", "D16_REVIEW_AND_RUN_APPROVAL",
)


def _approved_subset_value(value: object, expected: object) -> bool:
    if isinstance(expected, tuple):
        # Listing order is not a new candidate; missing/duplicate/extra rows are.
        return (type(value) in (list, tuple) and len(value) == len(expected)
                and all(type(item) is str for item in value) and set(value) == set(expected))
    if type(expected) is float:
        return type(value) in (int, float) and value == expected and math.isfinite(value)
    return type(value) is type(expected) and value == expected


def validate_approved_parameter_subset(parameters: Mapping[str, object], *,
                                       source_binding: Mapping[str, object] | None) -> dict[str, object]:
    """Check only the documented D03/D04/D06/D07 parameter record, without I/O.

    This does not calculate EWMA, sizing or Bayes, inspect observations, prove
    actual runner adoption, or accept prerequisite attestations. In particular,
    missing scope/PIT/holdout/journal evidence cannot be supplied as extra fields
    or promoted by a successful parameter comparison. The other decisions remain
    unverified by this function even if some of their values were approved.
    Returned mappings are fresh and their sequence values are immutable.
    """
    if not isinstance(source_binding, Mapping):
        raise PreparationStop("APPROVED_SUBSET_SOURCE_REQUIRED")
    if set(source_binding) != {key for key, _ in _APPROVED_SUBSET_SOURCE}:
        raise PreparationStop("APPROVED_SUBSET_SOURCE_MISMATCH")
    for key, expected in _APPROVED_SUBSET_SOURCE:
        if not _approved_subset_value(source_binding[key], expected):
            raise PreparationStop("APPROVED_SUBSET_SOURCE_MISMATCH:" + key)
    if not isinstance(parameters, Mapping) or set(parameters) != {key for key, _ in _APPROVED_SUBSET_PARAMETERS}:
        raise PreparationStop("APPROVED_SUBSET_FIELDS_INVALID")
    for key, expected in _APPROVED_SUBSET_PARAMETERS:
        if not _approved_subset_value(parameters[key], expected):
            raise PreparationStop("APPROVED_SUBSET_PARAMETER_MISMATCH:" + key)
    return {
        "status": "OWNER_APPROVED_SUBSET_ONLY",
        "validated_decisions": ("D03", "D04", "D06", "D07"),
        "parameters": dict(_APPROVED_SUBSET_PARAMETERS),
        "source_binding": dict(_APPROVED_SUBSET_SOURCE),
        "source_binding_role": "DOCUMENT_REFERENCE_NOT_EXTERNAL_ATTESTATION",
        "unverified_prerequisites": _UNVERIFIED_RESEARCH_PREREQUISITES,
        "research_frozen": False, "research_allowed": False,
        "live_ready": False, "no_order": True, "budget_effect": BUDGET_EFFECT_NONE,
    }


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PreparationStop(code)
    number = float(value)
    if not math.isfinite(number):
        raise PreparationStop(code)
    return number


def fee_role_report(inner_close_fill: Sequence[float], legacy_shifted: Sequence[float],
                    outer_traded: Sequence[float], *, sum_roles: bool = False) -> dict[str, object]:
    """Label the three RS-04 turnover roles. They are not an additive fee."""
    if sum_roles:
        raise PreparationStop("FEE_ROLES_MUST_NOT_BE_SUMMED")
    rows = []
    for series in (inner_close_fill, legacy_shifted, outer_traded):
        rows.append(tuple(_finite(value, "FEE_ROLE_INPUT_INVALID") for value in series))
    if len({len(row) for row in rows}) != 1:
        raise PreparationStop("FEE_ROLE_LENGTH_MISMATCH")
    return {"inner_close_fill": rows[0], "legacy_shifted": rows[1], "outer_traded": rows[2],
            "roles_equal": rows[0] == rows[1] == rows[2], "sum_permitted": False,
            "security_level_netting": "QPK_INTERFACE_ABSENT", "live_parity": False,
            "live_ready": False, "no_order": True, "budget_effect": BUDGET_EFFECT_NONE,
            "research_frozen": False}


def session_clocks(*, signal_index: int, timing: str) -> dict[str, object]:
    """Separate observation, availability, signal, and fill without claiming a live clock."""
    if timing not in ("same_close", "next_close", "next_open"):
        raise PreparationStop("FILL_TIMING_INVALID")
    if type(signal_index) is not int or signal_index < 0:
        raise PreparationStop("SESSION_INDEX_INVALID")
    fill_phase = "hypothetical_same_close" if timing == "same_close" else timing
    return {
        "observation_index": signal_index, "observation_phase": "close_print",
        "available_index": signal_index, "available_phase": "after_completed_close",
        "signal_index": signal_index, "signal_phase": "completed_close_signal",
        "fill_index": signal_index if timing == "same_close" else signal_index + 1,
        "fill_phase": fill_phase, "same_close_is_hypothetical": timing == "same_close",
        "live_parity": False,
    }


def ewma_variance_update(variance: float, simple_return: float, decay: float) -> float:
    """One zero-mean step: ``v_next = decay * v + (1 - decay) * r^2``."""
    current = _finite(variance, "VARIANCE_INVALID")
    observed = _finite(simple_return, "RETURN_INVALID")
    lam = _finite(decay, "DECAY_INVALID")
    if not 0.0 < lam < 1.0 or current < 0.0:
        raise PreparationStop("EWMA_INPUT_INVALID")
    updated = lam * current + (1.0 - lam) * observed * observed
    if not math.isfinite(updated):
        raise PreparationStop("EWMA_INPUT_INVALID")
    return updated


def advance_vol_forecast(observations: Sequence[object], *, predictor: str, decay: float,
                         initial_variance: float, warmup_sessions: int) -> dict[str, object]:
    """Walk a synthetic forecast. Failure parks at zero exposure and does not switch predictor."""
    base = {"predictor_used": predictor, "predictor_switched": False, "infinite_exposure": False,
            "annualized": False, "unit": "simple_return_squared_per_observation",
            "forecast_emitted": False, "variance": None, "variance_path": (),
            "live_ready": False, "no_order": True, "budget_effect": BUDGET_EFFECT_NONE,
            "research_frozen": False}
    if predictor == "HAR":
        return {**base, "status": "PARKED", "reason_code": "HAR_PARKED_DAILY_SQUARE_IS_NOT_RV",
                "exposure_scale": 0.0}
    if predictor != "EWMA":
        raise PreparationStop("PREDICTOR_INVALID")
    if type(warmup_sessions) is not int or warmup_sessions < 1:
        raise PreparationStop("WARMUP_INVALID")
    if isinstance(initial_variance, bool) or not isinstance(initial_variance, (int, float)):
        return {**base, "status": "PARKED", "reason_code": "INITIAL_VARIANCE_NOT_POSITIVE",
                "exposure_scale": 0.0}
    initial = float(initial_variance)
    if not math.isfinite(initial) or initial <= 0.0:
        return {**base, "status": "PARKED", "reason_code": "INITIAL_VARIANCE_NOT_POSITIVE",
                "exposure_scale": 0.0}
    lam = _finite(decay, "DECAY_INVALID")
    if not 0.0 < lam < 1.0:
        raise PreparationStop("EWMA_INPUT_INVALID")
    variance = initial
    path: list[float] = []
    for observed in observations:
        if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed):
            return {**base, "status": "PARKED", "reason_code": "FORECAST_INPUT_UNUSABLE",
                    "exposure_scale": 0.0, "variance_path": tuple(path)}
        variance = ewma_variance_update(variance, float(observed), lam)
        path.append(variance)
    if len(path) < warmup_sessions:
        return {**base, "status": "PARKED", "reason_code": "WARMUP_INCOMPLETE",
                "exposure_scale": 0.0, "variance_path": tuple(path)}
    return {**base, "status": "COMPUTED", "reason_code": None, "exposure_scale": None,
            "forecast_emitted": True, "variance": path[-1], "variance_path": tuple(path)}


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _wealth_inputs(wealth: float, peak: float, d_max: float) -> tuple[float, float, float]:
    current = _finite(wealth, "WEALTH_INVALID")
    maximum = _finite(peak, "PEAK_INVALID")
    limit = _finite(d_max, "D_MAX_INVALID")
    if current <= 0.0 or maximum < current or not 0.0 < limit <= 1.0:
        raise PreparationStop("DRAWDOWN_INPUT_INVALID")
    return current, maximum, limit


def continuous_drawdown_factor(wealth: float, peak: float, d_max: float, *,
                               report_floor_breach: bool = False) -> float | dict[str, object]:
    """``clip(1 - d / D_max, 0, 1)`` with ``d = 1 - W / M``. A breach is reported, not guaranteed away."""
    current, maximum, limit = _wealth_inputs(wealth, peak, d_max)
    drawdown = 1.0 - current / maximum
    factor = _clip01(1.0 - drawdown / limit)
    if not report_floor_breach:
        return factor
    return {"factor": factor, "breached_floor": current < (1.0 - limit) * maximum,
            "hard_drawdown_guarantee": False, "live_ready": False, "no_order": True,
            "budget_effect": BUDGET_EFFECT_NONE}


def normalized_cushion_factor(wealth: float, peak: float, d_max: float) -> float:
    """``clip((W - alpha M) / (D_max W), 0, 1)`` with ``alpha = 1 - D_max``."""
    current, maximum, limit = _wealth_inputs(wealth, peak, d_max)
    alpha = 1.0 - limit
    return _clip01((current - alpha * maximum) / (limit * current))


def original_ladder_factor(drawdown: float, *, full_until: float, half_until: float) -> float:
    """Exact fixture boundaries. The thresholds are not an attested live gate."""
    loss = _finite(drawdown, "DRAWDOWN_INVALID")
    upper_full = _finite(full_until, "LADDER_INVALID")
    upper_half = _finite(half_until, "LADDER_INVALID")
    if loss < 0.0 or not 0.0 <= upper_full < upper_half <= 1.0:
        raise PreparationStop("LADDER_INVALID")
    if loss <= upper_full:
        return 1.0
    if loss <= upper_half:
        return 0.5
    return 0.0


@dataclass(frozen=True)
class DrawdownState:
    investment_index: float
    peak_index: float
    external_cash: float
    initial_nav: float

    def dollar_wealth(self) -> float:
        return self.initial_nav * self.investment_index + self.external_cash


def start_drawdown(initial_nav: float) -> DrawdownState:
    nav = _finite(initial_nav, "NAV_INVALID")
    if nav <= 0.0:
        raise PreparationStop("NAV_INVALID")
    return DrawdownState(1.0, 1.0, 0.0, nav)


def apply_investment_return(state: DrawdownState, simple_return: float) -> DrawdownState:
    """A loss updates the index and leaves the peak where it is."""
    change = _finite(simple_return, "RETURN_INVALID")
    updated = state.investment_index * (1.0 + change)
    if not math.isfinite(updated) or updated <= 0.0:
        raise PreparationStop("RUINED")
    return DrawdownState(updated, max(state.peak_index, updated), state.external_cash, state.initial_nav)


def apply_external_cash(state: DrawdownState, amount: float) -> DrawdownState:
    """Cash changes dollar wealth only. It does not clear the investment-index drawdown."""
    cash = _finite(amount, "CASH_FLOW_INVALID")
    return DrawdownState(state.investment_index, state.peak_index,
                         state.external_cash + cash, state.initial_nav)


def checkpoint_drawdown(state: DrawdownState) -> tuple[float, float, float, float]:
    return (state.investment_index, state.peak_index, state.external_cash, state.initial_nav)


def restore_drawdown(token: Sequence[float]) -> DrawdownState:
    if len(tuple(token)) != 4:
        raise PreparationStop("CHECKPOINT_INVALID")
    return DrawdownState(*( _finite(value, "CHECKPOINT_INVALID") for value in token))


def _cap(value: object) -> float | None:
    if value is None:
        return None
    number = _finite(value, "CAP_INVALID")
    if not 0.0 < number <= 1.0:
        raise PreparationStop("CAP_INVALID")
    return number


def _kelly_leg(c: object, f_star: object) -> tuple[float, int]:
    fraction = _finite(c, "FRACTIONAL_ABOVE_CEILING")
    if not 0.0 <= fraction <= 0.5:
        raise PreparationStop("FRACTIONAL_ABOVE_CEILING")
    star = _finite(f_star, "KELLY_INPUT_INVALID")
    return _clip01(fraction * star), 1


def compose_exposure(*, kind: str, existing_cap: float | None, c: float | None = None,
                     f_star: float | None = None, target_vol: float | None = None,
                     forecast_vol: float | None = None, fractional_applications: int = 0,
                     breaker_veto: bool = False, cvar_constraint_available: bool = False) -> dict[str, object]:
    """Apply one fractional factor, then the original cap. Missing CVaR does not release it."""
    if type(fractional_applications) is not int or fractional_applications < 0:
        raise PreparationStop("FRACTIONAL_ALREADY_APPLIED")
    if fractional_applications != 0:
        raise PreparationStop("FRACTIONAL_ALREADY_APPLIED")
    if kind not in ("fractional_kelly", "fixed_50", "vol_target_and_kelly"):
        raise PreparationStop("EXPOSURE_KIND_INVALID")
    result = {"live_ready": False, "no_order": True, "budget_effect": BUDGET_EFFECT_NONE,
              "research_frozen": False, "cvar_released_cap": False, "infinite_exposure": False,
              "baseline_exempt": False, "fractional_applications": 0,
              "cvar_constraint_available": cvar_constraint_available is True}
    if breaker_veto:
        return {**result, "status": "CAPPED", "exposure": 0.0, "reason_code": "BREAKER_OR_VETO_PRIORITY"}
    cap = _cap(existing_cap)
    if cap is None:
        return {**result, "status": "UNCOMPUTABLE", "exposure": None, "reason_code": "CAP_NOT_ATTESTED"}
    applications = 0
    if kind == "fixed_50":
        leg = 0.5
    elif kind == "fractional_kelly":
        leg, applications = _kelly_leg(c, f_star)
    else:
        leg, applications = _kelly_leg(c, f_star)
        if isinstance(forecast_vol, bool) or not isinstance(forecast_vol, (int, float)) or not math.isfinite(
                float(forecast_vol)) or float(forecast_vol) <= 0.0 or target_vol is None:
            return {**result, "status": "PARKED", "exposure": 0.0, "reason_code": "FORECAST_VOL_UNUSABLE",
                    "fractional_applications": applications}
        target = _finite(target_vol, "TARGET_VOL_INVALID")
        if target <= 0.0:
            raise PreparationStop("TARGET_VOL_INVALID")
        leg = min(leg, _clip01(target / float(forecast_vol)))
    return {**result, "status": "CAPPED", "exposure": min(cap, leg), "reason_code": None,
            "fractional_applications": applications}


def period_measurement(*, sessions: Sequence[date], net_returns: Sequence[float],
                       gross_returns: Sequence[float] | None, trials_complete: bool,
                       initial_nav: float, legacy_annualized: float | None,
                       trial_sharpe_variance: float | None = None) -> dict[str, object]:
    """Same-session measurement. Missing gross or trials stay null; legacy CAGR stays a separate field."""
    days = tuple(sessions)
    net = tuple(_finite(value, "NET_RETURN_INVALID") for value in net_returns)
    if not days or len(days) != len(net) or any(type(day) is not date for day in days):
        raise PreparationStop("MEASUREMENT_SESSIONS_INVALID")
    if any(value <= -1.0 for value in net):
        raise PreparationStop("NET_RETURN_INVALID")
    nav = _finite(initial_nav, "NAV_INVALID")
    if nav <= 0.0:
        raise PreparationStop("NAV_INVALID")
    if len(net) < 2:
        sharpe, sharpe_reason = None, "INSUFFICIENT_OBSERVATIONS"
    else:
        scale = stdev(net)
        sharpe, sharpe_reason = (None, "ZERO_VARIANCE") if scale == 0.0 else (math.fsum(net) / len(net) / scale, None)
    if gross_returns is None:
        drag, drag_reason = None, "MISSING_GROSS"
    else:
        gross = tuple(_finite(value, "GROSS_RETURN_INVALID") for value in gross_returns)
        if len(gross) != len(net) or any(value <= -1.0 for value in gross):
            raise PreparationStop("GROSS_RETURN_INVALID")
        drag = math.fsum(math.log1p(before) - math.log1p(after) for before, after in zip(gross, net)) / len(net)
        drag_reason = None
    if trials_complete is not True:
        trial_variance, trial_reason = None, "TRIALS_INCOMPLETE"
    elif trial_sharpe_variance is None:
        trial_variance, trial_reason = None, "TRIAL_VARIANCE_NOT_SUPPLIED"
    else:
        trial_variance = _finite(trial_sharpe_variance, "TRIAL_VARIANCE_INVALID")
        if trial_variance < 0.0:
            raise PreparationStop("TRIAL_VARIANCE_INVALID")
        trial_reason = None
    legacy = None if legacy_annualized is None else _finite(legacy_annualized, "LEGACY_ANNUALIZED_INVALID")
    return {
        "observations": len(net), "zero_observations": sum(value == 0.0 for value in net),
        "initial_nav": nav, "initial_nav_is_observation": False,
        "raw_net_sharpe_ddof1": sharpe, "sharpe_reason": sharpe_reason, "raw_net_volatility_ddof": 1,
        "legacy_annualized_field": legacy, "cost_log_drag": drag, "cost_log_drag_reason": drag_reason,
        "trial_sharpe_variance": trial_variance, "trial_reason": trial_reason,
        "period_outcome_is_trade_score": False, "trade_score": None,
        "live_ready": False, "no_order": True, "budget_effect": BUDGET_EFFECT_NONE,
        "research_frozen": False, "status": STATUS_DEVELOPMENT_ONLY,
    }


class AttemptLedger:
    """Register every attempt before a result exists. Failed rows stay in the cumulative list."""

    _TERMINAL = frozenset({"COMPUTED", "PARKED", "REJECTED", "INTERRUPTED"})

    def __init__(self) -> None:
        self._rows: dict[str, str] = {}
        self._order: list[str] = []
        self.preregistered = False

    def preregister(self) -> None:
        self.preregistered = True

    def register(self, attempt_id: str) -> None:
        if self.preregistered is not True:
            raise PreparationStop("PREREGISTRATION_REQUIRED")
        if not isinstance(attempt_id, str) or not attempt_id or attempt_id in self._rows:
            raise PreparationStop("ATTEMPT_ALREADY_REGISTERED")
        self._rows[attempt_id] = "REGISTERED"
        self._order.append(attempt_id)

    def finish(self, attempt_id: str, status: str) -> None:
        if status not in self._TERMINAL:
            raise PreparationStop("ATTEMPT_STATUS_INVALID")
        if self._rows.get(attempt_id) != "REGISTERED":
            raise PreparationStop("ATTEMPT_NOT_REGISTERED")
        self._rows[attempt_id] = status

    def cumulative(self, *, successes_only: bool = False) -> tuple[tuple[str, str], ...]:
        if successes_only:
            raise PreparationStop("SUCCESS_ONLY_SELECTION_FORBIDDEN")
        if any(status == "REGISTERED" for status in self._rows.values()):
            raise PreparationStop("ATTEMPT_UNFINISHED")
        return tuple((attempt_id, self._rows[attempt_id]) for attempt_id in self._order)

    def delete(self, attempt_id: str) -> None:
        raise PreparationStop("FAILED_ROW_DELETE_FORBIDDEN")

    def overwrite(self, attempt_id: str, status: str) -> None:
        raise PreparationStop("FAILED_ROW_OVERWRITE_FORBIDDEN")
