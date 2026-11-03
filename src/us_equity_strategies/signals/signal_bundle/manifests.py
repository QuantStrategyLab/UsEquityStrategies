from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from os import PathLike
from pathlib import Path
from typing import Any

from .constants import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    FRESHNESS_FRESH,
    MARKET_SIGNAL_PLATFORM_HANDOFF_INDEX_SCHEMA_VERSION,
    MARKET_SIGNAL_PLATFORM_HANDOFF_SCHEMA_VERSION,
    MARKET_SIGNAL_RESEARCH_HANDOFF_SCHEMA_VERSION,
    RESEARCH_EXPORT_SCHEMA_VERSION,
)

from .errors import (
    SignalBundleContractError,
)

from .provenance import (
    _has_non_empty_value,
    _is_non_string_sequence,
    _is_string_sequence,
    _load_json_mapping,
    _resolve_relative_artifact_path,
    _sha256_file,
    _string_tuple,
    _validate_no_sensitive_fields,
    _validate_optional_count_matches_sequence,
)

from .consumer_contracts import (
    _validate_optional_registry_verification_fields,
    signal_consumer_contract_registry_audit_summary_from_manifest,
)

from .catalog import (
    signal_source_family_catalog_audit_summary_from_manifest,
)

from .extraction import (
    extract_canonical_input_from_manifest_for_consumer,
    signal_bundle_audit_summary_from_manifest,
    signal_bundle_consumer_audit_summary_from_manifest,
)


def signal_platform_handoff_audit_summary_from_manifest(
    path: str | PathLike[str],
    *,
    consumer: str | None = None,
    required_consumers: Iterable[str] = (),
    expected_source_transform: str | None = None,
    require_all_known_families: bool = False,
    require_all_known_consumers: bool = False,
    require_runtime_consumer_coverage: bool = False,
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, Any]:
    """Validate a MarketSignalSources platform handoff manifest and its links."""

    handoff_path = Path(path)
    handoff = _load_platform_signal_handoff_manifest(handoff_path)
    handoff_root = handoff_path.parent.resolve()
    signal_bundle_manifest_path = _resolve_relative_artifact_path(
        handoff_root,
        handoff["signal_bundle_manifest_path"],
        owner="platform signal handoff",
        field="signal_bundle_manifest_path",
    )
    source_catalog_manifest_path = _resolve_relative_artifact_path(
        handoff_root,
        handoff["source_family_catalog_manifest_path"],
        owner="platform signal handoff",
        field="source_family_catalog_manifest_path",
    )
    consumer_registry_manifest_path = _resolve_relative_artifact_path(
        handoff_root,
        handoff["consumer_contract_registry_manifest_path"],
        owner="platform signal handoff",
        field="consumer_contract_registry_manifest_path",
    )
    _validate_handoff_linked_sha256(
        signal_bundle_manifest_path,
        handoff["signal_bundle_manifest_sha256"],
        field="signal_bundle_manifest_sha256",
    )
    _validate_handoff_linked_sha256(
        source_catalog_manifest_path,
        handoff["source_family_catalog_manifest_sha256"],
        field="source_family_catalog_manifest_sha256",
    )
    _validate_handoff_linked_sha256(
        consumer_registry_manifest_path,
        handoff["consumer_contract_registry_manifest_sha256"],
        field="consumer_contract_registry_manifest_sha256",
    )

    target_consumer = str(consumer or handoff.get("consumer", "")).strip()
    target_required_consumers = tuple(
        str(item).strip()
        for item in required_consumers
        if str(item).strip()
    )
    if target_consumer and not target_required_consumers:
        target_required_consumers = (target_consumer,)
    if target_consumer:
        bundle_summary = signal_bundle_consumer_audit_summary_from_manifest(
            signal_bundle_manifest_path,
            consumer=target_consumer,
            now=now,
            reference_time=reference_time,
        )
    else:
        bundle_summary = signal_bundle_audit_summary_from_manifest(
            signal_bundle_manifest_path
        )
    source_catalog_summary = signal_source_family_catalog_audit_summary_from_manifest(
        source_catalog_manifest_path,
        required_consumers=target_required_consumers,
        expected_transform=expected_source_transform,
        require_all_known_families=require_all_known_families,
        require_runtime_consumer_coverage=require_runtime_consumer_coverage,
    )
    consumer_registry_summary = (
        signal_consumer_contract_registry_audit_summary_from_manifest(
            consumer_registry_manifest_path,
            require_all_known_consumers=require_all_known_consumers,
        )
    )
    missing_required_consumers = tuple(
        required_consumer
        for required_consumer in target_required_consumers
        if required_consumer not in consumer_registry_summary["consumers"]
    )
    if missing_required_consumers:
        raise SignalBundleContractError(
            "platform signal handoff consumer contract registry missing required "
            "consumers: "
            + ", ".join(missing_required_consumers)
        )

    summary = _platform_handoff_summary(
        handoff_path=handoff_path,
        handoff=handoff,
        signal_bundle_manifest_path=signal_bundle_manifest_path,
        bundle_summary=bundle_summary,
        source_catalog_manifest_path=source_catalog_manifest_path,
        source_catalog_summary=source_catalog_summary,
        consumer_registry_manifest_path=consumer_registry_manifest_path,
        consumer_registry_summary=consumer_registry_summary,
        consumer=target_consumer,
        required_consumers=target_required_consumers,
    )
    _validate_platform_handoff_consistency(handoff, summary)
    return summary


def load_research_export_manifest(path: str | PathLike[str]) -> dict[str, Any]:
    """Load and validate a MarketSignalSources research CSV export manifest."""

    manifest_path = Path(path)
    with manifest_path.open(encoding="utf-8") as file_obj:
        manifest = json.load(file_obj)
    if not isinstance(manifest, Mapping):
        raise SignalBundleContractError(
            "research export manifest JSON root must be a mapping"
        )
    manifest_dict = dict(manifest)
    _validate_research_export_manifest_shape(manifest_dict)
    return manifest_dict


def research_export_audit_summary_from_manifest(
    path: str | PathLike[str],
    *,
    expected_artifact_type: str | None = None,
    expected_transform: str | None = None,
) -> dict[str, Any]:
    """Validate the research output CSV and return non-sensitive audit metadata."""

    manifest_path = Path(path)
    manifest = load_research_export_manifest(manifest_path)
    if (
        expected_artifact_type is not None
        and str(manifest["artifact_type"]).strip() != str(expected_artifact_type).strip()
    ):
        raise SignalBundleContractError(
            "research export artifact_type mismatch: "
            f"{manifest['artifact_type']!r} != {expected_artifact_type!r}"
        )
    if (
        expected_transform is not None
        and str(manifest["transform"]).strip() != str(expected_transform).strip()
    ):
        raise SignalBundleContractError(
            "research export transform mismatch: "
            f"{manifest['transform']!r} != {expected_transform!r}"
        )

    output_record = dict(manifest["output_csv"])
    output_path = _resolve_research_manifest_file_path(
        manifest_path,
        output_record["path"],
        field="output_csv.path",
    )
    _validate_research_manifest_file_record(
        output_record,
        output_path,
        field="output_csv",
    )
    quality_summary: dict[str, object] = {}
    quality_record = manifest.get("quality_report")
    if quality_record is not None:
        if not isinstance(quality_record, Mapping):
            raise SignalBundleContractError(
                "research export quality_report must be a mapping"
            )
        quality_record = dict(quality_record)
        quality_path = _resolve_research_manifest_file_path(
            manifest_path,
            quality_record["path"],
            field="quality_report.path",
        )
        _validate_research_manifest_file_record(
            quality_record,
            quality_path,
            field="quality_report",
        )
        quality_payload = _load_json_mapping(
            quality_path,
            label="research export quality report",
        )
        _validate_no_sensitive_fields(
            quality_payload,
            path="research_export_quality_report",
        )
        quality_summary = {
            "quality_report_path": str(quality_path.resolve()),
            "quality_report_sha256": str(quality_record["sha256"]).strip().lower(),
            "quality_report_size_bytes": int(quality_record["size_bytes"]),
            "quality_report_schema_version": str(
                quality_payload.get("schema_version", "")
            ),
            "quality_report_artifact_type": str(
                quality_payload.get("artifact_type", "")
            ),
            "quality_report_sha256_verified": True,
            "quality_report_size_bytes_verified": True,
        }

    summary = {
        "manifest_path": str(manifest_path.resolve()),
        "manifest_schema_version": str(manifest["schema_version"]),
        "manifest_sha256": _sha256_file(manifest_path),
        "manifest_size_bytes": manifest_path.stat().st_size,
        "artifact_type": str(manifest["artifact_type"]),
        "transform": str(manifest["transform"]),
        "source_version": str(manifest["source_version"]),
        "as_of": manifest.get("as_of"),
        "min_history": int(manifest["min_history"]),
        "row_count": int(manifest["row_count"]),
        "first_date": str(manifest["first_date"]),
        "last_date": str(manifest["last_date"]),
        "columns": tuple(str(column) for column in manifest["columns"]),
        "input_csv_sha256": str(manifest["input_csv"]["sha256"]).strip().lower(),
        "output_csv_path": str(output_path.resolve()),
        "output_csv_sha256": str(output_record["sha256"]).strip().lower(),
        "output_csv_size_bytes": int(output_record["size_bytes"]),
        "output_csv_sha256_verified": True,
        "output_csv_size_bytes_verified": True,
    }
    summary.update(quality_summary)
    return summary


def load_research_signal_handoff_manifest(
    path: str | PathLike[str],
) -> dict[str, Any]:
    """Load and validate a MarketSignalSources research handoff manifest."""

    handoff_path = Path(path)
    with handoff_path.open(encoding="utf-8") as file_obj:
        handoff = json.load(file_obj)
    if not isinstance(handoff, Mapping):
        raise SignalBundleContractError(
            "research signal handoff manifest JSON root must be a mapping"
        )
    handoff_dict = dict(handoff)
    _validate_research_handoff_shape(handoff_dict)
    return handoff_dict


def signal_research_handoff_audit_summary_from_manifest(
    path: str | PathLike[str],
    *,
    consumer: str | None = None,
    expected_research_artifact_type: str | None = None,
    require_all_known_families: bool = False,
    require_all_known_consumers: bool = False,
    require_runtime_consumer_coverage: bool = False,
) -> dict[str, Any]:
    """Validate a MarketSignalSources research handoff manifest and its links."""

    handoff_path = Path(path)
    handoff = load_research_signal_handoff_manifest(handoff_path)
    handoff_root = handoff_path.parent.resolve()
    research_export_manifest_path = _resolve_relative_artifact_path(
        handoff_root,
        handoff["research_export_manifest_path"],
        owner="research signal handoff",
        field="research_export_manifest_path",
    )
    source_catalog_manifest_path = _resolve_relative_artifact_path(
        handoff_root,
        handoff["source_family_catalog_manifest_path"],
        owner="research signal handoff",
        field="source_family_catalog_manifest_path",
    )
    consumer_registry_manifest_path = _resolve_relative_artifact_path(
        handoff_root,
        handoff["consumer_contract_registry_manifest_path"],
        owner="research signal handoff",
        field="consumer_contract_registry_manifest_path",
    )
    _validate_handoff_linked_sha256(
        research_export_manifest_path,
        handoff["research_export_manifest_sha256"],
        field="research_export_manifest_sha256",
    )
    _validate_handoff_linked_sha256(
        source_catalog_manifest_path,
        handoff["source_family_catalog_manifest_sha256"],
        field="source_family_catalog_manifest_sha256",
    )
    _validate_handoff_linked_sha256(
        consumer_registry_manifest_path,
        handoff["consumer_contract_registry_manifest_sha256"],
        field="consumer_contract_registry_manifest_sha256",
    )

    target_consumer = str(consumer or handoff.get("consumer", "")).strip()
    required_consumers = (target_consumer,) if target_consumer else ()
    research_summary = research_export_audit_summary_from_manifest(
        research_export_manifest_path,
        expected_artifact_type=expected_research_artifact_type,
        expected_transform=str(handoff["research_transform"]),
    )
    source_catalog_summary = signal_source_family_catalog_audit_summary_from_manifest(
        source_catalog_manifest_path,
        required_consumers=required_consumers,
        expected_transform=str(research_summary["transform"]),
        require_all_known_families=require_all_known_families,
        require_runtime_consumer_coverage=require_runtime_consumer_coverage,
    )
    if not source_catalog_summary["matched_families"]:
        raise SignalBundleContractError(
            "research signal handoff source catalog missing family for transform: "
            f"{research_summary['transform']}"
        )
    consumer_registry_summary = (
        signal_consumer_contract_registry_audit_summary_from_manifest(
            consumer_registry_manifest_path,
            require_all_known_consumers=require_all_known_consumers,
        )
    )
    missing_required_consumers = tuple(
        required_consumer
        for required_consumer in required_consumers
        if required_consumer not in consumer_registry_summary["consumers"]
    )
    if missing_required_consumers:
        raise SignalBundleContractError(
            "research signal handoff consumer contract registry missing required "
            "consumers: "
            + ", ".join(missing_required_consumers)
        )

    summary = _research_handoff_summary(
        handoff_path=handoff_path,
        handoff=handoff,
        research_export_manifest_path=research_export_manifest_path,
        research_summary=research_summary,
        source_catalog_manifest_path=source_catalog_manifest_path,
        source_catalog_summary=source_catalog_summary,
        consumer_registry_manifest_path=consumer_registry_manifest_path,
        consumer_registry_summary=consumer_registry_summary,
        consumer=target_consumer,
    )
    _validate_research_handoff_consistency(handoff, summary)
    return summary


def extract_canonical_input_from_platform_handoff_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate a platform handoff manifest and return consumer market_data."""

    summary = signal_platform_handoff_audit_summary_from_manifest(
        path,
        consumer=consumer,
        now=now,
        reference_time=reference_time,
    )
    return extract_canonical_input_from_manifest_for_consumer(
        summary["signal_bundle_manifest_path"],
        consumer=consumer,
        now=now,
        reference_time=reference_time,
    )


def load_platform_signal_handoff_index(path: str | PathLike[str]) -> dict[str, Any]:
    """Load and validate a MarketSignalSources platform handoff index."""

    index_path = Path(path)
    with index_path.open(encoding="utf-8") as file_obj:
        index = json.load(file_obj)
    if not isinstance(index, Mapping):
        raise SignalBundleContractError(
            "platform signal handoff index JSON root must be a mapping"
        )
    index_dict = dict(index)
    _validate_no_sensitive_fields(index_dict, path="platform_signal_handoff_index")
    if (
        index_dict.get("schema_version")
        != MARKET_SIGNAL_PLATFORM_HANDOFF_INDEX_SCHEMA_VERSION
    ):
        raise SignalBundleContractError(
            "unsupported platform signal handoff index schema_version: "
            f"{index_dict.get('schema_version')!r}"
        )
    if index_dict.get("artifact_type") != "market_signal_platform_handoff_index":
        raise SignalBundleContractError(
            "platform signal handoff index artifact_type mismatch: "
            f"{index_dict.get('artifact_type')!r}"
        )
    handoffs = index_dict.get("handoffs")
    if not _is_non_string_sequence(handoffs) or not handoffs:
        raise SignalBundleContractError(
            "platform signal handoff index handoffs must be a non-empty sequence"
        )
    for raw_entry in handoffs:
        _validate_platform_handoff_index_entry(raw_entry)
    return index_dict


def resolve_platform_signal_handoff_manifest_from_index(
    path: str | PathLike[str],
    *,
    consumer: str | None = None,
    required_consumers: Iterable[str] = (),
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> Path:
    """Resolve the latest matching platform handoff manifest from an index."""

    index_path = Path(path)
    index = load_platform_signal_handoff_index(index_path)
    accepted = {str(item).strip().lower() for item in accepted_freshness_statuses}
    target_as_of = str(as_of).strip() if as_of is not None else None
    target_consumers = _required_handoff_index_consumers(
        consumer=consumer,
        required_consumers=required_consumers,
    )
    candidates: list[Mapping[str, Any]] = []

    for raw_entry in index["handoffs"]:
        entry = dict(raw_entry)
        entry_canonical_input = str(entry.get("canonical_input", "")).strip()
        entry_freshness = str(entry.get("freshness_status", "")).strip().lower()
        entry_as_of = str(entry.get("as_of", "")).strip()
        if entry_canonical_input != expected_canonical_input:
            continue
        if entry_freshness not in accepted:
            continue
        if target_as_of is not None and entry_as_of > target_as_of:
            continue
        if target_consumers and not _handoff_index_entry_matches_consumers(
            entry,
            target_consumers,
        ):
            continue
        candidates.append(entry)

    if not candidates:
        raise SignalBundleContractError(
            "platform signal handoff index has no matching handoff manifest entry"
        )

    selected = max(candidates, key=lambda entry: str(entry.get("as_of", "")))
    resolved_handoff_path = _resolve_relative_artifact_path(
        index_path.parent.resolve(),
        selected["handoff_manifest_path"],
        owner="platform signal handoff index",
        field="handoff_manifest_path",
    )
    expected_handoff_sha256 = str(selected["handoff_manifest_sha256"]).strip().lower()
    actual_handoff_sha256 = _sha256_file(resolved_handoff_path)
    if actual_handoff_sha256 != expected_handoff_sha256:
        raise SignalBundleContractError(
            "platform signal handoff index handoff_manifest_sha256 mismatch: "
            f"expected {expected_handoff_sha256}, got {actual_handoff_sha256}"
        )
    handoff = _load_platform_signal_handoff_manifest(resolved_handoff_path)
    _validate_handoff_index_manifest_consistency(selected, handoff)
    return resolved_handoff_path


def signal_platform_handoff_audit_summary_from_index(
    path: str | PathLike[str],
    *,
    consumer: str | None = None,
    required_consumers: Iterable[str] = (),
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    expected_source_transform: str | None = None,
    as_of: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    require_all_known_families: bool = False,
    require_all_known_consumers: bool = False,
    require_runtime_consumer_coverage: bool = False,
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, Any]:
    """Resolve a handoff index entry and validate the linked handoff manifest."""

    index_path = Path(path)
    index = load_platform_signal_handoff_index(index_path)
    handoff_path = resolve_platform_signal_handoff_manifest_from_index(
        index_path,
        consumer=consumer,
        required_consumers=required_consumers,
        expected_canonical_input=expected_canonical_input,
        as_of=as_of,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )
    summary = signal_platform_handoff_audit_summary_from_manifest(
        handoff_path,
        consumer=consumer,
        required_consumers=required_consumers,
        expected_source_transform=expected_source_transform,
        require_all_known_families=require_all_known_families,
        require_all_known_consumers=require_all_known_consumers,
        require_runtime_consumer_coverage=require_runtime_consumer_coverage,
        now=now,
        reference_time=reference_time,
    )
    summary.update(
        {
            "index_path": str(index_path.resolve()),
            "index_schema_version": str(index.get("schema_version", "")),
            "index_artifact_type": str(index.get("artifact_type", "")),
            "index_handoff_count": len(index.get("handoffs", ()) or ()),
            "handoff_manifest_path": str(handoff_path.resolve()),
            "handoff_manifest_sha256": _sha256_file(handoff_path),
        }
    )
    return summary


def extract_canonical_input_from_platform_handoff_index_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    as_of: str | None = None,
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate an index-selected handoff and return consumer market_data."""

    summary = signal_platform_handoff_audit_summary_from_index(
        path,
        consumer=consumer,
        as_of=as_of,
        now=now,
        reference_time=reference_time,
    )
    return extract_canonical_input_from_manifest_for_consumer(
        summary["signal_bundle_manifest_path"],
        consumer=consumer,
        now=now,
        reference_time=reference_time,
    )


def _validate_research_export_manifest_shape(manifest: Mapping[str, Any]) -> None:
    _validate_no_sensitive_fields(manifest, path="research_export_manifest")
    if manifest.get("schema_version") != RESEARCH_EXPORT_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported research export schema_version: "
            f"{manifest.get('schema_version')!r}"
        )
    for field in (
        "artifact_type",
        "transform",
        "source_version",
        "min_history",
        "row_count",
        "first_date",
        "last_date",
        "columns",
        "input_csv",
        "output_csv",
    ):
        if not _has_non_empty_value(manifest, field):
            raise SignalBundleContractError(
                f"research export manifest missing field: {field}"
            )
    if not _is_string_sequence(manifest["columns"]):
        raise SignalBundleContractError(
            "research export columns must be a sequence of strings"
        )
    for field in ("min_history", "row_count"):
        value = manifest[field]
        if not isinstance(value, int) or value < 0:
            raise SignalBundleContractError(
                f"research export {field} must be a non-negative integer"
            )
    for field in ("input_csv", "output_csv"):
        _validate_research_manifest_file_record_shape(manifest[field], field=field)
    quality_record = manifest.get("quality_report")
    if quality_record is not None:
        _validate_research_manifest_file_record_shape(
            quality_record,
            field="quality_report",
        )


def _validate_research_manifest_file_record_shape(
    record: object,
    *,
    field: str,
) -> None:
    if not isinstance(record, Mapping):
        raise SignalBundleContractError(f"research export {field} must be a mapping")
    for record_field in ("path", "sha256", "size_bytes"):
        if not _has_non_empty_value(record, record_field):
            raise SignalBundleContractError(
                f"research export {field} missing field: {record_field}"
            )
    size_bytes = record.get("size_bytes")
    if not isinstance(size_bytes, int) or size_bytes < 0:
        raise SignalBundleContractError(
            f"research export {field}.size_bytes must be a non-negative integer"
        )


def _resolve_research_manifest_file_path(
    manifest_path: Path,
    value: object,
    *,
    field: str,
) -> Path:
    raw_path = Path(str(value))
    if raw_path.is_absolute():
        return raw_path
    manifest_relative = (manifest_path.parent / raw_path).resolve()
    if manifest_relative.exists():
        return manifest_relative
    cwd_relative = raw_path.resolve()
    if cwd_relative.exists():
        return cwd_relative
    raise SignalBundleContractError(
        f"research export {field} does not exist relative to manifest or cwd: {value}"
    )


def _validate_research_manifest_file_record(
    record: Mapping[str, Any],
    path: Path,
    *,
    field: str,
) -> None:
    expected_sha256 = str(record["sha256"]).strip().lower()
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise SignalBundleContractError(
            f"research export {field}.sha256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    expected_size_bytes = int(record["size_bytes"])
    actual_size_bytes = path.stat().st_size
    if actual_size_bytes != expected_size_bytes:
        raise SignalBundleContractError(
            f"research export {field}.size_bytes mismatch: "
            f"expected {expected_size_bytes}, got {actual_size_bytes}"
        )


def _validate_research_handoff_shape(handoff: Mapping[str, Any]) -> None:
    _validate_no_sensitive_fields(handoff, path="research_signal_handoff")
    if handoff.get("schema_version") != MARKET_SIGNAL_RESEARCH_HANDOFF_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported research signal handoff schema_version: "
            f"{handoff.get('schema_version')!r}"
        )
    if handoff.get("artifact_type") != "market_signal_research_handoff":
        raise SignalBundleContractError(
            "research signal handoff artifact_type mismatch: "
            f"{handoff.get('artifact_type')!r}"
        )
    for field in (
        "consumer",
        "research_export_manifest_path",
        "research_export_manifest_sha256",
        "research_artifact_type",
        "research_transform",
        "research_as_of",
        "research_output_csv_sha256",
        "research_quality_report_sha256",
        "source_family_catalog_manifest_path",
        "source_family_catalog_manifest_sha256",
        "source_family_count",
        "source_families",
        "all_known_source_families_present",
        "all_consumer_contracts_satisfied",
        "consumer_contract_registry_manifest_path",
        "consumer_contract_registry_manifest_sha256",
        "consumer_contract_count",
        "consumer_contracts",
        "all_known_consumers_present",
    ):
        if field not in handoff:
            raise SignalBundleContractError(
                f"research signal handoff missing field: {field}"
            )
    for field in (
        "research_export_manifest_path",
        "research_export_manifest_sha256",
        "research_artifact_type",
        "research_transform",
        "research_output_csv_sha256",
        "source_family_catalog_manifest_path",
        "source_family_catalog_manifest_sha256",
        "consumer_contract_registry_manifest_path",
        "consumer_contract_registry_manifest_sha256",
    ):
        if not _has_non_empty_value(handoff, field):
            raise SignalBundleContractError(
                f"research signal handoff missing field: {field}"
            )
    if not _is_string_sequence(handoff["source_families"]):
        raise SignalBundleContractError(
            "research signal handoff source_families must be strings"
        )
    if "matched_source_families" in handoff and not _is_string_sequence(
        handoff["matched_source_families"]
    ):
        raise SignalBundleContractError(
            "research signal handoff matched_source_families must be strings"
        )
    if not _is_string_sequence(handoff["consumer_contracts"]):
        raise SignalBundleContractError(
            "research signal handoff consumer_contracts must be strings"
        )
    for field in (
        "all_known_source_families_present",
        "all_consumer_contracts_satisfied",
        "all_known_consumers_present",
        "all_runtime_consumers_covered",
    ):
        if field in handoff and not isinstance(handoff[field], bool):
            raise SignalBundleContractError(
                f"research signal handoff {field} must be a bool"
            )
    _validate_optional_count_matches_sequence(
        handoff,
        count_field="matched_source_family_count",
        sequence_field="matched_source_families",
        owner="research signal handoff",
    )
    _validate_optional_registry_verification_fields(
        handoff,
        owner="research signal handoff",
    )


def _research_handoff_summary(
    *,
    handoff_path: Path,
    handoff: Mapping[str, Any],
    research_export_manifest_path: Path,
    research_summary: Mapping[str, Any],
    source_catalog_manifest_path: Path,
    source_catalog_summary: Mapping[str, Any],
    consumer_registry_manifest_path: Path,
    consumer_registry_summary: Mapping[str, Any],
    consumer: str,
) -> dict[str, Any]:
    return {
        "path": str(handoff_path.resolve()),
        "schema_version": str(handoff["schema_version"]),
        "artifact_type": str(handoff["artifact_type"]),
        "sha256": _sha256_file(handoff_path),
        "size_bytes": handoff_path.stat().st_size,
        "consumer": consumer,
        "research_export_manifest_path": str(research_export_manifest_path.resolve()),
        "research_export_manifest_sha256": _sha256_file(
            research_export_manifest_path
        ),
        "research_artifact_type": research_summary["artifact_type"],
        "research_transform": research_summary["transform"],
        "research_as_of": research_summary["as_of"],
        "research_output_csv_sha256": research_summary["output_csv_sha256"],
        "research_quality_report_sha256": str(
            research_summary.get("quality_report_sha256", "")
        ),
        "source_family_catalog_manifest_path": str(
            source_catalog_manifest_path.resolve()
        ),
        "source_family_catalog_manifest_sha256": _sha256_file(
            source_catalog_manifest_path
        ),
        "source_family_count": len(source_catalog_summary["matched_families"]),
        "source_families": source_catalog_summary["matched_families"],
        "matched_source_family_count": len(source_catalog_summary["matched_families"]),
        "matched_source_families": source_catalog_summary["matched_families"],
        "all_known_source_families_present": source_catalog_summary[
            "all_known_families_present"
        ],
        "all_consumer_contracts_satisfied": source_catalog_summary[
            "all_consumer_contracts_satisfied"
        ],
        "all_runtime_consumers_covered": source_catalog_summary.get(
            "all_runtime_consumers_covered"
        ),
        "consumer_contract_registry_manifest_path": str(
            consumer_registry_manifest_path.resolve()
        ),
        "consumer_contract_registry_manifest_sha256": _sha256_file(
            consumer_registry_manifest_path
        ),
        "consumer_contract_count": consumer_registry_summary["consumer_count"],
        "consumer_contracts": consumer_registry_summary["consumers"],
        "all_known_consumers_present": consumer_registry_summary[
            "all_known_consumers_present"
        ],
        "canonical_registry_payload_sha256": consumer_registry_summary[
            "canonical_registry_payload_sha256"
        ],
        "local_registry_payload_sha256": consumer_registry_summary[
            "local_registry_payload_sha256"
        ],
        "local_contract_registry_verified": consumer_registry_summary[
            "local_contract_registry_verified"
        ],
        "research_export_output_csv_verified": True,
        "consumer_registry_contract_fields_verified": True,
        "handoff_linked_manifest_sha256s_verified": True,
    }


def _validate_research_handoff_consistency(
    handoff: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    handoff_consumer = str(handoff.get("consumer", "")).strip()
    if handoff_consumer and handoff_consumer != summary["consumer"]:
        raise SignalBundleContractError(
            "research signal handoff consumer mismatch: "
            f"{handoff_consumer!r} != {summary['consumer']!r}"
        )
    expected_values = {
        "research_artifact_type": summary["research_artifact_type"],
        "research_transform": summary["research_transform"],
        "research_as_of": summary["research_as_of"],
        "research_output_csv_sha256": summary["research_output_csv_sha256"],
        "research_quality_report_sha256": summary[
            "research_quality_report_sha256"
        ],
        "source_family_count": summary["source_family_count"],
        "source_families": summary["source_families"],
        "all_known_source_families_present": summary[
            "all_known_source_families_present"
        ],
        "all_consumer_contracts_satisfied": summary[
            "all_consumer_contracts_satisfied"
        ],
        "consumer_contract_count": summary["consumer_contract_count"],
        "consumer_contracts": summary["consumer_contracts"],
        "all_known_consumers_present": summary["all_known_consumers_present"],
    }
    for field, expected in expected_values.items():
        actual = handoff[field]
        if field in {"source_families", "consumer_contracts"}:
            actual = tuple(actual)
            expected = tuple(expected)
        if actual != expected:
            raise SignalBundleContractError(
                f"research signal handoff {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )
    optional_expected_values = {
        "matched_source_family_count": summary["matched_source_family_count"],
        "matched_source_families": summary["matched_source_families"],
        "all_runtime_consumers_covered": summary["all_runtime_consumers_covered"],
        "canonical_registry_payload_sha256": summary[
            "canonical_registry_payload_sha256"
        ],
        "local_registry_payload_sha256": summary["local_registry_payload_sha256"],
        "local_contract_registry_verified": summary["local_contract_registry_verified"],
    }
    for field, expected in optional_expected_values.items():
        if field not in handoff:
            continue
        actual = handoff[field]
        if field == "matched_source_families":
            actual = tuple(actual)
            expected = tuple(expected)
        if actual != expected:
            raise SignalBundleContractError(
                f"research signal handoff {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )


def _load_platform_signal_handoff_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        handoff = json.load(file_obj)
    if not isinstance(handoff, Mapping):
        raise SignalBundleContractError(
            "platform signal handoff manifest JSON root must be a mapping"
        )
    handoff_dict = dict(handoff)
    _validate_no_sensitive_fields(handoff_dict, path="platform_signal_handoff")
    if handoff_dict.get("schema_version") != MARKET_SIGNAL_PLATFORM_HANDOFF_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported platform signal handoff schema_version: "
            f"{handoff_dict.get('schema_version')!r}"
        )
    if handoff_dict.get("artifact_type") != "market_signal_platform_handoff":
        raise SignalBundleContractError(
            "platform signal handoff artifact_type mismatch: "
            f"{handoff_dict.get('artifact_type')!r}"
        )
    for field in (
        "consumer",
        "canonical_input",
        "bundle_id",
        "as_of",
        "freshness_status",
        "signal_bundle_manifest_path",
        "signal_bundle_manifest_sha256",
        "source_family_catalog_manifest_path",
        "source_family_catalog_manifest_sha256",
        "consumer_contract_registry_manifest_path",
        "consumer_contract_registry_manifest_sha256",
        "source_family_count",
        "source_families",
        "all_known_source_families_present",
        "all_consumer_contracts_satisfied",
        "consumer_contract_count",
        "consumer_contracts",
        "all_known_consumers_present",
    ):
        if field not in handoff_dict:
            raise SignalBundleContractError(
                f"platform signal handoff missing field: {field}"
            )
    if not _is_string_sequence(handoff_dict["source_families"]):
        raise SignalBundleContractError(
            "platform signal handoff source_families must be strings"
        )
    if "matched_source_families" in handoff_dict and not _is_string_sequence(
        handoff_dict["matched_source_families"]
    ):
        raise SignalBundleContractError(
            "platform signal handoff matched_source_families must be strings"
        )
    if not _is_string_sequence(handoff_dict["consumer_contracts"]):
        raise SignalBundleContractError(
            "platform signal handoff consumer_contracts must be strings"
        )
    for field in (
        "all_known_source_families_present",
        "all_consumer_contracts_satisfied",
        "all_known_consumers_present",
        "all_runtime_consumers_covered",
    ):
        if field in handoff_dict and not isinstance(handoff_dict[field], bool):
            raise SignalBundleContractError(
                f"platform signal handoff {field} must be a bool"
            )
    _validate_optional_count_matches_sequence(
        handoff_dict,
        count_field="matched_source_family_count",
        sequence_field="matched_source_families",
        owner="platform signal handoff",
    )
    _validate_optional_registry_verification_fields(
        handoff_dict,
        owner="platform signal handoff",
    )
    return handoff_dict


def _validate_handoff_linked_sha256(
    path: Path,
    expected: object,
    *,
    field: str,
) -> None:
    expected_sha256 = str(expected).strip().lower()
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise SignalBundleContractError(
            f"platform signal handoff {field} mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )


def _platform_handoff_summary(
    *,
    handoff_path: Path,
    handoff: Mapping[str, Any],
    signal_bundle_manifest_path: Path,
    bundle_summary: Mapping[str, Any],
    source_catalog_manifest_path: Path,
    source_catalog_summary: Mapping[str, Any],
    consumer_registry_manifest_path: Path,
    consumer_registry_summary: Mapping[str, Any],
    consumer: str,
    required_consumers: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "path": str(handoff_path.resolve()),
        "schema_version": str(handoff["schema_version"]),
        "artifact_type": str(handoff["artifact_type"]),
        "sha256": _sha256_file(handoff_path),
        "size_bytes": handoff_path.stat().st_size,
        "consumer": consumer,
        "required_signal_consumers": required_consumers,
        "canonical_input": bundle_summary["canonical_input"],
        "bundle_id": bundle_summary["bundle_id"],
        "as_of": bundle_summary["as_of"],
        "freshness_status": bundle_summary["freshness_status"],
        "signal_bundle_manifest_path": str(signal_bundle_manifest_path.resolve()),
        "signal_bundle_manifest_sha256": _sha256_file(signal_bundle_manifest_path),
        "source_family_catalog_manifest_path": str(
            source_catalog_manifest_path.resolve()
        ),
        "source_family_catalog_manifest_sha256": _sha256_file(
            source_catalog_manifest_path
        ),
        "consumer_contract_registry_manifest_path": str(
            consumer_registry_manifest_path.resolve()
        ),
        "consumer_contract_registry_manifest_sha256": _sha256_file(
            consumer_registry_manifest_path
        ),
        "source_family_count": source_catalog_summary["family_count"],
        "source_families": source_catalog_summary["families"],
        "matched_source_family_count": len(source_catalog_summary["matched_families"]),
        "matched_source_families": source_catalog_summary["matched_families"],
        "all_known_source_families_present": source_catalog_summary[
            "all_known_families_present"
        ],
        "all_consumer_contracts_satisfied": source_catalog_summary[
            "all_consumer_contracts_satisfied"
        ],
        "all_runtime_consumers_covered": source_catalog_summary.get(
            "all_runtime_consumers_covered"
        ),
        "consumer_contract_count": consumer_registry_summary["consumer_count"],
        "consumer_contracts": consumer_registry_summary["consumers"],
        "all_known_consumers_present": consumer_registry_summary[
            "all_known_consumers_present"
        ],
        "canonical_registry_payload_sha256": consumer_registry_summary[
            "canonical_registry_payload_sha256"
        ],
        "local_registry_payload_sha256": consumer_registry_summary[
            "local_registry_payload_sha256"
        ],
        "local_contract_registry_verified": consumer_registry_summary[
            "local_contract_registry_verified"
        ],
        "consumer_registry_contract_fields_verified": True,
        "handoff_linked_manifest_sha256s_verified": True,
    }


def _validate_platform_handoff_consistency(
    handoff: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    handoff_consumer = str(handoff.get("consumer", "")).strip()
    if handoff_consumer and handoff_consumer != summary["consumer"]:
        raise SignalBundleContractError(
            "platform signal handoff consumer mismatch: "
            f"{handoff_consumer!r} != {summary['consumer']!r}"
        )
    expected_values = {
        "canonical_input": summary["canonical_input"],
        "bundle_id": summary["bundle_id"],
        "as_of": summary["as_of"],
        "freshness_status": summary["freshness_status"],
        "source_family_count": summary["source_family_count"],
        "source_families": summary["source_families"],
        "all_known_source_families_present": summary[
            "all_known_source_families_present"
        ],
        "all_consumer_contracts_satisfied": summary[
            "all_consumer_contracts_satisfied"
        ],
        "consumer_contract_count": summary["consumer_contract_count"],
        "consumer_contracts": summary["consumer_contracts"],
        "all_known_consumers_present": summary["all_known_consumers_present"],
    }
    for field, expected in expected_values.items():
        actual = handoff[field]
        if field in {"source_families", "consumer_contracts"}:
            actual = tuple(actual)
            expected = tuple(expected)
        if actual != expected:
            raise SignalBundleContractError(
                f"platform signal handoff {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )
    optional_expected_values = {
        "matched_source_family_count": summary["matched_source_family_count"],
        "matched_source_families": summary["matched_source_families"],
        "all_runtime_consumers_covered": summary["all_runtime_consumers_covered"],
        "canonical_registry_payload_sha256": summary[
            "canonical_registry_payload_sha256"
        ],
        "local_registry_payload_sha256": summary["local_registry_payload_sha256"],
        "local_contract_registry_verified": summary["local_contract_registry_verified"],
    }
    for field, expected in optional_expected_values.items():
        if field not in handoff:
            continue
        actual = handoff[field]
        if field == "matched_source_families":
            actual = tuple(actual)
            expected = tuple(expected)
        if actual != expected:
            raise SignalBundleContractError(
                f"platform signal handoff {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )


def _validate_platform_handoff_index_entry(raw_entry: object) -> None:
    if not isinstance(raw_entry, Mapping):
        raise SignalBundleContractError(
            "platform signal handoff index entries must be mappings"
        )
    entry = dict(raw_entry)
    for field in (
        "handoff_manifest_path",
        "handoff_manifest_sha256",
        "consumer",
        "canonical_input",
        "bundle_id",
        "as_of",
        "freshness_status",
        "source_families",
        "consumer_contracts",
        "all_known_source_families_present",
        "all_consumer_contracts_satisfied",
        "all_known_consumers_present",
    ):
        if field not in entry:
            raise SignalBundleContractError(
                f"platform signal handoff index entry missing field: {field}"
            )
    for field in (
        "handoff_manifest_path",
        "handoff_manifest_sha256",
        "canonical_input",
        "bundle_id",
        "as_of",
        "freshness_status",
    ):
        if not _has_non_empty_value(entry, field):
            raise SignalBundleContractError(
                f"platform signal handoff index entry missing field: {field}"
            )
    if not _is_string_sequence(entry["source_families"]):
        raise SignalBundleContractError(
            "platform signal handoff index source_families must be strings"
        )
    if "matched_source_families" in entry and not _is_string_sequence(
        entry["matched_source_families"]
    ):
        raise SignalBundleContractError(
            "platform signal handoff index matched_source_families must be strings"
        )
    if not _is_string_sequence(entry["consumer_contracts"]):
        raise SignalBundleContractError(
            "platform signal handoff index consumer_contracts must be strings"
        )
    for field in (
        "all_known_source_families_present",
        "all_consumer_contracts_satisfied",
        "all_known_consumers_present",
        "all_runtime_consumers_covered",
    ):
        if field in entry and not isinstance(entry[field], bool):
            raise SignalBundleContractError(
                f"platform signal handoff index {field} must be a bool"
            )
    _validate_optional_count_matches_sequence(
        entry,
        count_field="matched_source_family_count",
        sequence_field="matched_source_families",
        owner="platform signal handoff index",
    )
    _validate_optional_registry_verification_fields(
        entry,
        owner="platform signal handoff index",
    )


def _required_handoff_index_consumers(
    *,
    consumer: str | None,
    required_consumers: Iterable[str],
) -> tuple[str, ...]:
    consumers: list[str] = []
    for raw_consumer in (consumer, *tuple(required_consumers)):
        normalized = str(raw_consumer or "").strip()
        if normalized and normalized not in consumers:
            consumers.append(normalized)
    return tuple(consumers)


def _handoff_index_entry_matches_consumers(
    entry: Mapping[str, Any],
    consumers: tuple[str, ...],
) -> bool:
    entry_consumer = str(entry.get("consumer", "")).strip()
    entry_consumers = {entry_consumer} if entry_consumer else set()
    entry_consumers.update(_string_tuple(entry.get("consumer_contracts")))
    return all(consumer in entry_consumers for consumer in consumers)


def _validate_handoff_index_manifest_consistency(
    entry: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> None:
    expected_values = {
        "consumer": str(handoff.get("consumer", "")).strip(),
        "canonical_input": str(handoff.get("canonical_input", "")).strip(),
        "bundle_id": str(handoff.get("bundle_id", "")).strip(),
        "as_of": str(handoff.get("as_of", "")).strip(),
        "freshness_status": str(handoff.get("freshness_status", "")).strip(),
        "source_families": tuple(handoff.get("source_families", ()) or ()),
        "consumer_contracts": tuple(handoff.get("consumer_contracts", ()) or ()),
        "all_known_source_families_present": handoff.get(
            "all_known_source_families_present"
        ),
        "all_consumer_contracts_satisfied": handoff.get(
            "all_consumer_contracts_satisfied"
        ),
        "all_known_consumers_present": handoff.get("all_known_consumers_present"),
    }
    for field, expected in expected_values.items():
        actual: object = entry.get(field)
        if field in {"source_families", "consumer_contracts"}:
            actual = tuple(actual or ())
        if field == "consumer":
            actual = str(actual or "").strip()
        if actual != expected:
            raise SignalBundleContractError(
                f"platform signal handoff index {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )
    optional_expected_values = {
        "matched_source_family_count": handoff.get("matched_source_family_count"),
        "matched_source_families": tuple(
            handoff.get("matched_source_families", ()) or ()
        ),
        "all_runtime_consumers_covered": handoff.get(
            "all_runtime_consumers_covered"
        ),
        "canonical_registry_payload_sha256": handoff.get(
            "canonical_registry_payload_sha256"
        ),
        "local_registry_payload_sha256": handoff.get(
            "local_registry_payload_sha256"
        ),
        "local_contract_registry_verified": handoff.get(
            "local_contract_registry_verified"
        ),
    }
    for field, expected in optional_expected_values.items():
        if field not in entry:
            continue
        actual = entry.get(field)
        if field == "matched_source_families":
            actual = tuple(actual or ())
        if actual != expected:
            raise SignalBundleContractError(
                f"platform signal handoff index {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )
