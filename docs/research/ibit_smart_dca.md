# IBIT Smart DCA Design Note

This note records the implementation for `ibit_smart_dca`. It is not
live-performance evidence and should not be treated as investment advice. The
profile defaults to fixed-amount ordinary DCA for platforms without native
recurring investment support; smart sizing is optional.

## Product Context

`IBIT` is the iShares Bitcoin Trust ETF listed on Nasdaq. BlackRock describes the fund as a convenient exchange-traded product for bitcoin exposure, with a benchmark of the CME CF Bitcoin Reference Rate - New York Variant and a fund launch date of 2024-01-05:

- https://www.blackrock.com/us/individual/products/333011/ishares-bitcoin-trust-etf

Because IBIT is a spot bitcoin ETF rather than a diversified equity ETF, the intended deployment shape is a dedicated account profile: one platform account runs this strategy as its main accumulation route.

## Strategy Shape

The implementation follows `nasdaq_sp500_smart_dca` where possible:

- direct runtime inputs: `market_history` and `portfolio_snapshot`
- default signal source: `BTC-USD`
- default trade target: `IBIT`
- buy-only value targets; no automatic sell-down
- weekly, monthly, or quarterly execution window
- cash reserve and minimum investment gates
- fixed-amount investment sizing from `base_investment_usd`
- optional trend, pullback, and overvaluation regimes based on daily close history

The strategy follows the same value-target pattern as `nasdaq_sp500_smart_dca`: each actionable run sets `target_value = current IBIT market value + planned investment`. It does not cap IBIT by a portfolio percentage and does not rebalance by selling.

The default monthly execution window opens on `monthly_day`. Deployments can
switch `cadence` to `weekly` or `quarterly`. Each cadence has its own
`*_window_calendar_days` setting so the platform can retry after a non-cash
execution failure. The strategy itself does not persist order-success state;
successful execution and retry suppression belong to the platform runtime.

Funding semantics match `nasdaq_sp500_smart_dca`: the strategy does not maintain
a separate cash pool for future dip buys. On each scheduled run it requests
`base_investment_usd`; when smart sizing is enabled, it requests
`base_investment_usd * multiplier`, optionally capped by `max_investment_usd`.
If available cash is below the requested amount, it skips rather than placing a
partial DCA order. Dedicated DCA accounts default to `cash_reserve_usd = 0` and
no single-run cap; deployments that need a small buffer or maximum order amount
can override `cash_reserve_usd` or `max_investment_usd`.

## Default Regimes

The default signal source uses bitcoin spot history rather than IBIT history. This mirrors `nasdaq_sp500_smart_dca`, which reads QQQ/SPY signals and trades QQQM/SPLG accumulation ETFs. Deployments can override `signal_symbols` if their data provider uses a different bitcoin symbol such as `BTCUSDT`.

The strategy emits dollar `target_value` outputs and this platform DCA profile
assumes fractional-share / dollar-order execution. `min_investment_usd` is only a
small-order guardrail, not an integer-share constraint. The default guardrail is
`min_investment_usd = 5`.

Sizing controls:

- `cash_reserve_usd = 0` by default for dedicated DCA accounts
- `investment_amount_mode = "fixed"` uses `base_investment_usd` for fixed weekly, monthly, or quarterly DCA; this is the only amount mode
- `max_investment_usd = null` by default; set it only when the deployment needs a single-run cap
- `cadence = "monthly"` by default; `weekly` and `quarterly` are supported configuration options
- `monthly_window_calendar_days`, `weekly_window_calendar_days`, and `quarterly_window_calendar_days` define retry windows
- `smart_multiplier_enabled = false` keeps the profile in ordinary DCA mode; this is the default
- `smart_multiplier_enabled = true` enables bitcoin pullback / overvaluation sizing

Ordinary DCA mode does not need 252-day signal indicators before it can place the
scheduled buy. Smart sizing mode still requires enough bitcoin spot history to
classify the pullback or overvaluation regime.

When smart sizing is enabled, bitcoin uses wider thresholds than broad equity ETFs:

| Regime | Default trigger | Default multiplier |
| --- | --- | ---: |
| `severe_pullback` | 252-day drawdown >= 40% | 2.50x |
| `deep_pullback` | drawdown >= 25% or <= -18% vs SMA200 | 1.75x |
| `mild_pullback` | drawdown >= 12% or <= -8% vs SMA200 | 1.25x |
| `very_expensive_overbought` | >= 60% vs SMA200, shallow drawdown, RSI overbought | 0.00x |
| `expensive` | >= 30% vs SMA200 with shallow drawdown | 0.50x |
| `normal` | none of the above | 1.00x |

## BTC Proxy Backtest

IBIT live history is too short to prove a full-cycle DCA sizing rule. For the
first proxy check, use Binance spot `BTCUSDT` daily closes as both the bitcoin
signal and a synthetic IBIT NAV path. The Binance spot API documents
`GET /api/v3/klines` as kline/candlestick bars identified by open time:

- https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints

Run date: 2026-06-19. Data window: 2017-08-17 through 2026-06-18. Parameters:
monthly contribution $1,000, fractional/dollar-order execution, monthly day 25,
optional smart multipliers, default max investment $2,000, no cash reserve in the
research harness, and a shared 252-day warm-up before both smart and fixed DCA
begin buying.

| Window | First buy | Contributions | Smart terminal | Fixed terminal | Smart vs fixed | Smart max DD | Fixed max DD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full BTC proxy | 2018-04-25 | $99,000 | $383,943.78 | $386,853.54 | -0.75% | 74.37% | 74.40% |
| 2020 cycle | 2020-01-25 | $78,000 | $169,033.49 | $173,253.73 | -2.44% | 68.73% | 69.12% |
| 2022 bear/recovery | 2022-01-25 | $54,000 | $81,541.27 | $81,709.46 | -0.21% | 47.29% | 47.37% |
| IBIT-launch proxy | 2024-01-25 | $30,000 | $24,321.96 | $24,394.47 | -0.30% | 40.18% | 40.40% |

This does not justify making smart sizing the default: smart sizing slightly
reduced drawdown but did not beat fixed DCA terminal value in any checked proxy
window. The runtime profile remains useful as a fixed DCA execution engine.

## Live Enablement Notes

Before live enablement, rerun paper or dry-run evidence across broker platforms and review:

- bitcoin market-history availability and symbol mapping, for example `BTC-USD` vs `BTCUSDT`
- IBIT orderability and split/adjustment handling
- fractional-share / dollar-order behavior for the target broker account
- monthly turnover and cash reserve behavior
- retry behavior after failed execution and no duplicate buy after successful execution
- smart DCA vs fixed DCA return, drawdown, deployment rate, and terminal value
- account-level aggregate crypto exposure outside this profile
