from __future__ import annotations

import hashlib
import json
from pathlib import Path

from us_equity_strategies.signals.signal_bundle_cli import main


FIXTURE_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "signal_bundles"
    / "crypto"
    / "btc"
    / "derived_indicators"
    / "2026-06-19"
    / "manifest.json"
)
FIXTURE_INDEX_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "signal_bundles"
    / "index.json"
)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_platform_handoff_inputs(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle_path = bundle_dir / "signal_bundle.json"
    bundle_path.write_text(
        FIXTURE_MANIFEST_PATH.with_name("signal_bundle.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    signal_manifest = json.loads(FIXTURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    signal_manifest["bundle_sha256"] = _sha256_path(bundle_path)
    signal_manifest_path = bundle_dir / "manifest.json"
    signal_manifest_path.write_text(
        json.dumps(signal_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    source_catalog_path = tmp_path / "source_catalog" / "signal_source_families.json"
    source_catalog_path.parent.mkdir()
    source_catalog_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_source_families.v1",
                "families": [
                    {
                        "family": "crypto.btc_cycle_daily",
                        "canonical_input": "derived_indicators",
                        "transform": "crypto.btc.ahr999.v1",
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
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    source_catalog_manifest_path = (
        source_catalog_path.parent / "signal_source_families.manifest.json"
    )
    source_catalog_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_source_family_catalog_manifest.v1",
                "artifact_type": "market_signal_source_family_catalog",
                "catalog_path": "signal_source_families.json",
                "catalog_sha256": _sha256_path(source_catalog_path),
                "catalog_size_bytes": source_catalog_path.stat().st_size,
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

    registry_path = tmp_path / "contracts" / "market_signal_consumers.json"
    registry_path.parent.mkdir()
    contracts = [
        {
            "consumer": "us_equity:ibit_smart_dca",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {"BTC-USD": ["ahr999"]},
        },
        {
            "consumer": "research:ibit_btc_ahr999_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {"BTC-USD": ["ahr999"]},
        },
        {
            "consumer": "research:ibit_btc_ahr999_helper_precomputed_variants",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "BTC-USD": [
                    "ahr999",
                    "ahr999_365d_percentile",
                    "ahr999_30d_slope",
                ]
            },
        },
        {
            "consumer": "research:ibit_btc_ahr999_mayer_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "BTC-USD": ["ahr999", "mayer_multiple"]
            },
        },
        {
            "consumer": "research:ibit_btc_ahr999_mayer_precomputed_variants",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "BTC-USD": ["ahr999", "ahr999_sma", "mayer_multiple"]
            },
        },
        {
            "consumer": "research:nasdaq_sp500_cape_vix_external_context_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "US-EQUITY-CONTEXT": [
                    "cape_percentile",
                    "vix_percentile",
                ]
            },
        },
        {
            "consumer": "research:nasdaq_sp500_external_context_precomputed",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "US-EQUITY-CONTEXT": [
                    "breadth_above_sma200_pct",
                    "cape_percentile",
                    "vix_percentile",
                ]
            },
        },
    ]
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "contracts": contracts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    registry_manifest_path = registry_path.parent / "market_signal_consumers.manifest.json"
    registry_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contract_manifest.v1",
                "artifact_type": "market_signal_consumer_contract_registry",
                "registry_path": "market_signal_consumers.json",
                "registry_sha256": _sha256_path(registry_path),
                "registry_size_bytes": registry_path.stat().st_size,
                "registry_schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "consumer_count": 7,
                "known_consumer_count": 7,
                "missing_known_consumers": [],
                "all_known_consumers_present": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    handoff_path = tmp_path / "platform_handoff.json"
    handoff_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_platform_handoff.v1",
                "artifact_type": "market_signal_platform_handoff",
                "consumer": "us_equity:ibit_smart_dca",
                "canonical_input": signal_manifest["canonical_input"],
                "bundle_id": signal_manifest["bundle_id"],
                "as_of": signal_manifest["as_of"],
                "freshness_status": signal_manifest["freshness_status"],
                "signal_bundle_manifest_path": signal_manifest_path.relative_to(
                    tmp_path
                ).as_posix(),
                "signal_bundle_manifest_sha256": _sha256_path(signal_manifest_path),
                "source_family_catalog_manifest_path": (
                    source_catalog_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "source_family_catalog_manifest_sha256": _sha256_path(
                    source_catalog_manifest_path
                ),
                "consumer_contract_registry_manifest_path": (
                    registry_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "consumer_contract_registry_manifest_sha256": _sha256_path(
                    registry_manifest_path
                ),
                "source_family_count": 1,
                "source_families": ["crypto.btc_cycle_daily"],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_count": len(contracts),
                "consumer_contracts": [
                    str(contract["consumer"])
                    for contract in contracts
                ],
                "all_known_consumers_present": True,
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


def _write_research_handoff_inputs(tmp_path: Path) -> tuple[Path, Path]:
    _write_platform_handoff_inputs(tmp_path)
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    input_csv = research_dir / "btc_daily.csv"
    output_csv = research_dir / "btc_cycle.csv"
    quality_report_path = research_dir / "btc_cycle.quality.json"
    input_csv.write_text("date,close\n2026-06-19,100000\n", encoding="utf-8")
    output_csv.write_text("date,ahr999\n2026-06-19,0.72\n", encoding="utf-8")
    quality_report_path.write_text(
        json.dumps(
            {
                "schema_version": "btc_cycle_research_quality_report.v1",
                "artifact_type": "btc_cycle_research_quality_report",
                "quality_status": "pass",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    research_manifest_path = research_dir / "btc_cycle.manifest.json"
    research_manifest_path.write_text(
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
    registry_path = tmp_path / "contracts" / "market_signal_consumers.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    contracts = registry["contracts"]
    assert isinstance(contracts, list)
    consumers = [str(contract["consumer"]) for contract in contracts]
    source_catalog_manifest_path = (
        tmp_path / "source_catalog" / "signal_source_families.manifest.json"
    )
    registry_manifest_path = (
        tmp_path / "contracts" / "market_signal_consumers.manifest.json"
    )
    research_manifest = json.loads(research_manifest_path.read_text(encoding="utf-8"))
    handoff_path = tmp_path / "research_handoff.json"
    handoff_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_research_handoff.v1",
                "artifact_type": "market_signal_research_handoff",
                "consumer": "research:ibit_btc_ahr999_precomputed",
                "research_export_manifest_path": (
                    research_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "research_export_manifest_sha256": _sha256_path(
                    research_manifest_path
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
                    source_catalog_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "source_family_catalog_manifest_sha256": _sha256_path(
                    source_catalog_manifest_path
                ),
                "source_family_count": 1,
                "source_families": ["crypto.btc_cycle_daily"],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_registry_manifest_path": (
                    registry_manifest_path.relative_to(tmp_path).as_posix()
                ),
                "consumer_contract_registry_manifest_sha256": _sha256_path(
                    registry_manifest_path
                ),
                "consumer_contract_count": len(consumers),
                "consumer_contracts": consumers,
                "all_known_consumers_present": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return research_manifest_path, handoff_path


def test_signal_bundle_cli_prints_non_sensitive_audit_summary(capsys) -> None:
    result = main([str(FIXTURE_MANIFEST_PATH), "--pretty"])

    assert result == 0
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["bundle_sha256"] == (
        "495b87b61c7aceff9822329ad0832ec9fc6952225d4552f4dd9d85f947a9adb9"
    )
    assert "us_equity:ibit_smart_dca" in summary["compatible_profiles"]
    assert summary["symbols"] == ["BTC-USD"]
    assert summary["indicator_fields_by_symbol"]["BTC-USD"] == [
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
    ]
    assert summary["indicator_field_count_by_symbol"] == {"BTC-USD": 11}
    assert "64000.0" not in output
    assert not any("token" in key.lower() or "secret" in key.lower() for key in summary)


def test_signal_bundle_cli_can_resolve_manifest_from_index(capsys) -> None:
    result = main(["--index", str(FIXTURE_INDEX_PATH), "--as-of", "2026-06-20", "--pretty"])

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["index_schema_version"] == "market_signal_index.v1"
    assert summary["manifest_path"] == str(FIXTURE_MANIFEST_PATH.resolve())


def test_signal_bundle_cli_validates_consumer_indicator_fields(capsys) -> None:
    result = main(
        [
            "--index",
            str(FIXTURE_INDEX_PATH),
            "--as-of",
            "2026-06-20",
            "--consumer",
            "research:ibit_btc_ahr999_mayer_precomputed_variants",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["consumer"] == "research:ibit_btc_ahr999_mayer_precomputed_variants"
    assert summary["consumer_profile_compatible"] is True
    assert "research:ibit_btc_ahr999_mayer_precomputed_variants" in summary[
        "compatible_profiles"
    ]
    assert summary["required_indicator_fields_by_symbol"] == {
        "BTC-USD": ["ahr999", "ahr999_sma", "mayer_multiple"]
    }


def test_signal_bundle_cli_validates_platform_handoff_manifest(
    tmp_path,
    capsys,
) -> None:
    handoff_path = _write_platform_handoff_inputs(tmp_path)

    result = main(
        [
            "--platform-handoff-manifest",
            str(handoff_path),
            "--consumer",
            "us_equity:ibit_smart_dca",
            "--require-all-known-families",
            "--require-all-known-consumers",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == "market_signal_platform_handoff.v1"
    assert summary["consumer"] == "us_equity:ibit_smart_dca"
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["source_families"] == ["crypto.btc_cycle_daily"]
    assert summary["matched_source_families"] == ["crypto.btc_cycle_daily"]
    assert summary["consumer_contract_count"] == 7
    assert summary["all_known_consumers_present"] is True
    assert summary["handoff_linked_manifest_sha256s_verified"] is True


def test_signal_bundle_cli_validates_platform_handoff_index(
    tmp_path,
    capsys,
) -> None:
    handoff_path = _write_platform_handoff_inputs(tmp_path)
    index_path = _write_platform_handoff_index(tmp_path, handoff_path)

    result = main(
        [
            "--platform-handoff-index",
            str(index_path),
            "--consumer",
            "us_equity:ibit_smart_dca",
            "--as-of",
            "2026-06-20",
            "--require-all-known-families",
            "--require-all-known-consumers",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["index_schema_version"] == (
        "market_signal_platform_handoff_index.v1"
    )
    assert summary["index_artifact_type"] == "market_signal_platform_handoff_index"
    assert summary["index_handoff_count"] == 1
    assert summary["handoff_manifest_path"] == str(handoff_path.resolve())
    assert summary["handoff_manifest_sha256"] == _sha256_path(handoff_path)
    assert summary["consumer"] == "us_equity:ibit_smart_dca"
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["source_families"] == ["crypto.btc_cycle_daily"]
    assert summary["matched_source_families"] == ["crypto.btc_cycle_daily"]
    assert summary["handoff_linked_manifest_sha256s_verified"] is True


def test_signal_bundle_cli_validates_research_export_manifest(
    tmp_path,
    capsys,
) -> None:
    research_manifest_path, _ = _write_research_handoff_inputs(tmp_path)

    result = main(
        [
            "--research-export-manifest",
            str(research_manifest_path),
            "--research-artifact-type",
            "btc_cycle_research_csv",
            "--research-transform",
            "crypto.btc.ahr999.v1",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["manifest_schema_version"] == "research_export.v1"
    assert summary["artifact_type"] == "btc_cycle_research_csv"
    assert summary["transform"] == "crypto.btc.ahr999.v1"
    assert summary["output_csv_sha256_verified"] is True
    assert summary["quality_report_sha256_verified"] is True


def test_signal_bundle_cli_validates_research_handoff_manifest(
    tmp_path,
    capsys,
) -> None:
    _, handoff_path = _write_research_handoff_inputs(tmp_path)

    result = main(
        [
            "--research-handoff-manifest",
            str(handoff_path),
            "--consumer",
            "research:ibit_btc_ahr999_precomputed",
            "--research-artifact-type",
            "btc_cycle_research_csv",
            "--require-all-known-consumers",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == "market_signal_research_handoff.v1"
    assert summary["consumer"] == "research:ibit_btc_ahr999_precomputed"
    assert summary["research_artifact_type"] == "btc_cycle_research_csv"
    assert summary["research_transform"] == "crypto.btc.ahr999.v1"
    assert summary["matched_source_families"] == ["crypto.btc_cycle_daily"]
    assert summary["consumer_contract_count"] == 7
    assert summary["research_export_output_csv_verified"] is True
    assert summary["handoff_linked_manifest_sha256s_verified"] is True


def test_signal_bundle_cli_validates_consumer_contract_registry(tmp_path, capsys) -> None:
    registry_path = tmp_path / "market_signal_consumers.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "contracts": [
                    {
                        "consumer": "us_equity:ibit_smart_dca",
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": ["ahr999"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--consumer-contract-registry",
            str(registry_path),
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == "market_signal_consumer_contracts.v1"
    assert summary["consumer_count"] == 1
    assert summary["consumers"] == ["us_equity:ibit_smart_dca"]
    assert summary["all_known_consumers_present"] is False
    assert summary["path"] == str(registry_path.resolve())


def test_signal_bundle_cli_validates_consumer_contract_registry_manifest(
    tmp_path,
    capsys,
) -> None:
    registry_path = tmp_path / "market_signal_consumers.json"
    registry_path.write_text(
        json.dumps(
            {
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
                        "consumer": "research:ibit_btc_ahr999_precomputed",
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": ["ahr999"],
                        },
                    },
                    {
                        "consumer": (
                            "research:ibit_btc_ahr999_helper_precomputed_variants"
                        ),
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": [
                                "ahr999",
                                "ahr999_365d_percentile",
                                "ahr999_30d_slope",
                            ],
                        },
                    },
                    {
                        "consumer": "research:ibit_btc_ahr999_mayer_precomputed",
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": ["ahr999", "mayer_multiple"],
                        },
                    },
                    {
                        "consumer": (
                            "research:ibit_btc_ahr999_mayer_precomputed_variants"
                        ),
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": [
                                "ahr999",
                                "ahr999_sma",
                                "mayer_multiple",
                            ],
                        },
                    },
                    {
                        "consumer": (
                            "research:nasdaq_sp500_cape_vix_external_context_precomputed"
                        ),
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "US-EQUITY-CONTEXT": [
                                "cape_percentile",
                                "vix_percentile",
                            ],
                        },
                    },
                    {
                        "consumer": (
                            "research:nasdaq_sp500_external_context_precomputed"
                        ),
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "US-EQUITY-CONTEXT": [
                                "breadth_above_sma200_pct",
                                "cape_percentile",
                                "vix_percentile",
                            ],
                        },
                    },
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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
                "registry_schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "consumer_count": 7,
                "known_consumer_count": 7,
                "missing_known_consumers": [],
                "all_known_consumers_present": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = main(
        [
            "--consumer-contract-registry-manifest",
            str(manifest_path),
            "--require-all-known-consumers",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["manifest_path"] == str(manifest_path.resolve())
    assert summary["manifest_schema_version"] == (
        "market_signal_consumer_contract_manifest.v1"
    )
    assert summary["registry_path"] == str(registry_path.resolve())
    assert summary["registry_sha256"] == hashlib.sha256(
        registry_path.read_bytes()
    ).hexdigest()
    assert summary["consumer_count"] == 7
    assert summary["all_known_consumers_present"] is True


def test_signal_bundle_cli_can_require_complete_consumer_contract_registry(tmp_path, capsys) -> None:
    registry_path = tmp_path / "market_signal_consumers.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumer_contracts.v1",
                "canonical_input": "derived_indicators",
                "contracts": [
                    {
                        "consumer": "us_equity:ibit_smart_dca",
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "BTC-USD": ["ahr999"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--consumer-contract-registry",
            str(registry_path),
            "--require-all-known-consumers",
        ]
    )

    assert result == 2
    assert "missing known consumers" in capsys.readouterr().err
