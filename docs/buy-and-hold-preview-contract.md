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
