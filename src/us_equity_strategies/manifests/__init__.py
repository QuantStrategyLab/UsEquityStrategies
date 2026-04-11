from __future__ import annotations

from quant_platform_kit.strategy_contracts import StrategyManifest


TECH_COMMUNICATION_PULLBACK_ENHANCEMENT_PROFILE = "tech_communication_pullback_enhancement"
QQQ_TECH_ENHANCEMENT_LEGACY_PROFILE = "qqq_tech_enhancement"


def _manifest(
    *,
    profile: str,
    display_name: str,
    description: str,
    aliases: tuple[str, ...] = (),
    required_inputs: frozenset[str] = frozenset(),
    default_config: dict[str, object] | None = None,
) -> StrategyManifest:
    return StrategyManifest(
        profile=profile,
        domain="us_equity",
        display_name=display_name,
        description=description,
        aliases=aliases,
        required_inputs=required_inputs,
        default_config=default_config or {},
    )


global_etf_rotation_manifest = _manifest(
    profile="global_etf_rotation",
    display_name="Global ETF Rotation",
    description="Quarterly top-2 global ETF rotation with daily canary defense and BIL safe haven.",
    aliases=("global_macro_etf_rotation",),
    required_inputs=frozenset({"market_history"}),
    default_config={
        "ranking_pool": (
            "EWY",
            "EWT",
            "INDA",
            "FXI",
            "EWJ",
            "VGK",
            "VOO",
            "XLK",
            "SMH",
            "GLD",
            "SLV",
            "USO",
            "DBA",
            "XLE",
            "XLF",
            "ITA",
            "XLP",
            "XLU",
            "XLV",
            "IHI",
            "VNQ",
            "KRE",
        ),
        "canary_assets": ("SPY", "EFA", "EEM", "AGG"),
        "safe_haven": "BIL",
        "top_n": 2,
        "hold_bonus": 0.02,
        "canary_bad_threshold": 4,
        "rebalance_months": (3, 6, 9, 12),
        "sma_period": 200,
    },
)

tqqq_growth_income_manifest = _manifest(
    profile="tqqq_growth_income",
    display_name="TQQQ Growth Income",
    description="QQQ-led TQQQ attack sleeve with SPYI / QQQI income and BOXX defense.",
    aliases=(),
    required_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
    default_config={
        "benchmark_symbol": "QQQ",
        "managed_symbols": ("TQQQ", "BOXX", "SPYI", "QQQI"),
        "income_threshold_usd": 100000.0,
        "qqqi_income_ratio": 0.5,
        "cash_reserve_ratio": 0.05,
        "rebalance_threshold_ratio": 0.01,
        "alloc_tier1_breakpoints": (0, 15000, 30000, 70000),
        "alloc_tier1_values": (1.0, 0.95, 0.85, 0.70),
        "alloc_tier2_breakpoints": (70000, 140000),
        "alloc_tier2_values": (0.70, 0.50),
        "risk_leverage_factor": 3.0,
        "risk_agg_cap": 0.50,
        "risk_numerator": 0.30,
        "atr_exit_scale": 2.0,
        "atr_entry_scale": 2.5,
        "exit_line_floor": 0.92,
        "exit_line_cap": 0.98,
        "entry_line_floor": 1.02,
        "entry_line_cap": 1.08,
    },
)

soxl_soxx_trend_income_manifest = _manifest(
    profile="soxl_soxx_trend_income",
    display_name="SOXL/SOXX Semiconductor Trend Income",
    description="SOXL / SOXX semiconductor trend switch with BOXX parking and additive income sleeve.",
    aliases=(),
    required_inputs=frozenset({"derived_indicators", "portfolio_snapshot"}),
    default_config={
        "managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        "trend_ma_window": 150,
        "cash_reserve_ratio": 0.03,
        "min_trade_ratio": 0.01,
        "min_trade_floor": 100.0,
        "rebalance_threshold_ratio": 0.01,
        "small_account_deploy_ratio": 0.60,
        "mid_account_deploy_ratio": 0.57,
        "large_account_deploy_ratio": 0.50,
        "trade_layer_decay_coeff": 0.04,
        "income_layer_start_usd": 150000.0,
        "income_layer_max_ratio": 0.15,
        "income_layer_qqqi_weight": 0.70,
        "income_layer_spyi_weight": 0.30,
    },
)

russell_1000_multi_factor_defensive_manifest = _manifest(
    profile="russell_1000_multi_factor_defensive",
    display_name="Russell 1000 Multi-Factor",
    description="Monthly price-only Russell 1000 stock selection with SPY+breadth defense and BOXX parking.",
    aliases=("r1000_multifactor_defensive",),
    required_inputs=frozenset({"feature_snapshot"}),
    default_config={
        "benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "holdings_count": 24,
        "single_name_cap": 0.06,
        "sector_cap": 0.20,
        "hold_bonus": 0.15,
        "soft_defense_exposure": 0.50,
        "hard_defense_exposure": 0.10,
        "soft_breadth_threshold": 0.55,
        "hard_breadth_threshold": 0.35,
    },
)

qqq_tech_enhancement_manifest = _manifest(
    profile=TECH_COMMUNICATION_PULLBACK_ENHANCEMENT_PROFILE,
    display_name="Tech/Communication Pullback Enhancement",
    description="Tech-heavy monthly stock selection with controlled pullback entry and explicit BOXX cash buffer.",
    aliases=(QQQ_TECH_ENHANCEMENT_LEGACY_PROFILE,),
    required_inputs=frozenset({"feature_snapshot"}),
    default_config={
        "benchmark_symbol": "QQQ",
        "safe_haven": "BOXX",
        "holdings_count": 8,
        "single_name_cap": 0.10,
        "sector_cap": 0.40,
        "min_position_value_usd": 3000.0,
        "max_dynamic_single_name_cap": 0.40,
        "max_dynamic_sector_cap": 0.60,
        "hold_bonus": 0.10,
        "risk_on_exposure": 0.80,
        "soft_defense_exposure": 0.60,
        "hard_defense_exposure": 0.00,
        "soft_breadth_threshold": 0.55,
        "hard_breadth_threshold": 0.35,
        "min_adv20_usd": 50000000.0,
        "sector_whitelist": ("Information Technology", "Communication"),
        "normalization": "universe_cross_sectional",
        "score_template": "balanced_pullback",
        "runtime_execution_window_trading_days": 3,
        "execution_cash_reserve_ratio": 0.0,
        "residual_proxy": "simple_excess_return_vs_QQQ",
    },
)

MANIFESTS = {
    global_etf_rotation_manifest.profile: global_etf_rotation_manifest,
    tqqq_growth_income_manifest.profile: tqqq_growth_income_manifest,
    soxl_soxx_trend_income_manifest.profile: soxl_soxx_trend_income_manifest,
    russell_1000_multi_factor_defensive_manifest.profile: russell_1000_multi_factor_defensive_manifest,
    qqq_tech_enhancement_manifest.profile: qqq_tech_enhancement_manifest,
}

MANIFEST_ALIASES = {
    str(alias).strip().lower(): manifest.profile
    for manifest in MANIFESTS.values()
    for alias in manifest.aliases
}


def get_strategy_manifest(profile: str) -> StrategyManifest:
    normalized = str(profile or "").strip().lower()
    return MANIFESTS[MANIFEST_ALIASES.get(normalized, normalized)]


__all__ = [
    "MANIFESTS",
    "get_strategy_manifest",
    "global_etf_rotation_manifest",
    "tqqq_growth_income_manifest",
    "soxl_soxx_trend_income_manifest",
    "qqq_tech_enhancement_manifest",
    "russell_1000_multi_factor_defensive_manifest",
]
