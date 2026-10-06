"""Internal TQQQ trial transaction; single-caller replay protection only."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from typing import Any

from quant_platform_kit.strategy_lifecycle.contracts import (
    BacktestResult, ResearchDailyLedger, ResearchTrialRecord, ResearchTrialStatus,
)
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore


def _record_trial_failure(store: PerformanceStore, started: ResearchTrialRecord,
                          status: ResearchTrialStatus, reason: str,
                          error: BaseException, *, terminal_attempted: bool) -> None:
    """Best effort only; never replace an exception or an existing immutable terminal."""
    try:
        if terminal_attempted:
            current = store.load_research_trial(started.domain, started.strategy_profile, started.trial_id)
            if current is None or current.status is not ResearchTrialStatus.STARTED:
                error.add_note("research_terminal_state_unverified" if current is None else "research_terminal_already_recorded")
                return
        terminal = replace(started, status=status, reason_code=reason, run_id=None, param_version=None)
        store.save_research_trial(terminal)
        if store.load_research_trial(started.domain, started.strategy_profile, started.trial_id) != terminal:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
    except BaseException:
        error.add_note("research_terminal_write_failed")


def _journal_trial(
    started: ResearchTrialRecord, store: PerformanceStore, frozen_params: str,
    simulate: Callable[[dict], Any],
    adapt: Callable[[ResearchTrialRecord, dict, Any], tuple[BacktestResult, ResearchDailyLedger]],
) -> tuple[ResearchTrialRecord, Any | None]:
    """Run an input-owning TQQQ caller's slot, without a distributed claim lock.

    Callbacks are internal wiring, never caller-provided research outputs.
    A write that raises may have committed; no compensating start terminal is safe.
    """
    previous = store.load_research_trial(started.domain, started.strategy_profile, started.trial_id)
    store.save_research_trial(started)
    stage = "start_readback"
    terminal_attempted = False
    try:
        readback = store.load_research_trial(started.domain, started.strategy_profile, started.trial_id)
        if readback is None:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        if previous is not None or readback.status is not ResearchTrialStatus.STARTED:
            return readback, None
        if readback != started:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        params = json.loads(frozen_params)
        if readback.actual_params != params:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        stage = "simulation"
        output = simulate(params)
        if params != json.loads(frozen_params) or readback.actual_params != json.loads(frozen_params):
            raise ValueError("SIMULATOR_PARAMS_CHANGED")
        stage = "ledger"
        result, ledger = adapt(readback, params, output)
        terminal = replace(readback, status=ResearchTrialStatus.SUCCEEDED, reason_code="",
                           run_id=result.run_id, param_version=result.param_version)
        stage = "persistence"
        store.save_backtest_result(result)
        store.save_research_ledger(ledger)
        terminal_attempted = True
        store.save_research_trial(terminal)
        if store.load_research_trial(started.domain, started.strategy_profile, started.trial_id,
                                     run_id=result.run_id, param_version=result.param_version) != terminal:
            raise ValueError("RESEARCH_TRIAL_READBACK_INVALID")
        return terminal, output
    except (KeyboardInterrupt, SystemExit) as exc:
        _record_trial_failure(store, started, ResearchTrialStatus.ABORTED, "trial_interrupted", exc,
                              terminal_attempted=terminal_attempted)
        raise
    except Exception as exc:
        rejected = isinstance(exc, ValueError) and stage in {"simulation", "ledger"}
        status = ResearchTrialStatus.REJECTED if rejected else ResearchTrialStatus.FAILED
        reason = {"start_readback": "journal_readback_failed",
                  "simulation": "simulation_rejected" if rejected else "simulation_failed",
                  "ledger": "ledger_rejected" if rejected else "ledger_failed",
                  "persistence": "persistence_failed"}[stage]
        _record_trial_failure(store, started, status, reason, exc, terminal_attempted=terminal_attempted)
        raise
