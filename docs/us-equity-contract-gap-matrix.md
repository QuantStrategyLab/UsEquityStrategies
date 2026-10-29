# US equity contract gap matrix

_Updated: 2026-06-04_

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
- `russell_top50_leader_rotation`
- `nasdaq_sp500_smart_dca`
- `ibit_smart_dca`

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

- `russell_1000_multi_factor_defensive`
- `mega_cap_leader_rotation_top50_balanced`
- `mega_cap_leader_rotation_dynamic_top20`
- `dynamic_mega_leveraged_pullback`
- `tech_communication_pullback_enhancement`

`mega_cap_leader_rotation_top50_balanced` was renamed to `russell_top50_leader_rotation`; the weaker defensive and dynamic profiles were retired instead of kept as runtime-compatible aliases.
The 2x dynamic Russell Top50/MAGS route added more product and input complexity
without a better promoted profile result.
`tech_communication_pullback_enhancement` stayed as an archived research
implementation/config for reproducibility, but it is no longer exposed through
catalog, manifest, entrypoint, runtime adapter, or platform rollout surfaces.

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
| `global_etf_rotation` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Quarterly top-2 ETF rotation with daily canary defense. |
| `tqqq_growth_income` | `value` | `benchmark_history`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Direct QQQ/TQQQ growth-income profile with explicit portfolio input. |
| `soxl_soxx_trend_income` | `value` | `derived_indicators`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Semiconductor trend profile using canonical derived indicators. |
| `russell_top50_leader_rotation` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Retained Russell Top50 leader-rotation path. |
| `nasdaq_sp500_smart_dca` | `value` | `market_history`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Buy-only Nasdaq 100 / S&P 500 smart DCA using market-history indicators and cash availability. |
| `ibit_smart_dca` | `value` | `derived_indicators`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Buy-only spot Bitcoin ETF smart DCA using derived indicators and portfolio cash availability. |

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
