"""Pure, deterministic TQQQ dual-drive core replay boundary."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
import re
from typing import Any, Mapping

from us_equity_strategies.backtest.session_asof_contract import SessionClose, SessionContractError
from us_equity_strategies.strategies.tqqq_growth_income import TqqqDualDriveCoreDecision
from us_equity_strategies.strategies.tqqq_growth_income import TqqqDualDriveCoreInput, evaluate_tqqq_dual_drive_core

_MANIFEST_SCHEMA = "qsl.research.tqqq_dual_drive_core_input_manifest.v1"
_PROFILE_ID = "tqqq_dual_drive_core_research_replay_v1"
_ANCHOR_COMMIT = "0c42ceb776672a97a37218b2cfe04e30b1c9aadd"
_COLUMNS = ("trading_date", "close_at_utc", "qqq_close", "tqqq_open", "tqqq_close", "qqqm_open", "qqqm_close")
_PUBLIC_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_PROVENANCE_KEYS = frozenset({"calendar", "fields", "retrieved_at_utc"})
_CALENDAR_KEYS = frozenset({"close_time_field_id", "dataset_id", "provider_id", "revision_sha256", "session_field_id"})
_FIELD_KEYS = frozenset({"dataset_id", "field_id", "provider_id", "revision_sha256"})
_MARKET_FIELDS = ("QQQ.close", "TQQQ.open", "TQQQ.close", "QQQM.open", "QQQM.close")
_POLICIES = {
    "calendar": "DECLARED_SESSION_SEQUENCE_IS_REPLAY_AUTHORITY_NO_INFERENCE",
    "corporate_actions": "EMBEDDED_IN_ADJUSTED_PRICES_NO_SEPARATE_DIVIDEND_OR_SPLIT_EVENT_QUANTITY_UNCHANGED",
    "duplicates": "REJECT", "missing": "REJECT_ANY_EMPTY_FIELD_OR_NONALIGNED_SYMBOL_SESSION",
    "ordering": "STRICT_TRADING_DATE_ASCENDING", "price_basis": "split_and_distribution_adjusted_ohlc_total_return_v1",
    "revisions": "IMMUTABLE_BYTES_NEW_DIGEST_IS_NEW_INPUT_OLD_IDENTITY_MUST_NOT_CHANGE",
}


class TqqqCoreReplayError(ValueError):
    """Stable fail-closed replay input error."""


@dataclass(frozen=True)
class TqqqCoreMarketRow:
    session: SessionClose
    qqq_close: float
    tqqq_open: float
    tqqq_close: float
    qqqm_open: float
    qqqm_close: float


@dataclass(frozen=True)
class TqqqCoreInputEnvelope:
    rows: tuple[TqqqCoreMarketRow, ...]
    input_sha256: str
    manifest_sha256: str
    artifact_sha256: str
    canonical_manifest_bytes: bytes
    canonical_artifact_bytes: bytes


def _fail(code: str) -> None:
    raise TqqqCoreReplayError(code) from None


def _canonical_json(value: object, *, terminal_lf: bool = True) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("INPUT_WIRE_INVALID")
    return raw + (b"\n" if terminal_lf else b"")


def _float_wire(value: object) -> object:
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("INPUT_WIRE_INVALID")
        return (0.0 if value == 0.0 else value).hex()
    if isinstance(value, dict):
        return {key: _float_wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_float_wire(item) for item in value]
    if isinstance(value, list):
        return [_float_wire(item) for item in value]
    return value


def core_decision_bytes(decision: TqqqDualDriveCoreDecision) -> bytes:
    if type(decision) is not TqqqDualDriveCoreDecision:
        _fail("INPUT_WIRE_INVALID")
    return _canonical_json(_float_wire(asdict(decision)), terminal_lf=False)


def _mapping(value: object, expected: frozenset[str], code: str) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != expected:
        _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _fail(code)
    return value


def _public(value: object, code: str) -> str:
    if type(value) is not str or not _PUBLIC_ID.fullmatch(value):
        _fail(code)
    return value


def _number(value: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _fail("PRICE_INVALID")
    if not math.isfinite(number) or number <= 0.0 or format(number, ".17g") != value:
        _fail("PRICE_INVALID")
    return number


def _parse_provenance(value: object) -> tuple[dict[str, str], str]:
    provenance = _mapping(value, _PROVENANCE_KEYS, "PROVENANCE_INVALID")
    if type(provenance["retrieved_at_utc"]) is not str or not _UTC.fullmatch(provenance["retrieved_at_utc"]):
        _fail("PROVENANCE_INVALID")
    calendar = _mapping(provenance["calendar"], _CALENDAR_KEYS, "PROVENANCE_INVALID")
    for item in calendar.values():
        _public(item, "PROVENANCE_INVALID") if item != calendar["revision_sha256"] else _sha(item, "PROVENANCE_INVALID")
    fields = _mapping(provenance["fields"], frozenset(_MARKET_FIELDS), "PROVENANCE_INVALID")
    revisions: dict[str, str] = {}
    for key in _MARKET_FIELDS:
        field = _mapping(fields[key], _FIELD_KEYS, "PROVENANCE_INVALID")
        for field_key, item in field.items():
            _sha(item, "PROVENANCE_INVALID") if field_key == "revision_sha256" else _public(item, "PROVENANCE_INVALID")
        revisions[key] = str(field["revision_sha256"])
    return revisions, str(calendar["revision_sha256"])


def parse_tqqq_core_input(manifest_bytes: bytes, artifact_bytes: bytes) -> TqqqCoreInputEnvelope:
    """Validate immutable bytes only; this boundary never reads paths or providers."""
    if type(manifest_bytes) is not bytes or type(artifact_bytes) is not bytes:
        _fail("INPUT_WIRE_INVALID")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("INPUT_WIRE_INVALID")
    expected_manifest = frozenset({"anchor_commit", "artifact", "coverage", "policies", "profile_id", "profile_version", "provenance", "research_only", "schema"})
    manifest = _mapping(manifest, expected_manifest, "MANIFEST_NOT_CANONICAL")
    if _canonical_json(manifest) != manifest_bytes:
        _fail("MANIFEST_NOT_CANONICAL")
    if (manifest["schema"], manifest["profile_id"], manifest["profile_version"], manifest["anchor_commit"], manifest["research_only"]) != (_MANIFEST_SCHEMA, _PROFILE_ID, 1, _ANCHOR_COMMIT, True):
        _fail("PROFILE_IDENTITY_MISMATCH")
    artifact = _mapping(manifest["artifact"], frozenset({"bytes", "row_count", "session_sha256", "sha256"}), "ARTIFACT_IDENTITY_MISMATCH")
    if type(artifact["bytes"]) is not int or type(artifact["row_count"]) is not int or artifact["bytes"] < 1 or artifact["row_count"] < 258:
        _fail("INSUFFICIENT_LOOKBACK")
    if artifact["bytes"] != len(artifact_bytes) or _sha(artifact["sha256"], "ARTIFACT_IDENTITY_MISMATCH") != hashlib.sha256(artifact_bytes).hexdigest() or not _SHA256.fullmatch(str(artifact["session_sha256"])):
        _fail("ARTIFACT_IDENTITY_MISMATCH")
    if _mapping(manifest["policies"], frozenset(_POLICIES), "INPUT_WIRE_INVALID") != _POLICIES:
        _fail("INPUT_WIRE_INVALID")
    revisions, calendar_revision = _parse_provenance(manifest["provenance"])
    try:
        text = artifact_bytes.decode("utf-8")
        parsed = list(csv.reader(text.splitlines()))
    except (UnicodeDecodeError, csv.Error):
        _fail("ARTIFACT_NOT_CANONICAL")
    if not text.endswith("\n") or "\r" in text or not parsed or tuple(parsed[0]) != _COLUMNS or len(parsed) != artifact["row_count"] + 1:
        _fail("ARTIFACT_NOT_CANONICAL")
    rows: list[TqqqCoreMarketRow] = []
    sessions: list[str] = []
    previous: date | None = None
    for raw in parsed[1:]:
        if len(raw) != len(_COLUMNS) or any(cell == "" for cell in raw):
            _fail("MISSING_MARKET_FIELD")
        try:
            session = SessionClose(date.fromisoformat(raw[0]), raw[1])
        except (ValueError, SessionContractError):
            _fail("SESSION_INVALID")
        if previous is not None and session.trading_date <= previous:
            _fail("DUPLICATE_SESSION" if session.trading_date == previous else "SESSION_ORDER_INVALID")
        previous = session.trading_date
        values = tuple(_number(cell) for cell in raw[2:])
        rows.append(TqqqCoreMarketRow(session, *values))
        sessions.append(f"{raw[0]}|{raw[1]}")
    canonical_artifact = ("\n".join(",".join((row.session.trading_date.isoformat(), row.session.close_at_utc, *(format(value, ".17g") for value in (row.qqq_close, row.tqqq_open, row.tqqq_close, row.qqqm_open, row.qqqm_close)))) for row in rows) + "\n").encode()
    canonical_artifact = b",".join(item.encode() for item in _COLUMNS) + b"\n" + canonical_artifact
    if canonical_artifact != artifact_bytes or hashlib.sha256(("\n".join(sessions) + "\n").encode()).hexdigest() != artifact["session_sha256"]:
        _fail("ARTIFACT_NOT_CANONICAL")
    coverage = _mapping(manifest["coverage"], frozenset({"end", "start"}), "COVERAGE_MISMATCH")
    if (coverage["start"], coverage["end"]) != (rows[0].session.trading_date.isoformat(), rows[-1].session.trading_date.isoformat()):
        _fail("COVERAGE_MISMATCH")
    manifest_sha256 = hashlib.sha256(manifest_bytes[:-1]).hexdigest()
    identity = {"schema": _MANIFEST_SCHEMA, "profile_id": _PROFILE_ID, "profile_version": 1, "anchor_commit": _ANCHOR_COMMIT, "manifest_sha256": manifest_sha256, "artifact_sha256": str(artifact["sha256"]), "artifact_bytes": len(artifact_bytes), "session_sha256": str(artifact["session_sha256"]), "field_revision_sha256s": revisions, "calendar_revision_sha256": calendar_revision}
    return TqqqCoreInputEnvelope(tuple(rows), hashlib.sha256(_canonical_json(identity, terminal_lf=False)).hexdigest(), manifest_sha256, str(artifact["sha256"]), manifest_bytes, artifact_bytes)


def _transition(state: dict[str, float], raw_open: dict[str, float], weights: dict[str, float], commission_bps: int, slippage_bps: int) -> tuple[dict[str, float], list[dict[str, object]], dict[str, float]]:
    """Apply the closed sell-then-buy, fractional long-only transition."""
    cash = float(state["cash"])
    quantities = {symbol: float(state[symbol]) for symbol in ("TQQQ", "QQQM")}
    opening_nav = cash + sum(quantities[symbol] * raw_open[symbol] for symbol in quantities)
    threshold = opening_nav * 0.01
    commission_rate, slippage_rate = commission_bps / 10000.0, slippage_bps / 10000.0
    fills: list[dict[str, object]] = []
    commission_total = slippage_total = 0.0
    def trade(symbol: str, side: str, quantity: float) -> None:
        nonlocal cash, commission_total, slippage_total
        if quantity <= 0.0:
            return
        raw = raw_open[symbol]
        fill = raw * (1.0 - slippage_rate if side == "SELL" else 1.0 + slippage_rate)
        notional, commission = quantity * fill, abs(quantity * fill) * commission_rate
        if side == "SELL":
            quantities[symbol] -= quantity
            cash += notional - commission
        else:
            quantities[symbol] += quantity
            cash -= notional + commission
        commission_total += commission
        slippage_total += quantity * abs(fill - raw)
        fills.append({"symbol": symbol, "side": side, "quantity": quantity, "raw_open_price": raw, "fill_price": fill, "raw_open_notional": quantity * raw, "fill_notional": notional, "commission": commission, "slippage_impact": quantity * abs(fill - raw)})
    for symbol in ("TQQQ", "QQQM"):
        target_value = opening_nav * weights[symbol]
        delta = target_value - quantities[symbol] * raw_open[symbol]
        if weights[symbol] == 0.0 and quantities[symbol] > 0.0:
            trade(symbol, "SELL", quantities[symbol])
        elif delta < 0.0 and abs(delta) > threshold:
            trade(symbol, "SELL", min(quantities[symbol], -delta / raw_open[symbol]))
    for symbol in ("TQQQ", "QQQM"):
        target_value = opening_nav * weights[symbol]
        delta = target_value - quantities[symbol] * raw_open[symbol]
        if delta > 0.0 and abs(delta) > threshold:
            fill = raw_open[symbol] * (1.0 + slippage_rate)
            quantity = min(delta / raw_open[symbol], cash / (fill * (1.0 + commission_rate)))
            trade(symbol, "BUY", max(0.0, quantity))
    post_trade_nav = cash + sum(quantities[symbol] * raw_open[symbol] for symbol in quantities)
    costs = {"commission_total": commission_total, "slippage_impact_total": slippage_total, "raw_open_post_trade_nav": post_trade_nav, "conservation_delta": post_trade_nav - (opening_nav - commission_total - slippage_total)}
    return {"cash": 0.0 if cash == 0.0 else cash, **quantities}, fills, costs


def _session_wire(row: TqqqCoreMarketRow) -> dict[str, str]:
    return row.session.to_wire()


def _record(envelope: TqqqCoreInputEnvelope, scenario: dict[str, object], ordinal: int, signal: TqqqCoreMarketRow, execution: TqqqCoreMarketRow, state: dict[str, float]) -> tuple[dict[str, object], dict[str, float]]:
    decision = evaluate_tqqq_dual_drive_core(TqqqDualDriveCoreInput(tuple(item.qqq_close for item in envelope.rows[: ordinal + 257]), state["TQQQ"], state["QQQM"]))
    raw_open = {"TQQQ": execution.tqqq_open, "QQQM": execution.qqqm_open}
    opening_nav = state["cash"] + sum(state[symbol] * raw_open[symbol] for symbol in raw_open)
    next_state, fills, costs = _transition(state, raw_open, decision.target_weights, int(scenario["commission_bps"]), int(scenario["slippage_bps"]))
    holdings = [{"symbol": symbol, "quantity": next_state[symbol], "valuation_price": getattr(execution, symbol.lower() + "_close"), "market_value": next_state[symbol] * getattr(execution, symbol.lower() + "_close")} for symbol in ("TQQQ", "QQQM")]
    closing_equity = next_state["cash"] + sum(item["market_value"] for item in holdings)
    diagnostics = {field: getattr(decision, field) for field in ("above_ma200", "current_risk_active", "ma20", "ma20_slope", "ma200", "positive_ma20_slope", "pullback_low", "pullback_rebound", "pullback_risk_on", "pullback_threshold", "pullback_volatility", "qqq_close", "realized_volatility", "risk_active", "volatility_delever_entry_triggered", "volatility_delever_hysteresis_triggered", "volatility_delever_trigger_reason", "volatility_delever_triggered", "volatility_dynamic_sample_count", "volatility_dynamic_threshold", "volatility_entry_threshold", "volatility_exit_threshold")}
    payload = {"allocation_route": decision.allocation_route, "closing_snapshot": {"as_of_session": _session_wire(execution), "cash": next_state["cash"], "holdings": holdings, "total_equity": closing_equity}, "core_signal": decision.core_signal, "costs": costs, "decision_diagnostics": diagnostics, "execution_session": _session_wire(execution), "fills": fills, "input_sha256": envelope.input_sha256, "opening_snapshot": {"cash": state["cash"], "holdings": {"QQQM": state["QQQM"], "TQQQ": state["TQQQ"]}, "opening_nav": opening_nav, "raw_open_prices": {"QQQM": raw_open["QQQM"], "TQQQ": raw_open["TQQQ"]}}, "ordinal": ordinal, "profile_id": _PROFILE_ID, "profile_sha256": "a7d6330ddcca9a27616e120e16e2352b77287d24db2017d33affdcd3dabe24fc", "profile_version": 1, "scenario_id": scenario["scenario_id"], "signal_session": _session_wire(signal), "target_weights": decision.target_weights, "threshold_value": opening_nav * 0.01}
    canonical_payload = _float_wire(payload)
    return {"schema": "qsl.research.tqqq_dual_drive_core_session_result.v1", "payload": canonical_payload, "payload_sha256": hashlib.sha256(_canonical_json(canonical_payload, terminal_lf=False)).hexdigest()}, next_state


def run_tqqq_core_replay(manifest_bytes: bytes, artifact_bytes: bytes, *, implementation_revision: str) -> tuple[dict[str, object], bytes]:
    """Run the four frozen costs scenarios without persistence or external effects."""
    if type(implementation_revision) is not str or not re.fullmatch(r"[0-9a-f]{40}", implementation_revision):
        _fail("INPUT_WIRE_INVALID")
    envelope = parse_tqqq_core_input(manifest_bytes, artifact_bytes)
    scenarios: list[dict[str, object]] = []
    for scenario in ({"scenario_id": "ZERO", "commission_bps": 0, "slippage_bps": 0}, {"scenario_id": "C1_2", "commission_bps": 1, "slippage_bps": 2}, {"scenario_id": "C2_5", "commission_bps": 2, "slippage_bps": 5}, {"scenario_id": "C5_10_STRESS", "commission_bps": 5, "slippage_bps": 10}):
        state = {"cash": 100000.0, "TQQQ": 0.0, "QQQM": 0.0}
        records: list[dict[str, object]] = []
        for index in range(256, len(envelope.rows) - 1):
            record, state = _record(envelope, scenario, index - 256, envelope.rows[index], envelope.rows[index + 1], state)
            records.append(record)
        scenario_payload = {"scenario_id": scenario["scenario_id"], "commission_bps": scenario["commission_bps"], "slippage_bps": scenario["slippage_bps"], "session_count": len(records), "session_result_sha256s": [record["payload_sha256"] for record in records], "session_results": records, "final_snapshot": records[-1]["payload"]["closing_snapshot"]}
        scenario_result = {**scenario_payload, "scenario_sha256": hashlib.sha256(_canonical_json(scenario_payload, terminal_lf=False)).hexdigest()}
        scenarios.append(scenario_result)
    replay_payload = {"schema": "qsl.research.tqqq_dual_drive_core_replay_result.v1", "anchor_commit": _ANCHOR_COMMIT, "complete_live_historical_parity": False, "implementation_revision": implementation_revision, "input_sha256": envelope.input_sha256, "non_equivalence_disclaimer": "Research-only deterministic replay of the QQQ-derived TQQQ/QQQM core. It excludes income, options, market-regime, macro, TACO, crisis, QPK risk-gate, PerformanceMonitor, broker/account and live controls and is not equivalent to the complete live strategy.", "optimization_seam": "NONE", "profile_id": _PROFILE_ID, "profile_sha256": "a7d6330ddcca9a27616e120e16e2352b77287d24db2017d33affdcd3dabe24fc", "profile_version": 1, "research_only": True, "scenario_order": ["ZERO", "C1_2", "C2_5", "C5_10_STRESS"], "scenarios": scenarios, "uv_lock_blob": "5b68a5fd450968f4e32f90e8e725e68039c59639"}
    replay = {**replay_payload, "replay_sha256": hashlib.sha256(_canonical_json(replay_payload, terminal_lf=False)).hexdigest()}
    return replay, _canonical_json(replay)


def read_tqqq_core_replay(replay_bytes: bytes) -> tuple[dict[str, object], bytes]:
    """Validate canonical replay bytes and produce the bounded canonical readback."""
    if type(replay_bytes) is not bytes:
        _fail("INPUT_WIRE_INVALID")
    try:
        replay = json.loads(replay_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("INPUT_WIRE_INVALID")
    replay_keys = frozenset({"anchor_commit", "complete_live_historical_parity", "implementation_revision", "input_sha256", "non_equivalence_disclaimer", "optimization_seam", "profile_id", "profile_sha256", "profile_version", "replay_sha256", "research_only", "scenario_order", "scenarios", "schema", "uv_lock_blob"})
    replay = _mapping(replay, replay_keys, "INPUT_WIRE_INVALID")
    if _canonical_json(replay) != replay_bytes:
        _fail("INPUT_WIRE_INVALID")
    replay_payload = {key: value for key, value in replay.items() if key != "replay_sha256"}
    if _sha(replay["replay_sha256"], "INPUT_WIRE_INVALID") != hashlib.sha256(_canonical_json(replay_payload, terminal_lf=False)).hexdigest():
        _fail("INPUT_WIRE_INVALID")
    if (replay["schema"], replay["anchor_commit"], replay["profile_id"], replay["profile_version"], replay["profile_sha256"], replay["research_only"], replay["complete_live_historical_parity"], replay["optimization_seam"], replay["uv_lock_blob"]) != ("qsl.research.tqqq_dual_drive_core_replay_result.v1", _ANCHOR_COMMIT, _PROFILE_ID, 1, "a7d6330ddcca9a27616e120e16e2352b77287d24db2017d33affdcd3dabe24fc", True, False, "NONE", "5b68a5fd450968f4e32f90e8e725e68039c59639"):
        _fail("INPUT_WIRE_INVALID")
    scenario_order = ["ZERO", "C1_2", "C2_5", "C5_10_STRESS"]
    if replay["scenario_order"] != scenario_order or type(replay["scenarios"]) is not list or len(replay["scenarios"]) != 4:
        _fail("INPUT_WIRE_INVALID")
    scenario_sha256s: list[dict[str, str]] = []
    session_count: int | None = None
    first_execution = last_execution = None
    for expected_id, scenario in zip(scenario_order, replay["scenarios"], strict=True):
        scenario = _mapping(scenario, frozenset({"commission_bps", "final_snapshot", "scenario_id", "scenario_sha256", "session_count", "session_result_sha256s", "session_results", "slippage_bps"}), "INPUT_WIRE_INVALID")
        scenario_payload = {key: value for key, value in scenario.items() if key != "scenario_sha256"}
        if scenario["scenario_id"] != expected_id or _sha(scenario["scenario_sha256"], "INPUT_WIRE_INVALID") != hashlib.sha256(_canonical_json(scenario_payload, terminal_lf=False)).hexdigest() or type(scenario["session_results"]) is not list or scenario["session_count"] != len(scenario["session_results"]):
            _fail("INPUT_WIRE_INVALID")
        if session_count is None:
            session_count = scenario["session_count"]
        elif session_count != scenario["session_count"]:
            _fail("INPUT_WIRE_INVALID")
        digests = []
        for record in scenario["session_results"]:
            record = _mapping(record, frozenset({"payload", "payload_sha256", "schema"}), "INPUT_WIRE_INVALID")
            if record["schema"] != "qsl.research.tqqq_dual_drive_core_session_result.v1" or _sha(record["payload_sha256"], "INPUT_WIRE_INVALID") != hashlib.sha256(_canonical_json(record["payload"], terminal_lf=False)).hexdigest():
                _fail("INPUT_WIRE_INVALID")
            digests.append(record["payload_sha256"])
        if scenario["session_result_sha256s"] != digests:
            _fail("INPUT_WIRE_INVALID")
        if first_execution is None and scenario["session_results"]:
            first_execution = scenario["session_results"][0]["payload"]["execution_session"]["trading_date"]
        if scenario["session_results"]:
            last_execution = scenario["session_results"][-1]["payload"]["execution_session"]["trading_date"]
        scenario_sha256s.append({"scenario_id": expected_id, "scenario_sha256": scenario["scenario_sha256"]})
    readback_payload = {"schema": "qsl.research.tqqq_dual_drive_core_replay_readback.v1", "first_execution_session": first_execution, "last_execution_session": last_execution, "input_sha256": replay["input_sha256"], "non_equivalence_disclaimer": replay["non_equivalence_disclaimer"], "profile_sha256": replay["profile_sha256"], "replay_sha256": replay["replay_sha256"], "scenario_sha256s": scenario_sha256s, "session_count": session_count}
    readback = {**readback_payload, "readback_sha256": hashlib.sha256(_canonical_json(readback_payload, terminal_lf=False)).hexdigest()}
    return readback, _canonical_json(readback)
