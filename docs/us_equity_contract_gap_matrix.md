# US equity contract gap matrix

This document started as the P2 contract-convergence bridge between:

- the shared cross-platform target in `QuantPlatformKit/docs/us_equity_cross_platform_strategy_spec.md`
- the current live `UsEquityStrategies` contract surface

It still does **not** change runtime behavior by itself.
At this point it mainly records what was migrated, what is now fully portable across the current three-platform scope, and which follow-ups are still implementation cleanups rather than platform-coverage gaps.

## Scope of this matrix

This matrix tracks the eight current live US equity profiles:

- `global_etf_rotation`
- `tqqq_growth_income`
- `soxl_soxx_trend_income`
- `russell_1000_multi_factor_defensive`
- `tech_communication_pullback_enhancement`
- `mega_cap_leader_rotation_aggressive`
- `mega_cap_leader_rotation_dynamic_top20`
- `dynamic_mega_leveraged_pullback`

Out of scope for this document:

- strategy formula changes
- broker execution translation changes
- platform input-builder implementation
- future rollout choices after the current full-matrix migration

## Current platform status snapshot

As of this document update, the platform status scripts show that all eight live US equity profiles are enabled on all three current broker runtimes:

- `ibkr`
  - `global_etf_rotation`
  - `tqqq_growth_income`
  - `soxl_soxx_trend_income`
  - `russell_1000_multi_factor_defensive`
  - `tech_communication_pullback_enhancement`
  - `mega_cap_leader_rotation_aggressive`
  - `mega_cap_leader_rotation_dynamic_top20`
  - `dynamic_mega_leveraged_pullback`
- `schwab`
  - `global_etf_rotation`
  - `tqqq_growth_income`
  - `soxl_soxx_trend_income`
  - `russell_1000_multi_factor_defensive`
  - `tech_communication_pullback_enhancement`
  - `mega_cap_leader_rotation_aggressive`
  - `mega_cap_leader_rotation_dynamic_top20`
  - `dynamic_mega_leveraged_pullback`
- `longbridge`
  - `global_etf_rotation`
  - `tqqq_growth_income`
  - `soxl_soxx_trend_income`
  - `russell_1000_multi_factor_defensive`
  - `tech_communication_pullback_enhancement`
  - `mega_cap_leader_rotation_aggressive`
  - `mega_cap_leader_rotation_dynamic_top20`
  - `dynamic_mega_leveraged_pullback`

That means the original profile-by-profile platform gaps for the current live strategies are closed. The remaining work is about payload normalization, artifact discipline, and future platforms or strategies.

## Canonical end-state input vocabulary

New US equity profiles should only use these canonical `required_inputs`:

- `market_history`
- `benchmark_history`
- `portfolio_snapshot`
- `derived_indicators`
- `feature_snapshot`

All eight current live profiles now use canonical `required_inputs` names at the strategy boundary. P4 still needs to converge platform input builders and payload shapes onto the same vocabulary.

## Legacy-to-canonical mapping used for migration planning

| Current legacy input | Intended canonical input | Notes |
| --- | --- | --- |
| `historical_close_loader` | `market_history` | `global_etf_rotation` should stop depending on a broker-style history loader name. |
| `qqq_history` | `benchmark_history` | `tqqq_growth_income` only needs benchmark history, not a Schwab/LongBridge-specific label. |
| `snapshot` | `portfolio_snapshot` | `ctx.portfolio` injection should come from the canonical portfolio input. |
| `indicators` | `derived_indicators` | Regime and indicator bundles belong to the normalized platform input layer. |
| `account_state` | `portfolio_snapshot` | Account cash / positions / equity should converge into one normalized portfolio snapshot contract. |
| `feature_snapshot` | `feature_snapshot` | Already canonical; keep it. |

## Profile-by-profile gap matrix

| Profile | Current `target_mode` | Current `required_inputs` | Current adapter coverage | Current enabled platforms | Intended canonical inputs | Current portability status | Follow-up after migration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `global_etf_rotation` | `weight` | `market_history` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `market_history` | full three-platform coverage is in place; the strategy-facing contract is canonical everywhere | keep converging `market_history` beyond the current loader-shaped bridge and lock payload shape in CI |
| `tqqq_growth_income` | `value` | `benchmark_history` + `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `benchmark_history` + `portfolio_snapshot` | full three-platform coverage is in place and the value-mode contract is canonical | keep benchmark and portfolio builders aligned across runtimes and avoid platform-specific drift |
| `soxl_soxx_trend_income` | `value` | `derived_indicators` + `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `derived_indicators` + `portfolio_snapshot` | full three-platform coverage is in place and the contract is already canonical | keep indicator and portfolio builders canonical and keep the current matrix locked in CI |
| `russell_1000_multi_factor_defensive` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `feature_snapshot` | full three-platform coverage is in place; canonical artifact input is shared across all runtimes | keep artifact transport, manifest validation, and rollout discipline consistent |
| `tech_communication_pullback_enhancement` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `feature_snapshot` | full three-platform coverage is in place and canonical config or snapshot names are now used on the mainline path | keep artifact rollout and config naming discipline tight, especially for future research outputs |
| `mega_cap_leader_rotation_aggressive` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `feature_snapshot` | full three-platform coverage is in place; aggressive leader artifacts use a separate feature-snapshot contract | keep concentrated-basket risk notes, universe provenance, and snapshot manifest validation explicit |
| `mega_cap_leader_rotation_dynamic_top20` | `weight` | `feature_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `feature_snapshot` | full three-platform coverage is in place; dynamic top20 artifacts use a separate feature-snapshot contract | keep universe-ranking provenance and snapshot manifest validation explicit |
| `dynamic_mega_leveraged_pullback` | `weight` | `feature_snapshot` + `market_history` + `benchmark_history` + `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `feature_snapshot` + `market_history` + `benchmark_history` + `portfolio_snapshot` | full three-platform coverage is in place; the strategy uses monthly dynamic mega-cap product snapshots plus daily market gates | keep product-map provenance, snapshot manifest validation, and hybrid input builders aligned |

## Current adapter details that matter for migration

### `global_etf_rotation`

- current adapter inputs:
  - `ibkr`: `market_history`
  - `schwab`: `market_history`, `portfolio_snapshot`
  - `longbridge`: `market_history`, `portfolio_snapshot`
- contract observation:
  - no strategy-side portfolio injection requirement today
  - the strategy-facing input name is now canonical
  - the current runtimes still place a callable history loader under `market_history`, so payload normalization remains an input-builder cleanup rather than a portability gap

### `tqqq_growth_income`

- current adapter inputs:
  - `ibkr`: `benchmark_history`, `portfolio_snapshot`
  - `schwab`: `benchmark_history`, `portfolio_snapshot`
  - `longbridge`: `benchmark_history`, `portfolio_snapshot`
- current portfolio injection:
  - `ibkr`: `portfolio_input_name="portfolio_snapshot"`
  - `schwab`: `portfolio_input_name="portfolio_snapshot"`
  - `longbridge`: `portfolio_input_name="portfolio_snapshot"`
- contract observation:
  - this profile now matches the canonical value-mode contract on strategy-facing inputs across all three current runtimes
  - the remaining work is only runtime-input cleanup, not platform coverage

### `soxl_soxx_trend_income`

- current adapter inputs:
  - `ibkr`: `derived_indicators`, `portfolio_snapshot`
  - `schwab`: `derived_indicators`, `portfolio_snapshot`
  - `longbridge`: `derived_indicators`, `portfolio_snapshot`
- current portfolio injection:
  - `ibkr`: `portfolio_input_name="portfolio_snapshot"`
  - `schwab`: `portfolio_input_name="portfolio_snapshot"`
  - `longbridge`: `portfolio_input_name="portfolio_snapshot"`
- contract observation:
  - this profile now matches the canonical value-mode contract on strategy-facing inputs
  - adapter coverage is already aligned across the current three-platform runtime scope

### `russell_1000_multi_factor_defensive`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`
  - `schwab`: `feature_snapshot`, `portfolio_snapshot`
  - `longbridge`: `feature_snapshot`, `portfolio_snapshot`
- contract observation:
  - the strategy already sits on the canonical artifact input
  - the current three-platform portability gap is closed; remaining work is artifact transport discipline and payload cleanup

### `tech_communication_pullback_enhancement`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`
  - `schwab`: `feature_snapshot`, `portfolio_snapshot`
  - `longbridge`: `feature_snapshot`, `portfolio_snapshot`
- contract observation:
  - same contract shape as `russell_1000_multi_factor_defensive`
  - the current three-platform portability gap is closed
  - the remaining work is artifact-delivery discipline and keeping canonical config or snapshot names stable

### `mega_cap_leader_rotation_aggressive`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`
  - `schwab`: `feature_snapshot`, `portfolio_snapshot`
  - `longbridge`: `feature_snapshot`, `portfolio_snapshot`
- contract observation:
  - same artifact-backed contract family as the dynamic mega-cap profiles
  - the current three-platform portability gap is closed
  - because the basket is concentrated, runtime notes and account-sizing guidance should stay explicit

### `mega_cap_leader_rotation_dynamic_top20`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`
  - `schwab`: `feature_snapshot`, `portfolio_snapshot`
  - `longbridge`: `feature_snapshot`, `portfolio_snapshot`
- contract observation:
  - the strategy already sits on the canonical artifact input
  - the current three-platform portability gap is closed
  - universe-ranking provenance and manifest validation remain the important artifact controls

### `dynamic_mega_leveraged_pullback`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`, `market_history`, `benchmark_history`, `portfolio_snapshot`
  - `schwab`: `feature_snapshot`, `market_history`, `benchmark_history`, `portfolio_snapshot`
  - `longbridge`: `feature_snapshot`, `market_history`, `benchmark_history`, `portfolio_snapshot`
- contract observation:
  - this profile combines artifact-backed monthly product selection with daily runtime inputs
  - the current three-platform portability gap is closed
  - product-map provenance, manifest validation, and hybrid input builders should remain aligned

## Cross-profile conclusions

### What is already in good shape

- all eight live profiles already declare `target_mode`
- all eight live profiles already expose metadata, manifest, and unified entrypoint
- all eight live profiles now use canonical `required_inputs` names at the strategy boundary
- all eight live profiles now have runtime-adapter coverage on `ibkr`, `schwab`, and `longbridge`
- the feature-snapshot profiles already use the canonical artifact input name
- the current eight-profile by three-platform matrix is now fully portable for the current scope

### What is still worth cleaning up after the migration

1. `global_etf_rotation` still uses a loader-shaped `market_history` bridge instead of a normalized payload
2. feature-snapshot delivery still needs disciplined rollout and validation outside pure strategy code
3. value-mode and weight-mode payload builders should keep converging toward one normalized input layer
4. future profiles or future platforms should keep this full-matrix standard instead of reintroducing partial coverage

## Recommended migration order after this document

1. **Current status**: the eight live profiles now use canonical input names and have full three-platform adapter coverage for the current US equity scope
2. **P3/P4 follow-up**: keep explicit execution translation rules in place and keep normalizing runtime payload builders
3. **Artifact follow-up**: keep feature-snapshot transport, manifest validation, and config naming consistent across runtimes
4. **Future expansion**: treat any new US equity profile or future platform as required to reach the same full-matrix bar before claiming rollout parity

## Review rule for future PRs in this track

Now that the current eight live profiles are migrated, each future PR in this track should state clearly:

- which profile contract changed
- whether `required_inputs` moved closer to canonical names
- whether runtime adapter coverage changed
- whether the change affected only `eligible` or also `enabled`
- whether the change keeps or weakens the current full three-platform matrix
- which later follow-up (`P3` / `P4` / artifact rollout / future platform work) still owns the remaining cleanup
