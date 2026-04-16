from __future__ import annotations

from quant_platform_kit.strategy_contracts import StrategyManifest

from us_equity_strategies.strategies.dynamic_mega_leveraged_pullback import (
    DEFAULT_CONFIG as DYNAMIC_MEGA_LEVERAGED_PULLBACK_DEFAULT_CONFIG,
)


TECH_COMMUNICATION_PULLBACK_ENHANCEMENT_PROFILE = "tech_communication_pullback_enhancement"
MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE = "mega_cap_leader_rotation_dynamic_top20"
MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE = "mega_cap_leader_rotation_aggressive"
DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE = "dynamic_mega_leveraged_pullback"
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
    description="QQQ/TQQQ dual-drive growth profile with BOXX/cash defense; income sleeve remains opt-in.",
    aliases=(),
    required_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
    default_config={
        "benchmark_symbol": "QQQ",
        "managed_symbols": ("TQQQ", "QQQ", "BOXX", "SPYI", "QQQI"),
        "income_threshold_usd": 1_000_000_000.0,
        "qqqi_income_ratio": 0.5,
        "cash_reserve_ratio": 0.02,
        "rebalance_threshold_ratio": 0.01,
        "execution_cash_reserve_ratio": 0.0,
        "attack_allocation_mode": "fixed_qqq_tqqq_pullback",
        "dual_drive_qqq_weight": 0.45,
        "dual_drive_tqqq_weight": 0.45,
        "dual_drive_unlevered_symbol": "QQQ",
        "dual_drive_cash_reserve_ratio": 0.02,
        "dual_drive_allow_pullback": True,
        "dual_drive_require_ma20_slope": True,
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
        "trend_ma_window": 140,
        "cash_reserve_ratio": 0.03,
        "min_trade_ratio": 0.01,
        "min_trade_floor": 100.0,
        "rebalance_threshold_ratio": 0.01,
        "income_layer_start_usd": 150000.0,
        "income_layer_max_ratio": 0.15,
        "income_layer_qqqi_weight": 0.70,
        "income_layer_spyi_weight": 0.30,
        "trend_entry_buffer": 0.08,
        "trend_mid_buffer": 0.06,
        "trend_exit_buffer": 0.02,
        "attack_allocation_mode": "soxx_gate_tiered_blend",
        "blend_gate_trend_source": "SOXX",
        "blend_gate_soxl_weight": 0.70,
        "blend_gate_mid_soxl_weight": 0.65,
        "blend_gate_active_soxx_weight": 0.20,
        "blend_gate_defensive_soxx_weight": 0.15,
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

mega_cap_leader_rotation_dynamic_top20_manifest = _manifest(
    profile=MEGA_CAP_LEADER_ROTATION_DYNAMIC_TOP20_PROFILE,
    display_name="Mega Cap Leader Rotation Dynamic Top20",
    description="Monthly dynamic top-20 mega-cap leader rotation with QQQ trend defense and BOXX parking.",
    aliases=(),
    required_inputs=frozenset({"feature_snapshot"}),
    default_config={
        "benchmark_symbol": "QQQ",
        "broad_benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "dynamic_universe_size": 20,
        "holdings_count": 4,
        "single_name_cap": 0.25,
        "min_position_value_usd": 3000.0,
        "hold_buffer": 2,
        "hold_bonus": 0.10,
        "risk_on_exposure": 1.0,
        "soft_defense_exposure": 0.50,
        "hard_defense_exposure": 0.50,
        "soft_breadth_threshold": 0.0,
        "hard_breadth_threshold": 0.0,
        "min_adv20_usd": 20000000.0,
        "runtime_execution_window_trading_days": 3,
        "execution_cash_reserve_ratio": 0.0,
    },
)

mega_cap_leader_rotation_aggressive_manifest = _manifest(
    profile=MEGA_CAP_LEADER_ROTATION_AGGRESSIVE_PROFILE,
    display_name="Mega Cap Leader Rotation Aggressive",
    description="Aggressive mega-cap leader rotation using a larger/curated snapshot, top-3 concentration, and no trend de-risking by default.",
    aliases=(),
    required_inputs=frozenset({"feature_snapshot"}),
    default_config={
        "benchmark_symbol": "QQQ",
        "broad_benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "dynamic_universe_size": 50,
        "holdings_count": 3,
        "single_name_cap": 0.35,
        "min_position_value_usd": 3000.0,
        "hold_buffer": 2,
        "hold_bonus": 0.10,
        "risk_on_exposure": 1.0,
        "soft_defense_exposure": 1.0,
        "hard_defense_exposure": 1.0,
        "soft_breadth_threshold": 0.0,
        "hard_breadth_threshold": 0.0,
        "min_adv20_usd": 20000000.0,
        "runtime_execution_window_trading_days": 3,
        "execution_cash_reserve_ratio": 0.0,
    },
)

dynamic_mega_leveraged_pullback_manifest = _manifest(
    profile=DYNAMIC_MEGA_LEVERAGED_PULLBACK_PROFILE,
    display_name="Dynamic Mega Leveraged Pullback",
    description="Monthly dynamic mega-cap candidate snapshot with daily QQQ ATR/SMA gate and top-3 2x long pullback execution.",
    aliases=(),
    required_inputs=frozenset({"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}),
    default_config=dict(DYNAMIC_MEGA_LEVERAGED_PULLBACK_DEFAULT_CONFIG),
)

MANIFESTS = {
    global_etf_rotation_manifest.profile: global_etf_rotation_manifest,
    tqqq_growth_income_manifest.profile: tqqq_growth_income_manifest,
    soxl_soxx_trend_income_manifest.profile: soxl_soxx_trend_income_manifest,
    russell_1000_multi_factor_defensive_manifest.profile: russell_1000_multi_factor_defensive_manifest,
    qqq_tech_enhancement_manifest.profile: qqq_tech_enhancement_manifest,
    mega_cap_leader_rotation_dynamic_top20_manifest.profile: mega_cap_leader_rotation_dynamic_top20_manifest,
    mega_cap_leader_rotation_aggressive_manifest.profile: mega_cap_leader_rotation_aggressive_manifest,
    dynamic_mega_leveraged_pullback_manifest.profile: dynamic_mega_leveraged_pullback_manifest,
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
    "mega_cap_leader_rotation_dynamic_top20_manifest",
    "mega_cap_leader_rotation_aggressive_manifest",
    "dynamic_mega_leveraged_pullback_manifest",
]
