from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from .constants import (
    FRESHNESS_FRESH,
    _FORBIDDEN_SENSITIVE_KEY_FRAGMENTS,
    _REQUIRED_PROVENANCE_FIELDS,
)

from .errors import (
    SignalBundleContractError,
)


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, Mapping):
        raise SignalBundleContractError(f"{label} JSON root must be a mapping")
    return dict(payload)


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


def _validate_freshness(
    bundle: Mapping[str, Any],
    *,
    accepted_freshness_statuses: Iterable[str],
    now: str | None = None,
    reference_time: str | None = None,
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
    max_age_hours = freshness.get("max_age_hours")
    if not isinstance(max_age_hours, int) or isinstance(max_age_hours, bool) or max_age_hours < 0:
        raise SignalBundleContractError(
            "freshness.max_age_hours must be a non-negative integer"
        )
    provider_time = _parse_utc_timestamp(
        provider_timestamp,
        field="freshness.provider_timestamp",
    )
    evaluation_time = _resolve_evaluation_time(
        bundle,
        now=now,
        reference_time=reference_time,
    )
    if provider_time > evaluation_time:
        raise SignalBundleContractError(
            "freshness.provider_timestamp is in the future relative to evaluation time: "
            f"{provider_timestamp!r}"
        )
    if normalized_status == FRESHNESS_FRESH:
        age = evaluation_time - provider_time
        if age > timedelta(hours=max_age_hours):
            raise SignalBundleContractError(
                "freshness.status claims fresh but provider_timestamp exceeds "
                f"max_age_hours={max_age_hours}: age_hours={age.total_seconds() / 3600.0}"
            )


def _validate_quality_report_bundle_freshness_consistency(
    quality_report: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> None:
    freshness = bundle.get("freshness")
    if not isinstance(freshness, Mapping):
        raise SignalBundleContractError("freshness must be a mapping")
    provider_timestamp = str(freshness.get("provider_timestamp", "")).strip()
    quality_last_date = str(quality_report.get("last_date", "")).strip()
    provider_date = _provider_timestamp_date(provider_timestamp)
    if quality_last_date and provider_date and quality_last_date != provider_date:
        raise SignalBundleContractError(
            "quality report last_date mismatch with freshness.provider_timestamp: "
            f"{quality_last_date!r} != {provider_date!r}"
        )


def _provider_timestamp_date(provider_timestamp: str) -> str:
    if not str(provider_timestamp or "").strip():
        return ""
    return _parse_utc_timestamp(
        provider_timestamp,
        field="freshness.provider_timestamp",
    ).date().isoformat()


def _wall_clock_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_consumer_evaluation_clock(
    *,
    now: str | None,
    reference_time: str | None,
) -> tuple[str | None, str | None]:
    if now is None and reference_time is None:
        return _wall_clock_now_iso(), None
    return now, reference_time


def _resolve_evaluation_time(
    bundle: Mapping[str, Any],
    *,
    now: str | None,
    reference_time: str | None,
) -> datetime:
    if reference_time is not None:
        return _parse_utc_timestamp(reference_time, field="reference_time")
    if now is not None:
        return _parse_utc_timestamp(now, field="now")
    generated_at = bundle.get("generated_at")
    if isinstance(generated_at, str) and generated_at.strip():
        return _parse_utc_timestamp(generated_at, field="generated_at")
    return datetime.now(timezone.utc)


def _parse_utc_timestamp(value: str, *, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise SignalBundleContractError(f"{field} must be a non-empty timestamp")
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SignalBundleContractError(
            f"{field} must be an ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
