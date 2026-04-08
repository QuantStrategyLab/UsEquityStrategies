# US equity contract gap matrix

This document is the P2 contract-convergence bridge between:

- the shared cross-platform target in `QuantPlatformKit/docs/us_equity_cross_platform_strategy_spec.md`
- the current live `UsEquityStrategies` contract surface

It does **not** change runtime behavior by itself.
Its job is to make the next contract-migration PRs explicit and small.

## Scope of this matrix

This matrix tracks the five current live US equity profiles:

- `global_etf_rotation`
- `tqqq_growth_income`
- `soxl_soxx_trend_income`
- `russell_1000_multi_factor_defensive`
- `qqq_tech_enhancement`

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
  - `qqq_tech_enhancement`
  - `soxl_soxx_trend_income`
- `schwab` enabled:
  - `tqqq_growth_income`
  - `soxl_soxx_trend_income`
- `longbridge` enabled:
  - `tqqq_growth_income`
  - `soxl_soxx_trend_income`
  - `qqq_tech_enhancement`

That means the shared contract has already spread beyond the original
platform-shaped split, but coverage is still uneven by profile.

## Canonical end-state input vocabulary

New US equity profiles should only use these canonical `required_inputs`:

- `market_history`
- `benchmark_history`
- `portfolio_snapshot`
- `derived_indicators`
- `feature_snapshot`

All five runtime-enabled profiles now use canonical `required_inputs` names at the strategy boundary. P4 still needs to converge platform input builders and payload shapes onto the same vocabulary.

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

| Profile | Current `target_mode` | Current `required_inputs` | Current adapter coverage | Current enabled platforms | Intended canonical inputs | Main P2 contract gap | Follow-up after P2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `global_etf_rotation` | `weight` | `market_history` | `ibkr` only | `ibkr` | `market_history` | strategy-facing input name is now canonical, but the current IBKR payload is still loader-shaped and adapter coverage is still single-platform | P3/P4 add weight/value translation path and normalized market-history builders for `schwab` / `longbridge` |
| `tqqq_growth_income` | `value` | `benchmark_history` + `portfolio_snapshot` | `schwab`, `longbridge` | `schwab`, `longbridge` | `benchmark_history` + `portfolio_snapshot` | strategy-facing contract is canonical; the main remaining gap is still missing `ibkr` adapter coverage | P3/P4 add `ibkr` translation path and keep benchmark/portfolio builders canonical |
| `soxl_soxx_trend_income` | `value` | `derived_indicators` + `portfolio_snapshot` | `ibkr`, `schwab`, `longbridge` | `ibkr`, `schwab`, `longbridge` | `derived_indicators` + `portfolio_snapshot` | strategy-facing contract and runtime-adapter coverage are both canonical for the current three-platform scope | P3/P4 keep indicator/portfolio builders canonical and lock this state in CI |
| `russell_1000_multi_factor_defensive` | `weight` | `feature_snapshot` | `ibkr` only | `ibkr` | `feature_snapshot` | input name is already canonical, but runtime adapter coverage is still single-platform | P3/P4 standardize artifact transport and add value-runtime paths for `schwab` / `longbridge` |
| `qqq_tech_enhancement` | `weight` | `feature_snapshot` | `ibkr`, `longbridge` | `ibkr`, `longbridge` | `feature_snapshot` | input name is canonical and runtime-adapter coverage now spans two runtimes; remaining gap is only `schwab` plus artifact rollout discipline | P3/P4 standardize artifact transport and decide whether `schwab` needs a value-runtime path |

## Current adapter details that matter for migration

### `global_etf_rotation`

- current adapter inputs:
  - `ibkr`: `market_history`
- contract observation:
  - no portfolio injection requirement today
  - the strategy-facing input name is now canonical
  - the current IBKR runtime still places a callable history loader under `market_history`, so payload normalization remains a P4 input-builder task

### `tqqq_growth_income`

- current adapter inputs:
  - `schwab`: `benchmark_history`, `portfolio_snapshot`
  - `longbridge`: `benchmark_history`, `portfolio_snapshot`
- current portfolio injection:
  - `schwab`: `portfolio_input_name="portfolio_snapshot"`
  - `longbridge`: `portfolio_input_name="portfolio_snapshot"`
- contract observation:
  - this profile now matches the canonical value-mode contract on strategy-facing inputs
  - the remaining gap is still `ibkr` adapter coverage, not naming

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
- contract observation:
  - the strategy already sits on the canonical artifact input
  - the main remaining gaps are multi-platform adapter coverage and downstream translation

### `qqq_tech_enhancement`

- current adapter inputs:
  - `ibkr`: `feature_snapshot`
  - `longbridge`: `feature_snapshot`, `portfolio_snapshot`
- contract observation:
  - same contract shape as `russell_1000_multi_factor_defensive`
  - the gap is no longer naming
  - the remaining work is artifact-delivery discipline and any future `schwab` path

## Cross-profile conclusions

### What is already in good shape

- all five live profiles already declare `target_mode`
- all five live profiles already expose metadata, manifest, and unified entrypoint
- all five live profiles now use canonical `required_inputs` names at the strategy boundary
- both feature-snapshot profiles already use the canonical artifact input name
- `soxl_soxx_trend_income` already has three-platform runtime-adapter coverage
- `qqq_tech_enhancement` already spans `ibkr` and `longbridge`

### What still blocks the end state

1. `global_etf_rotation` still uses a loader-shaped `market_history` bridge on `ibkr`
2. `tqqq_growth_income` still lacks an `ibkr` adapter path
3. `russell_1000_multi_factor_defensive` is still `ibkr`-only
4. feature-snapshot delivery still needs disciplined rollout and validation outside pure strategy code

## Recommended migration order after this document

1. **P2 status**: all five live profiles now use canonical input names at the strategy boundary
2. **P3**: keep explicit execution translation rules in place for:
   - `weight -> value`
   - `value -> weight`
3. **P4**: finish platform input builders so `market_history` is normalized beyond the current IBKR loader bridge and feature-snapshot transport stays portable
4. **P5**: close the remaining profile-specific gaps:
   - `tqqq_growth_income` on `ibkr`
   - `russell_1000_multi_factor_defensive` outside `ibkr`
   - any future `schwab` path for `qqq_tech_enhancement`

## Review rule for future PRs in this track

Until all live profiles are migrated, each PR in this track should state clearly:

- which profile contract changed
- whether `required_inputs` moved closer to canonical names
- whether runtime adapter coverage changed
- whether the change affected only `eligible` or also `enabled`
- which later phase (`P3` / `P4` / `P5`) still owns the remaining gap
