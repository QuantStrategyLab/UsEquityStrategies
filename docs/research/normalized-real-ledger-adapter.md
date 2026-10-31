# Normalized real-ledger research adapter

`us_equity_strategies.research.real_trial_ledger_adapter` validates caller-supplied
research records before delegating conditional statistics to
`trial_overfitting_report` and QPK. It accepts a normalized input contract;
broker exports and native research-store records need an explicit mapping first.
It performs no network, account, store or file operations.

The existing synthetic-only APIs retain their input restrictions. This adapter
does not convert a synthetic record into real evidence or certify the source
of a record that describes itself as real.

## Input contract

Call `report_normalized_real_ledgers` with these keyword arguments:

| Argument | Contract |
| --- | --- |
| `attempts` | Nonempty list of attempt records, including observed unsuccessful attempts. Each trial ID is unique within the supplied study. |
| `sources` | Dictionary from lowercase SHA-256 to the exact supplied bytes. Every referenced digest is checked. |
| `sessions` | Nonempty, strictly increasing list of ISO session dates. Every successful ledger must contain exactly this sequence. |
| `selected_trial_id` | A successful trial included in the explicitly declared candidate list. Selection is supplied, not performed by the adapter. |
| `selection_candidate_ids` | Unique successful trial IDs in the comparison. The subset does not establish complete family coverage. |
| `annual_risk_free_rate` | Finite annual simple decimal rate. Excess returns subtract this rate divided by 252 for each observation. |
| `effective_trials` | Optional dictionary with integer `count`, nonempty `method`, and a supplied `source_sha256`. The count must be between one and the number of comparison candidates. It is a documented assumption, not an estimate or a verified family count. |
| `pbo_splits` | Integer of at least two, default 16. QPK determines statistical feasibility and returns its status and reason. |

Money and returns are decimal strings. Numeric values must be finite, have at
most 24 coefficient digits and an adjusted exponent with magnitude at most 15.
Unsupported currency, cash-flow or valuation cases fail rather than being
converted or interpolated.

Each attempt declares `domain`, `strategy_profile`, `input_id`, `trial_id`,
`run_id`, `param_version`, `status`, `source_sha256` and
`raw_attempt_source_sha256`. Status is one of `succeeded`, `failed`, `rejected`,
`aborted` or `started`. Successful attempts require a nonempty run ID and a
positive integer parameter version. Unsuccessful attempts may retain an
optional run ID and parameter version, but cannot carry result ledgers. All
supplied run IDs are unique. Do not discard unsuccessful attempts to improve a
reported score.

Native trial IDs may be namespaced by a store or profile. The mapper must
resolve those identities explicitly and preserve the original fields. Reusing
one normalized ID for unrelated native trials or counting duplicate exports as
additional trials is invalid. This module does not infer that mapping.

## Source and normalized receipts

Canonical JSON uses UTF-8, sorted keys, compact separators and no NaN values.
The attempt's `source_sha256` identifies canonical bytes containing exactly
its domain, strategy profile, input ID, trial ID, run ID, parameter version,
status and raw-attempt digest. An omitted optional run or parameter value is
represented as JSON null in this receipt.

A successful attempt also supplies `ledger_source_sha256`, the normalized
`ledger`, and `ledger_sha256`. The last digest binds the entire canonical
normalized ledger. Its six identity fields must match the attempt.

Each fill's `fee_source_sha256` identifies a canonical receipt containing its
trial ID, run ID, fill ID, timestamp, session date, currency, fee, fee kind and
`raw_fee_source_sha256`. The raw attempt, ledger and fee digests identify
supplied original bytes. A matching digest proves byte integrity and the
normalized receipts bind declarations to those bytes. It does not prove who
produced them, whether their timestamps are trustworthy, or whether the mapper
interpreted the original contents correctly.

## Ledger and cost reconciliation

Supported ledgers declare USD, calendar ID `XNYS`, 252 periods per year,
`SIMPLE_NET` returns, `NET_NAV` cost basis and a nonempty `cost_definition`.
They provide a positive `initial_nav`, an `initial_date` before the first
return session, dated `days`, and `fills`.

Every day contains `date`, `nav`, `net_return`, `external_flow`, `fees`,
`gross_pnl` and `other_costs`. Nonzero external flows are unsupported. NAV must
remain positive. Fill fees and other costs must be nonnegative. The accounting
identity is checked with bounded high-precision Decimal arithmetic:

```text
net NAV = previous NAV + gross P&L - realized fill fees - other costs
net return = net NAV / previous NAV - 1
```

The NAV identity must match exactly. A declared return may differ from the NAV
ratio by at most `1e-12` in absolute return units to allow decimal rounding.
Fees are already included in NAV and are not subtracted a second time when
constructing returns.

Fill IDs must be unique within a ledger. Each fill needs a timezone-aware
timestamp whose date in `America/New_York` equals its supplied session date,
USD currency, an explicit fee and `fee_kind=REALIZED`. Fees are summed per
session and must equal the day's fee total. A no-fill day has zero fill fees;
a zero-fee fill still requires an explicit, source-bound zero. A flat-basis-point
model is not a realized fee. Missing or malformed amounts are rejected.

Candidate ledgers share domain, input ID, currency, calendar, annualization,
return unit, cost basis, cost definition, initial marked date and return
sessions. Profiles and parameters may differ within this declared study.
The supplied calendar is checked for alignment, not authenticated exchange
completeness. The adapter does not silently intersect dates or replace gaps.

## Report and statistical limits

The report retains attempt statuses, per-attempt provenance and a canonical
study specification with its SHA-256. Returned candidate and effective-count
values are copied so that later mutations of caller inputs cannot alter the
reported specification.

Primary `metrics.dsr` and `metrics.pbo` are always null and `UNCOMPUTABLE`.
Complete cumulative trial-family coverage and a qualified effective-trial
count are not established by this input contract.

`conditional_candidate_set_pbo` may contain QPK's probability of backtest
overfitting for the supplied successful subset. It uses combinatorially
symmetric cross-validation with the declared split count and per-session
Sharpe metric. `conditional_candidate_set_dsr` may contain QPK's deflated
Sharpe ratio only when an explicit effective-count assumption is supplied and
the required Sharpes are defined. An unavailable dependency or uncomputable
statistic retains a null value and reason. The adapter does not implement a
replacement statistic or infer missing trial returns.

The report distinguishes source authentication (`NOT_VERIFIED`), normalization
(`CALLER_ATTESTED`), calendar authenticity (`NOT_VERIFIED`) and reconciled
arithmetic (`NORMALIZED_ARITHMETIC_RECONCILED`). Gross P&L and other costs are
normalized declarations; the accounting equation alone does not certify their
original-source attribution or prove that every economic cost was included.
The output remains research-only, not eligible for promotion, not live-ready,
size-zero-required and no-order.

## Verification and use

Unit cases in `tests/research/test_real_trial_ledger_adapter.py` exercise
identity, receipt, calendar, timestamp, fee, NAV, return and report-integrity
failures before statistics. Synthetic fixtures describe invented records;
their `REALIZED` field exercises the contract without claiming broker evidence.
The integration case in `tests/research/test_trial_overfitting_report.py`
compares conditional outputs with direct QPK calls. The existing isolated
research-statistics CI job supplies that capability; package pins and runtime
entrypoints are unchanged.

Before using native evidence, document and review its raw-to-normalized
mapping, data availability, fees and selection history. Retain failed attempts
and unresolved provenance. Statistical interpretation still requires the
complete research process, including discarded trials and cost assumptions.
See the [existing trial-set contract](trial-overfitting-report.zh-CN.md) and
[trial registry](trial_registry/README.md) for their separate scope.
