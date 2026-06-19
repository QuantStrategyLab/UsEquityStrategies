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
    extract_canonical_input_from_platform_handoff_index_for_consumer,
    extract_canonical_input_from_platform_handoff_for_consumer,
    load_research_export_manifest,
    load_research_signal_handoff_manifest,
    load_signal_consumer_contract_registry,
    load_signal_bundle,
    load_signal_bundle_from_index,
    load_signal_bundle_from_manifest,
    load_signal_bundle_index,
    load_signal_bundle_manifest,
    load_platform_signal_handoff_index,
    resolve_platform_signal_handoff_manifest_from_index,
    resolve_signal_bundle_manifest_from_index,
    research_export_audit_summary_from_manifest,
    required_indicator_fields_for_consumer,
    signal_consumer_contract_registry_audit_summary,
    signal_consumer_contract_registry_audit_summary_from_file,
    signal_consumer_contract_registry_audit_summary_from_manifest,
    signal_platform_handoff_audit_summary_from_index,
    signal_platform_handoff_audit_summary_from_manifest,
    signal_research_handoff_audit_summary_from_manifest,
    signal_source_family_catalog_audit_summary_from_manifest,
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


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_signal_bundle_candidate(
    root: Path,
    *,
    relative_dir: Path,
    as_of: str,
    compatible_profiles: list[str],
) -> tuple[Path, dict[str, object]]:
    candidate_dir = root / relative_dir
    candidate_dir.mkdir(parents=True)
    provider_timestamp = f"{as_of}T00:00:00Z"
    generated_at = f"{as_of}T00:15:00Z"

    bundle = copy.deepcopy(_load_bundle())
    bundle["bundle_id"] = f"crypto.btc.derived_indicators.{as_of}"
    bundle["as_of"] = as_of
    bundle["generated_at"] = generated_at
    consumer_contract = bundle["consumer_contract"]
    assert isinstance(consumer_contract, dict)
    consumer_contract["compatible_profiles"] = compatible_profiles
    freshness = bundle["freshness"]
    assert isinstance(freshness, dict)
    freshness["provider_timestamp"] = provider_timestamp
    derived_indicators = bundle["derived_indicators"]
    assert isinstance(derived_indicators, dict)
    payload = derived_indicators["BTC-USD"]
    assert isinstance(payload, dict)
    payload["provider_timestamp"] = provider_timestamp

    bundle_path = candidate_dir / "signal_bundle.json"
    bundle_path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest = json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest.update(
        {
            "bundle_id": bundle["bundle_id"],
            "as_of": as_of,
            "generated_at": generated_at,
            "provider_timestamp": provider_timestamp,
            "bundle_sha256": _sha256_path(bundle_path),
            "compatible_profiles": compatible_profiles,
        }
    )
    manifest_path = candidate_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path, bundle


def _signal_bundle_index_entry(root: Path, manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "manifest_sha256": _sha256_path(manifest_path),
        "bundle_id": manifest["bundle_id"],
        "as_of": manifest["as_of"],
        "canonical_input": manifest["canonical_input"],
        "compatible_profiles": manifest["compatible_profiles"],
        "freshness_status": manifest["freshness_status"],
        "bundle_schema_version": manifest["bundle_schema_version"],
    }


def _consumer_contract_registry() -> dict[str, object]:
    return {
        "schema_version": "market_signal_consumer_contracts.v1",
        "canonical_input": "derived_indicators",
        "contracts": [
            {
                "consumer": "us_equity:ibit_smart_dca",
                "canonical_input": "derived_indicators",
                "required_indicator_fields_by_symbol": {
                    "BTC-USD": ["ahr999"],
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
            "consumer": "research:nasdaq_sp500_external_context_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "US-EQUITY-CONTEXT": [
                    "breadth_above_sma200_pct",
                    "cape_percentile",
                    "vix_percentile",
                ],
            },
        },
    )
    contracts.insert(
        2,
        {
            "consumer": "research:nasdaq_sp500_cape_vix_external_context_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "US-EQUITY-CONTEXT": [
                    "cape_percentile",
                    "vix_percentile",
                ],
            },
        },
    )
    contracts.insert(
        3,
        {
            "consumer": "research:ibit_btc_ahr999_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "BTC-USD": ["ahr999"],
            },
        },
    )
    contracts.insert(
        4,
        {
            "consumer": "research:ibit_btc_ahr999_helper_precomputed_variants",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "BTC-USD": [
                    "ahr999",
                    "ahr999_365d_percentile",
                    "ahr999_30d_slope",
                ],
            },
        },
    )
    contracts.insert(
        5,
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
            "research:ibit_btc_ahr999_precomputed",
        "research:ibit_btc_ahr999_helper_precomputed_variants",
            "research:ibit_btc_ahr999_mayer_precomputed",
            "research:ibit_btc_ahr999_mayer_precomputed_variants",
            "research:nasdaq_sp500_cape_vix_external_context_precomputed",
            "research:nasdaq_sp500_external_context_precomputed",
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
                "known_consumer_count": 7,
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


def _source_family_catalog() -> dict[str, object]:
    return {
        "schema_version": "market_signal_source_families.v1",
        "families": [
            {
                "family": "crypto.btc_cycle_daily",
                "domain": "crypto",
                "bundle_type": "derived_indicators",
                "bundle_id_prefix": "crypto.btc.derived_indicators",
                "canonical_input": "derived_indicators",
                "transform": "crypto.btc.ahr999.v1",
                "provider_dataset": "btc_usd_daily_ohlcv",
                "freshness_policy": "crypto_daily_close_t_plus_1",
                "minimum_history_rows": 200,
                "symbols": ["BTC-USD"],
                "derived_indicator_fields": [
                    "ahr999",
                    "ahr999_sma",
                    "mayer_multiple",
                ],
                "compatible_profiles": [
                    "us_equity:ibit_smart_dca",
                    "research:ibit_btc_ahr999_precomputed",
                    "research:ibit_btc_ahr999_mayer_precomputed",
                    "research:ibit_btc_ahr999_mayer_precomputed_variants",
                ],
            }
        ],
    }


def _write_source_family_catalog_manifest(tmp_path: Path) -> tuple[Path, Path]:
    catalog_path = tmp_path / "signal_source_families.json"
    catalog_path.write_text(
        json.dumps(_source_family_catalog(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "signal_source_families.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_source_family_catalog_manifest.v1",
                "artifact_type": "market_signal_source_family_catalog",
                "catalog_path": "signal_source_families.json",
                "catalog_sha256": _sha256_path(catalog_path),
                "catalog_size_bytes": catalog_path.stat().st_size,
                "catalog_schema_version": "market_signal_source_families.v1",
                "family_count": 1,
                "known_family_count": 1,
                "missing_known_families": [],
                "all_known_families_present": True,
                "all_consumer_contracts_satisfied": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return catalog_path, manifest_path


def _write_platform_handoff_manifest(
    tmp_path: Path,
    *,
    signal_bundle_manifest_path: Path,
    source_family_catalog_manifest_path: Path,
    consumer_contract_registry_manifest_path: Path,
    consumers: tuple[str, ...],
) -> Path:
    manifest = json.loads(signal_bundle_manifest_path.read_text(encoding="utf-8"))
    handoff_path = tmp_path / "platform_handoff.json"
    handoff_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_platform_handoff.v1",
                "artifact_type": "market_signal_platform_handoff",
                "consumer": "",
                "canonical_input": manifest["canonical_input"],
                "bundle_id": manifest["bundle_id"],
                "as_of": manifest["as_of"],
                "freshness_status": manifest["freshness_status"],
                "signal_bundle_manifest_path": (
                    signal_bundle_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "signal_bundle_manifest_sha256": _sha256_path(
                    signal_bundle_manifest_path
                ),
                "source_family_catalog_manifest_path": (
                    source_family_catalog_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "source_family_catalog_manifest_sha256": _sha256_path(
                    source_family_catalog_manifest_path
                ),
                "consumer_contract_registry_manifest_path": (
                    consumer_contract_registry_manifest_path.relative_to(
                        tmp_path
                    ).as_posix()
                ),
                "consumer_contract_registry_manifest_sha256": _sha256_path(
                    consumer_contract_registry_manifest_path
                ),
                "source_family_count": 1,
                "source_families": ["crypto.btc_cycle_daily"],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_count": len(consumers),
                "consumer_contracts": list(consumers),
                "all_known_consumers_present": len(consumers) == 7,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return handoff_path


def _write_platform_handoff_index(tmp_path: Path, handoff_path: Path) -> Path:
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    index_path = tmp_path / "platform_handoff_index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_platform_handoff_index.v1",
                "artifact_type": "market_signal_platform_handoff_index",
                "generated_at": "2026-06-19T00:30:00Z",
                "handoffs": [
                    {
                        "handoff_manifest_path": handoff_path.relative_to(
                            tmp_path
                        ).as_posix(),
                        "handoff_manifest_sha256": _sha256_path(handoff_path),
                        "consumer": handoff["consumer"],
                        "canonical_input": handoff["canonical_input"],
                        "bundle_id": handoff["bundle_id"],
                        "as_of": handoff["as_of"],
                        "freshness_status": handoff["freshness_status"],
                        "source_families": handoff["source_families"],
                        "consumer_contracts": handoff["consumer_contracts"],
                        "all_known_source_families_present": handoff[
                            "all_known_source_families_present"
                        ],
                        "all_consumer_contracts_satisfied": handoff[
                            "all_consumer_contracts_satisfied"
                        ],
                        "all_known_consumers_present": handoff[
                            "all_known_consumers_present"
                        ],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return index_path


def _write_research_export_manifest(tmp_path: Path) -> tuple[Path, Path, Path]:
    research_dir = tmp_path / "research"
    research_dir.mkdir(parents=True)
    input_csv = research_dir / "btc_daily.csv"
    output_csv = research_dir / "btc_cycle.csv"
    quality_report_path = research_dir / "btc_cycle.quality.json"
    input_csv.write_text(
        "date,close\n2026-06-19,100000\n",
        encoding="utf-8",
    )
    output_csv.write_text(
        "date,ahr999\n2026-06-19,0.72\n",
        encoding="utf-8",
    )
    quality_report_path.write_text(
        json.dumps(
            {
                "schema_version": "btc_cycle_research_quality_report.v1",
                "artifact_type": "btc_cycle_research_quality_report",
                "quality_status": "pass",
                "failure_reasons": [],
                "warning_reasons": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = research_dir / "btc_cycle.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "research_export.v1",
                "artifact_type": "btc_cycle_research_csv",
                "transform": "crypto.btc.ahr999.v1",
                "source_version": "0.1.0",
                "as_of": "2026-06-19",
                "min_history": 1,
                "row_count": 1,
                "first_date": "2026-06-19",
                "last_date": "2026-06-19",
                "columns": ["date", "ahr999"],
                "input_csv": {
                    "path": "btc_daily.csv",
                    "sha256": _sha256_path(input_csv),
                    "size_bytes": input_csv.stat().st_size,
                },
                "output_csv": {
                    "path": "btc_cycle.csv",
                    "sha256": _sha256_path(output_csv),
                    "size_bytes": output_csv.stat().st_size,
                },
                "quality_report": {
                    "path": "btc_cycle.quality.json",
                    "sha256": _sha256_path(quality_report_path),
                    "size_bytes": quality_report_path.stat().st_size,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path, output_csv, quality_report_path


def _write_research_handoff_manifest(
    tmp_path: Path,
    *,
    research_export_manifest_path: Path,
    source_family_catalog_manifest_path: Path,
    consumer_contract_registry_manifest_path: Path,
    consumers: tuple[str, ...],
) -> Path:
    research_manifest = json.loads(
        research_export_manifest_path.read_text(encoding="utf-8")
    )
    handoff_path = tmp_path / "research_handoff.json"
    handoff_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_research_handoff.v1",
                "artifact_type": "market_signal_research_handoff",
                "consumer": "research:ibit_btc_ahr999_precomputed",
                "research_export_manifest_path": (
                    research_export_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "research_export_manifest_sha256": _sha256_path(
                    research_export_manifest_path
                ),
                "research_artifact_type": research_manifest["artifact_type"],
                "research_transform": research_manifest["transform"],
                "research_as_of": research_manifest["as_of"],
                "research_output_csv_sha256": research_manifest["output_csv"][
                    "sha256"
                ],
                "research_quality_report_sha256": research_manifest[
                    "quality_report"
                ]["sha256"],
                "source_family_catalog_manifest_path": (
                    source_family_catalog_manifest_path.relative_to(
                        tmp_path
                    ).as_posix()
                ),
                "source_family_catalog_manifest_sha256": _sha256_path(
                    source_family_catalog_manifest_path
                ),
                "source_family_count": 1,
                "source_families": ["crypto.btc_cycle_daily"],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_registry_manifest_path": (
                    consumer_contract_registry_manifest_path.relative_to(
                        tmp_path
                    ).as_posix()
                ),
                "consumer_contract_registry_manifest_sha256": _sha256_path(
                    consumer_contract_registry_manifest_path
                ),
                "consumer_contract_count": len(consumers),
                "consumer_contracts": list(consumers),
                "all_known_consumers_present": len(consumers) == 7,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return handoff_path


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


def test_consumer_index_resolution_filters_incompatible_newer_bundle(tmp_path) -> None:
    consumer = "research:ibit_btc_ahr999_mayer_precomputed_variants"
    compatible_profiles = [
        "us_equity:ibit_smart_dca",
        "research:ibit_btc_ahr999_precomputed",
        "research:ibit_btc_ahr999_mayer_precomputed",
        consumer,
    ]
    compatible_manifest_path, compatible_bundle = _write_signal_bundle_candidate(
        tmp_path,
        relative_dir=Path("compatible"),
        as_of="2026-06-19",
        compatible_profiles=compatible_profiles,
    )
    incompatible_manifest_path, incompatible_bundle = _write_signal_bundle_candidate(
        tmp_path,
        relative_dir=Path("incompatible"),
        as_of="2026-06-20",
        compatible_profiles=["research:other_consumer"],
    )
    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_index.v1",
                "generated_at": "2026-06-21T00:00:00Z",
                "bundles": [
                    _signal_bundle_index_entry(tmp_path, incompatible_manifest_path),
                    _signal_bundle_index_entry(tmp_path, compatible_manifest_path),
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    latest_manifest_path = resolve_signal_bundle_manifest_from_index(
        index_path,
        as_of="2026-06-21",
    )
    market_data = extract_canonical_input_from_index_for_consumer(
        index_path,
        consumer=consumer,
        as_of="2026-06-21",
    )
    consumer_summary = signal_bundle_consumer_audit_summary_from_index(
        index_path,
        consumer=consumer,
        as_of="2026-06-21",
    )

    assert latest_manifest_path == incompatible_manifest_path.resolve()
    assert incompatible_bundle["bundle_id"] == "crypto.btc.derived_indicators.2026-06-20"
    assert consumer_summary["bundle_id"] == compatible_bundle["bundle_id"]
    assert consumer_summary["manifest_path"] == str(compatible_manifest_path.resolve())
    assert market_data["derived_indicators"]["BTC-USD"]["ahr999_sma"] == 0.75


def test_audit_summary_contains_non_sensitive_bundle_metadata() -> None:
    summary = signal_bundle_audit_summary(_load_bundle())
    manifest_summary = signal_bundle_audit_summary_from_manifest(FIXTURE_MANIFEST_PATH)
    index_summary = signal_bundle_audit_summary_from_index(FIXTURE_INDEX_PATH)
    summary_json = json.dumps(summary, sort_keys=True)

    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["canonical_input"] == "derived_indicators"
    assert "us_equity:ibit_smart_dca" in summary["compatible_profiles"]
    assert "research:ibit_btc_ahr999_precomputed" in summary["compatible_profiles"]
    assert "research:ibit_btc_ahr999_mayer_precomputed_variants" in summary[
        "compatible_profiles"
    ]
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
        "495b87b61c7aceff9822329ad0832ec9fc6952225d4552f4dd9d85f947a9adb9"
    )
    assert index_summary["index_schema_version"] == "market_signal_index.v1"
    assert index_summary["index_bundle_count"] == 1
    assert not any("token" in key.lower() or "secret" in key.lower() for key in manifest_summary)


def test_signal_bundle_rejects_invalid_compatible_profiles() -> None:
    bundle = _load_bundle()
    consumer_contract = copy.deepcopy(bundle["consumer_contract"])
    consumer_contract["compatible_profiles"] = []
    bundle["consumer_contract"] = consumer_contract

    with pytest.raises(SignalBundleContractError, match="compatible_profiles"):
        validate_signal_bundle(bundle)


def test_signal_bundle_validates_consumer_required_indicator_fields() -> None:
    bundle = _load_bundle()

    validate_signal_bundle_for_consumer(
        bundle,
        consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
    )
    validate_signal_bundle_for_consumer(
        bundle,
        consumer="research:ibit_btc_ahr999_precomputed",
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
        "research:ibit_btc_ahr999_precomputed"
    ) == {"BTC-USD": ("ahr999",)}
    assert required_indicator_fields_for_consumer(
        "research:ibit_btc_ahr999_mayer_precomputed_variants"
    ) == {"BTC-USD": ("ahr999", "ahr999_sma", "mayer_multiple")}
    assert summary["consumer"] == "research:ibit_btc_ahr999_mayer_precomputed_variants"
    assert summary["consumer_profile_compatible"] is True
    assert "research:ibit_btc_ahr999_mayer_precomputed_variants" in summary[
        "compatible_profiles"
    ]
    assert summary["required_indicator_fields_by_symbol"] == {
        "BTC-USD": ("ahr999", "ahr999_sma", "mayer_multiple")
    }
    assert manifest_summary["bundle_sha256"] == (
        "495b87b61c7aceff9822329ad0832ec9fc6952225d4552f4dd9d85f947a9adb9"
    )
    assert index_summary["manifest_path"] == str(FIXTURE_MANIFEST_PATH.resolve())


def test_signal_bundle_rejects_incompatible_consumer_profile() -> None:
    bundle = _load_bundle()
    consumer_contract = copy.deepcopy(bundle["consumer_contract"])
    consumer_contract["compatible_profiles"] = ["us_equity:ibit_smart_dca"]
    bundle["consumer_contract"] = consumer_contract

    with pytest.raises(SignalBundleContractError, match="compatible_profiles"):
        validate_signal_bundle_for_consumer(
            bundle,
            consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
        )
    with pytest.raises(SignalBundleContractError, match="compatible_profiles"):
        extract_canonical_input_for_consumer(
            bundle,
            consumer="research:ibit_btc_ahr999_mayer_precomputed_variants",
        )


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
        "research:ibit_btc_ahr999_helper_precomputed_variants",
        "research:ibit_btc_ahr999_mayer_precomputed",
        "research:ibit_btc_ahr999_precomputed",
        "research:nasdaq_sp500_cape_vix_external_context_precomputed",
        "research:nasdaq_sp500_external_context_precomputed",
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
    assert summary["consumer_count"] == 7
    assert summary["all_known_consumers_present"] is True

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["registry_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SignalBundleContractError, match="registry_sha256 mismatch"):
        signal_consumer_contract_registry_audit_summary_from_manifest(manifest_path)


def test_source_family_catalog_manifest_matches_required_consumers(tmp_path) -> None:
    catalog_path, manifest_path = _write_source_family_catalog_manifest(tmp_path)

    summary = signal_source_family_catalog_audit_summary_from_manifest(
        manifest_path,
        required_consumers=("us_equity:ibit_smart_dca",),
        expected_transform="crypto.btc.ahr999.v1",
        require_all_known_families=True,
    )

    assert summary["manifest_path"] == str(manifest_path.resolve())
    assert summary["catalog_path"] == str(catalog_path.resolve())
    assert summary["catalog_sha256"] == _sha256_path(catalog_path)
    assert summary["families"] == ("crypto.btc_cycle_daily",)
    assert summary["matched_families"] == ("crypto.btc_cycle_daily",)
    assert summary["required_signal_consumers_present"] is True


def test_platform_handoff_manifest_validates_linked_artifacts(tmp_path) -> None:
    _, signal_manifest_path, _ = _write_signal_bundle_manifest_with_quality_report(
        tmp_path
    )
    source_catalog_path, source_catalog_manifest_path = (
        _write_source_family_catalog_manifest(tmp_path)
    )
    source_catalog = json.loads(source_catalog_path.read_text(encoding="utf-8"))
    source_catalog["families"].append(
        {
            "family": "us_equity.nasdaq_sp500_context_daily",
            "domain": "us_equity",
            "bundle_type": "derived_indicators",
            "bundle_id_prefix": "us_equity.nasdaq_sp500.context",
            "canonical_input": "derived_indicators",
            "transform": "us_equity.nasdaq_sp500.context.v1",
            "provider_dataset": "nasdaq_sp500_external_context_daily",
            "freshness_policy": "us_equity_research_context_t_plus_1",
            "minimum_history_rows": 1,
            "symbols": ["US-EQUITY-CONTEXT"],
            "derived_indicator_fields": [
                "breadth_above_sma200_pct",
                "cape_percentile",
                "vix_percentile",
            ],
            "compatible_profiles": [
                "research:nasdaq_sp500_external_context_precomputed",
            ],
        }
    )
    source_catalog_path.write_text(
        json.dumps(source_catalog, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    source_manifest = json.loads(
        source_catalog_manifest_path.read_text(encoding="utf-8")
    )
    source_manifest["catalog_sha256"] = _sha256_path(source_catalog_path)
    source_manifest["catalog_size_bytes"] = source_catalog_path.stat().st_size
    source_manifest["family_count"] = 2
    source_catalog_manifest_path.write_text(
        json.dumps(source_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    registry_path, registry_manifest_path = _write_consumer_contract_registry_manifest(
        tmp_path,
        _complete_consumer_contract_registry(),
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    contracts = registry["contracts"]
    assert isinstance(contracts, list)
    consumers = tuple(str(contract["consumer"]) for contract in contracts)
    handoff_path = _write_platform_handoff_manifest(
        tmp_path,
        signal_bundle_manifest_path=signal_manifest_path,
        source_family_catalog_manifest_path=source_catalog_manifest_path,
        consumer_contract_registry_manifest_path=registry_manifest_path,
        consumers=consumers,
    )

    summary = signal_platform_handoff_audit_summary_from_manifest(
        handoff_path,
        consumer="us_equity:ibit_smart_dca",
        require_all_known_families=True,
        require_all_known_consumers=True,
    )
    market_data = extract_canonical_input_from_platform_handoff_for_consumer(
        handoff_path,
        consumer="us_equity:ibit_smart_dca",
    )

    assert summary["schema_version"] == "market_signal_platform_handoff.v1"
    assert summary["artifact_type"] == "market_signal_platform_handoff"
    assert summary["consumer"] == "us_equity:ibit_smart_dca"
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["signal_bundle_manifest_sha256"] == _sha256_path(
        signal_manifest_path
    )
    assert summary["source_family_catalog_manifest_sha256"] == _sha256_path(
        source_catalog_manifest_path
    )
    assert summary["consumer_contract_registry_manifest_sha256"] == _sha256_path(
        registry_manifest_path
    )
    assert summary["source_families"] == ("crypto.btc_cycle_daily",)
    assert summary["matched_source_families"] == ("crypto.btc_cycle_daily",)
    assert summary["consumer_contract_count"] == 7
    assert summary["handoff_linked_manifest_sha256s_verified"] is True
    assert summary["consumer_registry_contract_fields_verified"] is True
    assert market_data["derived_indicators"]["BTC-USD"]["ahr999"] == 0.72

    registry_manifest = json.loads(registry_manifest_path.read_text(encoding="utf-8"))
    registry_manifest["consumer_count"] = 3
    registry_manifest_path.write_text(json.dumps(registry_manifest), encoding="utf-8")

    with pytest.raises(
        SignalBundleContractError,
        match="consumer_contract_registry_manifest_sha256",
    ):
        signal_platform_handoff_audit_summary_from_manifest(
            handoff_path,
            consumer="us_equity:ibit_smart_dca",
        )


def test_research_handoff_manifest_validates_linked_research_export(
    tmp_path,
) -> None:
    research_manifest_path, output_csv, quality_report_path = (
        _write_research_export_manifest(tmp_path)
    )
    _, source_catalog_manifest_path = _write_source_family_catalog_manifest(tmp_path)
    registry_path, registry_manifest_path = _write_consumer_contract_registry_manifest(
        tmp_path,
        _complete_consumer_contract_registry(),
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    contracts = registry["contracts"]
    assert isinstance(contracts, list)
    consumers = tuple(str(contract["consumer"]) for contract in contracts)
    handoff_path = _write_research_handoff_manifest(
        tmp_path,
        research_export_manifest_path=research_manifest_path,
        source_family_catalog_manifest_path=source_catalog_manifest_path,
        consumer_contract_registry_manifest_path=registry_manifest_path,
        consumers=consumers,
    )

    export_manifest = load_research_export_manifest(research_manifest_path)
    export_summary = research_export_audit_summary_from_manifest(
        research_manifest_path,
        expected_artifact_type="btc_cycle_research_csv",
        expected_transform="crypto.btc.ahr999.v1",
    )
    handoff = load_research_signal_handoff_manifest(handoff_path)
    handoff_summary = signal_research_handoff_audit_summary_from_manifest(
        handoff_path,
        consumer="research:ibit_btc_ahr999_precomputed",
        expected_research_artifact_type="btc_cycle_research_csv",
        require_all_known_consumers=True,
    )

    assert export_manifest["schema_version"] == "research_export.v1"
    assert export_summary["output_csv_path"] == str(output_csv.resolve())
    assert export_summary["output_csv_sha256"] == _sha256_path(output_csv)
    assert export_summary["quality_report_path"] == str(quality_report_path.resolve())
    assert export_summary["quality_report_sha256"] == _sha256_path(
        quality_report_path
    )
    assert handoff["schema_version"] == "market_signal_research_handoff.v1"
    assert handoff_summary["schema_version"] == "market_signal_research_handoff.v1"
    assert handoff_summary["artifact_type"] == "market_signal_research_handoff"
    assert handoff_summary["consumer"] == "research:ibit_btc_ahr999_precomputed"
    assert handoff_summary["research_export_manifest_sha256"] == _sha256_path(
        research_manifest_path
    )
    assert handoff_summary["research_artifact_type"] == "btc_cycle_research_csv"
    assert handoff_summary["research_transform"] == "crypto.btc.ahr999.v1"
    assert handoff_summary["research_output_csv_sha256"] == _sha256_path(output_csv)
    assert handoff_summary["research_quality_report_sha256"] == _sha256_path(
        quality_report_path
    )
    assert handoff_summary["matched_source_families"] == ("crypto.btc_cycle_daily",)
    assert handoff_summary["source_family_count"] == 1
    assert handoff_summary["source_families"] == ("crypto.btc_cycle_daily",)
    assert handoff_summary["consumer_contract_count"] == 7
    assert handoff_summary["research_export_output_csv_verified"] is True
    assert handoff_summary["consumer_registry_contract_fields_verified"] is True
    assert handoff_summary["handoff_linked_manifest_sha256s_verified"] is True

    handoff_payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    handoff_payload["research_export_manifest_sha256"] = "0" * 64
    handoff_path.write_text(json.dumps(handoff_payload), encoding="utf-8")
    with pytest.raises(
        SignalBundleContractError,
        match="research_export_manifest_sha256",
    ):
        signal_research_handoff_audit_summary_from_manifest(
            handoff_path,
            consumer="research:ibit_btc_ahr999_precomputed",
        )


def test_platform_handoff_index_resolves_matching_handoff_manifest(tmp_path) -> None:
    _, signal_manifest_path, _ = _write_signal_bundle_manifest_with_quality_report(
        tmp_path
    )
    _, source_catalog_manifest_path = _write_source_family_catalog_manifest(tmp_path)
    _, registry_manifest_path = _write_consumer_contract_registry_manifest(
        tmp_path,
        _complete_consumer_contract_registry(),
    )
    handoff_path = _write_platform_handoff_manifest(
        tmp_path,
        signal_bundle_manifest_path=signal_manifest_path,
        source_family_catalog_manifest_path=source_catalog_manifest_path,
        consumer_contract_registry_manifest_path=registry_manifest_path,
        consumers=(
            "us_equity:ibit_smart_dca",
            "research:nasdaq_sp500_external_context_precomputed",
            "research:nasdaq_sp500_cape_vix_external_context_precomputed",
            "research:ibit_btc_ahr999_precomputed",
            "research:ibit_btc_ahr999_helper_precomputed_variants",
            "research:ibit_btc_ahr999_mayer_precomputed",
            "research:ibit_btc_ahr999_mayer_precomputed_variants",
        ),
    )
    index_path = _write_platform_handoff_index(tmp_path, handoff_path)

    index = load_platform_signal_handoff_index(index_path)
    resolved_handoff_path = resolve_platform_signal_handoff_manifest_from_index(
        index_path,
        consumer="us_equity:ibit_smart_dca",
    )
    summary = signal_platform_handoff_audit_summary_from_index(
        index_path,
        consumer="us_equity:ibit_smart_dca",
        require_all_known_consumers=True,
    )
    market_data = extract_canonical_input_from_platform_handoff_index_for_consumer(
        index_path,
        consumer="us_equity:ibit_smart_dca",
    )

    assert index["schema_version"] == "market_signal_platform_handoff_index.v1"
    assert resolved_handoff_path == handoff_path.resolve()
    assert summary["index_schema_version"] == (
        "market_signal_platform_handoff_index.v1"
    )
    assert summary["index_artifact_type"] == "market_signal_platform_handoff_index"
    assert summary["index_handoff_count"] == 1
    assert summary["handoff_manifest_path"] == str(handoff_path.resolve())
    assert summary["handoff_manifest_sha256"] == _sha256_path(handoff_path)
    assert summary["signal_bundle_manifest_sha256"] == _sha256_path(
        signal_manifest_path
    )
    assert market_data["derived_indicators"]["BTC-USD"]["ahr999"] == 0.72

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["handoffs"][0]["handoff_manifest_sha256"] = "0" * 64
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        SignalBundleContractError,
        match="handoff_manifest_sha256",
    ):
        resolve_platform_signal_handoff_manifest_from_index(index_path)


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
        "f95a9bcb331388cf60cad66505f5b44974d09f1cd5f5a00a96c4dca828bc237f"
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
