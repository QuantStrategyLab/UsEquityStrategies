# Nasdaq/S&P 500 Smart DCA Design


## 中文摘要

- 用途：本文档围绕 `Nasdaq/S&P 500 Smart DCA Design`，用于理解 `UsEquityStrategies` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Purpose`、`Research Basis`、`Default Instruments`、`Cadence`、`Sizing Rules`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。

## Purpose

`nasdaq_sp500_smart_dca` is a buy-only accumulation profile for cash-funded
accounts. It does not rebalance by selling existing ETF positions. Each
execution window decides how much new cash to deploy into Nasdaq 100 and S&P
500 ETF sleeves.

## Research Basis

The default design combines three common ideas:

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
- `base_investment_usd = 1000`
- `max_investment_usd = 2000`
- `min_investment_usd = 200`
- `cash_reserve_usd = 50`

The intended runtime schedule is daily near the US close during the 25th to
29th calendar-day window. If cash is not available, the strategy emits
`no_execute`; a later run in the same window can still buy if cash arrives.

For weekly accumulation, deployments may set:

- `cadence = "weekly"`
- `weekly_day = 4`
- a lower `base_investment_usd`

## Sizing Rules

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
| `severe_pullback` | average 252-day drawdown >= 25% | 2.00x |
| `deep_pullback` | average drawdown >= 15% or average gap <= -10% | 1.50x |
| `mild_pullback` | average drawdown >= 8% or average gap <= -5% | 1.25x |
| `normal` | no discount or overvaluation trigger | 1.00x |
| `expensive` | average gap >= 12% and drawdown <= 3% | 0.50x |
| `very_expensive_overbought` | average gap >= 20%, drawdown <= 3%, both RSI >= 70 | 0.00x |

The actual buy is capped by available cash after `cash_reserve_usd`. If the
result is below `min_investment_usd`, the strategy waits/skips instead of
creating a tiny order.

## Execution Contract

The profile uses:

- `required_inputs = {"market_history", "portfolio_snapshot"}`
- `target_mode = "value"`
- `signal_effective_after_trading_days = 0`

When actionable, target values are current managed-symbol market values plus
the planned buy amount. This makes the profile buy-only under normal execution:
it should not sell QQQM/SPLG simply to restore a fixed 50/50 ratio.
