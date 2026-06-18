from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import Any


MARKET_SIGNAL_BUNDLE_SCHEMA_VERSION = "market_signal_bundle.v1"
MARKET_SIGNAL_MANIFEST_SCHEMA_VERSION = "market_signal_manifest.v1"
CANONICAL_INPUT_DERIVED_INDICATORS = "derived_indicators"
FRESHNESS_FRESH = "fresh"

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


def load_signal_bundle_from_manifest(path: str | PathLike[str]) -> dict[str, Any]:
    """Load a local bundle through a manifest and verify file integrity."""

    manifest_path = Path(path)
    manifest = load_signal_bundle_manifest(manifest_path)
    bundle_path_value = str(manifest["bundle_path"])
    bundle_path = Path(bundle_path_value)
    if bundle_path.is_absolute():
        raise SignalBundleContractError("signal bundle manifest bundle_path must be relative")
    manifest_dir = manifest_path.parent.resolve()
    resolved_bundle_path = (manifest_dir / bundle_path).resolve()
    try:
        resolved_bundle_path.relative_to(manifest_dir)
    except ValueError as exc:
        raise SignalBundleContractError("signal bundle manifest bundle_path escapes artifact directory") from exc

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


def signal_bundle_audit_summary(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return non-sensitive audit fields for platform logs."""

    validate_signal_bundle(bundle)
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
