# Global ETF Rotation — Research Evidence P0

This bundle is the first research-governance sample for `global_etf_rotation`.
It records the current implementation, planned benchmarks and optimization
boundary without manufacturing a historical result.

## Current decision

`rework` / `research_backtest_only` under the new review standard.

The runtime catalog is intentionally unchanged by this bundle.  It must not be
interpreted as an OOS pass, a live promotion, a Kelly approval, or permission
to change capital allocation.

The executable `build_target_weights` market-history proxy now consumes the
frozen runtime signal configuration: SMA250, hold bonus, confidence weighting,
and the configured ETF/canary pool. It preserves close-derived event signals
with one trading-day effectiveness and treats `mode=hold` as no rebalance.
The proxy omits the income, option and market-regime layers and does not run
the runtime daily canary schedule, so it is not a replay of the full live
profile.
The earlier SMA200/no-hold-bonus/no-confidence proxy snapshot remains a historical
record; this update does not turn that old result into new evidence.

## Known blockers

- The repository has no immutable point-in-time historical feature-snapshot
  manifest for the chosen research window.
- Alpaca's public historical-data documentation starts in 2016, while the
  frozen specification starts training in 2015; the 27-ETF source, licensing,
  feed and corporate-action package still requires concrete verification. See
  [Alpaca market data documentation](https://docs.alpaca.markets/us/docs/about-market-data-api).
- The lifecycle walk-forward utility can synthesize proxy market history; such
  output is not admissible as research or drift baseline evidence.
- Immutable point-in-time data and one frozen research configuration contract
  are still required before any optimization or OOS claim is allowed.
- No complete all-trial ledger, real returns/trades/positions artifact, or
  cost-stress result exists for this specification.
- The older research note used a 5 bps turnover assumption, while the current
  orchestrator default is 10 bps.  The discrepancy must be reconciled using
  executable, versioned real-data runs.

`research-spec.json` deliberately encodes these failed hard gates.  The QPK
validator is expected to reject it until the corresponding source artifacts
are supplied.  `optimization-spec.json` is a frozen *future experiment plan*;
it contains no result and cannot override the blocked research state.

## Required P0 completion evidence

1. Immutable PIT price/universe/feature-snapshot manifest and replay record.
2. Real-data returns, trades, positions and cost artifacts with hashes.
3. A complete trial ledger, including rejected and failed trials.
4. Pre-registered nested walk-forward folds and a once-only locked holdout.
5. 1x/2x/3x cost-stress results against the registry in this directory.
