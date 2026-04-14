# mega_cap_leader_rotation research brief

Status: the static `mag7` / `expanded` variants remain research/backtest only. The historical dynamic top-20 variant is promoted as the selectable live profile `mega_cap_leader_rotation_dynamic_top20`.

## Objective

`mega_cap_leader_rotation` tests whether a concentrated monthly rotation among US mega-cap leaders can keep exposure to the strongest large-cap growth names while dropping weaker leaders.

This is intentionally different from the existing live profiles:

- `russell_1000_multi_factor_defensive`: broad Russell 1000 stock selection.
- `tech_communication_pullback_enhancement`: tech/communication pullback entry with an explicit cash buffer.
- `mega_cap_leader_rotation_dynamic_top20`: a narrow leader-only runtime profile, focused on relative strength among the historical top-20 mega-cap pool.

## Research and runtime scope

- Cadence: monthly.
- Input style: price-only research backtest.
- Default benchmark: `QQQ`; broad reference benchmark: `SPY`.
- Default safe haven: `BOXX` as a cash-like placeholder in research.
- First pools:
  - `mag7`: `AAPL`, `MSFT`, `NVDA`, `AMZN`, `GOOGL`, `META`, `TSLA`.
  - `expanded`: MAG7 plus `AVGO`, `NFLX`, `AMD`, `COST`, `JPM`, `BRK.B`, `LLY`.
  - `dynamic_top20`: rebuilds the mega-cap candidate pool each month from the
    top iShares Russell 1000 ETF holdings weights, reducing the "today's
    winners" look-ahead bias in pre-MAG7 periods. Known duplicate share classes
    such as `GOOG` / `GOOGL` are collapsed to one issuer before ranking.

## Initial signal design

The research script ranks eligible names using only price-derived features:

- 3-month momentum.
- 6-month momentum.
- 12-month momentum skipping the most recent month.
- 6-month relative momentum versus `QQQ`.
- 6-month relative momentum versus `SPY`.
- Distance from 252-day high.
- 200-day moving-average gap.
- 63-day volatility penalty.
- 126-day drawdown penalty.
- Small hold bonus to reduce monthly churn.

## Initial portfolio rules

- Research default started at top 3; the promoted dynamic top20 runtime default selects 4 names.
- Keep an existing holding if it remains inside `top_n + hold_buffer`.
- Static research default single-name cap: 35%; promoted dynamic top20 runtime default single-name cap: 25%.
- Optional account-size guard: set `--portfolio-total-equity` plus
  `--min-position-value-usd` to lower the effective top-N when a small account
  cannot support the requested number of minimum-sized stock positions.
- Market defense uses `QQQ` 200-day trend. The promoted dynamic top20 default uses a simple QQQ 200-day filter: full stock exposure when QQQ is above trend, 50% stock exposure when QQQ is below trend.
- Unused allocation goes to `BOXX` in the research output.

## Current implementation location

The live strategy module is `src/us_equity_strategies/strategies/mega_cap_leader_rotation_dynamic_top20.py`. The research backtest lives in `../UsEquitySnapshotPipelines`:

```bash
cd ../UsEquitySnapshotPipelines
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src \
python scripts/backtest_mega_cap_leader_rotation.py \
  --download \
  --pool expanded \
  --price-start 2015-01-01 \
  --start 2016-01-01 \
  --turnover-cost-bps 5 \
  --output-dir data/output/mega_cap_leader_rotation_backtest
```

Historical dynamic-universe check. The documented start uses the earliest
monthly iShares JSON snapshot range that resolved reliably in research:

```bash
cd ../UsEquitySnapshotPipelines
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src \
python scripts/backtest_mega_cap_leader_rotation.py \
  --download \
  --dynamic-universe \
  --universe-start 2017-09-01 \
  --price-start 2015-01-01 \
  --start 2017-10-01 \
  --mega-universe-size 20 \
  --top-n 4 \
  --single-name-cap 0.25 \
  --turnover-cost-bps 5 \
  --output-dir data/output/mega_cap_leader_rotation_dynamic_universe_top20_backtest
```

Expected research outputs:

- `summary.csv`
- `portfolio_returns.csv`
- `weights_history.csv`
- `turnover_history.csv`
- `candidate_scores.csv`
- `trades.csv`
- `exposure_history.csv`
- `reference_returns.csv`

For the default robustness matrix across `mag7` / `expanded`, top 3 / 4 / 5,
single-name caps 25% / 30% / 35%, and defense on / off:

```bash
cd ../UsEquitySnapshotPipelines
PYTHONPATH=src:../UsEquityStrategies/src:../QuantPlatformKit/src \
python scripts/backtest_mega_cap_leader_rotation_robustness.py \
  --download \
  --price-start 2015-01-01 \
  --start 2016-01-01 \
  --turnover-cost-bps 5 \
  --output-dir data/output/mega_cap_leader_rotation_robustness
```

The robustness command writes:

- `robustness_summary.csv`
- `robustness_summary_by_run.csv`

## Runtime profile

`mega_cap_leader_rotation_dynamic_top20` is the promoted selectable profile. Defaults:

- feature snapshot input: `mega_cap_leader_rotation_dynamic_top20.feature_snapshot.v1`;
- dynamic universe size: 20;
- selected holdings: 4;
- single-name cap: 25%;
- safe haven: `BOXX`;
- benchmark trend filter: `QQQ` 200-day SMA;
- stock exposure: 100% when QQQ is above trend, 50% when QQQ is below trend;
- execution window: first 3 NYSE trading days after the monthly snapshot date.

Remaining risk: this is still a simple price-only model. It is meant as a small parallel paper/live sleeve, not a replacement for broader allocation strategies.
