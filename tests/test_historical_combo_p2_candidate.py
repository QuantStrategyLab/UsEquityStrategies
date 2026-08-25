from __future__ import annotations

from copy import deepcopy

import pytest

from us_equity_strategies.research.historical_combo_p2_candidate import (
    FROZEN_RESEARCH_CANDIDATE,
    HISTORICAL_COMBO_P2_CANDIDATE_SCHEMA,
    PORTFOLIO_RISK_BUDGET_SCHEMA,
    HistoricalComboP2CandidateError,
    build_historical_combo_p2_candidate,
    calculate_historical_combo_p2_candidate_sha256,
    validate_historical_combo_p2_candidate,
)


def _candidate(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "p1_input_sha256": "1" * 64,
        "candidate_id": "us-equity-three-sleeve-baseline",
        "candidate_revision": "a" * 40,
        "config_sha256": "2" * 64,
        "selection_window": {"start": "2015-01-01", "end": "2021-12-31"},
        "holdout_window": {"start": "2022-01-03", "end": "2026-08-19"},
        "legs": [
            {
                "leg_id": "global-etf",
                "strategy_id": "global-etf-rotation",
                "strategy_revision": "b" * 40,
                "config_sha256": "3" * 64,
                "target_weight": 0.50,
            },
            {
                "leg_id": "russell",
                "strategy_id": "russell-top50-leader-rotation",
                "strategy_revision": "c" * 40,
                "config_sha256": "4" * 64,
                "target_weight": 0.30,
            },
            {
                "leg_id": "smart-dca",
                "strategy_id": "nasdaq-sp500-fixed-dca",
                "strategy_revision": "d" * 40,
                "config_sha256": "5" * 64,
                "target_weight": 0.20,
            },
        ],
        "risk_budget": {
            "schema_version": PORTFOLIO_RISK_BUDGET_SCHEMA,
            "policy_sha256": "6" * 64,
        },
    }
    values.update(overrides)
    return build_historical_combo_p2_candidate(**values)


def test_builds_a_frozen_non_executable_candidate_with_a_self_digest() -> None:
    result = _candidate()

    assert result["schema_version"] == HISTORICAL_COMBO_P2_CANDIDATE_SCHEMA
    assert result["candidate_state"] == FROZEN_RESEARCH_CANDIDATE
    assert result["promotion_recommendation"] is None
    assert result["p4_paper_authorized"] is False
    assert result["p5_shadow_authorized"] is False
    assert result["p6_live_authorized"] is False
    assert result["candidate_sha256"] == calculate_historical_combo_p2_candidate_sha256(result)
    assert [leg["leg_id"] for leg in result["legs"]] == [
        "global-etf",
        "russell",
        "smart-dca",
    ]


def test_build_is_deterministic_and_does_not_mutate_callers() -> None:
    arguments = {
        "p1_input_sha256": "1" * 64,
        "candidate_id": "us-equity-three-sleeve-baseline",
        "candidate_revision": "a" * 40,
        "config_sha256": "2" * 64,
        "selection_window": {"start": "2015-01-01", "end": "2021-12-31"},
        "holdout_window": {"start": "2022-01-03", "end": "2026-08-19"},
        "legs": [
            {
                "leg_id": "global-etf",
                "strategy_id": "global-etf-rotation",
                "strategy_revision": "b" * 40,
                "config_sha256": "3" * 64,
                "target_weight": 0.5,
            },
            {
                "leg_id": "russell",
                "strategy_id": "russell-top50-leader-rotation",
                "strategy_revision": "c" * 40,
                "config_sha256": "4" * 64,
                "target_weight": 0.3,
            },
            {
                "leg_id": "smart-dca",
                "strategy_id": "nasdaq-sp500-fixed-dca",
                "strategy_revision": "d" * 40,
                "config_sha256": "5" * 64,
                "target_weight": 0.2,
            },
        ],
        "risk_budget": {"schema_version": PORTFOLIO_RISK_BUDGET_SCHEMA, "policy_sha256": "6" * 64},
    }
    original = deepcopy(arguments)

    first = build_historical_combo_p2_candidate(**arguments)
    second = build_historical_combo_p2_candidate(**arguments)

    assert arguments == original
    assert first == second


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"holdout_window": {"start": "2021-12-31", "end": "2026-08-19"}},
            "selection and holdout windows must not overlap",
        ),
        (
            {
                "legs": [
                    {
                        "leg_id": "global-etf",
                        "strategy_id": "global-etf-rotation",
                        "strategy_revision": "b" * 40,
                        "config_sha256": "3" * 64,
                        "target_weight": 0.4,
                    },
                    {
                        "leg_id": "russell",
                        "strategy_id": "russell-top50-leader-rotation",
                        "strategy_revision": "c" * 40,
                        "config_sha256": "4" * 64,
                        "target_weight": 0.3,
                    },
                    {
                        "leg_id": "smart-dca",
                        "strategy_id": "nasdaq-sp500-fixed-dca",
                        "strategy_revision": "d" * 40,
                        "config_sha256": "5" * 64,
                        "target_weight": 0.2,
                    },
                ]
            },
            "combo leg target weights must sum to one",
        ),
        (
            {
                "legs": [
                    {
                        "leg_id": "russell",
                        "strategy_id": "russell-top50-leader-rotation",
                        "strategy_revision": "c" * 40,
                        "config_sha256": "4" * 64,
                        "target_weight": 0.5,
                    },
                    {
                        "leg_id": "global-etf",
                        "strategy_id": "global-etf-rotation",
                        "strategy_revision": "b" * 40,
                        "config_sha256": "3" * 64,
                        "target_weight": 0.5,
                    },
                ]
            },
            "combo legs must be uniquely sorted",
        ),
    ],
)
def test_rejects_ambiguous_or_tuned_candidate_inputs(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(HistoricalComboP2CandidateError, match=message):
        _candidate(**kwargs)


def test_rejects_tampering_or_any_execution_capability() -> None:
    tampered = _candidate()
    tampered["p5_shadow_authorized"] = True
    with pytest.raises(HistoricalComboP2CandidateError, match="cannot authorize execution"):
        validate_historical_combo_p2_candidate(tampered)

    tampered = _candidate()
    tampered["legs"][0]["target_weight"] = 0.49
    tampered["legs"][1]["target_weight"] = 0.31
    with pytest.raises(HistoricalComboP2CandidateError, match="digest mismatch"):
        validate_historical_combo_p2_candidate(tampered)
