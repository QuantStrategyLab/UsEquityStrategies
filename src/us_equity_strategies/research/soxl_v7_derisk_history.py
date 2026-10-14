"""UBPS v2 labelled close-return model and independent calibration gate.

Pure computation, no fetches. This cannot manufacture tradable OHLC, option
chains, BOXX history or plugin PIT inputs from an SOX/SOXX close series.
"""
from __future__ import annotations

from datetime import date
import math
from statistics import mean, stdev, variance
from typing import Mapping, Sequence

MODEL_ID = "soxl_3x_daily_reset_ubps_v2_v1"
STRESS_PERIODS = {"2000": ("2000-01-01", "2002-12-31"), "2008": ("2008-01-01", "2008-12-31"),
                  "2020": ("2020-01-01", "2020-12-31"), "2022": ("2022-01-01", "2022-12-31")}


def synthetic_return(underlying: float, *, rate: float, expense: float,
                     spread: float, drag: float, days: int,
                     spread_stress: float = 0., drag_multiplier: float = 1.) -> float:
    values = (underlying, rate, expense, spread, drag, spread_stress, drag_multiplier)
    if (any(not math.isfinite(x) for x in values) or underlying <= -1
            or isinstance(days, bool) or not isinstance(days, int) or days <= 0
            or expense < 0 or spread < 0 or spread_stress not in (0., .01, .03)
            or drag_multiplier not in (1., 2.)):
        raise ValueError("HISTORY_MODEL_INPUT_INVALID")
    stressed_drag = drag*drag_multiplier if drag >= 0 else drag
    result = 3*underlying-expense*days/365.-2*(rate+spread+spread_stress)*days/360.-stressed_drag
    if result <= -1:
        raise ValueError("SYNTHETIC_PRODUCT_TERMINATED_NO_REVIVAL")
    return result


def _metrics(real, model, dates, initial_date):
    residual = [m-r for r, m in zip(real, model)]
    denom = sum((r-mean(real))**2 for r in real)
    if len(real) < 2 or not denom:
        raise ValueError("CALIBRATION_UNCOMPUTABLE")
    def path(rs):
        wealth = peak = 1.
        dd = 0.
        for r in rs:
            wealth *= 1+r
            peak = max(peak, wealth)
            dd = max(dd, 1-wealth/peak)
        return wealth, dd
    rw, rd = path(real)
    mw, md = path(model)
    year_fraction = (dates[-1]-initial_date).days/365.25
    if year_fraction <= 0:
        raise ValueError("CALIBRATION_DATES_INVALID")
    rc, mc = rw**(1/year_fraction)-1, mw**(1/year_fraction)-1
    rpath = mpath = 1.
    drift = 0.
    for r, m in zip(real, model):
        rpath *= 1+r
        mpath *= 1+m
        drift = max(drift, abs(mpath/rpath-1))
    return {"observations": len(real), "predictive_r2": 1-sum(d*d for d in residual)/denom,
            "te_ann": stdev(residual)*math.sqrt(252), "bias_ann": 252*mean(residual),
            "rmse": math.sqrt(mean(d*d for d in residual)),
            "max_abs_daily_error": max(abs(d) for d in residual),
            "real_vol_ann": stdev(real)*math.sqrt(252), "cagr_difference": mc-rc,
            "mdd_difference": md-rd, "max_abs_wealth_drift": drift}


def _passes(m, *, crisis=False):
    if crisis:
        return (m["observations"] >= 63 and m["predictive_r2"] >= .98
                and m["te_ann"] <= .075 and m["max_abs_daily_error"] <= .015
                and abs(m["mdd_difference"]) <= .05)
    return (m["predictive_r2"] >= .99 and m["te_ann"] <= min(.05, .1*m["real_vol_ann"])
            and abs(m["bias_ann"]) <= .01 and abs(m["cagr_difference"]) <= .02
            and abs(m["mdd_difference"]) <= .05 and m["max_abs_wealth_drift"] <= .05)


def calibrate(real: Sequence[float], model_before_drag: Sequence[float], dates: Sequence[date], *,
              input_evidence: Mapping, train_sessions: int = 504) -> dict:
    """One constant drag fit on the first 504, never re-fit validation.

    Expected evidence must be established by the cloud data adapter, never a
    self-attested manifest boolean. Status here is mathematical mapping quality,
    independent of strategy/financial acceptance and trading permission.
    """
    if (len(real) != len(model_before_drag) or len(real) != len(dates)
            or list(dates) != sorted(set(dates)) or train_sessions < 504
            or any(not math.isfinite(r) or r <= -1 for r in (*real, *model_before_drag))):
        raise ValueError("CALIBRATION_INPUT_INVALID")
    required = ("license", "return_definition", "nav_and_market_basis", "historical_rates",
                "historical_fees", "calendar", "corporate_actions", "index_regimes", "pit_limits")
    missing = [k for k in required if not input_evidence.get(k)]
    if len(real) < train_sessions+252:
        return {"status": "INCONCLUSIVE", "reason": "AT_LEAST_504_TRAIN_252_VALIDATION_REQUIRED"}
    if missing:
        return {"status": "INCONCLUSIVE", "reason": "INPUT_EVIDENCE_MISSING", "missing": missing}
    expected = input_evidence.get("expected_validation_sessions")
    explanations = input_evidence.get("missing_session_explanations")
    observed = len(real)-train_sessions
    if (not isinstance(expected, int) or isinstance(expected, bool) or expected < observed
            or observed/expected < .99 or (expected > observed and (
                not isinstance(explanations, (list, tuple)) or len(explanations) != expected-observed
                or any(not e for e in explanations)))):
        return {"status": "INCONCLUSIVE", "reason": "VALIDATION_COVERAGE_NOT_QUALIFIED"}
    drag = mean(m-r for r, m in zip(real[:train_sessions], model_before_drag[:train_sessions]))
    model = [m-drag for m in model_before_drag]
    if any(m <= -1 for m in model):
        return {"status": "FAIL", "reason": "SYNTHETIC_PRODUCT_TERMINATED_NO_REVIVAL"}
    def measure(indices):
        indices = list(indices)
        return _metrics([real[i] for i in indices], [model[i] for i in indices],
                        [dates[i] for i in indices], dates[indices[0]-1])
    try:
        overall = measure(range(train_sessions, len(real)))
        blocks = [measure(range(i, i+252)) for i in range(train_sessions, len(real)-251, 252)]
        crises = {}
        missing_crises = []
        for year in (2020, 2022):
            indices = [i for i in range(train_sessions, len(real)) if dates[i].year == year]
            if len(indices) >= 63:
                crises[str(year)] = measure(indices)
            else:
                missing_crises.append(str(year))
        # Regime slices are supplied as complete date boundaries, pre-registered
        # before reading validation. Do not optimize regimes to pass calibration.
        regimes = {}
        for name, start, end in input_evidence.get("validation_regime_windows", ()):
            indices = [i for i in range(train_sessions, len(real)) if start <= dates[i] <= end]
            if len(indices) < 63:
                missing_crises.append(name)
            else:
                regimes[name] = measure(indices)
    except (ValueError, OverflowError):
        return {"status": "INCONCLUSIVE", "reason": "CALIBRATION_UNCOMPUTABLE"}
    passed = (_passes(overall) and all(_passes(m) for m in blocks)
              and all(_passes(m, crisis=True) for m in (*crises.values(), *regimes.values())))
    status = "FAIL" if not passed else "INCONCLUSIVE" if (missing_crises or not regimes) else "PASS"
    return {"status": status, "model_id": MODEL_ID, "frozen_daily_drag": drag,
            "train_sessions": train_sessions, "validation_sessions": observed,
            "overall": overall, "blocks_252": blocks, "crisis_validation": crises,
            "regime_validation": regimes, "missing_crisis_or_regime_validation": missing_crises,
            "regime_evidence_missing": not bool(regimes), "strategy_pass": False,
            "interpretation": "LABELLED_RETROSPECTIVE_MODEL_NOT_PIT_OR_PRODUCT_TRACK_RECORD"}


def splice_history(synthetic: Mapping[date, float], real: Mapping[date, float], *,
                   anchor: date, model_manifest: Mapping, calibration_report: Mapping,
                   expected_sessions: Sequence[date]) -> tuple[dict, ...]:
    """Return labels only; real-only must be independently rerun from cash.

    Anchor return is an explicit synthetic bridge. Every later real session must
    be present in the qualified calendar before this helper is called.
    """
    required = ("underlying", "target_index_regime", "rate_source", "expense_source", "calendar_ref", "calibration_sha256")
    if calibration_report.get("status") != "PASS":
        raise ValueError("CALIBRATION_GATE_NOT_PASSED")
    if any(not model_manifest.get(k) for k in required) or anchor not in synthetic:
        raise ValueError("SPLICE_IDENTITY_MISSING")
    if (model_manifest.get("leverage") != 3.
            or any(not isinstance(model_manifest.get(k), (int, float))
                   or not math.isfinite(model_manifest[k]) for k in ("expense_ratio", "financing_spread", "drag"))):
        raise ValueError("SPLICE_MODEL_PARAMETERS_MISSING")
    if any(d <= anchor for d in real) or not real:
        raise ValueError("REAL_RETURNS_MUST_START_AFTER_ANCHOR")
    actual_sessions = {d for d in synthetic if d <= anchor} | set(real)
    if (not expected_sessions or list(expected_sessions) != sorted(set(expected_sessions))
            or set(expected_sessions) != actual_sessions):
        raise ValueError("SPLICE_CALENDAR_COVERAGE_INCOMPLETE")
    result = []
    for d in sorted(set(synthetic) | set(real)):
        if d > anchor and d not in real:
            raise ValueError("REAL_HISTORY_GAP_NO_MODEL_FILL")
        value = synthetic[d] if d <= anchor else real[d]
        if not math.isfinite(value) or value <= -1:
            raise ValueError("HISTORY_RETURN_INVALID_OR_TERMINATED")
        result.append({"session": d, "return": value,
            "source_type": "synthetic_history" if d < anchor else "synthetic_bridge" if d == anchor else "real",
            "model_id": MODEL_ID, "model_manifest": dict(model_manifest)})
    return tuple(result)
