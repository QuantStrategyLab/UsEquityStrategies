from __future__ import annotations

from collections.abc import Iterable

from quant_platform_kit.common.strategies import (
    StrategyCatalog,
    StrategyComponentDefinition,
    StrategyDefinition,
    StrategyEntrypointDefinition,
    StrategyMetadata,
    US_EQUITY_DOMAIN,
    build_strategy_catalog,
    build_strategy_index_rows,
    get_catalog_compatible_platforms,
    get_catalog_strategy_definition,
    get_catalog_strategy_metadata,
    load_strategy_entrypoint,
    normalize_profile_name as qpk_normalize_profile_name,
)

from .ai_extensions import build_default_ai_extension_config
from .income_layer_defaults import income_layer_default_config

GLOBAL_ETF_ROTATION_PROFILE = "global_etf_rotation"
# Legacy alias retained for lookups and docs; runtime registry is canonical rotation.
GLOBAL_ETF_CONFIDENCE_VOL_GATE_PROFILE = "global_etf_confidence_vol_gate"
TQQQ_GROWTH_INCOME_PROFILE = "tqqq_growth_income"
SOXL_SOXX_TREND_INCOME_PROFILE = "soxl_soxx_trend_income"
RUSSELL_TOP50_LEADER_ROTATION_PROFILE = "russell_top50_leader_rotation"
NASDAQ_SP500_SMART_DCA_PROFILE = "nasdaq_sp500_smart_dca"
IBIT_SMART_DCA_PROFILE = "ibit_smart_dca"
SMART_DCA_RUNTIME_DEFAULT_CONTRACT_SCHEMA_VERSION = (
    "smart_dca_runtime_default_contract.v1"
)
FULL_SHARED_PLATFORM_MATRIX = frozenset(
    {"ibkr", "schwab", "longbridge", "firstrade", "paper_signal"}
)


STRATEGY_PLATFORM_COMPATIBILITY: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    TQQQ_GROWTH_INCOME_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    SOXL_SOXX_TREND_INCOME_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    NASDAQ_SP500_SMART_DCA_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
    IBIT_SMART_DCA_PROFILE: FULL_SHARED_PLATFORM_MATRIX,
}

STRATEGY_REQUIRED_INPUTS: dict[str, frozenset[str]] = {
    GLOBAL_ETF_ROTATION_PROFILE: frozenset({"feature_snapshot"}),
    TQQQ_GROWTH_INCOME_PROFILE: frozenset({"benchmark_history", "portfolio_snapshot"}),
    SOXL_SOXX_TREND_INCOME_PROFILE: frozenset({"derived_indicators", "portfolio_snapshot"}),
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: frozenset({"feature_snapshot"}),
    NASDAQ_SP500_SMART_DCA_PROFILE: frozenset({"market_history", "portfolio_snapshot"}),
    IBIT_SMART_DCA_PROFILE: frozenset({"derived_indicators", "portfolio_snapshot"}),
}
SMART_DCA_RUNTIME_DEFAULT_CONTRACT_PROFILES = (
    NASDAQ_SP500_SMART_DCA_PROFILE,
    IBIT_SMART_DCA_PROFILE,
)
SMART_DCA_RUNTIME_DEFAULT_REQUIRED_VALUES = {
    "base_investment_usd": 1000.0,
    "investment_amount_mode": "fixed",
    "smart_multiplier_enabled": False,
    "cadence": "monthly",
    "cash_reserve_usd": 0.0,
    "max_investment_usd": None,
    "min_investment_usd": 5.0,
    "monthly_day": 25,
    "monthly_window_calendar_days": 5,
    "weekly_day": 4,
    "weekly_window_calendar_days": 4,
    "quarterly_months": (1, 4, 7, 10),
    "quarterly_day": 25,
    "quarterly_window_calendar_days": 5,
    "execution_cash_reserve_ratio": 0.0,
    "execution_rebalance_threshold_ratio": 0.0,
}
SMART_DCA_RUNTIME_DEFAULT_REQUIRED_INPUTS = {
    NASDAQ_SP500_SMART_DCA_PROFILE: frozenset(
        {"market_history", "portfolio_snapshot"}
    ),
    IBIT_SMART_DCA_PROFILE: frozenset(
        {"derived_indicators", "portfolio_snapshot"}
    ),
}

STRATEGY_DEFAULT_CONFIG: dict[str, dict[str, object]] = {
    GLOBAL_ETF_ROTATION_PROFILE: {
        "ranking_pool": (
            "EWY", "EWT", "INDA", "FXI", "EWJ", "VGK", "VOO", "XLK", "SMH", "GLD",
            "SLV", "USO", "DBA", "XLE", "XLF", "ITA", "XLP", "XLU", "XLV", "IHI", "VNQ", "KRE",
        ),
        "canary_assets": ("SPY", "EFA", "EEM", "AGG"),
        "safe_haven": "BIL",
        "top_n": 2,
        "hold_bonus": 0.02,
        "canary_bad_threshold": 4,
        "rebalance_months": (3, 6, 9, 12),
        "sma_period": 250,
        "confidence_weighting_enabled": True,
        "confidence_metric": "z_gap",
        "confidence_threshold": 1.0,
        "confidence_top1_weight": 0.75,
        "confidence_volatility_gate_enabled": True,
        "confidence_volatility_window": 126,
        "confidence_volatility_max_ratio": 1.3,
        **income_layer_default_config(GLOBAL_ETF_ROTATION_PROFILE),
        "market_regime_control_enabled": True,
        "market_regime_control_apply_risk_reduced": False,
        "market_regime_control_apply_risk_off": False,
        "market_regime_control_risk_reduced_scalar": 0.50,
        "market_regime_control_risk_off_scalar": 0.0,
    },
    TQQQ_GROWTH_INCOME_PROFILE: {
        "benchmark_symbol": "QQQ",
        "managed_symbols": ("TQQQ", "QQQM", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
        "income_threshold_usd": 250000.0,
        "qqqi_income_ratio": 0.10,
        "cash_reserve_ratio": 0.02,
        "rebalance_threshold_ratio": 0.01,
        "execution_cash_reserve_ratio": 0.0,
        **income_layer_default_config(TQQQ_GROWTH_INCOME_PROFILE),
        "option_growth_overlay_enabled": True,
        "option_growth_overlay_recipe": "tqqq_leaps_growth_v1",
        "option_growth_overlay_start_usd": 250000.0,
        "option_growth_overlay_nav_budget_ratio": 0.03,
        "attack_allocation_mode": "fixed_qqq_tqqq_pullback",
        "dual_drive_qqq_weight": 0.45,
        "dual_drive_tqqq_weight": 0.45,
        "dual_drive_unlevered_symbol": "QQQM",
        "dual_drive_cash_reserve_ratio": 0.02,
        "dual_drive_allow_pullback": True,
        "dual_drive_require_ma20_slope": True,
        "dual_drive_pullback_rebound_window": 20,
        "dual_drive_pullback_rebound_threshold_mode": "volatility_scaled",
        "dual_drive_pullback_rebound_threshold": 0.0,
        "dual_drive_pullback_rebound_volatility_multiplier": 2.0,
        "dual_drive_volatility_delever_enabled": True,
        "dual_drive_volatility_delever_window": 5,
        "dual_drive_volatility_delever_threshold": 0.28,
        "dual_drive_volatility_delever_exit_threshold": 0.28,
        "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
        "dual_drive_volatility_delever_dynamic_lookback": 252,
        "dual_drive_volatility_delever_dynamic_percentile": 0.90,
        "dual_drive_volatility_delever_dynamic_min_periods": 126,
        "dual_drive_volatility_delever_dynamic_floor": 0.24,
        "dual_drive_volatility_delever_dynamic_cap": 0.36,
        "dual_drive_volatility_delever_taco_veto_enabled": True,
        "dual_drive_volatility_delever_retention_mode": "environment",
        "dual_drive_volatility_delever_retention_ratio": 0.0,
        "dual_drive_volatility_delever_retention_policy": "tqqq_step_softzero_0.25_0.50",
        "dual_drive_volatility_delever_retention_context_required": True,
        "dual_drive_volatility_delever_max_retention_ratio": 0.50,
        "dual_drive_macro_risk_governor_enabled": True,
        "dual_drive_crisis_defense_enabled": True,
        "market_regime_control_enabled": True,
        "ai_extensions": build_default_ai_extension_config(),
    },
    SOXL_SOXX_TREND_INCOME_PROFILE: {
        "managed_symbols": ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
        "trend_ma_window": 140,
        "cash_reserve_ratio": 0.03,
        "min_trade_ratio": 0.01,
        "min_trade_floor": 100.0,
        "rebalance_threshold_ratio": 0.01,
        **income_layer_default_config(SOXL_SOXX_TREND_INCOME_PROFILE),
        "option_income_overlay_enabled": True,
        "option_income_overlay_recipe": "soxx_put_credit_spread_income_v1",
        "option_income_overlay_start_usd": 1000000.0,
        "option_income_overlay_nav_risk_ratio": 0.01,
        "trend_entry_buffer": 0.08,
        "trend_mid_buffer": 0.06,
        "trend_exit_buffer": 0.02,
        "attack_allocation_mode": "soxx_gate_tiered_blend",
        "blend_gate_trend_source": "SOXX",
        "blend_gate_soxl_weight": 0.70,
        "blend_gate_mid_soxl_weight": 0.65,
        "blend_gate_active_soxx_weight": 0.20,
        "blend_gate_defensive_soxx_weight": 0.15,
        "blend_gate_rsi_cap_enabled": True,
        "blend_gate_rsi_threshold": 70.0,
        "blend_gate_dynamic_rsi_threshold_enabled": True,
        "blend_gate_bollinger_cap_enabled": True,
        "blend_gate_overlay_stack_triggers": True,
        "blend_gate_volatility_delever_enabled": True,
        "blend_gate_volatility_delever_symbol": "SOXX",
        "blend_gate_volatility_delever_window": 10,
        "blend_gate_volatility_delever_threshold": 0.55,
        "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
        "blend_gate_volatility_delever_dynamic_lookback": 252,
        "blend_gate_volatility_delever_dynamic_percentile": 0.95,
        "blend_gate_volatility_delever_dynamic_min_periods": 126,
        "blend_gate_volatility_delever_dynamic_floor": 0.50,
        "blend_gate_volatility_delever_dynamic_cap": 0.75,
        "blend_gate_volatility_delever_retention_ratio": 0.0,
        "blend_gate_volatility_delever_retention_mode": "environment",
        "blend_gate_volatility_delever_retention_policy": "soxl_step_rebound_0.25_0.50",
        "blend_gate_volatility_delever_retention_context_required": True,
        "blend_gate_volatility_delever_max_retention_ratio": 0.50,
        "blend_gate_volatility_delever_redirect_symbol": "SOXX",
        "market_regime_control_enabled": True,
        "market_regime_control_apply_risk_reduced": False,
        "market_regime_control_apply_risk_off": True,
    },
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: {
        "benchmark_symbol": "QQQ",
        "broad_benchmark_symbol": "SPY",
        "safe_haven": "BOXX",
        "dynamic_universe_size": 50,
        "blend_sleeves": (
            {"name": "top2_cap50", "weight": 0.50, "holdings_count": 2, "single_name_cap": 0.50},
            {"name": "top4_cap25", "weight": 0.50, "holdings_count": 4, "single_name_cap": 0.25},
        ),
        "holdings_count": 4,
        "single_name_cap": 0.25,
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
        **income_layer_default_config(RUSSELL_TOP50_LEADER_ROTATION_PROFILE),
        "option_growth_overlay_enabled": True,
        "option_growth_overlay_recipe": "qqq_leaps_growth_v1",
        "option_growth_overlay_start_usd": 1000000.0,
        "option_growth_overlay_nav_budget_ratio": 0.03,
        "market_regime_control_enabled": True,
        "market_regime_control_apply_risk_reduced": False,
        "market_regime_control_apply_risk_off": False,
        "market_regime_control_risk_reduced_scalar": 0.50,
        "market_regime_control_risk_off_scalar": 0.0,
    },
    NASDAQ_SP500_SMART_DCA_PROFILE: {
        "signal_symbols": ("QQQ", "SPY"),
        "trade_allocations": {
            "QQQM": 0.50,
            "SPLG": 0.50,
        },
        "managed_symbols": ("QQQM", "SPLG"),
        "base_investment_usd": 1000.0,
        "max_investment_usd": None,
        "cash_reserve_usd": 0.0,
        "min_investment_usd": 5.0,
        "investment_amount_mode": "fixed",
        "smart_multiplier_enabled": False,
        "cadence": "monthly",
        "monthly_day": 25,
        "monthly_window_calendar_days": 5,
        "weekly_day": 4,
        "weekly_window_calendar_days": 4,
        "quarterly_months": (1, 4, 7, 10),
        "quarterly_day": 25,
        "quarterly_window_calendar_days": 5,
        "mild_drawdown_threshold": 0.08,
        "deep_drawdown_threshold": 0.15,
        "severe_drawdown_threshold": 0.25,
        "mild_discount_gap": 0.05,
        "deep_discount_gap": 0.10,
        "expensive_gap": 0.12,
        "very_expensive_gap": 0.20,
        "shallow_drawdown_threshold": 0.03,
        "overbought_rsi": 70.0,
        "base_multiplier": 1.0,
        "mild_pullback_multiplier": 1.10,
        "deep_pullback_multiplier": 1.25,
        "severe_pullback_multiplier": 1.50,
        "expensive_multiplier": 1.0,
        "very_expensive_multiplier": 1.0,
        "execution_cash_reserve_ratio": 0.0,
        "execution_rebalance_threshold_ratio": 0.0,
    },
    IBIT_SMART_DCA_PROFILE: {
        "signal_symbols": ("BTC-USD",),
        "trade_allocations": {
            "IBIT": 1.0,
        },
        "managed_symbols": ("IBIT",),
        "base_investment_usd": 1000.0,
        "max_investment_usd": None,
        "cash_reserve_usd": 0.0,
        "min_investment_usd": 5.0,
        "investment_amount_mode": "fixed",
        "smart_multiplier_enabled": False,
        "cadence": "monthly",
        "monthly_day": 25,
        "monthly_window_calendar_days": 5,
        "weekly_day": 4,
        "weekly_window_calendar_days": 4,
        "quarterly_months": (1, 4, 7, 10),
        "quarterly_day": 25,
        "quarterly_window_calendar_days": 5,
        "mild_drawdown_threshold": 0.12,
        "deep_drawdown_threshold": 0.25,
        "severe_drawdown_threshold": 0.40,
        "mild_discount_gap": 0.08,
        "deep_discount_gap": 0.18,
        "expensive_gap": 0.30,
        "very_expensive_gap": 0.60,
        "shallow_drawdown_threshold": 0.05,
        "overbought_rsi": 75.0,
        "base_multiplier": 1.0,
        "mild_pullback_multiplier": 1.50,
        "deep_pullback_multiplier": 2.25,
        "severe_pullback_multiplier": 3.0,
        "expensive_multiplier": 1.0,
        "very_expensive_multiplier": 1.0,
        "cycle_indicator_enabled": True,
        "ahr999_bottom_threshold": 0.45,
        "ahr999_accumulation_threshold": 0.80,
        "ahr999_dca_threshold": 1.20,
        "ahr999_bottom_multiplier": 3.0,
        "ahr999_accumulation_multiplier": 2.25,
        "ahr999_dca_multiplier": 1.50,
        "ahr999_expensive_multiplier": 0.0,
        "execution_cash_reserve_ratio": 0.0,
        "execution_rebalance_threshold_ratio": 0.0,
    },
}

STRATEGY_ENTRYPOINT_ATTRIBUTES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "global_etf_rotation_entrypoint",
    TQQQ_GROWTH_INCOME_PROFILE: "tqqq_growth_income_entrypoint",
    SOXL_SOXX_TREND_INCOME_PROFILE: "soxl_soxx_trend_income_entrypoint",
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: "russell_top50_leader_rotation_entrypoint",
    NASDAQ_SP500_SMART_DCA_PROFILE: "nasdaq_sp500_smart_dca_entrypoint",
    IBIT_SMART_DCA_PROFILE: "ibit_smart_dca_entrypoint",
}

STRATEGY_TARGET_MODES: dict[str, str] = {
    GLOBAL_ETF_ROTATION_PROFILE: "weight",
    TQQQ_GROWTH_INCOME_PROFILE: "value",
    SOXL_SOXX_TREND_INCOME_PROFILE: "value",
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: "weight",
    NASDAQ_SP500_SMART_DCA_PROFILE: "value",
    IBIT_SMART_DCA_PROFILE: "value",
}

STRATEGY_BUNDLED_CONFIG_RELPATHS: dict[str, str] = {}


# `supported_platforms` 仍保留为兼容镜像，避免一次性改动所有平台 runtime。
# 平台真正的启用状态由各自 runtime 仓库维护；UES 这里只表达策略层兼容性。
def _build_strategy_definition(
    profile: str,
    *,
    component_name: str,
    module_path: str,
) -> StrategyDefinition:
    return StrategyDefinition(
        profile=profile,
        domain=US_EQUITY_DOMAIN,
        supported_platforms=STRATEGY_PLATFORM_COMPATIBILITY[profile],
        components=(
            StrategyComponentDefinition(
                name=component_name,
                module_path=module_path,
            ),
        ),
        entrypoint=StrategyEntrypointDefinition(
            module_path="us_equity_strategies.entrypoints",
            attribute_name=STRATEGY_ENTRYPOINT_ATTRIBUTES[profile],
        ),
        required_inputs=STRATEGY_REQUIRED_INPUTS[profile],
        default_config=STRATEGY_DEFAULT_CONFIG[profile],
        target_mode=STRATEGY_TARGET_MODES[profile],
        bundled_config_relpath=STRATEGY_BUNDLED_CONFIG_RELPATHS.get(profile),
    )


STRATEGY_DEFINITIONS: dict[str, StrategyDefinition] = {
    GLOBAL_ETF_ROTATION_PROFILE: _build_strategy_definition(
        GLOBAL_ETF_ROTATION_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.global_etf_rotation",
    ),
    TQQQ_GROWTH_INCOME_PROFILE: _build_strategy_definition(
        TQQQ_GROWTH_INCOME_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.tqqq_growth_income",
    ),
    SOXL_SOXX_TREND_INCOME_PROFILE: _build_strategy_definition(
        SOXL_SOXX_TREND_INCOME_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.soxl_soxx_trend_income",
    ),
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: _build_strategy_definition(
        RUSSELL_TOP50_LEADER_ROTATION_PROFILE,
        component_name="signal_logic",
        module_path="us_equity_strategies.strategies.mega_cap_leader_rotation",
    ),
    NASDAQ_SP500_SMART_DCA_PROFILE: _build_strategy_definition(
        NASDAQ_SP500_SMART_DCA_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.nasdaq_sp500_smart_dca",
    ),
    IBIT_SMART_DCA_PROFILE: _build_strategy_definition(
        IBIT_SMART_DCA_PROFILE,
        component_name="allocation",
        module_path="us_equity_strategies.strategies.ibit_smart_dca",
    ),
}


STRATEGY_METADATA: dict[str, StrategyMetadata] = {
    GLOBAL_ETF_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=GLOBAL_ETF_ROTATION_PROFILE,
        display_name="Global ETF Rotation",
        description="Quarterly top-2 global ETF rotation backed by snapshot-side ETF universe evidence, with daily canary defense and BIL safe haven.",
        aliases=("global_macro_etf_rotation", GLOBAL_ETF_CONFIDENCE_VOL_GATE_PROFILE),
        cadence="quarterly + daily canary",
        asset_scope="global_etf_rotation_snapshot",
        benchmark="VOO",
        role="defensive_rotation",
        status="runtime_enabled",
    ),
    TQQQ_GROWTH_INCOME_PROFILE: StrategyMetadata(
        canonical_profile=TQQQ_GROWTH_INCOME_PROFILE,
        display_name="TQQQ Growth Income",
        description="QQQ-signal TQQQ/QQQM dual-drive growth profile with BOXX/cash defense and additive income sleeve.",
        aliases=(),
        cadence="daily",
        asset_scope="us_equity_qqq_tqqq_dual_drive",
        benchmark="QQQ",
        role="offensive_dual_drive",
        status="runtime_enabled",
    ),
    SOXL_SOXX_TREND_INCOME_PROFILE: StrategyMetadata(
        canonical_profile=SOXL_SOXX_TREND_INCOME_PROFILE,
        display_name="SOXL/SOXX Semiconductor Trend Income",
        description="SOXL / SOXX semiconductor trend switch with BOXX parking and additive income sleeve.",
        aliases=(),
        cadence="daily",
        asset_scope="semiconductor_etf_plus_income",
        benchmark="SOXX",
        role="sector_offensive_income",
        status="runtime_enabled",
    ),
    RUSSELL_TOP50_LEADER_ROTATION_PROFILE: StrategyMetadata(
        canonical_profile=RUSSELL_TOP50_LEADER_ROTATION_PROFILE,
        display_name="Russell Top50 Leader Rotation",
        description="Balanced monthly Russell Top50 leader rotation using a fixed 50% Top2 / 50% Top4 sleeve blend and no trend de-risking by default.",
        localized_display_names={"zh": "罗素 Top50 领涨轮动"},
        aliases=(),
        cadence="monthly",
        asset_scope="us_russell_top50_leader_rotation_stocks",
        benchmark="QQQ",
        role="aggressive_leader_rotation",
        status="runtime_enabled",
    ),
    NASDAQ_SP500_SMART_DCA_PROFILE: StrategyMetadata(
        canonical_profile=NASDAQ_SP500_SMART_DCA_PROFILE,
        display_name="Nasdaq 100 / S&P 500 Smart DCA",
        description="Buy-only Nasdaq 100 and S&P 500 fixed-amount DCA profile with optional smart sizing gates.",
        localized_display_names={"zh": "纳指100 / 标普500 智能定投"},
        aliases=(),
        cadence="monthly",
        asset_scope="nasdaq_100_sp500_etf_dca",
        benchmark="QQQ/SPY",
        role="buy_only_smart_dca",
        status="runtime_enabled",
    ),
    IBIT_SMART_DCA_PROFILE: StrategyMetadata(
        canonical_profile=IBIT_SMART_DCA_PROFILE,
        display_name="IBIT Smart DCA",
        description="Buy-only spot Bitcoin ETF DCA profile with optional pullback-aware smart sizing.",
        localized_display_names={"zh": "IBIT 比特币 ETF 智能定投"},
        aliases=("bitcoin_etf_smart_dca",),
        cadence="monthly",
        asset_scope="spot_bitcoin_etf_main_dca",
        benchmark="BTC",
        role="buy_only_bitcoin_etf_smart_dca",
        status="runtime_enabled",
    ),
}

PROFILE_ALIASES: dict[str, str] = {
    alias: metadata.canonical_profile
    for metadata in STRATEGY_METADATA.values()
    for alias in metadata.aliases
}

STRATEGY_CATALOG: StrategyCatalog = build_strategy_catalog(
    strategy_definitions=STRATEGY_DEFINITIONS,
    metadata=STRATEGY_METADATA,
    compatible_platforms=STRATEGY_PLATFORM_COMPATIBILITY,
    profile_aliases=PROFILE_ALIASES,
)


def normalize_profile_name(profile: str | None) -> str:
    return qpk_normalize_profile_name(profile)


def resolve_canonical_profile(profile: str | None) -> str:
    normalized = normalize_profile_name(profile)
    if not normalized:
        return normalized
    definition = get_catalog_strategy_definition(STRATEGY_CATALOG, normalized)
    return definition.profile


def get_strategy_definitions() -> dict[str, StrategyDefinition]:
    return dict(STRATEGY_DEFINITIONS)


def get_strategy_catalog() -> StrategyCatalog:
    return STRATEGY_CATALOG


def get_strategy_platform_compatibility_map() -> dict[str, frozenset[str]]:
    return dict(STRATEGY_PLATFORM_COMPATIBILITY)


def get_compatible_platforms(profile: str) -> frozenset[str]:
    return get_catalog_compatible_platforms(STRATEGY_CATALOG, profile)


def get_strategy_definition(profile: str) -> StrategyDefinition:
    return get_catalog_strategy_definition(STRATEGY_CATALOG, profile)


def get_strategy_entrypoint(profile: str):
    definition = get_strategy_definition(profile)
    metadata = get_strategy_metadata(profile)
    return load_strategy_entrypoint(definition, metadata=metadata)


def audit_smart_dca_runtime_default_contract(
    profiles: Iterable[str] = SMART_DCA_RUNTIME_DEFAULT_CONTRACT_PROFILES,
) -> dict[str, object]:
    """Return a machine-readable guard for production smart-DCA defaults."""

    selected_profiles = tuple(str(profile or "").strip() for profile in profiles)
    profile_contracts = tuple(
        _smart_dca_runtime_default_profile_contract(profile)
        for profile in selected_profiles
    )
    failure_reasons = tuple(
        reason
        for profile_contract in profile_contracts
        for reason in profile_contract["failure_reasons"]
    )
    return {
        "schema_version": SMART_DCA_RUNTIME_DEFAULT_CONTRACT_SCHEMA_VERSION,
        "artifact_type": "smart_dca_runtime_default_contract",
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "profiles": selected_profiles,
        "profile_contracts": profile_contracts,
    }


def _smart_dca_runtime_default_profile_contract(profile: str) -> dict[str, object]:
    definition = get_strategy_definition(profile)
    metadata = get_strategy_metadata(profile)
    config = definition.default_config
    expected_inputs = SMART_DCA_RUNTIME_DEFAULT_REQUIRED_INPUTS.get(
        profile,
        frozenset(),
    )
    failure_reasons: list[str] = []
    actual_values = {
        field: config.get(field)
        for field in SMART_DCA_RUNTIME_DEFAULT_REQUIRED_VALUES
    }
    for field, expected in SMART_DCA_RUNTIME_DEFAULT_REQUIRED_VALUES.items():
        if config.get(field) != expected:
            failure_reasons.append(f"{profile}:{field}_mismatch")
    if "available_cash_investment_ratio" in config:
        failure_reasons.append(f"{profile}:available_cash_ratio_enabled")
    if definition.target_mode != "value":
        failure_reasons.append(f"{profile}:target_mode_not_value")
    if definition.required_inputs != expected_inputs:
        failure_reasons.append(f"{profile}:required_inputs_mismatch")
    if metadata.status != "runtime_enabled":
        failure_reasons.append(f"{profile}:metadata_status_not_runtime_enabled")
    if metadata.cadence != "monthly":
        failure_reasons.append(f"{profile}:metadata_cadence_not_monthly")
    return {
        "profile": profile,
        "passed": not failure_reasons,
        "failure_reasons": tuple(failure_reasons),
        "required_values": dict(SMART_DCA_RUNTIME_DEFAULT_REQUIRED_VALUES),
        "actual_values": actual_values,
        "available_cash_ratio_absent": (
            "available_cash_investment_ratio" not in config
        ),
        "target_mode": definition.target_mode,
        "required_inputs": tuple(sorted(definition.required_inputs)),
        "expected_required_inputs": tuple(sorted(expected_inputs)),
        "metadata_status": metadata.status,
        "metadata_cadence": metadata.cadence,
    }


def get_strategy_index_rows() -> list[dict[str, object]]:
    return build_strategy_index_rows(STRATEGY_CATALOG)


def get_strategy_metadata_map() -> dict[str, StrategyMetadata]:
    return dict(STRATEGY_METADATA)


def get_runtime_enabled_profiles() -> frozenset[str]:
    return frozenset(
        profile
        for profile, metadata in STRATEGY_METADATA.items()
        if str(metadata.status or "").strip().lower() == "runtime_enabled"
    )


def get_strategy_metadata(profile: str) -> StrategyMetadata:
    return get_catalog_strategy_metadata(STRATEGY_CATALOG, profile)


def get_profile_aliases() -> dict[str, str]:
    return dict(PROFILE_ALIASES)
