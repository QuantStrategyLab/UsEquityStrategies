from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from os import PathLike
from pathlib import Path
from typing import Any

from .constants import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    FRESHNESS_FRESH,
    MARKET_SIGNAL_INDEX_SCHEMA_VERSION,
    MARKET_SIGNAL_MANIFEST_SCHEMA_VERSION,
    QUALITY_REPORT_SCHEMA_VERSION,
)

from .errors import (
    SignalBundleContractError,
)

from .provenance import (
    _has_non_empty_value,
    _is_non_string_sequence,
    _is_string_sequence,
    _resolve_consumer_evaluation_clock,
    _resolve_relative_artifact_path,
    _sha256_file,
    _validate_no_sensitive_fields,
    _validate_quality_report_bundle_freshness_consistency,
)

from .consumer_contracts import (
    required_indicator_fields_for_consumer,
)

from .validation import (
    _canonical_input,
    _compatible_profiles,
    _index_compatible_profiles,
    validate_signal_bundle,
    validate_signal_bundle_for_consumer,
)


def extract_canonical_input(
    bundle: Mapping[str, Any],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return a StrategyContext.market_data-compatible canonical input dict."""

    validate_signal_bundle(
        bundle,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )
    return _canonical_market_data(bundle)


def extract_canonical_input_for_consumer(
    bundle: Mapping[str, Any],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate consumer field coverage and return StrategyContext.market_data input."""

    validate_signal_bundle_for_consumer(
        bundle,
        consumer=consumer,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
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
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a local bundle and return StrategyContext.market_data input."""

    return extract_canonical_input(
        load_signal_bundle(path),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )


def extract_canonical_input_from_file_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a local bundle, validate consumer fields, and return market_data input."""

    return extract_canonical_input_for_consumer(
        load_signal_bundle(path),
        consumer=consumer,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
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


def load_signal_bundle_from_manifest(
    path: str | PathLike[str],
    *,
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, Any]:
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
    validate_signal_bundle(
        bundle,
        now=now,
        reference_time=reference_time,
    )
    _validate_optional_quality_report_reference(
        manifest,
        manifest_root=manifest_dir,
        bundle=bundle,
    )
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
    now: str | None = None,
    reference_time: str | None = None,
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
    return load_signal_bundle_from_manifest(
        manifest_path,
        now=now,
        reference_time=reference_time,
    )


def extract_canonical_input_from_manifest(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a manifest-referenced bundle and return StrategyContext.market_data input."""

    return extract_canonical_input(
        load_signal_bundle_from_manifest(
            path,
            now=now,
            reference_time=reference_time,
        ),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )


def extract_canonical_input_from_manifest_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load a manifest bundle, validate consumer fields, and return market_data input."""

    now, reference_time = _resolve_consumer_evaluation_clock(
        now=now,
        reference_time=reference_time,
    )
    return extract_canonical_input_for_consumer(
        load_signal_bundle_from_manifest(
            path,
            now=now,
            reference_time=reference_time,
        ),
        consumer=consumer,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )


def extract_canonical_input_from_index(
    path: str | PathLike[str],
    *,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load an index-selected bundle and return StrategyContext.market_data input."""

    return extract_canonical_input(
        load_signal_bundle_from_index(
            path,
            expected_canonical_input=expected_canonical_input,
            as_of=as_of,
            bundle_id=bundle_id,
            accepted_freshness_statuses=accepted_freshness_statuses,
            now=now,
            reference_time=reference_time,
        ),
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
    )


def extract_canonical_input_from_index_for_consumer(
    path: str | PathLike[str],
    *,
    consumer: str,
    expected_canonical_input: str = CANONICAL_INPUT_DERIVED_INDICATORS,
    as_of: str | None = None,
    bundle_id: str | None = None,
    accepted_freshness_statuses: Iterable[str] = (FRESHNESS_FRESH,),
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load an index-selected bundle, validate consumer fields, and return market_data."""

    required_indicator_fields_for_consumer(consumer)
    now, reference_time = _resolve_consumer_evaluation_clock(
        now=now,
        reference_time=reference_time,
    )
    return extract_canonical_input_for_consumer(
        load_signal_bundle_from_index(
            path,
            expected_canonical_input=expected_canonical_input,
            as_of=as_of,
            bundle_id=bundle_id,
            compatible_profile=consumer,
            accepted_freshness_statuses=accepted_freshness_statuses,
            now=now,
            reference_time=reference_time,
        ),
        consumer=consumer,
        expected_canonical_input=expected_canonical_input,
        accepted_freshness_statuses=accepted_freshness_statuses,
        now=now,
        reference_time=reference_time,
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
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, Any]:
    """Return audit summary after validating consumer-specific field coverage."""

    validate_signal_bundle_for_consumer(
        bundle,
        consumer=consumer,
        now=now,
        reference_time=reference_time,
    )
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
    now: str | None = None,
    reference_time: str | None = None,
) -> dict[str, Any]:
    """Load a manifest-referenced bundle and validate consumer field coverage."""

    now, reference_time = _resolve_consumer_evaluation_clock(
        now=now,
        reference_time=reference_time,
    )
    bundle = load_signal_bundle_from_manifest(
        path,
        now=now,
        reference_time=reference_time,
    )
    summary = signal_bundle_consumer_audit_summary(
        bundle,
        consumer=consumer,
        now=now,
        reference_time=reference_time,
    )
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
    now: str | None = None,
    reference_time: str | None = None,
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
        now=now,
        reference_time=reference_time,
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
    bundle: Mapping[str, Any] | None = None,
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
    if bundle is not None:
        _validate_quality_report_bundle_freshness_consistency(quality_report, bundle)
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
