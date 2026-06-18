from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import Any


MARKET_SIGNAL_BUNDLE_SCHEMA_VERSION = "market_signal_bundle.v1"
MARKET_SIGNAL_MANIFEST_SCHEMA_VERSION = "market_signal_manifest.v1"
MARKET_SIGNAL_INDEX_SCHEMA_VERSION = "market_signal_index.v1"
MARKET_SIGNAL_CONSUMER_CONTRACTS_SCHEMA_VERSION = "market_signal_consumer_contracts.v1"
CANONICAL_INPUT_DERIVED_INDICATORS = "derived_indicators"
FRESHNESS_FRESH = "fresh"

REQUIRED_INDICATOR_FIELDS_BY_CONSUMER: dict[str, dict[str, tuple[str, ...]]] = {
    "us_equity:ibit_smart_dca": {
        "BTC-USD": ("ahr999", "mayer_multiple"),
    },
    "research:ibit_btc_ahr999_mayer_precomputed": {
        "BTC-USD": ("ahr999", "mayer_multiple"),
    },
    "research:ibit_btc_ahr999_mayer_precomputed_variants": {
        "BTC-USD": ("ahr999", "ahr999_sma", "mayer_multiple"),
    },
}

_REQUIRED_PROVENANCE_FIELDS = frozenset(
    {
        "source_repo",
        "source_version",
        "code_commit",
        "provider",
        "provider_dataset",
        "raw_artifact_sha256",
        "transform",
        "license_scope",
        "generated_by",
    }
)
_FORBIDDEN_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "signed_url",
        "token",
    }
)


class SignalBundleContractError(ValueError):
    """Raised when a signal bundle cannot be safely injected as a canonical input."""


def validate_signal_bundle(
    bundle: Mapping[str, Any],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
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
    _validate_freshness(bundle, accepted_freshness_statuses=accepted_freshness_statuses)
    _validate_derived_indicators(bundle)
    _validate_provenance(bundle)


def extract_canonical_input(
    bundle: Mapping[str, Any],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return a StrategyContext.market_data-compatible canonical input dict."""

    validate_signal_bundle(
        bundle,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )
    canonical_input = _canonical_input(bundle)
    indicators = bundle[canonical_input]
    return {
        canonical_input: {
            str(symbol): dict(payload)
            for symbol, payload in indicators.items()
        }
    }


def required_indicator_fields_for_consumer(
    consumer: str,
) -> dict[str, tuple[str, ...]]:
    """Return required derived indicator fields for a known platform consumer."""

    normalized = str(consumer or "").strip()
    if normalized not in REQUIRED_INDICATOR_FIELDS_BY_CONSUMER:
        known = ", ".join(sorted(REQUIRED_INDICATOR_FIELDS_BY_CONSUMER))
        raise SignalBundleContractError(
            f"unknown signal bundle consumer: {consumer!r}; known: {known}"
        )
    return {
        symbol: tuple(fields)
        for symbol, fields in REQUIRED_INDICATOR_FIELDS_BY_CONSUMER[normalized].items()
    }


def validate_signal_consumer_contract_registry(
    registry: Mapping[str, Any],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    require_all_known_consumers: bool = False,
) -> None:
    """Validate an external consumer contract registry against this strategy package."""

    if not isinstance(registry, Mapping):
        raise SignalBundleContractError("consumer contract registry must be a mapping")
    _validate_no_sensitive_fields(registry, path="consumer_contract_registry")
    schema_version = registry.get("schema_version")
    if schema_version != MARKET_SIGNAL_CONSUMER_CONTRACTS_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported consumer contract registry schema_version: "
            f"{schema_version!r}"
        )
    canonical_input = str(registry.get("canonical_input", "")).strip()
    if canonical_input != expected_canonical_input:
        raise SignalBundleContractError(
            "consumer contract registry canonical_input mismatch: "
            f"expected {expected_canonical_input!r}, got {canonical_input!r}"
        )
    contracts = registry.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise SignalBundleContractError(
            "consumer contract registry contracts must be a non-empty list"
        )

    seen_consumers: set[str] = set()
    for contract in contracts:
        _validate_signal_consumer_contract_record(
            contract,
            expected_canonical_input=expected_canonical_input,
            seen_consumers=seen_consumers,
        )
    if require_all_known_consumers:
        missing = sorted(set(REQUIRED_INDICATOR_FIELDS_BY_CONSUMER) - seen_consumers)
        if missing:
            raise SignalBundleContractError(
                "consumer contract registry missing known consumers: "
                + ", ".join(missing)
            )


def load_signal_consumer_contract_registry(
    path: str | PathLike[str],
    *,
    require_all_known_consumers: bool = False,
) -> dict[str, Any]:
    """Load and validate an external consumer contract registry JSON artifact."""

    with open(path, encoding="utf-8") as file_obj:
        registry = json.load(file_obj)
    if not isinstance(registry, Mapping):
        raise SignalBundleContractError("consumer contract registry JSON root must be a mapping")
    registry_dict = dict(registry)
    validate_signal_consumer_contract_registry(
        registry_dict,
        require_all_known_consumers=require_all_known_consumers,
    )
    return registry_dict


def signal_consumer_contract_registry_audit_summary(
    registry: Mapping[str, Any],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    require_all_known_consumers: bool = False,
) -> dict[str, Any]:
    """Return non-sensitive audit metadata for an external consumer contract registry."""

    validate_signal_consumer_contract_registry(
        registry,
        expected_canonical_input=expected_canonical_input,
        require_all_known_consumers=require_all_known_consumers,
    )
    contracts = registry["contracts"]
    consumers = tuple(str(contract["consumer"]) for contract in contracts)
    missing_known_consumers = tuple(
        sorted(set(REQUIRED_INDICATOR_FIELDS_BY_CONSUMER) - set(consumers))
    )
    return {
        "schema_version": str(registry.get("schema_version", "")),
        "canonical_input": str(registry.get("canonical_input", "")),
        "consumer_count": len(contracts),
        "consumers": consumers,
        "known_consumer_count": len(REQUIRED_INDICATOR_FIELDS_BY_CONSUMER),
        "missing_known_consumers": missing_known_consumers,
        "all_known_consumers_present": not missing_known_consumers,
    }


def signal_consumer_contract_registry_audit_summary_from_file(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    require_all_known_consumers: bool = False,
) -> dict[str, Any]:
    """Load a consumer contract registry artifact and return audit metadata."""

    registry_path = Path(path)
    registry = load_signal_consumer_contract_registry(
        registry_path,
        require_all_known_consumers=require_all_known_consumers,
    )
    summary = signal_consumer_contract_registry_audit_summary(
        registry,
        expected_canonical_input=expected_canonical_input,
        require_all_known_consumers=require_all_known_consumers,
    )
    summary.update(
        {
            "path": str(registry_path.resolve()),
            "sha256": _sha256_file(registry_path),
            "size_bytes": registry_path.stat().st_size,
        }
    )
    return summary


def validate_signal_bundle_indicator_fields(
    bundle: Mapping[str, Any],
    *,
    required_fields_by_symbol: Mapping[str, Iterable[str]],
) -> None:
    """Validate that a bundle covers required derived indicator fields."""

    validate_signal_bundle(bundle)
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


def validate_signal_bundle_for_consumer(
    bundle: Mapping[str, Any],
    *,
    consumer: str,
) -> None:
    """Validate a bundle against a known strategy or research consumer contract."""

    validate_signal_bundle_indicator_fields(
        bundle,
        required_fields_by_symbol=required_indicator_fields_for_consumer(consumer),
    )


def load_signal_bundle(path: str | PathLike[str]) -> dict[str, Any]:
    """Load a local JSON signal bundle for consumer-side validation."""

    with open(path, encoding="utf-8") as file_obj:
        bundle = json.load(file_obj)
    if not isinstance(bundle, Mapping):
        raise SignalBundleContractError("signal bundle JSON root must be a mapping")
    return dict(bundle)


def extract_canonical_input_from_file(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a local bundle and return StrategyContext.market_data input."""

    return extract_canonical_input(
        load_signal_bundle(path),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )


def load_signal_bundle_manifest(path: str | PathLike[str]) -> dict[str, Any]:
    """Load and validate a local signal bundle manifest."""

    with open(path, encoding="utf-8") as file_obj:
        manifest = json.load(file_obj)
    if not isinstance(manifest, Mapping):
        raise SignalBundleContractError("signal bundle manifest JSON root must be a mapping")
    manifest_dict = dict(manifest)
    _validate_no_sensitive_fields(manifest_dict, path="manifest")
    if manifest_dict.get("schema_version") != MARKET_SIGNAL_MANIFEST_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported signal bundle manifest schema_version: "
            f"{manifest_dict.get('schema_version')!r}"
        )
    for field in ("bundle_path", "bundle_sha256", "bundle_id", "as_of", "canonical_input"):
        if not _has_non_empty_value(manifest_dict, field):
            raise SignalBundleContractError(f"signal bundle manifest missing field: {field}")
    return manifest_dict


def load_signal_bundle_index(path: str | PathLike[str]) -> dict[str, Any]:
    """Load and validate a local index of published signal bundle manifests."""

    with open(path, encoding="utf-8") as file_obj:
        index = json.load(file_obj)
    if not isinstance(index, Mapping):
        raise SignalBundleContractError("signal bundle index JSON root must be a mapping")
    index_dict = dict(index)
    _validate_no_sensitive_fields(index_dict, path="index")
    if index_dict.get("schema_version") != MARKET_SIGNAL_INDEX_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported signal bundle index schema_version: "
            f"{index_dict.get('schema_version')!r}"
        )
    bundles = index_dict.get("bundles")
    if not _is_non_string_sequence(bundles) or not bundles:
        raise SignalBundleContractError("signal bundle index bundles must be a non-empty sequence")
    for raw_entry in bundles:
        if not isinstance(raw_entry, Mapping):
            raise SignalBundleContractError("signal bundle index entries must be mappings")
        entry = dict(raw_entry)
        for field in (
            "manifest_path",
            "manifest_sha256",
            "bundle_id",
            "as_of",
            "canonical_input",
            "freshness_status",
        ):
            if not _has_non_empty_value(entry, field):
                raise SignalBundleContractError(f"signal bundle index entry missing field: {field}")
    return index_dict


def load_signal_bundle_from_manifest(path: str | PathLike[str]) -> dict[str, Any]:
    """Load a local bundle through a manifest and verify file integrity."""

    manifest_path = Path(path)
    manifest = load_signal_bundle_manifest(manifest_path)
    manifest_dir = manifest_path.parent.resolve()
    resolved_bundle_path = _resolve_relative_artifact_path(
        manifest_dir,
        manifest["bundle_path"],
        owner="signal bundle manifest",
        field="bundle_path",
    )

    expected_sha256 = str(manifest["bundle_sha256"]).strip().lower()
    actual_sha256 = _sha256_file(resolved_bundle_path)
    if actual_sha256 != expected_sha256:
        raise SignalBundleContractError(
            "signal bundle sha256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    bundle = load_signal_bundle(resolved_bundle_path)
    _validate_manifest_bundle_consistency(manifest, bundle)
    return bundle


def resolve_signal_bundle_manifest_from_index(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> Path:
    """Resolve the latest matching manifest path from a local bundle index."""

    index_path = Path(path)
    index = load_signal_bundle_index(index_path)
    accepted = {str(item).strip().lower() for item in accepted_freshness_statuses}
    target_as_of = str(as_of).strip() if as_of is not None else None
    candidates: list[Mapping[str, Any]] = []

    for raw_entry in index["bundles"]:
        entry = dict(raw_entry)
        entry_canonical_input = str(entry.get("canonical_input", "")).strip()
        entry_freshness = str(entry.get("freshness_status", "")).strip().lower()
        entry_bundle_id = str(entry.get("bundle_id", "")).strip()
        entry_as_of = str(entry.get("as_of", "")).strip()
        if entry_canonical_input != expected_canonical_input:
            continue
        if entry_freshness not in accepted:
            continue
        if bundle_id is not None and entry_bundle_id != str(bundle_id).strip():
            continue
        if target_as_of is not None and entry_as_of > target_as_of:
            continue
        candidates.append(entry)

    if not candidates:
        raise SignalBundleContractError("signal bundle index has no matching manifest entry")

    selected = max(candidates, key=lambda entry: str(entry.get("as_of", "")))
    resolved_manifest_path = _resolve_relative_artifact_path(
        index_path.parent.resolve(),
        selected["manifest_path"],
        owner="signal bundle index",
        field="manifest_path",
    )
    expected_manifest_sha256 = str(selected["manifest_sha256"]).strip().lower()
    actual_manifest_sha256 = _sha256_file(resolved_manifest_path)
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise SignalBundleContractError(
            "signal bundle index manifest_sha256 mismatch: "
            f"expected {expected_manifest_sha256}, got {actual_manifest_sha256}"
        )
    manifest = load_signal_bundle_manifest(resolved_manifest_path)
    _validate_index_manifest_consistency(selected, manifest)
    return resolved_manifest_path


def load_signal_bundle_from_index(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, Any]:
    """Load a bundle through an index-selected manifest."""

    manifest_path = resolve_signal_bundle_manifest_from_index(
        path,
        expected_canonical_input=expected_canonical_input,
        as_of=as_of,
        bundle_id=bundle_id,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )
    return load_signal_bundle_from_manifest(manifest_path)


def extract_canonical_input_from_manifest(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a manifest-referenced bundle and return StrategyContext.market_data input."""

    return extract_canonical_input(
        load_signal_bundle_from_manifest(path),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )


def extract_canonical_input_from_index(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load an index-selected bundle and return StrategyContext.market_data input."""

    return extract_canonical_input(
        load_signal_bundle_from_index(
            path,
            expected_canonical_input=expected_canonical_input,
            as_of=as_of,
            bundle_id=bundle_id,
            accepted_freshness_statuses=accepted_freshness_statuses,
        ),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )


def signal_bundle_audit_summary(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return non-sensitive audit fields for platform logs."""

    validate_signal_bundle(bundle)
    indicator_fields_by_symbol = _indicator_fields_by_symbol(bundle)
    freshness = bundle.get("freshness")
    provenance = bundle.get("provenance")
    if not isinstance(freshness, Mapping) or not isinstance(provenance, Mapping):
        raise SignalBundleContractError("signal bundle audit fields are incomplete")
    return {
        "bundle_id": str(bundle.get("bundle_id", "")),
        "schema_version": str(bundle.get("schema_version", "")),
        "bundle_type": str(bundle.get("bundle_type", "")),
        "canonical_input": _canonical_input(bundle),
        "as_of": str(bundle.get("as_of", "")),
        "generated_at": str(bundle.get("generated_at", "")),
        "symbols": tuple(str(symbol) for symbol in bundle.get("symbols", ()) or ()),
        "indicator_fields_by_symbol": indicator_fields_by_symbol,
        "indicator_field_count_by_symbol": {
            symbol: len(fields)
            for symbol, fields in indicator_fields_by_symbol.items()
        },
        "freshness_status": str(freshness.get("status", "")),
        "freshness_policy": str(freshness.get("policy", "")),
        "provider_timestamp": str(freshness.get("provider_timestamp", "")),
        "source_repo": str(provenance.get("source_repo", "")),
        "source_version": str(provenance.get("source_version", "")),
        "code_commit": str(provenance.get("code_commit", "")),
        "provider": str(provenance.get("provider", "")),
        "provider_dataset": str(provenance.get("provider_dataset", "")),
        "transform": str(provenance.get("transform", "")),
    }


def signal_bundle_consumer_audit_summary(
    bundle: Mapping[str, Any],
    *,
    consumer: str,
) -> dict[str, Any]:
    """Return audit summary after validating consumer-specific field coverage."""

    validate_signal_bundle_for_consumer(bundle, consumer=consumer)
    summary = signal_bundle_audit_summary(bundle)
    summary.update(
        {
            "consumer": str(consumer),
            "required_indicator_fields_by_symbol": required_indicator_fields_for_consumer(
                consumer
            ),
        }
    )
    return summary


def signal_bundle_audit_summary_from_manifest(path: str | PathLike[str]) -> dict[str, Any]:
    """Load a manifest-referenced bundle and return non-sensitive audit fields."""

    bundle = load_signal_bundle_from_manifest(path)
    summary = signal_bundle_audit_summary(bundle)
    manifest = load_signal_bundle_manifest(path)
    summary.update(
        {
            "manifest_schema_version": str(manifest.get("schema_version", "")),
            "bundle_sha256": str(manifest.get("bundle_sha256", "")),
        }
    )
    return summary


def signal_bundle_consumer_audit_summary_from_manifest(
    path: str | PathLike[str],
    *,
    consumer: str,
) -> dict[str, Any]:
    """Load a manifest-referenced bundle and validate consumer field coverage."""

    bundle = load_signal_bundle_from_manifest(path)
    summary = signal_bundle_consumer_audit_summary(bundle, consumer=consumer)
    manifest = load_signal_bundle_manifest(path)
    summary.update(
        {
            "manifest_schema_version": str(manifest.get("schema_version", "")),
            "bundle_sha256": str(manifest.get("bundle_sha256", "")),
        }
    )
    return summary


def signal_bundle_audit_summary_from_index(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, Any]:
    """Resolve an index entry and return non-sensitive audit fields."""

    manifest_path = resolve_signal_bundle_manifest_from_index(
        path,
        expected_canonical_input=expected_canonical_input,
        as_of=as_of,
        bundle_id=bundle_id,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )
    summary = signal_bundle_audit_summary_from_manifest(manifest_path)
    index = load_signal_bundle_index(path)
    summary.update(
        {
            "index_schema_version": str(index.get("schema_version", "")),
            "index_bundle_count": len(index.get("bundles", ()) or ()),
            "manifest_path": str(manifest_path),
        }
    )
    return summary


def signal_bundle_consumer_audit_summary_from_index(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, Any]:
    """Resolve an index entry and validate consumer field coverage."""

    manifest_path = resolve_signal_bundle_manifest_from_index(
        path,
        expected_canonical_input=expected_canonical_input,
        as_of=as_of,
        bundle_id=bundle_id,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )
    summary = signal_bundle_consumer_audit_summary_from_manifest(
        manifest_path,
        consumer=consumer,
    )
    index = load_signal_bundle_index(path)
    summary.update(
        {
            "index_schema_version": str(index.get("schema_version", "")),
            "index_bundle_count": len(index.get("bundles", ()) or ()),
            "manifest_path": str(manifest_path),
        }
    )
    return summary


def _indicator_fields_by_symbol(bundle: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    canonical_input = _canonical_input(bundle)
    indicators = bundle.get(canonical_input)
    if not isinstance(indicators, Mapping):
        raise SignalBundleContractError("signal bundle indicators must be a mapping")

    fields_by_symbol: dict[str, tuple[str, ...]] = {}
    for symbol, payload in indicators.items():
        if not isinstance(payload, Mapping):
            raise SignalBundleContractError(
                f"signal bundle indicators[{symbol!r}] must be a mapping"
            )
        fields_by_symbol[str(symbol)] = tuple(sorted(str(field) for field in payload))
    return fields_by_symbol


def _validate_signal_consumer_contract_record(
    contract: object,
    *,
    expected_canonical_input: str,
    seen_consumers: set[str],
) -> None:
    if not isinstance(contract, Mapping):
        raise SignalBundleContractError("consumer contract registry entries must be mappings")
    consumer = str(contract.get("consumer", "")).strip()
    if not consumer:
        raise SignalBundleContractError("consumer contract registry entry missing consumer")
    if consumer in seen_consumers:
        raise SignalBundleContractError(f"duplicate consumer contract registry entry: {consumer}")
    seen_consumers.add(consumer)
    canonical_input = str(contract.get("canonical_input", "")).strip()
    if canonical_input != expected_canonical_input:
        raise SignalBundleContractError(
            f"consumer contract {consumer} canonical_input mismatch: {canonical_input!r}"
        )
    fields_by_symbol = contract.get("required_indicator_fields_by_symbol")
    if not isinstance(fields_by_symbol, Mapping) or not fields_by_symbol:
        raise SignalBundleContractError(
            f"consumer contract {consumer} missing required indicator fields"
        )
    expected = required_indicator_fields_for_consumer(consumer)
    normalized: dict[str, tuple[str, ...]] = {}
    for symbol, raw_fields in fields_by_symbol.items():
        normalized_symbol = str(symbol).strip()
        if not normalized_symbol:
            raise SignalBundleContractError(
                f"consumer contract {consumer} has empty indicator symbol"
            )
        if not _is_string_sequence(raw_fields):
            raise SignalBundleContractError(
                f"consumer contract {consumer} fields for {normalized_symbol} must be strings"
            )
        fields = tuple(str(field).strip() for field in raw_fields)
        if len(set(fields)) != len(fields):
            raise SignalBundleContractError(
                f"consumer contract {consumer} fields for {normalized_symbol} include duplicates"
            )
        normalized[normalized_symbol] = fields
    if normalized != expected:
        raise SignalBundleContractError(
            f"consumer contract {consumer} required fields drift from local contract"
        )


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper().removesuffix(".US")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_relative_artifact_path(
    root: Path,
    value: object,
    *,
    owner: str,
    field: str,
) -> Path:
    relative_path = Path(str(value))
    if relative_path.is_absolute():
        raise SignalBundleContractError(f"{owner} {field} must be relative")
    resolved = (root / relative_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SignalBundleContractError(f"{owner} {field} escapes artifact directory") from exc
    return resolved


def _validate_manifest_bundle_consistency(
    manifest: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> None:
    manifest_bundle_id = str(manifest.get("bundle_id", "")).strip()
    bundle_id = str(bundle.get("bundle_id", "")).strip()
    if manifest_bundle_id != bundle_id:
        raise SignalBundleContractError(
            f"signal bundle manifest bundle_id mismatch: {manifest_bundle_id!r} != {bundle_id!r}"
        )
    manifest_as_of = str(manifest.get("as_of", "")).strip()
    bundle_as_of = str(bundle.get("as_of", "")).strip()
    if manifest_as_of != bundle_as_of:
        raise SignalBundleContractError(
            f"signal bundle manifest as_of mismatch: {manifest_as_of!r} != {bundle_as_of!r}"
        )
    manifest_canonical_input = str(manifest.get("canonical_input", "")).strip()
    if manifest_canonical_input != _canonical_input(bundle):
        raise SignalBundleContractError(
            "signal bundle manifest canonical_input mismatch: "
            f"{manifest_canonical_input!r} != {_canonical_input(bundle)!r}"
        )
    manifest_bundle_schema = str(manifest.get("bundle_schema_version", "")).strip()
    if manifest_bundle_schema and manifest_bundle_schema != str(bundle.get("schema_version", "")).strip():
        raise SignalBundleContractError(
            "signal bundle manifest schema_version mismatch: "
            f"{manifest_bundle_schema!r} != {bundle.get('schema_version')!r}"
        )
    manifest_freshness = str(manifest.get("freshness_status", "")).strip()
    bundle_freshness = bundle.get("freshness")
    if isinstance(bundle_freshness, Mapping):
        bundle_freshness_status = str(bundle_freshness.get("status", "")).strip()
        if manifest_freshness and manifest_freshness != bundle_freshness_status:
            raise SignalBundleContractError(
                "signal bundle manifest freshness_status mismatch: "
                f"{manifest_freshness!r} != {bundle_freshness_status!r}"
            )


def _validate_index_manifest_consistency(
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    for field in ("bundle_id", "as_of", "canonical_input"):
        entry_value = str(entry.get(field, "")).strip()
        manifest_value = str(manifest.get(field, "")).strip()
        if entry_value != manifest_value:
            raise SignalBundleContractError(
                f"signal bundle index {field} mismatch: {entry_value!r} != {manifest_value!r}"
            )
    entry_freshness = str(entry.get("freshness_status", "")).strip()
    manifest_freshness = str(manifest.get("freshness_status", "")).strip()
    if manifest_freshness and entry_freshness != manifest_freshness:
        raise SignalBundleContractError(
            "signal bundle index freshness_status mismatch: "
            f"{entry_freshness!r} != {manifest_freshness!r}"
        )


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


def _validate_freshness(
    bundle: Mapping[str, Any],
    *,
    accepted_freshness_statuses: Iterable[str],
) -> None:
    freshness = bundle.get("freshness")
    if not isinstance(freshness, Mapping):
        raise SignalBundleContractError("freshness must be a mapping")
    status = freshness.get("status")
    if not isinstance(status, str) or not status.strip():
        raise SignalBundleContractError("freshness.status must be a non-empty string")
    accepted = {str(item).strip().lower() for item in accepted_freshness_statuses}
    normalized_status = status.strip().lower()
    if normalized_status not in accepted:
        raise SignalBundleContractError(
            f"unacceptable freshness.status: {status!r}"
        )
    provider_timestamp = freshness.get("provider_timestamp")
    if not isinstance(provider_timestamp, str) or not provider_timestamp.strip():
        raise SignalBundleContractError(
            "freshness.provider_timestamp must be a non-empty string"
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


def _validate_provenance(bundle: Mapping[str, Any]) -> None:
    provenance = bundle.get("provenance")
    if not isinstance(provenance, Mapping):
        raise SignalBundleContractError("provenance must be a mapping")

    missing = [
        field
        for field in sorted(_REQUIRED_PROVENANCE_FIELDS)
        if not _has_non_empty_value(provenance, field)
    ]
    if missing:
        raise SignalBundleContractError(
            "provenance missing required fields: " + ", ".join(missing)
        )


def _validate_no_sensitive_fields(value: Any, *, path: str = "bundle") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if any(fragment in key for fragment in _FORBIDDEN_SENSITIVE_KEY_FRAGMENTS):
                raise SignalBundleContractError(
                    f"sensitive field is not allowed in signal bundle: {path}.{raw_key}"
                )
            _validate_no_sensitive_fields(item, path=f"{path}.{raw_key}")
    elif _is_non_string_sequence(value):
        for index, item in enumerate(value):
            _validate_no_sensitive_fields(item, path=f"{path}[{index}]")


def _has_non_empty_value(mapping: Mapping[str, Any], field: str) -> bool:
    value = mapping.get(field)
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _is_non_string_sequence(value: Any) -> bool:
    return not isinstance(value, (str, bytes)) and isinstance(value, Sequence)


def _is_string_sequence(value: Any) -> bool:
    if not _is_non_string_sequence(value):
        return False
    return all(isinstance(item, str) and item.strip() for item in value)
