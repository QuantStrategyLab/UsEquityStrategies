# US equity strategy status and research handbook

[简体中文](us_equity_strategy_status.zh-CN.md)

_Last updated: 2026-06-28_

This document records configurable US equity strategy profiles, input shapes, and
research status. It does not record which profile any live account is running.

Strategy code lives in `UsEquityStrategies`; feature snapshots, research backtests,
and plugin artifacts live in `UsEquitySnapshotPipelines`; broker connectivity,
execution, notifications, and logs live in platform repositories.

Full archive index: [us_equity_runtime_archive.md](./us_equity_runtime_archive.md).

## Current `runtime_enabled` profiles

| Profile | Role | Input type | Notes |
| --- | --- | --- | --- |
| `global_etf_rotation` | Global ETF defensive rotation | direct runtime | Quarterly Top2 rotation with SMA250 confidence and relative-vol gate. |
| `tqqq_growth_income` | TQQQ growth + income | direct runtime | QQQ/TQQQ dual drive with environment retention via `market_regime_control`. |
| `soxl_soxx_trend_income` | SOXL/SOXX semiconductor trend + income | direct runtime | SOXX trend gate, dynamic vol delever, income sleeve. |
| `nasdaq_sp500_smart_dca` | Nasdaq / S&P smart DCA | direct runtime | Buy-only scheduled DCA. |
| `ibit_smart_dca` | IBIT Bitcoin ETF DCA | direct runtime | Buy-only scheduled DCA with optional smart sizing. |
| `russell_top50_leader_rotation` | Russell Top50 leader rotation | feature snapshot | Fixed Top2/Top4 blend; no default trend de-risk. |

See the Chinese handbook for localized positioning text and default parameter tables.

## Current live risk-budget stance

- Keep `soxl_soxx_trend_income` live as the high-return semiconductor engine; do not downgrade it to shadow/research solely because of drawdown risk.
- Keep `tqqq_growth_income`, `russell_top50_leader_rotation`, and `global_etf_rotation` live.
- Treat `us_equity_combo_leveraged_shadow_402040` as a replacement candidate for part of the existing TQQQ/SOXL exposure, not an additive live layer.
- Do not promote TECL, CN combo, or HK combo profiles to live without separate evidence and manual approval.

## Research retained, not `runtime_enabled`

| Profile / track | Status | Why not live |
| --- | --- | --- |
| `tecl_xlk_trend_income` | `research_backtest_only` | Failed promotion versus live TQQQ and SOXL on overlapping windows (2024+ CAGR 24.8%, max drawdown -46.0%). Code and backtest tooling retained. Docs: [`UsEquitySnapshotPipelines/docs/tecl-xlk-optimization-research.md`](../../UsEquitySnapshotPipelines/docs/tecl-xlk-optimization-research.md). |
| `tech_communication_pullback_enhancement` | research_only | Underperforms `russell_top50_leader_rotation` on return and drawdown. |
| `QQQ` / `SPY` LEAPS growth overlay | research_only | Design: [`research/index_leaps_growth_overlay.md`](./research/index_leaps_growth_overlay.md). Proxy backtest module: `UsEquitySnapshotPipelines/docs/index-leaps-growth-overlay-research.md`. |
| `us_equity_combo_leveraged_shadow_352045` | shadow_candidate | TQQQ 35% / SOXL 20% / BOXX 45% with `hard_defense_risk_exposure=0.0`. Backtest evidence: 22.21% CAGR, -33.88% max drawdown over 2010-01-05 to 2026-07-02 with 5 bps cost. Runtime config: `src/us_equity_strategies/configs/us_equity_combo_leveraged_shadow_352045.json`. |
| `us_equity_combo_leveraged_shadow_402040` | shadow_candidate | TQQQ 40% / SOXL 20% / BOXX 40% with `hard_defense_risk_exposure=0.0`. Backtest evidence: 23.81% CAGR, -35.53% max drawdown over 2010-01-05 to 2026-07-02 with 5 bps cost. Runtime config: `src/us_equity_strategies/configs/us_equity_combo_leveraged_shadow_402040.json`. |
| `crisis_response_shadow` plugin | shadow candidate | Defense-only observation; no allocation impact. |

## Promotion principles

- A switchable profile is not automatically production-ready.
- Do not cite performance without archived artifacts in-repo.
- Small accounts should review integer-share drift.
- Plugins remain sidecars unless explicitly promoted with evidence.
- `shadow` does not trade; `live` affects execution only under platform risk controls.

For removed legacy profiles, income-layer defaults, and the full backtest table,
use the [Chinese handbook](us_equity_strategy_status.zh-CN.md).
