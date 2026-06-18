from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.signals import (
    SignalBundleContractError,
    extract_canonical_input,
    extract_canonical_input_from_file,
    extract_canonical_input_from_index,
    extract_canonical_input_from_manifest,
    load_signal_bundle,
    load_signal_bundle_from_index,
    load_signal_bundle_from_manifest,
    load_signal_bundle_index,
    load_signal_bundle_manifest,
    resolve_signal_bundle_manifest_from_index,
    signal_bundle_audit_summary,
    signal_bundle_audit_summary_from_index,
    signal_bundle_audit_summary_from_manifest,
    validate_signal_bundle,
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


def test_fresh_signal_bundle_is_accepted() -> None:
    validate_signal_bundle(_load_bundle())
    assert load_signal_bundle(FIXTURE_PATH)["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert load_signal_bundle_manifest(FIXTURE_MANIFEST_PATH)["bundle_path"] == "signal_bundle.json"


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


def test_audit_summary_contains_non_sensitive_bundle_metadata() -> None:
    summary = signal_bundle_audit_summary(_load_bundle())
    manifest_summary = signal_bundle_audit_summary_from_manifest(FIXTURE_MANIFEST_PATH)
    index_summary = signal_bundle_audit_summary_from_index(FIXTURE_INDEX_PATH)

    assert summary["bundle_id"] == "crypto.btc.derived_indicators.2026-06-19"
    assert summary["canonical_input"] == "derived_indicators"
    assert summary["freshness_status"] == "fresh"
    assert summary["provider_timestamp"] == "2026-06-19T00:00:00Z"
    assert summary["transform"] == "crypto.btc.ahr999.v1"
    assert manifest_summary["bundle_sha256"] == (
        "3da3996095f134151019c38cb1bee9acc111978aa93dd5a613e1960385d41500"
    )
    assert index_summary["index_schema_version"] == "market_signal_index.v1"
    assert index_summary["index_bundle_count"] == 1
    assert not any("token" in key.lower() or "secret" in key.lower() for key in manifest_summary)


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
