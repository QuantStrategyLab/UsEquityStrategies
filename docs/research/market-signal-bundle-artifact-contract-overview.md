# Market Signal Bundle contract — English overview

[完整契约与示例](market-signal-bundle-artifact-contract.md) · [Research index](README.md)

This overview preserves the consumer boundary of the contract recorded on 2026-06-19. The complete linked document remains the reference for every field, CLI flag, schema and extraction helper; no interface is changed here.

## Artifact and validation rules

| Artifact | Schema and required behavior |
| --- | --- |
| Bundle | `market_signal_bundle.v1`; identifies bundle/type, canonical input, `as_of`, generation time, symbols, symbol-keyed `derived_indicators`, freshness and provenance. |
| Manifest | `market_signal_manifest.v1`; pins the relative bundle path and SHA-256; bundle ID, `as_of`, canonical input and optional schema/freshness summaries must match the bundle. |
| Index | `market_signal_index.v1`; pins each manifest path/hash and identity. Select the requested bundle, or the latest fresh entry with `as_of <= requested_as_of`; without a date, select the latest fresh entry. |

Reject absolute paths, directory traversal, hash/schema/identity/freshness mismatches and sensitive fields. If a manifest declares a quality report, verify its hash, schema and fields, and reject `quality_status=fail`. Index summaries never replace full manifest and bundle validation. Before smart sizing (`smart_multiplier_enabled=True`), require a fresh bundle and validate consumer profiles, required fields and symbols. Inject only the validated canonical payload into `StrategyContext.market_data`.

## Consumer registry and handoffs

Use `market_signal_consumer_for_strategy_profile()` rather than platform-specific mapping tables. The documented runtime mapping covers `ibit_smart_dca`, `nasdaq_sp500_smart_dca` and `soxl_soxx_trend_income`.

Registry manifest checks pin paths, SHA-256, schema, size and consumer coverage. Full local-registry identity requires `--require-all-known-consumers` and `local_contract_registry_verified=true`; canonical digests ignore JSON formatting and contract order. Platform handoffs pin the bundle, source-family catalog and consumer registry manifests; handoff indexes still require linked-hash, freshness and coverage checks. Saved consumption audits must retain the same artifact identity and injection permissions. When unified signals are required, use a valid handoff or valid `last_valid` artifact rather than recomputing locally.

Research handoffs and `research_export.v1` validate local CSV/quality-report hashes and sizes, transform/family coverage and consumer registry identity. They return audit summaries, not runtime payloads. Research CSV must not enter the platform runtime handoff path. Precomputed research checks reject duplicate/non-monotonic dates, dates beyond `as_of`, invalid percentiles, non-positive AHR999/Mayer values and non-finite slopes. Nasdaq/S&P context research also requires the applicable availability report and rejects failed quality evidence.

## Data and authority boundaries

Strategy code validates and extracts local artifacts; upstream producers own vendor access, caching, transforms and publication. Platforms own loading, validation and injection, and must not call vendor APIs during evaluation or alter strategy formulas. Preserve provenance including provider, dataset, transform, versions, raw hash and license scope. Logs contain field names/counts, hashes and provenance summaries, never indicator values, raw responses, tokens, cookies, signed URLs, secrets or account data.

Runtime coverage requirements are explicit; research validation cannot substitute for runtime coverage or permission to enable trading. Retain provider rights and the repository [license](../../LICENSE).
