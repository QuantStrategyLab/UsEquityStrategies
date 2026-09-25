# Phase 3: Descriptive Risk of the Frozen BOXX Account Paths

## Scope and source binding

`boxx_outer_cash_risk.py` derives risk statistics from the 18 previously saved daily account ledgers for the independent TQQQ/QQQ core, its cash/BOXX outer policies, and the two buy-and-hold references. It does not run a new strategy experiment or choose a budget. The existing private summary binds the manifest, policy and each ledger by SHA-256; the reader checks those hashes, the common 444 dates, daily account identity where the export contains all components, and each path's frozen terminal NAV, maximum drawdown and directly summed trading cost. The result is written only to the approved private research directory.

The fixed window is 2023-03-28 through 2024-12-31, with the initial account NAV preceding the first close. All results remain seen **development** evidence. The policy has no external account flows. Member transfers are internal; an external-flow field with nonzero value causes the risk reader to reject the ledger.

## Metric contract

- Daily account return is `NAV_close,t / NAV_close,t-1 - 1`; the first day uses the frozen initial NAV. Missing dates, nonpositive NAV, nonfinite values, or failed account reconciliation are rejected rather than filled or deleted.
- Historical daily-return volatility is sample standard deviation (`n-1`) times `sqrt(252)`. It is not forecast volatility.
- The reported signed tail mean averages the worst `ceil(0.05*n)` daily returns. At `n=444`, that is 23 days, an actual tail fraction of 5.18%, so it is not an exact-quantile or predictive CVaR.
- Maximum drawdown is the most negative close NAV relative to the running peak that includes initial NAV. It must equal the pre-existing summary; cost is summed from explicit ledger cost fields rather than inferred from NAV.
- Paired deltas use BOXX path minus cash path under the same budget and cost setting. A lower-return day counts only when the BOXX path's daily account return is strictly lower. These differences include TQQQ budget and state feedback and do not isolate a pure BOXX price effect.

## Observed risk, same window

Percentages below use daily account returns. Tail mean and maximum drawdown retain their negative-return signs.

| Cost | Path | Annualized historical volatility | Worst 23-day return mean | Maximum drawdown |
| ---: | --- | ---: | ---: | ---: |
| 5 bps | Fixed + cash | 9.985% | -1.446% | -9.946% |
| 5 bps | Fixed + BOXX | 10.007% | -1.440% | -9.329% |
| 5 bps | Automatic + cash | 7.616% | -1.166% | -6.187% |
| 5 bps | Automatic + BOXX | 7.041% | -1.101% | -5.632% |
| 10 bps | Fixed + cash | 9.988% | -1.448% | -10.077% |
| 10 bps | Fixed + BOXX | 9.995% | -1.438% | -9.442% |
| 10 bps | Automatic + cash | 7.436% | -1.117% | -5.011% |
| 10 bps | Automatic + BOXX | 5.503% | -0.857% | -6.566% |
| 15 bps | Fixed + cash | 9.993% | -1.450% | -10.193% |
| 15 bps | Fixed + BOXX | 10.000% | -1.443% | -9.555% |
| 15 bps | Automatic + cash | 7.156% | -1.092% | -5.068% |
| 15 bps | Automatic + BOXX | 6.686% | -1.024% | -6.713% |

| Cost | Reference | Annualized historical volatility | Worst 23-day return mean | Maximum drawdown |
| ---: | --- | ---: | ---: | ---: |
| 5 bps | QQQM buy and hold | 16.987% | -2.355% | -13.366% |
| 5 bps | BOXX buy and hold | 0.397% | -0.033% | -0.115% |
| 10 bps | QQQM buy and hold | 16.994% | -2.356% | -13.370% |
| 10 bps | BOXX buy and hold | 0.404% | -0.036% | -0.115% |
| 15 bps | QQQM buy and hold | 17.001% | -2.356% | -13.374% |
| 15 bps | BOXX buy and hold | 0.413% | -0.038% | -0.159% |

The fixed BOXX path's maximum drawdown is 0.617, 0.635 and 0.638 percentage points less severe than fixed cash at 5, 10 and 15 bps, respectively, while historical volatility is slightly higher by 0.021, 0.006 and 0.007 points. The automatic BOXX path has a *more* severe maximum drawdown than automatic cash at 10 and 15 bps, despite lower historical volatility and a less negative daily tail mean. These metrics therefore do not justify adopting the automatic policy or ranking policies by a single risk number. BOXX returned a lower daily account return than cash on 89/83/92 fixed-budget days and 110/121/122 automatic-budget days at 5/10/15 bps; this count does not measure the size or statistical significance of those differences.

## Evidence limits and next dependency

Fifteen exported paths permit independent reconstruction of NAV from listed securities, settled cash, unsettled sales and receivables. The three frozen QQQM reference ledgers omit QQQM position value from their exported component fields; their NAV, dates, source hash, costs and producer identity flag can be read and checked against the frozen summary, but an independent NAV component reconciliation is **not** available from those exports. The risk output marks those three paths accordingly. This limitation does not affect the four cash/BOXX policy paths.

This is one TQQQ strategy member plus an outer cash-management asset. It cannot establish TQQQ-versus-SOXL strategy correlation, multi-member risk contribution, unseen-crisis stress performance, or original full-v2 risk. It does not introduce a portfolio risk limit, forecast CVaR, candidate promotion or execution permission. The next joint-member risk analysis depends on qualified same-date member histories, matched state and cost contracts, and a common-account replay.
