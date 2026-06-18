# IBIT Smart DCA Design Note

This note records the first runtime implementation for `ibit_smart_dca`. It is not live-performance evidence and should not be treated as investment advice.

## Product Context

`IBIT` is the iShares Bitcoin Trust ETF listed on Nasdaq. BlackRock describes the fund as a convenient exchange-traded product for bitcoin exposure, with a benchmark of the CME CF Bitcoin Reference Rate - New York Variant and a fund launch date of 2024-01-05:

- https://www.blackrock.com/us/individual/products/333011/ishares-bitcoin-trust-etf

Because IBIT is a spot bitcoin ETF rather than a diversified equity ETF, the default strategy treats it as a small satellite sleeve instead of a broad-market core holding.

## Strategy Shape

The implementation follows `nasdaq_sp500_smart_dca` where possible:

- direct runtime inputs: `market_history` and `portfolio_snapshot`
- buy-only value targets; no automatic sell-down
- monthly or weekly execution window
- cash reserve and minimum investment gates
- trend, pullback, and overvaluation regimes based on daily close history

The implementation borrows one idea from `CryptoStrategies`: BTC exposure should have an account-size-aware target budget. The crypto package computes a dynamic BTC target ratio for the core BTC sleeve. This US-equity ETF profile scales that idea down into a conservative satellite sleeve:

- default dynamic base ratio: 3%
- default account-size growth term: `0.02 * log1p(total_equity / 10000)`
- default maximum IBIT sleeve: 10%

This means the strategy can accelerate buys during larger IBIT drawdowns, but only until the configured sleeve capacity is filled.

## Default Regimes

IBIT uses wider thresholds than broad equity ETFs:

| Regime | Default trigger | Default multiplier |
| --- | --- | ---: |
| `severe_pullback` | 252-day drawdown >= 40% | 2.50x |
| `deep_pullback` | drawdown >= 25% or <= -18% vs SMA200 | 1.75x |
| `mild_pullback` | drawdown >= 12% or <= -8% vs SMA200 | 1.25x |
| `very_expensive_overbought` | >= 60% vs SMA200, shallow drawdown, RSI overbought | 0.00x |
| `expensive` | >= 30% vs SMA200 with shallow drawdown | 0.50x |
| `weak_trend` | below the positive trend condition without a pullback discount | 0.50x |
| `normal` | none of the above | 1.00x |

## Live Enablement Notes

Before live enablement, rerun paper or dry-run evidence across broker platforms and review:

- IBIT data availability and split/adjustment handling
- integer-share behavior for small accounts
- monthly turnover and cash reserve behavior
- max satellite exposure after a strong IBIT rally or deep bitcoin drawdown
- account-level aggregate crypto exposure outside this profile
