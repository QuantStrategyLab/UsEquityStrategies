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

## Daily alpha, volatility latch and intent

The TQQQ daily-state contract keeps the trend alpha latch, alpha epoch, volatility latch and pending intent in one strategy-owned `tqqq_daily_state.v1` transition. The volatility latch enters at the entry threshold and remains set until a valid observation falls below the exit threshold, regardless of holdings, partial retention or the current alpha target. A prior intent is invalidated on alpha exit, an active volatility latch or a new session; releasing a risk control does not revive it.

The entrypoint accepts `StrategyContext.state["tqqq_daily_state"]` with a QPK identity, prior transition, explicit bootstrap flag, frozen input digest, expected prior session, and optional current-session intent. It returns the proposed QPK transition and status in decision diagnostics. A proposal is not a durable commit: the platform must verify continuity and the frozen inputs, perform the guarded append and readback, and fence execution against the confirmed state. A subsequent evaluation may use a confirmed same-session head with the identical input digest. Risk increase additionally requires `execution_fence_confirmed=true`, which the platform may assert only after enforcing the execution boundary; an absent fence retains the confirmed latch for reductions. A conflicting retry, missing chain, read failure, malformed state or missing continuity evidence cannot authorize a risk increase. Independently determined reductions remain available. The expected prior trading session must come from a validated platform calendar; the generic state store's date ordering does not establish calendar continuity.

A custom configuration using partial volatility retention with an exit threshold below the entry threshold requires migrated, continuous daily-state evidence. Without it risk-asset targets are capped at observed holdings, rather than inferring the hysteresis latch from holdings. Ordinary legacy configurations with equal thresholds or zero retention retain their existing behavior until enrolled in the daily-state contract. Explicitly enrolled operation with unavailable state is reduction-only. Bootstrap is an explicit platform-authorized genesis operation, not an inference from an empty or failed read. State persistence does not itself establish execution authority or prove that stale broker orders have been canceled.

The ordinary entrypoint ignores caller-supplied execution-fence booleans. A
separate conditional planning adapter accepts only a live session-owned QPK
journal handle bound to the current strategy head, intent, session and input
identity. It returns a non-executable candidate, not an order authorization.
The platform must run the central risk gate and reserve and dispatch each
concrete order through a fresh joint journal session. The adapter snapshots and
hashes the decision context, excluding only the separately journal-bound daily
state, and verifies that digest against the journal and handle. Unsupported
opaque context values fail closed. The platform must revalidate the same input
identity at submission. A digest does not authenticate its source or prove a fill.

## Production decision replay boundary

`research.production_decision_replay` evaluates frozen TQQQ and SOXL snapshots with their production decision builders and the QPK explicit-clock offline risk gate. It recomputes config and input hashes, checks the selected loaded strategy source files, and requires a dated portfolio snapshot. TQQQ benchmark observations are ordered and bounded by session and availability time; SOXL indicators carry an availability timestamp. The trace records actual target values and an offline gate record, with execution, promotion and live readiness disabled. Appending later observations cannot alter an earlier decision.

The replay distinguishes a producer's timestamp assertion from independently authenticated publication evidence. Historical inputs remain blocked unless a trusted verifier establishes immutable publication evidence tied to the input digest and decision clock; a trusted source verifier is likewise required to connect a revision claim to the checked source. The tool identifies missing prices for actual target symbols, missing price availability times, and incomplete rounding, minimum-trade, cash-settlement or corporate-action declarations. Those declarations do not simulate fills or prove completeness. This boundary does not reconstruct cash, positions or orders between snapshots, validate the entire source dependency closure, or establish production economic parity; its result remains blocked for that claim. The existing optimized fixture replay and frozen research gates retain their own contracts.

`research.production_economic_replay` adds a bounded synthetic ledger over these decision records. It carries cash, average cost, quantities and, when enrolled, the QPK daily-state transition. It evaluates signals at supplied closes and fills at the open of the specified later session; every new alpha generation invalidates older buy authority, while due reductions remain eligible. Buy tokens are bound to the carried TQQQ state and epoch. The ledger models supplied lot increments, minimum trade value, fees, slippage and settlement delay, and limits buys to settled cash. Synthetic split and dividend events occur before the session open; dividends accrue on the ex session and pay once on a later session for the same symbol. External cash flows are explicit. Session rows, assumptions and outputs are hashed.

The TQQQ daily contract expires an entry intent at the next session. With a lag greater than one session and daily re-observation, the simulator therefore cancels earlier TQQQ buys even when the text token is reused; due reductions remain queued. For a static SOXL fixture, a continuously unchanged synthetic intent token can remain eligible through the specified lag; changing the token invalidates the previous generation. The session sequence and synthetic event inputs are caller supplied. The ledger does not independently validate exchange-calendar completeness, broker partial fills, order queues, options, tax treatment or actual historical corporate-action publication. Its confirmations and execution fence exist only inside the simulation. Neither a synthetic ledger nor complete declarations establish historical production parity or confer execution authority.

## Runtime decision observation

TQQQ and SOXL entrypoints can emit an optional QPK decision-consumption receipt after the actual risk gate returns. The platform supplies a real run identity, an explicit timezone-aware decision clock, optional strategy/config identities and loader observations. The same explicit clock must be accepted and recorded by the gate; the signal session is a separate field. The receipt hashes the actual pre-risk and post-risk targets and does not itself establish an order, fill or broker confirmation.

Each loader observation identifies the source reference and logical consumer separately. A consumed observation is recorded only when the loader's canonical hash of the sanitized consumer payload matches the mapping selected by the strategy. Position authority is taken from the strategy's resolved authorization, not inferred from a route or artifact presence. Missing reads, unavailable hashes and unmatched payloads remain unavailable or unknown. The loader's physical `observed_at` is distinct from independent publication availability. The platform may attach an existing execution receipt only after that evidence exists. Receipt support is optional and older QPK versions retain their normal decision path. Receipt construction failure itself leaves targets unchanged; an explicitly supplied gate clock is independently validated by the risk gate and may cause a risk rejection.

## Runtime and frozen research sources

The original TQQQ growth, TQQQ dual-drive core and SOXL trend modules remain byte-identical to the source files bound by the existing core-only research protocol. Live TQQQ and SOXL entrypoints and their catalog components use the corresponding versioned `*_live_v2` modules, which contain the authority, signal and state corrections. Frozen research generators retain their original imports. Production decision replay identifies both the versioned runtime sources and the retained legacy sources; a source hash is not a claim that a runtime deployment has occurred.
