from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
from os import PathLike
from pathlib import Path
from typing import Any

from .constants import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_MANIFEST_SCHEMA_VERSION,
    MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_SCHEMA_VERSION,
    REQUIRED_INDICATOR_FIELDS_BY_CONSUMER,
)

from .errors import (
    SignalBundleContractError,
)

from .provenance import (
    _is_string_sequence,
    _normalize_symbol,
    _resolve_relative_artifact_path,
    _sha256_file,
    _string_tuple,
    _validate_no_sensitive_fields,
)

from .consumer_contracts import (
    required_indicator_fields_for_consumer,
)


def signal_source_family_catalog_audit_summary_from_manifest(
    path: str | PathLike[str],
    *,
    required_consumers: Iterable[str] = (),
    expected_transform: str | None = None,
    require_all_known_families: bool = False,
    require_runtime_consumer_coverage: bool = False,
) -> dict[str, Any]:
    """Validate an external source-family catalog manifest and linked catalog."""

    manifest_path = Path(path)
    manifest = _load_signal_source_family_catalog_manifest(manifest_path)
    if require_all_known_families and not manifest["all_known_families_present"]:
        raise SignalBundleContractError(
            "signal source family catalog manifest missing known families: "
            + ", ".join(str(item) for item in manifest["missing_known_families"])
        )
    if (
        require_runtime_consumer_coverage
        and manifest.get("all_runtime_consumers_covered") is not True
    ):
        raise SignalBundleContractError(
            "signal source family catalog manifest runtime consumer coverage is incomplete"
        )
    catalog_path = _resolve_relative_artifact_path(
        manifest_path.parent.resolve(),
        manifest["catalog_path"],
        owner="signal source family catalog manifest",
        field="catalog_path",
    )
    catalog_summary = signal_source_family_catalog_audit_summary_from_file(
        catalog_path,
        required_consumers=required_consumers,
        expected_transform=expected_transform,
        require_all_known_families=require_all_known_families,
        require_runtime_consumer_coverage=require_runtime_consumer_coverage,
    )
    _validate_signal_source_family_catalog_manifest_consistency(
        manifest,
        catalog_summary=catalog_summary,
    )
    return {
        "manifest_path": str(manifest_path.resolve()),
        "manifest_schema_version": str(manifest["schema_version"]),
        "manifest_sha256": _sha256_file(manifest_path),
        "manifest_size_bytes": manifest_path.stat().st_size,
        "artifact_type": str(manifest["artifact_type"]),
        "catalog_path": catalog_summary["path"],
        "catalog_sha256": catalog_summary["sha256"],
        "catalog_size_bytes": catalog_summary["size_bytes"],
        "catalog_schema_version": catalog_summary["schema_version"],
        "family_count": catalog_summary["family_count"],
        "families": catalog_summary["families"],
        "known_family_count": manifest["known_family_count"],
        "missing_known_families": tuple(manifest["missing_known_families"]),
        "all_known_families_present": manifest["all_known_families_present"],
        "all_consumer_contracts_satisfied": catalog_summary[
            "all_consumer_contracts_satisfied"
        ],
        "runtime_consumer_coverage_present": (
            "all_runtime_consumers_covered" in manifest
        ),
        "all_runtime_consumers_covered": manifest.get(
            "all_runtime_consumers_covered"
        ),
        "expected_transform": expected_transform or "",
        "required_signal_consumers": tuple(
            str(consumer).strip()
            for consumer in required_consumers
            if str(consumer).strip()
        ),
        "matched_family_count": catalog_summary["matched_family_count"],
        "matched_families": catalog_summary["matched_families"],
        "required_signal_consumers_present": catalog_summary[
            "required_signal_consumers_present"
        ],
        "catalog_sha256_verified": True,
        "catalog_size_bytes_verified": True,
    }


def signal_source_family_catalog_audit_summary_from_file(
    path: str | PathLike[str],
    *,
    required_consumers: Iterable[str] = (),
    expected_transform: str | None = None,
    require_all_known_families: bool = False,
    require_runtime_consumer_coverage: bool = False,
) -> dict[str, Any]:
    """Validate an external source-family catalog JSON artifact."""

    catalog_path = Path(path)
    catalog = _load_signal_source_family_catalog(catalog_path)
    required_consumer_tuple = tuple(
        str(consumer).strip()
        for consumer in required_consumers
        if str(consumer).strip()
    )
    matched_families = _matching_source_catalog_families(
        catalog["families"],
        required_consumers=required_consumer_tuple,
        expected_transform=expected_transform,
    )
    if required_consumer_tuple and not matched_families:
        raise SignalBundleContractError(
            "signal source family catalog missing family for required consumers: "
            + ", ".join(required_consumer_tuple)
        )
    families = tuple(str(record["family"]) for record in catalog["families"])
    missing_known_families: tuple[str, ...] = ()
    if require_all_known_families and missing_known_families:
        raise SignalBundleContractError(
            "signal source family catalog missing known families: "
            + ", ".join(missing_known_families)
        )
    runtime_coverage = _source_family_runtime_consumer_coverage(catalog["families"])
    if (
        require_runtime_consumer_coverage
        and not runtime_coverage["all_runtime_consumers_covered"]
    ):
        raise SignalBundleContractError(
            "signal source family catalog runtime consumer coverage is incomplete"
        )
    return {
        "path": str(catalog_path.resolve()),
        "schema_version": str(catalog["schema_version"]),
        "family_count": len(families),
        "families": families,
        "missing_known_families": missing_known_families,
        "all_known_families_present": not missing_known_families,
        "all_consumer_contracts_satisfied": _all_source_family_contracts_satisfied(
            catalog["families"]
        ),
        "runtime_consumer_coverage": runtime_coverage,
        "all_runtime_consumers_covered": runtime_coverage[
            "all_runtime_consumers_covered"
        ],
        "matched_family_count": len(matched_families),
        "matched_families": matched_families,
        "required_signal_consumers_present": not required_consumer_tuple
        or bool(matched_families),
        "sha256": _sha256_file(catalog_path),
        "size_bytes": catalog_path.stat().st_size,
    }


def _load_signal_source_family_catalog_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        manifest = json.load(file_obj)
    if not isinstance(manifest, Mapping):
        raise SignalBundleContractError(
            "signal source family catalog manifest JSON root must be a mapping"
        )
    manifest_dict = dict(manifest)
    _validate_no_sensitive_fields(
        manifest_dict,
        path="signal_source_family_catalog_manifest",
    )
    if (
        manifest_dict.get("schema_version")
        != MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_MANIFEST_SCHEMA_VERSION
    ):
        raise SignalBundleContractError(
            "unsupported signal source family catalog manifest schema_version: "
            f"{manifest_dict.get('schema_version')!r}"
        )
    if manifest_dict.get("artifact_type") != "market_signal_source_family_catalog":
        raise SignalBundleContractError(
            "signal source family catalog manifest artifact_type mismatch: "
            f"{manifest_dict.get('artifact_type')!r}"
        )
    for field in (
        "catalog_path",
        "catalog_sha256",
        "catalog_size_bytes",
        "catalog_schema_version",
        "family_count",
        "known_family_count",
        "missing_known_families",
        "all_known_families_present",
        "all_consumer_contracts_satisfied",
    ):
        if field not in manifest_dict:
            raise SignalBundleContractError(
                f"signal source family catalog manifest missing field: {field}"
            )
    if not _is_string_sequence(manifest_dict["missing_known_families"]):
        raise SignalBundleContractError(
            "signal source family catalog manifest missing_known_families must be strings"
        )
    if not isinstance(manifest_dict["all_known_families_present"], bool):
        raise SignalBundleContractError(
            "signal source family catalog manifest all_known_families_present must be a bool"
        )
    if not isinstance(manifest_dict["all_consumer_contracts_satisfied"], bool):
        raise SignalBundleContractError(
            "signal source family catalog manifest all_consumer_contracts_satisfied must be a bool"
        )
    if (
        "all_runtime_consumers_covered" in manifest_dict
        and not isinstance(manifest_dict["all_runtime_consumers_covered"], bool)
    ):
        raise SignalBundleContractError(
            "signal source family catalog manifest all_runtime_consumers_covered must be a bool"
        )
    return manifest_dict


def _load_signal_source_family_catalog(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        catalog = json.load(file_obj)
    if not isinstance(catalog, Mapping):
        raise SignalBundleContractError(
            "signal source family catalog JSON root must be a mapping"
        )
    catalog_dict = dict(catalog)
    _validate_no_sensitive_fields(catalog_dict, path="signal_source_family_catalog")
    if (
        catalog_dict.get("schema_version")
        != MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_SCHEMA_VERSION
    ):
        raise SignalBundleContractError(
            "unsupported signal source family catalog schema_version: "
            f"{catalog_dict.get('schema_version')!r}"
        )
    families = catalog_dict.get("families")
    if not isinstance(families, list) or not families:
        raise SignalBundleContractError(
            "signal source family catalog families must be a non-empty list"
        )
    seen: set[str] = set()
    for record in families:
        _validate_signal_source_family_record(record, seen_families=seen)
    return catalog_dict


def _validate_signal_source_family_catalog_manifest_consistency(
    manifest: Mapping[str, Any],
    *,
    catalog_summary: Mapping[str, Any],
) -> None:
    expected_values = {
        "catalog_sha256": catalog_summary["sha256"],
        "catalog_size_bytes": catalog_summary["size_bytes"],
        "catalog_schema_version": catalog_summary["schema_version"],
        "family_count": catalog_summary["family_count"],
        "all_consumer_contracts_satisfied": catalog_summary[
            "all_consumer_contracts_satisfied"
        ],
    }
    if "all_runtime_consumers_covered" in manifest:
        expected_values["all_runtime_consumers_covered"] = catalog_summary[
            "all_runtime_consumers_covered"
        ]
    for field, expected in expected_values.items():
        if manifest[field] != expected:
            raise SignalBundleContractError(
                f"signal source family catalog manifest {field} mismatch: "
                f"{manifest[field]!r} != {expected!r}"
            )


def _validate_signal_source_family_record(
    record: object,
    *,
    seen_families: set[str],
) -> None:
    if not isinstance(record, Mapping):
        raise SignalBundleContractError(
            "signal source family catalog records must be mappings"
        )
    family = str(record.get("family", "")).strip()
    if not family:
        raise SignalBundleContractError("signal source family record missing family")
    if family in seen_families:
        raise SignalBundleContractError(f"duplicate signal source family: {family}")
    seen_families.add(family)
    if str(record.get("canonical_input", "")).strip() != CANONICAL_INPUT_DERIVED_INDICATORS:
        raise SignalBundleContractError(
            f"signal source family {family} canonical_input mismatch"
        )
    if not str(record.get("transform", "")).strip():
        raise SignalBundleContractError(
            f"signal source family {family} missing transform"
        )
    for field in ("symbols", "derived_indicator_fields", "compatible_profiles"):
        if not _is_string_sequence(record.get(field)):
            raise SignalBundleContractError(
                f"signal source family {family} {field} must be strings"
            )


def _matching_source_catalog_families(
    families: Sequence[object],
    *,
    required_consumers: tuple[str, ...],
    expected_transform: str | None,
) -> tuple[str, ...]:
    matched: list[str] = []
    for record in families:
        if not isinstance(record, Mapping):
            raise SignalBundleContractError(
                "signal source family catalog records must be mappings"
            )
        family = str(record.get("family", "")).strip()
        transform = str(record.get("transform", "")).strip()
        if expected_transform is not None and transform != expected_transform:
            continue
        compatible_profiles = _string_tuple(record.get("compatible_profiles"))
        if not all(consumer in compatible_profiles for consumer in required_consumers):
            continue
        if not _source_family_covers_required_fields(
            record,
            required_consumers=required_consumers,
        ):
            continue
        matched.append(family)
    return tuple(matched)


def _all_source_family_contracts_satisfied(families: Sequence[object]) -> bool:
    return all(
        isinstance(record, Mapping)
        and _source_family_covers_required_fields(
            record,
            required_consumers=_string_tuple(record.get("compatible_profiles")),
        )
        for record in families
    )


def _source_family_runtime_consumer_coverage(
    families: Sequence[object],
) -> dict[str, Any]:
    known_runtime_consumers = tuple(
        sorted(
            consumer
            for consumer in REQUIRED_INDICATOR_FIELDS_BY_CONSUMER
            if not consumer.startswith("research:")
        )
    )
    source_families_by_consumer: dict[str, list[str]] = {
        consumer: []
        for consumer in known_runtime_consumers
    }
    runtime_consumers_seen: set[str] = set()
    unknown_runtime_consumers: set[str] = set()
    consumer_scope_errors: list[str] = []

    for record in families:
        if not isinstance(record, Mapping):
            continue
        family = str(record.get("family", "")).strip()
        compatible_profiles = set(_string_tuple(record.get("compatible_profiles")))
        runtime_consumers = set(_string_tuple(record.get("runtime_consumers")))
        research_consumers = set(_string_tuple(record.get("research_consumers")))
        declared_consumers = runtime_consumers | research_consumers
        if declared_consumers != compatible_profiles:
            consumer_scope_errors.append(f"{family}:consumer_scope_mismatch")
        if runtime_consumers & research_consumers:
            consumer_scope_errors.append(f"{family}:runtime_research_consumer_overlap")

        for consumer in runtime_consumers:
            if consumer.startswith("research:"):
                consumer_scope_errors.append(f"{family}:research_consumer_marked_runtime")
                continue
            runtime_consumers_seen.add(consumer)
            if consumer in source_families_by_consumer:
                source_families_by_consumer[consumer].append(family)
            else:
                unknown_runtime_consumers.add(consumer)

        for consumer in research_consumers:
            if not consumer.startswith("research:"):
                consumer_scope_errors.append(f"{family}:runtime_consumer_marked_research")

    missing_runtime_consumers = tuple(
        consumer
        for consumer, source_families in source_families_by_consumer.items()
        if not source_families
    )
    return {
        "known_runtime_consumers": known_runtime_consumers,
        "known_runtime_consumer_count": len(known_runtime_consumers),
        "runtime_consumers": tuple(sorted(runtime_consumers_seen)),
        "runtime_consumer_count": len(runtime_consumers_seen),
        "runtime_consumer_source_families": {
            consumer: tuple(source_families)
            for consumer, source_families in sorted(source_families_by_consumer.items())
        },
        "runtime_consumers_without_source_family": missing_runtime_consumers,
        "unknown_runtime_consumers": tuple(sorted(unknown_runtime_consumers)),
        "consumer_scope_errors": tuple(consumer_scope_errors),
        "all_runtime_consumers_covered": (
            not missing_runtime_consumers
            and not unknown_runtime_consumers
            and not consumer_scope_errors
        ),
    }


def _source_family_covers_required_fields(
    record: Mapping[str, Any],
    *,
    required_consumers: tuple[str, ...],
) -> bool:
    symbols = {_normalize_symbol(symbol) for symbol in _string_tuple(record.get("symbols"))}
    fields = {
        str(field).strip().lower()
        for field in _string_tuple(record.get("derived_indicator_fields"))
    }
    for consumer in required_consumers:
        required_fields_by_symbol = required_indicator_fields_for_consumer(consumer)
        for symbol, required_fields in required_fields_by_symbol.items():
            if _normalize_symbol(symbol) not in symbols:
                return False
            for field in required_fields:
                if str(field).strip().lower() not in fields:
                    return False
    return True
