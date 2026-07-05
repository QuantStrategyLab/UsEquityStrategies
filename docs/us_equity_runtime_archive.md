# US equity runtime archive

[简体中文](us_equity_runtime_archive.zh-CN.md)

_Last updated: 2026-06-28_

This index records current `runtime_enabled` US equity profiles and reviewable
evidence. It does not restate runtime contract details and does not mark profiles
without long-window summaries as fully archived.

## Archive overview

| Profile | Archive status | Reviewable evidence | CAGR | Max drawdown | Notes |
| --- | --- | --- | ---: | ---: | --- |
| `global_etf_rotation` | archived (threshold-4 recheck) | `UsEquitySnapshotPipelines/data/output/global_etf_rotation_threshold4_2026-05-04/summary.csv`, `docs/us_equity_strategy_status.md`, `docs/us_equity_contract_gap_matrix.md`, `UsEquitySnapshotPipelines/docs/operator_runbook.md` | 13.25% | -23.29% | Threshold-4 version retained after recheck; better drawdown than SPY with slightly higher CAGR. |
| `tqqq_growth_income` | archived | `docs/us_equity_strategy_status.md` | 33.96% | -31.48% | Best directly reviewable executable evidence currently available. |
| `soxl_soxx_trend_income` | archived | `UsEquitySnapshotPipelines/data/output/soxl_soxx_trend_income_archive_2026-05-04/summary.csv`, `docs/us_equity_value_mode_input_contract.md`, `docs/us_equity_contract_gap_matrix.md` | 98.03% | -39.29% | Daily replay with 100k initial equity and 5 bps cost; income sleeve joins later in sample. |
| `russell_1000_multi_factor_defensive` | archived | `UsEquitySnapshotPipelines/data/output/russell_1000_multi_factor_defensive_archive_2026-05-04/summary.csv`, release status artifacts, operator runbook | 16.64% | -27.62% | Full backtest output available for like-for-like comparison. |
| `russell_top50_leader_rotation` | archived | concentration variant summary, release status artifacts | 36.41% | -30.56% | Current retained unlevered leader-rotation line. |

## Research retained, not live

These profiles keep implementation, contracts, or legacy aliases for manual replay,
but are not in the `runtime_enabled` live list.

| Profile | Status | Key result | Reason |
| --- | --- | --- | --- |
| `tecl_xlk_trend_income` | research_backtest_only | 2024+ CAGR 24.8%, max drawdown -46.0%; did not beat SOXL (172% / -34%) or TQQQ live proxy. | Technology 3x transplant failed promotion gate; code, backtest entrypoints, and research artifacts retained only. See `UsEquitySnapshotPipelines/docs/tecl-xlk-optimization-research.md`. |
| `tech_communication_pullback_enhancement` | research_backtest_only | CAGR 24.31%, max drawdown -30.84%. | Sector-limited tech/communication sleeve underperforms `russell_top50_leader_rotation` without better drawdown. |

## Removed legacy research

These names are no longer valid `STRATEGY_PROFILE` values; offline evidence remains.

| Removed profile | Status | Key result |
| --- | --- | --- |
| `mega_cap_leader_rotation_dynamic_top20` | removed | CAGR 21.51%, max drawdown -23.14%. |
| `dynamic_mega_leveraged_pullback` | removed | CAGR 30.96%, max drawdown -34.80%. |

## Archive definitions

- `archived` means a directly reviewable long-window summary or complete offline research output exists in-repo.
- `runtime-ready, performance archive pending` means contracts and entrypoints exist but no unified long backtest summary yet.
- `long summary pending` means strategy logic exists but final reviewable performance archive is still missing.
- Runtime contracts and backtest evidence are separate concerns.
