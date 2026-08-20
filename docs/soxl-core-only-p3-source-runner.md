# SOXL core-only P3 source runner

`us_equity_strategies.research.soxl_core_only_p3_source_runner` is the
source-side execution seam for the frozen
`soxl_soxx_core_only_p2_v2` candidate.  It gives a future P3 verifier one
small, JSON-only way to call the public P2 v2 adapter at this repository's
exact revision.

Its input is a fully materialized research context, not a provider request:

- `qsl.soxl-core-only-p3-strategy-context.v1` contains the as-of timestamp,
  a USD research portfolio, the two symbols' derived indicators, and the
  frozen runtime configuration.
- It accepts only `SOXL`, `SOXX`, and `BOXX` portfolio state.  Non-core
  holdings, account IDs, arbitrary metadata, incomplete indicators, and
  non-JSON runtime values are rejected.
- The public adapter still enforces the core-only boundary: income/option
  sleeves, AI, external market-regime control, and volatility-retention
  cannot be re-enabled through the runner.

On success it emits `qsl.soxl-core-only-p3-decision.v1`: the three target
values, a short deterministic diagnostic summary, and the SHA-256 of that
canonical output.  It deliberately omits rendered messages, account IDs,
orders, credentials, raw bars, and execution annotations.  A malformed input
or source decision produces a sanitized `PARKED` result instead of a fallback
strategy result.

The runner has no P1 authority.  A future pipeline P3 verifier must first
validate the complete immutable P1 manifest/binding and the full P2 candidate
hash, then run this module inside this source revision's own `uv.lock`
environment.  It must not upgrade the shared TQQQ runtime or use a mutable
`latest` source checkout.  This module performs no network, storage, workflow,
recording, risk-assessment, sizing, or order operation.
