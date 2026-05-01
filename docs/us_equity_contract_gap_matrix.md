# US equity contract gap matrix

_Updated: 2026-05-01_

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

These profiles are designed against the shared contract and are portable across
the current US equity platform IDs:

- `ibkr`
- `schwab`
- `longbridge`
- `paper_signal`

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
| `global_etf_rotation` | `weight` | `market_history` | `ibkr`, `schwab`, `longbridge`, `paper_signal` | runtime-enabled | Quarterly top-2 ETF rotation with daily canary defense. |
| `tqqq_growth_income` | `value` | `benchmark_history`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `paper_signal` | runtime-enabled | Direct QQQ/TQQQ growth-income profile with explicit portfolio input. |
| `soxl_soxx_trend_income` | `value` | `derived_indicators`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `paper_signal` | runtime-enabled | Semiconductor trend profile using canonical derived indicators. |
| `russell_1000_multi_factor_defensive` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `paper_signal` | runtime-enabled | Artifact-backed Russell 1000 defensive selection. |
| `tech_communication_pullback_enhancement` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `paper_signal` | runtime-enabled | Artifact-backed tech/communication pullback selection with bundled config support. |
| `mega_cap_leader_rotation_top50_balanced` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `paper_signal` | runtime-enabled | Retained Top50 balanced leader-rotation path. |

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
