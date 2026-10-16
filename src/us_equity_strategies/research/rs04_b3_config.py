"""Single, immutable preregistration for rs04-b3-profit-regime-v1.

Owner decisions override the Phase-1 draft. No CLI parameter search is exposed.
D10/D11/D12 proposals are PROVISIONAL, not historical-data qualification.
"""
from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class Registration:
    candidate: str = "rs04-b3-profit-regime-v1"
    seed: int = 20261015
    annual_sessions: int = 252
    atr_period: int = 14
    adx_period: int = 14
    ma_period: int = 200
    slope_period: int = 20
    rv_period: int = 20
    percentile_period: int = 252
    rsi_period: int = 2
    trend_enter_adx: float = 25.
    trend_exit_adx: float = 20.
    trend_confirm: int = 3
    range_enter_adx: float = 18.
    range_exit_adx: float = 22.
    range_confirm: int = 5
    exit_confirm: int = 3
    min_hold: int = 5
    range_slope: float = .02
    range_rv_percentile: float = .8
    risk_ma_ratio: float = .98
    entry_atr_r: float = 2.
    trail_activation_r: float = 2.
    trail_atr: float = 3.
    cooldown: int = 5
    mr_cap: float = .10
    mr_loss_budget: float = .0025
    mr_enter_rsi: float = 10.
    mr_exit_rsi: float = 60.
    mr_max_hold: int = 5
    kelly_c: float = .25
    ewma_lambda: float = .94
    ewma_warmup: int = 63
    target_vol: float = .20
    dd_full: float = .10
    dd_half: float = .20
    dd_max: float = .20
    soxl_total: float = .97
    soxl_single: float = .679
    soxl_effective: float = 2.328
    tqqq_total: float = .98
    initial_usd: float = 100_000.
    commission_bps: float = 5.
    slippage_including_spread_bps: float = 10.
    minimum_commission_usd: float = 1.
    commission_per_share_usd: float = .005
    cost_multipliers: tuple = (1., 2.)
    fee_status: str = "PROVISIONAL_D_UNSPEC_D10_SELL_REGULATORY_FEE_UNSPECIFIED"
    source: str = "alpaca_sip_raw_bars_plus_corporate_actions"
    source_status: str = "PROVISIONAL_D_UNSPEC_D11_PIT_NOT_ATTESTED"
    development_start: str = "2023-03-28"
    development_end: str = "2026-08-25"
    holdout_start: str = "2026-11-01"
    holdout_end: str = "2028-10-31"
    holdout_status: str = "PROVISIONAL_D_UNSPEC_D12_NOT_YET_OBSERVABLE"
    train: int = 504
    validation: int = 126
    test: int = 126
    purge: int = 5
    embargo: int = 5
    min_folds: int = 4
    giveback_block: int = 63
    giveback_min_peak: float = .02
    giveback_min_blocks: int = 6
    min_mr_episodes: int = 30
    cagr_retention: float = .90
    material_mdd_reduction: float = .20
    material_giveback_reduction: float = .20
    stress_giveback_reduction: float = .10
    max_turnover_ratio: float = 1.5
    bootstrap_samples: int = 5000
    bootstrap_block: int = 20
    timing: str = "t_minus_1_completed_close_decision_t_close_fill_t_plus_1_earn"


CONFIG = Registration()


def config_identity() -> str:
    return hashlib.sha256(json.dumps(asdict(CONFIG), sort_keys=True).encode()).hexdigest()
