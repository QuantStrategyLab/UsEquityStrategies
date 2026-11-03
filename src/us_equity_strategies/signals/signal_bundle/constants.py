from __future__ import annotations


MARKET_SIGNAL_BUNDLE_SCHEMA_VERSION = "market_signal_bundle.v1"
MARKET_SIGNAL_MANIFEST_SCHEMA_VERSION = "market_signal_manifest.v1"
MARKET_SIGNAL_INDEX_SCHEMA_VERSION = "market_signal_index.v1"
MARKET_SIGNAL_CONSUMER_CONTRACTS_SCHEMA_VERSION = "market_signal_consumer_contracts.v1"
MARKET_SIGNAL_CONSUMER_CONTRACT_MANIFEST_SCHEMA_VERSION = (
    "market_signal_consumer_contract_manifest.v1"
)
MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_SCHEMA_VERSION = "market_signal_source_families.v1"
MARKET_SIGNAL_SOURCE_FAMILY_CATALOG_MANIFEST_SCHEMA_VERSION = (
    "market_signal_source_family_catalog_manifest.v1"
)
MARKET_SIGNAL_PLATFORM_HANDOFF_SCHEMA_VERSION = "market_signal_platform_handoff.v1"
MARKET_SIGNAL_PLATFORM_HANDOFF_INDEX_SCHEMA_VERSION = (
    "market_signal_platform_handoff_index.v1"
)
MARKET_SIGNAL_RESEARCH_HANDOFF_SCHEMA_VERSION = "market_signal_research_handoff.v1"
MARKET_SIGNAL_CONSUMPTION_AUDIT_SCHEMA_VERSION = (
    "market_signal_consumption_audit.v1"
)
RESEARCH_EXPORT_SCHEMA_VERSION = "research_export.v1"
QUALITY_REPORT_SCHEMA_VERSION = "market_signal_quality_report.v1"
CANONICAL_INPUT_DERIVED_INDICATORS = "derived_indicators"
FRESHNESS_FRESH = "fresh"


REQUIRED_INDICATOR_FIELDS_BY_CONSUMER: dict[str, dict[str, tuple[str, ...]]] = {
    "us_equity:ibit_smart_dca": {
        "BTC-USD": (
            "close",
            "sma200",
            "sma200_gap",
            "rsi14",
            "ahr999",
            "ahr999_sma",
            "mayer_multiple",
        ),
    },
    "us_equity:nasdaq_sp500_smart_dca": {
        "QQQ": (
            "close",
            "sma50",
            "sma200",
            "high252",
            "drawdown_252d",
            "sma200_gap",
            "rsi14",
        ),
        "SPY": (
            "close",
            "sma50",
            "sma200",
            "high252",
            "drawdown_252d",
            "sma200_gap",
            "rsi14",
        ),
    },
    "us_equity:soxl_soxx_trend_income": {
        "SOXL": (
            "price",
            "ma_trend",
        ),
        "SOXX": (
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
        ),
    },
    "research:nasdaq_sp500_external_context_precomputed": {
        "US-EQUITY-CONTEXT": (
            "breadth_above_sma200_pct",
            "cape_percentile",
            "vix_percentile",
        ),
    },
    "research:nasdaq_sp500_cape_vix_external_context_precomputed": {
        "US-EQUITY-CONTEXT": (
            "cape_percentile",
            "vix_percentile",
        ),
    },
    "research:nasdaq_sp500_price_proxy": {
        "US-EQUITY-PRICE-PROXY": (
            "QQQ",
            "SPY",
        ),
    },
    "research:ibit_btc_ahr999_precomputed": {
        "BTC-USD": ("ahr999",),
    },
    "research:ibit_btc_ahr999_helper_precomputed_variants": {
        "BTC-USD": ("ahr999", "ahr999_365d_percentile", "ahr999_30d_slope"),
    },
    "research:ibit_btc_ahr999_mayer_precomputed": {
        "BTC-USD": ("ahr999", "mayer_multiple"),
    },
    "research:ibit_btc_ahr999_mayer_precomputed_variants": {
        "BTC-USD": ("ahr999", "ahr999_sma", "mayer_multiple"),
    },
}


_REQUIRED_PROVENANCE_FIELDS = frozenset(
    {
        "source_repo",
        "source_version",
        "code_commit",
        "provider",
        "provider_dataset",
        "raw_artifact_sha256",
        "transform",
        "license_scope",
        "generated_by",
    }
)
_FORBIDDEN_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "signed_url",
        "token",
    }
)
