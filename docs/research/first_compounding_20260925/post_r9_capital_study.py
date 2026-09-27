"""Active capital-boundary study on the M1 date-effective dynamic account.

C0 keeps the constant 5% member cap. C1 freezes r(initial NAV). C2 refreshes
r(W) before each actual decision. Formal paths pass an empty external-flow
list through the same reference update used by the synthetic acceptance.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path

import r7_joint_account_compare as r7
import r8_joint_allocation_compare as r8
import r9_frozen_policy_validation as r9
import post_r9_settlement_study as settlement
from quant_platform_kit.strategy_lifecycle.live_equity import cash_flow_adjusted_return
from us_equity_strategies.research.c3_capital_path import smooth_bounded_capital_risk_ratio

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "post_r9_capital_policy.v1.json"
POLICY_SHA256 = "59a70fefa6c4714206f73945c02bc153c2c1c6d7a52adefaa9574bed30359a01"
M1_SUMMARY_SHA256 = "e0e5c2e51836edb246e70cd9ea7f4b64def713928aafd4d6933b403a69f793cc"
M1_DYNAMIC_LEDGER_SHA256 = "9ab7b28d0fa9a024c389d49d4eaa3f79adae591cd100d80411f125bf03da832e"
SCALES = (1_000.0, 10_000.0, 100_000.0)
VARIANTS = ("C0", "C1", "C2")
REUSED_M1 = (10_000.0, "C0")
SESSIONS = 856
FIRST_SESSION = "2023-03-28"
LAST_SESSION = "2026-08-25"
MAX_SHORT_HISTORY_CHECKS = 2
_DIFF_KEYS = (
    "cumulative_return", "cagr_252_sessions", "max_drawdown", "total_fees_usd",
    "participation_days", "shortage_event_count", "ending_nav_usd",
    "average_curve_ratio", "average_aggregate_member_cap_usd",
    "average_member_budget_usd", "average_member_nav_to_decision_equity",
    "average_nasdaq_nominal_exposure_to_nav",
    "average_semiconductor_nominal_exposure_to_nav",
    "average_outer_boxx_to_nav", "average_soxl_internal_boxx_to_nav",
    "average_settled_cash_usd", "average_pending_sale_usd",
    "average_receivable_usd", "max_overcap_residual_usd",
)
_LEGACY_WEIGHTS = {
    "B0": {"qqqm": 0.50, "tqqq_cap": 0.0, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01},
    "B1": {"qqqm": 0.45, "tqqq_cap": 0.05, "soxl_cap": 0.0, "outer_boxx": 0.49, "outer_cash": 0.01},
    "B2": {"qqqm": 0.45, "tqqq_cap": 0.0, "soxl_cap": 0.05, "outer_boxx": 0.49, "outer_cash": 0.01},
    "B3": {"qqqm": 0.45, "tqqq_cap": 0.025, "soxl_cap": 0.025, "outer_boxx": 0.49, "outer_cash": 0.01},
}
_FLOW_SIGN = {"deposit": 1.0, "withdrawal": -1.0}
_NON_EXTERNAL = {"dividend", "owner_transfer", "unsettled"}


def _policy() -> dict:
    content = POLICY_PATH.read_bytes()
    if hashlib.sha256(content).hexdigest() != POLICY_SHA256:
        raise ValueError("POST_R9_CAPITAL_POLICY_CHANGED")
    policy = json.loads(content)
    parameters = policy.get("parameters")
    if (policy.get("schema") != "qsl.research.post_r9_capital_policy.v1"
            or policy.get("policy_id") != "post_r9_active_capital_boundary_v1"
            or policy.get("research_only") is not True or policy.get("development") is not True
            or any(policy.get(key) is not False for key in
                   ("paper_authorized", "shadow_authorized", "live_authorized"))
            or policy.get("capital_function") != "smooth_bounded_capital_risk_ratio"
            or policy.get("curve_superiority_assumed") is not False
            or policy.get("real_contribution_plan_supported") is not False
            or policy.get("investment_index_is_dollar_wealth") is not False
            or policy.get("external_flows_on_formal_paths") != "none"
            or policy.get("sessions") != SESSIONS or policy.get("cost_bps") != 10
            or policy.get("scales_usd") != [1000, 10000, 100000]
            or policy.get("variants") != {
                "C0": "date_effective_dynamic_constant_upper",
                "C1": "fixed_r_of_initial_nav",
                "C2": "r_of_wealth_high_water_before_each_actual_decision",
            }
            or policy.get("wealth_reference")
               != "max(initial_nav, prior_and_current_signal_decision_equity)"
            or policy.get("aggregate_member_cap")
               != "current_decision_equity_times_ratio_not_wealth_times_ratio"
            or policy.get("initial_books") != "dynamic_b0_all_initial_capital_in_outer"
            or policy.get("window") != {"start": FIRST_SESSION, "end": LAST_SESSION}
            or parameters != {"a0": 10_000.0, "lower": 0.01, "upper": 0.05, "curvature": 1.0}
            or policy.get("settlement_policy_sha256") != r7.SETTLEMENT_POLICY_SHA256
            or policy.get("reused_m1_10000_c0", {}).get("summary_sha256") != M1_SUMMARY_SHA256
            or policy.get("reused_m1_10000_c0", {}).get("dynamic_ledger_sha256")
               != M1_DYNAMIC_LEDGER_SHA256):
        raise ValueError("POST_R9_CAPITAL_POLICY_IDENTITY_INVALID")
    return policy


def action_weights(ratio: float, action: str) -> dict:
    """Map one frozen ratio onto B0-B3. At 5% the weights are the legacy actions."""
    if action not in _LEGACY_WEIGHTS:
        raise ValueError("POST_R9_CAPITAL_ACTION_INVALID")
    if action == "B0" or math.isclose(ratio, 0.05, rel_tol=0.0, abs_tol=1e-15):
        return dict(_LEGACY_WEIGHTS[action])
    if not math.isfinite(ratio) or ratio < 0.0 or ratio > 0.05:
        raise ValueError("POST_R9_CAPITAL_RATIO_INVALID")
    tqqq = ratio if action == "B1" else (ratio / 2.0 if action == "B3" else 0.0)
    soxl = ratio if action == "B2" else (ratio / 2.0 if action == "B3" else 0.0)
    weights = {"qqqm": 0.45, "tqqq_cap": tqqq, "soxl_cap": soxl,
               "outer_boxx": 0.54 - ratio, "outer_cash": 0.01}
    if abs(math.fsum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("POST_R9_CAPITAL_WEIGHTS_INVALID")
    return weights


class CapitalDecisionFreeze:
    def __init__(self, policy_id: str, variant: str, decision_equity: float,
                 wealth: float, ratio: float) -> None:
        self.policy_id = policy_id
        self.variant = variant
        self.decision_equity_usd = float(decision_equity)
        self.wealth_reference_usd = float(wealth)
        self.curve_ratio = float(ratio)
        self.aggregate_member_cap_usd = self.decision_equity_usd * self.curve_ratio

    def weights(self, action: str) -> dict:
        return action_weights(self.curve_ratio, action)


class CapitalResearchHook:
    def __init__(self, policy: dict, variant: str, initial_nav: float) -> None:
        if variant not in VARIANTS:
            raise ValueError("POST_R9_CAPITAL_VARIANT_INVALID")
        initial = float(initial_nav)
        if not math.isfinite(initial) or initial <= 0.0:
            raise ValueError("POST_R9_CAPITAL_INITIAL_NAV_INVALID")
        self.policy_id = policy["policy_id"]
        self.parameters = policy["parameters"]
        self.variant = variant
        self.initial_nav = initial
        self.w = initial
        self.seen_event_ids: list[str] = []
        self.investment_index = 1.0
        self.investment_high_water = 1.0
        self._c1_ratio = self._curve(initial)["risk_ratio"]

    def _curve(self, capital: float) -> dict:
        return smooth_bounded_capital_risk_ratio(
            capital=capital, a0=self.parameters["a0"], lower=self.parameters["lower"],
            upper=self.parameters["upper"], curvature=self.parameters["curvature"])

    def _ratio(self) -> float:
        if self.variant == "C0":
            return float(self.parameters["upper"])
        if self.variant == "C1":
            return self._c1_ratio
        return float(self._curve(self.w)["risk_ratio"])

    def freeze_actual(self, decision_equity: float) -> CapitalDecisionFreeze:
        """Update dollar W from the current known decision equity only."""
        value = float(decision_equity)
        if isinstance(decision_equity, bool) or not math.isfinite(value) or value <= 0.0:
            raise ValueError("POST_R9_CAPITAL_DECISION_EQUITY_INVALID")
        self.w = max(self.initial_nav, self.w, value)
        return CapitalDecisionFreeze(
            self.policy_id, self.variant, value, self.w, self._ratio())

    def binding(self, *, selected: str, shortages: list, overcap: float, ratio: float) -> str:
        if selected == "B0":
            return "not_bound_selected_b0"
        if overcap > 1e-6:
            return "passive_overcap_retained"
        if shortages:
            return "not_bound_unfilled_after_cap"
        if ratio + 1e-15 < float(self.parameters["upper"]):
            return "bound_below_constant_5pct"
        return "at_constant_5pct_cap"

    def apply_period_reference(self, *, previous_equity: float, ending_equity: float,
                               events) -> dict:
        """Chain the public end-of-period flow return into an investment index.

        The index is not dollar W. Deposits and withdrawals are external flows.
        Dividends, unsettled cash and owner transfers are not. An empty event
        list is the formal F=0 entry. This does not implement a contribution plan.
        """
        if events is None:
            raise ValueError("POST_R9_CAPITAL_EVENTS_INVALID")
        flow = 0.0
        fresh: list[str] = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("POST_R9_CAPITAL_EVENT_INVALID")
            event_id = event.get("event_id")
            kind = event.get("kind")
            amount = event.get("amount")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("POST_R9_CAPITAL_EVENT_INVALID")
            if event_id in self.seen_event_ids or event_id in fresh:
                raise ValueError("POST_R9_CAPITAL_DUPLICATE_EVENT")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(float(amount)):
                raise ValueError("POST_R9_CAPITAL_EVENT_INVALID")
            if kind in _FLOW_SIGN:
                if float(amount) <= 0.0:
                    raise ValueError("POST_R9_CAPITAL_EVENT_INVALID")
                flow += _FLOW_SIGN[kind] * float(amount)
            elif kind not in _NON_EXTERNAL:
                raise ValueError("POST_R9_CAPITAL_EVENT_INVALID")
            fresh.append(event_id)
        adjusted = cash_flow_adjusted_return(
            previous_equity, ending_equity, net_external_cash_flow=flow)
        if adjusted is None:
            raise ValueError("POST_R9_CAPITAL_FLOW_REFERENCE_INVALID")
        self.investment_index *= 1.0 + adjusted
        self.investment_high_water = max(self.investment_high_water, self.investment_index)
        self.seen_event_ids.extend(fresh)
        return {"external_flow_usd": flow, "investment_return": adjusted,
                "investment_index": self.investment_index,
                "investment_high_water": self.investment_high_water,
                "dollar_w_usd": self.w, "dollar_w_is_investment_index": False,
                "real_contribution_plan_supported": False}

    def export_state(self) -> dict:
        return {"capital_policy_id": self.policy_id, "capital_variant": self.variant,
                "capital_w_usd": self.w, "capital_initial_nav_usd": self.initial_nav,
                "capital_seen_event_ids": list(self.seen_event_ids),
                "investment_index": self.investment_index,
                "investment_high_water": self.investment_high_water}

    def restore(self, checkpoint: dict) -> None:
        if (checkpoint.get("capital_policy_id") != self.policy_id
                or checkpoint.get("capital_variant") != self.variant
                or abs(float(checkpoint.get("capital_initial_nav_usd", -1.0)) - self.initial_nav) > 1e-9):
            raise ValueError("POST_R9_CAPITAL_CHECKPOINT_IDENTITY_MISMATCH")
        wealth = float(checkpoint["capital_w_usd"])
        seen = checkpoint.get("capital_seen_event_ids")
        if (not math.isfinite(wealth) or wealth + 1e-9 < self.initial_nav
                or not isinstance(seen, list)
                or any(not isinstance(item, str) or not item for item in seen)
                or len(seen) != len(set(seen))):
            raise ValueError("POST_R9_CAPITAL_CHECKPOINT_W_INVALID")
        index = float(checkpoint["investment_index"])
        high_water = float(checkpoint["investment_high_water"])
        if (not math.isfinite(index) or index <= 0.0 or not math.isfinite(high_water)
                or high_water + 1e-12 < index):
            raise ValueError("POST_R9_CAPITAL_CHECKPOINT_INVESTMENT_INVALID")
        self.w = wealth
        self.seen_event_ids = list(seen)
        self.investment_index = index
        self.investment_high_water = high_water


def capital_hook(variant: str, initial_nav: float) -> CapitalResearchHook:
    return CapitalResearchHook(_policy(), variant, initial_nav)


def _load_m1(root: Path) -> tuple[dict, list]:
    summary_path = Path(root) / "summary.json"
    ledger_path = Path(root) / "private_daily_dynamic_10bps.json"
    if summary_path.is_symlink() or ledger_path.is_symlink():
        raise ValueError("POST_R9_CAPITAL_M1_SYMLINK")
    summary_bytes = summary_path.read_bytes()
    ledger_bytes = ledger_path.read_bytes()
    if hashlib.sha256(summary_bytes).hexdigest() != M1_SUMMARY_SHA256:
        raise ValueError("POST_R9_CAPITAL_M1_SUMMARY_MISMATCH")
    if hashlib.sha256(ledger_bytes).hexdigest() != M1_DYNAMIC_LEDGER_SHA256:
        raise ValueError("POST_R9_CAPITAL_M1_LEDGER_MISMATCH")
    summary = json.loads(summary_bytes)
    ledger = json.loads(ledger_bytes)
    if (summary.get("schema") != "qsl.research.post_r9_date_effective_settlement_study.v1"
            or summary.get("policy_id") != "post_r9_us_equity_dtc_standard_settlement_v1"
            or summary.get("settlement_policy_sha256") != r7.SETTLEMENT_POLICY_SHA256
            or summary.get("research_only") is not True
            or any(summary.get(key) is not False for key in
                   ("paper_authorized", "shadow_authorized", "live_authorized"))
            or summary.get("compared_paths") != ["B0_10bps", "dynamic_10bps"]
            or summary.get("private_ledger_sha256", {}).get("dynamic_10bps")
               != M1_DYNAMIC_LEDGER_SHA256):
        raise ValueError("POST_R9_CAPITAL_M1_IDENTITY_INVALID")
    if (not isinstance(ledger, list) or len(ledger) != SESSIONS
            or ledger[0].get("date") != FIRST_SESSION
            or ledger[-1].get("date") != LAST_SESSION
            or any(row.get("cost_bps") != 10 for row in ledger)
            or any(float(row.get("external_flow_usd", 0.0)) != 0.0 for row in ledger)):
        raise ValueError("POST_R9_CAPITAL_M1_LEDGER_INVALID")
    return summary, ledger


def _write_exclusive(path: Path, content: bytes) -> str:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)
    os.chmod(path, 0o600)
    return hashlib.sha256(content).hexdigest()


def _path_key(scale: float, variant: str) -> str:
    return f"{int(scale)}_{variant}_10bps"


def _capital_metrics(ledger: list[dict], initial: float) -> dict:
    report = settlement._path_summary(ledger, initial)
    ending = float(ledger[-1]["economic_nav_close_usd"])
    report["cagr_252_sessions"] = (ending / initial) ** (252 / len(ledger)) - 1.0
    report["ending_nav_usd"] = ending
    report["participation_days"] = sum(
        float(row.get("tqqq_value_usd", 0.0)) + float(row.get("soxl_value_usd", 0.0))
        + float(row.get("soxx_value_usd", 0.0)) > 0.0 for row in ledger)
    ratios = [float(row.get("curve_ratio", 0.05)) for row in ledger]
    decision_navs = [float(row["decision_equity_usd"]) for row in ledger]
    caps = [float(row.get("aggregate_member_cap_usd", nav * ratio))
            for row, nav, ratio in zip(ledger, decision_navs, ratios, strict=True)]
    budgets = [math.fsum(float(value) for value in row["member_budget_usd"].values())
               for row in ledger]
    member_navs = [math.fsum(float(row["owner_nav_usd"][owner])
                            for owner in ("tqqq", "soxl")) for row in ledger]
    navs = [float(row["economic_nav_close_usd"]) for row in ledger]
    report.update({
        "average_curve_ratio": math.fsum(ratios) / len(ratios),
        "minimum_curve_ratio": min(ratios),
        "maximum_curve_ratio": max(ratios),
        "average_aggregate_member_cap_usd": math.fsum(caps) / len(caps),
        "average_member_budget_usd": math.fsum(budgets) / len(budgets),
        "average_member_nav_to_decision_equity": math.fsum(
            member / decision for member, decision in zip(member_navs, decision_navs, strict=True)
        ) / len(ledger),
        "max_overcap_residual_usd": max(
            float(row.get("overcap_residual_usd", max(0.0, member - cap)))
            for row, member, cap in zip(ledger, member_navs, caps, strict=True)
        ),
        "capital_binding_counts": dict(Counter(
            str(row.get("capital_binding", "legacy_constant_5pct")) for row in ledger
        )),
        "average_nasdaq_nominal_exposure_to_nav": math.fsum(
            (float(row.get("qqqm_value_usd", 0.0)) + 3 * float(row.get("tqqq_value_usd", 0.0))) / nav
            for row, nav in zip(ledger, navs, strict=True)
        ) / len(ledger),
        "average_semiconductor_nominal_exposure_to_nav": math.fsum(
            (3 * float(row.get("soxl_value_usd", 0.0)) + float(row.get("soxx_value_usd", 0.0))) / nav
            for row, nav in zip(ledger, navs, strict=True)
        ) / len(ledger),
        "average_outer_boxx_to_nav": math.fsum(
            float(row.get("outer_boxx_value_usd", 0.0)) / nav
            for row, nav in zip(ledger, navs, strict=True)
        ) / len(ledger),
        "average_soxl_internal_boxx_to_nav": math.fsum(
            float(row.get("soxl_internal_boxx_value_usd", 0.0)) / nav
            for row, nav in zip(ledger, navs, strict=True)
        ) / len(ledger),
        "average_settled_cash_usd": math.fsum(float(row.get("settled_cash_usd", 0.0))
                                               for row in ledger) / len(ledger),
        "average_pending_sale_usd": math.fsum(float(row.get("pending_sale_usd", 0.0))
                                               for row in ledger) / len(ledger),
        "average_receivable_usd": math.fsum(float(row.get("receivable_usd", 0.0))
                                             for row in ledger) / len(ledger),
        "ending_owner_nav_usd": ledger[-1].get("owner_nav_usd"),
        "ending_owner_security_values_usd": ledger[-1].get("owner_security_values_usd"),
        "ending_owner_settled_cash_usd": ledger[-1].get("owner_settled_cash_usd"),
        "ending_owner_pending_sale_usd": ledger[-1].get("owner_pending_sale_usd"),
        "ending_owner_receivable_usd": ledger[-1].get("owner_receivable_usd"),
        "ending_owner_shares": ledger[-1].get("owner_shares"),
    })
    return report


def _difference(later: dict, earlier: dict) -> dict:
    return {key: later[key] - earlier[key] for key in _DIFF_KEYS}


def _play(rows, actions, indicators, contract, account, dynamic, settlement, hook, **extra):
    selector = r8._selector(indicators, contract, actions, account, dynamic, settlement)
    return r7._replay(rows, actions, indicators, contract, account, path_name="B0", cost_bps=10,
                      action_selector=selector, candidate_id=dynamic["candidate_id"],
                      settlement_policy=settlement, capital_hook=hook, **extra)


def _deterministic_recovery(rows, actions, indicators, contract, account, dynamic,
                            settlement) -> dict:
    if MAX_SHORT_HISTORY_CHECKS < 2:
        raise ValueError("POST_R9_CAPITAL_SHORT_CHECK_LIMIT")
    scale, variant = 1_000.0, "C2"
    scaled = copy.deepcopy(account)
    scaled["initial_research_nav_usd"] = scale
    start = next(index for index, row in enumerate(rows) if row["date"] == account["data"]["first_trade"])
    if start + 2 >= len(rows):
        raise ValueError("POST_R9_CAPITAL_RECOVERY_WINDOW_INVALID")
    full_last = rows[start + 2]["date"]
    checkpoint: dict = {}
    _metrics, prefix = _play(rows, actions, indicators, contract, scaled, dynamic, settlement,
                             CapitalResearchHook(_policy(), variant, scale),
                             short_sessions=2, checkpoint_out=checkpoint)
    restored = CapitalResearchHook(_policy(), variant, scale)
    resume = copy.deepcopy(scaled)
    resume["data"] = {**resume["data"], "last_session": checkpoint["last_date"]}
    _metrics, suffix = _play(rows, actions, indicators, contract, resume, dynamic, settlement,
                             restored, continuation_from_session=checkpoint["last_date"],
                             continuation_last_session=full_last,
                             continuation_checkpoint=checkpoint)
    _metrics, full = _play(rows, actions, indicators, contract, scaled, dynamic, settlement,
                           CapitalResearchHook(_policy(), variant, scale), short_sessions=3)
    if prefix + suffix != full:
        raise ValueError("POST_R9_CAPITAL_RECOVERY_MISMATCH")
    if restored.w + 1e-9 < float(checkpoint["capital_w_usd"]):
        raise ValueError("POST_R9_CAPITAL_RECOVERY_W_RESET")
    return {"status": "matched", "short_history_checks": 2, "restored_w_usd": restored.w}


def run(raw_root: Path, r6_root: Path, materialized_path: Path,
        r7_archive_root: Path, r8_archive_root: Path, r9_archive_root: Path,
        future_root: Path, m1_root: Path, output_root: Path) -> dict:
    del r7_archive_root, r8_archive_root, r9_archive_root
    m1_summary, m1_ledger = _load_m1(m1_root)
    recorded = m1_summary.get("private_ledger_sha256", {}).get("dynamic_10bps")
    if recorded != M1_DYNAMIC_LEDGER_SHA256:
        raise ValueError("POST_R9_CAPITAL_M1_SUMMARY_IDENTITY_MISMATCH")
    settlement_policy = r7._load_settlement_policy(
        HERE / "post_r9_date_effective_settlement_policy.v1.json")
    account = r7._policy()
    dynamic = r8._policy()
    capital = _policy()
    rows, actions, indicators, source = r7._load_inputs(
        raw_root, r6_root, materialized_path, account)
    rows, actions, future_meta = r9._verified_future(future_root, rows, actions, indicators)
    end = future_meta["last_extension_session"]
    recovery = _deterministic_recovery(
        rows, actions, indicators, source["tqqq_contract"], account, dynamic, settlement_policy)
    ledgers = {}
    for scale in SCALES:
        for variant in VARIANTS:
            if (scale, variant) == REUSED_M1:
                continue
            scaled = copy.deepcopy(account)
            scaled["initial_research_nav_usd"] = scale
            _metrics, ledger = _play(
                rows, actions, indicators, source["tqqq_contract"], scaled, dynamic,
                settlement_policy, CapitalResearchHook(capital, variant, scale),
                continuation_last_session=end)
            if (len(ledger) != SESSIONS or ledger[0].get("date") != FIRST_SESSION
                    or ledger[-1].get("date") != LAST_SESSION
                    or any(float(row.get("external_flow_usd", 0.0)) != 0.0 for row in ledger)):
                raise ValueError("POST_R9_CAPITAL_SESSION_COUNT_INVALID")
            ledgers[_path_key(scale, variant)] = ledger
    by_scale = {}
    for scale in SCALES:
        by_scale[scale] = {}
        for variant in VARIANTS:
            if (scale, variant) == REUSED_M1:
                by_scale[scale][variant] = _capital_metrics(m1_ledger, scale)
            else:
                by_scale[scale][variant] = _capital_metrics(ledgers[_path_key(scale, variant)], scale)
    comparisons = {
        str(int(scale)): {
            "C1_minus_C0": _difference(by_scale[scale]["C1"], by_scale[scale]["C0"]),
            "C2_minus_C1": _difference(by_scale[scale]["C2"], by_scale[scale]["C1"]),
        } for scale in SCALES}
    summary = {
        "schema": "qsl.research.post_r9_capital_study.v1",
        "policy_id": capital["policy_id"],
        "policy_sha256": POLICY_SHA256,
        "research_only": True, "development": True,
        "paper_authorized": False, "shadow_authorized": False, "live_authorized": False,
        "curve_superiority_assumed": False,
        "external_flows": "none",
        "formal_paths_use_reference_entry": True,
        "real_contribution_plan_supported": False,
        "reused_m1_10000_c0": {
            "summary_sha256": M1_SUMMARY_SHA256,
            "dynamic_ledger_sha256": M1_DYNAMIC_LEDGER_SHA256,
            "read_only": True,
        },
        "candidate_id": dynamic["candidate_id"],
        "r7_policy_sha256": r7.POLICY_SHA256,
        "r8_policy_sha256": r8.POLICY_SHA256,
        "settlement_policy_id": settlement_policy["policy_id"],
        "settlement_policy_sha256": r7.SETTLEMENT_POLICY_SHA256,
        "raw_manifest_sha256": account["data"]["raw_manifest_sha256"],
        "r6_manifest_sha256": account["data"]["r6_manifest_sha256"],
        "r6_materialized_file_sha256": account["data"]["r6_materialized_file_sha256"],
        "tqqq_contract_sha256": account["data"]["tqqq_contract_sha256"],
        "r9_summary_sha256": settlement.R9_FORMAL_SUMMARY_SHA256,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "r7_engine_sha256": hashlib.sha256(Path(r7.__file__).read_bytes()).hexdigest(),
        "r8_engine_sha256": hashlib.sha256(Path(r8.__file__).read_bytes()).hexdigest(),
        "input_validation": future_meta,
        "source_assurance": account["soxl_input_assurance"],
        "strict_point_in_time_certified": False,
        "limits": (
            "Development research only. R6 is single-source structural evidence; corporate-action "
            "process_date is a retrospective proxy, not strict point-in-time certification. The "
            "1%-5% curve is pre-specified and not optimized, does not raise the prior 5% member "
            "upper bound, does not guarantee an account drawdown limit, and grants no paper, "
            "shadow, live, deployment, account, or trading authority."
        ),
        "sessions": SESSIONS, "cost_bps": 10,
        "scales_usd": [1000, 10000, 100000],
        "variants": list(VARIANTS),
        "paths": {str(int(scale)): by_scale[scale] for scale in SCALES},
        "comparisons": comparisons,
        "recovery": {key: value for key, value in recovery.items() if key != "restored_w_usd"},
    }
    output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(output_root, 0o700)
    hashes = {}
    for key, ledger in sorted(ledgers.items()):
        hashes[key] = _write_exclusive(
            output_root / f"private_daily_{key}.json", r7._canonical(ledger))
    summary["private_ledger_sha256"] = hashes
    content = r7._canonical(summary)
    summary_sha = _write_exclusive(output_root / "summary.json", content)
    return {"status": "COMPLETE", "policy_id": capital["policy_id"],
            "path_count": len(ledgers), "summary_sha256": summary_sha}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--r6-root", type=Path, required=True)
    parser.add_argument("--materialized", type=Path, required=True)
    parser.add_argument("--r7-archive-root", type=Path, required=True)
    parser.add_argument("--r8-archive-root", type=Path, required=True)
    parser.add_argument("--r9-archive-root", type=Path, required=True)
    parser.add_argument("--future-root", type=Path, required=True)
    parser.add_argument("--m1-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.raw_root, args.r6_root, args.materialized, args.r7_archive_root,
                         args.r8_archive_root, args.r9_archive_root, args.future_root,
                         args.m1_root, args.output_root), sort_keys=True))
