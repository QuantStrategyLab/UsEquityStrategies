"""Disclosures for explicit v2 evaluators; legacy disclosures remain unchanged."""
from us_equity_strategies.research.kelly_disclosures import coverage_disclosure as legacy_coverage


def versioned_report_context(context, version, plan):
    return {**context, "limitations": (*context.get("limitations", ()),
            f"evaluator_version={version}; label_horizon_sessions={plan.label_horizon_sessions}; "
            f"embargo_sessions={plan.embargo_sessions}; metrics stitch admitted test sessions only")}


def coverage_disclosure(dates, folds):
    coverage = legacy_coverage(dates, folds)
    count = sum(end - start for _, _, start, end in folds)
    return {**coverage, "tested_sessions": count,
            "excluded_between_test_sessions": folds[-1][3] - folds[0][2] - count,
            "test_selection": "FOLD_TEST_INDICES_ONLY_HALF_OPEN_SESSION_INDEX"}
