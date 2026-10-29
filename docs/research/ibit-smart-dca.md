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

- direct runtime inputs: `derived_indicators` and `portfolio_snapshot`
- compatible fallback signal source: `market_history` for `BTC-USD`
- default trade target: `IBIT`
- buy-only value targets; no automatic sell-down
- weekly, monthly, or quarterly execution window
- cash reserve and minimum investment gates
- fixed-amount investment sizing from `base_investment_usd`
- optional AHR999 cycle regimes; BTC price-history pullback regimes remain as fallback

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

IBIT trades on US equity platforms, but its signal source is crypto-native. The
runtime contract therefore follows the crypto strategy package pattern: the
preferred input is an externally maintained `derived_indicators` snapshot keyed
by `BTC-USD`, `BTCUSDT`, or `BTC`. This avoids assuming that every US broker
platform can serve reliable bitcoin spot or chain-derived indicators. The
strategy still accepts `market_history` as a compatibility fallback for local
tests or platforms that already expose BTC spot close history.

The `CryptoStrategies` package uses externally supplied `derived_indicators`
and `benchmark_snapshot` inputs instead of fetching exchange data inside the
strategy. Its current live rotation indicators are trend and liquidity metrics:
`close`, `sma20`, `sma60`, `sma200`, `roc20`, `roc60`, `roc120`, `vol20`,
`avg_quote_vol_*`, `trend_persist_90`, `age_days`, and BTC benchmark ROC
fields. It does not currently use AHR999, MVRV, or NUPL directly, but the same
input-boundary pattern is the right fit for IBIT's crypto signal dependency.

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
- `smart_multiplier_enabled = true` enables external AHR999 cycle sizing when available
- `cycle_indicator_enabled = true` prefers AHR999 from `derived_indicators`; set it to `false` for price-history pullback sizing only

Ordinary DCA mode does not need 252-day signal indicators before it can place the
scheduled buy. Smart sizing can run from an external AHR999 snapshot without
pulling BTC price history. If no AHR999 value is supplied, the strategy falls
back to BTC spot history and requires enough daily closes to classify the
pullback or overvaluation regime.

AHR999 is a BTC cycle valuation indicator. Public descriptions define it as the
product of BTC price versus 200-day cost and BTC price versus an exponential
growth valuation fitted to coin age:

`AHR999 = (BTC price / 200-day cost) * (BTC price / growth estimate price)`

This implementation prefers the geometric 200-day cost (`ahr999` or
`ahr999_gma`) when an external snapshot supplies it. It can also derive a
compatible value from BTC close history using:

`growth estimate price = 10 ^ (5.84 * log10(days since 2009-01-03) - 17.01)`

Selected AHR999 smart sizing:

| Regime | Default trigger | Default multiplier |
| --- | --- | ---: |
| `ahr999_bottom` | AHR999 <= 0.45 | 3.00x |
| `ahr999_accumulation` | AHR999 <= 0.80 | 2.25x |
| `ahr999_dca` | AHR999 <= 1.20 | 1.50x |
| `ahr999_expensive` | AHR999 > 1.20 | 0.00x |

The `ahr999_expensive` rule intentionally skips smart DCA buys in the expensive
zone. Ordinary DCA mode is unaffected and still buys the fixed base amount.

Fallback BTC price-history sizing uses wider thresholds than broad equity ETFs:

| Regime | Default trigger | Default multiplier |
| --- | --- | ---: |
| `severe_pullback` | 252-day drawdown >= 40% | 3.00x |
| `deep_pullback` | drawdown >= 25% or <= -18% vs SMA200 | 2.25x |
| `mild_pullback` | drawdown >= 12% or <= -8% vs SMA200 | 1.50x |
| `very_expensive_overbought` | >= 60% vs SMA200, shallow drawdown, RSI overbought | 1.00x |
| `expensive` | >= 30% vs SMA200 with shallow drawdown | 1.00x |
| `normal` | none of the above | 1.00x |

## BTC Proxy Backtest

IBIT live history is too short to prove a full-cycle DCA sizing rule. For the
first proxy check, use Binance spot `BTCUSDT` daily closes as both the bitcoin
signal and a synthetic IBIT NAV path. The Binance spot API documents
`GET /api/v3/klines` as kline/candlestick bars identified by open time:

- https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints

Run date: 2026-06-19. Data window: 2017-08-17 through 2026-06-18. Parameters:
monthly contribution $1,000, fractional/dollar-order execution, monthly day 25,
optional smart multipliers, no cash reserve, no single-run cap, and a shared
252-day warm-up before both smart and fixed DCA begin buying.

The selected smart variant is AHR999 GMA gate-tier sizing. It buys more in the
bottom / accumulation / DCA zones and skips when AHR999 is above 1.20. This is
different from ordinary DCA: fixed DCA remains the default for accounts that
should invest every scheduled month regardless of crypto valuation.

For reproducible research artifacts, the production-equivalent smart candidate
is `ibit_btc_precomputed_ahr999_cycle`: it uses the externally supplied AHR999
field from `derived_indicators` and does not let Mayer Multiple change the
regime. Mayer-based candidates remain research variants / sanity checks, not the
current production smart-mode contract.

Additional Tier 1 helper candidates are available through
`ibit_btc_ahr999_helper_precomputed_variants`. They use precomputed
`ahr999_365d_percentile` and `ahr999_30d_slope` fields from the external BTC
cycle signal source to compare percentile-tier sizing and an expensive-zone
skip guard. These are research-only variants and do not change the production
equivalent candidate. The 2026-06-18 helper matrix is recorded in
`docs/research/ibit_ahr999_helper_matrix_2026-06-18.md`.

| Smart variant | Terminal | Vs fixed | Max DD | DD delta |
| --- | ---: | ---: | ---: | ---: |
| Fixed DCA benchmark | $386,795 | 0.00% | 74.40% | 0.00% |
| AHR999 GMA gate-tier, 3.00/2.25/1.50/0.00x | $412,311 | +6.60% | 72.24% | -2.16% |
| AHR999 GMA gate, 3.00/1.50/0.00x | $410,149 | +6.04% | 72.08% | -2.32% |
| AHR999 GMA gate plus price pullback fallback | $405,677 | +4.88% | 73.10% | -1.30% |
| Price pullback aggressive dip-only | $391,153 | +1.13% | 74.07% | -0.33% |
| Price pullback default dip-only | $387,124 | +0.09% | 74.19% | -0.21% |

Window sensitivity is material:

| Window | AHR999 GMA gate-tier vs fixed | DD delta |
| --- | ---: | ---: |
| Full BTC proxy, first buy 2018-04-25 | +6.60% | -2.16% |
| 2020 cycle | +5.58% | -10.47% |
| 2022 bear/recovery | +1.17% | -0.41% |
| IBIT-launch proxy, 2024-01-25 | -2.16% | -1.69% |

This is enough to replace the price-only smart selection with AHR999 GMA
gate-tier sizing when smart mode is explicitly enabled. It is still not enough
to make smart sizing the default: the 2024 IBIT-launch proxy window still trails
fixed DCA terminal value, and AHR999 skip behavior means some scheduled months
will intentionally invest nothing even when cash is available. The runtime
profile therefore remains ordinary fixed DCA by default.

## Live Enablement Notes

Before live enablement, rerun paper or dry-run evidence across broker platforms and review:

- bitcoin market-history availability and symbol mapping, for example `BTC-USD` vs `BTCUSDT`
- external `derived_indicators` freshness, especially `ahr999`, `close`, `sma200`, and provider timestamp
- IBIT orderability and split/adjustment handling
- fractional-share / dollar-order behavior for the target broker account
- monthly turnover and cash reserve behavior
- retry behavior after failed execution and no duplicate buy after successful execution
- smart DCA vs fixed DCA return, drawdown, deployment rate, and terminal value
- account-level aggregate crypto exposure outside this profile
