"""Pure, JSON-only source runner for the frozen SOXL core-only P2 v2 adapter.

This is a deliberately small boundary between a future P3 verifier and the
version-pinned strategy source.  It accepts an already materialized research
context, calls only the public core-only adapter, and emits a canonical target
summary.  It never fetches data, reads credentials, sizes orders, records a
decision, or writes files.

The caller remains responsible for validating the immutable P1 input and the
complete P2 configuration hash before supplying this context.  Keeping that
provenance in the pipeline avoids duplicating a second candidate registry in
the strategy package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import StrategyContext

from us_equity_strategies.entrypoints import (
    build_soxl_soxx_core_only_p2_v2_research_decision,
)


INPUT_SCHEMA = "qsl.soxl-core-only-p3-strategy-context.v1"
OUTPUT_SCHEMA = "qsl.soxl-core-only-p3-decision.v1"
ENTRYPOINT = (
    "us_equity_strategies.entrypoints."
    "build_soxl_soxx_core_only_p2_v2_research_decision"
)
_SYMBOLS = ("SOXL", "SOXX", "BOXX")
_INDICATOR_FIELDS = {
    "SOXL": frozenset({"price", "ma_trend"}),
    "SOXX": frozenset(
        {
            "price",
            "ma_trend",
            "ma20",
            "ma20_slope",
            "rsi14",
            "bb_upper",
            "realized_volatility_10",
            "realized_volatility_10_dynamic_threshold",
            "realized_volatility_10_dynamic_sample_count",
        }
    ),
}
_DIAGNOSTIC_FIELDS = (
    "blend_tier",
    "base_blend_tier",
    "active_risk_asset",
    "blend_gate_volatility_delever_triggered",
    "blend_gate_volatility_delever_redirect_symbol",
    "market_regime_control_enabled",
    "market_regime_control_applied",
)


class SoxlCoreOnlyP3SourceRunnerError(ValueError):
    """Fail-closed, non-sensitive input or decision error."""


def _fail() -> None:
    raise SoxlCoreOnlyP3SourceRunnerError("invalid SOXL core-only P3 source context")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _fail()
    return dict(value)


def _finite(value: object, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail()
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0) or (nonnegative and result < 0.0):
        _fail()
    return 0.0 if result == 0.0 else result


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail()
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail()
    if result.tzinfo is None or result.utcoffset() is None:
        _fail()
    return result


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _finite(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in _mapping(value).items()}
    _fail()


def _parse_positions(value: object) -> tuple[Position, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail()
    positions: list[Position] = []
    seen: set[str] = set()
    expected = {"symbol", "quantity", "market_value", "average_cost", "currency", "account_id"}
    for raw in value:
        row = _mapping(raw)
        if set(row) != expected:
            _fail()
        symbol = row["symbol"]
        if not isinstance(symbol, str) or symbol not in _SYMBOLS or symbol in seen:
            _fail()
        seen.add(symbol)
        average_cost = row["average_cost"]
        if average_cost is not None:
            average_cost = _finite(average_cost, positive=True)
        if row["currency"] != "USD" or row["account_id"] is not None:
            _fail()
        positions.append(
            Position(
                symbol=symbol,
                quantity=_finite(row["quantity"]),
                market_value=_finite(row["market_value"], nonnegative=True),
                average_cost=average_cost,
                currency="USD",
                account_id=None,
            )
        )
    return tuple(positions)


def _parse_metadata(value: object, positions: tuple[Position, ...]) -> dict[str, object]:
    metadata = _mapping(value)
    if set(metadata) - {"observed_effective_exposure", "sellable_quantities"}:
        _fail()
    normalized: dict[str, object] = {}
    if "observed_effective_exposure" in metadata:
        normalized["observed_effective_exposure"] = _finite(
            metadata["observed_effective_exposure"], nonnegative=True
        )
    if "sellable_quantities" in metadata:
        raw_quantities = _mapping(metadata["sellable_quantities"])
        position_symbols = {position.symbol for position in positions}
        if set(raw_quantities) != position_symbols:
            _fail()
        normalized["sellable_quantities"] = {
            symbol: _finite(quantity, nonnegative=True)
            for symbol, quantity in sorted(raw_quantities.items())
        }
    return normalized


def _parse_portfolio(value: object, as_of: datetime) -> PortfolioSnapshot:
    payload = _mapping(value)
    expected = {"as_of", "total_equity", "buying_power", "cash_balance", "positions", "metadata"}
    if set(payload) != expected or _timestamp(payload["as_of"]) != as_of:
        _fail()
    positions = _parse_positions(payload["positions"])
    buying_power = payload["buying_power"]
    cash_balance = payload["cash_balance"]
    if buying_power is not None:
        buying_power = _finite(buying_power, nonnegative=True)
    if cash_balance is not None:
        cash_balance = _finite(cash_balance, nonnegative=True)
    return PortfolioSnapshot(
        as_of=as_of,
        total_equity=_finite(payload["total_equity"], positive=True),
        buying_power=buying_power,
        cash_balance=cash_balance,
        positions=positions,
        metadata=_parse_metadata(payload["metadata"], positions),
    )


def _parse_market_data(value: object) -> dict[str, object]:
    payload = _mapping(value)
    if set(payload) != {"derived_indicators"}:
        _fail()
    indicators = _mapping(payload["derived_indicators"])
    if set(indicators) != {"SOXL", "SOXX"}:
        _fail()
    normalized: dict[str, object] = {}
    for symbol, required in _INDICATOR_FIELDS.items():
        row = _mapping(indicators[symbol])
        if set(row) != required:
            _fail()
        parsed = {field: _finite(row[field]) for field in sorted(required)}
        if parsed["price"] <= 0.0 or parsed["ma_trend"] <= 0.0:
            _fail()
        if symbol == "SOXX":
            if (
                parsed["ma20"] <= 0.0
                or parsed["bb_upper"] <= 0.0
                or not 0.0 <= parsed["rsi14"] <= 100.0
                or parsed["realized_volatility_10"] < 0.0
                or parsed["realized_volatility_10_dynamic_threshold"] <= 0.0
                or parsed["realized_volatility_10_dynamic_sample_count"] <= 0.0
            ):
                _fail()
        normalized[symbol] = parsed
    return {"derived_indicators": normalized}


def strategy_context_from_p3_source_input(value: object) -> StrategyContext:
    """Validate a JSON-only context for the frozen SOXL P2 v2 source call."""
    payload = _mapping(value)
    expected = {"schema_version", "as_of", "portfolio", "market_data", "runtime_config"}
    if set(payload) != expected or payload["schema_version"] != INPUT_SCHEMA:
        _fail()
    as_of = _timestamp(payload["as_of"])
    return StrategyContext(
        as_of=as_of,
        portfolio=_parse_portfolio(payload["portfolio"], as_of),
        market_data=_parse_market_data(payload["market_data"]),
        runtime_config=_mapping(_json_value(payload["runtime_config"])),
    )


def run_soxl_core_only_p3_source(value: object) -> dict[str, object]:
    """Run the public core-only adapter and return canonical, non-order evidence."""
    ctx = strategy_context_from_p3_source_input(value)
    try:
        decision = build_soxl_soxx_core_only_p2_v2_research_decision(ctx)
    except ValueError as exc:
        raise SoxlCoreOnlyP3SourceRunnerError(
            "SOXL core-only P3 source decision parked"
        ) from exc
    all_targets = {
        position.symbol: _finite(position.target_value, nonnegative=True)
        for position in decision.positions
        if position.target_value is not None
    }
    if (
        not set(_SYMBOLS).issubset(all_targets)
        or any(
            target != 0.0
            for symbol, target in all_targets.items()
            if symbol not in _SYMBOLS
        )
    ):
        _fail()
    targets = {symbol: all_targets[symbol] for symbol in _SYMBOLS}
    diagnostics = _mapping(decision.diagnostics)
    summary = {field: diagnostics.get(field) for field in _DIAGNOSTIC_FIELDS}
    if (
        summary["blend_tier"] not in {"full", "mid", "defensive"}
        or summary["base_blend_tier"] not in {"full", "mid", "defensive"}
        or summary["active_risk_asset"] not in {"SOXL", "SOXX", "SOXX+SOXL"}
        or not isinstance(summary["blend_gate_volatility_delever_triggered"], bool)
        or summary["blend_gate_volatility_delever_redirect_symbol"] not in {"SOXX", None}
        or summary["market_regime_control_enabled"] is not False
        or summary["market_regime_control_applied"] is not False
    ):
        _fail()
    result: dict[str, object] = {
        "schema_version": OUTPUT_SCHEMA,
        "entrypoint": ENTRYPOINT,
        "as_of": ctx.as_of.isoformat(),
        "target_values": {symbol: targets[symbol] for symbol in _SYMBOLS},
        "diagnostics": summary,
    }
    result["output_sha256"] = _sha256(result)
    return result


def _read_input(path: str) -> object:
    try:
        raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
        return json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise SoxlCoreOnlyP3SourceRunnerError("SOXL core-only P3 source input parked") from None


def _parked(failure_class: str) -> dict[str, str]:
    return {
        "schema_version": OUTPUT_SCHEMA,
        "status": "PARKED",
        "failure_class": failure_class,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="JSON research-context path, or - for stdin")
    args = parser.parse_args(argv)
    try:
        result = run_soxl_core_only_p3_source(_read_input(args.input))
    except SoxlCoreOnlyP3SourceRunnerError:
        result = _parked("strategy_context_invalid")
    except Exception:  # pragma: no cover - defensive boundary for future source changes
        result = _parked("strategy_internal_failure")
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
