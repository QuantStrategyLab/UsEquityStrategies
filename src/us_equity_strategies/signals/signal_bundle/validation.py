from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
from typing import Any

from .constants import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    FRESHNESS_FRESH,
    MARKET_SIGNAL_BUNDLE_SCHEMA_VERSION,
)

from .errors import (
    SignalBundleContractError,
)

from .provenance import (
    _is_string_sequence,
    _normalize_symbol,
    _resolve_consumer_evaluation_clock,
    _validate_freshness,
    _validate_no_sensitive_fields,
    _validate_provenance,
)

from .consumer_contracts import (
    required_indicator_fields_for_consumer,
)


def validate_signal_bundle(
    bundle: Mapping[str, Any],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> None:
    """Validate the consumer-side contract for a market signal bundle.

    The MVP contract is intentionally narrow: only complete, fresh
    ``derived_indicators`` bundles are accepted for StrategyContext.market_data
    injection.
    """

    if not isinstance(bundle, Mapping):
        raise SignalBundleContractError("signal bundle must be a mapping")

    schema_version = bundle.get("schema_version")
    if schema_version != MARKET_SIGNAL_BUNDLE_SCHEMA_VERSION:
        raise SignalBundleContractError(
            f"unsupported signal bundle schema_version: {schema_version!r}"
        )

    bundle_type = bundle.get("bundle_type")
    if bundle_type != CANONICAL_INPUT_DERIVED_INDICATORS:
        raise SignalBundleContractError(
            f"unsupported signal bundle_type: {bundle_type!r}"
        )

    canonical_input = _canonical_input(bundle)
    _compatible_profiles(bundle)
    if canonical_input != expected_canonical_input:
        raise SignalBundleContractError(
            "signal bundle canonical_input mismatch: "
            f"expected {expected_canonical_input!r}, got {canonical_input!r}"
        )
    if canonical_input != CANONICAL_INPUT_DERIVED_INDICATORS:
        raise SignalBundleContractError(
            f"unsupported canonical_input for MVP: {canonical_input!r}"
        )

    _validate_no_sensitive_fields(bundle)
    _validate_freshness(
        bundle,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )
    _validate_derived_indicators(bundle)
    _validate_provenance(bundle)


def validate_signal_bundle_indicator_fields(
    bundle: Mapping[str, Any],
    *,
    required_fields_by_symbol: Mapping[str, Iterable[str]],
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> None:
    """Validate that a bundle covers required derived indicator fields."""

    validate_signal_bundle(
        bundle,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )
    indicators = bundle[CANONICAL_INPUT_DERIVED_INDICATORS]
    if not isinstance(indicators, Mapping):
        raise SignalBundleContractError("derived_indicators must be a mapping")
    normalized_indicators = {
        _normalize_symbol(symbol): payload
        for symbol, payload in indicators.items()
    }
    for symbol, raw_required_fields in required_fields_by_symbol.items():
        normalized_symbol = _normalize_symbol(symbol)
        payload = normalized_indicators.get(normalized_symbol)
        if not isinstance(payload, Mapping):
            raise SignalBundleContractError(
                f"derived_indicators missing required symbol: {symbol}"
            )
        available = {str(field).strip().lower() for field in payload}
        required_fields = tuple(
            str(field).strip()
            for field in raw_required_fields
            if str(field).strip()
        )
        missing = [
            field
            for field in required_fields
            if field.lower() not in available
        ]
        if missing:
            raise SignalBundleContractError(
                f"derived_indicators[{symbol!r}] missing required fields: "
                + ", ".join(missing)
            )
        for field in required_fields:
            if field.lower().startswith("realized_volatility_") and field.lower().split("_")[-1].isdigit():
                value = next(payload[key] for key in payload if str(key).strip().lower() == field.lower())
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise SignalBundleContractError(
                        f"derived_indicators[{symbol!r}][{field!r}] must be finite and non-negative"
                    )


def validate_signal_bundle_for_consumer(
    bundle: Mapping[str, Any],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> None:
    """Validate a bundle against a known strategy or research consumer contract."""

    now, reference_time = _resolve_consumer_evaluation_clock(
        now=now,
        reference_time=reference_time,
    )
    validate_signal_bundle_indicator_fields(
        bundle,
        required_fields_by_symbol=required_indicator_fields_for_consumer(consumer),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )
    _validate_consumer_profile_compatibility(bundle, consumer=consumer)


def _canonical_input(bundle: Mapping[str, Any]) -> str:
    consumer_contract = bundle.get("consumer_contract")
    if not isinstance(consumer_contract, Mapping):
        raise SignalBundleContractError("consumer_contract must be a mapping")
    canonical_input = consumer_contract.get("canonical_input")
    if not isinstance(canonical_input, str) or not canonical_input.strip():
        raise SignalBundleContractError(
            "consumer_contract.canonical_input must be a non-empty string"
        )
    return canonical_input.strip()


def _compatible_profiles(bundle: Mapping[str, Any]) -> tuple[str, ...]:
    consumer_contract = bundle.get("consumer_contract")
    if not isinstance(consumer_contract, Mapping):
        raise SignalBundleContractError("consumer_contract must be a mapping")
    profiles = consumer_contract.get("compatible_profiles")
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Sequence):
        raise SignalBundleContractError(
            "consumer_contract.compatible_profiles must be a non-empty sequence"
        )

    normalized: list[str] = []
    for profile in profiles:
        if not isinstance(profile, str) or not profile.strip():
            raise SignalBundleContractError(
                "consumer_contract.compatible_profiles items must be non-empty strings"
            )
        normalized.append(profile.strip())
    if not normalized:
        raise SignalBundleContractError(
            "consumer_contract.compatible_profiles must include at least one profile"
        )
    return tuple(normalized)


def _index_compatible_profiles(entry: Mapping[str, Any]) -> tuple[str, ...]:
    if "compatible_profiles" not in entry:
        return ()
    profiles = entry.get("compatible_profiles")
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Sequence):
        raise SignalBundleContractError(
            "signal bundle index compatible_profiles must be a non-empty sequence"
        )

    normalized: list[str] = []
    for profile in profiles:
        if not isinstance(profile, str) or not profile.strip():
            raise SignalBundleContractError(
                "signal bundle index compatible_profiles items must be non-empty strings"
            )
        normalized.append(profile.strip())
    if not normalized:
        raise SignalBundleContractError(
            "signal bundle index compatible_profiles must include at least one profile"
        )
    return tuple(normalized)


def _validate_consumer_profile_compatibility(
    bundle: Mapping[str, Any],
    *,
    consumer: str,
) -> None:
    normalized_consumer = str(consumer or "").strip()
    compatible_profiles = _compatible_profiles(bundle)
    if normalized_consumer not in compatible_profiles:
        raise SignalBundleContractError(
            "consumer_contract.compatible_profiles does not include consumer: "
            f"{normalized_consumer!r}"
        )


def _validate_derived_indicators(bundle: Mapping[str, Any]) -> None:
    indicators = bundle.get(CANONICAL_INPUT_DERIVED_INDICATORS)
    if not isinstance(indicators, Mapping) or not indicators:
        raise SignalBundleContractError("derived_indicators must be a non-empty mapping")

    symbols = bundle.get("symbols")
    if symbols is not None:
        if not _is_string_sequence(symbols):
            raise SignalBundleContractError("symbols must be a sequence of strings")
        missing_symbols = [symbol for symbol in symbols if symbol not in indicators]
        if missing_symbols:
            raise SignalBundleContractError(
                "derived_indicators missing symbols: "
                + ", ".join(str(symbol) for symbol in missing_symbols)
            )

    for symbol, payload in indicators.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise SignalBundleContractError("derived_indicators keys must be symbols")
        if not isinstance(payload, Mapping) or not payload:
            raise SignalBundleContractError(
                f"derived_indicators[{symbol!r}] must be a non-empty mapping"
            )
