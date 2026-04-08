# US equity value-mode canonical input contract

This document fixes the **strategy-facing canonical input contract** for the two
current US equity value-mode profiles:

- `hybrid_growth_income`
- `semiconductor_rotation_income`

It started as the P2.2 planning document for contract convergence.
Both current value-mode live profiles have since been migrated in code; the
remaining sections describe the current contract plus the still-open follow-up
work around rollout and deeper payload normalization.

## Why this document exists

The cross-platform target already says new US equity strategies should use
canonical input names.

The current two value-mode live profiles are now aligned on their
strategy-facing canonical inputs:

- `hybrid_growth_income` uses `benchmark_history` + `portfolio_snapshot`
- `semiconductor_rotation_income` uses `derived_indicators` + `portfolio_snapshot`

This document defines the end-state contract that later P2/P4 code changes
should implement.

Current implementation status:

- `hybrid_growth_income` already uses canonical strategy-facing inputs on
  `schwab` and `longbridge`
- `semiconductor_rotation_income` already uses canonical strategy-facing inputs
  on `ibkr`, `schwab`, and `longbridge`

## Fixed end-state summary

| Profile | Keep `target_mode` | Current strategy-facing inputs | Fixed canonical strategy-facing inputs |
| --- | --- | --- | --- |
| `hybrid_growth_income` | `value` | `benchmark_history` + `portfolio_snapshot` | `benchmark_history` + `portfolio_snapshot` |
| `semiconductor_rotation_income` | `value` | `derived_indicators` + `portfolio_snapshot` | `derived_indicators` + `portfolio_snapshot` |

Shared rules for both profiles:

- `target_mode` stays `value`
- strategy code must consume a canonical `portfolio_snapshot`
- strategy code must not depend on legacy keys such as `snapshot`, `qqq_history`, `indicators`, or `account_state` in the end state
- broker execution metadata must stay outside the strategy contract

## Shared contract rules for value-mode profiles

### 1. Canonical required inputs

Value-mode profiles should declare only canonical `required_inputs`.

For this track, the exact target is:

- `hybrid_growth_income`
  - `required_inputs = {"benchmark_history", "portfolio_snapshot"}`
- `semiconductor_rotation_income`
  - `required_inputs = {"derived_indicators", "portfolio_snapshot"}`

`portfolio_snapshot` is not just an adapter-local helper. It is part of the
strategy contract.

### 2. Canonical portfolio object

For both profiles, `ctx.portfolio` should be populated from the same canonical
input name:

- `runtime_adapter.portfolio_input_name = "portfolio_snapshot"`

The object should be `quant_platform_kit.common.models.PortfolioSnapshot`.

Minimum fields that the strategy path may rely on:

- `as_of`
- `total_equity`
- `buying_power` when available
- `cash_balance` when available
- `positions`
- `metadata`

The strategy layer may derive simple helper views from `PortfolioSnapshot`, for
example:

- symbol -> market value
- symbol -> quantity
- available cash fallback logic

But it must not require broker-native account payloads directly.

Implementation note: because the shared context builder may also keep
`portfolio_snapshot` under `ctx.market_data`, value-mode entrypoints should still
standardize on reading the portfolio object from `ctx.portfolio`, not from a
second market-data code path.

### 3. What does not belong in the strategy contract

These items are execution/runtime concerns and should not be part of the
canonical strategy-facing input contract:

- `sellable_quantities`
- broker-specific order-size constraints
- order-sequencing hints
- platform-only cash sweep behavior
- account-state payloads copied straight from one broker API

If a platform still needs them, keep them in the platform mapper/runtime layer.
Do not preserve them as permanent strategy inputs.

## Profile contract: `hybrid_growth_income`

### Intent

This profile is a value-mode strategy over:

- benchmark history for `QQQ`
- current portfolio state

The strategy does **not** need a platform-specific snapshot key in its final
contract.

### Fixed canonical input contract

### `benchmark_history`

Purpose:

- provide the benchmark time series used to compute:
  - latest `QQQ` price
  - `MA200`
  - ATR-derived entry/exit lines

Minimum accepted shape for this migration track:

- ordered oldest -> newest iterable of records
- each record provides:
  - `close`
  - `high`
  - `low`

Example:

```python
benchmark_history = [
    {"close": 300.0, "high": 301.0, "low": 299.0},
    {"close": 301.0, "high": 302.0, "low": 300.0},
]
```

Notes:

- the benchmark symbol belongs in strategy config (`benchmark_symbol="QQQ"`),
  not in the input key name
- this phase does not force a `PriceSeries` rewrite yet; it only fixes the
  canonical input name and minimal shape

### `portfolio_snapshot`

Purpose:

- provide current holdings and account equity for:
  - current managed-symbol market values
  - current quantities
  - total equity
  - buying power / cash fallback

Required strategy-facing object:

```python
PortfolioSnapshot(
    as_of=...,
    total_equity=120000.0,
    buying_power=20000.0,
    cash_balance=...,
    positions=(...),
    metadata={...},
)
```

The strategy may derive a local helper state like:

- `market_values = {symbol: position.market_value}`
- `quantities = {symbol: position.quantity}`
- `real_buying_power = buying_power or 0.0`

But those are internal derived views, not contract inputs.

### Target entrypoint shape

End-state entrypoint expectations:

- `ctx.market_data["benchmark_history"]`
- `ctx.portfolio` populated from `portfolio_snapshot`
- no direct dependency on `ctx.market_data["qqq_history"]`
- no direct dependency on adapter-local `snapshot`

### Target adapter shape

For every platform that supports this profile today, the adapter shape is:

```python
StrategyRuntimeAdapter(
    available_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
    portfolio_input_name="portfolio_snapshot",
)
```

No broker capability requirement should be declared for this profile.

Current status:

- implemented on `schwab`
- implemented on `longbridge`
- `ibkr` remains future work for this specific profile

## Profile contract: `semiconductor_rotation_income`

### Intent

This profile is a value-mode strategy over:

- a precomputed indicator/regime bundle
- current portfolio state

The final strategy contract should not depend on raw `account_state`.

### Fixed canonical input contract

### `derived_indicators`

Purpose:

- provide the precomputed regime signal that decides whether the active risk
  asset is `SOXL` or `SOXX`
- keep indicator computation in the platform runtime, not inside the strategy

Minimum accepted shape for this migration track:

```python
derived_indicators = {
    "soxl": {
        "price": 80.0,
        "ma_trend": 75.0,
    },
    "soxx": {
        "price": 210.0,
    },
}
```

Rules:

- `trend_ma_window` remains runtime config / strategy config, not part of the
  input key name
- platform runtimes own the actual candle fetching and indicator calculation
- the strategy consumes the normalized indicator bundle only

### `portfolio_snapshot`

Purpose:

- provide the current strategy equity and holdings needed to compute value
  targets

The current `account_state` fields map to canonical portfolio-derived data like
this:

| Current field | Canonical source |
| --- | --- |
| `total_strategy_equity` | `portfolio_snapshot.total_equity` |
| `available_cash` | `portfolio_snapshot.buying_power` or `cash_balance` fallback |
| `market_values` | derived from `portfolio_snapshot.positions` |
| `quantities` | derived from `portfolio_snapshot.positions` |
| `sellable_quantities` | platform execution concern, not a canonical strategy input |

Important conclusion:

- the strategy implementation should build any temporary helper state from
  `PortfolioSnapshot` internally instead of keeping `account_state` as a
  permanent contract input
- `sellable_quantities` should drop out of the strategy-facing contract
- if a platform still needs `sellable_quantities` for execution safety, keep it
  in the platform decision mapper only

### Target entrypoint shape

End-state entrypoint expectations:

- `ctx.market_data["derived_indicators"]`
- `ctx.portfolio` populated from `portfolio_snapshot`
- no direct dependency on `ctx.market_data["indicators"]`
- no direct dependency on `ctx.market_data["account_state"]`
- no platform asymmetry where one runtime injects portfolio and another does not

### Target adapter shape

For every platform that supports this profile today, the adapter shape is:

```python
StrategyRuntimeAdapter(
    available_inputs=frozenset({"derived_indicators", "portfolio_snapshot"}),
    portfolio_input_name="portfolio_snapshot",
)
```

No broker capability requirement should be declared for this profile.

Current status:

- implemented on `ibkr`
- implemented on `schwab`
- implemented on `longbridge`

## Platform matrix implied by this document

| Profile | Current `ibkr` adapter | Current `schwab` adapter | Current `longbridge` adapter |
| --- | --- | --- | --- |
| `hybrid_growth_income` | not yet implemented | `benchmark_history` + `portfolio_snapshot` | `benchmark_history` + `portfolio_snapshot` |
| `semiconductor_rotation_income` | `derived_indicators` + `portfolio_snapshot` | `derived_indicators` + `portfolio_snapshot` | `derived_indicators` + `portfolio_snapshot` |

This matrix defines current adapter state, not rollout state.
Whether a platform becomes `enabled=true` stays a separate rollout decision.

## Migration guidance for later implementation PRs

When later code changes are still needed, prefer this order:

1. update entrypoints/normalization helpers so they can consume the canonical
   contract
2. update strategy definitions + manifests if any profile adds new platform coverage
3. update runtime adapters to advertise canonical `available_inputs` and
   `portfolio_input_name="portfolio_snapshot"`
4. update platform input builders to pass canonical keys
5. add or refresh portability smoke tests

Do not merge a partial change that leaves the strategy contract half-canonical
and half-platform-specific without documenting the short-lived bridge.

## Non-goals of this document

This document does not decide:

- how any future `hybrid_growth_income` `value -> weight` translation should work on `ibkr`
- how rollout allowlists should change
- whether the value-mode formulas themselves should change
- whether benchmark history should later tighten from record lists to a shared
  `PriceSeries` type
