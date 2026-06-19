from __future__ import annotations

from pathlib import Path
from typing import Any

from quant_platform_kit.common.market_signal_artifacts import (
    materialize_market_signal_artifact_tree,
)

from .signal_bundle_contract import (
    extract_canonical_input_from_consumption_audit_for_consumer,
    extract_canonical_input_from_platform_handoff_for_consumer,
    extract_canonical_input_from_platform_handoff_index_for_consumer,
)


IBIT_SMART_DCA_MARKET_SIGNAL_CONSUMER = "us_equity:ibit_smart_dca"
MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT = "consumption_audit"
MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF = "platform_handoff"
MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF_INDEX = "platform_handoff_index"
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize a signal artifact reference and return StrategyContext market inputs."""

    normalized_reference_type = normalize_market_signal_reference_type(reference_type)
    local_path, materialize_metadata = materialize_market_signal_artifact_tree(
        reference,
        cache_dir=Path(cache_dir),
        client_factory=client_factory,
    )
    if normalized_reference_type == MARKET_SIGNAL_REFERENCE_CONSUMPTION_AUDIT:
        market_inputs = extract_canonical_input_from_consumption_audit_for_consumer(
            local_path,
            consumer=consumer,
        )
    elif normalized_reference_type == MARKET_SIGNAL_REFERENCE_PLATFORM_HANDOFF:
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
        "reference_type": normalized_reference_type,
        "consumer": str(consumer),
    }


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
