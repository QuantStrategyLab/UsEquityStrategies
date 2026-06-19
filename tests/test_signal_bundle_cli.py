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
                            "close",
                            "rsi14",
                            "sma200",
                            "sma200_gap",
                            "ahr999_sma",
                            "mayer_multiple",
                        ],
                        "compatible_profiles": [
                            "us_equity:ibit_smart_dca",
                            "research:ibit_btc_ahr999_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed_variants",
                        ],
                        "runtime_consumers": ["us_equity:ibit_smart_dca"],
                        "research_consumers": [
                            "research:ibit_btc_ahr999_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed",
                            "research:ibit_btc_ahr999_mayer_precomputed_variants",
                        ],
                    },
                    {
                        "family": "us_equity.technical_daily",
                        "canonical_input": "derived_indicators",
                        "transform": "technical.daily_ohlcv.v1",
                        "symbols": ["QQQ", "SPY"],
                        "derived_indicator_fields": [
                            "close",
                            "sma50",
                            "sma200",
                            "high252",
                            "drawdown_252d",
                            "sma200_gap",
                            "rsi14",
                        ],
                        "compatible_profiles": [
                            "us_equity:nasdaq_sp500_smart_dca",
                        ],
                        "runtime_consumers": [
                            "us_equity:nasdaq_sp500_smart_dca",
                        ],
                        "research_consumers": [],
                    },
                    {
                        "family": "us_equity.semiconductor_rotation_daily",
                        "canonical_input": "derived_indicators",
                        "transform": "us_equity.semiconductor_rotation.v1",
                        "symbols": ["SOXL", "SOXX"],
                        "derived_indicator_fields": [
                            "price",
                            "ma_trend",
                            "ma20",
                            "ma20_slope",
                            "rsi14",
                            "rsi14_dynamic_threshold",
                            "bb_upper",
                            "realized_volatility_10",
                            "realized_volatility_10_dynamic_threshold",
                            "realized_volatility_10_dynamic_sample_count",
                        ],
                        "compatible_profiles": [
                            "us_equity:soxl_soxx_trend_income",
                        ],
                        "runtime_consumers": [
                            "us_equity:soxl_soxx_trend_income",
                        ],
                        "research_consumers": [],
                    },
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
                "family_count": 3,
                "known_family_count": 3,
                "missing_known_families": [],
                "all_known_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "all_runtime_consumers_covered": True,
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
            "required_indicator_fields_by_symbol": {
                "BTC-USD": [
                    "close",
                    "sma200",
                    "sma200_gap",
                    "rsi14",
                    "ahr999",
                    "ahr999_sma",
                    "mayer_multiple",
                ]
            },
        },
        {
            "consumer": "us_equity:nasdaq_sp500_smart_dca",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "QQQ": [
                    "close",
                    "sma50",
                    "sma200",
                    "high252",
                    "drawdown_252d",
                    "sma200_gap",
                    "rsi14",
                ],
                "SPY": [
                    "close",
                    "sma50",
                    "sma200",
                    "high252",
                    "drawdown_252d",
                    "sma200_gap",
                    "rsi14",
                ],
            },
        },
        {
            "consumer": "us_equity:soxl_soxx_trend_income",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "SOXL": [
                    "price",
                    "ma_trend",
                ],
                "SOXX": [
                    "price",
                    "ma_trend",
                    "ma20",
                    "ma20_slope",
                    "rsi14",
                    "rsi14_dynamic_threshold",
                    "bb_upper",
                    "realized_volatility_10",
                    "realized_volatility_10_dynamic_threshold",
                    "realized_volatility_10_dynamic_sample_count",
                ],
            },
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
        {
            "consumer": "research:nasdaq_sp500_price_proxy",
            "canonical_input": "derived_indicators",
            "required_indicator_fields_by_symbol": {
                "US-EQUITY-PRICE-PROXY": [
                    "QQQ",
                    "SPY",
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
                "consumer_count": 10,
                "known_consumer_count": 10,
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
                "source_family_count": 3,
                "source_families": [
                    "crypto.btc_cycle_daily",
                    "us_equity.technical_daily",
                    "us_equity.semiconductor_rotation_daily",
                ],
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


def _write_runtime_consumption_audit(
    tmp_path: Path,
    handoff_path: Path,
    index_path: Path,
) -> Path:
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    manifest_path = tmp_path / handoff["signal_bundle_manifest_path"]
    audit_path = tmp_path / "consumption_audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "schema_version": "market_signal_consumption_audit.v1",
                "artifact_type": "market_signal_consumption_audit",
                "consumption_mode": "runtime_platform",
                "handoff_source": "platform_handoff_index",
                "consumer": "us_equity:ibit_smart_dca",
                "consumer_role": "runtime",
                "ready_for_consumption": True,
                "ready_for_runtime_injection": True,
                "ready_for_research_consumption": False,
                "runtime_injection_allowed": True,
                "research_csv_runtime_injection_allowed": False,
                "runtime_market_data_key": "derived_indicators",
                "runtime_payload_field": "derived_indicators",
                "canonical_input": handoff["canonical_input"],
                "bundle_id": handoff["bundle_id"],
                "as_of": handoff["as_of"],
                "lookup_as_of": "2026-06-20",
                "freshness_status": handoff["freshness_status"],
                "handoff_manifest_path": str(handoff_path.resolve()),
                "handoff_manifest_sha256": _sha256_path(handoff_path),
                "index_path": str(index_path.resolve()),
                "index_handoff_count": 1,
                "signal_bundle_manifest_path": str(manifest_path.resolve()),
                "signal_bundle_manifest_sha256": _sha256_path(manifest_path),
                "source_family_catalog_manifest_path": str(
                    tmp_path / handoff["source_family_catalog_manifest_path"]
                ),
                "source_family_catalog_manifest_sha256": handoff[
                    "source_family_catalog_manifest_sha256"
                ],
                "consumer_contract_registry_manifest_path": str(
                    tmp_path / handoff["consumer_contract_registry_manifest_path"]
                ),
                "consumer_contract_registry_manifest_sha256": handoff[
                    "consumer_contract_registry_manifest_sha256"
                ],
                "source_family_count": handoff["source_family_count"],
                "source_families": handoff["source_families"],
                "matched_source_family_count": 1,
                "matched_source_families": ["crypto.btc_cycle_daily"],
                "all_known_source_families_present": True,
                "all_consumer_contracts_satisfied": True,
                "consumer_contract_count": handoff["consumer_contract_count"],
                "consumer_contracts": handoff["consumer_contracts"],
                "all_known_consumers_present": True,
                "all_runtime_consumers_covered": True,
                "linked_manifest_sha256s_verified": True,
                "consumer_contract_verified": True,
                "source_catalog_verified": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return audit_path


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
                "all_runtime_consumers_covered": True,
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
            "--require-runtime-consumer-coverage",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == "market_signal_platform_handoff.v1"
    assert summary["consumer"] == "us_equity:ibit_smart_dca"
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["source_families"] == [
        "crypto.btc_cycle_daily",
        "us_equity.technical_daily",
        "us_equity.semiconductor_rotation_daily",
    ]
    assert summary["matched_source_families"] == ["crypto.btc_cycle_daily"]
    assert summary["consumer_contract_count"] == 10
    assert summary["all_known_consumers_present"] is True
    assert summary["all_runtime_consumers_covered"] is True
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
            "--require-runtime-consumer-coverage",
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
    assert summary["all_runtime_consumers_covered"] is True
    assert summary["source_families"] == [
        "crypto.btc_cycle_daily",
        "us_equity.technical_daily",
        "us_equity.semiconductor_rotation_daily",
    ]
    assert summary["matched_source_families"] == ["crypto.btc_cycle_daily"]
    assert summary["handoff_linked_manifest_sha256s_verified"] is True


def test_signal_bundle_cli_validates_runtime_consumption_audit(
    tmp_path,
    capsys,
) -> None:
    handoff_path = _write_platform_handoff_inputs(tmp_path)
    index_path = _write_platform_handoff_index(tmp_path, handoff_path)
    audit_path = _write_runtime_consumption_audit(tmp_path, handoff_path, index_path)

    result = main(
        [
            "--consumption-audit-json",
            str(audit_path),
            "--consumer",
            "us_equity:ibit_smart_dca",
            "--require-runtime-consumer-coverage",
            "--pretty",
        ]
    )

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == "market_signal_consumption_audit.v1"
    assert summary["consumption_mode"] == "runtime_platform"
    assert summary["consumer"] == "us_equity:ibit_smart_dca"
    assert summary["lookup_as_of"] == "2026-06-20"
    assert summary["all_runtime_consumers_covered"] is True
    assert summary["bundle_identity_verified"] is True

    bad_audit = json.loads(audit_path.read_text(encoding="utf-8"))
    bad_audit["all_runtime_consumers_covered"] = False
    audit_path.write_text(json.dumps(bad_audit), encoding="utf-8")
    bad_result = main(
        [
            "--consumption-audit-json",
            str(audit_path),
            "--consumer",
            "us_equity:ibit_smart_dca",
            "--require-runtime-consumer-coverage",
        ]
    )

    assert bad_result == 2
    assert "runtime consumer coverage" in capsys.readouterr().err


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
    assert summary["consumer_contract_count"] == 10
    assert summary["research_export_output_csv_verified"] is True
    assert summary["handoff_linked_manifest_sha256s_verified"] is True


def test_signal_bundle_cli_prints_local_consumer_contract_registry(capsys) -> None:
    result = main(["--local-consumer-contract-registry", "--pretty"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "market_signal_consumer_contracts.v1"
    assert payload["canonical_input"] == "derived_indicators"
    assert len(payload["contracts"]) == 10
    consumers = [contract["consumer"] for contract in payload["contracts"]]
    assert "research:nasdaq_sp500_price_proxy" in consumers
    price_proxy_contract = next(
        contract
        for contract in payload["contracts"]
        if contract["consumer"] == "research:nasdaq_sp500_price_proxy"
    )
    assert price_proxy_contract["required_indicator_fields_by_symbol"] == {
        "US-EQUITY-PRICE-PROXY": ["QQQ", "SPY"]
    }


def test_signal_bundle_cli_rejects_local_consumer_registry_mixed_args(
    capsys,
) -> None:
    result = main(
        [
            "--local-consumer-contract-registry",
            "--require-all-known-consumers",
        ]
    )

    assert result == 2
    assert "--local-consumer-contract-registry" in capsys.readouterr().err


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
                            "BTC-USD": [
                                "close",
                                "sma200",
                                "sma200_gap",
                                "rsi14",
                                "ahr999",
                                "ahr999_sma",
                                "mayer_multiple",
                            ],
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
    assert "research:nasdaq_sp500_price_proxy" in summary["missing_known_consumers"]
    assert summary["path"] == str(registry_path.resolve())
    assert summary["local_contract_registry_verified"] is True
    assert summary["canonical_registry_payload_sha256"] == summary[
        "local_registry_payload_sha256"
    ]


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
                            "BTC-USD": [
                                "close",
                                "sma200",
                                "sma200_gap",
                                "rsi14",
                                "ahr999",
                                "ahr999_sma",
                                "mayer_multiple",
                            ],
                        },
                    },
                    {
                        "consumer": "us_equity:nasdaq_sp500_smart_dca",
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "QQQ": [
                                "close",
                                "sma50",
                                "sma200",
                                "high252",
                                "drawdown_252d",
                                "sma200_gap",
                                "rsi14",
                            ],
                            "SPY": [
                                "close",
                                "sma50",
                                "sma200",
                                "high252",
                                "drawdown_252d",
                                "sma200_gap",
                                "rsi14",
                            ],
                        },
                    },
                    {
                        "consumer": "us_equity:soxl_soxx_trend_income",
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "SOXL": [
                                "price",
                                "ma_trend",
                            ],
                            "SOXX": [
                                "price",
                                "ma_trend",
                                "ma20",
                                "ma20_slope",
                                "rsi14",
                                "rsi14_dynamic_threshold",
                                "bb_upper",
                                "realized_volatility_10",
                                "realized_volatility_10_dynamic_threshold",
                                "realized_volatility_10_dynamic_sample_count",
                            ],
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
                    {
                        "consumer": "research:nasdaq_sp500_price_proxy",
                        "canonical_input": "derived_indicators",
                        "required_indicator_fields_by_symbol": {
                            "US-EQUITY-PRICE-PROXY": [
                                "QQQ",
                                "SPY",
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
                "consumer_count": 10,
                "known_consumer_count": 10,
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
    assert summary["consumer_count"] == 10
    assert summary["all_known_consumers_present"] is True
    assert summary["local_contract_registry_verified"] is True
    assert summary["canonical_registry_payload_sha256"] == summary[
        "local_registry_payload_sha256"
    ]


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
                            "BTC-USD": [
                                "close",
                                "sma200",
                                "sma200_gap",
                                "rsi14",
                                "ahr999",
                                "ahr999_sma",
                                "mayer_multiple",
                            ],
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
