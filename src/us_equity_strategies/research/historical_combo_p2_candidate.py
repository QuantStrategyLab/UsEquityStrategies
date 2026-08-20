"""Immutable P2 descriptor for one historical multi-strategy research candidate.

This is a small, pure control-plane contract.  It freezes a candidate's P1
input digest, code/config identities, selection/holdout split, sleeve weights,
and the separate portfolio-risk-policy digest before any replay begins.  It
does not open data, score a candidate, write an artifact, schedule work, or
authorize paper, shadow, or live execution.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

HISTORICAL_COMBO_P2_CANDIDATE_SCHEMA = "qsl.us-equity-historical-combo-p2-candidate.v1"
PORTFOLIO_RISK_BUDGET_SCHEMA = "qsl.portfolio-risk-budget-research.v1"
FROZEN_RESEARCH_CANDIDATE = "FROZEN_RESEARCH_CANDIDATE"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_EPSILON = 1e-12
_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "research_only",
        "candidate_state",
        "p1_input_sha256",
        "candidate",
        "selection_window",
        "holdout_window",
        "legs",
        "risk_budget",
        "promotion_recommendation",
        "p4_paper_authorized",
        "p5_shadow_authorized",
        "p6_live_authorized",
        "candidate_sha256",
    }
)
_CANDIDATE_FIELDS = frozenset({"candidate_id", "candidate_revision", "config_sha256"})
_WINDOW_FIELDS = frozenset({"start", "end"})
_LEG_FIELDS = frozenset(
    {"leg_id", "strategy_id", "strategy_revision", "config_sha256", "target_weight"}
)
_RISK_BUDGET_FIELDS = frozenset({"schema_version", "policy_sha256"})


class HistoricalComboP2CandidateError(ValueError):
    """Raised when a historical combo candidate is malformed or mutable."""


def _fail(message: str) -> None:
    raise HistoricalComboP2CandidateError(message)


def _identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        _fail(f"invalid {label}")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail(f"invalid {label}")
    return value


def _revision(value: object, label: str) -> str:
    if not isinstance(value, str) or not _REVISION.fullmatch(value):
        _fail(f"invalid {label}")
    return value


def _date(value: object, label: str) -> str:
    if not isinstance(value, str):
        _fail(f"invalid {label}")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HistoricalComboP2CandidateError(f"invalid {label}") from exc


def _weight(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"invalid {label}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0 or numeric > 1.0:
        _fail(f"invalid {label}")
    return numeric


def _candidate(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _CANDIDATE_FIELDS:
        _fail("invalid combo candidate")
    return {
        "candidate_id": _identity(value["candidate_id"], "candidate id"),
        "candidate_revision": _revision(value["candidate_revision"], "candidate revision"),
        "config_sha256": _digest(value["config_sha256"], "candidate config digest"),
    }


def _window(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _WINDOW_FIELDS:
        _fail(f"invalid {label} window")
    start = _date(value["start"], f"{label} window start")
    end = _date(value["end"], f"{label} window end")
    if start > end:
        _fail(f"invalid {label} window")
    return {"start": start, "end": end}


def _windows(selection: object, holdout: object) -> tuple[dict[str, str], dict[str, str]]:
    normalized_selection = _window(selection, "selection")
    normalized_holdout = _window(holdout, "holdout")
    if normalized_selection["end"] >= normalized_holdout["start"]:
        _fail("selection and holdout windows must not overlap")
    return normalized_selection, normalized_holdout


def _legs(value: object) -> list[dict[str, object]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) < 2
    ):
        _fail("invalid combo legs")
    result: list[dict[str, object]] = []
    previous_leg_id: str | None = None
    for raw_leg in value:
        if not isinstance(raw_leg, Mapping) or set(raw_leg) != _LEG_FIELDS:
            _fail("invalid combo leg")
        leg_id = _identity(raw_leg["leg_id"], "leg id")
        if previous_leg_id is not None and leg_id <= previous_leg_id:
            _fail("combo legs must be uniquely sorted")
        previous_leg_id = leg_id
        result.append(
            {
                "leg_id": leg_id,
                "strategy_id": _identity(raw_leg["strategy_id"], "leg strategy id"),
                "strategy_revision": _revision(
                    raw_leg["strategy_revision"], "leg strategy revision"
                ),
                "config_sha256": _digest(raw_leg["config_sha256"], "leg config digest"),
                "target_weight": _weight(raw_leg["target_weight"], "leg target weight"),
            }
        )
    if not math.isclose(
        math.fsum(float(leg["target_weight"]) for leg in result),
        1.0,
        rel_tol=0.0,
        abs_tol=_EPSILON,
    ):
        _fail("combo leg target weights must sum to one")
    return result


def _risk_budget(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _RISK_BUDGET_FIELDS:
        _fail("invalid portfolio risk budget binding")
    if value["schema_version"] != PORTFOLIO_RISK_BUDGET_SCHEMA:
        _fail("invalid portfolio risk budget schema")
    return {
        "schema_version": PORTFOLIO_RISK_BUDGET_SCHEMA,
        "policy_sha256": _digest(value["policy_sha256"], "portfolio risk policy digest"),
    }


def _canonical_json(value: Mapping[str, Any], *, without_digest: bool) -> bytes:
    material = dict(value)
    if without_digest:
        material.pop("candidate_sha256", None)
    try:
        return json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HistoricalComboP2CandidateError("invalid combo P2 candidate") from exc


def calculate_historical_combo_p2_candidate_sha256(value: Mapping[str, Any]) -> str:
    """Return the self-digest for one exact frozen P2 candidate descriptor."""
    return hashlib.sha256(_canonical_json(value, without_digest=True)).hexdigest()


def build_historical_combo_p2_candidate(
    *,
    p1_input_sha256: object,
    candidate_id: object,
    candidate_revision: object,
    config_sha256: object,
    selection_window: object,
    holdout_window: object,
    legs: object,
    risk_budget: object,
) -> dict[str, object]:
    """Build one frozen P2 research candidate without evaluating or enabling it."""
    selection, holdout = _windows(selection_window, holdout_window)
    result: dict[str, object] = {
        "schema_version": HISTORICAL_COMBO_P2_CANDIDATE_SCHEMA,
        "research_only": True,
        "candidate_state": FROZEN_RESEARCH_CANDIDATE,
        "p1_input_sha256": _digest(p1_input_sha256, "P1 input digest"),
        "candidate": _candidate(
            {
                "candidate_id": candidate_id,
                "candidate_revision": candidate_revision,
                "config_sha256": config_sha256,
            }
        ),
        "selection_window": selection,
        "holdout_window": holdout,
        "legs": _legs(legs),
        "risk_budget": _risk_budget(risk_budget),
        "promotion_recommendation": None,
        "p4_paper_authorized": False,
        "p5_shadow_authorized": False,
        "p6_live_authorized": False,
        "candidate_sha256": "",
    }
    result["candidate_sha256"] = calculate_historical_combo_p2_candidate_sha256(result)
    return validate_historical_combo_p2_candidate(result)


def validate_historical_combo_p2_candidate(value: object) -> dict[str, object]:
    """Validate a P2 descriptor without opening P1 data or evaluating a replay."""
    if not isinstance(value, Mapping) or set(value) != _ROOT_FIELDS:
        _fail("invalid combo P2 candidate")
    if value["schema_version"] != HISTORICAL_COMBO_P2_CANDIDATE_SCHEMA:
        _fail("invalid combo P2 candidate schema")
    if value["research_only"] is not True or value["candidate_state"] != FROZEN_RESEARCH_CANDIDATE:
        _fail("combo P2 candidate must remain research only")
    if value["promotion_recommendation"] is not None:
        _fail("combo P2 candidate cannot contain a promotion recommendation")
    if (
        value["p4_paper_authorized"] is not False
        or value["p5_shadow_authorized"] is not False
        or value["p6_live_authorized"] is not False
    ):
        _fail("combo P2 candidate cannot authorize execution")
    selection, holdout = _windows(value["selection_window"], value["holdout_window"])
    normalized: dict[str, object] = {
        "schema_version": HISTORICAL_COMBO_P2_CANDIDATE_SCHEMA,
        "research_only": True,
        "candidate_state": FROZEN_RESEARCH_CANDIDATE,
        "p1_input_sha256": _digest(value["p1_input_sha256"], "P1 input digest"),
        "candidate": _candidate(value["candidate"]),
        "selection_window": selection,
        "holdout_window": holdout,
        "legs": _legs(value["legs"]),
        "risk_budget": _risk_budget(value["risk_budget"]),
        "promotion_recommendation": None,
        "p4_paper_authorized": False,
        "p5_shadow_authorized": False,
        "p6_live_authorized": False,
        "candidate_sha256": _digest(value["candidate_sha256"], "candidate self digest"),
    }
    if normalized["candidate_sha256"] != calculate_historical_combo_p2_candidate_sha256(normalized):
        _fail("combo P2 candidate digest mismatch")
    return normalized


__all__ = [
    "FROZEN_RESEARCH_CANDIDATE",
    "HISTORICAL_COMBO_P2_CANDIDATE_SCHEMA",
    "PORTFOLIO_RISK_BUDGET_SCHEMA",
    "HistoricalComboP2CandidateError",
    "build_historical_combo_p2_candidate",
    "calculate_historical_combo_p2_candidate_sha256",
    "validate_historical_combo_p2_candidate",
]
