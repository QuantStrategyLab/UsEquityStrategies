from __future__ import annotations

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


def test_signal_bundle_cli_prints_non_sensitive_audit_summary(capsys) -> None:
    result = main([str(FIXTURE_MANIFEST_PATH), "--pretty"])

    assert result == 0
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["bundle_sha256"] == (
        "3da3996095f134151019c38cb1bee9acc111978aa93dd5a613e1960385d41500"
    )
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
    assert summary["required_indicator_fields_by_symbol"] == {
        "BTC-USD": ["ahr999", "ahr999_sma", "mayer_multiple"]
    }


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
                            "BTC-USD": ["ahr999", "mayer_multiple"],
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
    assert summary["path"] == str(registry_path.resolve())
