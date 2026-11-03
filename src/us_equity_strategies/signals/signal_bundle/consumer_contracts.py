from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import Any

from .constants import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    MARKET_SIGNAL_CONSUMER_CONTRACTS_SCHEMA_VERSION,
    MARKET_SIGNAL_CONSUMER_CONTRACT_MANIFEST_SCHEMA_VERSION,
    REQUIRED_INDICATOR_FIELDS_BY_CONSUMER,
)

from .errors import (
    SignalBundleContractError,
)

from .provenance import (
    _has_non_empty_value,
    _is_string_sequence,
    _resolve_relative_artifact_path,
    _sha256_file,
    _validate_no_sensitive_fields,
)


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


def known_signal_consumers() -> tuple[str, ...]:
    """Return known signal consumer identifiers in stable order."""

    return tuple(sorted(REQUIRED_INDICATOR_FIELDS_BY_CONSUMER))


def signal_consumer_contract_registry_payload(
    *,
    consumers: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return the local JSON-safe consumer registry contract payload."""

    selected_consumers = (
        tuple(consumers)
        if consumers is not None
        else known_signal_consumers()
    )
    return {
        "schema_version": MARKET_SIGNAL_CONSUMER_CONTRACTS_SCHEMA_VERSION,
        "canonical_input": CANONICAL_INPUT_DERIVED_INDICATORS,
        "contracts": [
            _signal_consumer_contract_record(consumer)
            for consumer in selected_consumers
        ],
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
    canonical_payload_sha256 = _canonical_registry_payload_sha256(registry)
    local_payload_sha256 = _local_registry_payload_sha256(consumers)
    return {
        "schema_version": str(registry.get("schema_version", "")),
        "canonical_input": str(registry.get("canonical_input", "")),
        "consumer_count": len(contracts),
        "consumers": consumers,
        "known_consumer_count": len(REQUIRED_INDICATOR_FIELDS_BY_CONSUMER),
        "missing_known_consumers": missing_known_consumers,
        "all_known_consumers_present": not missing_known_consumers,
        "canonical_registry_payload_sha256": canonical_payload_sha256,
        "local_registry_payload_sha256": local_payload_sha256,
        "local_contract_registry_verified": (
            canonical_payload_sha256 == local_payload_sha256
        ),
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


def signal_consumer_contract_registry_audit_summary_from_manifest(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    require_all_known_consumers: bool = False,
) -> dict[str, Any]:
    """Validate a registry manifest and return non-sensitive audit metadata."""

    manifest_path = Path(path)
    manifest = _load_signal_consumer_contract_registry_manifest(manifest_path)
    registry_path = _resolve_relative_artifact_path(
        manifest_path.parent.resolve(),
        manifest["registry_path"],
        owner="consumer contract manifest",
        field="registry_path",
    )
    registry_summary = signal_consumer_contract_registry_audit_summary_from_file(
        registry_path,
        expected_canonical_input=expected_canonical_input,
        require_all_known_consumers=require_all_known_consumers,
    )
    _validate_signal_consumer_contract_registry_manifest_consistency(
        manifest,
        registry_summary=registry_summary,
    )
    return {
        "manifest_path": str(manifest_path.resolve()),
        "manifest_schema_version": str(manifest["schema_version"]),
        "manifest_sha256": _sha256_file(manifest_path),
        "manifest_size_bytes": manifest_path.stat().st_size,
        "artifact_type": str(manifest["artifact_type"]),
        "registry_path": registry_summary["path"],
        "registry_sha256": registry_summary["sha256"],
        "registry_size_bytes": registry_summary["size_bytes"],
        "registry_schema_version": registry_summary["schema_version"],
        "canonical_input": registry_summary["canonical_input"],
        "consumer_count": registry_summary["consumer_count"],
        "consumers": registry_summary["consumers"],
        "known_consumer_count": registry_summary["known_consumer_count"],
        "missing_known_consumers": registry_summary["missing_known_consumers"],
        "all_known_consumers_present": registry_summary["all_known_consumers_present"],
        "canonical_registry_payload_sha256": registry_summary[
            "canonical_registry_payload_sha256"
        ],
        "local_registry_payload_sha256": registry_summary[
            "local_registry_payload_sha256"
        ],
        "local_contract_registry_verified": registry_summary[
            "local_contract_registry_verified"
        ],
    }


def _load_signal_consumer_contract_registry_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        manifest = json.load(file_obj)
    if not isinstance(manifest, Mapping):
        raise SignalBundleContractError(
            "consumer contract manifest JSON root must be a mapping"
        )
    manifest_dict = dict(manifest)
    _validate_no_sensitive_fields(
        manifest_dict,
        path="consumer_contract_manifest",
    )
    if (
        manifest_dict.get("schema_version")
        != MARKET_SIGNAL_CONSUMER_CONTRACT_MANIFEST_SCHEMA_VERSION
    ):
        raise SignalBundleContractError(
            "unsupported consumer contract manifest schema_version: "
            f"{manifest_dict.get('schema_version')!r}"
        )
    if manifest_dict.get("artifact_type") != "market_signal_consumer_contract_registry":
        raise SignalBundleContractError(
            "consumer contract manifest artifact_type mismatch: "
            f"{manifest_dict.get('artifact_type')!r}"
        )
    for field in (
        "registry_path",
        "registry_sha256",
        "registry_size_bytes",
        "registry_schema_version",
        "canonical_input",
        "consumer_count",
        "known_consumer_count",
        "missing_known_consumers",
        "all_known_consumers_present",
    ):
        if not _has_non_empty_value(manifest_dict, field):
            raise SignalBundleContractError(
                f"consumer contract manifest missing field: {field}"
            )
    if not _is_string_sequence(manifest_dict["missing_known_consumers"]):
        raise SignalBundleContractError(
            "consumer contract manifest missing_known_consumers must be strings"
        )
    if not isinstance(manifest_dict["all_known_consumers_present"], bool):
        raise SignalBundleContractError(
            "consumer contract manifest all_known_consumers_present must be a bool"
        )
    return manifest_dict


def _validate_signal_consumer_contract_registry_manifest_consistency(
    manifest: Mapping[str, Any],
    *,
    registry_summary: Mapping[str, Any],
) -> None:
    expected_values = {
        "registry_sha256": registry_summary["sha256"],
        "registry_size_bytes": registry_summary["size_bytes"],
        "registry_schema_version": registry_summary["schema_version"],
        "canonical_input": registry_summary["canonical_input"],
        "consumer_count": registry_summary["consumer_count"],
        "known_consumer_count": registry_summary["known_consumer_count"],
        "all_known_consumers_present": registry_summary["all_known_consumers_present"],
    }
    for field, expected in expected_values.items():
        if manifest[field] != expected:
            raise SignalBundleContractError(
                f"consumer contract manifest {field} mismatch: "
                f"{manifest[field]!r} != {expected!r}"
            )
    if tuple(manifest["missing_known_consumers"]) != tuple(
        registry_summary["missing_known_consumers"]
    ):
        raise SignalBundleContractError(
            "consumer contract manifest missing_known_consumers mismatch: "
            f"{manifest['missing_known_consumers']!r} != "
            f"{registry_summary['missing_known_consumers']!r}"
        )


def _validate_optional_registry_verification_fields(
    payload: Mapping[str, Any],
    *,
    owner: str,
) -> None:
    fields = (
        "canonical_registry_payload_sha256",
        "local_registry_payload_sha256",
        "local_contract_registry_verified",
    )
    if not any(field in payload for field in fields):
        return
    for field in fields[:2]:
        value = str(payload.get(field, "")).strip().lower()
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise SignalBundleContractError(f"{owner} invalid sha256 field: {field}")
    if payload.get("local_contract_registry_verified") is not True:
        raise SignalBundleContractError(
            f"{owner} local consumer contract registry is not verified"
        )


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


def _signal_consumer_contract_record(consumer: str) -> dict[str, Any]:
    required_fields = required_indicator_fields_for_consumer(consumer)
    return {
        "consumer": str(consumer),
        "canonical_input": CANONICAL_INPUT_DERIVED_INDICATORS,
        "required_indicator_fields_by_symbol": {
            symbol: list(fields)
            for symbol, fields in required_fields.items()
        },
    }


def _canonical_registry_payload_sha256(registry: Mapping[str, Any]) -> str:
    normalized = {
        "schema_version": MARKET_SIGNAL_CONSUMER_CONTRACTS_SCHEMA_VERSION,
        "canonical_input": CANONICAL_INPUT_DERIVED_INDICATORS,
        "contracts": sorted(
            (
                {
                    "consumer": str(contract["consumer"]),
                    "canonical_input": CANONICAL_INPUT_DERIVED_INDICATORS,
                    "required_indicator_fields_by_symbol": {
                        str(symbol): [
                            str(field)
                            for field in fields
                        ]
                        for symbol, fields in sorted(
                            contract["required_indicator_fields_by_symbol"].items()
                        )
                    },
                }
                for contract in registry["contracts"]
            ),
            key=lambda contract: str(contract["consumer"]),
        ),
    }
    return hashlib.sha256(
        json.dumps(
            normalized,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _local_registry_payload_sha256(consumers: Iterable[str]) -> str:
    return _canonical_registry_payload_sha256(
        signal_consumer_contract_registry_payload(consumers=tuple(sorted(consumers)))
    )
