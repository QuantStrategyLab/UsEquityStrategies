from __future__ import annotations

from collections.abc import Iterable, Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from .constants import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    FRESHNESS_FRESH,
    MARKET_SIGNAL_CONSUMPTION_AUDIT_SCHEMA_VERSION,
)

from .errors import (
    SignalBundleContractError,
)

from .provenance import (
    _has_non_empty_value,
    _is_string_sequence,
    _load_json_mapping,
    _resolve_relative_artifact_path,
    _sha256_file,
    _validate_no_sensitive_fields,
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
    signal_bundle_consumer_audit_summary_from_manifest,
)


def load_signal_consumption_audit(path: str | PathLike[str]) -> dict[str, Any]:
    """Load and validate a MarketSignalSources consumption audit artifact shape."""

    audit_path = Path(path)
    audit = _load_json_mapping(audit_path, label="signal consumption audit")
    _validate_signal_consumption_audit_shape(audit)
    return audit


def signal_consumption_audit_summary_from_file(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    require_all_known_families: bool = False,
    require_all_known_consumers: bool = False,
    require_runtime_consumer_coverage: bool = False,
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, Any]:
    """Validate a saved runtime consumption audit and its linked bundle manifest."""

    audit_path = Path(path)
    audit = load_signal_consumption_audit(audit_path)
    target_consumer = str(consumer or "").strip()
    if not target_consumer:
        raise SignalBundleContractError("signal consumption audit consumer is required")
    if str(audit["consumer"]).strip() != target_consumer:
        raise SignalBundleContractError(
            "signal consumption audit consumer mismatch: "
            f"{audit['consumer']!r} != {target_consumer!r}"
        )
    canonical_input = str(audit["canonical_input"]).strip()
    if canonical_input != expected_canonical_input:
        raise SignalBundleContractError(
            "signal consumption audit canonical_input mismatch: "
            f"{canonical_input!r} != {expected_canonical_input!r}"
        )
    accepted = {str(item).strip().lower() for item in accepted_freshness_statuses}
    freshness_status = str(audit["freshness_status"]).strip().lower()
    if freshness_status not in accepted:
        raise SignalBundleContractError(
            "signal consumption audit freshness_status is not accepted: "
            f"{audit['freshness_status']!r}"
        )
    if (
        require_runtime_consumer_coverage
        and audit.get("all_runtime_consumers_covered") is not True
    ):
        raise SignalBundleContractError(
            "signal consumption audit runtime consumer coverage is incomplete"
        )
    signal_bundle_manifest_path = _resolve_consumption_audit_artifact_path(
        audit_path.parent.resolve(),
        audit["signal_bundle_manifest_path"],
        owner="signal consumption audit",
        field="signal_bundle_manifest_path",
    )
    _validate_consumption_audit_linked_sha256(
        signal_bundle_manifest_path,
        audit["signal_bundle_manifest_sha256"],
        field="signal_bundle_manifest_sha256",
    )
    bundle_summary = signal_bundle_consumer_audit_summary_from_manifest(
        signal_bundle_manifest_path,
        consumer=target_consumer,
        now=now,
        reference_time=reference_time,
    )
    source_catalog_manifest_path = _optional_consumption_audit_artifact_path(
        audit,
        audit_path.parent.resolve(),
        path_field="source_family_catalog_manifest_path",
        sha256_field="source_family_catalog_manifest_sha256",
        owner="signal consumption audit",
    )
    if source_catalog_manifest_path is not None:
        _validate_consumption_audit_linked_sha256(
            source_catalog_manifest_path,
            audit["source_family_catalog_manifest_sha256"],
            field="source_family_catalog_manifest_sha256",
        )
        source_catalog_summary = signal_source_family_catalog_audit_summary_from_manifest(
            source_catalog_manifest_path,
            required_consumers=(target_consumer,),
            expected_transform=str(bundle_summary["transform"]),
            require_all_known_families=require_all_known_families,
            require_runtime_consumer_coverage=require_runtime_consumer_coverage,
        )
        _validate_consumption_audit_source_catalog_identity(
            audit,
            source_catalog_summary=source_catalog_summary,
        )
    else:
        source_catalog_summary = None
    consumer_registry_manifest_path = _optional_consumption_audit_artifact_path(
        audit,
        audit_path.parent.resolve(),
        path_field="consumer_contract_registry_manifest_path",
        sha256_field="consumer_contract_registry_manifest_sha256",
        owner="signal consumption audit",
    )
    if consumer_registry_manifest_path is not None:
        _validate_consumption_audit_linked_sha256(
            consumer_registry_manifest_path,
            audit["consumer_contract_registry_manifest_sha256"],
            field="consumer_contract_registry_manifest_sha256",
        )
        consumer_registry_summary = (
            signal_consumer_contract_registry_audit_summary_from_manifest(
                consumer_registry_manifest_path,
                require_all_known_consumers=require_all_known_consumers,
            )
        )
        _validate_consumption_audit_consumer_registry_identity(
            audit,
            consumer_registry_summary=consumer_registry_summary,
        )
    else:
        consumer_registry_summary = None
    _validate_consumption_audit_bundle_identity(
        audit,
        bundle_summary=bundle_summary,
        signal_bundle_manifest_path=signal_bundle_manifest_path,
    )
    return {
        "path": str(audit_path.resolve()),
        "schema_version": str(audit["schema_version"]),
        "artifact_type": str(audit["artifact_type"]),
        "sha256": _sha256_file(audit_path),
        "size_bytes": audit_path.stat().st_size,
        "consumption_mode": str(audit["consumption_mode"]),
        "handoff_source": str(audit["handoff_source"]),
        "consumer": target_consumer,
        "canonical_input": canonical_input,
        "bundle_id": str(audit["bundle_id"]),
        "as_of": str(audit["as_of"]),
        "lookup_as_of": str(audit.get("lookup_as_of", "") or ""),
        "freshness_status": str(audit["freshness_status"]),
        "runtime_market_data_key": str(audit["runtime_market_data_key"]),
        "runtime_payload_field": str(audit["runtime_payload_field"]),
        "signal_bundle_manifest_path": str(signal_bundle_manifest_path.resolve()),
        "signal_bundle_manifest_sha256": _sha256_file(signal_bundle_manifest_path),
        "handoff_manifest_path": str(audit["handoff_manifest_path"]),
        "handoff_manifest_sha256": str(audit["handoff_manifest_sha256"]).lower(),
        "source_family_catalog_manifest_path": (
            str(source_catalog_manifest_path.resolve())
            if source_catalog_manifest_path is not None
            else str(audit.get("source_family_catalog_manifest_path", ""))
        ),
        "source_family_catalog_manifest_sha256": (
            _sha256_file(source_catalog_manifest_path)
            if source_catalog_manifest_path is not None
            else str(audit.get("source_family_catalog_manifest_sha256", "")).lower()
        ),
        "consumer_contract_registry_manifest_path": (
            str(consumer_registry_manifest_path.resolve())
            if consumer_registry_manifest_path is not None
            else str(audit.get("consumer_contract_registry_manifest_path", ""))
        ),
        "consumer_contract_registry_manifest_sha256": (
            _sha256_file(consumer_registry_manifest_path)
            if consumer_registry_manifest_path is not None
            else str(
                audit.get("consumer_contract_registry_manifest_sha256", "")
            ).lower()
        ),
        "source_family_count": (
            source_catalog_summary["family_count"]
            if source_catalog_summary is not None
            else audit.get("source_family_count")
        ),
        "source_families": (
            tuple(source_catalog_summary["families"])
            if source_catalog_summary is not None
            else tuple(str(item) for item in audit["source_families"])
        ),
        "matched_source_family_count": (
            source_catalog_summary["matched_family_count"]
            if source_catalog_summary is not None
            else audit.get("matched_source_family_count")
        ),
        "matched_source_families": (
            tuple(source_catalog_summary["matched_families"])
            if source_catalog_summary is not None
            else tuple(str(item) for item in audit["matched_source_families"])
        ),
        "all_known_source_families_present": audit.get(
            "all_known_source_families_present"
        )
        if source_catalog_summary is None
        else source_catalog_summary["all_known_families_present"],
        "all_consumer_contracts_satisfied": audit.get(
            "all_consumer_contracts_satisfied"
        )
        if source_catalog_summary is None
        else source_catalog_summary["all_consumer_contracts_satisfied"],
        "all_runtime_consumers_covered": audit.get("all_runtime_consumers_covered")
        if source_catalog_summary is None
        else source_catalog_summary["all_runtime_consumers_covered"],
        "consumer_contract_count": (
            consumer_registry_summary["consumer_count"]
            if consumer_registry_summary is not None
            else audit.get("consumer_contract_count")
        ),
        "consumer_contracts": (
            tuple(consumer_registry_summary["consumers"])
            if consumer_registry_summary is not None
            else tuple(str(item) for item in audit["consumer_contracts"])
        ),
        "all_known_consumers_present": (
            consumer_registry_summary["all_known_consumers_present"]
            if consumer_registry_summary is not None
            else audit.get("all_known_consumers_present")
        ),
        "canonical_registry_payload_sha256": (
            str(consumer_registry_summary["canonical_registry_payload_sha256"])
            if consumer_registry_summary is not None
            else str(audit.get("canonical_registry_payload_sha256", ""))
        ),
        "local_registry_payload_sha256": (
            str(consumer_registry_summary["local_registry_payload_sha256"])
            if consumer_registry_summary is not None
            else str(audit.get("local_registry_payload_sha256", ""))
        ),
        "local_contract_registry_verified": (
            consumer_registry_summary["local_contract_registry_verified"]
            if consumer_registry_summary is not None
            else audit.get("local_contract_registry_verified")
        ),
        "ready_for_runtime_injection": True,
        "runtime_injection_allowed": True,
        "linked_manifest_sha256s_verified": True,
        "bundle_identity_verified": True,
    }


def extract_canonical_input_from_consumption_audit_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate a saved consumption audit and return consumer market_data."""

    summary = signal_consumption_audit_summary_from_file(
        path,
        consumer=consumer,
        require_runtime_consumer_coverage=True,
        now=now,
        reference_time=reference_time,
    )
    return extract_canonical_input_from_manifest_for_consumer(
        summary["signal_bundle_manifest_path"],
        consumer=consumer,
        now=now,
        reference_time=reference_time,
    )


def _validate_signal_consumption_audit_shape(audit: Mapping[str, Any]) -> None:
    _validate_no_sensitive_fields(audit, path="signal_consumption_audit")
    if audit.get("schema_version") != MARKET_SIGNAL_CONSUMPTION_AUDIT_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported signal consumption audit schema_version: "
            f"{audit.get('schema_version')!r}"
        )
    if audit.get("artifact_type") != "market_signal_consumption_audit":
        raise SignalBundleContractError(
            "signal consumption audit artifact_type mismatch: "
            f"{audit.get('artifact_type')!r}"
        )
    required_fields = (
        "consumption_mode",
        "handoff_source",
        "consumer",
        "consumer_role",
        "canonical_input",
        "bundle_id",
        "as_of",
        "freshness_status",
        "runtime_market_data_key",
        "runtime_payload_field",
        "signal_bundle_manifest_path",
        "signal_bundle_manifest_sha256",
        "handoff_manifest_path",
        "handoff_manifest_sha256",
        "source_families",
        "matched_source_families",
        "consumer_contracts",
    )
    for field in required_fields:
        if not _has_non_empty_value(audit, field):
            raise SignalBundleContractError(
                f"signal consumption audit missing field: {field}"
            )
    if audit["consumption_mode"] != "runtime_platform":
        raise SignalBundleContractError(
            "signal consumption audit must be runtime_platform"
        )
    if audit["consumer_role"] != "runtime":
        raise SignalBundleContractError("signal consumption audit consumer_role mismatch")
    if audit["runtime_market_data_key"] != audit["canonical_input"]:
        raise SignalBundleContractError(
            "signal consumption audit runtime_market_data_key mismatch"
        )
    if audit["runtime_payload_field"] != audit["canonical_input"]:
        raise SignalBundleContractError(
            "signal consumption audit runtime_payload_field mismatch"
        )
    if audit.get("ready_for_consumption") is not True:
        raise SignalBundleContractError("signal consumption audit is not ready")
    if audit.get("ready_for_runtime_injection") is not True:
        raise SignalBundleContractError(
            "signal consumption audit is not runtime-injectable"
        )
    if audit.get("runtime_injection_allowed") is not True:
        raise SignalBundleContractError(
            "signal consumption audit does not allow runtime injection"
        )
    if audit.get("ready_for_research_consumption") is not False:
        raise SignalBundleContractError(
            "signal consumption audit is marked research-ready"
        )
    for field in (
        "linked_manifest_sha256s_verified",
        "consumer_contract_verified",
        "source_catalog_verified",
    ):
        if audit.get(field) is not True:
            raise SignalBundleContractError(
                f"signal consumption audit {field} is not true"
            )
    if "all_runtime_consumers_covered" in audit and not isinstance(
        audit["all_runtime_consumers_covered"],
        bool,
    ):
        raise SignalBundleContractError(
            "signal consumption audit all_runtime_consumers_covered must be a bool"
        )
    for field in (
        "all_known_source_families_present",
        "all_consumer_contracts_satisfied",
        "all_known_consumers_present",
    ):
        if field in audit and not isinstance(audit[field], bool):
            raise SignalBundleContractError(
                f"signal consumption audit {field} must be a bool"
            )
    for field, sequence_field in (
        ("source_family_count", "source_families"),
        ("matched_source_family_count", "matched_source_families"),
        ("consumer_contract_count", "consumer_contracts"),
    ):
        if field in audit:
            if not isinstance(audit[field], int) or audit[field] < 0:
                raise SignalBundleContractError(
                    f"signal consumption audit {field} must be a non-negative int"
                )
            if audit[field] != len(audit[sequence_field]):
                raise SignalBundleContractError(
                    f"signal consumption audit {field} does not match "
                    f"{sequence_field}"
                )
    for field in ("source_families", "matched_source_families", "consumer_contracts"):
        if not _is_string_sequence(audit[field]):
            raise SignalBundleContractError(
                f"signal consumption audit {field} must be strings"
            )
    if (
        "matched_source_family_count" in audit
        and audit["matched_source_family_count"] <= 0
    ):
        raise SignalBundleContractError(
            "signal consumption audit has no matched source family"
        )
    _validate_optional_registry_verification_fields(
        audit,
        owner="signal consumption audit",
    )


def _resolve_consumption_audit_artifact_path(
    root: Path,
    value: object,
    *,
    owner: str,
    field: str,
) -> Path:
    raw_path = Path(str(value))
    if raw_path.is_absolute():
        return raw_path
    return _resolve_relative_artifact_path(root, value, owner=owner, field=field)


def _optional_consumption_audit_artifact_path(
    audit: Mapping[str, Any],
    root: Path,
    *,
    path_field: str,
    sha256_field: str,
    owner: str,
) -> Path | None:
    has_path = _has_non_empty_value(audit, path_field)
    has_sha256 = _has_non_empty_value(audit, sha256_field)
    if has_path != has_sha256:
        raise SignalBundleContractError(
            f"{owner} must provide {path_field} and {sha256_field} together"
        )
    if not has_path:
        return None
    return _resolve_consumption_audit_artifact_path(
        root,
        audit[path_field],
        owner=owner,
        field=path_field,
    )


def _validate_consumption_audit_linked_sha256(
    path: Path,
    expected: object,
    *,
    field: str,
) -> None:
    expected_sha256 = str(expected).strip().lower()
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise SignalBundleContractError(
            f"signal consumption audit {field} mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )


def _validate_consumption_audit_bundle_identity(
    audit: Mapping[str, Any],
    *,
    bundle_summary: Mapping[str, Any],
    signal_bundle_manifest_path: Path,
) -> None:
    expected_values = {
        "canonical_input": bundle_summary["canonical_input"],
        "bundle_id": bundle_summary["bundle_id"],
        "as_of": bundle_summary["as_of"],
        "freshness_status": bundle_summary["freshness_status"],
        "signal_bundle_manifest_sha256": _sha256_file(signal_bundle_manifest_path),
    }
    for field, expected in expected_values.items():
        actual = str(audit.get(field, "")).strip()
        if actual != str(expected):
            raise SignalBundleContractError(
                f"signal consumption audit {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )


def _validate_consumption_audit_source_catalog_identity(
    audit: Mapping[str, Any],
    *,
    source_catalog_summary: Mapping[str, Any],
) -> None:
    expected_values = {
        "source_family_count": source_catalog_summary["family_count"],
        "source_families": tuple(source_catalog_summary["families"]),
        "matched_source_family_count": source_catalog_summary["matched_family_count"],
        "matched_source_families": tuple(source_catalog_summary["matched_families"]),
        "all_known_source_families_present": source_catalog_summary[
            "all_known_families_present"
        ],
        "all_consumer_contracts_satisfied": source_catalog_summary[
            "all_consumer_contracts_satisfied"
        ],
    }
    if source_catalog_summary.get("runtime_consumer_coverage_present"):
        expected_values["all_runtime_consumers_covered"] = source_catalog_summary[
            "all_runtime_consumers_covered"
        ]
    _validate_consumption_audit_expected_values(
        audit,
        expected_values=expected_values,
        tuple_fields={"source_families", "matched_source_families"},
    )


def _validate_consumption_audit_consumer_registry_identity(
    audit: Mapping[str, Any],
    *,
    consumer_registry_summary: Mapping[str, Any],
) -> None:
    expected_values = {
        "consumer_contract_count": consumer_registry_summary["consumer_count"],
        "consumer_contracts": tuple(consumer_registry_summary["consumers"]),
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
    }
    _validate_consumption_audit_expected_values(
        audit,
        expected_values=expected_values,
        tuple_fields={"consumer_contracts"},
    )


def _validate_consumption_audit_expected_values(
    audit: Mapping[str, Any],
    *,
    expected_values: Mapping[str, Any],
    tuple_fields: set[str],
) -> None:
    for field, expected in expected_values.items():
        if field not in audit:
            continue
        actual = audit[field]
        if field in tuple_fields:
            actual = tuple(actual or ())
            expected = tuple(expected or ())
        if actual != expected:
            raise SignalBundleContractError(
                f"signal consumption audit {field} mismatch: "
                f"{actual!r} != {expected!r}"
            )
