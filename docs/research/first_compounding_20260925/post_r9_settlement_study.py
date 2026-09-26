"""Date-effective DTC settlement study for B0 and R8 v2 dynamic at 10 bps.

The runner reuses R9 input checks and the archived two-session ledgers.
It does not call r9.analyze and does not rebuild the other thirteen paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path

import r7_joint_account_compare as r7
import r8_joint_allocation_compare as r8
import r9_frozen_policy_validation as r9

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE / "post_r9_date_effective_settlement_policy.v1.json"
# Completed R9 summary identity recorded in r9_validation_status.md. Read-only.
R9_FORMAL_SUMMARY_SHA256 = "628de89afde2fad718ae6298370e895585d3c4c082452a5c9044b5abeb2ba95f"
_STATE_KEYS = (
    "date", "path", "economic_nav_close_usd", "total_cost_usd", "shortages",
    "trade_shares", "owner_shares", "owner_settled_cash_usd", "owner_pending_sale_usd",
    "owner_transfers_usd", "dividend_paid_usd", "sale_proceeds_released_usd",
)


def _settlement_policy() -> dict:
    return r7._load_settlement_policy(POLICY_PATH)


def _observation_index(rows: list[dict], trade_index: int, policy: dict) -> int | None:
    legal = r7._settlement_date(rows[trade_index]["date"], policy)
    for index in range(trade_index + 1, len(rows)):
        if rows[index]["date"] >= legal:
            return index
    return None


def _same_cash_timing(rows: list[dict], trade_index: int, policy: dict) -> bool:
    legacy = trade_index + 2
    legacy_index = legacy if legacy < len(rows) else None
    return _observation_index(rows, trade_index, policy) == legacy_index


def _state(row: dict) -> dict:
    return {key: row.get(key) for key in _STATE_KEYS}


def _common_settlement_prefix(old: list[dict], new: list[dict], rows: list[dict],
                              policy: dict) -> dict:
    """Report mapping and account-state divergence independently.

    A trade can have a different release observation before the two ledgers'
    account state actually differs. The joint prefix counts only leading
    sessions where both are still the same. A zero difference stays zero.
    """
    if [row["date"] for row in old] != [row["date"] for row in new] or not old:
        raise ValueError("POST_R9_WINDOW_MISMATCH")
    start = next(index for index, row in enumerate(rows) if row["date"] == old[0]["date"])
    mapping_trade_date = None
    sessions_before_mapping = len(old)
    state_date = None
    sessions_before_state = len(old)
    for offset, (left, right) in enumerate(zip(old, new, strict=True)):
        session = start + offset
        if session >= len(rows) or rows[session]["date"] != left["date"]:
            raise ValueError("POST_R9_WINDOW_MISMATCH")
        if mapping_trade_date is None and not _same_cash_timing(rows, session, policy):
            mapping_trade_date = rows[session]["date"]
            sessions_before_mapping = offset
        if (state_date is None
                and r7._canonical(_state(left)) != r7._canonical(_state(right))):
            state_date = left["date"]
            sessions_before_state = offset
    return {
        "first_release_observation_mapping_difference_trade_date": mapping_trade_date,
        "ledger_sessions_before_release_observation_mapping_difference": sessions_before_mapping,
        "first_account_state_difference_date": state_date,
        "ledger_sessions_before_account_state_difference": sessions_before_state,
        "joint_prefix_sessions": min(sessions_before_mapping, sessions_before_state),
    }


def _merge_baseline(prefix: list[dict], extension: list[dict]) -> list[dict]:
    if (not prefix or not extension or prefix[-1]["date"] >= extension[0]["date"]
            or [row["date"] for row in prefix] != sorted(row["date"] for row in prefix)
            or [row["date"] for row in extension] != sorted(row["date"] for row in extension)):
        raise ValueError("POST_R9_BASELINE_INVALID")
    if prefix[0]["date"] == "2023-03-28" and (
            len(prefix) != 444 or prefix[-1]["date"] != "2024-12-31"
            or len(extension) != 412 or extension[0]["date"] != "2025-01-02"
            or extension[-1]["date"] != "2026-08-25"):
        raise ValueError("POST_R9_BASELINE_IDENTITY_INVALID")
    return [*prefix, *extension]


def _path_summary(ledger: list[dict], initial_nav: float) -> dict:
    navs = [float(row["economic_nav_close_usd"]) for row in ledger]
    peak = initial_nav
    peak_index = -1
    worst = 0.0
    trough_index = -1
    worst_peak_index = -1
    for index, nav in enumerate(navs):
        if nav > peak:
            peak, peak_index = nav, index
        drawdown = nav / peak - 1.0
        if drawdown < worst:
            worst, trough_index, worst_peak_index = drawdown, index, peak_index
    recovered = None
    recovery_sessions = None
    if trough_index >= 0 and worst < 0:
        peak_nav = initial_nav if worst_peak_index < 0 else navs[worst_peak_index]
        for index in range(trough_index + 1, len(navs)):
            if navs[index] >= peak_nav - 1e-9:
                recovered = ledger[index]["date"]
                recovery_sessions = index - max(worst_peak_index, 0)
                break
    releases = [item for row in ledger for item in row.get("settlement_cash_releases", [])]
    if any(item["observation_date"] < item["settlement_date"] for item in releases):
        raise ValueError("POST_R9_OBSERVATION_BEFORE_SETTLEMENT")
    counts = Counter(row["path"] for row in ledger)
    return {
        "settlement_observation_counts": {
            "releases": len(releases),
            "observation_equals_settlement_date": sum(
                item["observation_date"] == item["settlement_date"] for item in releases),
            "observation_after_settlement_date": sum(
                item["observation_date"] > item["settlement_date"] for item in releases),
        },
        "ending_securities": ledger[-1].get("owner_security_values_usd", {}),
        "shortage_event_count": sum(len(row["shortages"]) for row in ledger),
        "cumulative_return": navs[-1] / initial_nav - 1.0,
        "max_drawdown": worst,
        "drawdown_recovery": {"date": recovered, "sessions": recovery_sessions},
        "total_fees_usd": math.fsum(float(row["total_cost_usd"]) for row in ledger),
        "action_counts": dict(counts),
        "action_transitions": sum(ledger[index]["path"] != ledger[index - 1]["path"]
                                  for index in range(1, len(ledger))),
    }


def _versus(new: dict, old: dict, fork: dict) -> dict:
    actions = set(new["action_counts"]) | set(old["action_counts"])
    return {
        "first_release_observation_mapping_difference_trade_date":
            fork["first_release_observation_mapping_difference_trade_date"],
        "ledger_sessions_before_release_observation_mapping_difference":
            fork["ledger_sessions_before_release_observation_mapping_difference"],
        "first_account_state_difference_date": fork["first_account_state_difference_date"],
        "ledger_sessions_before_account_state_difference":
            fork["ledger_sessions_before_account_state_difference"],
        "joint_prefix_sessions": fork["joint_prefix_sessions"],
        "cumulative_return_difference": new["cumulative_return"] - old["cumulative_return"],
        "max_drawdown_difference": new["max_drawdown"] - old["max_drawdown"],
        "total_fees_difference_usd": new["total_fees_usd"] - old["total_fees_usd"],
        "shortage_event_count_difference": new["shortage_event_count"] - old["shortage_event_count"],
        "action_count_difference": {
            name: new["action_counts"].get(name, 0) - old["action_counts"].get(name, 0)
            for name in sorted(actions)},
        "action_transition_difference": new["action_transitions"] - old["action_transitions"],
    }


def _write_exclusive(path: Path, content: bytes) -> str:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)
    return hashlib.sha256(content).hexdigest()


def run(raw_root: Path, r6_root: Path, materialized_path: Path,
        r7_archive_root: Path, r8_archive_root: Path, r9_archive_root: Path,
        future_root: Path, output_root: Path) -> dict:
    settlement = _settlement_policy()
    protocol = r9._protocol()
    if protocol["r8_formal_summary_sha256"] != r9.R8_SUMMARY_SHA256:
        raise ValueError("POST_R9_R8_SUMMARY_IDENTITY_MISMATCH")
    account = r7._policy()
    dynamic = r8._policy()
    rows, actions, indicators, source = r7._load_inputs(
        raw_root, r6_root, materialized_path, account)
    rows, actions, future_meta = r9._verified_future(future_root, rows, actions, indicators)
    _summary, archived_b0 = r9._load_archive(
        r7_archive_root, protocol["r7_formal_summary_sha256"], {"B0_10bps"})
    _summary, archived_dynamic = r9._load_archive(
        r8_archive_root, r9.R8_SUMMARY_SHA256, {"dynamic_10bps"})
    _summary, archived_extension = r9._load_archive(
        r9_archive_root, R9_FORMAL_SUMMARY_SHA256, {"B0_10bps", "dynamic_10bps"})
    old = {
        "B0_10bps": _merge_baseline(archived_b0["B0_10bps"], archived_extension["B0_10bps"]),
        "dynamic_10bps": _merge_baseline(archived_dynamic["dynamic_10bps"],
                                         archived_extension["dynamic_10bps"]),
    }
    end = future_meta["last_extension_session"]
    selector = r8._selector(indicators, source["tqqq_contract"], actions, account, dynamic,
                            settlement_policy=settlement)
    ledgers = {}
    _metrics, ledgers["B0_10bps"] = r7._replay(
        rows, actions, indicators, source["tqqq_contract"], account,
        path_name="B0", cost_bps=10, settlement_policy=settlement,
        continuation_last_session=end)
    _metrics, ledgers["dynamic_10bps"] = r7._replay(
        rows, actions, indicators, source["tqqq_contract"], account,
        path_name="B0", cost_bps=10, action_selector=selector,
        candidate_id=dynamic["candidate_id"], settlement_policy=settlement,
        continuation_last_session=end)
    initial = float(account["initial_research_nav_usd"])
    paths = {}
    for key, ledger in ledgers.items():
        current = _path_summary(ledger, initial)
        current["versus_legacy"] = _versus(current, _path_summary(old[key], initial),
                                           _common_settlement_prefix(old[key], ledger, rows, settlement))
        paths[key] = current
    summary = {
        "schema": "qsl.research.post_r9_date_effective_settlement_study.v1",
        "policy_id": settlement["policy_id"],
        "calendar_id": settlement["calendar_id"],
        "settlement_policy_sha256": r7.SETTLEMENT_POLICY_SHA256,
        "attribution": "settlement_policy_only",
        "broker_buying_power_inferred": False,
        "research_only": True,
        "development": True,
        "paper_authorized": False,
        "shadow_authorized": False,
        "live_authorized": False,
        "compared_paths": ["B0_10bps", "dynamic_10bps"],
        "legacy_baseline": "read_only_r7_prefix_plus_r9_extension",
        "paths": paths,
    }
    output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = {}
    for key, ledger in sorted(ledgers.items()):
        hashes[key] = _write_exclusive(output_root / f"private_daily_{key}.json", r7._canonical(ledger))
    summary["private_ledger_sha256"] = hashes
    content = r7._canonical(summary)
    summary_sha = _write_exclusive(output_root / "summary.json", content)
    return {"status": "COMPLETE", "policy_id": settlement["policy_id"],
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
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.raw_root, args.r6_root, args.materialized, args.r7_archive_root,
                         args.r8_archive_root, args.r9_archive_root, args.future_root,
                         args.output_root), sort_keys=True))
