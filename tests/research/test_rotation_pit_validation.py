"""Synthetic metadata only, never historical PIT qualification."""
from copy import deepcopy

import pytest

from us_equity_strategies.research.rotation_pit_validation import validate_rotation_pit_inputs


def _inputs():
    return {
        "enabled": True,
        "window_start": "2040-01-02T12:00:00+00:00",
        "window_end": "2040-03-01T12:00:00+00:00",
        "decision_at": "2040-02-01T12:00:00+00:00",
        "selected_symbols": ["SYNTH_A"],
        "snapshots": [
            {"effective_at": "2040-01-02T12:00:00+00:00",
             "visible_at": "2040-01-02T12:00:00+00:00", "members": ["SYNTH_A", "SYNTH_DELIST"]},
            {"effective_at": "2040-02-01T12:00:00+00:00",
             "visible_at": "2040-02-01T12:00:00+00:00", "members": ["SYNTH_A"]},
            {"effective_at": "2040-03-01T12:00:00+00:00",
             "visible_at": "2040-03-01T12:00:00+00:00", "members": ["SYNTH_FUTURE"]},
        ],
        "security_history": {
            symbol: {"listed_at": "2039-01-01T00:00:00+00:00",
                     "delisted_at": None, "terminal_value": None,
                     "coverage_start": "2040-01-02T12:00:00+00:00",
                     "coverage_end": "2040-03-01T12:00:00+00:00",
                     "delisting_complete": True, "corporate_actions_complete": True}
            for symbol in ["SYNTH_A", "SYNTH_DELIST", "SYNTH_FUTURE"]
        },
    }


def test_disabled_default_does_not_claim_validation():
    assert validate_rotation_pit_inputs().enabled is False


def test_visible_snapshot_boundary_and_known_delisting():
    inputs = _inputs()
    inputs["security_history"]["SYNTH_DELIST"].update(
        delisted_at="2040-01-31T12:00:00+00:00", terminal_value=0.0,
    )
    result = validate_rotation_pit_inputs(**inputs)
    assert result.enabled is True
    assert result.members == ("SYNTH_A",)
    assert result.live_ready is False


@pytest.mark.parametrize("mutation,reason", [
    ("first_snapshot", "first_snapshot"),
    ("unknown_visibility", "visible_at"),
    ("late_visibility", "future_member"),
    ("future_member", "future_member"),
    ("duplicate", "duplicate"),
    ("missing_security", "security_history"),
    ("delisting_unknown", "delisting"),
    ("corporate_actions_unknown", "corporate_actions"),
    ("terminal_unknown", "terminal_value"),
    ("terminal_nonfinite", "terminal_value"),
    ("already_delisted", "not_listed"),
    ("not_yet_listed", "not_listed"),
    ("unselected_future_listing", "not_listed"),
    ("removed_corporate_actions", "corporate_actions"),
    ("coverage_gap", "coverage"),
    ("naive_time", "timezone"),
])
def test_rejects_incomplete_or_noncausal_research_metadata(mutation, reason):
    inputs = deepcopy(_inputs())
    security = inputs["security_history"]["SYNTH_A"]
    if mutation == "first_snapshot":
        inputs["snapshots"][0]["visible_at"] = "2040-01-03T12:00:00+00:00"
    elif mutation == "unknown_visibility":
        inputs["snapshots"][0]["visible_at"] = None
    elif mutation == "late_visibility":
        inputs["snapshots"][1]["visible_at"] = "2040-02-02T12:00:00+00:00"
        inputs["snapshots"][1]["members"] = ["SYNTH_FUTURE"]
        inputs["selected_symbols"] = ["SYNTH_FUTURE"]
    elif mutation == "future_member":
        inputs["selected_symbols"] = ["SYNTH_FUTURE"]
    elif mutation == "duplicate":
        inputs["snapshots"][0]["members"].append("synth_a")
    elif mutation == "missing_security":
        del inputs["security_history"]["SYNTH_DELIST"]
    elif mutation == "delisting_unknown":
        security["delisting_complete"] = False
    elif mutation == "corporate_actions_unknown":
        security["corporate_actions_complete"] = None
    elif mutation in {"terminal_unknown", "terminal_nonfinite", "already_delisted"}:
        security["delisted_at"] = "2040-01-31T12:00:00+00:00"
        security["terminal_value"] = {"terminal_unknown": None, "terminal_nonfinite": float("nan"),
                                      "already_delisted": 0.0}[mutation]
    elif mutation == "not_yet_listed":
        security["listed_at"] = "2040-02-02T12:00:00+00:00"
    elif mutation == "unselected_future_listing":
        inputs["security_history"]["SYNTH_DELIST"]["listed_at"] = "2040-01-03T00:00:00+00:00"
    elif mutation == "removed_corporate_actions":
        inputs["security_history"]["SYNTH_DELIST"]["corporate_actions_complete"] = False
    elif mutation == "coverage_gap":
        security["coverage_start"] = "2040-01-03T12:00:00+00:00"
    elif mutation == "naive_time":
        inputs["decision_at"] = "2040-02-01T12:00:00"
    with pytest.raises(ValueError, match=reason):
        validate_rotation_pit_inputs(**inputs)
