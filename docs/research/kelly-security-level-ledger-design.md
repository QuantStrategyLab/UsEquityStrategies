# Design Note: Security-Level Net Trading Ledger for Outer Kelly (RS-07 (c))

English | [简体中文](kelly-security-level-ledger-design.zh-CN.md)

> Research only. Design note only—modifies no code or production behavior.

## 1. Current State (Verified for Audit Finding (c))

The outer layer of `fractional_kelly_walk_forward` and `portfolio_kelly_walk_forward` treats each strategy return stream as a **virtual asset**:

- Before each session open, the portfolio rebalances to target fraction `f` (or weights `w` in the portfolio variant), leaving the remainder in cash.
- Turnover equals outer rebalance `|f - drifted_position| + f * inner_turnover`, and fees multiply turnover by bps.
- The outer cash leg (BOXX return series) **incurs no fees**, whereas trading BOXX inside the inner core path does incur fees.
- Buys and sells of the same security across outer and inner layers **do not net against each other**. For example, if the outer layer trims SOXL while the inner layer adds SOXL, both legs pay fees.

Consequence: fees may be overstated (no cross-layer netting) or understated (free outer cash leg, while daily rebalancing in practice creates fractional trades), with unknown net direction. Current cost tiers are therefore approximations, as noted in `cost_model_disclosure`.

## 2. Goal

Compute daily net trades at the **security level**: aggregate target weights across all layers per holding, then compare against the prior day's drifted holdings to compute one net trade per security. Fees and turnover both read from that net trade.

## 3. Design

1. **Holdings table**: For each trading day `t`, record `holdings[t][symbol]` as fraction of NAV across SOXL, SOXX, TQQQ, QQQM, BOXX, and cash.
2. **Composite target**: `target[t][s] = sum_k w_k(t) * inner_weight_k(t)[s]`, where `k` indexes strategy streams (one stream with `w = f` in the single-strategy case). Outer cash and inner BOXX merge into one BOXX position alongside an unremunerated, fee-free true-cash item (settlement buffer only, default 0).
3. **Net trade**: `trade[t][s] = target[t][s] - drift(holdings[t-1])[s]`, and `turnover[t] = sum_s |trade[t][s]|`, including BOXX consistently with the inner layer.
4. **Fees**: `cost[t] = sum_s |trade[t][s]| * bps_s * multiplier`. Per-security bps (such as tighter spreads for BOXX) are allowed if preregistered in advance.
5. **Rebalance rule (preregistered parameter)**: Keep daily-to-target as an upper bound; add a drift band (rebalancing only when any security deviates by more than `x%`) as a more realistic variant. Changing the rule counts as a new trial and must be recorded in the ledger.
6. **Input requirement**: Core generators must output daily inner **security** weights (already present in `CorePath.weights`) rather than aggregate turnover alone.
7. **Verification**: (a) For a single strategy with `f = 1` and no outer rebalance, net ledger turnover matches inner turnover exactly; (b) when outer and inner trades oppose, net trade is strictly below their sum; (c) cash plus BOXX and all securities sum identically to 1.
8. **Output**: In `backtest_report.v1` `cost_model`, set `model_id` to `security_level_net_trades` while keeping the legacy figure as a diagnostic for comparison.

## 4. Out of Scope

Whole shares, settlement cycles (`T+1`), minimum trade notional, and live execution slippage wait for live fill exports (see the parity checklist) before calibration.
