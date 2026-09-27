"""Offline R9 continuation of the frozen R7/R8 owner-account experiments."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import math
import os
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "r9_frozen_validation_protocol.v1.json"
PROTOCOL_SHA256 = "27109d8d4ee5c5da594977b9fa5e8c7f825707dc789d48e5b8cf34c500e25ca6"
R8_SUMMARY_SHA256 = "48afaf92fd31cd1168f22e58baa33666f7209163c8e8e7d7fe406345c65abf02"
SYMBOLS = ("QQQ", "TQQQ", "QQQM", "SOXL", "SOXX", "BOXX")
EXECUTION_SYMBOLS = ("QQQM", "TQQQ", "SOXL", "SOXX", "BOXX")
OWNERS = ("outer", "tqqq", "soxl")
OWNER_SYMBOLS = {"outer": ("QQQM", "BOXX"), "tqqq": ("TQQQ",),
                 "soxl": ("SOXL", "SOXX", "BOXX")}
BAR_FIELDS = ("o", "h", "l", "c", "v")
FIRST_EXTENSION = "2025-01-01"
LAST_EXTENSION = "2026-08-25"
PREFIX_END = "2024-12-31"
PRIVATE_BUCKET = "qsl-research-evidence-831478360303"
PRIVATE_PREFIX = "research/v2/input/r9-temporal-extension-20260926-001/"
# SHA of the private user_attested R9 record, not of a supplier document.
LICENSE_BASIS_SHA256 = "779f219f35f6b396caba0c787337b9593d9eb98597895afd280eb4dca1ccf73f"
R9_PRIOR_PROBE_GENERATION = "1790428811648938"
R9_PRIOR_PROBE_BYTES = 180
R9_PRIOR_PROVIDER_PAGES = 1
R9_PRIOR_PROVIDER_BYTES = 46047
R9_PRIOR_STORAGE_OPERATIONS = 3
R9_PRIOR_STORAGE_TRANSFER_BYTES = 2 * R9_PRIOR_PROBE_BYTES
ACTION_TYPES = frozenset({
    "capital_gains_distributions", "cash_dividends", "cash_mergers", "forward_splits",
    "name_changes", "partial_calls", "redemptions", "reorganizations", "reverse_splits",
    "rights_distributions", "spin_offs", "stock_and_cash_mergers", "stock_dividends",
    "stock_mergers", "unit_splits", "worthless_removals",
})
ACCOUNTED_ACTION_TYPES = frozenset({"forward_splits", "cash_dividends"})


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("R9_INPUT_FILE_INVALID:" + path.name)
    return json.loads(path.read_bytes())


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def _protocol() -> dict:
    if _sha(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("R9_PROTOCOL_CHANGED")
    return _read_json(PROTOCOL)


def _engines():
    return (importlib.import_module("r7_joint_account_compare"),
            importlib.import_module("r8_joint_allocation_compare"))


def _manifest_entries(root: Path) -> tuple[dict, dict[tuple[str, str], dict]]:
    if LICENSE_BASIS_SHA256 is None:
        raise ValueError("R9_LICENSE_RECORD_UNVERIFIED")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("R9_FUTURE_ROOT_INVALID")
    manifest = _read_json(root / "manifest.json")
    try:
        retrieved_at = datetime.fromisoformat(str(manifest.get("retrieved_at", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("R9_FUTURE_MANIFEST_TIME_INVALID") from exc
    if (retrieved_at.tzinfo is None
            or retrieved_at.astimezone(timezone.utc).date().isoformat() <= LAST_EXTENSION):
        raise ValueError("R9_FUTURE_MANIFEST_TIME_INVALID")
    if (manifest.get("schema_version") != "qsl.research.raw_sip_input.v1"
            or manifest.get("source") != "alpaca.stocks.bars.v2_and_corporate_actions.v1"
            or manifest.get("feed") != "sip" or manifest.get("price_adjustment") != "raw"
            or manifest.get("calendar") != "XNYS" or manifest.get("timezone") != "America/New_York"
            or manifest.get("currency") != "USD"
            or manifest.get("scope") != PRIVATE_PREFIX
            or manifest.get("license_basis_record_sha256") != LICENSE_BASIS_SHA256
            or manifest.get("no_order") is not True or manifest.get("research_only") is not True
            or manifest.get("execution_authorized") is not False):
        raise ValueError("R9_FUTURE_MANIFEST_INVALID")
    probe = manifest.get("write_probe", {})
    if (probe.get("uri") != f"gs://{PRIVATE_BUCKET}/{PRIVATE_PREFIX}_write_probe.json"
            or probe.get("generation") != R9_PRIOR_PROBE_GENERATION
            or probe.get("bytes") != R9_PRIOR_PROBE_BYTES
            or len(str(probe.get("sha256", ""))) != 64):
        raise ValueError("R9_FUTURE_WRITE_PROBE_REFERENCE_INVALID")
    total_pages = sum(len(item.get("pages", [])) for item in manifest.get("inputs", []))
    if (manifest.get("prior_provider_page_requests") != R9_PRIOR_PROVIDER_PAGES
            or manifest.get("prior_provider_response_bytes") != R9_PRIOR_PROVIDER_BYTES
            or manifest.get("prior_storage_operations") != R9_PRIOR_STORAGE_OPERATIONS
            or manifest.get("prior_storage_transfer_bytes") != R9_PRIOR_STORAGE_TRANSFER_BYTES
            or total_pages + R9_PRIOR_PROVIDER_PAGES > 60
            or manifest.get("provider_page_requests", 0) + R9_PRIOR_PROVIDER_PAGES > 60
            or manifest.get("provider_response_bytes", 0) + R9_PRIOR_PROVIDER_BYTES > 128 * 1024 * 1024):
        raise ValueError("R9_FUTURE_SOURCE_BUDGET_EXCEEDED")
    page_bytes = [page.get("bytes") for item in manifest.get("inputs", [])
                  for page in item.get("pages", [])]
    if (any(not isinstance(size, int) or isinstance(size, bool) or size <= 0 for size in page_bytes)
            or sum(page_bytes) != manifest.get("provider_response_bytes")
            or manifest.get("provider_page_requests") != total_pages
            or R9_PRIOR_STORAGE_OPERATIONS + 1 + 3 * (total_pages + 1) > 200
            or (R9_PRIOR_STORAGE_TRANSFER_BYTES + R9_PRIOR_PROBE_BYTES
                + 2 * (sum(page_bytes) + (root / "manifest.json").stat().st_size))
            > 1024 * 1024 * 1024):
        raise ValueError("R9_FUTURE_SOURCE_BUDGET_MISMATCH")
    entries = {}
    for item in manifest.get("inputs", []):
        key = (item.get("symbol"), item.get("kind"))
        if key in entries or key[0] not in SYMBOLS or key[1] not in ("bars", "actions"):
            raise ValueError("R9_FUTURE_MANIFEST_INPUT_INVALID")
        if item.get("complete_pagination") is not True or not item.get("pages"):
            raise ValueError("R9_FUTURE_PAGINATION_INCOMPLETE")
        expected_request = _expected_request(*key)
        if item.get("request") != expected_request:
            raise ValueError("R9_FUTURE_REQUEST_CONTRACT_MISMATCH")
        entries[key] = item
    if set(entries) != {(symbol, kind) for symbol in SYMBOLS for kind in ("bars", "actions")}:
        raise ValueError("R9_FUTURE_INPUT_SET_INCOMPLETE")
    return manifest, entries


def _expected_request(symbol: str, kind: str) -> dict:
    if kind == "bars":
        return {"timeframe": "1Day", "start": "2025-01-01T00:00:00-05:00",
                "end": "2026-08-25T23:59:59-04:00", "asof": LAST_EXTENSION,
                "feed": "sip", "adjustment": "raw", "currency": "USD",
                "sort": "asc", "limit": "10000"}
    return {"symbols": symbol, "region": "us", "start": "2024-10-01",
            "end": LAST_EXTENSION, "data_quality": "all", "sort": "asc", "limit": "1000"}


def _verified_future(root: Path, base_rows: list[dict], base_actions: dict,
                     indicators: dict) -> tuple[list[dict], dict, dict]:
    """Append hash-verified raw rows while retaining the old prefix byte-for-byte."""
    manifest, inputs = _manifest_entries(root)
    future_bars: dict[str, dict[str, dict]] = {}
    future_actions: dict[str, dict] = {}
    page_hashes = {}
    for (symbol, kind), spec in inputs.items():
        payload_items = []
        seen_tokens = set()
        for page_index, page in enumerate(spec["pages"], 1):
            rel = Path(kind) / symbol / f"page-{page_index:03d}.json"
            path = root / rel
            expected_uri = f"gs://{PRIVATE_BUCKET}/{PRIVATE_PREFIX}{kind}/{symbol}/page-{page_index:03d}.json"
            if (path.is_symlink() or not path.is_file()
                    or page.get("uri") != expected_uri
                    or not str(page.get("generation", "")).isdigit()
                    or path.stat().st_size != page.get("bytes")
                    or _sha(path) != page.get("sha256")):
                raise ValueError("R9_FUTURE_PAGE_MISMATCH:" + symbol + ":" + kind)
            payload = _read_json(path)
            if payload.get("symbol") not in (None, symbol):
                raise ValueError("R9_FUTURE_PAGE_SYMBOL_MISMATCH")
            token = payload.get("next_page_token")
            if page_index < len(spec["pages"]):
                if (not isinstance(token, str) or not token or len(token) > 2048
                        or token in seen_tokens):
                    raise ValueError("R9_FUTURE_PAGINATION_INVALID")
                seen_tokens.add(token)
            elif token not in (None, ""):
                raise ValueError("R9_FUTURE_PAGINATION_INCOMPLETE")
            payload_items.append(payload)
            page_hashes[f"{symbol}:{kind}:{page_index}"] = page["sha256"]
        if kind == "bars":
            bars = [bar for payload in payload_items for bar in payload.get("bars", [])]
            dates = [str(bar.get("t", ""))[:10] for bar in bars]
            if dates != sorted(set(dates)) or len(dates) != spec.get("count"):
                raise ValueError("R9_FUTURE_BAR_DATES_INVALID:" + symbol)
            by_day = {}
            for day, bar in zip(dates, bars, strict=True):
                values = [bar.get(field) for field in BAR_FIELDS]
                stamp = bar.get("t")
                if not isinstance(stamp, str):
                    raise ValueError("R9_FUTURE_BAR_TIMESTAMP_INVALID:" + symbol)
                try:
                    parsed_stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError("R9_FUTURE_BAR_TIMESTAMP_INVALID:" + symbol) from exc
                if parsed_stamp.tzinfo is None:
                    raise ValueError("R9_FUTURE_BAR_TIMESTAMP_INVALID:" + symbol)
                if (not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) for value in values)
                        or any(values[index] <= 0 for index in range(4)) or values[4] < 0
                        or values[1] < max(values[0], values[2], values[3])
                        or values[2] > min(values[0], values[3])):
                    raise ValueError("R9_FUTURE_BAR_INVALID:" + symbol + ":" + day)
                by_day[day] = bar
            future_bars[symbol] = by_day
            if (not isinstance(spec.get("count"), int) or isinstance(spec.get("count"), bool)
                    or sum(len(payload.get("bars", [])) for payload in payload_items) != spec.get("count")):
                raise ValueError("R9_FUTURE_BAR_COUNT_INVALID:" + symbol)
            first_time = next((bar["t"] for payload in payload_items for bar in payload["bars"]), None)
            last_time = next((bar["t"] for payload in reversed(payload_items)
                              for bar in reversed(payload["bars"])), None)
            if first_time is not None:
                first_normalized = datetime.fromisoformat(first_time.replace("Z", "+00:00")).astimezone(
                    timezone.utc).isoformat().replace("+00:00", "Z")
                last_normalized = datetime.fromisoformat(last_time.replace("Z", "+00:00")).astimezone(
                    timezone.utc).isoformat().replace("+00:00", "Z")
                if (first_normalized != spec.get("first_bar_time")
                        or last_normalized != spec.get("last_bar_time")):
                    raise ValueError("R9_FUTURE_BAR_COVERAGE_MISMATCH:" + symbol)
        else:
            action_pages = [payload.get("corporate_actions", {}) for payload in payload_items]
            if any(not isinstance(item, dict) or set(item) - ACTION_TYPES for item in action_pages):
                raise ValueError("R9_FUTURE_ACTION_SCHEMA_INVALID:" + symbol)
            count = 0
            for action_page in action_pages:
                for category, items in action_page.items():
                    if not isinstance(items, list):
                        raise ValueError("R9_FUTURE_ACTION_SCHEMA_INVALID:" + symbol)
                    count += len(items)
                    if category not in ACCOUNTED_ACTION_TYPES and items:
                        raise ValueError("R9_FUTURE_ACTION_KIND_UNSUPPORTED:" + symbol + ":" + category)
            if count != spec.get("count"):
                raise ValueError("R9_FUTURE_ACTION_COUNT_INVALID:" + symbol)
            actions = [dict(event, symbol=event.get("symbol") or symbol)
                       for action_page in action_pages
                       for event in action_page.get("forward_splits", [])]
            dividends = [dict(event, symbol=event.get("symbol") or symbol)
                         for action_page in action_pages
                         for event in action_page.get("cash_dividends", [])]
            for event in actions:
                if (event.get("symbol") != symbol or not event.get("ex_date")
                        or not math.isfinite(float(event.get("old_rate", 0)))
                        or not math.isfinite(float(event.get("new_rate", 0)))
                        or float(event["old_rate"]) <= 0 or float(event["new_rate"]) <= 0):
                    raise ValueError("R9_SPLIT_EVENT_INVALID")
            for event in dividends:
                if (event.get("symbol") != symbol or not all(event.get(key) for key in
                        ("ex_date", "payable_date", "process_date"))
                        or event["payable_date"] < event["ex_date"]
                        or not math.isfinite(float(event.get("rate", -1)))
                        or float(event["rate"]) < 0):
                    raise ValueError("R9_DIVIDEND_EVENT_INVALID")
            if symbol == "QQQ" and actions:
                raise ValueError("R9_QQQ_SPLIT_ADJUSTMENT_NOT_IMPLEMENTED")
            future_actions[symbol] = {"forward_splits": actions, "cash_dividends": dividends}
    days_by_symbol = {symbol: set(bars) for symbol, bars in future_bars.items()}
    days = sorted(days_by_symbol[SYMBOLS[0]])
    if not days or any(days_by_symbol[symbol] != set(days) for symbol in SYMBOLS):
        raise ValueError("R9_FUTURE_COMMON_DATES_INVALID")
    if days[0] < FIRST_EXTENSION or days[-1] != LAST_EXTENSION:
        raise ValueError("R9_FUTURE_RANGE_INVALID")
    old_last = base_rows[-1]["date"]
    if old_last != PREFIX_END or days[0] <= old_last:
        raise ValueError("R9_PREFIX_BOUNDARY_INVALID")
    expected = sorted(day for day in indicators if old_last < day <= LAST_EXTENSION)
    if days != expected:
        raise ValueError("R9_FUTURE_SESSION_COVERAGE_MISMATCH")
    if any(day not in indicators for day in (old_last, *days)):
        raise ValueError("R9_SIGNAL_SESSION_MISSING")
    merged_rows = [dict(row) for row in base_rows]
    for day in days:
        row = {"date": day}
        for symbol in SYMBOLS:
            bar = future_bars[symbol][day]
            row[symbol.lower() + "_open"] = float(bar["o"])
            row[symbol.lower() + "_close"] = float(bar["c"])
        merged_rows.append(row)
    # Prefix replay below must still receive the untouched original action book.
    merged_actions = copy.deepcopy(base_actions)
    # QQQ is a raw-price signal input, never an owned security in the R7/R8 books.
    # Its action page is validated above but does not join the execution action book.
    for symbol in EXECUTION_SYMBOLS:
        existing = merged_actions.get(symbol, {"forward_splits": [], "cash_dividends": []})
        extra = future_actions[symbol]
        old_events = {
            (kind, _canonical(event))
            for kind, values in (("split", existing.get("forward_splits", [])),
                                 ("dividend", existing.get("cash_dividends", [])))
            for event in values
        }
        for kind, values in (("split", extra["forward_splits"]),
                             ("dividend", extra["cash_dividends"])):
            for event in values:
                if event.get("symbol") != symbol or not event.get("ex_date"):
                    raise ValueError("R9_CORPORATE_ACTION_IDENTITY_INVALID")
                if event["ex_date"] <= old_last:
                    if (kind, _canonical(event)) not in old_events:
                        raise ValueError("R9_OLD_CORPORATE_ACTION_CONFLICT")
                    continue
                if event["ex_date"] not in days:
                    raise ValueError("R9_CORPORATE_ACTION_OUTSIDE_EXTENSION")
                if (kind, _canonical(event)) in old_events:
                    continue
                if kind == "split":
                    if any(item.get("ex_date") == event["ex_date"]
                           for item in existing.get("forward_splits", [])):
                        raise ValueError("R9_SPLIT_EVENT_CONFLICT")
                    if (not math.isfinite(float(event["old_rate"]))
                            or not math.isfinite(float(event["new_rate"]))
                            or float(event["old_rate"]) <= 0 or float(event["new_rate"]) <= 0):
                        raise ValueError("R9_SPLIT_EVENT_INVALID")
                    existing.setdefault("forward_splits", []).append(event)
                    old_events.add((kind, _canonical(event)))
                else:
                    if (not event.get("process_date") or not event.get("payable_date")
                            or event["payable_date"] < event["ex_date"]
                            or not math.isfinite(float(event["rate"]))
                            or float(event["rate"]) < 0):
                        raise ValueError("R9_DIVIDEND_EVENT_INVALID")
                    existing.setdefault("cash_dividends", []).append(event)
                    old_events.add((kind, _canonical(event)))
        merged_actions[symbol] = existing
    if [row["date"] for row in merged_rows[:len(base_rows)]] != [row["date"] for row in base_rows]:
        raise ValueError("R9_OLD_PREFIX_MUTATED")
    return merged_rows, merged_actions, {
        "future_manifest_sha256": _sha(root / "manifest.json"),
        "future_page_sha256": page_hashes,
        "future_session_count": len(days),
        "first_extension_session": days[0],
        "last_extension_session": days[-1],
        "retrieved_at": manifest["retrieved_at"],
        "license_basis_record_sha256": manifest["license_basis_record_sha256"],
        "source": manifest["source"],
    }


def _load_archive(root: Path, expected_summary_sha: str, keys: set[str]) -> tuple[dict, dict[str, list[dict]]]:
    summary_path = root / "summary.json"
    if summary_path.is_symlink() or _sha(summary_path) != expected_summary_sha:
        raise ValueError("R9_ARCHIVE_SUMMARY_MISMATCH")
    summary = _read_json(summary_path)
    expected_hashes = summary.get("private_ledger_sha256", {})
    if not keys <= expected_hashes.keys():
        raise ValueError("R9_ARCHIVE_LEDGER_INDEX_INCOMPLETE")
    ledgers = {}
    for key in keys:
        path = root / f"private_daily_{key}.json"
        if path.is_symlink() or _sha(path) != expected_hashes[key]:
            raise ValueError("R9_ARCHIVE_LEDGER_HASH_MISMATCH:" + key)
        ledgers[key] = _read_json(path)
    return summary, ledgers


def _normalize_risk_key(value: object) -> object:
    if isinstance(value, list):
        return [_normalize_risk_key(item) for item in value]
    if isinstance(value, dict):
        result = {_normalize_risk_key(key): _normalize_risk_key(item) for key, item in value.items()
                  if key != "nasdaq_lookthrough_to_nav"}
        if "nasdaq_lookthrough_to_nav" in value:
            if "nominal_equity_index_exposure_to_nav" in value:
                raise ValueError("R9_RISK_KEY_AMBIGUOUS")
            result["nominal_equity_index_exposure_to_nav"] = _normalize_risk_key(
                value["nasdaq_lookthrough_to_nav"])
        return result
    return value


def _compare_prefix(rebuilt: dict[str, list[dict]], archived: dict[str, list[dict]]) -> dict:
    report = {}
    for key, old_rows in archived.items():
        prefix = [row for row in rebuilt[key] if row["date"] <= PREFIX_END]
        if len(old_rows) != 444 or len(prefix) != 444:
            raise ValueError("R9_PREFIX_ROW_COUNT_INVALID:" + key)
        if _canonical(_normalize_risk_key(prefix)) != _canonical(_normalize_risk_key(old_rows)):
            raise ValueError("R9_PREFIX_LEDGER_MISMATCH:" + key)
        if prefix[0]["date"] != "2023-03-28" or prefix[-1]["date"] != PREFIX_END:
            raise ValueError("R9_PREFIX_DATE_RANGE_INVALID:" + key)
        report[key] = {"rows": len(prefix), "first": prefix[0]["date"], "last": prefix[-1]["date"],
                       "exact_after_risk_key_alias": True}
    return report


def _drawdown(rows: list[dict], initial_nav: float, initial_date: str) -> dict:
    peak, peak_day = initial_nav, initial_date
    max_amount = 0.0
    max_amount_peak, max_amount_trough = initial_date, initial_date
    max_fraction = 0.0
    fraction_amount = 0.0
    fraction_peak, fraction_trough = initial_date, initial_date
    fraction_peak_value = initial_nav
    for row in rows:
        nav, day = float(row["economic_nav_close_usd"]), row["date"]
        if nav > peak:
            peak, peak_day = nav, day
        amount = peak - nav
        fraction = amount / peak
        if amount > max_amount:
            max_amount, max_amount_peak, max_amount_trough = amount, peak_day, day
        if fraction > max_fraction:
            max_fraction = fraction
            fraction_amount = amount
            fraction_peak, fraction_trough = peak_day, day
            fraction_peak_value = peak
    recovered = next((row["date"] for row in rows if row["date"] > fraction_trough
                      and float(row["economic_nav_close_usd"]) >= fraction_peak_value), None)
    return {"max_drawdown_fraction": max_fraction,
            "drawdown_usd_at_max_drawdown_fraction": fraction_amount,
            "max_drawdown_fraction_peak_date": fraction_peak,
            "max_drawdown_fraction_trough_date": fraction_trough,
            "max_drawdown_fraction_recovery_date": recovered,
            "max_drawdown_fraction_unrecovered": max_fraction > 0 and recovered is None,
            "max_dollar_drawdown_usd": max_amount,
            "max_dollar_drawdown_peak_date": max_amount_peak,
            "max_dollar_drawdown_trough_date": max_amount_trough}


def _metrics(ledger: list[dict], prefix: list[dict]) -> tuple[dict, list[float]]:
    extension = [row for row in ledger if row["date"] > PREFIX_END]
    if not extension or extension[0]["date"] < FIRST_EXTENSION or extension[-1]["date"] != LAST_EXTENSION:
        raise ValueError("R9_EXTENSION_WINDOW_INVALID")
    base = float(prefix[-1]["economic_nav_close_usd"])
    previous = base
    returns = []
    for row in extension:
        nav = float(row["economic_nav_close_usd"])
        daily = nav / previous - 1.0
        if not math.isfinite(daily) or daily <= -1:
            raise ValueError("R9_DAILY_RETURN_INVALID")
        returns.append(daily)
        previous = nav
    total = extension[-1]["economic_nav_close_usd"] / base - 1.0
    dd_extension = _drawdown(extension, base, PREFIX_END)
    old_high_row = max(prefix, key=lambda row: float(row["economic_nav_close_usd"]))
    inherited_high = float(old_high_row["economic_nav_close_usd"])
    inherited = _drawdown(extension, inherited_high, old_high_row["date"])
    owner_last = extension[-1]
    result = {
        "start": extension[0]["date"], "end": extension[-1]["date"],
        "sessions": len(extension), "start_nav_2024_12_31_usd": base,
        "first_extension_close_nav_usd": extension[0]["economic_nav_close_usd"],
        "end_nav_usd": extension[-1]["economic_nav_close_usd"],
        "total_return": total,
        "cagr": (extension[-1]["economic_nav_close_usd"] / base) ** (252 / len(extension)) - 1,
        "drawdown_extension_including_2024_12_31": dd_extension,
        "drawdown_inherited_high_water": inherited,
        "total_fees_usd": math.fsum(float(row["total_cost_usd"]) for row in extension),
        "traded_notional_usd": math.fsum(float(row["traded_notional_usd"]) for row in extension),
        "turnover_over_start_nav": math.fsum(float(row["traded_notional_usd"])
                                                for row in extension) / base,
        "shortage_event_count": sum(len(row["shortages"]) for row in extension),
        "action_counts": dict(Counter(row["path"] for row in extension)),
        "average_nasdaq_nominal_exposure_to_nav": math.fsum(
            (row["qqqm_value_usd"] + 3 * row["tqqq_value_usd"]) / row["economic_nav_close_usd"]
            for row in extension) / len(extension),
        "average_semiconductor_nominal_exposure_to_nav": math.fsum(
            (3 * row["soxl_value_usd"] + row["soxx_value_usd"]) / row["economic_nav_close_usd"]
            for row in extension) / len(extension),
        "average_owner_nav_fraction": {
            owner: math.fsum(row["owner_nav_usd"][owner] / row["economic_nav_close_usd"]
                             for row in extension) / len(extension)
            for owner in OWNERS
        },
        "average_owner_security_exposure_to_nav": {
            owner: {symbol: math.fsum(
                row["owner_security_values_usd"][owner][symbol] / row["economic_nav_close_usd"]
                for row in extension) / len(extension)
                    for symbol in OWNER_SYMBOLS[owner]}
            for owner in OWNERS
        },
        "ending_owner_nav_usd": owner_last["owner_nav_usd"],
        "ending_owner_security_values_usd": owner_last["owner_security_values_usd"],
        "ending_owner_settled_cash_usd": owner_last["owner_settled_cash_usd"],
        "ending_owner_pending_sale_usd": owner_last["owner_pending_sale_usd"],
        "ending_owner_receivable_usd": owner_last["owner_receivable_usd"],
        "source_assurance": "R6 Twelve single-source signals plus Alpaca SIP raw execution; retrospective process_date proxy; no strict PIT or cross-provider certification",
    }
    return result, returns


def _bootstrap(differences: list[float]) -> dict:
    spec = _protocol()["analysis"]["bootstrap"]
    if not differences or not all(math.isfinite(value) for value in differences):
        raise ValueError("R9_BOOTSTRAP_SERIES_INVALID")
    n = len(differences)
    rng = random.Random(spec["seed"])
    samples = []
    blocks = math.ceil(n / spec["block_sessions"])
    for _ in range(spec["draws"]):
        values = []
        for _ in range(blocks):
            start = rng.randrange(n)
            values.extend(differences[(start + offset) % n]
                          for offset in range(spec["block_sessions"]))
        samples.append(252 * math.fsum(values[:n]) / n)
    samples.sort()

    def quantile(p: float) -> float:
        index = p * (len(samples) - 1)
        lower = math.floor(index)
        fraction = index - lower
        if lower + 1 == len(samples):
            return samples[lower]
        return samples[lower] * (1 - fraction) + samples[lower + 1] * fraction

    return {"series": "paired_daily_log_return_R8_minus_B0_10bps",
            "block_sessions": spec["block_sessions"], "draws": spec["draws"],
            "seed": spec["seed"], "delta_g_point": 252 * math.fsum(differences) / n,
            "percentile_95": [quantile(0.025), quantile(0.975)],
            "interpretation": spec["interpretation"]}


def analyze(raw_root: Path, r6_root: Path, materialized_path: Path,
            r7_archive_root: Path, r8_archive_root: Path,
            future_root: Path) -> tuple[dict, dict[str, list[dict]]]:
    protocol = _protocol()
    r7, r8 = _engines()
    r7_policy = r7._policy()
    r8_policy = r8._policy()
    base_rows, base_actions, indicators, source = r7._load_inputs(
        raw_root, r6_root, materialized_path, r7_policy)
    if base_rows[-1]["date"] != PREFIX_END or len(base_rows) != 753:
        raise ValueError("R9_ORIGINAL_INPUT_PREFIX_INVALID")
    rows, actions, future_meta = _verified_future(future_root, base_rows, base_actions, indicators)
    r7_keys = {f"{name}_{cost}bps" for name in ("B0", "B1", "B2", "B3") for cost in (5, 10, 15)}
    r8_keys = {f"dynamic_{cost}bps" for cost in (5, 10, 15)}
    _, archived_r7 = _load_archive(r7_archive_root,
                                   protocol["r7_formal_summary_sha256"], r7_keys)
    _, archived_r8 = _load_archive(r8_archive_root, R8_SUMMARY_SHA256, r8_keys)
    archive = {**archived_r7, **archived_r8}
    prefix_ledgers, checkpoints, results, ledgers = {}, {}, {}, {}
    for action in ("B0", "B1", "B2", "B3"):
        for cost in (5, 10, 15):
            key = f"{action}_{cost}bps"
            checkpoints[key] = {}
            _metrics_unused, prefix_ledgers[key] = r7._replay(
                base_rows, base_actions, indicators, source["tqqq_contract"], r7_policy,
                path_name=action, cost_bps=cost, checkpoint_out=checkpoints[key])
    for cost in (5, 10, 15):
        key = f"dynamic_{cost}bps"
        selector = r8._selector(indicators, source["tqqq_contract"], base_actions, r7_policy, r8_policy)
        checkpoints[key] = {}
        _metrics_unused, prefix_ledgers[key] = r7._replay(
            base_rows, base_actions, indicators, source["tqqq_contract"], r7_policy,
            path_name="B0", cost_bps=cost, action_selector=selector,
            candidate_id=r8_policy["candidate_id"], checkpoint_out=checkpoints[key])
    prefix_checks = _compare_prefix(prefix_ledgers, archive)
    checkpoint_checks = {
        key: {"last_date": value["last_date"],
              "last_global_index": value["last_global_index"],
              "prior_nav_usd": value["prior_nav_usd"],
              "previous_action": value["previous_action"],
              "checkpoint_sha256": hashlib.sha256(_canonical(value)).hexdigest()}
        for key, value in checkpoints.items()
    }
    for action in ("B0", "B1", "B2", "B3"):
        for cost in (5, 10, 15):
            key = f"{action}_{cost}bps"
            _metrics_unused, ledgers[key] = r7._replay(
                rows, actions, indicators, source["tqqq_contract"], r7_policy,
                path_name=action, cost_bps=cost, continuation_last_session=LAST_EXTENSION,
                continuation_from_session=PREFIX_END,
                continuation_checkpoint=checkpoints[key])
            results[key], _ = _metrics(ledgers[key], prefix_ledgers[key])
            results[key]["action_counts"] = {
                name: len(ledgers[key]) if name == action else 0
                for name in ("B0", "B1", "B2", "B3")}
    for cost in (5, 10, 15):
        key = f"dynamic_{cost}bps"
        selector = r8._selector(indicators, source["tqqq_contract"], actions, r7_policy, r8_policy)
        _metrics_unused, ledgers[key] = r7._replay(
            rows, actions, indicators, source["tqqq_contract"], r7_policy,
            path_name="B0", cost_bps=cost, action_selector=selector,
            candidate_id=r8_policy["candidate_id"], continuation_last_session=LAST_EXTENSION,
            continuation_from_session=PREFIX_END,
            continuation_checkpoint=checkpoints[key])
        metrics, _ = _metrics(ledgers[key], prefix_ledgers[key])
        counts = Counter(row["path"] for row in ledgers[key])
        metrics["action_counts"] = {action: counts[action] for action in r8_policy["action_ids"]}
        metrics["action_transitions"] = sum(
            ledgers[key][index]["path"] != ledgers[key][index - 1]["path"]
            for index in range(1, len(ledgers[key])))
        results[key] = metrics
    paired = {}
    for cost in (5, 10, 15):
        r8_key, b0_key = f"dynamic_{cost}bps", f"B0_{cost}bps"
        dynamic_rows, b0_rows = ledgers[r8_key], ledgers[b0_key]
        dynamic_previous = [prefix_ledgers[r8_key][-1], *dynamic_rows[:-1]]
        b0_previous = [prefix_ledgers[b0_key][-1], *b0_rows[:-1]]
        paired[cost] = [math.log(float(a["economic_nav_close_usd"]) /
                                  float(pa["economic_nav_close_usd"]))
                        - math.log(float(b["economic_nav_close_usd"]) /
                                   float(pb["economic_nav_close_usd"]))
                        for a, b, pa, pb in zip(dynamic_rows, b0_rows,
                                                 dynamic_previous, b0_previous, strict=True)]
    delta_g_10 = 252 * math.fsum(paired[10]) / len(paired[10])
    return ({"schema": "qsl.research.r9_frozen_policy_validation_result.v1",
             "candidate_id": r8_policy["candidate_id"], "research_only": True,
             "development": True, "not_untouched_oos": True,
             "paper_authorized": False, "shadow_authorized": False, "live_authorized": False,
             "protocol_sha256": PROTOCOL_SHA256,
             "r7_policy_sha256": r7.POLICY_SHA256,
             "r8_policy_sha256": r8.POLICY_SHA256,
             "raw_manifest_sha256": r7_policy["data"]["raw_manifest_sha256"],
             "r6_manifest_sha256": r7_policy["data"]["r6_manifest_sha256"],
             "r6_materialized_file_sha256": r7_policy["data"]["r6_materialized_file_sha256"],
             "r7_archive_summary_sha256": protocol["r7_formal_summary_sha256"],
             "r8_archive_summary_sha256": R8_SUMMARY_SHA256,
             "r7_engine_sha256": _sha(Path(r7.__file__)),
             "r8_engine_sha256": _sha(Path(r8.__file__)),
             "cost_bps_scenarios": [5, 10, 15], "primary_cost_bps": 10,
             "prefix_reconstruction_checks": prefix_checks,
             "continuation_checkpoint_checks": checkpoint_checks,
             "input_validation": future_meta,
             "delta_g_r8_minus_b0_10bps": delta_g_10,
             "bootstrap_10bps": _bootstrap(paired[10]),
             "results": results}, ledgers)


def run(raw_root: Path, r6_root: Path, materialized_path: Path,
        r7_archive_root: Path, r8_archive_root: Path,
        future_root: Path, output_root: Path) -> dict:
    summary, ledgers = analyze(raw_root, r6_root, materialized_path,
                               r7_archive_root, r8_archive_root, future_root)
    output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    ledger_hashes = {}
    for key, ledger in sorted(ledgers.items()):
        content = _canonical([row for row in ledger if row["date"] > PREFIX_END])
        path = output_root / f"private_daily_{key}.json"
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(content)
        ledger_hashes[key] = hashlib.sha256(content).hexdigest()
    summary["private_ledger_sha256"] = ledger_hashes
    content = _canonical(summary)
    with os.fdopen(os.open(output_root / "summary.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)
    return {"status": "COMPLETE", "path_count": len(ledgers),
            "first_date": summary["input_validation"]["first_extension_session"],
            "last_date": summary["input_validation"]["last_extension_session"],
            "summary_sha256": hashlib.sha256(content).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--r6-root", type=Path, required=True)
    parser.add_argument("--materialized", type=Path, required=True)
    parser.add_argument("--r7-archive-root", type=Path, required=True)
    parser.add_argument("--r8-archive-root", type=Path, required=True)
    parser.add_argument("--future-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.raw_root, args.r6_root, args.materialized,
                         args.r7_archive_root, args.r8_archive_root,
                         args.future_root, args.output_root), sort_keys=True))
