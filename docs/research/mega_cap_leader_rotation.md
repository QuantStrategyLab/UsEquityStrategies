# mega_cap_leader_rotation research brief

Status: research/backtest only. This is not a live `StrategyCatalog` profile and should not be enabled in broker runtimes until a separate promotion review is done.

## Objective

`mega_cap_leader_rotation` tests whether a concentrated monthly rotation among US mega-cap leaders can keep exposure to the strongest large-cap growth names while dropping weaker leaders.

This is intentionally different from the existing live profiles:

- `russell_1000_multi_factor_defensive`: broad Russell 1000 stock selection.
- `tech_communication_pullback_enhancement`: tech/communication pullback entry with an explicit cash buffer.
- `mega_cap_leader_rotation`: a narrow leader-only pool, focused on relative strength among mega caps.

## First research scope

- Cadence: monthly.
- Input style: price-only research backtest.
- Default benchmark: `QQQ`; broad reference benchmark: `SPY`.
- Default safe haven: `BOXX` as a cash-like placeholder in research.
- First pools:
  - `mag7`: `AAPL`, `MSFT`, `NVDA`, `AMZN`, `GOOGL`, `META`, `TSLA`.
  - `expanded`: MAG7 plus `AVGO`, `NFLX`, `AMD`, `COST`, `JPM`, `BRK.B`, `LLY`.

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

- Select the top 3 names by default.
- Keep an existing holding if it remains inside `top_n + hold_buffer`.
- Default single-name cap: 35%.
- Market defense uses `QQQ` 200-day trend and mega-cap pool breadth.
- Unused allocation goes to `BOXX` in the research output.

## Current implementation location

The research backtest lives in `../UsEquitySnapshotPipelines`:

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

## Promotion criteria

Do not promote this into a live profile unless the research result shows:

1. better drawdown control than `QQQ` or equal-weight mega-cap references;
2. acceptable turnover after costs;
3. behavior that is not just a duplicate of `tech_communication_pullback_enhancement`;
4. no obvious single-period overfit, especially around one dominant stock cycle.
