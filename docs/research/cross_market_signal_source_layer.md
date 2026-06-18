# Cross-Market Signal Source Layer Research

Run date: 2026-06-19.

This note records the architecture direction for a shared signal-source layer
across US equity, HK equity, and crypto strategy packages. It is research only;
it does not change deployment ownership or live data pipelines.

## Current Architecture Understanding

`UsEquityStrategies`, `HkEquityStrategies`, and `CryptoStrategies` already share
the same high-level boundary:

- strategy packages own pure strategy code, catalog metadata, manifests,
  entrypoints, and runtime adapters
- platform repositories own broker or exchange credentials, account snapshots,
  order submission, dry-run/live switches, notifications, and rollout controls
- snapshot pipeline repositories own production feature snapshots, artifact
  manifests, point-in-time lineage, promotion evidence, and freshness checks

The current input shapes are close but not fully unified:

| Domain | Direct runtime inputs | Snapshot or derived inputs |
| --- | --- | --- |
| US equity | `market_history`, `benchmark_history`, `portfolio_snapshot` | `feature_snapshot`, `derived_indicators` |
| HK equity | `market_history` | `feature_snapshot` |
| Crypto | `market_prices`, `derived_indicators`, `benchmark_snapshot`, `portfolio_snapshot`, `universe_snapshot` | live-pool snapshot artifacts |

IBIT is the awkward but important case: the traded asset is a US-listed ETF, but
the signal source is crypto-native. A US broker may be able to buy `IBIT` while
being unable to provide reliable `BTC-USD`, AHR999, MVRV, NUPL, or other BTC
cycle indicators. That makes IBIT a good forcing function for a shared signal
source boundary.

## Main Design Pressure

The current platform-facing model has three pressure points:

- Data ownership is split by asset class, while strategies increasingly need
  cross-market signals.
- `market_history` is too generic for signal provenance; it does not say which
  vendor, transform, timestamp, currency, adjustment policy, or freshness check
  produced the data.
- Chain and cycle indicators such as AHR999, MVRV, NUPL, Mayer Multiple, Fear &
  Greed, and BTC dominance do not fit cleanly into broker platform market-data
  APIs.

The risk is not just missing data. The larger risk is silent inconsistency:
different platforms could calculate the same named signal differently, causing
the same strategy version to produce different live orders.

## Recommended Low-Risk Direction

Create a dedicated signal-source layer as a separate package or repository, but
roll it out through existing canonical inputs first.

Recommended shape:

- `QuantSignalSources` or `MarketSignalSources` owns fetch, normalize, derive,
  cache, freshness, and provenance logic.
- Strategy packages continue to consume canonical inputs only:
  `market_history`, `derived_indicators`, `benchmark_snapshot`,
  `feature_snapshot`, `universe_snapshot`, and `portfolio_snapshot`.
- Platform repositories do not calculate indicators directly. They call the
  signal-source layer or load its published artifacts, then pass canonical
  payloads into strategy entrypoints.
- Snapshot pipelines remain authoritative for monthly or research-backed
  snapshots. The signal-source layer can be a dependency of those pipelines, but
  should not replace their promotion evidence gates.

Minimal first contract:

```text
SignalBundle
  as_of
  generated_at
  source_name
  source_version
  freshness_policy
  symbols
  market_history
  derived_indicators
  benchmark_snapshot
  provenance
```

For IBIT, the first useful `derived_indicators` payload can stay small:

```text
derived_indicators["BTC-USD"] or ["BTCUSDT"]
  close
  sma200
  high252
  drawdown_252d
  sma200_gap
  rsi14
  ahr999
  ahr999_sma
  mayer_multiple
  provider_timestamp
```

This is enough for the current IBIT smart DCA mode. MVRV and NUPL should be
added only after choosing a stable data provider and storing provider terms,
lag, and historical coverage in provenance metadata.

## Why This Is Low Risk

This approach keeps the current strategy packages stable. Strategies do not need
to import provider SDKs, read environment variables, or know whether data came
from Binance, FRED, a broker, Glassnode, CoinMetrics, GCS, Firestore, or a CSV
artifact. Runtime adapters already understand input contracts, so the new layer
can be introduced behind those contracts.

For IBIT specifically, the current implementation already moves in this
direction: it prefers `derived_indicators` for AHR999 and only falls back to BTC
`market_history` when no external cycle snapshot is supplied.

## Not Recommended

Do not put direct vendor calls inside strategy packages. That would mix trading
logic, secrets, rate limits, retry behavior, and data licensing into code that
should remain portable.

Do not make each platform calculate its own AHR999, MVRV, or feature snapshots.
That guarantees drift and makes audit evidence platform-specific.

Do not replace snapshot pipelines with a generic data layer. Snapshot pipelines
own point-in-time evidence, promotion decisions, and artifact contracts; those
are governance functions, not just data-fetch functions.

Do not introduce a broad new abstraction before one live use case is stable.
Start with IBIT's BTC/AHR999 bundle, then generalize only after US, HK, and
crypto consumers agree on the shape.

## Suggested Module Scope

Phase 1:

- Add a signal-source repository or package with BTC daily bars, AHR999, Mayer
  Multiple, and provenance metadata.
- Add a small platform adapter hook that loads the IBIT `derived_indicators`
  bundle and passes it to `UsEquityStrategies`.
- Keep all strategy behavior behind existing runtime configs.

Phase 2:

- Move duplicated daily history normalization helpers into the signal-source
  layer.
- Add HK ETF market-history provenance reports and broker product-permission
  evidence references.
- Add crypto live-pool indicator builders only where they match the existing
  `CryptoLivePoolPipelines` artifact contract.

Phase 3:

- Add provider abstraction only after multiple providers are real:
  `BinanceKlinesProvider`, `FredIndexProvider`, `BrokerHistoryProvider`,
  `OnChainMetricsProvider`, and `ArtifactProvider`.
- Add freshness SLAs per signal family, not globally.

## Validation Strategy

Use characterization tests before moving production flows:

- same input bundle produces identical strategy decisions across platforms
- missing optional indicators degrade to documented fallback behavior
- stale indicators block smart mode or emit a clear `no_execute` / risk flag
- provenance fields are present for every external signal
- AHR999 values are reproducible from cached BTC bars within a documented
  tolerance
- platform dry-run logs include signal bundle version and provider timestamp

For IBIT, keep the current tests:

- ordinary DCA runs without fetching BTC history
- external AHR999 snapshot controls smart sizing
- price-history fallback still works when `cycle_indicator_enabled=false`
- expensive AHR999 zone can intentionally skip smart DCA buys

## Compatibility, Security, Performance, and Migration Risk

Compatibility risk is moderate because platform repositories must learn to pass
new canonical bundles. Keep `market_history` fallback until every target platform
has a signal-source integration.

Security risk is concentrated in provider credentials and artifact URIs. The
signal-source layer must own secret loading outside strategy packages and must
not place signed URLs, tokens, cookies, or account identifiers in strategy
diagnostics.

Performance risk is low for daily DCA and monthly snapshots, but the layer must
cache external calls and enforce rate limits. Live strategy evaluation should
read prepared bundles, not call slow vendors during order generation.

Migration risk is best handled per profile. IBIT should be the first cross-market
consumer because its current strategy already has a narrow BTC/AHR999 signal
surface and a fixed DCA fallback.

The concrete bootstrap plan for the future `MarketSignalSources` repository,
including crypto live-pool indicator families observed from the existing crypto
pipeline, is tracked in
`docs/research/market_signal_sources_bootstrap_plan.md`.
