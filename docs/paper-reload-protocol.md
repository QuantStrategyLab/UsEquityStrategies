# Multi-Platform Paper Reload Protocol

English | [简体中文](paper-reload-protocol.zh-CN.md)

This protocol applies to all strategies, platforms, and plugin combinations, especially leveraged strategies. It does not replace backtesting or grant live trading authority.

1. **Evidence completion**: Produce reproducible historical backtests, trade/position series, cost assumptions, stress scenarios, and manual acceptance; the evidence package must pass promotion validation.
2. **Release identity**: Run `assess_strategy_release.py` against the committed strategy revision, configuration, risk rules, evidence, and full plugin bundle. Only `ready: true` allows a controlled release pipeline to generate a manifest.
3. **All-target preloading**: Load the same release identity into every target platform's paper runtime; each platform must return a matching `runtime_release_receipt`. Any missing receipt or digest mismatch halts reload.
4. **Paper execution checks**: First verify read-only status, order cancellation, unknown-order reconciliation, `HALTED`/`REDUCING` boundaries, and risk-increase blocking; any anomaly allows only de-risking or halting.
5. **Observation period**: Across the agreed trading sessions and at least one high-volatility scenario, compare targets, fill receipts, positions, and risk diagnostics. On drift, return to step 1 instead of relaxing parameters or increasing risk.
6. **Separate authorization**: Passing paper observation does not authorize live trading. Live enablement, higher leverage, or stop-loss changes still require fresh evidence, a new manifest, and explicit manual approval.

SOXL is currently blocked at step 1: no verifiable promotion evidence package exists in this repository, so there is no manifest and it cannot enter reload, `ACTIVE`, or live stages on any platform.
