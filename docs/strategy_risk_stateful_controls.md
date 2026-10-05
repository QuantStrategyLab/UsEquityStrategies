# Stateful volatility-deleveraging control

`us_equity_strategies.volatility_delever_cooldown` is a pure, reusable rule
for strategies that need to prevent immediate re-entry after a local
volatility-deleveraging event. It can apply to SOXL, TQQQ, TECL, or another
strategy, but it is deliberately not part of a broker, plugin, allocation, or
runtime target.

For a two-session cooldown, a trigger blocks the trigger session plus the next
two effective sessions. A further trigger resets the countdown. Re-entry may
only be considered in the following session, and still needs every existing
strategy/regime/position guard to pass.

The helper refuses malformed state, stale dates, changed cooldown settings, or
ambiguous boolean input. `build_volatility_delever_cooldown_transition` then
wraps the result in QPK's immutable `StrategyRiskStateTransition`, binding it
to the frozen strategy candidate, configuration, account scope, frozen input,
and prior transition.

This is a research and paper-adapter building block only. It does **not** alter
the current SOXL configuration, make an unqualified candidate promotion
eligible, persist state, enable a platform, or submit an order. A future
paper-only platform adapter must use an append-only store and fail closed on a
missing predecessor, duplicate writer, stale source, or divergent frozen input.

## TQQQ plugin-release candidate contract (draft v1, 2026-10-04)

### Scope and adoption status

This is a **new, unimplemented research policy**, proposed as
`tqqq_qqq_guard_cash_release_intent_research_v1`. It is not an edit to
`tqqq_qqq_guard_cash_research_v1`, the core-only comparator, the runtime catalog,
the shared dual-drive core, or the default/live entrypoint. A package version,
plugin permission bit, research approval, AI review, and Risk Gate success
cannot independently authorize a buy. Restoring a plugin permission removes an
interception; it does not replenish an old strategy intent.

The baseline inspected here is UES
`cc05a78da902c3762a5766291213580cb458e698`, with QPK pin
`62bcd5f6d5e236c315a7383ddf7b35e2aac30b62` and research QSP pin
`3416da47580be1537183e378f3ca9efacc6f9b8c`. The pure core rebuilds dollar
targets from current NAV and scalar inputs each call. Its `entry`/`hold` label
is derived from current holdings, not a durable alpha-intent version: a
risk-forced liquidation can therefore produce `entry` while the original
alpha condition never turned false. This label is **not** a release event.

Reuse the existing QPK `StrategyRiskStateIdentity`,
`StrategyRiskStateTransition`, and `StrategyRiskStateStore`; do not create a
second state store, engine, authorization service, or execution-intent schema.
The pinned store already has content-addressed transitions and atomic,
create-only successor writes. UES uses the transition contract for its pure
volatility cooldown, but neither TQQQ core nor its private replay currently
loads/stores this release state or issues the strategy intent defined below.
An empty loaded chain is not proof of a first run. These are adoption blockers,
not capabilities provided by this document or its synthetic specification.

### Frozen identity, input, and minimum state

Use the existing identity fields: `strategy_profile`, logical `account_scope`,
the **new** `candidate_id`, and the frozen `config_sha256`. Freeze the exact
strategy source revision, policy version, instrument identity, long-only
quantity convention, session calendar, signal/effective-time rule, and intent
expiry in that configuration. New revision/config means a new chain; never
silently reuse another candidate's state.

The transition's `input_sha256` must cover the previous alpha state, previous
intent version, complete settled holdings, all potentially risk-increasing
open orders/reservations, confirmed cancellation/fill status, prices/NAV,
corporate-action basis and its evidence digest, every applicable plugin cap,
their requested/observed/effective times, and the decision-time snapshot. Bind
the full inputs in a frozen local bundle; state stores only non-secret digests
and bounded quantities, not broker account/order IDs or raw responses.
Unattributed orders, incomplete ownership/positions, stale input, ambiguous
corporate actions, or mismatched snapshot times block **new** risk. They do not
trigger guessed liquidation or imply no-action/low-risk.

The proposed strategy-owned state object contains:

- `schema_version=tqqq_plugin_release_state.spec.v1`, and explicit initialization
  evidence separate from an absent chain
- instrument and split-adjusted long-quantity basis, with the last applied
  corporate-action digest/basis; all quantities are finite and nonnegative
- nonincreasing retained quantity ceiling, the latest restriction signal
  session/event digest, and the blocked intent-version watermark
- last consumed strategy intent sequence, alpha-epoch ID, and intent digest
- the bounded result quantity/reason for deterministic replay

Existing transition fields bind effective session, prior transition digest,
identity, frozen input digest and result digest. The existing contract is
one transition per effective session; this candidate supports a single daily
decision. Multiple intraday revisions or fills needing a new state transition
in that same session are unsupported, not silently assigned fake dates.

### What constitutes a new valid strategy intent

The strategy, rather than plugin or AI, must issue an immutable version bound
to the exact frozen candidate/source/config and input digest. The minimum
version has a strictly increasing sequence, a new alpha-epoch ID, a
content-addressed intent digest, completed signal session, permitted effective
session/expiry, and an explicit maximum quantity in the declared basis.

For draft v1, issuance requires a validated **alpha-condition false-to-true
edge** in the complete strategy stream. The prior false observation and new
true observation belong in the frozen input. Both must be available at the
decision time. A new timestamp, a new hash of the same hold target, changed NAV,
ordinary rebalancing, a risk-created empty position, or a permission flip does
not create a new alpha epoch. An intent is new only if its sequence exceeds
the consumed/restriction watermark, its alpha epoch differs from the consumed
one, its exact identity matches, and it was issued after the restriction it
would release. An expired, replayed, foreign or forged version is rejected.

This draft deliberately does not define a generic manual override or
`approved_refresh=true` bypass. An alternative strategy-rearm rule would need
its own frozen policy/version and evidence. A real human decision, if later
supported, needs a separate attributable approval receipt bound to candidate,
intent and scope. `ai_audit=agree/ok`, confidence/text, successful notification,
`automation_approved`, or `research_backtest_approved` are not such receipts.

The false-to-true issuer is a deliberately strict draft choice, not an
economically optimal rule established by user approval. In a prolonged positive
trend, a restriction may leave the strategy underallocated until a later true
alpha exit/entry cycle. Measure missed recovery, time underallocated, turnover
and tail risk against unchanged comparators in untouched OOS/forward evidence.
Approval of the new-intent principle does not validate this issuer's economics.

### Quantity ceiling and transitions

The narrow candidate trades only long TQQQ plus residual cash. Track
intentional share exposure in a consistent split-adjusted basis; do not compare
old dollar targets, target weights or today's NAV. Passive price/NAV changes
may change marked USD exposure without granting additional shares. Existing
independent nominal/effective/account risk caps still apply and can be tighter.
This long-only rule does not claim validity for shorts, options, hedges or
multi-leg substitutions.

1. **Explicit genesis:** a distinct validated initialization receipt plus a
   complete flat account and zero pending-risk reservations may start a new
   chain. No intent means a zero buy allowance. A valid initial strategy entry
   may buy up to its quantity authorization and current plugin/account caps,
   even if the initial plugin cap is already reduced. An absent predecessor
   after initialization, or a nonflat migration treated as genesis, blocks new
   risk. The existing cooldown's permissive `previous_state=None` behavior
   must not be copied as a release-state recovery policy.
2. **Restrict or retain:** for an existing chain, ratchet the retained ceiling
   down to the tighter applicable quantity proposal and reconciled exposure.
   Settled quantity plus remaining pending buys/reservations is worst-case
   outstanding exposure. Pending sells do not provide headroom until fills
   are confirmed. Confirmed sells/cancellations reduce the retained ceiling;
   the old intent cannot refill it. Update the restriction event/watermark on
   a new tightening event; observing the same already-reduced cap again does
   not manufacture a new event. A proposed lower ceiling does not make
   already outstanding buys disappear: reconcile/cancel them through an
   authorized platform before issuing more risk. This draft submits nothing.
3. **Permission restored, old intent:** preserve the retained ceiling. Choose
   no more than `min(current capped proposal quantity, retained ceiling)`.
   The incremental buy allowance is at most
   `max(0, ceiling - settled quantity - pending buy/reservation quantity)`;
   repeat delivery must not multiply this allowance. Target reduction remains
   possible. Do not normalize residual cash back into TQQQ.
4. **Fresh strategy entry after restriction:** once the frozen plugin policy
   permits new risk **within its current caps**, a valid new alpha edge/version
   may establish a new ceiling,
   bounded by that intent's maximum quantity, current proposal, cash and every
   applicable risk cap. Existing holdings/open orders must first reconcile.
   Permissive does not mean every scalar equals 1: a still-reduced cap may
   permit an initial or subsequent genuine strategy entry within that cap.
   A blocked/risk-off policy does not. A fresh intent cannot bypass a newly
   tightened restriction with the same signal-session version. Acceptance
   consumes the version exactly once. Its authorized quantity is
   not permission for later canceled/filled sells to be replenished indefinitely.
5. **Exit and re-entry:** normal alpha exit ratchets the ceiling down as fills
   complete. Risk-only liquidation preserves the consumed alpha epoch. It
   cannot manufacture a fresh entry when the original alpha remains true.
   Complete alpha false then true, with a valid new version, is needed for a
   subsequent entry. A pending entry means the account is not an initial flat
   entry even when settled quantity is zero.
6. **Splits:** adjust ceiling, settled quantity and remaining order/reservation
   quantity by the same verified effective split factor exactly once. A 2:1
   split doubling all share counts permits zero economic new risk. An unknown
   action, duplicate application, unsupported fractional settlement or basis
   mismatch blocks new risk; no inference from a price halving. Passive drift
   is reported separately from deliberate quantity change.

For example, NAV 1000 and a 450 USD baseline become 112.5 USD under the
existing two scalar dimensions at 0.5. At price 10 the retained ceiling is
11.25 shares. Restoring the scalars to 1 does not authorize the old intent to
buy the additional 33.75 shares. If price falls to 5, keeping the former
112.5 USD target would also wrongly buy another 11.25 shares; the quantity
ceiling prevents this. A verified split instead changes the ceiling and
holdings together and is not that price-only case.

### Narrow seam, persistence and migration

For a reviewed future implementation, keep `decide_tqqq_dual_drive` unchanged.
Calculate the existing signal-day proposal first, then apply the release rule
only in a new research adapter's effective-session quantity sizing, after
verified corporate actions and before any positive target delta. This is the
seam corresponding to `_simulate`'s `target_qty` calculation in the private
guard/cash replay. Recompute residual cash consistently and retain complete
cost/self-financing accounting. Calling `_decision` alone is not enforcement:
next-open price gaps, holdings and pending risk must still be checked at the
quantity seam. A production adapter would also need execution-time Risk Gate
and command/reservation idempotency; a state receipt is not an order receipt.

Validate input/chain and calculate deterministically, then append through the
existing store before releasing any newly increasing candidate target. An
identical session/input retry must return the stored result/transition digest
and `ALREADY_APPENDED`, without recomputing against changed holdings or consuming
the intent again. A different input in the same session, stale predecessor,
competing successor, missing chain, identity/config change, or append failure
blocks new risk. After a crash between append and downstream publication,
reconcile the exact receipt and command/reservation state; do not issue a second
command merely because the process restarted. QPK's state store alone does not
make downstream order publication exactly-once.

Migration starts a **new** candidate/chain using an explicit reconciliation
receipt. Already held positions may initialize a retained ceiling no greater
than reconciled quantity plus genuinely outstanding buys, additionally capped
by current applicable restrictions; they cannot initialize a fresh entry.
Missing older release history/intent provenance means no new risk until the
explicit migration and a genuinely new strategy intent are valid. Do not
reinterpret old P1/P2/P3, existing observation artifacts or target-equivalence
receipts as acceptance of this new behavior. Freeze new source/config/input
identities, retain paired unchanged core/guard comparators, and run prospective
and untouched OOS acceptance separately before any promotion. This document
does not authorize that backtest, installation, paper/live wiring or deployment.

### Synthetic specification and remaining blockers

`tests/test_tqqq_plugin_release_contract_spec.py` is an executable **reference
specification in tests**, not a production helper or adapter. It reproduces
the unmodified core's 112.5 to 450 hold restoration, checks proposed quantity
invariants with artificial states/intent records, and exercises the real pinned
QPK transition/local-store seam. It is not return evidence or execution proof.

Covered boundaries include permission-only release, price/NAV drift, verified
splits, pending buys/sells, confirmed exposure reductions, initial entry versus
old hold/risk-forced flat, exact intent identity/version/expiry, invalid or
missing state, AI-origin attempts, identical delivery, conflicting input,
missing predecessor and competing successors. Adoption remains blocked on:

- actual strategy-owned alpha epoch/intent issuance and validation in TQQQ
- explicit initialization/migration receipts and crash/missing-chain lifecycle
- an isolated new research adapter consuming the real quantity rule/state
- effective-time snapshot completeness and corporate-action provenance
- downstream command/reservation idempotency and reconciliation, when an
  execution consumer is separately authorized

Do not patch the pure core with a `previous_target` float or add a plugin
`allow_buy`/AI approval flag to bridge these missing contracts.

### Implemented no-rearm prerequisite (isolated, not adopted)

`research.tqqq_plugin_release_retention` now implements only the independently
testable **retention** half of this candidate, through
`build_tqqq_plugin_retention_transition`. The actual core still has no
holdings-independent, strategy-owned alpha-observation/epoch decision contract.
Its `entry`/`hold` labels cannot be substituted for that contract. The helper
accepts **no intent**, manual-refresh flag, AI verdict, or positive buy
allowance. It is not the complete release rule, an alpha issuer, an executable
research replay, or an execution adapter. The strict false-to-true rule's
economics remain unvalidated.

The pure helper consumes a complete frozen effective-session quantity snapshot
and the existing parsed QPK identity/transition. The caller must calculate the
core proposal first and separately supply nominal, effective and account
total-exposure cap quantities in the same verified basis. The helper intersects those
caps; it does not collapse their meanings into one scalar. It floors to the
declared quantity step and never normalizes residual cash back into TQQQ.
Already-funded holdings remain part of total exposure when free cash is zero.
This no-buy prerequisite has no free-cash sizing gate: cash divided by price
is not a total-exposure cap or a liquidation instruction. A future fresh-intent
adapter must enforce incremental cash affordability, costs and reservations
separately before any purchase.
For an existing chain, its ceiling is at most the split-adjusted prior ceiling,
the current capped proposal, and settled shares plus remaining pending
buys/reservations. Pending sells do not create headroom. Confirmed sales or
cancellations ratchet down. Outstanding orders above the result remain counted
and set `reconciliation_required`; the helper does not cancel or liquidate them.

The distinct `tqqq_plugin_release_retention.v1` state is explicitly
`no_rearm_only`. A caller-validated initialization receipt is mandatory for a
root: flat genesis starts at zero; migration retains at most current capped,
reconciled outstanding exposure. Neither allows a buy. An absent chain after
initialization must not be reset with another receipt. The helper cannot
detect erased storage or independently validate receipt authenticity, snapshot
completeness, market calendars, availability timestamps, or corporate actions.
The referenced local snapshot bundle must contain that evidence, including
attributed orders/reservations, confirmed fill/cancel status, prices/NAV, all
cap requested/observed/effective times and the decision-time cutoff. Assertions
and hashes supplied by a caller are not evidence validation.

Quantities use bounded canonical decimal strings (0 through 10^18, at most
18 decimal places). A verified split changes the prior ceiling and reconciled
positions/orders in the same basis; the helper rejects basis mismatches,
duplicate events and unsupported fractional settlement. It retains at most
32 distinct post-initialization split digests and fails closed at that limit;
it never truncates history or silently starts a new chain. Pre-migration action
history must be reconciled in the explicit migration evidence.

No second store or command schema is introduced. The caller must load and
append with QPK's existing `StrategyRiskStateStore`. Identical same-session
input returns the exact parsed stored result, before quantity recalculation or
split application; changed holdings, caps or snapshot digest conflict. A stale
session, foreign identity, malformed strategy state or mutated transition fails
closed. Store tests cover missing predecessors and competing successors. This
is still one daily transition; later intraday fills require reconciliation,
not an invented date or a second calculation. A stored state receipt does not
prove downstream command idempotency or authorize an order.

`tests/test_tqqq_plugin_release_retention.py` exercises the real source helper
with artificial inputs and the actual pinned local QPK store. The earlier
test-only full-policy oracle remains unchanged. Default core, replay, catalog,
entrypoints and dependency pins are unchanged, and no runtime consumes this
module. A future fresh-intent path needs its own reviewed alpha decision
contract, initialization/missing-chain lifecycle, prospective/OOS economics,
and separately authorized snapshot and execution integration.


## Bounded TQQQ synthetic contract acceptance (2026-10-05)

This addition is **test-only specification acceptance**, not a `src` feature,
R1 observation validator, complete R3 closure, or runtime adoption. The used
caller is `_run_toy_cycle` in `tests/test_tqqq_plugin_release_contract_spec.py`.
It calls the real `decide_tqqq_preplugin_risk_on`, projects an issued version
into the existing test-only quantity oracle, then appends one combined record
through the real pinned QPK **local** `StrategyRiskStateStore`. No second store,
provider, broker, command, scheduler or model path is added. The immutable
retention-v1 and ordinary strategy callers remain unchanged.

The synthetic fixture configuration binds UES source revision
`416356b4816390a674e8af7a4aba2e9f17d2d474` and the actual predicate-containing
source-file SHA-256
`d57745b0de59291a08293f9f07ba60152d5362b1f323d4b7cc088973930e110c`.
Its configuration digest covers that binding, the separate-latch policy,
unknown-history bootstrap, toy-input domain and one-effective-session expiry.
The issued version binds candidate/revision/config, full frozen fixture input,
prior false/current true observation digests, increasing sequence, epoch,
signal/effective/expiry and maximum quantity. This is content consistency with
a fixed source file, not evidence of deployed code or genuine data provenance.

### Separate logical state and conservative bootstrap

Persist `trend_latch`, overall `eligibility` and `armed` separately. Only
`trend_latch` is passed as the predicate's next `prior_risk_active`; overall
eligibility is `trend_risk_active OR pullback_risk_on`. A below-MA200 pullback
can be eligible while the trend latch is false. Feeding that overall true
back into the trend prior would incorrectly hold at an above-MA200 observation
whose entry slope is nonpositive. The added counterexample exercises this
actual predicate, rather than repeating its formula in another implementation.

Unknown genesis stays unarmed with unknown trend/eligibility, including when
both possible prior states imply a currently forced-true observation. It does
not invent an earlier false. Only an observation independently false under
both possible trend priors arms the fixture. A later valid false-to-true edge
increments the issued sequence once; holding true, risk-only liquidation,
changed NAV, AI agreement, permission relaxation and cooldown completion do
not. An edge observed while blocked advances the issued watermark without
positive allowance, and cannot be repackaged when the block disappears.

### Risk state meanings and actual coverage

Keep `policy_mode` separate from `release_state`; a reduced cap can coexist
with waiting for a fresh intent. The specification uses:

- `normal`: new risk is permitted and the retained/accepted target is within
  the normal policy; this does not itself issue another version
- `reduced`: a genuine new version is accepted within an already-reduced
  proposal; independent existing capital/risk/order gates still apply
- `blocked`: policy disallows new risk or the supplied synthetic cooldown is
  active; new allowance is zero
- `await_fresh_intent`: risk permission is available but the ratcheted ceiling
  cannot be enlarged using the old version; no elapsed time or NAV change exits
  this state without a valid new edge

The fixture receives cooldown/risk observations as **synthetic assumptions**.
It does not compute or verify breaker triggers, session-count progression,
stale-data detection, available-at evidence or external risk resolution. For
future R3 adoption, triggers and repeated triggers must enter/reset a frozen
session-based block; absent/stale inputs block new risk and cannot decrement a
cooldown or advance alpha history. Cooldown completion only removes its block.
Exact loss measure, trigger threshold, observation cadence, missing-session
recovery and execution constraints remain to be frozen for a real candidate.
The old cooldown helper's permissive absent predecessor is not adopted here.

The consumer returns a newly increasing result only after append succeeds.
An append exception exposes no returned positive result. An identical restart
returns the exact stored transition and `already_appended`; changed frozen
input in that effective session rejects. Receipt replay is not downstream
order or reservation exactly-once delivery. Explicit initialization is required;
missing initialized history must not be repaired with a new genesis. QPK alone
cannot detect erased history if a caller dishonestly resubmits an initialization
receipt, so an independently validated lifecycle remains a real-adoption gap.

The new cases cover separate latches, unknown forced true, independent false
then one true edge, risk/cooldown/NAV/AI non-edges, blocked-edge watermark,
append failure, exact receipt restart, same-session conflict, expired/foreign
scope or revision/config, and missing initialization. Existing tests retain
quantity, split, pending-order, capital and lower-cap specifications unchanged.
No new economic result or expected-session completeness claim follows.

### Inputs and SOXL still require independent contracts

The new inputs are explicitly **toy indicators** and an illustrative short
ordered fixture schedule. Neither a `complete=true` declaration nor a parsed
hash establishes actual causal observations. No actual raw-prefix derivation,
venue calendar/half-day completeness, source availability, rights, historical
PIT, cutoff or interruption reconciliation validator was implemented. The
private guard/cash candidate's 257-bar warmup belongs to its own feature
contract; the pure TQQQ predicate has no universal warmup count. Real issuer
use remains blocked on the existing R1/source-evidence prerequisites.

SOXL is not wired to this TQQQ predicate or fixture. Ordinary SOXL's tiered
SOXX buffer rule uses `price > entry_line` for full and `price > mid_line OR
(current_blend_active AND price > exit_line)` for mid, with physical SOXL
holdings supplying the current active condition. Frozen SOXL SMA-v1 research
uses `close >= SMA`, while its RSI2 trend filter uses `close > SMA` and a
separate held/RSI target rule. Those equality boundaries and logical-state
owners require their own reviewed source/config/observation contract. This
addition leaves SOXL-v1, RSI2, legacy runtime and all existing research numbers
unchanged, and provides no synthetic unified SOXL adapter.
