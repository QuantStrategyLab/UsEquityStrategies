"""Pure identity envelope for one optimized SOXL or TQQQ research member.

The caller supplies every bound value. This module does not read git, score a
candidate, emit daily returns, or construct a research ledger. Declarations are
not proof of prices, costs, or corporate actions, and the result is not
historical evidence or a production equivalent.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from quant_platform_kit.strategy_lifecycle.contracts import validate_periods_per_year

OPTIMIZED_MEMBER_IDENTITY_SCHEMA = "qsl.us-equity-optimized-member-identity.v1"
EVIDENCE_SCOPE = "IDENTITY_ONLY_NO_HISTORICAL_RETURNS"
CONTRACT_PROOF = "DECLARATION_ONLY"
_DOMAIN = "us_equity"
_PROFILES = frozenset({"soxl_soxx_trend_income", "tqqq_growth_income"})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_PLACEHOLDERS = frozenset({"unknown", "default", "none", "null", "na", "n/a"})
_FOREIGN_SCHEMAS = frozenset(
    {
        "qsl.c3-batch-a-frozen-member-pack.v1",
        "qsl.c3-batch-a-frozen-member-pack.v2",
        "qsl.research.soxl_soxx_typed_baseline_result.v1",
        "qsl.research.tqqq_typed_baseline_result.v1",
    }
)
_FOREIGN_PROFILES = frozenset(
    {
        "soxl_soxx_trend_income_parity_baseline_v1",
        "tqqq_growth_income_research_baseline_v1",
        "soxl_core",
        "tqqq_core",
        "cash_sleeve",
    }
)
_FOREIGN_COSTS = frozenset(
    {
        "TYPED_BASELINE_ZERO",
        "TYPED_BASELINE_ZERO_ASSUMED_NO_MEMBER_FEES",
        "ASSUMED_ZERO_USD_CASH",
    }
)
_SMA_TIMING = frozenset(
    {
        "SMA200_INCLUSIVE_CLOSE_V1",
        "SOXX_SMA200_INCLUSIVE_CLOSE_NEXT_SOXL_OPEN_V1",
    }
)
_COST_FIELDS = frozenset({"commission_bps", "market_impact_bps", "slippage_bps"})
_EXECUTION_FIELDS = frozenset(
    {
        "execution_timing_contract",
        "fill_price_field",
        "nav_mark_field",
        "signal_effective_after_trading_days",
    }
)
_CONTRACT_FIELDS = frozenset(
    {
        "adjustment",
        "cash",
        "corporate_action",
        "external_cashflow",
        "share_quantity",
    }
)
_ROOT_FIELDS = frozenset(
    {
        "actual_params",
        "calendar_id",
        "config_sha256",
        "contract_proof",
        "cost_inputs",
        "cost_source",
        "declared_contracts",
        "domain",
        "economic_identity_sha256",
        "evidence_scope",
        "execution",
        "execution_authorized",
        "input_sha256",
        "param_set_id",
        "periods_per_year",
        "promotion_authorized",
        "qpk_revision",
        "research_only",
        "schema_version",
        "strategy_profile",
        "ues_revision",
        "ues_workspace_patch_sha256",
        "window_end",
        "window_start",
    }
)


class OptimizedMemberIdentityError(ValueError):
    """Raised when an optimized member identity is incomplete or overclaimed."""


def _fail(message: str) -> None:
    raise OptimizedMemberIdentityError(message)


def _placeholder(value: str) -> bool:
    return value.strip().casefold() in _PLACEHOLDERS


def _reject_foreign(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(item, str):
                _reject_foreign(item)
                continue
            if key == "schema_version" and item in _FOREIGN_SCHEMAS:
                _fail("rejected foreign evidence identity")
            if key == "evidence_use":
                _fail("rejected foreign evidence identity")
            if key in {"profile", "strategy_profile"} and item in _FOREIGN_PROFILES:
                _fail("rejected foreign evidence identity")
            if key in {"cost_scenario", "cost_source"} and item in _FOREIGN_COSTS:
                _fail("rejected foreign evidence identity")
            if key in {"execution_timing_contract", "signal_timing"} and item in _SMA_TIMING:
                _fail("rejected foreign evidence identity")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _reject_foreign(item)


def _revision(value: object) -> str:
    if not isinstance(value, str) or not _REVISION.fullmatch(value):
        _fail("invalid optimized member identity")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail("invalid optimized member identity")
    return value


def _patch(value: object) -> str | None:
    if value is None:
        return None
    return _digest(value)


def _token(value: object) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value) or _placeholder(value):
        _fail("invalid optimized member identity")
    return value


def _profile(value: object) -> str:
    if not isinstance(value, str):
        _fail("invalid optimized member identity")
    if value in _FOREIGN_PROFILES:
        _fail("rejected foreign evidence identity")
    if value not in _PROFILES:
        _fail("invalid optimized member identity")
    return str(value)


def _date(value: object) -> str:
    if not isinstance(value, str):
        _fail("invalid optimized member identity")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise OptimizedMemberIdentityError("invalid optimized member identity") from exc


def _window(start: object, end: object) -> tuple[str, str]:
    start_text = _date(start)
    end_text = _date(end)
    if start_text > end_text:
        _fail("invalid optimized member identity")
    return start_text, end_text


def _periods(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("invalid optimized member identity")
    number = float(value)
    if not math.isfinite(number):
        _fail("invalid optimized member identity")
    try:
        return validate_periods_per_year(number)
    except ValueError as exc:
        raise OptimizedMemberIdentityError("invalid optimized member identity") from exc


def _cost_inputs(value: object) -> dict[str, float]:
    if type(value) is not dict or set(value) != _COST_FIELDS:
        _fail("invalid optimized member identity")
    parsed: dict[str, float] = {}
    for key in ("commission_bps", "slippage_bps", "market_impact_bps"):
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            _fail("invalid optimized member identity")
        number = float(item)
        if not math.isfinite(number) or number < 0.0:
            _fail("invalid optimized member identity")
        parsed[key] = number
    return parsed


def _walk_params(value: object) -> None:
    if isinstance(value, str):
        if not value.strip() or _placeholder(value):
            _fail("invalid optimized member identity")
        return
    if isinstance(value, bool) or value is None:
        if value is None:
            _fail("invalid optimized member identity")
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            _fail("invalid optimized member identity")
        return
    if isinstance(value, list):
        for item in value:
            _walk_params(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str or not key.strip() or _placeholder(key):
                _fail("invalid optimized member identity")
            _walk_params(item)
        return
    _fail("invalid optimized member identity")


def _params(value: object) -> dict[str, Any]:
    if type(value) is not dict or len(value) == 0:
        _fail("invalid optimized member identity")
    _reject_foreign(value)
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        parsed = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise OptimizedMemberIdentityError("invalid optimized member identity") from exc
    if type(parsed) is not dict or len(parsed) == 0:
        _fail("invalid optimized member identity")
    _walk_params(parsed)
    return parsed


def _declaration(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 200
        or _placeholder(value)
        or any(ord(character) < 32 for character in value)
        or not any(character.isalpha() for character in value)
    ):
        _fail("invalid optimized member identity")
    return value


def _contracts(
    adjustment: object,
    cash: object,
    corporate_action: object,
    external_cashflow: object,
    share_quantity: object,
) -> dict[str, str]:
    return {
        "adjustment": _declaration(adjustment),
        "cash": _declaration(cash),
        "corporate_action": _declaration(corporate_action),
        "external_cashflow": _declaration(external_cashflow),
        "share_quantity": _declaration(share_quantity),
    }


def _contracts_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _CONTRACT_FIELDS:
        _fail("invalid optimized member identity")
    return _contracts(
        value["adjustment"],
        value["cash"],
        value["corporate_action"],
        value["external_cashflow"],
        value["share_quantity"],
    )


def _fill(value: object) -> str:
    if not isinstance(value, str) or value not in {"open", "close"}:
        _fail("invalid optimized member identity")
    return str(value)


def _execution(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _EXECUTION_FIELDS:
        _fail("invalid execution contract")
    days = value["signal_effective_after_trading_days"]
    if (
        type(days) is not int
        or days != 1
        or value["execution_timing_contract"] != "next_trading_day"
        or value["nav_mark_field"] != "close"
        or not isinstance(value["fill_price_field"], str)
        or value["fill_price_field"] not in {"open", "close"}
    ):
        _fail("invalid execution contract")
    return {
        "signal_effective_after_trading_days": 1,
        "execution_timing_contract": "next_trading_day",
        "fill_price_field": value["fill_price_field"],
        "nav_mark_field": "close",
    }


def _canonical_json(value: Mapping[str, Any], *, without_digest: bool) -> bytes:
    material = dict(value)
    if without_digest:
        material.pop("economic_identity_sha256", None)
    try:
        return json.dumps(
            material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OptimizedMemberIdentityError("invalid optimized member identity") from exc


def calculate_optimized_member_identity_sha256(value: Mapping[str, Any]) -> str:
    """Return the SHA-256 of one identity with its own digest removed."""
    return hashlib.sha256(_canonical_json(value, without_digest=True)).hexdigest()


def build_optimized_member_identity(
    *,
    strategy_profile: object,
    ues_revision: object,
    qpk_revision: object,
    ues_workspace_patch_sha256: object,
    param_set_id: object,
    actual_params: object,
    config_sha256: object,
    input_sha256: object,
    window_start: object,
    window_end: object,
    calendar_id: object,
    periods_per_year: object,
    cost_source: object,
    cost_inputs: object,
    fill_price_field: object,
    adjustment_contract: object,
    cash_contract: object,
    corporate_action_contract: object,
    external_cashflow_contract: object,
    share_quantity_contract: object,
) -> dict[str, object]:
    """Bind one caller-supplied member identity without evaluating it."""
    _reject_foreign(
        {
            "strategy_profile": strategy_profile,
            "cost_source": cost_source,
            "actual_params": actual_params,
        }
    )
    start, end = _window(window_start, window_end)
    result: dict[str, object] = {
        "schema_version": OPTIMIZED_MEMBER_IDENTITY_SCHEMA,
        "research_only": True,
        "execution_authorized": False,
        "promotion_authorized": False,
        "evidence_scope": EVIDENCE_SCOPE,
        "domain": _DOMAIN,
        "strategy_profile": _profile(strategy_profile),
        "ues_revision": _revision(ues_revision),
        "qpk_revision": _revision(qpk_revision),
        "ues_workspace_patch_sha256": _patch(ues_workspace_patch_sha256),
        "param_set_id": _token(param_set_id),
        "actual_params": _params(actual_params),
        "config_sha256": _digest(config_sha256),
        "input_sha256": _digest(input_sha256),
        "window_start": start,
        "window_end": end,
        "calendar_id": _token(calendar_id),
        "periods_per_year": _periods(periods_per_year),
        "cost_source": _token(cost_source),
        "cost_inputs": _cost_inputs(cost_inputs),
        "execution": {
            "signal_effective_after_trading_days": 1,
            "execution_timing_contract": "next_trading_day",
            "fill_price_field": _fill(fill_price_field),
            "nav_mark_field": "close",
        },
        "declared_contracts": _contracts(
            adjustment_contract,
            cash_contract,
            corporate_action_contract,
            external_cashflow_contract,
            share_quantity_contract,
        ),
        "contract_proof": CONTRACT_PROOF,
        "economic_identity_sha256": "",
    }
    result["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(result)
    return validate_optimized_member_identity(result)


def validate_optimized_member_identity(value: object) -> dict[str, object]:
    """Validate one identity envelope. This does not prove the declared sources."""
    if isinstance(value, Mapping):
        _reject_foreign(value)
    if not isinstance(value, Mapping) or set(value) != _ROOT_FIELDS:
        _fail("invalid optimized member identity")
    if value["schema_version"] != OPTIMIZED_MEMBER_IDENTITY_SCHEMA:
        _fail("invalid optimized member identity")
    if value["research_only"] is not True:
        _fail("optimized member identity must remain research only")
    if value["execution_authorized"] is not False or value["promotion_authorized"] is not False:
        _fail("optimized member identity cannot authorize execution")
    if value["evidence_scope"] != EVIDENCE_SCOPE:
        _fail("invalid evidence scope")
    if value["contract_proof"] != CONTRACT_PROOF:
        _fail("invalid contract proof")
    if value["domain"] != _DOMAIN:
        _fail("invalid optimized member identity")
    start, end = _window(value["window_start"], value["window_end"])
    normalized: dict[str, object] = {
        "schema_version": OPTIMIZED_MEMBER_IDENTITY_SCHEMA,
        "research_only": True,
        "execution_authorized": False,
        "promotion_authorized": False,
        "evidence_scope": EVIDENCE_SCOPE,
        "domain": _DOMAIN,
        "strategy_profile": _profile(value["strategy_profile"]),
        "ues_revision": _revision(value["ues_revision"]),
        "qpk_revision": _revision(value["qpk_revision"]),
        "ues_workspace_patch_sha256": _patch(value["ues_workspace_patch_sha256"]),
        "param_set_id": _token(value["param_set_id"]),
        "actual_params": _params(value["actual_params"]),
        "config_sha256": _digest(value["config_sha256"]),
        "input_sha256": _digest(value["input_sha256"]),
        "window_start": start,
        "window_end": end,
        "calendar_id": _token(value["calendar_id"]),
        "periods_per_year": _periods(value["periods_per_year"]),
        "cost_source": _token(value["cost_source"]),
        "cost_inputs": _cost_inputs(value["cost_inputs"]),
        "execution": _execution(value["execution"]),
        "declared_contracts": _contracts_mapping(value["declared_contracts"]),
        "contract_proof": CONTRACT_PROOF,
        "economic_identity_sha256": _digest(value["economic_identity_sha256"]),
    }
    config_digest = hashlib.sha256(
        _canonical_json(normalized["actual_params"], without_digest=False)
    ).hexdigest()
    if normalized["config_sha256"] != config_digest:
        _fail("invalid optimized member identity")
    digest = calculate_optimized_member_identity_sha256(normalized)
    if normalized["economic_identity_sha256"] != digest:
        _fail("optimized member identity digest mismatch")
    return normalized


__all__ = [
    "CONTRACT_PROOF",
    "EVIDENCE_SCOPE",
    "OPTIMIZED_MEMBER_IDENTITY_SCHEMA",
    "OptimizedMemberIdentityError",
    "build_optimized_member_identity",
    "calculate_optimized_member_identity_sha256",
    "validate_optimized_member_identity",
]
