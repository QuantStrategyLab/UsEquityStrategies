# Historical runtime bundle contract

The offline bundle checker validates a sanitized export, reproduces its observed
order and cash ledger, and compares supported decision calculations with supplied
golden outputs. It reads files only. It does not connect to a broker, alter a
runtime binding, or authorize execution.

The contract identifier is `qsl.historical_runtime_bundle.v1`. The current
accounting scope is a USD, long-only account containing both
`tqqq_growth_income` and `soxl_soxx_trend_income`. An unsupported historical
release, missing source record, unresolved order, or unsupported accounting event
remains a reported gap. Extending the supported scope requires a reviewed adapter
and regression coverage; it must not be represented by a successful input flag.

## Evidence and result boundaries

The checker distinguishes these results:

| Result | Meaning |
| --- | --- |
| `PASS` for integrity or coverage | Supplied bytes and declared record relationships satisfy this contract. |
| `PASS` for accounting | Supplied opening balances, events, and closing balances reconcile exactly within the supported arithmetic model. |
| `PASS` for shared-account bookkeeping | Both profiles share one account alias, one ordered journal, cash reservations, and reconciled ownership. |
| `calculation_status=PASS` and `golden_comparison=PASS` | The installed, supported decision adapter computed a trace identical to the supplied golden trace. |
| `BLOCKED` | A required observation, supported adapter, independent attestation, or unresolved-outcome reconciliation is unavailable. |
| `FAIL` | Supplied evidence contradicts its digest, identity, chronology, conservation rules, or another observed record. |
| `NOT_EXECUTED` | A preceding gate prevented this check from running. |

`conformance_status` covers integrity, coverage, order lifecycle, accounting and
shared-account bookkeeping. It is not historical acceptance. Supplied hashes and
timestamps cannot establish source authenticity or that an input was available
to a historical consumer. The current checker always reports
`historical_parity_established=false`, `execution_authorized=false`, and
`production_ready=false`.

Run-to-intent identifiers and supplied ledger events are checked, but exact
post-risk order sizing and approval facts are not reproduced by this initial
adapter. The machine-readable finding
`DECISION_TO_ORDER_SIZING_AND_RISK_LINK_UNVERIFIED` remains blocked. A matching
decision calculation and reconciled supplied fills do not jointly establish
complete decision-to-broker parity.

The existing production decision replay adapter also keeps its own `BLOCKED`
result. A matching computed trace does not override that result. The checker
does not supply a verifier that blindly returns `True`, import code named by a
bundle, or execute a release downloaded from a supplied URL.

## Files and freezing

Use one directory containing exactly these files. JSONL files contain one object
per line; JSON files contain arrays, except `manifest.json`.

| File | Manifest role | Contents |
| --- | --- | --- |
| `manifest.json` | — | Bundle identity and file inventory. |
| `SHA256SUMS` | — | SHA-256 of the manifest and every data file. |
| `release-manifests.json` | `releases` | Code, configuration and deployment identities effective during the window. |
| `sessions.json` | `sessions` | Every declared session and both profiles' run indexes. |
| `runs.jsonl` | `runs` | Frozen decision inputs and observed golden outputs. |
| `order-events.jsonl` | `order_events` | Intent reservations, submissions, broker outcomes and fills. |
| `account-events.jsonl` | `account_events` | External flows, settlement, supported corporate actions, fees and restarts. |
| `account-snapshots.jsonl` | `account_snapshots` | Observed daily opening/closing balances, holdings and reservations. |
| `source-evidence.json` | `source_evidence` | Sanitized source payloads and declared observation/availability clocks. |

`manifest.json` requires `schema_version`, `evidence_kind` (`historical` or
`synthetic_fixture`), `bundle_id`, `created_at`, `account_alias`, `currency`,
`profiles`, `first_session`, `last_session`, and `files`. The account alias has
the form `acct_` followed by 16 to 64 lowercase hexadecimal characters. Each file
entry contains `role`, `path`, `sha256`, `size_bytes`, and `rows`. `profiles`
contains both profile identifiers exactly once. The first and last sessions are
inclusive ISO dates. `created_at` is the export clock, not an input-availability
claim.

The freezer derives file metadata from actual bytes and writes `SHA256SUMS` last.
It refuses an existing checksum file and rejects symlink paths, including a
symlink manifest or dangling checksum link, and a hard-linked manifest. The checker rejects extra files,
path substitutions, digest changes, duplicate JSON keys, nonfinite JSON numbers,
and mismatched row counts. Store notes and validation outputs outside this
directory. Keep sanitized raw exports, normalization mappings and extra source
documents in a sibling `supporting-materials` directory, not inside the nine-file
validated directory. Retain the final manifest digest separately from the package.

To correct a frozen export, create a new directory and bundle identifier, retain
the original export, and freeze the corrected version. Do not silently edit a
frozen input or substitute freshly downloaded market data into its identity.

## Common field conventions

- Preserve timezone-aware ISO-8601 clocks, including their actual precision.
  The checker normalizes clocks to UTC for comparisons. A session date is not an
  intraday availability timestamp.
- Ledger cash, quantities, prices, fees, ratios and allocations use finite decimal
  **strings**, such as `"12.3456"`. Booleans, floats, exponents and grouping
  separators are rejected. Each value is limited to 80 characters. Arithmetic
  uses precision 256 and traps inexact operations; a larger calculation is
  blocked rather than rounded. There is no tolerance that can absorb a mismatch.
- Other frozen strategy inputs retain their original JSON numeric types and
  exact serialization semantics required by their existing adapter. Do not
  convert the strategy input object to the ledger's decimal-string format.
- Identifiers contain 1 to 128 characters from `A-Z`, `a-z`, digits, `_`, `.`,
  `:`, and `-`. Preserve a stable one-to-one pseudonym for each run, intent,
  attempt, broker order, execution, snapshot and account across every file.
- SHA-256 fields use 64 lowercase hexadecimal characters. Source revisions use
  40 lowercase hexadecimal characters. Do not substitute a branch name.
- Every data row except a source-evidence row requires `source_evidence_id`.
  A reference must identify a retained source payload. These references provide
  a review path; they do not independently authenticate a broker or publication.
- An observed empty array means the observer recorded no entries. A missing file,
  omitted field or `null` observation is not an empty array. Preserve unknown,
  timeout, stale and read-failure states rather than inventing a terminal result.

Canonical payload hashes use JSON with sorted keys, compact separators, ASCII
escaping and nonfinite numbers disabled. File hashes cover actual bytes, including
line endings. These are different hashes and must not be interchanged.

## Release and session records

Each release record requires:

`release_id`, `effective_from`, `effective_until`, `source_revision`,
`source_hashes`, `dependency_lock_sha256`, `image_sha256`, `config`,
`config_sha256`, `effective_config_sha256`, and `source_evidence_id`.

The effective interval includes `effective_from` and excludes `effective_until`.
Use separate release records when effective configuration differs by profile or
changes within the window. Retain resolved flags, plugin permissions, risk
bindings and state capabilities inside the corresponding frozen configuration
or input. A manifest describing today's repository defaults does not identify
the historically deployed release. The current-source adapter compares its
explicit source-file inventory; this is not a complete dependency closure or
image-attestation check.

Each session record requires `session`, `open_at`, `close_at`, `calendar_id`,
`runs_by_profile`, `no_run_reasons`, and `source_evidence_id`.
`runs_by_profile` maps each profile to the ordered list of its actual `run_id`
values. Multiple runs per day are allowed and every retained run must occur
exactly once in the session index. An observed no-run session uses `[]` and a
specific entry in `no_run_reasons`; an unobserved session uses `null` and remains
blocked. Declare all sessions in one continuous requested window, including
sessions on which neither strategy ran. The checker validates the supplied
index; independent calendar completeness remains a separate acceptance item.

## Decision records

Each run requires `run_id`, `profile`, `account_alias`, `session`, `decision_at`,
`release_id`, `input`, `input_sha256`, `output`, `output_sha256`, `availability`,
`order_observation`, `intent_ids`, and `source_evidence_id`.

`input` is the complete frozen request used by the supported decision adapter:
benchmark history with per-row availability, derived indicators and their
availability, portfolio/cash/positions, persistent strategy state, instrument
prices and clocks, capabilities, plugin observations, and effective bindings.
The existing adapter's required fields remain in force. `output` is the full
canonical trace expected from that adapter, mapped from retained golden runtime
records. Its contents include decision targets, risk flags, the offline risk
decision and identities. If the raw historical report has a different schema,
preserve it in source evidence and supply a reviewed normalization; do not fill
in absent values from a current run.

Every `availability` entry requires `name`, `sha256`, `available_at`, and
`source_evidence_id`. An entry named `frozen_input` must bind to `input_sha256`.
No listed availability clock may exceed `decision_at`. Preserve per-row market
and plugin clocks inside `input` as well; one aggregate hash does not prove that
all component observations were timely.

`order_observation="observed"` with `intent_ids=[]` records an observed no-order
run. Missing or non-observed order evidence blocks coverage. Every listed intent
must have exactly one reservation event in the global account journal.

## Global journal and order lifecycle

Both event files share one consecutive `sequence`, starting at 1. Do not number
each strategy independently. Every event requires `event_id`, `sequence`,
`account_alias`, `kind`, `event_at`, `observed_at`, and `source_evidence_id`.
Observation clocks cannot move backwards in journal order. Preserve the actual
event clock when a duplicate or delayed broker report is observed later.
Submissions cannot precede the reservation, broker events cannot precede their
submission, and retry/release clocks cannot precede the terminal outcome they use.
A cancellation cannot predate a fill already included in that attempt. The full
fill terminal clock includes the latest execution clock even when observations
arrive out of execution-time order.

Order events additionally require `intent_id` and the following fields:

| Kind | Additional fields and constraints |
| --- | --- |
| `reserve` | `run_id`, `profile`, `observation_id`, `side`, `symbol`, `quantity`, `reserved_cash`. The snapshot must precede the event sequence and remain valid at the event clock. A buy reserves positive cash; a sell reserves zero cash and strategy-owned shares. |
| `submit` | `attempt_id`, `quantity`. A new attempt may submit only the intent's unfilled remainder after earlier attempts reached terminal outcomes. |
| `acknowledged` | `attempt_id`, `broker_order_id`. The broker identity cannot be reused across attempts. An acknowledgment records an open order, not a fill. |
| `unknown` | `attempt_id`. Retains the reservation. While any account attempt is unknown, new reservations and submissions for either profile fail the conservative journal contract. |
| `rejected` / `cancelled` | `attempt_id`, `reason` equal to `broker_rejected` or `broker_cancelled`. A timeout or HTTP error is not terminal broker evidence. |
| `fill` | `attempt_id`, `broker_order_id`, `execution_id`, `quantity`, `price`, `fee`, `cumulative_quantity`, `remaining_quantity`. Cumulative fills must increase by this execution's quantity and sum with the remaining quantity to the attempt quantity. |
| `release` | All attempts must be terminal. An unresolved reservation cannot expire or be released merely because the next session started. |

Repeated identical execution identities are idempotent, including observations
arriving after reservation release. A conflicting reuse fails. A partial fill
after an unknown outcome keeps the remaining order unresolved; a bound complete
fill or explicit broker outcome can resolve it. Retry identity and quantities
remain linked to the original intent.

The journal uses one settled-cash pool. Reservations from both profiles are
summed before the next intent is admitted. Buys reduce cash, settled cash and the
intent reservation by exact notional plus fee. Sales increase cash by proceeds
less fees; a separate settlement event makes those proceeds available as settled
cash. This is an evidence-accounting model and does not replace a broker's
historical buying-power or settlement-policy adapter.

## Account events and snapshots

Supported account-event fields are:

| Kind | Fields |
| --- | --- |
| `external_cashflow` | Signed `amount`, `settled=true`. Unsettled external flows require a future adapter. |
| `settlement` | Positive `amount`, bounded by observed total cash. Retain the source settlement record. |
| `split` | `symbol`, positive `ratio`. An active order on the affected symbol blocks this adapter. |
| `dividend_ex` | `symbol`, nonnegative `per_share`, unique `entitlement_id`. Computes the entitlement from owned shares. |
| `dividend_pay` | `entitlement_id`, `amount`, equal to the retained entitlement. The payment clock cannot precede entitlement creation, and a paid identity cannot be created again. |
| `fee` | Nonnegative `amount` for a separately recorded account fee. Do not record an execution fee a second time. |
| `restart` | `reservation_sha256` of the current sorted intent-to-decimal-string reservation map. No reservation is cleared by restart. |

Each account snapshot requires `snapshot_id`, `account_alias`, `session`,
`boundary` (`opening` or `closing` for the required daily observations), `at`,
`valid_until`, `sequence`, `cash`, `settled_cash`, `positions`, `allocations`,
`reserved_cash`, `receivables`, and `source_evidence_id`.

`sequence` identifies the last included event; the opening baseline is sequence
0. `positions` maps symbol to quantity. `allocations` maps each profile to its
owned symbol quantities, whose sum must match account positions.
`reserved_cash` maps intent to the remaining reservation. `receivables` maps
dividend entitlement identity to its unpaid amount. Additional intraday
snapshots may be provided with a distinct boundary label.

Every session needs exactly one opening and closing observation. Opening clocks
must not follow the declared market open; closing clocks must not precede the
declared close. A snapshot cannot omit an already observed event or precede an
event it includes. The closing snapshot must cover the complete exported
journal. Balances and ownership are compared after replaying each included event.

An opening reservation requires the earlier intent and lifecycle history. Extend
the window to a supported opening baseline instead of deleting that reservation.
The initial version does not reconstruct transfers, fractional-share cash in
lieu, withholding, FX, option assignments, average-cost changes, corporate-action
adjustment of open orders, or account-level risk decisions. Unsupported events
remain blocked and their source records must be retained.

## Source evidence and shared-account acceptance

Each source record requires `evidence_id`, `kind`, `available_at`, `observed_at`,
`payload`, and `payload_sha256`. Preserve actual availability and observation
clocks. Export time is not publication time. This checker verifies payload hashes
and reference existence. It does not verify digital signatures, access logs,
broker authenticity, normalization fidelity, risk approval, or source retention
controls.

Shared-account acceptance additionally requires independently supported account
and account-domain binding, effective enrollment for both profiles, complete
broker observations, risk-policy versions and exact-order approved risk facts,
account equity/peak/exposure, source dependency closure, and consumer publication
evidence. The checker records these as unresolved provenance requirements.

`scenario_coverage` reports whether supplied events exercise concurrent profile
buy reservations, partial fills, duplicate executions, resolved unknowns, retries
after terminal outcomes, restart with retained reservations, and settlement.
`shared_account_scenario_status` stays blocked when any case is absent.
`shared_account_acceptance` remains blocked even if all mechanical scenarios are
present. No-run/no-order fixtures can pass bookkeeping while proving none of
these scenarios. Synthetic supplemental scenarios test mechanisms and do not
become observations of the user's account.

## Commands

Run from a checkout of this repository with its supported Python environment:

```bash
PYTHONPATH=src python -m us_equity_strategies.research.historical_runtime_bundle template /tmp/runtime-example
PYTHONPATH=src python -m us_equity_strategies.research.historical_runtime_bundle check /tmp/runtime-example --output /tmp/runtime-example-result.json
PYTHONPATH=src python -m us_equity_strategies.research.historical_runtime_bundle init /path/to/new-staging-export
PYTHONPATH=src python -m us_equity_strategies.research.historical_runtime_bundle freeze /path/to/new-staging-export
PYTHONPATH=src python -m us_equity_strategies.research.historical_runtime_bundle check /path/to/frozen-export --output /path/to/replay-result.json
```

The example is explicitly synthetic and uses a placeholder source identity; its
bookkeeping can pass while its historical decision replay stays blocked. Use it
as a field-format example, not as evidence to complete an export.

`init` creates an unfrozen historical intake skeleton with null manifest identity
fields, empty data files and no `SHA256SUMS`. Fill the skeleton from actual
retained observations before freezing it. The empty files in this incomplete
skeleton are placeholders, not declarations of observed no-order activity.
An unfilled skeleton remains blocked, even if its current bytes are frozen.

`freeze` expects the manifest metadata and all seven data files in a new staging
directory. `check` is read-only and returns exit code 0 for overall `PASS`, 1 for
`FAIL`, and 2 for `BLOCKED`. The current authentication boundaries mean a normal
complete supplied bundle still returns 2; automation must not reinterpret it as
success. `--skip-decision-replay` is available for a structural/accounting check
and explicitly records that the decision replay was not requested. Missing
optional current-source dependencies are reported as an adapter blocker rather
than a successful or skipped replay.
An output path must be new; the CLI never overwrites an existing report, symlink,
or hard link. Choose a separate new report path for each validation attempt.
