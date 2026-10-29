# Explicit session timing: synthetic-only fixture contract

## Status and minimum plan

This batch is a local, opt-in research candidate, not adopted production behavior.
Base: UsEquityStrategies `4652c67d0972d4b63d755a291c2c9f76cda63804`.
Keep QPK `62bcd5f6d5e236c315a7383ddf7b35e2aac30b62` and QSP
`3416da47580be1537183e378f3ca9efacc6f9b8c` unchanged.

1. Establish failing synthetic regressions for both profiles and the unchanged checker.
2. Validate one frozen calendar declaration and its exact replay projection.
3. Pass its successor into the two private builders during timing construction;
   share the existing replay loop, fills, fees, positions and strategy algorithms.
4. Verify rejection/causality, ordinary-day economic equality, production defaults,
   identity binding and all available existing replay regressions.

Only this document, entrypoints/__init__.py, research/optimized_strategy_replay.py,
and tests/test_optimized_strategy_replay.py change. No loader, CLI, persistence,
provider, broker, live configuration or pin changes. Archived evidence is immutable.

## Boundary and evidence

The old QPK business-day helper skips weekends only. An explicit research calendar
can name 2026-09-08 as the successor of 2026-09-04 while the old helper names
2026-09-07; the existing replay checker correctly rejects that inconsistency.
NYSE lists September 7 as the 2026 Labor Day closure:
https://www.nyse.com/trade/hours-calendars . This demonstrates a research contract
conflict; it is not evidence of a production wrong-order incident.

The new `replay_synthetic_session_fixture(request, *, session_timing=...)` is
explicitly opt-in. `replay_optimized_strategy` retains its old path. The original
`_check_timing` remains unchanged and mandatory. No decision is rewritten after
the builder returns. Production `compute_*` wrappers do not pass research timing.

The calendar wire is a structural reference to QSP main `9f02eeeed7d5b4c05cc284206f4b4a17040e04ea`,
[semiconductor research contract](https://github.com/QuantStrategyLab/QuantStrategyPlugins/blob/9f02eeeed7d5b4c05cc284206f4b4a17040e04ea/docs/semiconductor-regime-observer-research.md#L49-L83).
It is not present in the pinned QSP revision and is not imported or adopted from
that pin. UES validates the supplied wire locally with its existing SessionClose.

## Frozen declaration

`session_timing` has exactly these keys:

- source: nonempty attribution text, a caller declaration, not certification
- calendar_id, calendar_version: nonempty fixed identifiers; mutable aliases rejected
- timezone: exactly America/New_York
- coverage_start, coverage_end: canonical YYYY-MM-DD, inclusive replay coverage
- calendar: exact QSP-style keys id/version/available_at/sessions; sessions contains
  exact date/close_at/complete rows in strictly increasing unique order
- sha256: lowercase SHA-256 of the normalized declaration without this key
- fixture_only: true
- historical_pit_verified, real_entry, promotion_eligible, live_executable: false

IDs/versions must match the frozen calendar, and request.calendar_id must match.
Normalize all UTC timestamps to six fractional digits and Z; normalize dates to
YYYY-MM-DD. Hash UTF-8 JSON, sorted keys, separators comma/colon, ensure_ascii=false,
allow_nan=false. The full normalized declaration, including its digest, is bound
into the replay run identity and returned research provenance. It is not inserted
into effective production strategy parameters.

Session closes reuse UES SessionClose: UTC, matching New York trading date, exactly
16:00 or 13:00 New York including DST conversion. No sort, fill, invented session or
calendar library is allowed. The replay dates equal every complete source session
in the declared interval, including the final fill/valuation successor. The first
and last replay date must equal the declared endpoints. Incomplete source sessions
inside that interval are rejected rather than silently omitted.

This is consistency against a supplied frozen source, not official calendar
authentication. Removing a day from both the source and prices and recomputing the
digest cannot be detected without independent authoritative evidence. A declared
13:00 close is structurally valid; this interface cannot authenticate which dates
are officially half days. Output is always fixture_only with
historical_pit_verified=false, real_entry=false, promotion_eligible=false and
live_executable=false. REAL_ENTRY_PIT_REQUIRED remains the real-evidence barrier.

## Causality and economics

Every signal date must have its existing state_inputs row and explicit decision_at.
There is no synthetic 21:00 fallback on this path. The calendar must already be
available and the consumed signal session must have closed by that decision, with
New York date consistency. Successor membership means a scheduled future session
is known, not that its OHLC is already available. Existing DatedBar, state value and
indicator provenance checks are mandatory for consumed signal data.

There are three separate clocks: each signal's decision_at, the successor's
execution/valuation session, and the research result's computed_at. Root review
found that the first candidate could value a terminal bar published at 22:00 while
claiming computed_at=20:00. A dedicated RED regression now guards this correction:
on the explicit path computed_at must be UTC, at or after every used signal
and option decision, the terminal valuation close, and every read price's
available_at. Existing indicator/state/option source deadlines remain bounded
by their consuming decision. Canonical computed_at is also bound to run identity.
Future calendar arrangements outside the replay interval and unused terminal
benchmark rows are not completion prerequisites. This does not change the old
entrypoint's computed_at behavior.

Next-open fills and subsequent close valuation stay separate from signal
visibility. The final successor's complete daily bar is allowed for retrospective
valuation; it is not required to exist before that session opens, but it must
exist by the claimed research completion time. No fill, cost,
position, strategy, return, or metric formulas change.

## Verification

Tests are offline synthetic fixtures. Cover both profiles' holiday mismatch/new
success, ordinary-day exact economic equality, exact projection and shape errors,
digest and identity changes, timezone/DST/half-day syntax, decision/calendar/data
availability, immutable source inputs and prohibited real/live/promotion claims.
Focused and aggregate results are recorded with the candidate review evidence;
this document does not claim full real-history validation or production adoption.
