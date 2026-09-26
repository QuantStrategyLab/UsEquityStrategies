"""Fixed JSON stdin/stdout preview for one frozen R8 research case.

Operator input roots come from process environment at startup:
UX1_RAW_ROOT, UX1_R6_ROOT, and UX1_MATERIALIZED. They are absolute local
paths. The request cannot supply a path, command, URL, credential, or
authority flag. This entry does not run orders, brokers, or external data.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUEST_SCHEMA = "qsl.ux1.preview_request.v1"
RESULT_SCHEMA = "qsl.ux1.preview_result.v1"
RESEARCH_CASE_ID = "r8_first_dynamic_2023_03_29"
CANDIDATE_ID = "r8_finite_action_joint_account_60session_b0_startup_development_v2"
OBJECTIVE = "one_step_net_log_score"
CANDIDATE_SET_ID = "r8_b0_b3"
CAPITAL_VARIANT = "C2"
COST_BPS = 10
SOURCE_CLASS = "historical_development"
INITIAL_NAV_USD = 10000
DECISION_DATE = "2023-03-29"
TRADE_DATE = "2023-03-30"
STARTUP_DATES = ("2023-03-27", "2023-03-28")
OPTIMALITY_SCOPE = "best_score_among_feasible_B0_B3_only"
MODEL_ID = "paired_60_executable_one_session_log_v1"
ACTIONS = ("B0", "B1", "B2", "B3")
OWNERS = ("outer", "tqqq", "soxl")
OWNER_SYMBOLS = {"outer": ("QQQM", "BOXX"), "tqqq": ("TQQQ",), "soxl": ("SOXL", "SOXX", "BOXX")}
ORIGINAL_FULL_V2 = frozenset({
    "independent-soxl-full-manifest-v1",
    "independent-tqqq-full-manifest-v1",
    "independent-soxl-full-manifest-v2",
    "independent-tqqq-full-manifest-v2",
})
REQUEST_KEYS = frozenset({
    "schema", "research_case_id", "candidate_id", "objective", "candidate_set_id",
    "capital_variant", "cost_bps", "source_class", "advanced_settings",
})
ADVANCED_TYPES = {
    "plugin_mode": "mode",
    "income_layer_mode": "mode",
    "income_layer_start_usd": "usd",
    "income_layer_max_ratio": "ratio",
    "option_overlay_mode": "mode",
    "reserve_policy_mode": "mode",
    "min_reserved_cash_usd": "usd",
    "reserved_cash_ratio": "ratio",
    "cash_only_execution_mode": "mode",
    "dca_mode": "mode",
    "dca_base_investment_usd": "usd",
}
FORBIDDEN_KEYS = frozenset({
    "path", "paths", "url", "uri", "href", "command", "cmd", "shell", "argv",
    "raw_root", "r6_root", "materialized", "input_root", "filename", "file",
    "credential", "credentials", "secret", "token", "password", "account",
    "account_id", "broker", "order", "orders", "execution_authority",
    "execution_authority_granted", "paper_authorized", "shadow_authorized",
    "live_authorized", "native_observed", "view_mode", "authority",
})
ROOT_ENV = ("UX1_RAW_ROOT", "UX1_R6_ROOT", "UX1_MATERIALIZED")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,80}")
_MODE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")
_MAX_BYTES = 65536
_LIMITATIONS = (
    "model_scope_is_existing_r8_one_step_finite_actions_only",
    "optimality_is_best_score_among_feasible_b0_b3_only",
    "not_original_full_v2",
    "not_strict_point_in_time",
    "not_paper_shadow_or_live",
    "no_future_return_or_max_drawdown_guarantee",
    "historical_execution_does_not_revise_the_prior_decision",
    "selected_action_does_not_prove_targets_filled",
    "null_advanced_settings_do_not_assert_original_full_v2_feature_toggles",
    "prior_window_cumulative_metrics_are_not_this_draft_performance",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def _fingerprint(request: dict) -> str:
    body = {key: request[key] for key in (
        "advanced_settings", "candidate_id", "candidate_set_id", "capital_variant",
        "cost_bps", "objective", "research_case_id", "source_class")}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _flags(**extra: object) -> dict:
    body = {
        "schema": RESULT_SCHEMA,
        "research_only": True,
        "no_order": True,
        "execution_authority_granted": False,
        "native_observed": False,
        "paper_authorized": False,
        "shadow_authorized": False,
        "live_authorized": False,
        "original_full_v2_mapped": False,
        "original_full_v2_features_silently_disabled": False,
        "advanced_null_asserts_original_full_v2_toggles": False,
    }
    body.update(extra)
    return body


def _code(exc: BaseException) -> str:
    token = str(exc).split(":", 1)[0].strip()
    if _CODE.fullmatch(token):
        return token
    return "UX1_PREVIEW_FAILED"


def _failure(request: dict | None, status: str, code: str) -> dict:
    body = _flags(status=status, reason_code=code, reasons=[code])
    if request is not None:
        body["request_fingerprint"] = _fingerprint(request)
        body["advanced_settings"] = dict(request["advanced_settings"])
    return body


def _reject(code: str) -> dict:
    return _flags(status="rejected", reason_code=code, reasons=[code])


def _looks_controlled(value: str) -> bool:
    return "://" in value or value.startswith(("/", "~")) or "\\" in value or "\x00" in value


def _scan(value: object, depth: int = 0) -> str | None:
    if depth > 6:
        return "UX1_REQUEST_SCHEMA_INVALID"
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in FORBIDDEN_KEYS:
                return "UX1_FORBIDDEN_FIELD"
            found = _scan(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, list):
        return "UX1_REQUEST_SCHEMA_INVALID"
    if isinstance(value, str):
        return "UX1_FORBIDDEN_FIELD" if _looks_controlled(value) else None
    if value is None or isinstance(value, (bool, int, float)):
        return None
    return "UX1_REQUEST_SCHEMA_INVALID"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _decimal_text(value: int | float) -> str:
    text = format(Decimal(str(value)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _typed_advanced(raw: object) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict) or set(raw) != set(ADVANCED_TYPES):
        return None, "UX1_ADVANCED_SETTINGS_INVALID"
    cleaned = {}
    for key, kind in ADVANCED_TYPES.items():
        value = raw[key]
        if value is None:
            cleaned[key] = None
            continue
        if kind == "mode":
            if not isinstance(value, str) or not _MODE.fullmatch(value):
                return None, "UX1_ADVANCED_VALUE_INVALID"
            cleaned[key] = value
        else:
            if isinstance(value, str):
                if len(value) > 32 or not _DECIMAL.fullmatch(value):
                    return None, "UX1_ADVANCED_VALUE_INVALID"
                decimal_text = value
            else:
                number = _number(value)
                if number is None:
                    return None, "UX1_ADVANCED_VALUE_INVALID"
                decimal_text = _decimal_text(value)
            try:
                amount = Decimal(decimal_text)
            except InvalidOperation:
                return None, "UX1_ADVANCED_VALUE_INVALID"
            if amount < 0 or (kind == "ratio" and amount > 1) or (kind == "usd" and amount > 1_000_000_000):
                return None, "UX1_ADVANCED_VALUE_INVALID"
            cleaned[key] = decimal_text
    return cleaned, None


def _parse(payload: object) -> tuple[dict | None, dict | None]:
    if not isinstance(payload, dict):
        return None, _reject("UX1_REQUEST_SCHEMA_INVALID")
    forbidden = _scan(payload)
    if forbidden:
        return None, _reject(forbidden)
    if set(payload) != REQUEST_KEYS or payload.get("schema") != REQUEST_SCHEMA:
        return None, _reject("UX1_REQUEST_SCHEMA_INVALID")
    settings, error = _typed_advanced(payload.get("advanced_settings"))
    if error:
        return None, _reject(error)
    request = {
        "schema": REQUEST_SCHEMA,
        "research_case_id": payload.get("research_case_id"),
        "candidate_id": payload.get("candidate_id"),
        "objective": payload.get("objective"),
        "candidate_set_id": payload.get("candidate_set_id"),
        "capital_variant": payload.get("capital_variant"),
        "cost_bps": payload.get("cost_bps"),
        "source_class": payload.get("source_class"),
        "advanced_settings": settings,
    }
    if any(not isinstance(request[key], str) for key in (
            "research_case_id", "candidate_id", "objective", "candidate_set_id",
            "capital_variant", "source_class")):
        return None, _reject("UX1_REQUEST_SCHEMA_INVALID")
    return request, None


def _case_mismatch(request: dict) -> dict:
    return _failure(request, "rejected", "UX1_FROZEN_CASE_MISMATCH")


def _unsupported_candidate(request: dict) -> dict:
    reasons = ["unsupported_candidate"]
    if request.get("candidate_id") in ORIGINAL_FULL_V2:
        reasons.append("original_full_v2_not_mapped_to_simplified_r8")
    body = _failure(request, "unsupported_candidate", reasons[0])
    body["reasons"] = reasons
    return body


def _unsupported_scope(request: dict) -> dict:
    reasons = [f"{key}_not_modeled_by_r8" for key, value in request["advanced_settings"].items()
               if value is not None]
    body = _failure(request, "unsupported_scope", "unsupported_scope")
    body["reasons"] = reasons
    body["limitations"] = (
        "explicit_advanced_values_are_retained_and_not_applied",
        "null_advanced_settings_do_not_assert_original_full_v2_feature_toggles",
        "original_full_v2_is_not_mapped_or_silently_disabled",
    )
    return body


def _frozen_case(request: dict) -> dict | None:
    if request["candidate_id"] != CANDIDATE_ID:
        return _unsupported_candidate(request)
    if type(request["cost_bps"]) is not int or request["cost_bps"] != COST_BPS:
        return _case_mismatch(request)
    expected = {
        "research_case_id": RESEARCH_CASE_ID,
        "objective": OBJECTIVE,
        "candidate_set_id": CANDIDATE_SET_ID,
        "capital_variant": CAPITAL_VARIANT,
        "source_class": SOURCE_CLASS,
    }
    if any(request[key] != value for key, value in expected.items()):
        return _case_mismatch(request)
    if any(value is not None for value in request["advanced_settings"].values()):
        return _unsupported_scope(request)
    return None


def _operator_roots() -> tuple[Path, Path, Path]:
    paths = []
    for name in ROOT_ENV:
        raw = os.environ.get(name)
        if (not isinstance(raw, str) or not raw or raw != raw.strip()
                or "://" in raw or raw.startswith("~") or "\\" in raw or "\x00" in raw
                or not raw.startswith("/")):
            raise ValueError("UX1_OPERATOR_INPUT_UNCONFIGURED")
        path = Path(raw)
        if not path.is_absolute() or ".." in path.parts or path.is_symlink():
            raise ValueError("UX1_OPERATOR_INPUT_UNCONFIGURED")
        paths.append(path)
    return paths[0], paths[1], paths[2]


def _iso_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("UX1_DECISION_DATE_INVALID")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("UX1_DECISION_DATE_INVALID") from exc
    if parsed.isoformat() != value:
        raise ValueError("UX1_DECISION_DATE_INVALID")
    return value


def _finite(value: object) -> float:
    number = _number(value)
    if number is None:
        raise ValueError("UX1_RESULT_NOT_FINITE")
    return number


def _whole(value: object) -> int:
    number = _number(value)
    if number is None or not number.is_integer():
        raise ValueError("UX1_SHARE_NOT_WHOLE")
    return int(number)


def _nested_numbers(raw: object, shape: dict[str, tuple[str, ...]]) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(shape):
        raise ValueError("UX1_RESULT_SHAPE_INVALID")
    cleaned = {}
    for owner, symbols in shape.items():
        item = raw[owner]
        if not isinstance(item, dict) or set(item) != set(symbols):
            raise ValueError("UX1_RESULT_SHAPE_INVALID")
        cleaned[owner] = {symbol: _finite(item[symbol]) for symbol in symbols}
    return cleaned


def _shares(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(OWNER_SYMBOLS):
        raise ValueError("UX1_RESULT_SHAPE_INVALID")
    cleaned = {}
    for owner, symbols in OWNER_SYMBOLS.items():
        item = raw[owner]
        if not isinstance(item, dict) or set(item) != set(symbols):
            raise ValueError("UX1_RESULT_SHAPE_INVALID")
        cleaned[owner] = {symbol: _whole(item[symbol]) for symbol in symbols}
    return cleaned


def _no_advantage(scores: dict[str, float], previous: str, tolerance: float) -> tuple[bool, float]:
    best = max(scores.values())
    gain = best - scores[previous]
    return scores[previous] >= best - tolerance, gain


def _decision_preview(*, selection: dict, budgets: dict, targets: dict,
                      outer_cash_target: float, curve_ratio: float,
                      wealth_reference: float, member_cap: float,
                      tolerance: float) -> dict:
    """Fields known at the decision close. Trade fills are not an input."""
    if selection.get("startup_fixed") is not False or selection.get("scenario_count") != 60:
        raise ValueError("UX1_DECISION_NOT_DYNAMIC")
    observed = _iso_date(selection.get("observed_through"))
    if observed > DECISION_DATE:
        raise ValueError("UX1_FUTURE_SCENARIO")
    selected = selection.get("selected_action")
    previous = selection.get("previous_action")
    if selected not in ACTIONS or previous not in ACTIONS:
        raise ValueError("UX1_ACTION_INVALID")
    if not isinstance(budgets, dict) or set(budgets) != {"tqqq", "soxl"}:
        raise ValueError("UX1_RESULT_SHAPE_INVALID")
    raw_scores = selection.get("estimated_scores")
    if not isinstance(raw_scores, dict) or set(raw_scores) != set(ACTIONS):
        raise ValueError("UX1_SCORE_SHAPE_INVALID")
    scores = {name: _finite(raw_scores[name]) for name in ACTIONS}
    mean = selection.get("estimated_mean_log_growth")
    if mean is None or abs(_finite(mean) - scores[selected]) > 1e-12:
        raise ValueError("UX1_SCORE_INCONSISTENT")
    no_advantage, gain = _no_advantage(scores, previous, tolerance)
    member_budgets = {owner: _finite(budgets[owner]) for owner in ("tqqq", "soxl")}
    return {
        "decision_date": DECISION_DATE,
        "scenarios_observed_through": observed,
        "scenario_count": 60,
        "selected_action": selected,
        "previous_action": previous,
        "previous_action_retained": selection.get("previous_action_retained") is True,
        "scores": scores,
        "score_gain_over_previous_action": gain,
        "tie_log_tolerance": tolerance,
        "no_advantage": no_advantage,
        "member_budgets_usd": member_budgets,
        "asset_targets_usd": _nested_numbers(targets, OWNER_SYMBOLS),
        "outer_cash_target_usd": _finite(outer_cash_target),
        "curve_ratio": _finite(curve_ratio),
        "wealth_reference_usd": _finite(wealth_reference),
        "aggregate_member_cap_usd": _finite(member_cap),
    }


def _shortages(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("UX1_RESULT_SHAPE_INVALID")
    cleaned = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {
                "owner", "symbol", "desired_shares", "filled_shares"}:
            raise ValueError("UX1_RESULT_SHAPE_INVALID")
        owner = item["owner"]
        symbol = item["symbol"]
        if owner not in OWNER_SYMBOLS or symbol not in OWNER_SYMBOLS[owner]:
            raise ValueError("UX1_RESULT_SHAPE_INVALID")
        desired = _whole(item["desired_shares"])
        filled = _whole(item["filled_shares"])
        if desired < 0 or filled < 0 or filled > desired:
            raise ValueError("UX1_SHORTAGE_INVALID")
        cleaned.append({"owner": owner, "symbol": symbol,
                        "desired_shares": desired, "filled_shares": filled})
    return cleaned


def _execution_check(*, trade_date: str, trade_shares: dict, fees: dict,
                     total_fees: float, settled_cash: float, pending: float,
                     receivable: float, shortages: list[dict]) -> dict:
    shares = _shares(trade_shares)
    fee_map = _nested_numbers(fees, OWNER_SYMBOLS)
    for owner in fee_map.values():
        for amount in owner.values():
            if amount < 0:
                raise ValueError("UX1_FEE_INVALID")
    total = _finite(total_fees)
    summed = math.fsum(amount for owner in fee_map.values() for amount in owner.values())
    if abs(summed - total) > 1e-6:
        raise ValueError("UX1_FEE_INCONSISTENT")
    quantities = [qty for owner in shares.values() for qty in owner.values()]
    return {
        "trade_date": trade_date,
        "decision_date": DECISION_DATE,
        "feeds_back_into_decision": False,
        "trade_shares": shares,
        "fees_usd": fee_map,
        "total_fees_usd": total,
        "settled_cash_usd": _finite(settled_cash),
        "pending_sale_usd": _finite(pending),
        "receivable_usd": _finite(receivable),
        "shortages": shortages,
        "no_action": all(qty == 0 for qty in quantities),
    }


def _require_prefix(ledger: list, tolerance: float) -> dict:
    if not isinstance(ledger, list) or len(ledger) != 3 or any(not isinstance(row, dict) for row in ledger):
        raise ValueError("UX1_PREFIX_LENGTH_INVALID")
    for row, signal in zip(ledger[:2], STARTUP_DATES, strict=True):
        selection = row.get("action_selection") or {}
        if (row.get("signal_date") != signal or row.get("path") != "B0"
                or selection.get("startup_fixed") is not True
                or selection.get("selected_action") != "B0"
                or selection.get("scenario_count") != 0):
            raise ValueError("UX1_STARTUP_PREFIX_INVALID")
    row = ledger[2]
    selection = row.get("action_selection")
    if not isinstance(selection, dict):
        raise ValueError("UX1_DECISION_NOT_DYNAMIC")
    if (_iso_date(row.get("signal_date")) != DECISION_DATE
            or _iso_date(row.get("date")) != TRADE_DATE
            or row.get("path") != selection.get("selected_action")
            or row.get("cost_bps") != COST_BPS
            or row.get("candidate_id") != CANDIDATE_ID
            or row.get("capital_variant") != CAPITAL_VARIANT):
        raise ValueError("UX1_DECISION_DATE_INVALID")
    decision = _decision_preview(
        selection=selection, budgets=row.get("member_budget_usd"),
        targets=row.get("target_usd"), outer_cash_target=row.get("cash_target_usd"),
        curve_ratio=row.get("curve_ratio"), wealth_reference=row.get("wealth_reference_usd"),
        member_cap=row.get("aggregate_member_cap_usd"), tolerance=tolerance)
    execution = _execution_check(
        trade_date=TRADE_DATE, trade_shares=row.get("trade_shares"),
        fees=row.get("trade_cost_usd"), total_fees=row.get("total_cost_usd"),
        settled_cash=row.get("settled_cash_usd"), pending=row.get("pending_sale_usd"),
        receivable=row.get("receivable_usd"), shortages=_shortages(row.get("shortages")))
    return {"decision_preview": decision, "historical_execution_check": execution}


def _calculate(request: dict) -> dict:
    raw_root, r6_root, materialized = _operator_roots()
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import post_r9_capital_study as capital
    import r7_joint_account_compare as r7
    import r8_joint_allocation_compare as r8

    r8_policy = r8._policy()
    r7_policy = r7._policy()
    if (r8_policy["candidate_id"] != CANDIDATE_ID
            or r8_policy["startup"]["dynamic_first_decision_date"] != DECISION_DATE
            or r8_policy["startup"]["first_dynamic_trade_date"] != TRADE_DATE
            or r8_policy["startup"]["decision_dates"] != list(STARTUP_DATES)
            or r7_policy["initial_research_nav_usd"] != INITIAL_NAV_USD
            or r8_policy["initial_research_nav_usd"] != INITIAL_NAV_USD):
        raise ValueError("UX1_CASE_IDENTITY_INVALID")
    tolerance = _finite(r8_policy["estimator"]["tie_log_tolerance"])
    settlement = r7._load_settlement_policy(
        here / "post_r9_date_effective_settlement_policy.v1.json")
    rows, actions, indicators, source = r7._load_inputs(
        raw_root, r6_root, materialized, r7_policy)
    hook = capital.capital_hook(CAPITAL_VARIANT, INITIAL_NAV_USD)
    if hook.variant != CAPITAL_VARIANT or hook.initial_nav != INITIAL_NAV_USD:
        raise ValueError("UX1_CAPITAL_HOOK_INVALID")
    selector = r8._selector(indicators, source["tqqq_contract"], actions, r7_policy,
                            r8_policy, settlement)
    _metrics, ledger = r7._replay(
        rows, actions, indicators, source["tqqq_contract"], r7_policy,
        path_name="B0", cost_bps=COST_BPS, short_sessions=3,
        action_selector=selector, candidate_id=r8_policy["candidate_id"],
        settlement_policy=settlement, capital_hook=hook)
    del _metrics
    projected = _require_prefix(ledger, tolerance)
    return _flags(
        status="ok",
        request_fingerprint=_fingerprint(request),
        research_case_id=RESEARCH_CASE_ID,
        candidate_id=CANDIDATE_ID,
        candidate_set_id=CANDIDATE_SET_ID,
        objective=OBJECTIVE,
        source_class=SOURCE_CLASS,
        source_assurance=r7_policy["soxl_input_assurance"],
        model_id=MODEL_ID,
        r7_policy_sha256=r7.POLICY_SHA256,
        r8_policy_sha256=r8.POLICY_SHA256,
        capital_policy_id=hook.policy_id,
        capital_policy_sha256=capital.POLICY_SHA256,
        capital_variant=CAPITAL_VARIANT,
        settlement_policy_id=settlement["policy_id"],
        settlement_policy_sha256=r7.SETTLEMENT_POLICY_SHA256,
        raw_manifest_sha256=r7_policy["data"]["raw_manifest_sha256"],
        r6_manifest_sha256=r7_policy["data"]["r6_manifest_sha256"],
        r6_materialized_file_sha256=r7_policy["data"]["r6_materialized_file_sha256"],
        cost_bps=COST_BPS,
        initial_research_nav_usd=INITIAL_NAV_USD,
        optimality_scope=OPTIMALITY_SCOPE,
        advanced_settings=dict(request["advanced_settings"]),
        decision_preview=projected["decision_preview"],
        historical_execution_check=projected["historical_execution_check"],
        limitations=list(_LIMITATIONS),
        development=True,
        strict_point_in_time_certified=False,
    )


def preview(payload: object) -> dict:
    request, rejected = _parse(payload)
    if rejected is not None:
        return rejected
    blocked = _frozen_case(request)
    if blocked is not None:
        return blocked
    try:
        return _calculate(request)
    except FileNotFoundError:
        return _failure(request, "missing_input", "UX1_INPUT_UNAVAILABLE")
    except OSError:
        return _failure(request, "failed", "UX1_PREVIEW_FAILED")
    except ValueError as exc:
        code = _code(exc)
        status = "missing_input" if code == "UX1_OPERATOR_INPUT_UNCONFIGURED" else "failed"
        return _failure(request, status, code)
    except Exception:
        return _failure(request, "failed", "UX1_PREVIEW_FAILED")


def _reject_json_constant(_name: str) -> None:
    raise ValueError("UX1_REQUEST_SCHEMA_INVALID")


def _emit(result: dict) -> None:
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False))
    sys.stdout.write("\n")


def main(stdin=None) -> int:
    if len(sys.argv) != 1:
        _emit(_reject("UX1_ARGUMENTS_REJECTED"))
        return 0
    stream = sys.stdin.buffer if stdin is None else stdin
    try:
        raw = stream.read(_MAX_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode()
    except Exception:
        _emit(_reject("UX1_REQUEST_SCHEMA_INVALID"))
        return 0
    if len(raw) > _MAX_BYTES:
        _emit(_reject("UX1_REQUEST_TOO_LARGE"))
        return 0
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except Exception:
        _emit(_reject("UX1_REQUEST_SCHEMA_INVALID"))
        return 0
    if payload is None:
        _emit(_reject("UX1_REQUEST_SCHEMA_INVALID"))
        return 0
    _emit(preview(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
