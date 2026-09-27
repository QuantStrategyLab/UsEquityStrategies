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

## Later Phase 4 v3 joint-account risk readback

The separately reviewed QQQM/TQQQ/BOXX Phase 4 v3 account comparison now supplies six same-window daily ledgers. `phase4_joint_account_risk.py` reads them without a new strategy replay, verifies the frozen source summary SHA256 `32e812ee0f38461f74f10975d32003db496b81e9232be7bd5f6be6b8459c9793`, all six ledger hashes, daily NAV components, no external flow, terminal NAV, maximum drawdown and summed costs. It uses the same 444 dates and initial-NAV first return as above. The private aggregate output `phase4_fixed_pair_v3/phase4_joint_account_risk_summary.v1.json` has SHA256 `361e6160894cd462b75a0d9d853cfc35cf4fcf1dd0613c1e5fb9842f4a0292fa` and mode `0600` under the approved private research root; no daily ledger enters Git.

The table gives sample daily-return standard deviation times `sqrt(252)`, the signed mean of the worst 23/444 daily returns, and the worst **observed** compounded five-session return. Each path's worst five-session span was 2024-08-01 through 2024-08-07. Economic NAV already includes executed costs, so the risk reader does not deduct them again.

| Cost | Account path | Historical annualized volatility | Worst 23-day mean | Worst observed five-session return | Max drawdown |
|---:|---|---:|---:|---:|---:|
| 5 bps | Enhanced | 8.417% | −1.151% | −3.392% | −6.201% |
| 5 bps | Matched defense | 8.446% | −1.163% | −3.724% | −6.649% |
| 10 bps | Enhanced | 8.419% | −1.150% | −3.398% | −6.199% |
| 10 bps | Matched defense | 8.455% | −1.167% | −3.734% | −6.655% |
| 15 bps | Enhanced | 8.418% | −1.150% | −3.404% | −6.213% |
| 15 bps | Matched defense | 8.453% | −1.164% | −3.741% | −6.638% |

The paired enhanced/baseline daily **account-return** correlations are 0.998555, 0.998601 and 0.998584 at 5/10/15 bps. Both accounts hold overlapping assets, so these are not TQQQ-versus-SOXL member correlations or an estimate of diversification among independent strategies. The enhanced path's historical volatility, tail mean and five-session loss were slightly less severe in this window, consistent with its lower actual security exposure; none is an adoption argument. The 23-day mean is not exact-quantile or predictive CVaR, and the selected five-session span is not an unseen-crisis stress test. Astra returned GO for this narrow math and accounting interpretation after code review; it did not inspect the private values. This readback still does not establish a declared portfolio risk limit or original complete v2 risk.

## Same-window traded-asset correlation

`phase4_asset_correlation.py` separately reads the already approved raw bars and corporate actions through the existing manifest-verified reader, then aligns the 444 execution-window sessions to a SHA-checked Phase 4 ledger. The manifest SHA256 is `cb14a511083c824a748d137a271c93cfe0e8adf38f648905b26e37decf4c6182` and declares `price_adjustment=raw`. No TQQQ, QQQM or BOXX split takes effect in this window. A one-session retrospective economic holding return is `(raw close_t + per-share cash dividend with ex_date=t) / raw close_(t−1) − 1`; this avoids treating an ex-dividend raw price drop as the entire economic loss. The formula is not used at a historical signal. The private aggregate summary `phase4_fixed_pair_v3/phase4_asset_correlation_summary.v1.json` is mode `0600`, SHA256 `8e19a2484aa0f8beccde765cb05bef89e972711fd9a86a8db77fcd0e673383f5`.

| Historical daily-return correlation | QQQM | TQQQ | BOXX |
|---|---:|---:|---:|
| QQQM | 1.0000 | 0.9997 | 0.0056 |
| TQQQ | 0.9997 | 1.0000 | 0.0035 |
| BOXX | 0.0056 | 0.0035 | 1.0000 |

Observed negative one-session returns occurred on 185 QQQM days, 188 TQQQ days and 66 BOXX days; all three were negative on 22 of the 444 common sessions. The dividend event counts in the window were 7/7/1 for TQQQ/QQQM/BOXX. Astra reviewed the narrow price/dividend and time semantics and returned GO without inspecting the private values. The near-zero BOXX price-plus-dividend correlation does not make it Treasury cash or risk-free, and the QQQM/TQQQ correlation is between ETF holding returns, not between full strategy members with guard, budgets, fees and internal cash. The matrix describes this already-seen period only; it is neither a stable covariance forecast nor evidence from an unseen crisis. Historical provider processing dates do not prove past dividend announcement times, so these ex-date economic returns cannot be fed into earlier decisions as then-known signals.
