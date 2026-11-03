# US equity contract gap matrix

This document tracks the current shared US equity strategy contract across
`UsEquityStrategies`, `QuantPlatformKit`, and the platform runtimes.

It records runtime portability and research contract distinctions needed for
reproducibility. Research candidates that compare worse
than a runtime-enabled peer are not kept as deployable or replayable profile
entries in this matrix.

## Current Runtime Scope

The current runtime-selectable US equity profiles are defined by
[`runtime_allowlist.py`](../src/us_equity_strategies/runtime_allowlist.py):
- `tqqq_growth_income`
- `soxl_soxx_trend_income`
- `russell_top50_leader_rotation`
- `nasdaq_sp500_smart_dca`
- `ibit_smart_dca`

`global_etf_rotation` remains available for historical research and compatibility,
but is excluded from the runtime-selectable allowlist. Its legacy aliases,
including `global_etf_confidence_vol_gate`, do not restore selection eligibility.
Catalog lifecycle metadata and retained entrypoints describe implementation
history; they do not override the explicit allowlist.

The strategy implementations use a shared contract with adapters for these US
equity platform IDs. Adapter coverage does not establish account selection,
release qualification, or order authority:

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

## Research CVaR definition and frozen source

The fractional-Kelly research evaluator reports `cvar_95_daily` as the mean
of the lowest `ceil(0.05 * n)` signed per-session returns. QPK's unified net
report instead integrates exactly the lowest 5% empirical probability mass,
using fractional weight for a boundary observation. Both are discrete-tail
conventions; they can differ and should not be combined or compared under the
same name without stating the convention. For 30 returns with the two lowest
equal to -0.30 and -0.10, the UES value is -0.20, while the QPK convention
gives (-0.30 - 0.05) / 1.5 = -0.2333 (approximately).

The historical protocol `docs/research/protocols/R3_VS_R0_CHALLENGER_20261022.json`
binds `research/fractional_kelly_walk_forward.py` to SHA-256
`7e5892d59fd9c1243a5433b4c87b616d0805ed14130d24d65850bbfc8734ec74`.
The single-strategy v1 private runner uses that evaluator; portfolio v1 uses
its path metrics, and the single-strategy and portfolio v2 evaluators also
consume those metrics. The v2 contract requires zero-embargo comparator parity
with v1 and does not inherit v1 real-data authorization.

A candidate change to the frozen evaluator was withdrawn after its source
binding failed. Any future adoption of a different tail convention needs a
separately versioned research implementation and an appropriate protocol and
consumer migration; it must not alter the historical binding or imply new
authorization. Existing research evidence, including the B5 rejection, remains
unchanged.

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
| `global_etf_rotation` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | historical compatibility; not runtime-selectable | Retained quarterly ETF rotation and daily canary implementation. |
| `tqqq_growth_income` | `value` | `benchmark_history`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Direct QQQ/TQQQ growth-income profile with explicit portfolio input. |
| `soxl_soxx_trend_income` | `value` | `derived_indicators`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Semiconductor trend profile using canonical derived indicators. |
| `russell_top50_leader_rotation` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Retained Russell Top50 leader-rotation path. |
| `nasdaq_sp500_smart_dca` | `value` | `market_history`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Buy-only Nasdaq 100 / S&P 500 smart DCA using market-history indicators and cash availability. |
| `ibit_smart_dca` | `value` | `derived_indicators`, `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge`, `firstrade`, `paper_signal` | runtime-enabled | Buy-only spot Bitcoin ETF smart DCA using derived indicators and portfolio cash availability. |

## Current Conclusions

- The runtime-enabled US equity set is intentionally small and shared.
- Platform registries should derive selection candidates from
  `get_runtime_enabled_profiles()`, which returns the explicit selectable
  allowlist. Platform policy, required inputs, release bindings, and account
  risk checks remain separate conditions.
- A new US equity profile must ship catalog metadata, a manifest, an entrypoint,
  a runtime adapter, and platform input support before it is considered
  runtime-enabled.
- Research outputs can remain as evidence, but weaker duplicate profile names
  should not remain as deployable strategy surfaces.
