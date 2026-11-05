"""Terminal bookkeeping for incomplete frozen retrospective studies."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore


def record_incomplete_trials(output_dir, active_key, *, interrupted=False):
    """Close every registered, unlinked trial without implying calculation success."""
    output_dir = Path(output_dir)
    path = output_dir / "trial_registry.json"
    if not path.exists():
        return None
    registry = json.loads(path.read_text())
    store = PerformanceStore(local_root=output_dir / "performance_store")
    outcomes = []
    for item in registry["trials"]:
        profile = "range_rsi2_" + item["symbol"].lower()
        trial = store.load_research_trial("us_equity", profile, item["trial_id"])
        if trial is None:
            outcomes.append({"trial_id": item["trial_id"], "status": "REGISTRATION_MISSING"})
            continue
        if trial.status is not ResearchTrialStatus.STARTED:
            outcomes.append({"trial_id": item["trial_id"], "status": trial.status.value})
            continue
        key = (item["symbol"], item["strategy"], item["cost"], item["lag"])
        failed = not interrupted and active_key is not None and key == active_key
        terminal = replace(trial, status=ResearchTrialStatus.FAILED if failed else ResearchTrialStatus.ABORTED,
                           reason_code="scenario_failed" if failed else "study_incomplete")
        store.save_research_trial(terminal)
        if store.load_research_trial(trial.domain, trial.strategy_profile, trial.trial_id) != terminal:
            raise ValueError("failure terminal readback mismatch")
        outcomes.append({"trial_id": item["trial_id"], "status": terminal.status.value})
    result = {"status": "INCOMPLETE", "promotion_eligible": False, "outcomes": outcomes}
    (output_dir / "trial_failure_report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result
