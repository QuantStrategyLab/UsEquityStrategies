"""Retrospective paired-path diagnostic; never produces a trading decision."""
from __future__ import annotations

import csv
import argparse
from datetime import date
import hashlib
import math
import re
from pathlib import Path
import statistics
import json


def _read(path: Path):
    raw = path.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    result = []
    for row in rows:
        session, value = date.fromisoformat(row["date"]), float(row["return"])
        if not math.isfinite(value) or value <= -1:
            raise ValueError("INVALID_RETURN")
        if result and session <= result[-1][0]:
            raise ValueError("SESSIONS_NOT_STRICTLY_ORDERED")
        result.append((session, value))
    return hashlib.sha256(raw).hexdigest(), result


def evaluate_pair(baseline: Path, candidate: Path, *, through: date,
                  expected_baseline_sha256: str, expected_candidate_sha256: str,
                  calendar: dict | None = None):
    """Evaluate matching frozen paths without resetting positions at year start.

    Observation availability is an assumed retrospective convention, never
    evidence of historical publication. Without a bound calendar, a month
    matures only when a later month appears in both paths before the cutoff.
    Calendar mode uses its last monthly session and a fixed successor session;
    the date-only cutoff assumes that the requested session has ended.
    """
    if type(through) is not date or through.year != 2026:
        raise ValueError("2026_CUTOFF_REQUIRED")
    bhash, before = _read(baseline)
    chash, after = _read(candidate)
    if bhash != expected_baseline_sha256 or chash != expected_candidate_sha256:
        raise ValueError("FROZEN_PATH_DIGEST_MISMATCH")
    if not before or [x[0] for x in before] != [x[0] for x in after]:
        raise ValueError("MATCHED_SESSION_PATHS_REQUIRED")
    data = [(d, a, b) for (d, a), (_, b) in zip(before, after, strict=True) if d <= through]
    months = {}
    for session, a, b in data:
        months.setdefault((session.year, session.month), []).append((session, math.log1p(b) - math.log1p(a)))
    keys = sorted(months)
    completeness = "UNVERIFIED_CALENDAR_COVERAGE"
    calendar_binding = None
    if calendar is None:
        matured = keys[:-1]
        availability = {key: months[keys[index + 1]][0][0]
                        for index, key in enumerate(matured)}
    else:
        expected = [date.fromisoformat(day) for day in calendar["sessions"]]
        if (expected != sorted(set(expected)) or not expected or
                date.fromisoformat(calendar["coverage_start"]) > data[0][0] or
                date.fromisoformat(calendar["coverage_end"]) < date(through.year, 12, 31) or
                not isinstance(calendar.get("source"), str) or not calendar["source"] or
                not isinstance(calendar.get("source_sha256"), str) or
                re.fullmatch(r"[0-9a-f]{64}", calendar["source_sha256"]) is None):
            raise ValueError("CALENDAR_BINDING_INVALID")
        relevant = [day for day in expected if day <= through]
        if relevant != [day for day, _, _ in data]:
            raise ValueError("CALENDAR_SESSION_MISMATCH")
        last_by_month = {}
        for day in expected:
            last_by_month[(day.year, day.month)] = day
        matured = [key for key in keys if key in last_by_month and last_by_month[key] <= through]
        successor = dict(zip(expected, expected[1:]))
        if any(last_by_month[key] not in successor for key in matured):
            raise ValueError("CALENDAR_NEXT_SESSION_REQUIRED")
        availability = {key: successor[last_by_month[key]] for key in matured}
        completeness = "MATCHED_SELECTED_CALENDAR_SOURCE_NOT_EXCHANGE_ATTESTED"
        calendar_binding = {"source": calendar["source"], "source_sha256": calendar["source_sha256"],
                            "sessions_sha256": hashlib.sha256(json.dumps(calendar["sessions"],
                                separators=(",", ":")).encode()).hexdigest()}
    training = [math.fsum(v for _, v in months[key]) for key in matured if key[0] < 2026]
    if len(training) < 12:
        raise ValueError("INSUFFICIENT_COMPLETED_TRAINING_MONTHS")
    variance = statistics.variance(training)
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError("POSITIVE_TRAINING_VARIANCE_REQUIRED")
    posterior = []
    total = 0.0
    n = 0
    for key in matured:
        if key[0] != 2026:
            continue
        n += 1
        monthly = math.fsum(v for _, v in months[key])
        total += monthly
        posterior.append({"month": f"{key[0]:04d}-{key[1]:02d}",
                          "outcome_end": months[key][-1][0].isoformat(),
                          "assumed_available_at": availability[key].isoformat(),
                          "paired_log_difference": monthly,
                          "posterior_mean": total / (n + 1),
                          "posterior_std": math.sqrt(variance / (n + 1)),
                          "prior_sensitivity": {str(k): {"posterior_mean": total / (n + k),
                                                        "posterior_std": math.sqrt(variance / (n + k))}
                                                for k in (.25, 1.0, 4.0)}})
    ytd = [(d, a, b) for d, a, b in data if d.year == 2026]
    if not ytd:
        raise ValueError("MISSING_2026_OBSERVATIONS")
    log_base = math.fsum(math.log1p(a) for _, a, _ in ytd)
    log_candidate = math.fsum(math.log1p(b) for _, _, b in ytd)
    return {"scope": "retrospective_research_only", "executable": False, "promotion_eligible": False,
            "month_completeness": completeness, "calendar_binding": calendar_binding,
            "availability_status": "ASSUMED_NOT_HISTORICALLY_VERIFIED", "baseline_sha256": bhash,
            "candidate_sha256": chash, "first_session": ytd[0][0].isoformat(),
            "last_session": ytd[-1][0].isoformat(), "sessions": len(ytd),
            "baseline_ytd_return": math.expm1(log_base), "candidate_ytd_return": math.expm1(log_candidate),
            "paired_log_difference": log_candidate - log_base, "training_months": len(training),
            "training_variance": variance, "posterior": posterior}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--through", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    result = evaluate_pair(args.baseline, args.candidate, through=args.through,
                           expected_baseline_sha256=args.baseline_sha256,
                           expected_candidate_sha256=args.candidate_sha256)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
