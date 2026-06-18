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
    summary = json.loads(capsys.readouterr().out)
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["bundle_sha256"] == (
        "3da3996095f134151019c38cb1bee9acc111978aa93dd5a613e1960385d41500"
    )
    assert summary["symbols"] == ["BTC-USD"]
    assert not any("token" in key.lower() or "secret" in key.lower() for key in summary)


def test_signal_bundle_cli_can_resolve_manifest_from_index(capsys) -> None:
    result = main(["--index", str(FIXTURE_INDEX_PATH), "--as-of", "2026-06-20", "--pretty"])

    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["index_schema_version"] == "market_signal_index.v1"
    assert summary["manifest_path"] == str(FIXTURE_MANIFEST_PATH.resolve())
