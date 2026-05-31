# US equity contract gap matrix


## 中文摘要

- 用途：本文档围绕 `US equity contract gap matrix`，用于理解 `UsEquityStrategies` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Current Runtime Scope`、`Removed Research Profile Exposure`、`Canonical Input Vocabulary`、`Profile Matrix`、`Current Conclusions`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
_Updated: 2026-05-26_

This document tracks the current shared US equity strategy contract across
`UsEquityStrategies`, `QuantPlatformKit`, and the platform runtimes.

It records runtime portability only. Research candidates that compare worse
than a runtime-enabled peer are not kept as deployable or replayable profile
entries in this matrix.

## Current Runtime Scope

The current runtime-enabled US equity profiles are:

- `global_etf_rotation`
- `tqqq_growth_income`
- `soxl_soxx_trend_income`
- `russell_1000_multi_factor_defensive`
- `tech_communication_pullback_enhancement`
- `mega_cap_leader_rotation_top50_balanced`
- `nasdaq_sp500_smart_dca`

`global_etf_confidence_vol_gate` is a legacy alias that resolves to `global_etf_rotation` and does not appear as a separate runtime-enabled row.

These profiles are designed against the shared contract and are portable across
the current US equity platform IDs:

- `ibkr`
- `schwab`
- `longbridge`
- `firstrade`
- `paper_signal`

`firstrade` is a broker runtime backed by an unofficial reverse-engineered
client. Its strategy contract remains the same as other broker runtimes;
broker authentication and API risk are owned by `FirstradePlatform`.

`paper_signal` is brokerless and publishes signal notifications only. The
strategy contract is still shared; broker execution remains platform-specific.

## Removed Research Profile Exposure

The following research/profile exposures were removed from catalog, manifest,
entrypoint, runtime adapter, publish-window, and platform rollout surfaces after
comparison with runtime-enabled peers:

- `mega_cap_leader_rotation_dynamic_top20`
- `mega_cap_leader_rotation_aggressive`
- `dynamic_mega_leveraged_pullback`

The first two were superseded by `mega_cap_leader_rotation_top50_balanced`.
The 2x dynamic mega-cap/MAGS route added more product and input complexity
without a better promoted profile result.

Historical output files can still be inspected in research directories, but
these names are no longer valid `STRATEGY_PROFILE` values and should not appear
as platform-enabled or replay-adapter rows.

## Canonical Input Vocabulary

New US equity profiles should use only these canonical `required_inputs`:

- `market_history`
- `benchmark_history`
- `portfolio_snapshot`
- `derived_indicators`
- `feature_snapshot`

## Profile Matrix

| Profile | `target_mode` | `required_inputs` | Adapter coverage | Runtime status | Notes |
| --- | --- | --- | --- | --- | --- |
| `global_etf_rotation` | `weight` | `market_history` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Quarterly top-2 ETF rotation with daily canary defense. |
| `tqqq_growth_income` | `value` | `benchmark_history`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Direct QQQ/TQQQ growth-income profile with explicit portfolio input. |
| `soxl_soxx_trend_income` | `value` | `derived_indicators`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Semiconductor trend profile using canonical derived indicators. |
| `russell_1000_multi_factor_defensive` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Artifact-backed Russell 1000 defensive selection. |
| `tech_communication_pullback_enhancement` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Artifact-backed tech/communication pullback selection with bundled config support. |
| `mega_cap_leader_rotation_top50_balanced` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Retained Top50 balanced leader-rotation path. |
| `nasdaq_sp500_smart_dca` | `value` | `market_history`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Buy-only Nasdaq/S&P 500 smart DCA using market-history indicators and cash availability. |

## Current Conclusions

- The runtime-enabled US equity set is intentionally small and shared.
- Platform registries should derive rollout candidates from
  `get_runtime_enabled_profiles()` rather than carrying local research-only
  overrides.
- A new US equity profile must ship catalog metadata, a manifest, an entrypoint,
  a runtime adapter, and platform input support before it is considered
  runtime-enabled.
- Research outputs can remain as evidence, but weaker duplicate profile names
  should not remain as deployable strategy surfaces.
