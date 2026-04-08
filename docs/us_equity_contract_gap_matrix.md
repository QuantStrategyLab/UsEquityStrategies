# US equity contract gap matrix

This document is the P2 contract-convergence bridge between:

- the shared cross-platform target in `QuantPlatformKit/docs/us_equity_cross_platform_strategy_spec.md`
- the current live `UsEquityStrategies` contract surface

It does **not** change runtime behavior by itself.
Its job is to make the next contract-migration PRs explicit and small.

## Scope of this matrix

This matrix tracks the five current live US equity profiles:

- `global_etf_rotation`
- `hybrid_growth_income`
- `semiconductor_rotation_income`
- `russell_1000_multi_factor_defensive`
- `tech_pullback_cash_buffer`

Out of scope for this step:

- strategy formula changes
- broker execution translation changes
- platform input-builder implementation
- rollout allowlist changes

## Current platform status snapshot

As of this document update, the platform status scripts show:

- `ibkr` enabled:
  - `global_etf_rotation`
  - `russell_1000_multi_factor_defensive`
  - `tech_pullback_cash_buffer`
- `schwab` enabled:
  - `hybrid_growth_income`
  - `semiconductor_rotation_income`
- `longbridge` enabled:
  - `hybrid_growth_income`
  - `semiconductor_rotation_income`

That means the current profile split is still platform-shaped even though the shared strategy contract already exists.

## Canonical end-state input vocabulary

New US equity profiles should only use these canonical `required_inputs`:

- `market_history`
- `benchmark_history`
- `portfolio_snapshot`
- `derived_indicators`
- `feature_snapshot`

All five current live profiles now use canonical `required_inputs` names at the strategy boundary. P4 still needs to converge platform input builders and payload shapes onto the same vocabulary.

## Legacy-to-canonical mapping used for migration planning

| Current legacy input | Intended canonical input | Notes |
| --- | --- | --- |
| `historical_close_loader` | `market_history` | `global_etf_rotation` should stop depending on a broker-style history loader name. |
| `qqq_history` | `benchmark_history` | `hybrid_growth_income` only needs benchmark history, not a Schwab/LongBridge-specific label. |
| `snapshot` | `portfolio_snapshot` | `ctx.portfolio` injection should come from the canonical portfolio input. |
| `indicators` | `derived_indicators` | Regime and indicator bundles belong to the normalized platform input layer. |
| `account_state` | `portfolio_snapshot` | Account cash / positions / equity should converge into one normalized portfolio snapshot contract. |
| `feature_snapshot` | `feature_snapshot` | Already canonical; keep it. |

## Profile-by-profile gap matrix

| Profile | Current `target_mode` | Current `required_inputs` | Current adapter coverage | Current enabled platforms | Intended canonical inputs | Main P2 contract gap | Follow-up after P2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `global_etf_rotation` | `weight` | `market_history` | `ibkr` only | `ibkr` | `market_history` | strategy-facing input name is now canonical, but the current IBKR payload is still loader-shaped and adapter coverage is single-platform | P3/P4 add weight/value translation path and normalized market-history builders for `schwab` / `longbridge` |
| `hybrid_growth_income` | `value` | `benchmark_history` + `portfolio_snapshot` | `schwab`, `longbridge` | `schwab`, `longbridge` | `benchmark_history` + `portfolio_snapshot` | strategy-facing contract is now canonical; remaining gap is platform coverage beyond the current two value-mode runtimes | P3/P4 add `ibkr` translation path and keep benchmark/portfolio builders canonical |
| `semiconductor_rotation_income` | `value` | `derived_indicators` + `portfolio_snapshot` | `schwab`, `longbridge` | `schwab`, `longbridge` | `derived_indicators` + `portfolio_snapshot` | strategy-facing contract is now canonical; remaining gap is platform coverage beyond the current two value-mode runtimes | P3/P4 add `ibkr` translation path and keep indicator/portfolio builders canonical |
| `russell_1000_multi_factor_defensive` | `weight` | `feature_snapshot` | `ibkr` only | `ibkr` | `feature_snapshot` | input name is already canonical, but runtime adapter coverage is still single-platform | P3/P4 standardize artifact transport and add value-runtime paths for `schwab` / `longbridge` |
| `tech_pullback_cash_buffer` | `weight` | `feature_snapshot` | `ibkr` only | `ibkr` | `feature_snapshot` | input name is already canonical, but runtime adapter coverage is still single-platform | P3/P4 standardize artifact transport and add value-runtime paths for `schwab` / `longbridge` |

## Current adapter details that matter for migration

### `global_etf_rotation`

- current adapter inputs:
  - `ibkr`: `market_history`
- contract observation:
  - no portfolio injection requirement today
  - the strategy-facing input name is now canonical
  - the current IBKR runtime still places a callable history loader under `market_history`, so payload normalization remains a P4 input-builder task

### `hybrid_growth_income`

- current adapter inputs:
  - `schwab`: `benchmark_history`, `portfolio_snapshot`
  - `longbridge`: `benchmark_history`, `portfolio_snapshot`
- current portfolio injection:
  - `schwab`: `portfolio_input_name="portfolio_snapshot"`
  - `longbridge`: `portfolio_input_name="portfolio_snapshot"`
- contract observation:
  - this profile now matches the canonical value-mode contract on strategy-facing inputs
  - the remaining gap is future `ibkr` adapter coverage, not naming

### `semiconductor_rotation_income`

- current adapter inputs:
  - `schwab`: `derived_indicators`, `portfolio_snapshot`
  - `longbridge`: `derived_indicators`, `portfolio_snapshot`
- current portfolio injection:
  - `schwab`: `portfolio_input_name="portfolio_snapshot"`
  - `longbridge`: `portfolio_input_name="portfolio_snapshot"`
- contract observation:
  - this profile now matches the canonical value-mode contract on strategy-facing inputs
  - the remaining gap is future `ibkr` adapter coverage, not naming

### `russell_1000_multi_factor_defensive`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`
- contract observation:
  - the strategy already sits on the canonical artifact input
  - the main remaining gaps are multi-platform adapter coverage and downstream translation

### `tech_pullback_cash_buffer`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`
- contract observation:
  - same contract shape as `russell_1000_multi_factor_defensive`
  - the gap is not naming, but cross-platform adapter + artifact-delivery support

## Cross-profile conclusions

### What is already in good shape

- all five live profiles already declare `target_mode`
- all five live profiles already expose metadata, manifest, and unified entrypoint
- all five live profiles now use canonical `required_inputs` names at the strategy boundary
- both feature-snapshot profiles already use the canonical artifact input name

### What still blocks the end state

1. runtime-adapter coverage is still split by current platform ownership
2. `global_etf_rotation` now uses the canonical `market_history` name, but the current IBKR payload is still a loader-shaped compatibility bridge
3. none of the current live profiles yet represent the full three-platform end state in `runtime_adapters.py`

## Recommended migration order after this document

1. **P2 status**: all five live profiles now use canonical input names at the strategy boundary
2. **P3**: add explicit execution translation rules for:
   - `weight -> value`
   - `value -> weight`
3. **P4**: update platform input builders to emit canonical input names and normalize the `market_history` payload beyond the current IBKR loader bridge
4. **P5**: migrate the five live profiles one by one with smoke coverage per platform

## Review rule for future PRs in this track

Until all live profiles are migrated, each PR in this track should state clearly:

- which profile contract changed
- whether `required_inputs` moved closer to canonical names
- whether runtime adapter coverage changed
- whether the change affected only `eligible` or also `enabled`
- which later phase (`P3` / `P4` / `P5`) still owns the remaining gap
