"""Fixed evaluation windows and aggregate metrics; never selects parameters."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import math
from statistics import mean
from typing import Mapping, Sequence

from .rs04_b3_cloud import walk_forward_plan
from .rs04_b3_config import CONFIG, config_identity
from .rs04_b3_runner import paired_giveback, path_metrics


def historical_plan(sessions: Sequence[date], *, feature_warmup: int = 0) -> dict:
    if not sessions or list(sessions) != sorted(set(sessions)):
        raise ValueError("SESSIONS_NOT_ORDERED")
    end = sessions[-1]
    # Last 24 calendar months of the archived sample, not today's trailing
    # 24 months. No date is chosen from performance and no parameter is fitted.
    anchor = end.replace(year=end.year-2, day=min(end.day, 28)) if end.month == 2 else end.replace(year=end.year-2)
    start = anchor+timedelta(days=1)
    start_i = next((i for i, d in enumerate(sessions) if d >= start), len(sessions))
    covered = sessions[0] <= start
    wf = walk_forward_plan(start_i, feature_warmup=feature_warmup)
    return {"config_sha256_frozen_before_read": config_identity(),
            "walk_forward": wf,
            "pseudo_holdout": {"start": start.isoformat(), "end": end.isoformat(),
                "start_index": start_i, "end_index": len(sessions),
                "status": "COVERED" if covered else "INSUFFICIENT_COVERAGE",
                "anchor": "LAST_ARCHIVED_SESSION_NOT_CURRENT_DATE", "weaker_evidence": True,
                "previously_seen_development": True, "untouched": False,
                "parameters_frozen_before_read": True, "overlaps_wfa": False},
            "forward_holdout": {"start": CONFIG.holdout_start, "end": CONFIG.holdout_end,
                "status": "NOT_YET_OBSERVABLE", "provisional": True, "opened": False}}


def evaluate_paths(paths: Mapping, *, start: int, end: int, cost_multiplier: float = 1.) -> dict:
    """Slice existing net paths, keeping prior holdings/NAV/costs/state intact."""
    if "baseline" not in paths or not 0 <= start < end or cost_multiplier not in CONFIG.cost_multipliers:
        raise ValueError("EVALUATION_WINDOW_INVALID")
    baseline_points = paths["baseline"].points[start:end]
    baseline_returns = [p["net_return"] for p in baseline_points]
    result, checks = {}, {}
    for mode, replay in paths.items():
        points = replay.points[start:end]
        if len(points) != end-start or [p["session"] for p in points] != [p["session"] for p in baseline_points]:
            raise ValueError("PAIRED_SESSIONS_NOT_ALIGNED")
        returns = [p["net_return"] for p in points]
        metrics = path_metrics(returns, turnover=math.fsum(p["turnover_one_side"] for p in points))
        paired = paired_giveback(baseline_returns, returns)
        metrics["giveback"] = paired
        grouped = defaultdict(list)
        for p in points:
            grouped[p["earning_regime"]].append(p["net_return"])
        metrics["regime_stats"] = {k: dict(path_metrics(v, turnover=0.),
            interpretation="CONDITIONAL_SESSIONS_NOT_A_CONTIGUOUS_INVESTABLE_PATH") for k, v in grouped.items()}
        metrics["regime_sessions"] = dict(Counter(p["earning_regime"] for p in points))
        metrics["regime_switches"] = sum(a["regime"] != b["regime"] for a, b in zip(points, points[1:]))
        metrics["combined_exposure"] = {"max_nominal": max(p["nominal"] for p in points),
            "mean_nominal": mean(p["nominal"] for p in points),
            "max_effective": max(p["effective"] for p in points),
            "mean_sizing_factor": mean(p["sizing_factor"] for p in points)}
        metrics["fees"] = {k: sum(p["fees"][k] for p in points) for k in ("commission", "slippage")}
        result[mode] = metrics
    base = result["baseline"]
    for mode in ("b3", "de_risk_only"):
        if mode not in result:
            continue
        candidate = result[mode]
        cagr_ok = (candidate["cagr"] >= CONFIG.cagr_retention*base["cagr"] if base["cagr"] > 0
                   else math.log1p(candidate["cagr"]) >= math.log1p(base["cagr"])+math.log(CONFIG.cagr_retention))
        gb = candidate["giveback"]
        reduction = gb["paired_reduction_normalized_by_baseline_peak"]
        relative = (reduction/gb["baseline_giveback"] if reduction is not None
                    and gb["baseline_giveback"] > 0 else None)
        threshold = CONFIG.stress_giveback_reduction if cost_multiplier == 2. else CONFIG.material_giveback_reduction
        checks[mode] = {"cagr_retention": cagr_ok,
            "mdd_reduction_20pct": base["max_drawdown"] > 0 and candidate["max_drawdown"] <= .8*base["max_drawdown"],
            "giveback_relative_reduction": relative,
            "giveback_material_reduction": None if relative is None else relative >= threshold,
            "giveback_required_reduction": threshold,
            "minimum_giveback_blocks": gb["eligible_blocks"] >= CONFIG.giveback_min_blocks,
            "turnover_within_limit": candidate["turnover_one_side"] <= CONFIG.max_turnover_ratio*base["turnover_one_side"]}
    return {"paths": result, "numeric_acceptance": checks,
            "acceptance": "DESCRIPTIVE_ONLY_PENDING_PARITY_OOS_TRIAL_AND_FEE_EVIDENCE",
            "continuous_ledger": True, "recommend_shadow": False}


def evaluate_windows(paths: Mapping, *, feature_warmup: int, cost_multiplier: float) -> dict:
    """Identical dates/cost scenario, no refit, reset or holdout selection."""
    sessions = [p["session"] for p in paths["baseline"].points]
    plan = historical_plan(sessions, feature_warmup=feature_warmup)
    wf = plan["walk_forward"]
    wf["results"] = [evaluate_paths(paths, start=f["test_start"], end=f["test_end"],
                                    cost_multiplier=cost_multiplier) for f in wf["folds"]]
    wf["executed"] = bool(wf["results"])
    ph = plan["pseudo_holdout"]
    ph["results"] = (evaluate_paths(paths, start=ph["start_index"], end=ph["end_index"],
                                    cost_multiplier=cost_multiplier) if ph["status"] == "COVERED" else None)
    return {**plan, "cost_multiplier": cost_multiplier,
            "full_history": evaluate_paths(paths, start=feature_warmup, end=len(sessions),
                                            cost_multiplier=cost_multiplier),
            "acceptance": "INSUFFICIENT_EVIDENCE", "recommend_shadow": False}
