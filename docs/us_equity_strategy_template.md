# US equity strategy template

This document is the authoring template for **new** US equity strategy profiles in `UsEquityStrategies`.

It is aligned with:

- `QuantPlatformKit/docs/us_equity_cross_platform_strategy_spec.md`
- `QuantPlatformKit/docs/strategy_contract_migration.md`

Use this document when you add a new profile or migrate an existing profile toward the cross-platform end state.

## What “done” looks like

A new profile is not ready until it has all of the following:

1. canonical profile registration in `catalog.py`
2. `StrategyMetadata`, `StrategyDefinition`, manifest, and entrypoint
3. exactly one declared `target_mode`
4. canonical `required_inputs`
5. `ibkr` / `schwab` / `longbridge` runtime adapters, or an explicit unsupported-platform note in the PR
6. contract tests
7. adapter tests
8. portability smoke coverage

## Minimal repository footprint

A greenfield profile should touch at least these locations:

```text
src/us_equity_strategies/
  catalog.py
  manifests/__init__.py
  entrypoints/__init__.py
  runtime_adapters.py
  strategies/<profile>.py
  snapshots/<profile>.py                # only if the strategy owns feature snapshot generation helpers
  backtests/<profile>.py                # optional research/backtest helper
  research/configs/<profile>.json       # optional bundled config

tests/
  test_catalog.py
  test_entrypoints.py
  test_platform_registry_support.py     # if platform-facing registry expectations change
  test_<profile>.py                     # formula / regression coverage
  test_<profile>_feature_snapshot.py    # when feature_snapshot is part of the contract

docs/
  us_equity_strategy_template.md
  us_equity_portability_checklist.md
```

For a **greenfield** profile, `strategies/<profile>.py` should be pure strategy logic over normalized inputs.
Keep legacy `signal_logic` / `allocation` compatibility shims only when an existing runtime still depends on them.

## 1. Profile metadata and catalog registration

Register the profile in `src/us_equity_strategies/catalog.py`.

At minimum, add:

- a canonical profile constant
- platform compatibility entry
- required input entry
- default config entry
- entrypoint attribute entry
- target mode entry
- `StrategyDefinition`
- `StrategyMetadata`

### `StrategyDefinition` must declare

- `profile`
- `domain=US_EQUITY_DOMAIN`
- `supported_platforms` (structural compatibility mirror only; runtime `enabled` stays in platform repositories)
- `entrypoint`
- `required_inputs`
- `compatible_capabilities` only when the shared spec explicitly allows it; otherwise keep it empty
- `default_config`
- `target_mode`
- `bundled_config_relpath` when a bundled config is part of the contract

`components` is optional for greenfield profiles. Only keep it when a migration window still needs a legacy module path.

### Example

```python
MY_NEW_PROFILE = "my_new_profile"

STRATEGY_PLATFORM_COMPATIBILITY[MY_NEW_PROFILE] = frozenset({"ibkr", "schwab", "longbridge"})
STRATEGY_REQUIRED_INPUTS[MY_NEW_PROFILE] = frozenset({
    "market_history",
    "benchmark_history",
    "portfolio_snapshot",
})
STRATEGY_DEFAULT_CONFIG[MY_NEW_PROFILE] = {
    "benchmark_symbol": "SPY",
    "safe_haven": "BOXX",
}
STRATEGY_ENTRYPOINT_ATTRIBUTES[MY_NEW_PROFILE] = "my_new_profile_entrypoint"
STRATEGY_TARGET_MODES[MY_NEW_PROFILE] = "weight"

STRATEGY_DEFINITIONS[MY_NEW_PROFILE] = StrategyDefinition(
    profile=MY_NEW_PROFILE,
    domain=US_EQUITY_DOMAIN,
    supported_platforms=STRATEGY_PLATFORM_COMPATIBILITY[MY_NEW_PROFILE],
    entrypoint=StrategyEntrypointDefinition(
        module_path="us_equity_strategies.entrypoints",
        attribute_name=STRATEGY_ENTRYPOINT_ATTRIBUTES[MY_NEW_PROFILE],
    ),
    required_inputs=STRATEGY_REQUIRED_INPUTS[MY_NEW_PROFILE],
    default_config=STRATEGY_DEFAULT_CONFIG[MY_NEW_PROFILE],
    target_mode=STRATEGY_TARGET_MODES[MY_NEW_PROFILE],
)

STRATEGY_METADATA[MY_NEW_PROFILE] = StrategyMetadata(
    canonical_profile=MY_NEW_PROFILE,
    display_name="My New Profile",
    description="One-line description of the strategy.",
    aliases=("my_new_profile_alias",),
    cadence="daily",
    asset_scope="us_equity_example",
    benchmark="SPY",
    role="example_role",
    status="paper_dry_run",
)
```

## 2. Manifest and entrypoint

Add the manifest in `src/us_equity_strategies/manifests/__init__.py` and the unified entrypoint in `src/us_equity_strategies/entrypoints/__init__.py`.

### Manifest requirements

The manifest is the runtime-facing contract. Keep it aligned with `catalog.py` on:

- `profile`
- `display_name`
- `description`
- `aliases`
- `required_inputs`
- `default_config`

Current helper `_manifest(...)` does not expose `compatible_capabilities`. That is intentional for now: new US equity strategies should default to **no broker capability dependency**. If a new profile truly needs `compatible_capabilities`, update the helper and the cross-platform spec in the same PR instead of hiding the requirement in runtime code.

### Entrypoint rules

The entrypoint must:

- read normalized inputs from `StrategyContext`
- merge manifest defaults with `ctx.runtime_config`
- return `StrategyDecision`
- stay free of broker env access, file-path assumptions, and platform branches

### Example entrypoint skeleton

```python
from quant_platform_kit.strategy_contracts import CallableStrategyEntrypoint, StrategyContext, StrategyDecision

from us_equity_strategies.manifests import my_new_profile_manifest
from us_equity_strategies.strategies import my_new_profile as strategy_logic
from ._common import merge_runtime_config, require_market_data, require_portfolio, weights_to_positions


def evaluate_my_new_profile(ctx: StrategyContext) -> StrategyDecision:
    config = merge_runtime_config(my_new_profile_manifest.default_config, ctx)
    market_history = require_market_data(ctx, "market_history")
    benchmark_history = require_market_data(ctx, "benchmark_history")
    portfolio = require_portfolio(ctx)

    weights, diagnostics = strategy_logic.compute_targets(
        market_history=market_history,
        benchmark_history=benchmark_history,
        portfolio=portfolio,
        current_holdings=ctx.state.get("current_holdings", ()),
        **config,
    )

    return StrategyDecision(
        positions=weights_to_positions(weights, safe_haven=str(config.get("safe_haven", "BOXX"))),
        diagnostics=diagnostics,
    )


my_new_profile_entrypoint = CallableStrategyEntrypoint(
    manifest=my_new_profile_manifest,
    _evaluate=evaluate_my_new_profile,
)
```

If the profile is `target_mode="value"`, use `target_values_to_positions(...)` and return only `target_value` positions.

## 3. `required_inputs`: canonical vocabulary only

For **new** US equity profiles, `required_inputs` may only use these canonical names:

- `market_history`
- `benchmark_history`
- `portfolio_snapshot`
- `derived_indicators`
- `feature_snapshot`

### Meaning of each canonical input

- `market_history`: ranking / rotation / risk history across the managed universe
- `benchmark_history`: benchmark series such as `SPY` or `QQQ`
- `portfolio_snapshot`: current holdings, cash, market values, and account state
- `derived_indicators`: runtime-owned indicator bundle
- `feature_snapshot`: validated artifact-backed cross-sectional dataset

### Rules

- Do **not** add a new ad-hoc input name in `catalog.py`, `manifest`, or runtime adapters.
- If the canonical list is insufficient, update the spec in `QuantPlatformKit` first.
- Existing live profiles may still use legacy names during migration. New profiles should not copy those legacy names forward.

If the strategy needs a portfolio object in `ctx.portfolio`, also set `portfolio_input_name="portfolio_snapshot"` in each runtime adapter so the shared context builder injects the same normalized input into `ctx.portfolio`.

## 4. `target_mode`: declare exactly one mode

Every profile must declare exactly one `target_mode` in `catalog.py`:

- `weight`
- `value`

Rules:

- one profile, one mode
- no mixed `target_weight` + `target_value` outputs inside the same profile
- strategy code chooses the semantic target mode
- platform runtimes own the `weight <-> value` translation when their native execution model differs

Practical mapping today:

- `ibkr` runtime is naturally close to `weight`
- `schwab` runtime is naturally close to `value`
- `longbridge` runtime is naturally close to `value`

That runtime difference must stay in the platform translation layer, not in the strategy module.

## 5. Runtime adapters: all three platforms by default

Add runtime adapters in `src/us_equity_strategies/runtime_adapters.py`.

Default expectation for a new profile:

- `ibkr` adapter present
- `schwab` adapter present
- `longbridge` adapter present

If a platform is intentionally unsupported in the current PR:

- omit that adapter
- omit the platform from `supported_platforms`
- explain the gap explicitly in the PR
- leave that platform `eligible=false` until the gap is closed

### Adapter fields to fill

For each platform, set:

- `available_inputs` (normally the same canonical set as `required_inputs`)
- `available_capabilities` only when truly required
- `portfolio_input_name` when the strategy expects `ctx.portfolio`
- artifact-validation metadata when `feature_snapshot` is involved

### Example adapter skeleton

```python
PLATFORM_RUNTIME_ADAPTERS = {
    "ibkr": {
        MY_NEW_PROFILE: StrategyRuntimeAdapter(
            status_icon="🧪",
            available_inputs=frozenset({
                "market_history",
                "benchmark_history",
                "portfolio_snapshot",
            }),
            portfolio_input_name="portfolio_snapshot",
        ),
    },
    "schwab": {
        MY_NEW_PROFILE: StrategyRuntimeAdapter(
            status_icon="🧪",
            available_inputs=frozenset({
                "market_history",
                "benchmark_history",
                "portfolio_snapshot",
            }),
            portfolio_input_name="portfolio_snapshot",
        ),
    },
    "longbridge": {
        MY_NEW_PROFILE: StrategyRuntimeAdapter(
            status_icon="🧪",
            available_inputs=frozenset({
                "market_history",
                "benchmark_history",
                "portfolio_snapshot",
            }),
            portfolio_input_name="portfolio_snapshot",
        ),
    },
}
```

New profiles should default to empty `available_capabilities`. Prefer normalized inputs over passing broker clients into strategy code.

## 6. Feature snapshot and artifact-backed strategies

If the strategy depends on `feature_snapshot`, the contract must be explicit.

### Strategy-side contract

Declare in the profile contract:

- `required_inputs` includes `feature_snapshot`
- expected feature columns
- accepted date columns
- freshness lag rule
- whether a manifest/checksum is required
- contract version when manifest validation is required

### Runtime adapter fields to use

For `feature_snapshot` profiles, fill the adapter with the relevant fields:

- `required_feature_columns`
- `snapshot_date_columns`
- `max_snapshot_month_lag`
- `require_snapshot_manifest`
- `snapshot_contract_version`
- `managed_symbols_extractor` only when the runtime needs it for validation or reconciliation
- `runtime_parameter_loader` only for temporary migration-window config loading

### Example

```python
StrategyRuntimeAdapter(
    status_icon="📦",
    available_inputs=frozenset({"feature_snapshot"}),
    required_feature_columns=frozenset({"symbol", "as_of", "score", "eligible"}),
    snapshot_date_columns=("as_of", "snapshot_date"),
    max_snapshot_month_lag=1,
    require_snapshot_manifest=True,
    snapshot_contract_version="my_new_profile.feature_snapshot.v1",
)
```

### Artifact rules

- platform repos own transport, storage path, freshness validation, and manifest loading
- strategy code must not open broker-local files or assume a service-local path
- do not invent a new artifact key in `ctx.artifacts` for a new US equity profile without updating the shared spec first

Today the shared contract has first-class support for `feature_snapshot`. If a new profile needs a different artifact type, extend `QuantPlatformKit` and the shared spec before introducing it here.

## 7. Allowed and forbidden patterns

### Allowed

- pure helper functions in `strategies/<profile>.py`
- normalized input access through `StrategyContext`
- runtime metadata in `StrategyRuntimeAdapter`
- strategy diagnostics that describe signal, regime, exposure, or validation state
- platform-side execution translation outside this repository

### Forbidden

Do not do any of the following in strategy code or unified entrypoints:

- branch on platform id (`ibkr`, `schwab`, `longbridge`)
- read broker env vars directly
- import platform runtime modules
- return broker-specific order fields
- return UI layout fields such as `portfolio_rows`
- return broker sequencing hints such as `sell_order_symbols`, `buy_order_symbols`, `limit_order_symbols`, or `cash_sweep_symbol`
- perform broker-specific order sizing inside the strategy repo
- assume artifact file paths or service-local directories
- add new one-off `required_inputs` names without updating the shared spec

`StrategyDecision.diagnostics` is for strategy-level diagnostics, not a backdoor for broker payloads.

## 8. Minimum test requirements

Every new profile should land with at least these tests.

### A. Contract test

Cover:

- canonical profile resolves correctly
- metadata and aliases are registered
- `required_inputs` use only canonical names
- `target_mode` is set and matches the position output shape
- entrypoint exposes the expected manifest

Recommended home: `tests/test_catalog.py` and `tests/test_entrypoints.py`.

### B. Adapter test

Cover:

- `get_platform_runtime_adapter(profile, platform_id=...)` works for `ibkr`, `schwab`, and `longbridge`
- `available_inputs` match the normalized contract
- `portfolio_input_name` is present when needed
- feature snapshot metadata is enforced when applicable

Recommended home: `tests/test_entrypoints.py` or a profile-specific adapter test file.

### C. Portability smoke test

Cover at least one dry-run path per intended platform:

- build a normalized `StrategyContext`
- call `entrypoint.evaluate(ctx)`
- assert the result is a valid `StrategyDecision`
- assert no broker-specific fields leaked into diagnostics

If a platform is not enabled yet, the smoke test should still prove the profile is portable enough to stay `eligible=true`, or the PR should explicitly document why portability is still incomplete.

## 9. New-strategy PR checklist

Copy this checklist into the PR body for any new US equity profile.

- [ ] canonical profile constant added in `catalog.py`
- [ ] `StrategyMetadata` added
- [ ] `StrategyDefinition` added with explicit `target_mode`
- [ ] `required_inputs` use canonical vocabulary only
- [ ] manifest added and aligned with catalog registration
- [ ] unified entrypoint added
- [ ] strategy logic stays platform-agnostic
- [ ] no broker env access in strategy code
- [ ] no broker-specific order or UI fields in `StrategyDecision`
- [ ] `ibkr` runtime adapter added or unsupported reason documented
- [ ] `schwab` runtime adapter added or unsupported reason documented
- [ ] `longbridge` runtime adapter added or unsupported reason documented
- [ ] feature snapshot / artifact contract documented when applicable
- [ ] contract tests added
- [ ] adapter tests added
- [ ] portability smoke coverage added
- [ ] rollout note explains `eligible` vs `enabled`

## 10. Migration note for existing profiles

When migrating an existing live profile, keep the runtime stable first:

1. preserve the current trading formula
2. normalize inputs and outputs
3. move platform-only execution details out of the strategy path
4. keep compatibility shims only as long as rollback safety requires them
5. delete the shim in a later cleanup PR once all downstream runtimes are off it

For greenfield profiles, skip new legacy shims unless they are truly needed for a staged rollout.
