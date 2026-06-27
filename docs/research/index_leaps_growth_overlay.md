# Index LEAPS growth overlay research design

[简体中文](index_leaps_growth_overlay.zh-CN.md)

_Last updated: 2026-06-23_

> Investing involves risk. This is an engineering research note, not investment advice.

## Summary

Buying `QQQ` / `SPY` LEAPS calls can be studied as a growth overlay, but should
not replace the current income layer. The income layer targets portfolio drawdown
budgeting with `SCHD`, `DGRO`, `SGOV`, `SPYI`, and `QQQI`. LEAPS add convex
upside with premium at risk and behave like a capped re-leverage sleeve.

| Layer | Goal | Default handling |
| --- | --- | --- |
| Income / stability | Reduce portfolio stress drawdown | Keep existing `income_layer_*`; do not put LEAPS in `income_layer_allocations` |
| LEAPS growth overlay | Add long-index convexity with a small premium budget | Config visible by default; live gate blocks real option intents until promotion evidence exists |

## Related artifacts

- Full Chinese design note: [index_leaps_growth_overlay.zh-CN.md](index_leaps_growth_overlay.zh-CN.md)
- Proxy backtest module and CLI outputs: [UsEquitySnapshotPipelines/docs/index-leaps-growth-overlay-research.md](../../UsEquitySnapshotPipelines/docs/index-leaps-growth-overlay-research.md)

## Status

Research-only. Option overlay recipes remain blocked from live order intent until
real option-chain history validation is archived.
