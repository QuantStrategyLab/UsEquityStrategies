# IBIT AHR999 Helper Candidate Matrix

Run date: 2026-06-19. Data as-of: 2026-06-18.

This note records a bounded robustness matrix for the research-only
`ibit_btc_ahr999_helper_precomputed_variants` set. It does not change
`ibit_smart_dca` production behavior. The runtime default remains fixed DCA.

## Input

- Raw source: Binance Spot API `BTCUSDT` daily klines, 2017-08-17 through
  2026-06-18.
- Raw rows: 3,228.
- Signal transform: `crypto.btc.ahr999.v1` from `MarketSignalSources`.
- Signal CSV rows: 3,029, from 2018-03-04 through 2026-06-18.
- Signal CSV SHA-256:
  `bf732d771cfe89c0627f48363e1793878816aa08091bcc9ed7b58347571a8518`.
- Trade path: BTC close proxy, matching the current full-cycle IBIT proxy
  research convention.

Generated artifacts are local and not committed. The latest rerun with
diagnostic fields is under:

`/home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/helper_matrix_diagnostics/`

The signal manifest still verifies the full exported CSV. Signal contract
validation is scoped to the actual matrix window, 2018-04-25 through
2026-06-18, so the first 30 warm-up rows for `ahr999_30d_slope` are not part of
the required finite-value validation set.

## Matrix

- Candidate set: `ibit_btc_ahr999_helper_precomputed_variants`.
- Compared candidates:
  - `ibit_btc_precomputed_ahr999_cycle`
  - `ibit_btc_precomputed_ahr999_guarded_cycle`
  - `ibit_btc_precomputed_ahr999_percentile_cycle`
- Cadences: weekly, monthly, quarterly.
- Contribution scales: $500, $1,000, $3,000 monthly equivalent.
- Monthly/quarterly execution days: 15 and 25.
- Sample windows:
  - `full`: 2018-04-25 through 2026-06-18
  - `cycle2020`: 2020-01-01 through 2021-12-31
  - `bear2022`: 2022-01-01 through 2023-12-31
  - `ibit_live`: 2024-01-25 through 2026-06-18
- Scenario count: 60.
- Coverage gate: passed.

## Result

| Candidate | Pass rate | Worst vs fixed | Median vs fixed | Worst DD delta | Max cash ratio | Status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `ibit_btc_precomputed_ahr999_guarded_cycle` | 95% | -4.46% | 0.00% | +0.13 pp | 6.77% | failed gate |
| `ibit_btc_precomputed_ahr999_cycle` | 90% | -3.46% | +1.32% | +0.38 pp | 19.18% | failed gate |
| `ibit_btc_precomputed_ahr999_percentile_cycle` | 60% | -11.59% | -2.11% | +0.03 pp | 17.30% | failed gate |

Diagnostic summary from the rerun:

| Candidate | Dominant diagnosis | Failure reasons | Deployment drag | Max skipped buys |
| --- | --- | --- | ---: | ---: |
| guarded | `terminal_edge_non_negative` | `terminal_underperformance_without_drawdown_improvement` | -18.75 pp | 20.83% |
| AHR999 baseline | `terminal_edge_non_negative` | `skip_rate_too_high_without_drawdown_improvement`, `terminal_underperformance_without_drawdown_improvement` | -52.08 pp | 54.17% |
| percentile | `terminal_underperformance_vs_fixed` | `terminal_underperformance_without_drawdown_improvement` | -25.00 pp | 29.52% |

The best observed helper candidate was
`ibit_btc_precomputed_ahr999_guarded_cycle`, but it still failed the robustness
gate because the worst scenario underperformed fixed DCA by 4.46%. The weakest
scenario was:

`sample_window_cycle2020__monthly_day_15_contribution_usd_3000_start_2020_01_01`

Window-level summary:

| Window | Candidate | Pass rate | Worst vs fixed | Median vs fixed | Median skip ratio |
| --- | --- | ---: | ---: | ---: | ---: |
| full | AHR999 baseline | 100% | +7.75% | +8.36% | 27% |
| full | guarded | 100% | -0.25% | +0.98% | 10% |
| full | percentile | 40% | -3.57% | -1.52% | 22% |
| cycle2020 | AHR999 baseline | 60% | -3.46% | -3.24% | 53% |
| cycle2020 | guarded | 80% | -4.46% | -3.60% | 17% |
| cycle2020 | percentile | 80% | -11.59% | -7.79% | 25% |
| bear2022 | AHR999 baseline | 100% | 0.00% | +1.24% | 8% |
| bear2022 | guarded | 100% | 0.00% | 0.00% | 0% |
| bear2022 | percentile | 40% | -5.86% | -2.51% | 20% |
| ibit_live | AHR999 baseline | 100% | -0.01% | +0.90% | 19% |
| ibit_live | guarded | 100% | -0.01% | +0.11% | 10% |
| ibit_live | percentile | 80% | -3.42% | -0.79% | 20% |

## Decision

Do not promote either helper candidate. The matrix supports keeping
`ibit_smart_dca` in fixed DCA by default and keeping the production-equivalent
smart candidate as `ibit_btc_precomputed_ahr999_cycle` for explicit smart-mode
research comparison.

The selection summary still chooses
`ibit_btc_precomputed_ahr999_guarded_cycle` as the best observed smart
candidate, but the recommendation remains `hold_default_fixed_dca` and
`not_recommended_for_enablement`. It failed the minimum-effect gate on worst
terminal edge, median terminal edge, and worst rank score. The guarded helper is
useful as a diagnostic variant because it lowers cash drag and skip frequency
versus the baseline AHR999 gate, but it gives up too much terminal value in the
2020 cycle window. The percentile-only candidate is weaker across most windows
and should be deprioritized unless a future hypothesis changes its economic
interpretation.

## Reproduction

The matrix was generated with:

```bash
python -m market_signal_sources.cli.export_btc_cycle_research_csv \
  --input-csv /home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/raw/btcusdt_1d_binance_2017-08-17_2026-06-18.csv \
  --output-csv /home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/signals/btc_cycle_indicators.csv \
  --manifest-path /home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/signals/btc_cycle_indicators.manifest.json \
  --as-of 2026-06-18

python -m us_equity_strategies.backtests.smart_dca_research_cli \
  --signal-csv /home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/signals/btc_cycle_indicators.csv \
  --trade-csv /home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/raw/btcusdt_1d_binance_2017-08-17_2026-06-18.csv \
  --signal-manifest /home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/signals/btc_cycle_indicators.manifest.json \
  --output-dir /home/ubuntu/Projects/dca_research_runs/ibit_helper_20260618/helper_matrix_diagnostics \
  --candidate-set ibit_btc_ahr999_helper_precomputed_variants \
  --signal-columns ahr999,ahr999_365d_percentile,ahr999_30d_slope \
  --trade-column close \
  --execution-days 15,25 \
  --robustness-preset standard \
  --sample-windows full:2018-04-25:2026-06-18,cycle2020:2020-01-01:2021-12-31,bear2022:2022-01-01:2023-12-31,ibit_live:2024-01-25:2026-06-18 \
  --min-review-scenarios 12
```
