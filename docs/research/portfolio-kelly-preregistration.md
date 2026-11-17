# RS-06 Portfolio Fractional Kelly Walk-Forward Study: Preregistration (rs06-kelly-portfolio-v1)

English | [简体中文](portfolio-kelly-preregistration.zh-CN.md)

> Research only. Frozen before computing any RS-06 figures. All results are `DEVELOPMENT_ONLY`, **do not raise any budget**, and make no production changes; SOXL still requires 252 shadow sessions first. Code mirror: `src/us_equity_strategies/research/portfolio_kelly_preregistration.py`.

## 1. Purpose

Single-strategy Kelly (RS-04e) examines each stream's mean and variance in isolation. Long-horizon compounding optimization depends on both Sharpe ratios and cross-strategy correlations: this study places both strategy return streams in a joint portfolio, solves for Kelly weights from training-fold mean and covariance estimates, and tests whether it achieves higher log growth than single-strategy Kelly and fixed allocations.

## 2. Assets and Data

| Item | Frozen Value |
|---|---|
| Assets | SOXL core (`rs04-kelly-soxl-v1`, `no_plugin_core`) and TQQQ core (`rs04-kelly-tqqq-v1`, `no_plugin_core`), both full-exposure daily return streams with inner turnover from RS-04e |
| Cash | BOXX total return (same data source) |
| Data | `alpaca_sip_raw_bars_plus_corporate_actions_box_fetch_20261011`, manifest SHA-256 `b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482`, retrieved 2026-10-11 10:07 (CST), user-attested non-commercial research license |
| Window | 2023-03-28 through 2026-08-25, XNYS |
| Timing | Signals use complete post-close data on day `t` and fill at `t+1` close (`next_close`), matching RS-04e |

Both component preregistrations must be confirmed for `no_plugin_core` with identical manifests and windows, or execution is rejected.

## 3. Walk-Forward and Estimation (Training Data Only Per Fold)

- Walk-forward: 252 training / 63 test / 5 purge trading sessions, rolling/non-anchored (matching RS-04e so fold boundaries align).
- Mean: daily excess returns over cash per asset, shrunk toward zero using RS-04's positive-part shrinkage `max(0, 1 - 1/t^2)`.
- Covariance: Ledoit-Wolf (2004) linear shrinkage of excess returns toward a scaled identity target with analytical shrinkage intensity, demeaned and normalized by `1/n` (matching `sklearn.covariance.ledoit_wolf` defaults).
- If training samples `< 30`, any asset has zero variance, or the covariance matrix is non-invertible, the fold parks entirely in cash.
- Kelly: `f* = Sigma^-1 mu`, scaled by `c` in `{0.25, 0.5}`; full Kelly is disallowed.
- Constraints: long-only, unleveraged (`w_i >= 0`, `sum(w_i) <= 1`), remainder in cash. Both solver methods are preregistered:
  - `qp` (primary): maximize `w^T mu - w^T Sigma w / (2c)` under the constraints via exact KKT active-set enumeration; equals `c * Sigma^-1 mu` when unconstrained.
  - `clip_scale` (literal truncation): compute `w = c * Sigma^-1 mu`, zero negative entries, and proportionally scale down if sum exceeds 1.
- Weights stay constant within each test fold and rebalance to target at the start of each session.

## 4. Benchmarks (Same Test Window and Cost Tiers)

Single-strategy Kelly for SOXL and TQQQ at `c = 0.25` and `0.5` (recomputed on the same folds per RS-04, matching RS-04e); 50/50 equal weight; 25/25 equal weight (50% scaled); fixed 50% SOXL and fixed 50% TQQQ; full-exposure SOXL and full-exposure TQQQ; buy-and-hold SOXX and QQQ benchmarks; and daily-rebalanced 50/50 SOXX/QQQ.

## 5. Costs and Metrics

- 5 bps one-way on traded notional, stressed at 1x/2x/3x; outer cash leg pays zero fees; asset inner turnover enters weighted by portfolio weight.
- Metrics: per-fold weights, training correlation matrices (sample and shrunk), per-strategy training annualized Sharpe, full-test-window correlation matrix; per-scheme mean daily log growth, max drawdown, daily CVaR95, gross excess PSR; DSR uses 1x net excess across all 14 allocation schemes (4 portfolio Kelly + 4 single-strategy Kelly + 2 equal-weight + 2 fixed-50% + 2 full-exposure), excluding benchmarks.
- No scheme selection; outputs serve strictly as exposure-cap hints and never raise budgets.

## 6. Known Limitations

`no_plugin_core` differs from live (all plugins off); data are retroactively fetched vendor data rather than strict point-in-time; the entire history has already been seen and serves as development reference only; fractional weights omit whole-share rounding and settlement.

## 7. Implementation (Second RS-06 PR)

- Evaluator: `research/portfolio_kelly_walk_forward.py` (Ledoit-Wolf bit-checked against `sklearn.covariance.ledoit_wolf`; `qp` checked against brute-force grid).
- Private runner: `research/portfolio_kelly_private_runner.py`, reusing RS-04 archive validation and `no_plugin_core` generators; forbidden in CI, writing only to `0700`/`0600` private paths.
- Single-strategy Kelly benchmarks match the RS-04 evaluator on identical data (covered by tests).

## 8. RS-07 Revision: rs06-kelly-portfolio-v2 (New Trial)

Components switch to `rs04-kelly-soxl-v2` and `rs04-kelly-tqqq-v2` (`core_v2_parity`, see the RS-07 section of the single-strategy preregistration doc); all other frozen parameters stay unchanged.
