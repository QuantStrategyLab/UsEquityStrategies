"""Materialize Batch A frozen member pack v2 from validated price snapshots.

Reuses the existing typed SMA200 strategy baselines and binds an explicit
cost-model digest.  Member returns are full strategy daily returns — never
raw symbol buy-and-hold returns.  Cash is ``ASSUMED_ZERO_USD_CASH`` only.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from us_equity_strategies.research.batch_a_dataset import PriceSnapshotV2
from us_equity_strategies.research.soxl_soxx_offline_input_contract import (
    InputRow as SoxlInputRow,
)
from us_equity_strategies.research.soxl_soxx_offline_input_contract import (
    OfflineInput as SoxlOfflineInput,
)
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import (
    PROFILE as SOXL_PROFILE,
)
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import (
    TRANSACTION_COST_RATE as SOXL_COST_RATE,
)
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import (
    VERSION as SOXL_BASELINE_VERSION,
)
from us_equity_strategies.research.soxl_soxx_typed_baseline_result import (
    run_typed_baseline as run_soxl_typed_baseline,
)
from us_equity_strategies.research.tqqq_offline_input_contract import (
    InputRow as TqqqInputRow,
)
from us_equity_strategies.research.tqqq_offline_input_contract import (
    OfflineInput as TqqqOfflineInput,
)
from us_equity_strategies.research.tqqq_typed_baseline_result import (
    PROFILE as TQQQ_PROFILE,
)
from us_equity_strategies.research.tqqq_typed_baseline_result import (
    TRANSACTION_COST_RATE as TQQQ_COST_RATE,
)
from us_equity_strategies.research.tqqq_typed_baseline_result import (
    VERSION as TQQQ_BASELINE_VERSION,
)
from us_equity_strategies.research.tqqq_typed_baseline_result import (
    run_typed_baseline as run_tqqq_typed_baseline,
)

SCHEMA_VERSION = "qsl.c3-batch-a-frozen-member-pack.v2"
CASH_RETURN_POLICY = "ASSUMED_ZERO_USD_CASH"
REQUIRED_MEMBER_IDS = ("cash_sleeve", "soxl_core", "tqqq_core")
QUOTE_CURRENCY = "USD"


class BatchAMemberPackError(ValueError):
    """Sanitized member-pack materialization / validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise BatchAMemberPackError(code)


def _digest_payload(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def frozen_cost_model_digest() -> str:
    """Stable digest for the reused typed-baseline zero-cost model."""

    return _digest_payload(
        {
            "soxl_profile": SOXL_PROFILE,
            "soxl_baseline_version": SOXL_BASELINE_VERSION,
            "soxl_transaction_cost_rate": SOXL_COST_RATE,
            "tqqq_profile": TQQQ_PROFILE,
            "tqqq_baseline_version": TQQQ_BASELINE_VERSION,
            "tqqq_transaction_cost_rate": TQQQ_COST_RATE,
            "cost_scenario": "TYPED_BASELINE_ZERO",
        }
    )


def _rows_for(snapshot: PriceSnapshotV2, symbols: Sequence[str]) -> tuple[PriceRow, ...]:
    present = {row.symbol for row in snapshot.rows}
    if present != set(symbols):
        _fail("DATASET_SYMBOLS_INVALID")
    return snapshot.rows


def _soxl_offline(snapshot: PriceSnapshotV2) -> SoxlOfflineInput:
    rows = _rows_for(snapshot, ("SOXX", "SOXL"))
    typed = tuple(
        SoxlInputRow(
            symbol=row.symbol,
            as_of=row.as_of,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in rows
    )
    return SoxlOfflineInput(
        rows=typed,
        canonical_bytes=snapshot.artifact_bytes,
        input_digest=snapshot.input_digest,
        source_revision=str(snapshot.manifest["source_revision"]),
    )


def _tqqq_offline(snapshot: PriceSnapshotV2) -> TqqqOfflineInput:
    rows = _rows_for(snapshot, ("QQQ", "TQQQ"))
    typed = tuple(
        TqqqInputRow(
            symbol=row.symbol,
            as_of=row.as_of,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
        )
        for row in rows
    )
    return TqqqOfflineInput(
        rows=typed,
        canonical_bytes=snapshot.artifact_bytes,
        input_digest=snapshot.input_digest,
        source_revision=str(snapshot.manifest["source_revision"]),
    )


def _strategy_series(
    *,
    member_id: str,
    dates: Sequence[str],
    returns: Sequence[float],
    input_digest: str,
    as_of: str,
    capital_basis_digest: str,
    cost_model_digest: str,
    risk_policy_digest: str,
    data_scope_digest: str,
) -> dict[str, object]:
    if len(dates) != len(returns) or len(dates) < 2:
        _fail("STRATEGY_SERIES_INCOMPLETE")
    for item in returns:
        if not math.isfinite(item) or item <= -1.0:
            _fail("STRATEGY_RETURN_INVALID")
    payload = {
        "member_id": member_id,
        "dates": list(dates),
        "returns": list(returns),
        "input_digest": input_digest,
        "as_of": as_of,
        "quote_currency": QUOTE_CURRENCY,
        "capital_basis_digest": capital_basis_digest,
        "cost_model_digest": cost_model_digest,
        "risk_policy_digest": risk_policy_digest,
        "data_scope_digest": data_scope_digest,
    }
    payload["evidence_digest"] = _digest_payload(payload)
    return payload


def materialize_batch_a_member_pack(
    *,
    soxl_snapshot: PriceSnapshotV2,
    tqqq_snapshot: PriceSnapshotV2,
    as_of: str | None = None,
    capital_basis_digest: str | None = None,
    risk_policy_digest: str | None = None,
) -> dict[str, object]:
    """Build a v2 frozen pack from two validated snapshots."""

    soxl_result = run_soxl_typed_baseline(_soxl_offline(soxl_snapshot))
    tqqq_result = run_tqqq_typed_baseline(_tqqq_offline(tqqq_snapshot))
    soxl_points = soxl_result.daily_returns
    tqqq_points = tqqq_result.daily_returns
    soxl_dates = tuple(point.date for point in soxl_points)
    tqqq_dates = tuple(point.date for point in tqqq_points)
    if soxl_dates != tqqq_dates or len(soxl_dates) < 2:
        _fail("MEMBER_DATES_INCOMPLETE")
    dates = soxl_dates
    soxl_returns = tuple(float(point.daily_return) for point in soxl_points)
    tqqq_returns = tuple(float(point.daily_return) for point in tqqq_points)
    # Strategy evaluation length is shorter than raw bar count (SMA warmup).
    raw_sessions = len({row.as_of for row in soxl_snapshot.rows})
    if len(dates) >= raw_sessions:
        _fail("STRATEGY_RETURNS_MUST_EXCLUDE_WARMUP")

    resolved_as_of = as_of or dates[-1]
    try:
        if date.fromisoformat(resolved_as_of).isoformat() != resolved_as_of:
            _fail("AS_OF_INVALID")
    except ValueError as exc:
        raise BatchAMemberPackError("AS_OF_INVALID") from exc

    cost_digest = frozen_cost_model_digest()
    capital = capital_basis_digest or _digest_payload(
        {"basis": "BATCH_A_TYPED_BASELINE_INITIAL_EQUITY", "amount": 100_000.0}
    )
    risk = risk_policy_digest or _digest_payload(
        {
            "cash_symbol": "USD_CASH",
            "policy": "BATCH_A_V2_ASSUMED_ZERO_USD_CASH",
        }
    )
    data_scope = _digest_payload(
        {
            "soxl_dataset_id": soxl_snapshot.dataset_id,
            "soxl_input_digest": soxl_snapshot.input_digest,
            "tqqq_dataset_id": tqqq_snapshot.dataset_id,
            "tqqq_input_digest": tqqq_snapshot.input_digest,
            "evaluation_dates": list(dates),
        }
    )
    cash_returns = tuple(0.0 for _ in dates)
    members = (
        _strategy_series(
            member_id="cash_sleeve",
            dates=dates,
            returns=cash_returns,
            input_digest=_digest_payload({"cash_return_policy": CASH_RETURN_POLICY}),
            as_of=resolved_as_of,
            capital_basis_digest=capital,
            cost_model_digest=cost_digest,
            risk_policy_digest=risk,
            data_scope_digest=data_scope,
        ),
        _strategy_series(
            member_id="soxl_core",
            dates=dates,
            returns=soxl_returns,
            input_digest=soxl_snapshot.input_digest,
            as_of=resolved_as_of,
            capital_basis_digest=capital,
            cost_model_digest=cost_digest,
            risk_policy_digest=risk,
            data_scope_digest=data_scope,
        ),
        _strategy_series(
            member_id="tqqq_core",
            dates=dates,
            returns=tqqq_returns,
            input_digest=tqqq_snapshot.input_digest,
            as_of=resolved_as_of,
            capital_basis_digest=capital,
            cost_model_digest=cost_digest,
            risk_policy_digest=risk,
            data_scope_digest=data_scope,
        ),
    )
    pack: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "research_only": True,
        "execution_authorized": False,
        "cash_return_policy": CASH_RETURN_POLICY,
        "strategy_bindings": {
            "soxl_core": {
                "profile": SOXL_PROFILE,
                "baseline_version": SOXL_BASELINE_VERSION,
                "cost_scenario": "TYPED_BASELINE_ZERO",
            },
            "tqqq_core": {
                "profile": TQQQ_PROFILE,
                "baseline_version": TQQQ_BASELINE_VERSION,
                "cost_scenario": "TYPED_BASELINE_ZERO",
            },
        },
        "source_note": "BATCH_A_V2_MATERIALIZED_FROM_PRICE_SNAPSHOT_V2",
        "members": list(members),
    }
    pack["pack_digest"] = _digest_payload({key: value for key, value in pack.items()})
    return validate_batch_a_member_pack(pack)


def validate_batch_a_member_pack(raw: Mapping[str, object]) -> dict[str, Any]:
    """Validate a v2 pack; reject v1 and digest / membership mismatches."""

    if raw.get("schema_version") != SCHEMA_VERSION:
        if raw.get("schema_version") == "qsl.c3-batch-a-frozen-member-pack.v1":
            _fail("FROZEN_MEMBER_PACK_V1_REJECTED")
        _fail("FROZEN_MEMBER_PACK_SCHEMA_INVALID")
    if raw.get("research_only") is not True:
        _fail("FROZEN_MEMBER_PACK_NOT_RESEARCH_ONLY")
    if raw.get("execution_authorized") is not False:
        _fail("FROZEN_MEMBER_PACK_EXECUTION_AUTHORIZED")
    if raw.get("cash_return_policy") != CASH_RETURN_POLICY:
        _fail("CASH_RETURN_POLICY_INVALID")
    members_raw = raw.get("members")
    if not isinstance(members_raw, Sequence) or isinstance(members_raw, (str, bytes)):
        _fail("FROZEN_MEMBERS_INVALID")
    by_id: dict[str, Mapping[str, object]] = {}
    for item in members_raw:
        if not isinstance(item, Mapping):
            _fail("FROZEN_MEMBERS_INVALID")
        member_id = item.get("member_id")
        if not isinstance(member_id, str):
            _fail("FROZEN_MEMBERS_INVALID")
        if member_id in by_id:
            _fail("DUPLICATE_MEMBER_ID")
        by_id[member_id] = item
    if tuple(sorted(by_id)) != tuple(REQUIRED_MEMBER_IDS):
        _fail("FROZEN_MEMBERS_INCOMPLETE")
    cash = by_id["cash_sleeve"]
    dates = cash.get("dates")
    returns = cash.get("returns")
    if not isinstance(dates, Sequence) or isinstance(dates, (str, bytes)) or len(dates) < 2:
        _fail("CASH_DATES_INVALID")
    if not isinstance(returns, Sequence) or isinstance(returns, (str, bytes)):
        _fail("CASH_RETURNS_INVALID")
    if len(returns) != len(dates):
        _fail("CASH_RETURNS_INVALID")
    for item in returns:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            _fail("ASSUMED_ZERO_CASH_RETURNS_NONZERO")
        if abs(float(item)) > 1e-15:
            _fail("ASSUMED_ZERO_CASH_RETURNS_NONZERO")
    for member_id in REQUIRED_MEMBER_IDS:
        member = by_id[member_id]
        member_dates = member.get("dates")
        member_returns = member.get("returns")
        if member_dates != dates:
            _fail("MEMBER_DATES_INCOMPLETE")
        if (
            not isinstance(member_returns, Sequence)
            or isinstance(member_returns, (str, bytes))
            or len(member_returns) != len(dates)
        ):
            _fail("MEMBER_RETURNS_INVALID")
        for field in (
            "evidence_digest",
            "input_digest",
            "as_of",
            "quote_currency",
            "capital_basis_digest",
            "cost_model_digest",
            "risk_policy_digest",
            "data_scope_digest",
        ):
            value = member.get(field)
            if not isinstance(value, str) or not value:
                _fail("MEMBER_COMPARABILITY_INCOMPLETE")
        if member.get("quote_currency") != QUOTE_CURRENCY:
            _fail("MEMBER_COMPARABILITY_INCOMPLETE")
    without_digest = {key: value for key, value in raw.items() if key != "pack_digest"}
    expected = _digest_payload(without_digest)
    actual = raw.get("pack_digest")
    if actual != expected:
        _fail("PACK_DIGEST_MISMATCH")
    return {
        "schema_version": SCHEMA_VERSION,
        "research_only": True,
        "execution_authorized": False,
        "cash_return_policy": CASH_RETURN_POLICY,
        "strategy_bindings": raw.get("strategy_bindings"),
        "source_note": raw.get("source_note"),
        "members": tuple(by_id[member_id] for member_id in REQUIRED_MEMBER_IDS),
        "pack_digest": expected,
    }


__all__ = [
    "CASH_RETURN_POLICY",
    "REQUIRED_MEMBER_IDS",
    "SCHEMA_VERSION",
    "BatchAMemberPackError",
    "frozen_cost_model_digest",
    "materialize_batch_a_member_pack",
    "validate_batch_a_member_pack",
]
