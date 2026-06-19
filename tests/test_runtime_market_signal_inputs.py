from __future__ import annotations

from pathlib import Path

import pytest

from us_equity_strategies.signals import runtime_market_signal_inputs as runtime_inputs


def test_extract_consumer_market_signal_inputs_from_reference_uses_handoff_index(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_materialize(reference, *, cache_dir, client_factory=None):
        calls["materialize"] = (reference, cache_dir, client_factory)
        return tmp_path / "index.json", {"source_uri": reference, "materialized_count": 3}

    def fake_extract(path, *, consumer, as_of=None):
        calls["extract"] = (path, consumer, as_of)
        return {"derived_indicators": {"BTC": {"mvrv_z_score": 1.0}}}

    monkeypatch.setattr(
        runtime_inputs,
        "materialize_market_signal_artifact_tree",
        fake_materialize,
    )
    monkeypatch.setattr(
        runtime_inputs,
        "extract_canonical_input_from_platform_handoff_index_for_consumer",
        fake_extract,
    )

    market_inputs, metadata = runtime_inputs.extract_consumer_market_signal_inputs_from_reference(
        "gs://signals/platform_handoffs/index.json",
        reference_type="handoff-index",
        consumer="us_equity:ibit_smart_dca",
        cache_dir=tmp_path / "cache",
        as_of="2026-06-19",
        client_factory=object,
    )

    assert market_inputs == {"derived_indicators": {"BTC": {"mvrv_z_score": 1.0}}}
    assert calls["materialize"] == (
        "gs://signals/platform_handoffs/index.json",
        tmp_path / "cache",
        object,
    )
    assert calls["extract"] == (
        tmp_path / "index.json",
        "us_equity:ibit_smart_dca",
        "2026-06-19",
    )
    assert metadata["reference_type"] == "platform_handoff_index"
    assert metadata["consumer"] == "us_equity:ibit_smart_dca"
    assert metadata["materialized_count"] == 3


def test_normalize_market_signal_reference_type_rejects_unknown():
    assert runtime_inputs.normalize_market_signal_reference_type("audit") == "consumption_audit"
    assert runtime_inputs.normalize_market_signal_reference_type("handoff_manifest") == "platform_handoff"

    with pytest.raises(ValueError, match="unsupported market signal reference_type"):
        runtime_inputs.normalize_market_signal_reference_type("research_export")
