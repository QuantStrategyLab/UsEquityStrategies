# Buy-and-hold preview contract

`nasdaq100_buy_and_hold` and `sp500_buy_and_hold` are offline baseline
profiles. Both use `plan_buy_and_hold` in `us_equity_strategies.buy_and_hold`.
Their catalog status is `preview_only`, platform compatibility is empty, and
neither has a platform runtime adapter or a rollout allowlist entry.

| Profile | Candidate choices | Stable reporting reference |
| --- | --- | --- |
| `nasdaq100_buy_and_hold` | QQQ, QQQM, QNDX | QQQ |
| `sp500_buy_and_hold` | SPY, VOO, SPLG with verified SPYM binding | SPY |

QNDX is a US ETF, distinct from Irish UCITS CNDX. SPLG is the historical
candidate name; a selected SPLG candidate requires an independently verified
SPYM contract and `alias_verified=True`. An unresolved old symbol is blocked.
There is no preferred instrument selected in code, automatic substitution,
tax-driven migration, or pre-inception return history. Reporting references
do not change with risk preference or the selected execution instrument.

## Explicit inputs

All input records are frozen dataclasses. Monetary amounts, prices and
quantities use finite nonnegative `Decimal` values, at most 12 decimal places
and at most 1e18; quantity steps and prices must be positive. Floats, integers,
booleans, NaN and infinity are rejected as money. Time is timezone-aware and
provided by the caller. There are no file, environment, clock or network reads.

- `InstrumentBinding` identifies the candidate, selected symbol, verified ISIN
  or broker contract reference, MIC, USD currency, quantity step, minimum
  notional and unlevered cash-equity ETF product. A ticker alone is insufficient.
- `PlanBinding` binds that complete instrument to the profile, opaque physical
  account reference, mandate revision, strategy release, exclusive owner and
  environment. Only `offline_preview` and `paper` descriptions are accepted;
  neither proves a broker PAPER environment. Live is rejected.
- `Mandate` supplies active authority, start/expiry, cash reserve, principal
  ceiling and canonical preference. There are no default amounts or account
  assignments. Dividend policy is cash; an explicitly approved new allowance
  may fund a later buy, but dividends never enlarge an existing allowance.
- `PortfolioFacts` supplies a reconciled snapshot: adopted holdings, exclusive
  ownership, absence of unrelated holdings/orders, settled cash **already net
  of filled debits and deduplicated outstanding reservations**, independent
  account risk capital, explicit risk approval, final risk spend headroom,
  total principal spent/reserved and the current contribution/state revision.
  Reconciliation includes complete open-order, fill and uncertain-order facts.
- `Allowance` is a confirmed contribution bound to the same mandate/account/
  instrument/environment/release/owner. It includes its contribution reference,
  current durable state revision, validity, final amount, reconciled spend
  including fees and live/uncertain reservations. The caller supplies this
  state; the planner never creates a deposit, schedule, reservation or authority.
- `Quote` supplies the same complete instrument, timestamps, positive marked
  price, bounded limit price at least as high as that mark, and a fee upper
  bound for the increment. The caller must independently qualify that bound.
- `Capabilities` describes verified instrument/account/API qualification.
  Fractional steps additionally require `fractional_verified=True`. No
  broker-name branch or generic catalog capability grants this qualification.

Every facts/capability/allowance binding must equal the mandate binding, and
the quote must match the exact instrument. Snapshot and quote timestamps must
cover the injected time and be no more than five minutes old. A mismatched
contribution revision, incomplete/unknown integrity state, missing risk
approval, unrelated exposure or invalid monetary input returns `BLOCKED`.
Injected verification flags are a contract for external evidence producers;
synthetic fixtures establish no broker or runtime facts.

## Funding and sizing

The versioned policy `buy_and_hold_funding.v1` validates these scales only for
new, explicitly approved funding:

| Canonical preference | Scale |
| --- | --- |
| `CAPITAL_PRESERVATION` | 0.50 |
| `BALANCED_COMPOUNDING` | 0.75 |
| `GROWTH_COMPOUNDING` | 1.00 |

A derived allowance stores `base_amount`, `applied_scale`, `final_amount` and
`policy_version`. The planner validates `final_amount = base_amount * scale`
and uses the final amount directly. A directly approved dollar allowance has
`base_amount=None` and `applied_scale=None`; it is never scaled again.
Holdings, residual allowance and standard reporting benchmarks are never
rescaled. A different mandate or tier needs a newly confirmed contribution.

Remaining allowance is final amount minus reconciled spend and outstanding
reservations. Eligible spend is the minimum of remaining allowance, settled
cash after reserve, current risk headroom and remaining principal ceiling.
Principal spent/reserved must cover the contribution's spend/reservations.
Historical fills and outstanding reservations are not debited from already
net broker cash a second time. Gains, unrelated deposits and cash dividends
do not replenish allowance. Margin and FX are outside this contract.

Quantity is rounded down to the verified quantity step after allowing for the
fee buffer. Whole lots use integer steps. Fractional steps must divide one
whole share exactly. An unaffordable unit, insufficient headroom or a value
below minimum notional returns `DEFER` with no target. No due allowance or an
exhausted/reserved allowance returns `HOLD` with no target. Price changes
alone never create a sell or a purchase allowance.

## Preview output and entrypoint

Every `StrategyDecision` has empty budgets, `no_order=True`,
`live_ready=False`, and the risk flags `no_execute`, `no_order`, `preview_only`.
`BLOCKED`, `HOLD` and `DEFER` have no positions. `BUY_PREVIEW` has exactly one
`PositionTarget`: the selected symbol, value mode, marked current holdings
plus the permitted new notional. Diagnostics include decimal strings for the
incremental quantity, notional and maximum spend including fees. These are
disposable previews, never durable orders. Input account/owner/mandate/
contract references and exception text are omitted from output and logs.

The two manifests are also available from `manifests.buy_and_hold`. The older
root manifest registry remains unchanged because research protocols bind its
exact source bytes; use the catalog entrypoint's `manifest` for these profiles.

The existing catalog loader resolves both preview entrypoints. They reuse the
canonical `portfolio_snapshot` required input and existing context fields:

```python
from quant_platform_kit.common.strategy_contracts import StrategyContext
from us_equity_strategies import get_strategy_entrypoint

# All variables below are explicit, independently qualified input records.
decision = get_strategy_entrypoint(mandate.binding.profile).evaluate(
    StrategyContext(
        as_of=now,
        portfolio=facts,
        market_data={"quote": quote},
        state={"mandate": mandate, "allowance": allowance},
        capabilities={"buy_and_hold": capabilities},
    )
)
```

Repeat evaluation does not consume allowance or grant execution ownership.
Future platform integration must re-read state under a durable claim, reserve
atomically, bind idempotency and ownership generation, reconcile uncertain
submissions, and enforce buy-only/selected-contract/cash ceilings after all
scalers and mappers. Never replay an absolute preview target after a restart
or price change. Unknown submission must retain reservations.

This contract includes no broker adapter qualification, reporting data store,
tax calculation, runtime risk-policy override, deployment, order transport,
Kelly, stop-loss or live activation. Every platform remains unsupported for
execution until its separately reviewed integration supplies those boundaries.

## Plain-DCA period composition

`us_equity_strategies.plain_dca.evaluate_dca_period` adds a pure schedule and
amount layer over `plan_buy_and_hold`. It does not change the P1 API, catalog,
entrypoints or the legacy `nasdaq_sp500_smart_dca` strategy. It selects one
family and the mandate's exact instrument, with empty decision budgets and
`no_order`, `no_execute`, `preview_only`, `live_ready=false` on every result.
No recurring execution or rollout registration is provided.

The frozen `DcaPlan` contains the existing `Mandate`, a stable plan reference,
revision, `ScheduleSpec`, approved final tranche B (including fees), carry cap
and gross period ceiling. Monetary inputs use `Decimal`; B has USD cent
precision. B is already final: an optional reference base and applied tier are
validated once, with the derived B rounded down to cents. The combined
tranche/carry allowance is passed to P1 as directly approved final dollars,
without applying a tier again.

`ScheduleSpec` supports monthly days 1–31 and weekly weekdays 0–6 (Monday is
0). Defaults are monthly day 5, America/New_York, opening plus 30 minutes to
closing minus 30 minutes. `catch_up` must be false. Missing month-end dates
clamp to the last calendar day before rolling forward to the next supplied
exchange session. Monthly keys use YYYY-MM; weekly keys use ISO week-year and
week of the **unrolled** anchor. Rolling December into January keeps the
December key. Half-day closes and DST come from explicit session timestamps.
A nominal anchor before the mandate's local start date is not eligible.

`ExchangeCalendar` declares inclusive complete coverage from at least the
mandate's local start through the evaluation date, an explicit version, MIC,
timezone and observation/expiry timestamps, plus an already loaded exchange
`tzinfo` object whose `key` matches that timezone. Evaluation never constructs a
timezone or reads the ambient timezone database. Absent dates within that declared
range are closures. Sessions must be an ordered tuple with unique dates,
aware open/close timestamps on that exchange-local day, and open before close.
The due anchor must have a covered next session. Missing, expired,
contradictory or incomplete inputs block; the function never invents a weekday
calendar. Completeness is an injected attestation, not independently verified
exchange evidence. Its validity window is caller-provided; no calendar download
or clock lookup occurs.

`PeriodFacts` binds the plan, revision and nominal key to an immutable fresh
snapshot (at most five minutes old), prior period records, verified unreserved
funded carry, and remaining annual/lifetime new-grant and gross-spend ceilings.
P1 independently validates fresh cash, quote, risk and capability facts. Any
existing record for the selected key blocks a new grant, including a terminal
skip or deferral. Outstanding prepared/submitting/acknowledged/partial/unknown
records block new cycles. Unknown record states, duplicates and missing money
are rejected. Revisions do not change the period key.

Only the latest anchored period is considered. Before its window the status is
`NOT_DUE`; after its window it is `SKIPPED/expired`. Older unrecorded periods appear as
expired or same-session collision keys and never add tranches. Holiday rollover
is on time. A preview returns these classifications without persisting them.

The new tranche is all-or-nothing. Insufficient annual/lifetime grant or spend,
risk, principal or gross period headroom for B skips with `budget_exhausted`.
Known settled cash, net of reservations and the mandate reserve, must back both
B and existing carry; otherwise the preview is `SKIPPED/shortfall` with zero new
grant and unchanged carry. Unknown or stale cash blocks. Carry cannot exceed
the approved cap, and that cap cannot exceed B. The spend envelope is
`min(B + carry, gross period cap, annual/lifetime spend headroom, risk headroom,
principal headroom)`. P1 rounds down to verified whole/fractional quantity steps
with its fee buffer and minimum notional. A zero quantity returns `DEFERRED`.
After a due/deferred preview, the unspent B plus carry is split into bounded
`carry_out` and `released_authority`; fees are included in `maximum_spend`.
These are proposed movements, not durable grants, fills or reconciled balances.

`PeriodPreview` includes status, static reason, nominal key/anchor, due session,
expired/collision keys, tranche, carry in/out, envelope, maximum spend, released
authority and the original P1 decision. It emits no account, plan, contribution,
release or calendar identity. No logging, file/network/broker/clock I/O, store,
scheduler, claim or order submission exists. Repeated calls are disposable
previews, not idempotent execution. A future authorized consumer must persist
terminal results, reread current facts, and atomically claim/fence the period at
its serialized order writer. This library does not establish restart safety,
PAPER qualification, installed adoption or live readiness.
