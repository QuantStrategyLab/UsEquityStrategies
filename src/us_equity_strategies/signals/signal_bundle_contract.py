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
MARKET_SIGNAL_CONSUMER_CONTRACT_MANIFEST_SCHEMA_VERSION = (
    "market_signal_consumer_contract_manifest.v1"
)
MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_SCHEMA_VERSION = "market_signal_source_families.v1"
MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_MANIFEST_SCHEMA_VERSION = (
    "market_signal_source_family_catalog_manifest.v1"
)
MARKET_SIGNAL_PLATFORM_HANDOFF_SCHEMA_VERSION = "market_signal_platform_handoff.v1"
MARKET_SIGNAL_PLATFORM_HANDOFF_INDEX_SCHEMA_VERSION = (
    "market_signal_platform_handoff_index.v1"
)
MARKET_SIGNAL_RESEARCH_HANDOFF_SCHEMA_VERSION = "market_signal_research_handoff.v1"
MARKET_SIGNAL_CONSUMPTION_AUDIT_SCHEMA_VERSION = (
    "market_signal_consumption_audit.v1"
)
RESEARCH_EXPORT_SCHEMA_VERSION = "research_export.v1"
QUALITY_REPORT_SCHEMA_VERSION = "market_signal_quality_report.v1"
CANONICAL_INPUT_DERIVED_INDICATORS = "derived_indicators"
FRESHNESS_FRESH = "fresh"

REQUIRED_INDICATOR_FIELDS_BY_CONSUMER: dict[str, dict[str, tuple[str, ...]]] = {
    "us_equity:ibit_smart_dca": {
        "BTC-USD": ("ahr999",),
    },
    "research:nasdaq_sp500_external_context_precomputed": {
        "US-EQUITY-CONTEXT": (
            "breadth_above_sma200_pct",
            "cape_percentile",
            "vix_percentile",
        ),
    },
    "research:nasdaq_sp500_cape_vix_external_context_precomputed": {
        "US-EQUITY-CONTEXT": (
            "cape_percentile",
            "vix_percentile",
        ),
    },
    "research:nasdaq_sp500_price_proxy": {
        "US-EQUITY-PRICE-PROXY": (
            "QQQ",
            "SPY",
        ),
    },
    "research:ibit_btc_ahr999_precomputed": {
        "BTC-USD": ("ahr999",),
    },
    "research:ibit_btc_ahr999_helper_precomputed_variants": {
        "BTC-USD": ("ahr999", "ahr999_365d_percentile", "ahr999_30d_slope"),
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
    return _canonical_market_data(bundle)


def extract_canonical_input_for_consumer(
    bundle: Mapping[str, Any],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate consumer field coverage and return StrategyContext.market_data input."""

    validate_signal_bundle_for_consumer(
        bundle,
        consumer=consumer,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )
    return _canonical_market_data(bundle)


def _canonical_market_data(
    bundle: Mapping[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
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


def signal_platform_handoff_audit_summary_from_manifest(
    path: str | PathLike[str],
    *,
    consumer: str | None = None,
    required_consumers: Iterable[str] = (),
    expected_source_transform: str | None = None,
    require_all_known_families: bool = False,
    require_all_known_consumers: bool = False,
    require_runtime_consumer_coverage: bool = False,
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
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate a platform handoff manifest and return consumer market_data."""

    summary = signal_platform_handoff_audit_summary_from_manifest(
        path,
        consumer=consumer,
    )
    return extract_canonical_input_from_manifest_for_consumer(
        summary["signal_bundle_manifest_path"],
        consumer=consumer,
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
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate an index-selected handoff and return consumer market_data."""

    summary = signal_platform_handoff_audit_summary_from_index(
        path,
        consumer=consumer,
        as_of=as_of,
    )
    return extract_canonical_input_from_manifest_for_consumer(
        summary["signal_bundle_manifest_path"],
        consumer=consumer,
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
    require_runtime_consumer_coverage: bool = False,
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
    bundle_summary = signal_bundle_consumer_audit_summary_from_manifest(
        signal_bundle_manifest_path,
        consumer=target_consumer,
    )
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
        "source_family_count": audit.get("source_family_count"),
        "source_families": tuple(str(item) for item in audit["source_families"]),
        "matched_source_family_count": audit.get("matched_source_family_count"),
        "matched_source_families": tuple(
            str(item) for item in audit["matched_source_families"]
        ),
        "all_known_source_families_present": audit.get(
            "all_known_source_families_present"
        ),
        "all_consumer_contracts_satisfied": audit.get(
            "all_consumer_contracts_satisfied"
        ),
        "consumer_contract_count": audit.get("consumer_contract_count"),
        "consumer_contracts": tuple(str(item) for item in audit["consumer_contracts"]),
        "all_known_consumers_present": audit.get("all_known_consumers_present"),
        "all_runtime_consumers_covered": audit.get("all_runtime_consumers_covered"),
        "canonical_registry_payload_sha256": str(
            audit.get("canonical_registry_payload_sha256", "")
        ),
        "local_registry_payload_sha256": str(
            audit.get("local_registry_payload_sha256", "")
        ),
        "local_contract_registry_verified": audit.get(
            "local_contract_registry_verified"
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
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate a saved consumption audit and return consumer market_data."""

    summary = signal_consumption_audit_summary_from_file(
        path,
        consumer=consumer,
        require_runtime_consumer_coverage=True,
    )
    return extract_canonical_input_from_manifest_for_consumer(
        summary["signal_bundle_manifest_path"],
        consumer=consumer,
    )


def validate_signal_bundle_indicator_fields(
    bundle: Mapping[str, Any],
    *,
    required_fields_by_symbol: Mapping[str, Iterable[str]],
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> None:
    """Validate that a bundle covers required derived indicator fields."""

    validate_signal_bundle(
        bundle,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
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


def validate_signal_bundle_for_consumer(
    bundle: Mapping[str, Any],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> None:
    """Validate a bundle against a known strategy or research consumer contract."""

    validate_signal_bundle_indicator_fields(
        bundle,
        required_fields_by_symbol=required_indicator_fields_for_consumer(consumer),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
    )
    _validate_consumer_profile_compatibility(bundle, consumer=consumer)


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


def extract_canonical_input_from_file_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a local bundle, validate consumer fields, and return market_data input."""

    return extract_canonical_input_for_consumer(
        load_signal_bundle(path),
        consumer=consumer,
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
    quality_path_present = _has_non_empty_value(manifest_dict, "quality_report_path")
    quality_sha_present = _has_non_empty_value(manifest_dict, "quality_report_sha256")
    if quality_path_present != quality_sha_present:
        raise SignalBundleContractError(
            "signal bundle manifest quality_report_path and "
            "quality_report_sha256 must be provided together"
        )
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
    _validate_optional_quality_report_reference(
        manifest,
        manifest_root=manifest_dir,
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
    compatible_profile: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> Path:
    """Resolve the latest matching manifest path from a local bundle index."""

    index_path = Path(path)
    index = load_signal_bundle_index(index_path)
    accepted = {str(item).strip().lower() for item in accepted_freshness_statuses}
    target_as_of = str(as_of).strip() if as_of is not None else None
    target_profile = str(compatible_profile).strip() if compatible_profile is not None else None
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
        if target_profile is not None and target_profile not in _index_compatible_profiles(
            entry
        ):
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
    compatible_profile: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, Any]:
    """Load a bundle through an index-selected manifest."""

    manifest_path = resolve_signal_bundle_manifest_from_index(
        path,
        expected_canonical_input=expected_canonical_input,
        as_of=as_of,
        bundle_id=bundle_id,
        compatible_profile=compatible_profile,
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


def extract_canonical_input_from_manifest_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a manifest bundle, validate consumer fields, and return market_data input."""

    return extract_canonical_input_for_consumer(
        load_signal_bundle_from_manifest(path),
        consumer=consumer,
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


def extract_canonical_input_from_index_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load an index-selected bundle, validate consumer fields, and return market_data."""

    required_indicator_fields_for_consumer(consumer)
    return extract_canonical_input_for_consumer(
        load_signal_bundle_from_index(
            path,
            expected_canonical_input=expected_canonical_input,
            as_of=as_of,
            bundle_id=bundle_id,
            compatible_profile=consumer,
            accepted_freshness_statuses=accepted_freshness_statuses,
        ),
        consumer=consumer,
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
        "compatible_profiles": _compatible_profiles(bundle),
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
            "compatible_profiles": _compatible_profiles(bundle),
            "consumer_profile_compatible": True,
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
    summary.update(
        _validate_optional_quality_report_reference(
            manifest,
            manifest_root=Path(path).parent.resolve(),
        )
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
    summary.update(
        _validate_optional_quality_report_reference(
            manifest,
            manifest_root=Path(path).parent.resolve(),
        )
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

    required_indicator_fields_for_consumer(consumer)
    manifest_path = resolve_signal_bundle_manifest_from_index(
        path,
        expected_canonical_input=expected_canonical_input,
        as_of=as_of,
        bundle_id=bundle_id,
        compatible_profile=consumer,
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


def _validate_optional_quality_report_reference(
    manifest: Mapping[str, Any],
    *,
    manifest_root: Path,
) -> dict[str, Any]:
    if not _has_non_empty_value(manifest, "quality_report_path"):
        return {}
    quality_path = _resolve_relative_artifact_path(
        manifest_root,
        manifest["quality_report_path"],
        owner="signal bundle manifest",
        field="quality_report_path",
    )
    expected_sha256 = str(manifest["quality_report_sha256"]).strip().lower()
    actual_sha256 = _sha256_file(quality_path)
    if actual_sha256 != expected_sha256:
        raise SignalBundleContractError(
            "signal bundle quality_report_sha256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    quality_report = _load_quality_report(quality_path)
    _validate_quality_report(quality_report)
    return {
        "quality_report_path": str(quality_path.resolve()),
        "quality_report_sha256": expected_sha256,
        "quality_status": str(quality_report["quality_status"]),
        "quality_failure_reasons": tuple(quality_report["failure_reasons"]),
        "quality_warning_reasons": tuple(quality_report["warning_reasons"]),
        "quality_raw_row_count": int(quality_report["raw_row_count"]),
        "quality_normalized_row_count": int(quality_report["normalized_row_count"]),
        "quality_first_date": str(quality_report["first_date"]),
        "quality_last_date": str(quality_report["last_date"]),
    }


def _load_quality_report(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        report = json.load(file_obj)
    if not isinstance(report, Mapping):
        raise SignalBundleContractError("quality report JSON root must be a mapping")
    return dict(report)


def _validate_quality_report(report: Mapping[str, Any]) -> None:
    _validate_no_sensitive_fields(report, path="quality_report")
    if report.get("schema_version") != QUALITY_REPORT_SCHEMA_VERSION:
        raise SignalBundleContractError(
            "unsupported quality report schema_version: "
            f"{report.get('schema_version')!r}"
        )
    if report.get("artifact_type") != "local_ohlcv_quality_report":
        raise SignalBundleContractError(
            "quality report artifact_type mismatch: "
            f"{report.get('artifact_type')!r}"
        )
    for field in (
        "quality_status",
        "failure_reasons",
        "warning_reasons",
        "raw_row_count",
        "normalized_row_count",
        "first_date",
        "last_date",
    ):
        if field not in report:
            raise SignalBundleContractError(f"quality report missing field: {field}")
    if not _has_non_empty_value(report, "quality_status"):
        raise SignalBundleContractError("quality report missing field: quality_status")
    if report["quality_status"] not in {"pass", "warn", "fail"}:
        raise SignalBundleContractError(
            f"unsupported quality_status: {report['quality_status']!r}"
        )
    if not _is_string_sequence(report["failure_reasons"]):
        raise SignalBundleContractError("quality report failure_reasons must be strings")
    if not _is_string_sequence(report["warning_reasons"]):
        raise SignalBundleContractError("quality report warning_reasons must be strings")
    for field in ("raw_row_count", "normalized_row_count"):
        value = report[field]
        if not isinstance(value, int) or value < 0:
            raise SignalBundleContractError(
                f"quality report {field} must be a non-negative integer"
            )
    if report["quality_status"] == "fail":
        raise SignalBundleContractError(
            "quality report status is fail: "
            + ",".join(str(reason) for reason in report["failure_reasons"])
        )


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


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, Mapping):
        raise SignalBundleContractError(f"{label} JSON root must be a mapping")
    return dict(payload)


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


def _validate_optional_count_matches_sequence(
    payload: Mapping[str, Any],
    *,
    count_field: str,
    sequence_field: str,
    owner: str,
) -> None:
    if count_field not in payload and sequence_field not in payload:
        return
    if count_field not in payload or sequence_field not in payload:
        raise SignalBundleContractError(
            f"{owner} must provide {count_field} and {sequence_field} together"
        )
    count = payload[count_field]
    sequence = payload[sequence_field]
    if not isinstance(count, int) or count < 0:
        raise SignalBundleContractError(
            f"{owner} {count_field} must be a non-negative int"
        )
    if not _is_non_string_sequence(sequence):
        raise SignalBundleContractError(f"{owner} {sequence_field} must be a sequence")
    if count != len(sequence):
        raise SignalBundleContractError(
            f"{owner} {count_field} does not match {sequence_field}"
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


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not _is_non_string_sequence(value):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())
