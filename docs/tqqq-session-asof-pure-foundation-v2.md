# TQQQ pure session/as-of foundation v2

This slice contains only two frozen immutable contracts: `SessionClose` and
`RequestedObservedWindow`. `SessionClose` accepts caller-supplied
`trading_date` plus canonical UTC `close_at_utc`; after conversion to
`America/New_York`, local time must be exactly 16:00 regular close or 13:00
approved half-day close, with zero seconds and microseconds. No holiday or
half-day calendar is inferred.

The window keeps requested dates separate from observed dates. `as_of` is
derived and must equal the last observed session. Missing requested end fails
by default; an explicit opt-out still reports the actual earlier `as_of`.
Both wire shapes have exact keys, strict parsing, deterministic canonical JSON,
and no user metadata or mutable mapping is retained.

Portfolio snapshots, metadata merging, simulator logic, metrics, persistence,
QPK, live entrypoints, network, orders, funds, and leverage are intentionally
outside this PR.
