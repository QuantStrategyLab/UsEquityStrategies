# NEW TQQQ/SOXL evidence package

`ues-new-evidence-v1` defines two NEW fixed research candidates and a shared
six-symbol evidence contract. This revision supports offline engineering and
read-only documentation preflight only. It contains no collector, real-input
comparison runner, forward scheduler, or financial evaluator. The separately
named `replay_new_observed_research` entry validates declarations and stops;
it never invokes the fixture economic loop. The existing replay continues to
reject non-fixture inputs with `REAL_ENTRY_PIT_REQUIRED`.

## Current status and interface

Run from a checkout without credentials or market inputs:

```bash
PYTHONPATH=src python -m us_equity_strategies.research.new_evidence_package preflight
```

The command prints a sanitized `PREFLIGHT_BLOCKED` JSON summary and exits 2.
Its standard-library-only path does not import strategy dependencies, inspect
environment values, or contact services. `freeze`, `collect`, `compare`,
`verify-retention`, `register-forward`, `observe-session`, and `finalize` all
exit 2 with `DATA_EXECUTION_NOT_AUTHORIZED`. Unknown commands never echo their
arguments. There is no execute switch, caller-provided receipt, or fixture
flag that unlocks these stages.

The manual `new-tqqq-soxl-evidence.yml` workflow has `execute=false` and
`collect=false` defaults, rejects either execution request, and can only
produce a blocked preflight summary. It has no provider, storage or workload
identity credentials and no scheduled trigger. Its final qualification step
fails even after successful summary upload. A retrievable preflight summary
is engineering output, not a retained experiment or successful acquisition.
The seven-day sanitized summary retention does not meet the research retention
policy below. Do not dispatch this workflow as part of engineering acceptance.

`package_definition()` returns independent NEW declaration objects. It expands
public manifest values and the listed ETF-only overrides, records exact
candidate/control difference allowlists, and enumerates all 12 logical paths.
It is **not a fully materialized executable manifest**: helper/function
defaults, archived TQQQ recipe equivalence, current runtime artifacts and a
qualified causal QSP producer have not been established. It reports
`materialization_status=BLOCKED`; the hash of a declaration is not proof those
unknowns are resolved. No dependency pin changes are part of this package.

## Fixed identities and inputs

The only symbols are QQQ, TQQQ, QQQM, BOXX, SOXL and SOXX. A future acquisition
would need daily OHLCV, complete forward/reverse splits, distributions and
exchange-calendar evidence. Twelve Data is the proposed canonical source;
Yahoo is a verifier only, never a fallback or averaged source. Issuer/exchange
corroboration is required wherever complete independent positive and negative
action coverage is unproved. Alpaca is an alternative requiring a separate
source binding, not an automatic substitute. Public accessibility and existing
source code do not prove entitlement, free capacity or retention rights.

Historical common start is 2022-12-28 through a verified cutoff C, currently
null. The old 2026-08-04 cutoff is a minimum reference only. Signal warmup from
2021-01-01 for QQQ/SOXX/SOXL requires derived nested-lookback verification. No
pre-inception proxy or forward-filled day is permitted. Preserve reference
train/evaluate folds:

| Train | Evaluate |
| --- | --- |
| 2022-12-28–2023-06-30 | 2023-07-03–2023-12-29 |
| 2024-01-02–2024-06-28 | 2024-07-01–2024-12-31 |
| 2025-01-02–2025-02-28 | 2025-03-03–2025-07-31 |

Use actual frozen XNYS sessions, one-session purge and one-day embargo, plus
trailing 252 and continuous 756 sessions ending at C. No fold selects parameters.
All historical outcomes remain reference/development absent authentic vintage
and prior-access evidence. SOXL history through 2026-08-25 stays contaminated.

## Candidate/control recipes

`tqqq-current-guard-new-v1` uses the current
`build_tqqq_core_only_p2_benchmark_guard_research_decision` and downstream TQQQ
builder. The declared guard policy is 20 sessions, soft/hard drawdown -5%/-10%,
scalars 0.5/0, and maximum price age three days. Both arms exclude income,
options, AI extensions and volatility-delever retention/TACO. The control uses
`build_tqqq_core_only_p2_v2_research_decision`; only market-regime control,
macro-risk governor and crisis-defense flags are disabled. Native volatility
and portfolio safety settings stay equal. Re-materializing archived V9 values
under current source/dependencies creates a NEW identity, never original V9.
The declaration is blocked until all effective defaults and runtime artifacts
are frozen and verified.

`soxl-current-regime-retention-new-v1` deliberately chooses current public
manifest parameters as NEW values: MA 140; reserve 0.03; SOXL full/mid 0.70/0.65;
SOXX active/defensive 0.20/0.15; buffers 0.08/0.06/0.02; volatility 10/252,
percentile 0.95, minimum periods 126, floor/cap 0.50/0.75. Income/options/AI are
off and managed symbols are SOXL/SOXX/BOXX. Retention is environment-driven,
policy `soxl_step_rebound_0.25_0.50`, maximum 0.50 and context required; regime
is enabled, apply-risk-reduced false, apply-risk-off true. Control disables
regime and retention mode/context/maximum; the static retention ratio is zero
in both arms. Other defaults remain identical. This measures their joint
contribution. The actual SOXL entry builder and `build_rebalance_plan` are used
in fake caller tests. These tests do not qualify a producer or causal lineage.

The exact pinned QSP producer/configuration and input sufficiency are UNKNOWN.
A static plugin state, syntactically valid hash, fixture artifact or a declared
approval boolean cannot replace it. `validate_observed_admission` compares local
caller/strategy/manifest hashes and declared recipe/path identities, checks
provenance/resources/assurance/retention declarations, then unconditionally
stops with `PRODUCER_COMPATIBILITY_UNKNOWN`. Real execution also lacks a later
grant. Missing compatibility must not trigger a pin bump or expanded input set.

## Accounting and qualification contract

There are exactly 12 correlated logical paths: two candidates × treatment/control
× 5/10/15 bps all-in per one-way traded notional. Use two independent hypothetical
USD 100,000 books, zero initial holdings and external flows, zero cash yield,
no borrowing, fractional ETF quantities, self-financing sells before buys and
initial allocation fees. Costs are applied once to each book's actual turnover,
not subtracted again from reported returns. No forced final liquidation is
recommended. Signals follow completed-session provider finality; fills occur at
the next verified completed-session close. Missing/late fills remain pending.
No numerical real-data accounting implementation is connected in this revision.

Raw-price books require exactly-once split/dividend events and receivable/payable
reconciliation; total-return-adjusted prices cannot also receive dividends.
The offline assurance validator rejects every nonempty action set in this
revision because no admitted action replay exists. Empty events require full
coverage declarations, exhausted pagination and independent negative coverage.
Those declarations remain unverified provider truth and never produce PASS.

Offline validation rejects missing/duplicate symbol-session cells, impossible or
nonfinite OHLCV, source-basis conflicts, price disagreement and late availability.
The proposed same-basis tolerance is max(USD 0.01, 0.0001 × canonical price) per
OHLC field. Actual provider-finality, official calendar/early closes, warmup and
complete source coverage are unresolved. QQQ split-price signals do not depend
on QQQ dividends; QQQ total-return results and held-ETF economic books do.
Historical corrected data cannot establish historical PIT. Validator success is
`OFFLINE_CONSISTENCY_ONLY`, financial `NOT_RUN`, execution false.

## Future protocol and retention

No registration or real waiting clock has started. T4-N requires 252 new
contiguous completed XNYS sessions after a new frozen P3 anchor. S2-N requires
252 completed sessions **and** 12 calendar months from first scheduled
observation, whichever is later. Register immutable parameter/protocol/input/P3
identities before the first session opens. Historical/trailing windows contribute
zero forward sessions. Missing sessions, late registration, mid-window identity
changes, pending final fills or unrecorded/early outcome access block completion.
A fixed endpoint and hard collection expiry no more than 400 calendar days after
the frozen first session prohibit silent extension or restart. Actual calendar,
availability, access/custodian controls and elapsed-time receipts remain UNKNOWN.
The fake receipt validator does not authenticate a calendar or observation.

Future retention requires an existing approved private immutable run prefix,
create-if-absent writes, one writer and unique attempts. Keep evidence at least
18 calendar months from registration and 90 days after final evaluation,
whichever is later. Required inventory includes frozen plan/candidates/protocol;
inputs/actions/calendar/assurance; runtime/tests/access and all trial records;
decisions/daily books/paired results; forward registration/receipts/results;
artifact index, independent evaluator readback and terminal state. Verify actual
bytes, sizes, identities, versions and expiry. For an Actions delivery route,
also require exact-run sanitized artifact upload/list/download/readback; zero
artifacts, unreadable objects and a successful process alone cannot qualify it.

`persist_fake_attempt` accepts only a fake attempt ID and writes constant,
nonfinancial mechanism records in an exclusively created directory. A second
process or restarted writer cannot reuse it; incomplete claims remain occupied.
Fresh readback checks current terminal bytes, index and identity, rejecting
missing/tampered/symlink objects. This tests local exclusion/readback only and
is not a production storage adapter or proof of research retention rights.

## Requested ceilings and unresolved preflight

| Limit | Requested ceiling |
| --- | --- |
| Initial HTTP requests including all retries/pages | 2,000 |
| Requests per expected forward session | 128 |
| Overall HTTP requests | 40,000 |
| Transport attempts per object | 3 |
| Downloaded new input | 2 GiB |
| Retained new data | 10 GiB |
| Runner CPU time excluding idle calendar waiting | 24 hours |
| Collection expiry from frozen first session | 400 calendar days |
| Incremental monetary spend | USD 0 |

These are stop limits, not observed capacity. Stricter actual quota controls
first. Provider entitlement/pricing/remaining allowances, source/verifier/
corroboration identity, approved runner/store, retention support, current runtime,
effective defaults, causal QSP producer and calendar/finality/access controls
are all UNKNOWN/BLOCKED. No credentials, vendor/account query or variable-value
read is needed to report that result. No assumed free page or fixture can fill
an unknown. Engineering can finish while acquisition remains blocked.

## Honest conclusions

All original T1–T4 and S1–S4 remain `INSUFFICIENT_EVIDENCE`, admissible fills 0/8.
Original unknown parameters stay null with `ACTUAL_PARAMS_UNKNOWN`. NEW identities
cannot redeem historical receipts. DSR and PBO remain null. No tuning, optimizer,
seed shopping, alternate-member substitution or old SOXL exploration is included.

A future frozen financial rule proposes positive paired Calmar improvement,
no larger MDD magnitude and nonnegative geometric-growth difference at all three
costs, plus paired 97.5% lower growth bound ≥ 0. Use stationary-block bootstrap,
expected block 20, 10,000 resamples and one pre-outcome protocol-derived seed;
resample whole same-session six-path vectors. Undefined/zero-MDD statistics,
uncertain intervals or no exercised treatment mean INSUFFICIENT. Qualified point
failure can mean REJECT. Historical diagnostics cannot authorize adoption.
These criteria are recorded for later freezing; no financial evaluator runs now.

Engineering, input assurance, historical results, real forward time, retention
and financial conclusions remain separate. `ADOPT_RESEARCH_ONLY`, if ever reached,
would grant no runtime activation. Merge, deploy, PAPER/shadow/live, Kelly,
stop-loss changes, HOLD lift, Batch 5 and paid use are outside this package's
engineering execution authority.
