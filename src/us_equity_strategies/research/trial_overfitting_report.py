"""Opt-in, research-only trial-set overfitting report: DSR and PBO (RS-02).

Consumes QPK ``quant_platform_kit.research_stats`` (``deflated_sharpe_ratio``,
``probability_of_backtest_overfitting``) when the installed QPK provides it.
The UES package pin is **not** changed for this: when the installed QPK lacks
``research_stats`` (or one of the functions), the affected metric is
``UNCOMPUTABLE`` with reason ``QPK_RESEARCH_STATS_UNAVAILABLE`` -- never zero
and never a locally re-implemented substitute.

Scope and honesty rules:

- Only journaled **synthetic** TQQQ SMA ledgers are accepted by the store
  adapter; real/historical ledgers raise ``REAL_RESEARCH_DATA_UNQUALIFIED``.
- All trial ids of the study must be supplied, including failed/rejected/
  aborted ones. Those have no return path; they are counted and disclosed but
  not imputed. DSR uses the succeeded trials' per-session Sharpes and
  ``n_effective_trials = succeeded count``.
- Trials are compared on exactly the same sessions. Without an explicit
  common ``start_session``/``end_session`` that yields identical session
  sequences, both metrics are ``UNCOMPUTABLE`` (no silent intersection).
- Nothing is simulated, selected, persisted, promoted or authorized. Runtime
  entrypoints and strategies never import this module.
"""
from __future__ import annotations

import importlib
import math
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

REPORT_VERSION = "trial_set_overfitting_report_v1"
STATUS_COMPUTED = "COMPUTED"
STATUS_UNCOMPUTABLE = "UNCOMPUTABLE"
UNAVAILABLE = "QPK_RESEARCH_STATS_UNAVAILABLE"
PERIODS_PER_YEAR = 252


def _qpk_function(name: str) -> Any | None:
    try:
        module = importlib.import_module("quant_platform_kit.research_stats")
    except ImportError:
        return None
    return getattr(module, name, None)


def _metric(status: str, value: float | None, reason: str | None, details: Mapping | None = None) -> dict:
    return {"status": status, "value": value, "reason_code": reason, "details": dict(details or {})}


def _unavailable() -> dict:
    return _metric(STATUS_UNCOMPUTABLE, None, UNAVAILABLE)


def _from_stat(result: Any) -> dict:
    value = result.value if result.status == STATUS_COMPUTED else None
    return _metric(result.status, value, result.reason_code, result.details)


def _per_session_sharpe(excess: Sequence[float]) -> float | None:
    n = len(excess)
    if n < 2:
        return None
    mean = math.fsum(excess) / n
    variance = math.fsum((x - mean) ** 2 for x in excess) / (n - 1)
    if not math.isfinite(variance) or variance <= 0.0:
        return None
    return mean / math.sqrt(variance)


def compute_trial_set_overfitting(
    dated_returns_by_trial: Mapping[str, Sequence[tuple[date, float]]], *,
    selected_trial_id: str, recorded_trial_count: int, annual_risk_free_rate: float,
    pbo_splits: int = 16, synthetic: bool,
) -> dict:
    """Pure DSR/PBO report over succeeded trials' dated session net returns.

    ``annual_risk_free_rate`` is an annual simple decimal divided by 252 per
    session (the journal's convention) and is removed before Sharpe/DSR. PBO
    uses CSCV on the same excess returns with the per-period Sharpe metric.
    """
    if synthetic is not True:
        raise ValueError("REAL_RESEARCH_DATA_UNQUALIFIED")
    if type(annual_risk_free_rate) not in (int, float) or not math.isfinite(annual_risk_free_rate):
        raise ValueError("RESEARCH_RATE_INVALID")
    if not isinstance(dated_returns_by_trial, Mapping) or selected_trial_id not in dated_returns_by_trial:
        raise ValueError("SELECTED_TRIAL_NOT_SUCCEEDED")
    succeeded = len(dated_returns_by_trial)
    if (isinstance(recorded_trial_count, bool) or not isinstance(recorded_trial_count, int)
            or recorded_trial_count < succeeded):
        raise ValueError("RECORDED_TRIAL_COUNT_INVALID")

    rf = annual_risk_free_rate / PERIODS_PER_YEAR
    sessions = {tid: tuple(d for d, _ in rows) for tid, rows in dated_returns_by_trial.items()}
    aligned = len(set(sessions.values())) == 1
    excess = {tid: [float(r) - rf for _, r in rows] for tid, rows in dated_returns_by_trial.items()}

    dsr_fn = _qpk_function("deflated_sharpe_ratio")
    pbo_fn = _qpk_function("probability_of_backtest_overfitting")
    if not aligned:
        dsr = _metric(STATUS_UNCOMPUTABLE, None, "TRIAL_SESSIONS_NOT_ALIGNED")
        pbo = _metric(STATUS_UNCOMPUTABLE, None, "TRIAL_SESSIONS_NOT_ALIGNED")
    else:
        if dsr_fn is None:
            dsr = _unavailable()
        else:
            sharpes = {tid: _per_session_sharpe(xs) for tid, xs in excess.items()}
            undefined = sorted(tid for tid, s in sharpes.items() if s is None)
            if undefined:
                dsr = _metric(STATUS_UNCOMPUTABLE, None, "TRIAL_SHARPE_UNDEFINED", {"trials": undefined})
            else:
                dsr = _from_stat(dsr_fn(excess[selected_trial_id], list(sharpes.values()),
                                        n_effective_trials=succeeded))
        pbo = _unavailable() if pbo_fn is None else _from_stat(
            pbo_fn(excess, n_splits=pbo_splits, metric="sharpe"))

    observation_counts = sorted({len(v) for v in sessions.values()})
    return {
        "research_only": True, "synthetic": True, "promotion_eligible": False,
        "live_ready": False, "size_zero_required": True, "no_order": True,
        "report_profile": REPORT_VERSION,
        "selected_trial_id": selected_trial_id,
        "recorded_trial_count": recorded_trial_count,
        "succeeded_trial_count": succeeded,
        "not_succeeded_trial_count": recorded_trial_count - succeeded,
        "sessions_aligned": aligned,
        "observation_counts": observation_counts,
        "metrics": {"dsr": dsr["value"], "pbo": pbo["value"]},
        "metric_status": {"dsr": dsr["status"], "pbo": pbo["status"]},
        "metric_reason": {"dsr": dsr["reason_code"], "pbo": pbo["reason_code"]},
        "metric_details": {"dsr": dsr["details"], "pbo": pbo["details"]},
        "evaluation_contract": {
            "version": REPORT_VERSION, "role": "REPORT_ONLY",
            "unit": "STRATEGY_ACCOUNT_SESSION_SIMPLE_NET_RETURN_MINUS_RF_PER_SESSION",
            "rf": {"annual_simple_decimal": annual_risk_free_rate, "per_session": rf},
            "annual_rate_conversion": "DIVIDE_BY_252_NOT_COMPOUND_RATE_CONVERSION",
            "dsr": "QPK_RESEARCH_STATS_DEFLATED_SHARPE_PER_SESSION_DDOF1",
            "dsr_trial_count": "SUCCEEDED_TRIALS_ONLY_NOT_SUCCEEDED_DISCLOSED_NOT_IMPUTED",
            "pbo": "QPK_RESEARCH_STATS_CSCV_PER_SESSION_SHARPE",
            "pbo_splits": pbo_splits,
            "session_alignment": "IDENTICAL_SESSION_SEQUENCE_REQUIRED_NO_INTERSECTION",
            "qpk_research_stats_available": {"dsr": dsr_fn is not None, "pbo": pbo_fn is not None},
            "undefined_policy": "NONE_WITH_REASON_NO_ZERO_FILL",
            "trial_dependence": "NOT_ESTIMATED_N_EFFECTIVE_EQUALS_SUCCEEDED_COUNT",
        },
    }


def report_journaled_tqqq_trial_set_overfitting(
    *, store: Any, trial_ids: Sequence[str], selected_trial_id: str,
    annual_risk_free_rate: float, start_session: date | None = None,
    end_session: date | None = None, pbo_splits: int = 16,
) -> dict:
    """Read a journaled synthetic TQQQ SMA study and report DSR/PBO; no writes."""
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    from .tqqq_core_trial_journal import PROFILE

    if (not isinstance(store, PerformanceStore) or store.cloud_bucket
            or not isinstance(store.local_root, Path) or store.local_root.is_symlink()
            or not store.local_root.is_dir()):
        raise ValueError("LOCAL_RESEARCH_STORE_REQUIRED")
    ids = list(trial_ids)
    if not ids or len(set(ids)) != len(ids) or any(not isinstance(t, str) or not t for t in ids):
        raise ValueError("TRIAL_IDS_INVALID")
    if selected_trial_id not in ids:
        raise ValueError("SELECTED_TRIAL_NOT_IN_STUDY")
    for bound in (start_session, end_session):
        if bound is not None and type(bound) is not date:
            raise ValueError("RESEARCH_WINDOW_INVALID")

    dated: dict[str, list[tuple[date, float]]] = {}
    statuses: dict[str, str] = {}
    for trial_id in ids:
        record = store.load_research_trial("us_equity", PROFILE, trial_id)
        if record is None:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        statuses[trial_id] = record.status.value
        if record.status is not ResearchTrialStatus.SUCCEEDED:
            continue
        ledger = store.load_research_ledger(record.domain, record.strategy_profile,
                                            record.trial_id, record.run_id, record.param_version)
        if ledger.synthetic is not True:
            raise ValueError("REAL_RESEARCH_DATA_UNQUALIFIED")
        if ledger.calendar_id != "XNYS" or ledger.periods_per_year != PERIODS_PER_YEAR:
            raise ValueError("RESEARCH_BASIS_INVALID")
        if any(day.external_cashflow != 0.0 for day in ledger.days):
            raise ValueError("RESEARCH_EXTERNAL_FLOWS_UNSUPPORTED")
        sessions = [day.session_date for day in ledger.days]
        if ((start_session is not None and start_session not in sessions)
                or (end_session is not None and end_session not in sessions)
                or (start_session is not None and end_session is not None
                    and start_session > end_session)):
            raise ValueError("RESEARCH_WINDOW_INVALID")
        dated[trial_id] = [
            (day.session_date, day.daily_return) for day in ledger.days
            if (start_session is None or day.session_date >= start_session)
            and (end_session is None or day.session_date <= end_session)]
    if statuses[selected_trial_id] != ResearchTrialStatus.SUCCEEDED.value:
        raise ValueError("RESEARCH_TRIAL_NOT_SUCCEEDED:" + statuses[selected_trial_id])

    report = compute_trial_set_overfitting(
        dated, selected_trial_id=selected_trial_id, recorded_trial_count=len(ids),
        annual_risk_free_rate=annual_risk_free_rate, pbo_splits=pbo_splits, synthetic=True)
    report.update({
        "strategy_profile": PROFILE,
        "trial_statuses": statuses,
        "window": {"start_session": None if start_session is None else start_session.isoformat(),
                   "end_session": None if end_session is None else end_session.isoformat()},
        "scope": "JOURNALED_SYNTHETIC_TRIAL_SET_NOT_SELECTION_OR_QUALIFICATION",
    })
    return report
