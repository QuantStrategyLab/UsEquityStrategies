from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quant_platform_kit.common.market_signal_artifacts import (
    materialize_market_signal_artifact_tree,
)

from .signal_bundle_contract import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    SignalBundleContractError,
    extract_canonical_input_from_consumption_audit_for_consumer,
    extract_canonical_input_from_platform_handoff_for_consumer,
    extract_canonical_input_from_platform_handoff_index_for_consumer,
    required_indicator_fields_for_consumer,
)


IBIT_SMART_DCA_MARKET_SIGNAL_CONSUMER = "us_equity:ibit_smart_dca"
NASDAQ_SP500_SMART_DCA_MARKET_SIGNAL_CONSUMER = "us_equity:nasdaq_sp500_smart_dca"
MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT = "consumption_audit"
MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF = "platform_handoff"
MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF_INDEX = "platform_handoff_index"
MARKET_SIGNAL_FALLBACK_MODE_NONE = "none"
MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID = "last_valid"
DEFAULT_MARKET_SIGNAL_FALLBACK_MODE = MARKET_SIGNAL_FALLBACK_MODE_NONE
DEFAULT_MARKET_SIGNAL_FALLBACK_MAX_STALE_DAYS = 3
SUPPORTED_MARKET_SIGNAL_REFERENCE_TYPES = frozenset(
    {
        MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT,
        MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF,
        MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF_INDEX,
    }
)


def extract_consumer_market_signal_inputs_from_reference(
    reference: str,
    *,
    reference_type: str,
    consumer: str,
    cache_dir: str | Path,
    as_of: str | None = None,
    client_factory: Any = None,
    fallback_mode: str | None = DEFAULT_MARKET_SIGNAL_FALLBACK_MODE,
    fallback_max_stale_days: int | None = DEFAULT_MARKET_SIGNAL_FALLBACK_MAX_STALE_DAYS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize a signal artifact reference and return StrategyContext market inputs."""

    normalized_reference_type = normalize_market_signal_reference_type(reference_type)
    normalized_fallback_mode = normalize_market_signal_fallback_mode(fallback_mode)
    try:
        market_inputs, metadata = _extract_consumer_market_signal_inputs_from_reference(
            reference,
            reference_type=normalized_reference_type,
            consumer=consumer,
            cache_dir=Path(cache_dir),
            as_of=as_of,
            client_factory=client_factory,
        )
    except Exception as exc:
        if normalized_fallback_mode != MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID:
            raise
        fallback = _load_last_valid_market_signal_inputs(
            reference=reference,
            reference_type=normalized_reference_type,
            consumer=consumer,
            cache_dir=Path(cache_dir),
            failed_exc=exc,
            max_stale_days=fallback_max_stale_days,
        )
        if fallback is None:
            raise
        return fallback

    if normalized_fallback_mode == MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID:
        _write_last_valid_market_signal_inputs(
            reference=reference,
            reference_type=normalized_reference_type,
            consumer=consumer,
            cache_dir=Path(cache_dir),
            as_of=as_of,
            market_inputs=market_inputs,
            metadata=metadata,
        )
    return market_inputs, metadata


def _extract_consumer_market_signal_inputs_from_reference(
    reference: str,
    *,
    reference_type: str,
    consumer: str,
    cache_dir: Path,
    as_of: str | None,
    client_factory: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    local_path, materialize_metadata = materialize_market_signal_artifact_tree(
        reference,
        cache_dir=cache_dir,
        client_factory=client_factory,
    )
    if reference_type == MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT:
        market_inputs = extract_canonical_input_from_consumption_audit_for_consumer(
            local_path,
            consumer=consumer,
        )
    elif reference_type == MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF:
        market_inputs = extract_canonical_input_from_platform_handoff_for_consumer(
            local_path,
            consumer=consumer,
        )
    else:
        market_inputs = extract_canonical_input_from_platform_handoff_index_for_consumer(
            local_path,
            consumer=consumer,
            as_of=as_of,
        )
    return market_inputs, {
        **dict(materialize_metadata),
        "reference_type": reference_type,
        "consumer": str(consumer),
    }


def normalize_market_signal_fallback_mode(value: object) -> str:
    mode = str(value or DEFAULT_MARKET_SIGNAL_FALLBACK_MODE).strip().lower().replace("-", "_")
    aliases = {
        "": MARKET_SIGNAL_FALLBACK_MODE_NONE,
        "off": MARKET_SIGNAL_FALLBACK_MODE_NONE,
        "disabled": MARKET_SIGNAL_FALLBACK_MODE_NONE,
        "false": MARKET_SIGNAL_FALLBACK_MODE_NONE,
        "none": MARKET_SIGNAL_FALLBACK_MODE_NONE,
        "last": MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID,
        "last_valid": MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID,
        "last_valid_signal": MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID,
    }
    normalized = aliases.get(mode, mode)
    if normalized not in {
        MARKET_SIGNAL_FALLBACK_MODE_NONE,
        MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID,
    }:
        raise ValueError(
            f"unsupported market signal fallback mode {value!r}; "
            "supported: none, last_valid"
        )
    return normalized


def normalize_market_signal_reference_type(reference_type: str) -> str:
    value = str(reference_type or "").strip().lower().replace("-", "_")
    aliases = {
        "audit": MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT,
        "consumption": MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT,
        "consumption_audit": MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT,
        "handoff": MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF,
        "handoff_manifest": MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF,
        "platform_handoff": MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF,
        "platform_handoff_manifest": MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF,
        "handoff_index": MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF_INDEX,
        "index": MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF_INDEX,
        "platform_handoff_index": MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF_INDEX,
    }
    normalized = aliases.get(value, value)
    if normalized not in SUPPORTED_MARKET_SIGNAL_REFERENCE_TYPES:
        supported = ", ".join(sorted(SUPPORTED_MARKET_SIGNAL_REFERENCE_TYPES))
        raise ValueError(
            f"unsupported market signal reference_type {reference_type!r}; "
            f"supported: {supported}"
        )
    return normalized


def _write_last_valid_market_signal_inputs(
    *,
    reference: str,
    reference_type: str,
    consumer: str,
    cache_dir: Path,
    as_of: str | None,
    market_inputs: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> None:
    _validate_market_signal_inputs_for_consumer(market_inputs, consumer=consumer)
    path = _last_valid_market_signal_inputs_path(
        reference=reference,
        reference_type=reference_type,
        consumer=consumer,
        cache_dir=cache_dir,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "market_signal_last_valid_inputs.v1",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "reference": str(reference),
        "reference_type": str(reference_type),
        "consumer": str(consumer),
        "as_of": as_of,
        "market_inputs": dict(market_inputs),
        "metadata": dict(metadata),
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _load_last_valid_market_signal_inputs(
    *,
    reference: str,
    reference_type: str,
    consumer: str,
    cache_dir: Path,
    failed_exc: Exception,
    max_stale_days: int | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    path = _last_valid_market_signal_inputs_path(
        reference=reference,
        reference_type=reference_type,
        consumer=consumer,
        cache_dir=cache_dir,
    )
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, Mapping):
        return None
    stale_reason = _last_valid_market_signal_stale_reason(payload, max_stale_days=max_stale_days)
    if stale_reason:
        return None
    market_inputs = payload.get("market_inputs")
    if not isinstance(market_inputs, Mapping):
        return None
    _validate_market_signal_inputs_for_consumer(market_inputs, consumer=consumer)
    metadata = dict(payload.get("metadata") or {})
    metadata.update(
        {
            "consumer": str(consumer),
            "reference_type": str(reference_type),
            "artifact_fallback_used": True,
            "artifact_fallback_mode": MARKET_SIGNAL_FALLBACK_MODE_LAST_VALID,
            "artifact_fallback_reason": f"{type(failed_exc).__name__}:{failed_exc}",
            "artifact_fallback_saved_at": payload.get("saved_at"),
            "artifact_fallback_cache_path": str(path),
        }
    )
    return dict(market_inputs), metadata


def _last_valid_market_signal_inputs_path(
    *,
    reference: str,
    reference_type: str,
    consumer: str,
    cache_dir: Path,
) -> Path:
    payload = {
        "reference": str(reference),
        "reference_type": str(reference_type),
        "consumer": str(consumer),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return cache_dir / "last_valid_market_signal_inputs" / f"{digest}.json"


def _last_valid_market_signal_stale_reason(
    payload: Mapping[str, Any],
    *,
    max_stale_days: int | None,
) -> str | None:
    if max_stale_days is None:
        return None
    if int(max_stale_days) < 0:
        return "last_valid_max_stale_days_negative"
    try:
        saved_at = datetime.fromisoformat(str(payload.get("saved_at")))
    except Exception:
        return "last_valid_saved_at_invalid"
    if saved_at.tzinfo is None:
        saved_at = saved_at.replace(tzinfo=timezone.utc)
    if saved_at < datetime.now(timezone.utc) - timedelta(days=int(max_stale_days)):
        return "last_valid_stale"
    return None


def _validate_market_signal_inputs_for_consumer(
    market_inputs: Mapping[str, Any],
    *,
    consumer: str,
) -> None:
    derived_indicators = market_inputs.get(CANONICAL_INPUT_DERIVED_INDICATORS)
    if not isinstance(derived_indicators, Mapping) or not derived_indicators:
        raise SignalBundleContractError("last valid market signal missing derived_indicators")
    required = required_indicator_fields_for_consumer(consumer)
    for symbol, fields in required.items():
        payload = derived_indicators.get(symbol)
        if not isinstance(payload, Mapping):
            raise SignalBundleContractError(
                f"last valid market signal missing required symbol: {symbol}"
            )
        missing = tuple(field for field in fields if field not in payload)
        if missing:
            raise SignalBundleContractError(
                f"last valid market signal missing required fields for {symbol}: "
                + ", ".join(missing)
            )
