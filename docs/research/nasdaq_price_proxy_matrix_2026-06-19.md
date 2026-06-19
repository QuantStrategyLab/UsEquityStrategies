# Nasdaq Price Proxy Matrix 2026-06-19

Run date: 2026-06-19. This rerun closes the older Nasdaq/S&P price-only
reproducibility gap by using a hash-pinned `MarketSignalSources` price proxy
export and the strategy research CLI `--price-manifest` validation path.

## Inputs

Local artifact root:
`/home/ubuntu/Projects/dca_research_runs/nasdaq_price_proxy_20260619`

Raw source snapshots:

| Source | Path | SHA-256 |
| --- | --- | --- |
| FRED `NASDAQ100` | `raw/NASDAQ100.csv` | `fce42471b33558b9ca65f51dcaf493e22f272b5e926c7932951fd5bcc855688a` |
| FRED `SP500` | `raw/SP500.csv` | `35642a8c7dfb11155b8979bfe6664b9a7cd3832061863d351d37415d5f01e83a` |

Generated research inputs:

| Artifact | Path | SHA-256 |
| --- | --- | --- |
| Price proxy manifest | `processed/us_equity_price_proxy.manifest.json` | `d94d1fb5de73edc70b925c3a881c353d8198ee09aac7d9403fcfa942ce6e4b8d` |
| Price proxy CSV | `processed/us_equity_price_proxy.csv` | `722de36acf70cfdff2d01dc79200b5f18fede17e46af72a07460886f20cde91c` |
| 50/50 trade proxy manifest | `processed/nasdaq_sp500_50_50_trade_proxy.manifest.json` | `11ea05140c924f560a32ca2c2126106a97c5f6c2f8c1b794a1d0f0d1573040c6` |
| 50/50 trade proxy CSV | `processed/nasdaq_sp500_50_50_trade_proxy.csv` | `d271501c75275626687d1b4076912ec59fed1f5236ea5153de839efb60aabe2f` |

The price proxy manifest is `research_export.v1`,
`artifact_type=us_equity_price_proxy_research_csv`, and
`transform=us_equity.nasdaq_sp500.price_proxy.v1`. It covers 2,514 rows from
2016-06-20 through 2026-06-18. The strategy CLI validated the price contract
window from 2017-06-20 through 2026-06-18 and required positive `QQQ` / `SPY`
columns.

The trade proxy is a local 50/50 normalized price-only index:
`100 * (0.5 * NASDAQ100 / first_NASDAQ100 + 0.5 * SP500 / first_SP500)`.
It is not a total-return ETF proxy and does not include dividends, fees, taxes,
spreads, or borrow/cash yield.

## Research Handoff

The price proxy export was also wrapped as a minimal `market_signal_research_handoff`
for the generic signal source consumption path. This package intentionally covers
only the price proxy family and consumer; it is not a full all-family catalog.

| Artifact | Path | SHA-256 |
| --- | --- | --- |
| Research handoff | `research_handoff.json` | `2815684ff369c7ec784fac7d0dc9cc4fbd257fb9c44afea8edf2ec1656412a9e` |
| Source family catalog manifest | `handoff/source_catalog/signal_source_families.manifest.json` | `c74c52bff2c4547acd14e4b20f3023a8ae9997cffb7b969ea3441b19774d8300` |
| Consumer contract registry manifest | `handoff/contracts/market_signal_consumers.manifest.json` | `a4430fcf10fd5f69872e0dbf22c2178e300e8bf1afece82d16012f361fb2eaef` |

Validation summary:

- consumer: `research:nasdaq_sp500_price_proxy`
- source family: `us_equity.nasdaq_sp500_price_proxy_daily`
- research artifact type: `us_equity_price_proxy_research_csv`
- research transform: `us_equity.nasdaq_sp500.price_proxy.v1`
- `summary_verified = true`
- `linked_manifest_sha256s_verified = true`
- `ready_for_research_consumption = true`
- `runtime_injection_allowed = false`

## Matrix

Command shape:

```bash
python -m us_equity_strategies.backtests.smart_dca_research_cli \
  --signal-csv ./processed/us_equity_price_proxy.csv \
  --trade-csv ./processed/nasdaq_sp500_50_50_trade_proxy.csv \
  --price-manifest ./processed/us_equity_price_proxy.manifest.json \
  --trade-manifest ./processed/nasdaq_sp500_50_50_trade_proxy.manifest.json \
  --output-dir ./strategy/nasdaq_sp500_price_proxy_matrix \
  --candidate-set nasdaq_sp500_price_variants \
  --signal-columns QQQ,SPY \
  --trade-column close \
  --robustness-preset standard \
  --execution-days 1,10,15,20,25 \
  --sample-windows discovery:2017-06-20:2020-12-31,validation:2021-01-01:2023-12-31,oos:2024-01-01:2026-06-18 \
  --end-date 2026-06-18 \
  --pretty
```

The matrix covered 99 scenarios: 3 sample windows, 3 contribution values
(`$500`, `$1000`, `$3000`), 3 cadences (`weekly`, `monthly`, `quarterly`), and
monthly/quarterly execution-day variants on days `1,10,15,20,25`.

Top-level output hashes:

| Artifact | Path | SHA-256 |
| --- | --- | --- |
| Scenario manifest | `strategy/nasdaq_sp500_price_proxy_matrix/scenario_manifest.json` | `61eb2693ca69cbbeb4188f62bdbbc222da31908156748eee6e88283ce22c6ec7` |
| Review decision | `strategy/nasdaq_sp500_price_proxy_matrix/review_decision.json` | `fda0a57d57c956684b27e22f0a9ba132775f5c4eecbe21903ae588400040856a` |
| Robustness summary | `strategy/nasdaq_sp500_price_proxy_matrix/robustness_summary.csv` | `4ebcd1ddcc853ed508b8c5a47759f186759ab4d8a562475f6a563a023e585670` |
| Selection summary | `strategy/nasdaq_sp500_price_proxy_matrix/selection_summary.csv` | `ca9fc47e5fd7a7a58dc0a3b36953f87147e3fc6d52e85ccea1f5158d59510b44` |

## Results

| Candidate | Scenarios | Pass rate | Robustness gate | Min terminal vs fixed | Median terminal vs fixed | Max terminal cash | Diagnosis |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |
| `nasdaq_sp500_price_no_skip` | 99 | 100.0% | pass | 0.00% | 0.00% | 0.00% | `terminal_edge_non_negative` |
| `nasdaq_sp500_price_defensive` | 99 | 75.8% | fail | -1.87% | -0.01% | 5.40% | `terminal_underperformance_vs_fixed` |

The observed best smart candidate was `nasdaq_sp500_price_no_skip`, but it did
not pass the effect-size gate because the median terminal edge versus fixed DCA
was `0.00%`, below the required `1.00%`. The research decision therefore remains:

- `runtime_default_recommendation = fixed_dca`
- `smart_mode_enablement_status = not_recommended_for_enablement`
- `default_change_allowed_by_research = false`

This confirms the earlier summary with stronger evidence: a no-skip smart mode
can match fixed DCA under these frozen price-only rules, but it does not beat
fixed DCA enough to justify enabling smart sizing by default. The defensive
variant remains rejected because reducing/skipping expensive-regime buys creates
cash drag and underperforms fixed DCA in a material subset of scenarios.

## Next Checks

- Keep fixed DCA as the default Nasdaq/S&P mode.
- Treat `nasdaq_sp500_price_no_skip` only as the explicit smart-mode baseline for
  comparing external signals.
- Do not tune price-only thresholds further unless the candidate family is
  frozen before rerun; the current evidence does not justify more parameter
  search.
- If better market data becomes available, rerun with adjusted-close ETF or
  total-return proxies to quantify dividend/fee sensitivity separately from the
  FRED price-only result.
