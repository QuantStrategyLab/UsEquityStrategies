# Signal-source repository design — English overview

[完整中文设计](generic-signal-source-repository-design.md) · [Research index](README.md)

Scope: design recorded on 2026-06-19. Proposed repository names and migration phases describe that design, not current service or deployment status. The complete Chinese document retains the schemas, examples, tests and migration details.

## Ownership and interfaces

`MarketSignalSources` is an upstream artifact producer: provider adapters, raw caches, normalization, transforms, provenance and freshness belong there. Strategy packages retain pure strategy logic, catalogs, manifests and runtime adapters. Snapshot pipelines retain point-in-time evidence, promotion gates and production manifests. Platforms retain broker accounts, scheduling, artifact loading, dry-run/live controls, orders and notifications.

The IBIT MVP uses `StrategyContext.market_data["derived_indicators"]`, with `BTC-USD` as the canonical symbol. `BTCUSDT` and `BTC` remain consumer aliases. Other existing canonical input names are unchanged. The JSON schemas are `market_signal_bundle.v1`, `market_signal_manifest.v1` and `market_signal_index.v1`; see the [artifact contract overview](market_signal_bundle_artifact_contract.en.md) and [complete contract](market-signal-bundle-artifact-contract.md).

## Validation, security and migration

- Platforms validate schema, hashes, canonical input, consumer compatibility, provenance and freshness before strategy evaluation. Cache hits alone do not prove freshness. The proposed BTC daily policy allows at most 36 hours from the provider timestamp; other families use their own market/calendar policies.
- `freshness.status` supports `fresh`, `stale`, `missing` and `partial`. Smart IBIT sizing requires fresh inputs; the strategy itself does not enforce bundle metadata, so the platform loader must enforce it.
- Vendor SDKs, provider keys, rate limits, bounded retries and data-license handling stay upstream. Broker credentials stay in platforms. Platforms receive only the artifact read access they need. Signal production may call vendors; strategy evaluation consumes prepared artifacts.
- Bundles and provenance must exclude tokens, secrets, cookies, signed URLs, account IDs and raw broker payloads. Logs and notifications use only non-sensitive audit summaries.
- Migrate one profile at a time. Preserve v1 readers until consumers migrate; use characterization tests for identical decisions across platforms. Provider changes require updated provenance and at least one complete platform dry-run cycle before live use.

Provider access and `license_scope` metadata do not by themselves grant redistribution or runtime rights. Preserve applicable provider terms and the repository [license](../../LICENSE). This design does not authorize deployment, strategy enablement or trading.
