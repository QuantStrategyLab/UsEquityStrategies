# Smart DCA research plan — English overview

[完整研究计划、门槛与 CLI](smart_dca_indicator_research_plan.md) · [Research index](README.md)

This is an overview of the plan dated 2026-06-19, not a new research result or investment recommendation. The complete document retains the candidate definitions, historical evidence ledger, sample windows, numerical thresholds and CLI examples.

## Baseline and research scope

`nasdaq_sp500_smart_dca` and `ibit_smart_dca` keep fixed-amount DCA as the default (`smart_multiplier_enabled=false`). Optional smart mode changes only the current contribution: it does not sell, change assets, rebalance to weights, add leverage/options or transfer cash between accounts. Monthly remains the default; weekly/quarterly cadence is explicit configuration, not a choice optimized after observing results.

Nasdaq/S&P research compares price rules with external valuation/CAPE, VIX, breadth, rates and liquidity signals. IBIT research compares the AHR999 baseline with reproducible BTC price/cycle, helper and externally sourced indicators. Research inputs remain separate from runtime injection and provider downloads.

## Evidence and rejection rules

- Freeze candidate families and economically meaningful presets before comparison; record failures and candidate-definition hashes. Use fixed DCA, current smart rules and simple dip-only benchmarks with identical contributions, warm-up, calendars and cash rules.
- Use point-in-time inputs, publication lags, out-of-sample and walk-forward/rolling-start evaluation. Vary execution days, cadence and contribution sizes; preserve uninvested cash. Do not use future constituents, future percentiles or revised macro history as contemporaneously available data.
- Report terminal value, drawdown, underwater duration, money-weighted return, deployment/cash, skipped buys, rolling relative gaps, availability/freshness and order count. Unclear licensing, unauditable/uncacheable sources or unavailable point-in-time history disqualify a candidate.
- The [shared rules](smart_dca_indicator_research_plan.md#shared-research-rules) and profile-specific sections retain all elimination thresholds. In particular, candidates outside buy-only scope are rejected. Price-only proxies must disclose missing dividends/fees; historical breadth cannot be reconstructed from today's constituents.

## Artifacts and promotion boundary

The offline CLI reads local CSV and emits metrics, decisions and manifests; it does not fetch vendors or read credentials. Coverage, robustness and selection outputs must agree on the frozen candidate universe and fixed benchmark. Insufficient or inconsistent scenario coverage keeps `hold_default_fixed_dca`.

The fixed selection effect-size gate requires worst relative terminal value at least equal to fixed DCA, median improvement at least `1%`, nonnegative worst `rank_score`, and terminal cash at most `35%`. At least three scenarios are required for manual-review eligibility; scenario count alone is not statistical significance. Diagnostics do not tune ranking or thresholds.

Before promotion, use the [promotion gate](smart_dca_indicator_research_plan.md#promotion-gate) on `review_decision.json`, `production_profile_decisions.csv` and, where available, `scenario_manifest.json`. Hash-pinned reviews recheck summaries, coverage, candidate summaries/specs and input metadata. Require runtime-consumer coverage only for matrices carrying the corresponding external-source evidence, and verify the referenced hashes/sizes.

Passing produces advisory manual-review eligibility only. `default_change_allowed_by_research=false` remains in force; defaults and live authority require separate decisions. Preserve provider/data-license requirements, provenance and the repository [license](../../LICENSE).
