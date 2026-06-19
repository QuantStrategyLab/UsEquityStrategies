# Nasdaq 100 / S&P 500 Smart DCA Design

## Purpose

`nasdaq_sp500_smart_dca` is a buy-only accumulation profile for cash-funded
accounts on platforms without native recurring investment support. It defaults
to fixed DCA and can optionally enable smart sizing. It does not rebalance by
selling existing ETF positions. Each execution window decides how much new cash
to deploy into Nasdaq 100 and S&P 500 ETF sleeves.

## Research Basis

The optional smart sizing mode combines three common ideas:

- Long-term trend filter: 200-day / 10-month moving-average rules are widely
  used as simple tactical allocation gates. Faber's tactical allocation paper
  and later asset-allocation studies document the use of long moving averages
  to reduce portfolio risk versus always-on exposure.
- Valuation-aware sizing: the S&P 500 Shiller CAPE / PE10 is a mainstream
  long-horizon valuation reference, but it is not a daily broker-runtime input.
  This profile therefore uses a runtime-native valuation proxy: price distance
  from the 200-day average plus 252-day drawdown.
- Value averaging / smart DCA: when the market is materially below trend or in
  drawdown, buy more than the base amount; when it is stretched far above trend
  and overbought, buy less or skip.

References:

- Meb Faber, "A Quantitative Approach to Tactical Asset Allocation":
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461
- Bryan Foltice and Steven Dolvin, "Using Simple Technical Analysis Indicators
  for Asset Allocation Decisions": https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3732822
- Shiller PE / CAPE reference: https://www.multpl.com/shiller-pe
- Value averaging overview: https://www.investopedia.com/terms/v/value_averaging.asp

## Default Instruments

Signals:

- `QQQ`: Nasdaq 100 proxy
- `SPY`: S&P 500 proxy

Trade targets:

- `QQQM`: 50%
- `SPLG`: 50%

The strategy uses QQQ/SPY for longer signal history and QQQM/SPLG as lower-cost
accumulation ETFs. Deployments can override `trade_allocations` if a broker or
account prefers QQQ/SPY directly.

## Cadence

Default cadence is monthly:

- `monthly_day = 25`
- `monthly_window_calendar_days = 5`
- `weekly_day = 4`
- `weekly_window_calendar_days = 4`
- `quarterly_months = (1, 4, 7, 10)`
- `quarterly_day = 25`
- `quarterly_window_calendar_days = 5`
- `base_investment_usd = 1000`
- `max_investment_usd = null`
- `min_investment_usd = 5`
- `cash_reserve_usd = 0`
- `investment_amount_mode = "fixed"`
- `smart_multiplier_enabled = false`

The intended runtime schedule is daily near the US close during the 25th to
29th calendar-day window. If cash is not available, the strategy emits
`no_execute`; a later run in the same window can still buy if cash arrives.

The default cadence remains monthly. Deployments can switch the same fixed
amount semantics to weekly or quarterly accumulation:

- `cadence = "weekly"`
- `weekly_day = 4`
- a lower `base_investment_usd`

or:

- `cadence = "quarterly"`
- `quarterly_months = (1, 4, 7, 10)`
- `quarterly_day = 25`
- a higher `base_investment_usd`

The `*_window_calendar_days` settings define how many calendar days remain
eligible after the scheduled day. They are intended for platform retries after
non-cash execution failures. Successful-order suppression and broker failure
classification belong to the platform runtime.

This platform DCA profile assumes fractional-share / dollar-order execution.
`min_investment_usd` is only a small-order guardrail, not an integer-share
constraint.

Funding semantics are simple: the strategy does not maintain a separate cash
pool for future dip buys. By default, each scheduled run requests
`base_investment_usd`. If smart sizing is enabled, the requested amount is
`base_investment_usd * multiplier`, optionally capped by `max_investment_usd`.
If available cash is below the requested amount, the strategy emits
`no_execute` rather than placing a partial DCA order; a later run in the same
window can still buy if cash arrives. Dedicated DCA accounts default to no cash
reserve and no single-run cap. Deployments that want a small cash buffer or
maximum order amount can set `cash_reserve_usd` or `max_investment_usd`.

The default is ordinary DCA without valuation multipliers. In that mode, the run
does not need the 252-day signal indicators before it can place the scheduled
buy. To enable smart sizing, set `smart_multiplier_enabled = true`.

## Sizing Rules

These rules apply only when `smart_multiplier_enabled = true`.

For each signal ETF, the strategy computes:

- latest close
- 50-day SMA
- 200-day SMA
- distance from 200-day SMA
- 252-day high and drawdown
- 14-day RSI

It averages QQQ and SPY drawdown / SMA gap and maps the state to a multiplier:

| Regime | Default trigger | Multiplier |
| --- | --- | ---: |
| `severe_pullback` | average 252-day drawdown >= 25% | 1.50x |
| `deep_pullback` | average drawdown >= 15% or average gap <= -10% | 1.25x |
| `mild_pullback` | average drawdown >= 8% or average gap <= -5% | 1.10x |
| `normal` | no discount or overvaluation trigger | 1.00x |
| `expensive` | average gap >= 12% and drawdown <= 3% | 1.00x |
| `very_expensive_overbought` | average gap >= 20%, drawdown <= 3%, both RSI >= 70 | 1.00x |

The actual buy is capped by available cash after any configured
`cash_reserve_usd`. If the result is below `min_investment_usd`, the strategy
waits/skips instead of creating a tiny order.

## Smart Sizing Sweep

Run date: 2026-06-19. Data: FRED `NASDAQ100` and `SP500` daily closes, using a
50/50 price-only proxy from 2017-06-20 through 2026-06-17. Assumptions:
monthly contribution $1,000, fractional / dollar-order execution, shared 252-day
warm-up, fixed DCA as the benchmark. The proxy is price-only and does not
include ETF expense ratios, spreads, taxes, dividends, or total-return
reinvestment.

| Smart variant | Terminal | Vs fixed | Max DD | DD delta |
| --- | ---: | ---: | ---: | ---: |
| Fixed DCA benchmark | $249,508 | 0.00% | 28.25% | 0.00% |
| Conservative dip-only, 1.10/1.25/1.50x | $247,501 | -0.80% | 27.97% | -0.28% |
| Conservative with trimming | $247,433 | -0.83% | 27.79% | -0.46% |
| Previous default smart sizing | $246,273 | -1.30% | 28.21% | -0.04% |

The best smart variant in this sweep was conservative dip-only sizing: it does
not reduce buys in expensive regimes and only raises the scheduled buy modestly
in pullbacks. It still did not beat fixed DCA terminal value, so
`smart_multiplier_enabled` remains disabled by default. These multipliers are
the recommended parameters only when a deployment explicitly enables smart
sizing, not evidence that smart sizing should replace ordinary fixed DCA.

For research artifact naming, the production-equivalent smart candidate is
`nasdaq_sp500_price_no_skip` and the direct candidate set is
`nasdaq_sp500_production_equivalent`. The defensive candidate that reduces or
skips buys in expensive regimes is retained only as a research variant.

The next external-signal research entry is
`nasdaq_sp500_external_precomputed_variants`. It keeps
`nasdaq_sp500_price_no_skip` as the current smart baseline and adds two
research-only precomputed context variants:

- `nasdaq_sp500_precomputed_valuation_guard`, using `cape_percentile` to reduce
  this period's contribution to `0.75x` only in high-valuation regimes.
- `nasdaq_sp500_precomputed_vol_breadth_stress`, using `vix_percentile` and
  `breadth_above_sma200_pct` to raise this period's contribution to `1.25x`
  only when volatility stress and weak breadth coincide.

These candidates require point-in-time external context CSVs or future signal
source artifacts. In the strategy research helper they are tied to the
`research:nasdaq_sp500_external_context_precomputed` consumer contract, which
expects `US-EQUITY-CONTEXT` fields for `cape_percentile`, `vix_percentile`, and
`breadth_above_sma200_pct`. They are not production defaults, and they should
not be enabled unless a robustness matrix beats fixed DCA under the same review
gates used for the price-only sweep.

## Execution Contract

The profile uses:

- `required_inputs = {"market_history", "portfolio_snapshot"}`
- `target_mode = "value"`
- `signal_effective_after_trading_days = 0`

When actionable, target values are current managed-symbol market values plus
the planned buy amount. This makes the profile buy-only under normal execution:
it should not sell QQQM/SPLG simply to restore a fixed 50/50 ratio.
