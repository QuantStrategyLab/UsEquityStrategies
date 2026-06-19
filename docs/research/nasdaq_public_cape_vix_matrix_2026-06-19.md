# Nasdaq Public CAPE/VIX Matrix - 2026-06-19

This note records the first end-to-end public-data run for the
`nasdaq_sp500_cape_vix_precomputed_variants` smart-DCA candidate. It is a
source-chain validation run, not a production recommendation.

## Data

- VIX input: FRED `VIXCLS`, downloaded from
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS`.
- Trade price input: FRED `NASDAQCOM`, downloaded from
  `https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQCOM`.
- CAPE input: Yale/Shiller `ie_data.xls`, downloaded from
  `http://www.econ.yale.edu/~shiller/data/ie_data.xls`.
- Run directory:
  `/home/ubuntu/Projects/dca_research_runs/public_cape_vix_20260619`.
- Latest diagnostic strategy output:
  `/home/ubuntu/Projects/dca_research_runs/public_cape_vix_20260619/strategy/nasdaqcom_cape_vix_diagnostics`.

The downloaded Yale/Shiller file only contained CAPE data through
`2023-09-30`, so the strategy matrix was capped at `2023-09-29`. This means the
run is useful for validating the signal-source chain and historical behavior,
but it is not enough to choose a current Nasdaq smart-DCA default.

The Stooq QQQ CSV endpoint returned a browser verification page during this run,
so QQQ was not used. FRED `NASDAQCOM` was used instead to keep the input source
auditable.

## Source Artifact

`MarketSignalSources` exported:

- signal CSV:
  `/home/ubuntu/Projects/dca_research_runs/public_cape_vix_20260619/signals/us_equity_public_context.csv`
- manifest:
  `/home/ubuntu/Projects/dca_research_runs/public_cape_vix_20260619/signals/us_equity_public_context.manifest.json`
- quality report:
  `/home/ubuntu/Projects/dca_research_runs/public_cape_vix_20260619/signals/us_equity_public_context.quality.json`

Validation passed for `research_export.v1` with
`artifact_type=us_equity_context_research_csv` and
`transform=us_equity.nasdaq_sp500.context.v1`. The manifest pinned the quality
report and the validator verified both quality-report SHA-256 and file size.

Quality status was `warn`, not `fail`, because FRED VIX contained null rows and
rows after the capped `as_of` date were filtered. The exported signal covered
`1990-01-02` through `2023-09-29` with 8511 rows.

## Strategy Matrix

Command shape:

```bash
python -m us_equity_strategies.backtests.smart_dca_research_cli \
  --signal-csv ./signals/us_equity_public_context.csv \
  --trade-csv ./processed/NASDAQCOM_trade.csv \
  --signal-manifest ./signals/us_equity_public_context.manifest.json \
  --signal-quality-report ./signals/us_equity_public_context.quality.json \
  --output-dir ./strategy/nasdaqcom_cape_vix_diagnostics \
  --candidate-set nasdaq_sp500_cape_vix_precomputed_variants \
  --signal-columns cape_percentile,vix_percentile \
  --trade-column close \
  --robustness-preset standard \
  --start-date 1990-01-02 \
  --end-date 2023-09-29 \
  --min-review-scenarios 10
```

The matrix produced 33 scenarios across:

- cadences: weekly, monthly, quarterly
- monthly/periodic contribution amounts: 500, 1000, 3000
- monthly and quarterly execution days: 1, 10, 15, 20, 25
- start date: 1990-01-02

The strategy CLI recorded
`matches_signal_manifest_quality_report=true`, proving the supplied quality
report matched the signal manifest's pinned report.

## Result

The only smart candidate in this set,
`nasdaq_sp500_precomputed_cape_vix_guard`, passed the matrix robustness coverage
gate but failed the effect-size gate against fixed DCA:

- pass rate: `1.0`
- worst relative terminal value vs fixed DCA: `-13.78%`
- median relative terminal value vs fixed DCA: `-13.65%`
- worst rank score: `-12.97`
- max terminal cash ratio: `2.90%`
- deployment-rate drag vs fixed DCA: `-20.00 percentage points`
- max skipped-buy ratio: `0.0`
- dominant performance diagnosis: `terminal_underperformance_vs_fixed`
- performance diagnoses:
  `below_fixed_average_multiplier`,
  `drawdown_better_than_fixed`,
  `lower_deployment_rate`,
  `paid_terminal_value_for_drawdown_relief`,
  `terminal_underperformance_vs_fixed`
- regimes observed:
  `cape_vix_normal`,
  `cape_vix_valuation_expensive_guard`,
  `cape_vix_volatility_stress_add`

The review decision was:

- `overall_recommendation_status=hold_default_fixed_dca`
- `runtime_default_recommendation=fixed_dca`
- `smart_mode_enablement_status=not_recommended_for_enablement`
- blocking reason: `insufficient_effect_size_vs_fixed_dca`
- effect-size failure reasons:
  `worst_terminal_edge_below_min_effect_size`,
  `median_terminal_edge_below_min_effect_size`,
  `worst_rank_score_below_min_effect_size`

## Interpretation

This confirms the current CAPE/VIX guard is not a better default than fixed DCA
on the auditable FRED/Yale public dataset. The rule spends most periods below
the fixed baseline multiplier, with average scheduled multiplier around `0.80`.
That cash drag is consistent with the broader finding that valuation guards tend
to underperform fixed DCA in long rising equity markets.

The diagnostic rerun also confirms the underperformance is not primarily a
failed-order or skip-frequency problem: the max skipped-buy ratio is zero. The
rule buys every scheduled period, but it routinely buys less than fixed DCA in
high-CAPE regimes. It buys some volatility stress periods at `1.25x`, yet that
does not offset the long-horizon deployment drag. This makes open-ended
threshold tuning a poor next step unless a richer point-in-time breadth or
macro source changes the economic hypothesis.

The next useful research step is not to tune this rule open-ended. It is to
improve the data-source layer first:

- find or license a current CAPE source with reproducible snapshots beyond
  `2023-09-30`;
- add a stable adjusted ETF/index total-return input for QQQ or Nasdaq-100;
- rerun fixed, no-skip, and smart candidates across pinned discovery,
  validation, and out-of-sample windows before considering any parameter change.
