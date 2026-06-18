from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.signals import (
    SignalBundleContractError,
    extract_canonical_input,
    extract_canonical_input_for_consumer,
    extract_canonical_input_from_file,
    extract_canonical_input_from_file_for_consumer,
    extract_canonical_input_from_index,
    extract_canonical_input_from_index_for_consumer,
    extract_canonical_input_from_manifest,
    extract_canonical_input_from_manifest_for_consumer,
    load_signal_consumer_contract_registry,
    load_signal_bundle,
    load_signal_bundle_from_index,
    load_signal_bundle_from_manifest,
    load_signal_bundle_index,
    load_signal_bundle_manifest,
    resolve_signal_bundle_manifest_from_index,
    required_indicator_fields_for_consumer,
    signal_consumer_contract_registry_audit_summary,
    signal_consumer_contract_registry_audit_summary_from_file,
    signal_consumer_contract_registry_audit_summary_from_manifest,
    signal_bundle_consumer_audit_summary,
    signal_bundle_consumer_audit_summary_from_index,
    signal_bundle_consumer_audit_summary_from_manifest,
    signal_bundle_audit_summary,
    signal_bundle_audit_summary_from_index,
    signal_bundle_audit_summary_from_manifest,
    validate_signal_consumer_contract_registry,
    validate_signal_bundle,
    validate_signal_bundle_for_consumer,
    validate_signal_bundle_indicator_fields,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "signal_bundles"
    / "crypto"
    / "btc"
    / "derived_indicators"
    / "2026-06-19"
    / "signal_bundle.json"
)
FIXTURE_MANIFEST_PATH = FIXTURE_PATH.with_name("manifest.json")
FIXTURE_INDEX_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "signal_bundles"
    / "index.json"
)


def _load_bundle() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _consumer_contract_registry() -> dict[str, object]:
    return {
        "schema_version": "market_signal_consumer_contracts.v1",
        "canonical_input": "derived_indicators",
        "contracts": [
            {
                "consumer": "us_equity:ibit_smart_dca",
                "canonical_input": "derived_indicators",
                "required_indicator_fields_by_symbol": {
                    "BTC-USD": ["ahr999", "mayer_multiple"],
                },
            },
            {
                "consumer": "research:ibit_btc_ahr999_mayer_precomputed_variants",
                "canonical_input": "derived_indicators",
                "required_indicator_fields_by_symbol": {
                    "BTC-USD": ["ahr999", "ahr999_sma", "mayer_multiple"],
                },
            },
        ],
    }


def _complete_consumer_contract_registry() -> dict[str, object]:
    registry = _consumer_contract_registry()
    contracts = registry["contracts"]
    assert isinstance(contracts, list)
    contracts.insert(
        1,
        {
            "consumer": "research:ibit_btc_ahr999_mayer_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "BTC-USD": ["ahr999", "mayer_multiple"],
            },
        },
    )
    return registry


def _write_consumer_contract_registry_manifest(
    tmp_path: Path,
    registry: dict[str, object],
) -> tuple[Path, Path]:
    registry_path = tmp_path / "market_signal_consumers.json"
    registry_path.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    contracts = registry["contracts"]
    assert isinstance(contracts, list)
    consumers = [str(contract["consumer"]) for contract in contracts]
    missing_consumers = sorted(
        {
            "us_equity:ibit_smart_dca",
            "research:ibit_btc_ahr999_mayer_precomputed",
            "research:ibit_btc_ahr999_mayer_precomputed_variants",
        }
        - set(consumers)
    )
    manifest_path = tmp_path / "market_signal_consumers.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contract_manifest.v1",
                "artifact_type": "market_signal_consumer_contract_registry",
                "registry_path": "market_signal_consumers.json",
                "registry_sha256": hashlib.sha256(
                    registry_path.read_bytes()
                ).hexdigest(),
                "registry_size_bytes": registry_path.stat().st_size,
                "registry_schema_version": registry["schema_version"],
                "canonical_input": registry["canonical_input"],
                "consumer_count": len(contracts),
                "known_consumer_count": 3,
                "missing_known_consumers": missing_consumers,
                "all_known_consumers_present": not missing_consumers,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, manifest_path


def _write_signal_bundle_manifest_with_quality_report(
    tmp_path: Path,
    *,
    quality_status: str = "pass",
    failure_reasons: tuple[str, ...] = (),
) -> tuple[Path, Path, Path]:
    bundle_path = tmp_path / "signal_bundle.json"
    bundle_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    quality_report = {
        "schema_version": "market_signal_quality_report.v1",
        "artifact_type": "local_ohlcv_quality_report",
        "quality_status": quality_status,
        "failure_reasons": failure_reasons,
        "warning_reasons": (),
        "raw_row_count": 260,
        "normalized_row_count": 260,
        "first_date": "2025-01-01",
        "last_date": "2025-09-17",
    }
    quality_report_path = tmp_path / "quality_report.json"
    quality_report_path.write_text(
        json.dumps(quality_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["bundle_path"] = "signal_bundle.json"
    manifest["bundle_sha256"] = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    manifest["quality_report_path"] = "quality_report.json"
    manifest["quality_report_sha256"] = hashlib.sha256(
        quality_report_path.read_bytes()
    ).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return bundle_path, manifest_path, quality_report_path


def test_fresh_signal_bundle_is_accepted() -> None:
    validate_signal_bundle(_load_bundle())
    assert load_signal_bundle(FIXTURE_PATH)["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert load_signal_bundle_manifest(FIXTURE_MANIFEST_PATH)["bundle_path"] == "signal_bundle.json"


def test_manifest_quality_report_reference_is_validated(tmp_path) -> None:
    _, manifest_path, quality_report_path = _write_signal_bundle_manifest_with_quality_report(
        tmp_path,
    )

    bundle = load_signal_bundle_from_manifest(manifest_path)
    summary = signal_bundle_audit_summary_from_manifest(manifest_path)

    assert bundle["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["quality_report_path"] == str(quality_report_path.resolve())
    assert summary["quality_report_sha256"] == hashlib.sha256(
        quality_report_path.read_bytes()
    ).hexdigest()
    assert summary["quality_status"] == "pass"
    assert summary["quality_failure_reasons"] == ()
    assert summary["quality_normalized_row_count"] == 260
    assert summary["quality_first_date"] == "2025-01-01"
    assert summary["quality_last_date"] == "2025-09-17"

    quality_report = json.loads(quality_report_path.read_text(encoding="utf-8"))
    quality_report["quality_status"] = "warn"
    quality_report_path.write_text(json.dumps(quality_report), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="quality_report_sha256"):
        load_signal_bundle_from_manifest(manifest_path)


def test_manifest_quality_report_fail_status_is_rejected(tmp_path) -> None:
    _, manifest_path, _ = _write_signal_bundle_manifest_with_quality_report(
        tmp_path,
        quality_status="fail",
        failure_reasons=("insufficient_history_rows",),
    )

    with pytest.raises(SignalBundleContractError, match="quality report status is fail"):
        load_signal_bundle_from_manifest(manifest_path)


@pytest.mark.parametrize("status", ["stale", "missing"])
def test_stale_or_missing_signal_bundle_is_rejected(status: str) -> None:
    bundle = _load_bundle()
    freshness = copy.deepcopy(bundle["freshness"])
    freshness["status"] = status
    bundle["freshness"] = freshness

    with pytest.raises(SignalBundleContractError, match="freshness.status"):
        validate_signal_bundle(bundle)


def test_canonical_input_mismatch_is_rejected() -> None:
    bundle = _load_bundle()
    consumer_contract = copy.deepcopy(bundle["consumer_contract"])
    consumer_contract["canonical_input"] = "market_history"
    bundle["consumer_contract"] = consumer_contract

    with pytest.raises(SignalBundleContractError, match="canonical_input mismatch"):
        extract_canonical_input(bundle)


def test_sensitive_bundle_fields_are_rejected() -> None:
    bundle = _load_bundle()
    provenance = copy.deepcopy(bundle["provenance"])
    provenance["signed_url"] = "https://example.invalid/private?token=abc"
    bundle["provenance"] = provenance

    with pytest.raises(SignalBundleContractError, match="sensitive field"):
        validate_signal_bundle(bundle)


def test_extracts_btc_usd_ahr999_payload_for_strategy_context_market_data() -> None:
    market_data = extract_canonical_input(_load_bundle())
    file_market_data = extract_canonical_input_from_file(FIXTURE_PATH)
    manifest_market_data = extract_canonical_input_from_manifest(FIXTURE_MANIFEST_PATH)
    index_market_data = extract_canonical_input_from_index(FIXTURE_INDEX_PATH)

    assert set(market_data) == {"derived_indicators"}
    assert file_market_data == market_data
    assert manifest_market_data == market_data
    assert index_market_data == market_data
    payload = market_data["derived_indicators"]["BTC-USD"]
    assert payload["close"] == 64000.0
    assert payload["ahr999"] == 0.72
    assert payload["ahr999_sma"] == 0.75
    assert payload["mayer_multiple"] == 1.0847457627
    assert payload["provider_timestamp"] == "2026-06-19T00:00:00Z"


def test_extracts_market_data_after_consumer_contract_validation() -> None:
    bundle = _load_bundle()
    expected_market_data = extract_canonical_input(bundle)

    market_data = extract_canonical_input_for_consumer(
        bundle,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    file_market_data = extract_canonical_input_from_file_for_consumer(
        FIXTURE_PATH,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    manifest_market_data = extract_canonical_input_from_manifest_for_consumer(
        FIXTURE_MANIFEST_PATH,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    index_market_data = extract_canonical_input_from_index_for_consumer(
        FIXTURE_INDEX_PATH,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
        as_of="2026-06-20",
    )

    assert market_data == expected_market_data
    assert file_market_data == expected_market_data
    assert manifest_market_data == expected_market_data
    assert index_market_data == expected_market_data


def test_audit_summary_contains_non_sensitive_bundle_metadata() -> None:
    summary = signal_bundle_audit_summary(_load_bundle())
    manifest_summary = signal_bundle_audit_summary_from_manifest(FIXTURE_MANIFEST_PATH)
    index_summary = signal_bundle_audit_summary_from_index(FIXTURE_INDEX_PATH)
    summary_json = json.dumps(summary, sort_keys=True)

    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["canonical_input"] == "derived_indicators"
    assert summary["freshness_status"] == "fresh"
    assert summary["provider_timestamp"] == "2026-06-19T00:00:00Z"
    assert summary["transform"] == "crypto.btc.ahr999.v1"
    assert summary["indicator_fields_by_symbol"]["BTC-USD"] == (
        "ahr999",
        "ahr999_estimate_price",
        "ahr999_sma",
        "close",
        "drawdown_252d",
        "high252",
        "mayer_multiple",
        "provider_timestamp",
        "rsi14",
        "sma200",
        "sma200_gap",
    )
    assert summary["indicator_field_count_by_symbol"] == {"BTC-USD": 11}
    assert "64000.0" not in summary_json
    assert "78000.0" not in summary_json
    assert manifest_summary["bundle_sha256"] == (
        "3da3996095f134151019c38cb1bee9acc111978aa93dd5a613e1960385d41500"
    )
    assert index_summary["index_schema_version"] == "market_signal_index.v1"
    assert index_summary["index_bundle_count"] == 1
    assert not any("token" in key.lower() or "secret" in key.lower() for key in manifest_summary)


def test_signal_bundle_validates_consumer_required_indicator_fields() -> None:
    bundle = _load_bundle()

    validate_signal_bundle_for_consumer(
        bundle,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    validate_signal_bundle_indicator_fields(
        bundle,
        required_fields_by_symbol={"btc-usd": ("AHR999", "AHR999_SMA")},
    )
    summary = signal_bundle_consumer_audit_summary(
        bundle,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    manifest_summary = signal_bundle_consumer_audit_summary_from_manifest(
        FIXTURE_MANIFEST_PATH,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    index_summary = signal_bundle_consumer_audit_summary_from_index(
        FIXTURE_INDEX_PATH,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
        as_of="2026-06-20",
    )

    assert required_indicator_fields_for_consumer(
        "research:ibit_btc_ahr999_mayer_precomputed_variants"
    ) == {"BTC-USD": ("ahr999", "ahr999_sma", "mayer_multiple")}
    assert summary["consumer"] == "research:ibit_btc_ahr999_mayer_precomputed_variants"
    assert summary["required_indicator_fields_by_symbol"] == {
        "BTC-USD": ("ahr999", "ahr999_sma", "mayer_multiple")
    }
    assert manifest_summary["bundle_sha256"] == (
        "3da3996095f134151019c38cb1bee9acc111978aa93dd5a613e1960385d41500"
    )
    assert index_summary["manifest_path"] == str(FIXTURE_MANIFEST_PATH.resolve())


def test_signal_bundle_rejects_missing_consumer_required_indicator_fields() -> None:
    bundle = _load_bundle()
    payload = copy.deepcopy(bundle["derived_indicators"]["BTC-USD"])
    payload.pop("ahr999_sma")
    bundle["derived_indicators"]["BTC-USD"] = payload

    with pytest.raises(SignalBundleContractError, match="ahr999_sma"):
        validate_signal_bundle_for_consumer(
            bundle,
            consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
        )
    with pytest.raises(SignalBundleContractError, match="ahr999_sma"):
        extract_canonical_input_for_consumer(
            bundle,
            consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
        )


def test_signal_bundle_rejects_unknown_consumer_contract() -> None:
    with pytest.raises(SignalBundleContractError, match="unknown signal bundle consumer"):
        required_indicator_fields_for_consumer("unknown:consumer")


def test_external_consumer_contract_registry_matches_local_contracts(tmp_path) -> None:
    registry = _consumer_contract_registry()
    registry_path = tmp_path / "market_signal_consumers.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    validate_signal_consumer_contract_registry(registry)
    loaded = load_signal_consumer_contract_registry(registry_path)
    summary = signal_consumer_contract_registry_audit_summary(registry)
    file_summary = signal_consumer_contract_registry_audit_summary_from_file(registry_path)

    assert loaded == registry
    assert summary["schema_version"] == "market_signal_consumer_contracts.v1"
    assert summary["consumer_count"] == 2
    assert summary["consumers"] == (
        "us_equity:ibit_smart_dca",
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    assert summary["all_known_consumers_present"] is False
    assert summary["missing_known_consumers"] == (
        "research:ibit_btc_ahr999_mayer_precomputed",
    )
    assert file_summary["sha256"] == hashlib.sha256(
        registry_path.read_bytes()
    ).hexdigest()
    assert file_summary["size_bytes"] == registry_path.stat().st_size


def test_external_consumer_contract_registry_manifest_matches_local_contracts(
    tmp_path,
) -> None:
    registry_path, manifest_path = _write_consumer_contract_registry_manifest(
        tmp_path,
        _complete_consumer_contract_registry(),
    )

    summary = signal_consumer_contract_registry_audit_summary_from_manifest(
        manifest_path,
        require_all_known_consumers=True,
    )

    assert summary["manifest_path"] == str(manifest_path.resolve())
    assert summary["manifest_schema_version"] == (
        "market_signal_consumer_contract_manifest.v1"
    )
    assert summary["manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert summary["registry_path"] == str(registry_path.resolve())
    assert summary["registry_sha256"] == hashlib.sha256(
        registry_path.read_bytes()
    ).hexdigest()
    assert summary["registry_schema_version"] == "market_signal_consumer_contracts.v1"
    assert summary["consumer_count"] == 3
    assert summary["all_known_consumers_present"] is True

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["registry_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="registry_sha256 mismatch"):
        signal_consumer_contract_registry_audit_summary_from_manifest(manifest_path)


def test_external_consumer_contract_registry_can_require_all_known_consumers() -> None:
    incomplete_registry = _consumer_contract_registry()
    complete_registry = _complete_consumer_contract_registry()

    with pytest.raises(SignalBundleContractError, match="missing known consumers"):
        validate_signal_consumer_contract_registry(
            incomplete_registry,
            require_all_known_consumers=True,
        )

    validate_signal_consumer_contract_registry(
        complete_registry,
        require_all_known_consumers=True,
    )
    summary = signal_consumer_contract_registry_audit_summary(
        complete_registry,
        require_all_known_consumers=True,
    )
    assert summary["all_known_consumers_present"] is True
    assert summary["missing_known_consumers"] == ()


def test_external_consumer_contract_registry_rejects_drift() -> None:
    registry = _consumer_contract_registry()
    contracts = registry["contracts"]
    assert isinstance(contracts, list)
    required_fields = contracts[1]["required_indicator_fields_by_symbol"]
    assert isinstance(required_fields, dict)
    required_fields["BTC-USD"] = ["ahr999", "mayer_multiple"]

    with pytest.raises(SignalBundleContractError, match="drift"):
        validate_signal_consumer_contract_registry(registry)


def test_index_resolves_latest_fresh_manifest_for_platform_loader() -> None:
    index = load_signal_bundle_index(FIXTURE_INDEX_PATH)
    manifest_path = resolve_signal_bundle_manifest_from_index(
        FIXTURE_INDEX_PATH,
        as_of="2026-06-20",
    )
    bundle = load_signal_bundle_from_index(FIXTURE_INDEX_PATH)

    assert index["schema_version"] == "market_signal_index.v1"
    assert index["bundles"][0]["manifest_sha256"] == (
        "2b85d0852f369986285022796427371809b58b16dcb3c79846267fcabb388e05"
    )
    assert manifest_path == FIXTURE_MANIFEST_PATH.resolve()
    assert bundle["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"


def test_manifest_checksum_mismatch_is_rejected(tmp_path) -> None:
    bundle_path = tmp_path / "signal_bundle.json"
    manifest_path = tmp_path / "manifest.json"
    bundle_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="sha256 mismatch"):
        load_signal_bundle_from_manifest(manifest_path)


def test_manifest_bundle_path_escape_is_rejected(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["bundle_path"] = "../signal_bundle.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="escapes artifact directory"):
        load_signal_bundle_from_manifest(manifest_path)


def test_index_manifest_path_escape_is_rejected(tmp_path) -> None:
    index_path = tmp_path / "index.json"
    index = json.loads(FIXTURE_INDEX_PATH.read_text(encoding="utf-8"))
    index["bundles"][0]["manifest_path"] = "../manifest.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="manifest_path escapes"):
        resolve_signal_bundle_manifest_from_index(index_path)


def test_index_manifest_checksum_mismatch_is_rejected(tmp_path) -> None:
    index_path = tmp_path / "index.json"
    manifest_dir = tmp_path / "crypto" / "btc" / "derived_indicators" / "2026-06-19"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    index = json.loads(FIXTURE_INDEX_PATH.read_text(encoding="utf-8"))
    index["bundles"][0]["manifest_sha256"] = "0" * 64
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="manifest_sha256 mismatch"):
        resolve_signal_bundle_manifest_from_index(index_path)


def test_index_without_matching_entry_is_rejected() -> None:
    with pytest.raises(SignalBundleContractError, match="no matching manifest"):
        resolve_signal_bundle_manifest_from_index(
            FIXTURE_INDEX_PATH,
            as_of="2026-06-18",
        )


def test_manifest_freshness_mismatch_is_rejected(tmp_path) -> None:
    bundle_path = tmp_path / "signal_bundle.json"
    manifest_path = tmp_path / "manifest.json"
    bundle_text = FIXTURE_PATH.read_text(encoding="utf-8")
    bundle_path.write_text(bundle_text, encoding="utf-8")
    manifest = json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = hashlib.sha256(bundle_text.encode("utf-8")).hexdigest()
    manifest["freshness_status"] = "stale"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="freshness_status mismatch"):
        load_signal_bundle_from_manifest(manifest_path)
