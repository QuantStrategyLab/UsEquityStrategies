from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import Any

from .signal_bundle.constants import (
    MARKET_SIGNAL_BUNDLE_SCHEMA_VERSION,
    MARKET_SIGNAL_MANIFEST_SCHEMA_VERSION,
    MARKET_SIGNAL_INDEX_SCHEMA_VERSION,
    MARKET_SIGNAL_CONSUMER_CONTRACTS_SCHEMA_VERSION,
    MARKET_SIGNAL_CONSUMER_CONTRACT_MANIFEST_SCHEMA_VERSION,
    MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_SCHEMA_VERSION,
    MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_MANIFEST_SCHEMA_VERSION,
    MARKET_SIGNAL_PLATFORM_HANDOFF_SCHEMA_VERSION,
    MARKET_SIGNAL_PLATFORM_HANDOFF_INDEX_SCHEMA_VERSION,
    MARKET_SIGNAL_RESEARCH_HANDOFF_SCHEMA_VERSION,
    MARKET_SIGNAL_CONSUMPTION_AUDIT_SCHEMA_VERSION,
    RESEARCH_EXPORT_SCHEMA_VERSION,
    QUALITY_REPORT_SCHEMA_VERSION,
    CANONICAL_INPUT_DERIVED_INDICATORS,
    FRESHNESS_FRESH,
    REQUIRED_INDICATOR_FIELDS_BY_CONSUMER,
    _REQUIRED_PROVENANCE_FIELDS,
    _FORBIDDEN_SENSITIVE_KEY_FRAGMENTS,
)

from .signal_bundle.errors import (
    SignalBundleContractError,
)

from .signal_bundle.provenance import (
    _load_json_mapping,
    _validate_optional_count_matches_sequence,
    _normalize_symbol,
    _sha256_file,
    _resolve_relative_artifact_path,
    _validate_freshness,
    _validate_quality_report_bundle_freshness_consistency,
    _provider_timestamp_date,
    _wall_clock_now_iso,
    _resolve_consumer_evaluation_clock,
    _resolve_evaluation_time,
    _parse_utc_timestamp,
    _validate_provenance,
    _validate_no_sensitive_fields,
    _has_non_empty_value,
    _is_non_string_sequence,
    _is_string_sequence,
    _string_tuple,
)

from .signal_bundle.consumer_contracts import (
    required_indicator_fields_for_consumer,
    known_signal_consumers,
    signal_consumer_contract_registry_payload,
    validate_signal_consumer_contract_registry,
    load_signal_consumer_contract_registry,
    signal_consumer_contract_registry_audit_summary,
    signal_consumer_contract_registry_audit_summary_from_file,
    signal_consumer_contract_registry_audit_summary_from_manifest,
    _load_signal_consumer_contract_registry_manifest,
    _validate_signal_consumer_contract_registry_manifest_consistency,
    _validate_optional_registry_verification_fields,
    _validate_signal_consumer_contract_record,
    _signal_consumer_contract_record,
    _canonical_registry_payload_sha256,
    _local_registry_payload_sha256,
)

from .signal_bundle.catalog import (
    signal_source_family_catalog_audit_summary_from_manifest,
    signal_source_family_catalog_audit_summary_from_file,
    _load_signal_source_family_catalog_manifest,
    _load_signal_source_family_catalog,
    _validate_signal_source_family_catalog_manifest_consistency,
    _validate_signal_source_family_record,
    _matching_source_catalog_families,
    _all_source_family_contracts_satisfied,
    _source_family_runtime_consumer_coverage,
    _source_family_covers_required_fields,
)

from .signal_bundle.validation import (
    validate_signal_bundle,
    validate_signal_bundle_indicator_fields,
    validate_signal_bundle_for_consumer,
    _canonical_input,
    _compatible_profiles,
    _index_compatible_profiles,
    _validate_consumer_profile_compatibility,
    _validate_derived_indicators,
)

from .signal_bundle.extraction import (
    extract_canonical_input,
    extract_canonical_input_for_consumer,
    _canonical_market_data,
    load_signal_bundle,
    extract_canonical_input_from_file,
    extract_canonical_input_from_file_for_consumer,
    load_signal_bundle_manifest,
    load_signal_bundle_index,
    load_signal_bundle_from_manifest,
    resolve_signal_bundle_manifest_from_index,
    load_signal_bundle_from_index,
    extract_canonical_input_from_manifest,
    extract_canonical_input_from_manifest_for_consumer,
    extract_canonical_input_from_index,
    extract_canonical_input_from_index_for_consumer,
    signal_bundle_audit_summary,
    signal_bundle_consumer_audit_summary,
    signal_bundle_audit_summary_from_manifest,
    signal_bundle_consumer_audit_summary_from_manifest,
    signal_bundle_audit_summary_from_index,
    signal_bundle_consumer_audit_summary_from_index,
    _indicator_fields_by_symbol,
    _validate_optional_quality_report_reference,
    _load_quality_report,
    _validate_quality_report,
    _validate_manifest_bundle_consistency,
    _validate_index_manifest_consistency,
)

from .signal_bundle.consumption import (
    load_signal_consumption_audit,
    signal_consumption_audit_summary_from_file,
    extract_canonical_input_from_consumption_audit_for_consumer,
    _validate_signal_consumption_audit_shape,
    _resolve_consumption_audit_artifact_path,
    _optional_consumption_audit_artifact_path,
    _validate_consumption_audit_linked_sha256,
    _validate_consumption_audit_bundle_identity,
    _validate_consumption_audit_source_catalog_identity,
    _validate_consumption_audit_consumer_registry_identity,
    _validate_consumption_audit_expected_values,
)

from .signal_bundle.manifests import (
    signal_platform_handoff_audit_summary_from_manifest,
    load_research_export_manifest,
    research_export_audit_summary_from_manifest,
    load_research_signal_handoff_manifest,
    signal_research_handoff_audit_summary_from_manifest,
    extract_canonical_input_from_platform_handoff_for_consumer,
    load_platform_signal_handoff_index,
    resolve_platform_signal_handoff_manifest_from_index,
    signal_platform_handoff_audit_summary_from_index,
    extract_canonical_input_from_platform_handoff_index_for_consumer,
    _validate_research_export_manifest_shape,
    _validate_research_manifest_file_record_shape,
    _resolve_research_manifest_file_path,
    _validate_research_manifest_file_record,
    _validate_research_handoff_shape,
    _research_handoff_summary,
    _validate_research_handoff_consistency,
    _load_platform_signal_handoff_manifest,
    _validate_handoff_linked_sha256,
    _platform_handoff_summary,
    _validate_platform_handoff_consistency,
    _validate_platform_handoff_index_entry,
    _required_handoff_index_consumers,
    _handoff_index_entry_matches_consumers,
    _validate_handoff_index_manifest_consistency,
)
