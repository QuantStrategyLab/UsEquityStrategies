# mega_cap_leader_rotation research brief

Status: the static `mag7` / `expanded` variants remain research/backtest only.
`mega_cap_leader_rotation_top50_balanced` is the retained runtime-enabled
Top50 candidate. Narrow Top20 and aggressive Top50 profile exposure was removed
after comparison against the balanced Top50 route.

## Objective

`mega_cap_leader_rotation` tests whether a concentrated monthly rotation among
US mega-cap leaders can keep exposure to the strongest large-cap growth names
while dropping weaker leaders.

This is intentionally different from the other runtime profiles:

- `russell_1000_multi_factor_defensive`: broad Russell 1000 stock selection.
- `tech_communication_pullback_enhancement`: tech/communication pullback entry
  with an explicit cash buffer.
- `mega_cap_leader_rotation_top50_balanced`: the current runtime-enabled Top50
  leader-rotation candidate.

## Research And Runtime Scope

- Cadence: monthly.
- Input style: price-only research backtest, promoted through feature snapshots
  when used at runtime.
- Default benchmark: `QQQ`; broad reference benchmark: `SPY`.
- Default safe haven: `BOXX` as a cash-like placeholder in research.
- Research pools:
  - `mag7`: `AAPL`, `MSFT`, `NVDA`, `AMZN`, `GOOGL`, `META`, `TSLA`.
  - `expanded`: MAG7 plus `AVGO`, `NFLX`, `AMD`, `COST`, `JPM`, `BRK.B`, `LLY`.
  - dynamic mega-cap pool: rebuilt from historical iShares Russell 1000 ETF
    holdings snapshots to reduce "today's winners" look-ahead bias.

## Signal Design

The research script ranks eligible names using price-derived features:

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

## Retained Runtime Shape

`mega_cap_leader_rotation_top50_balanced` keeps the Top50 candidate universe and
uses a fixed sleeve blend:

- 50% sleeve: top 2 names, 50% single-name cap.
- 50% sleeve: top 4 names, 25% single-name cap.
- safe haven: `BOXX`.
- feature snapshot input:
  `mega_cap_leader_rotation_top50_balanced.feature_snapshot.v1`.
- historical execution window: first 3 NYSE trading days after the monthly
  snapshot date.

This retained route had the better promoted comparison result:

- Top50 balanced `blend_top2_50_top4_50`: CAGR 36.41%, max drawdown -30.56%.
- Dynamic Top20: CAGR 21.51%, max drawdown -23.14%.
- Top50 aggressive `top3_cap35_no_defense`: CAGR 32.42%, max drawdown -28.64%.

The narrower Top20 and aggressive Top50 profile names are no longer valid
runtime or replay profile surfaces. The shared implementation helper lives at
`src/us_equity_strategies/strategies/mega_cap_leader_rotation.py`.

## Research Commands

Historical dynamic-universe check:

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

Current Top50 concentration validation evidence lives in
`../UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_top50_concentration_variants/`.

Expected research outputs:

- `summary.csv`
- `portfolio_returns.csv`
- `weights_history.csv`
- `turnover_history.csv`
- `candidate_scores.csv`
- `trades.csv`
- `exposure_history.csv`
- `reference_returns.csv`

## Remaining Risk

This is still a simple price-only model. The retained Top50 balanced route
should remain in paper/shadow observation until snapshot freshness, integer
share drift, turnover, notifications, and account-size behavior are reviewed
against live-like data.
