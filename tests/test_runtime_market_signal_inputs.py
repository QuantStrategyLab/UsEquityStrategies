from __future__ import annotations

import pytest

from us_equity_strategies.signals import runtime_market_signal_inputs as runtime_inputs


def test_runtime_market_signal_consumer_constants_are_exported():
    assert runtime_inputs.IBIT_SMART_DCA_MARKET_SIGNAL_CONSUMER == (
        "us_equity:ibit_smart_dca"
    )
    assert runtime_inputs.NASDAQ_SP500_SMART_DCA_MARKET_SIGNAL_CONSUMER == (
        "us_equity:nasdaq_sp500_smart_dca"
    )
    assert runtime_inputs.SOXL_SOXX_TREND_INCOME_MARKET_SIGNAL_CONSUMER == (
        "us_equity:soxl_soxx_trend_income"
    )


def test_market_signal_profile_registry_maps_all_runtime_consumers():
    assert runtime_inputs.market_signal_consumer_for_strategy_profile(
        "IBIT_SMART_DCA"
    ) == "us_equity:ibit_smart_dca"
    assert runtime_inputs.market_signal_consumer_for_strategy_profile(
        "nasdaq_sp500_smart_dca"
    ) == "us_equity:nasdaq_sp500_smart_dca"
    assert runtime_inputs.market_signal_consumer_for_strategy_profile(
        "soxl_soxx_trend_income"
    ) == "us_equity:soxl_soxx_trend_income"
    assert runtime_inputs.market_signal_consumer_for_strategy_profile(
        "tqqq_growth_income"
    ) is None
    assert runtime_inputs.market_signal_strategy_profiles() == (
        "ibit_smart_dca",
        "nasdaq_sp500_smart_dca",
        "soxl_soxx_trend_income",
    )
    assert set(runtime_inputs.market_signal_consumers_for_strategy_profiles()) == {
        "us_equity:ibit_smart_dca",
        "us_equity:nasdaq_sp500_smart_dca",
        "us_equity:soxl_soxx_trend_income",
    }
    assert runtime_inputs.default_market_signal_inputs_when_unconfigured(
        "ibit_smart_dca"
    ) == {"derived_indicators": {}}
    assert runtime_inputs.default_market_signal_inputs_when_unconfigured(
        "soxl_soxx_trend_income"
    ) == {}


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


def test_extract_consumer_market_signal_inputs_uses_last_valid_on_failure(monkeypatch, tmp_path):
    calls = {"materialize": 0}

    def fake_materialize(reference, *, cache_dir, client_factory=None):
        calls["materialize"] += 1
        if calls["materialize"] == 1:
            return tmp_path / "index.json", {"source_uri": reference, "materialized_count": 3}
        raise RuntimeError("signal source unavailable")

    def fake_extract(path, *, consumer, as_of=None):
        return {
            "derived_indicators": {
                "BTC-USD": {
                    "close": 64000.0,
                    "sma200": 59000.0,
                    "sma200_gap": 0.08,
                    "rsi14": 54.0,
                    "ahr999": 0.8,
                    "ahr999_sma": 0.82,
                    "mayer_multiple": 1.08,
                }
            }
        }

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

    first_inputs, first_metadata = runtime_inputs.extract_consumer_market_signal_inputs_from_reference(
        "gs://signals/platform_handoffs/index.json",
        reference_type="platform_handoff_index",
        consumer="us_equity:ibit_smart_dca",
        cache_dir=tmp_path / "cache",
        as_of="2026-06-19",
        fallback_mode="last_valid",
    )
    fallback_inputs, fallback_metadata = runtime_inputs.extract_consumer_market_signal_inputs_from_reference(
        "gs://signals/platform_handoffs/index.json",
        reference_type="platform_handoff_index",
        consumer="us_equity:ibit_smart_dca",
        cache_dir=tmp_path / "cache",
        as_of="2026-06-20",
        fallback_mode="last_valid",
    )

    assert first_inputs["derived_indicators"]["BTC-USD"]["ahr999"] == 0.8
    assert first_metadata["materialized_count"] == 3
    assert fallback_inputs == first_inputs
    assert fallback_metadata["artifact_fallback_used"] is True
    assert fallback_metadata["artifact_fallback_mode"] == "last_valid"
    assert "signal source unavailable" in fallback_metadata["artifact_fallback_reason"]


def test_extract_consumer_market_signal_inputs_without_fallback_raises(monkeypatch, tmp_path):
    def fake_materialize(reference, *, cache_dir, client_factory=None):
        raise RuntimeError("signal source unavailable")

    monkeypatch.setattr(
        runtime_inputs,
        "materialize_market_signal_artifact_tree",
        fake_materialize,
    )

    with pytest.raises(RuntimeError, match="signal source unavailable"):
        runtime_inputs.extract_consumer_market_signal_inputs_from_reference(
            "gs://signals/platform_handoffs/index.json",
            reference_type="platform_handoff_index",
            consumer="us_equity:ibit_smart_dca",
            cache_dir=tmp_path / "cache",
        )


def test_normalize_market_signal_reference_type_rejects_unknown():
    assert runtime_inputs.normalize_market_signal_reference_type("audit") == "consumption_audit"
    assert runtime_inputs.normalize_market_signal_reference_type("handoff_manifest") == "platform_handoff"
    assert runtime_inputs.normalize_market_signal_fallback_mode("last-valid") == "last_valid"

    with pytest.raises(ValueError, match="unsupported market signal reference_type"):
        runtime_inputs.normalize_market_signal_reference_type("research_export")

    with pytest.raises(ValueError, match="unsupported market signal fallback mode"):
        runtime_inputs.normalize_market_signal_fallback_mode("platform")
