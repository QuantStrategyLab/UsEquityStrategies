# mega_cap_leader_rotation research brief

Status: the static `mag7` / `expanded` variants remain research/backtest only.
`russell_top50_leader_rotation` is the retained runtime-enabled
Top50 candidate. Narrow Top20 and aggressive Top50 profile exposure was removed
after comparison against the balanced Top50 route.

## Objective

`mega_cap_leader_rotation` tests whether a concentrated monthly rotation among
US Russell Top50 leaders can keep exposure to the strongest large-cap growth names
while dropping weaker leaders.

This is intentionally different from the other runtime profiles:

- `russell_1000_multi_factor_defensive`: broad Russell 1000 stock selection.
- `tech_communication_pullback_enhancement`: tech/communication pullback entry
  with an explicit cash buffer.
- `russell_top50_leader_rotation`: the current runtime-enabled Top50
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
  - dynamic Russell Top50 pool: rebuilt from historical iShares Russell 1000 ETF
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

`russell_top50_leader_rotation` keeps the Top50 candidate universe and
uses a fixed sleeve blend:

- 50% sleeve: top 2 names, 50% single-name cap.
- 50% sleeve: top 4 names, 25% single-name cap.
- safe haven: `BOXX`.
- feature snapshot input:
  `russell_top50_leader_rotation.feature_snapshot.v1`.
- historical execution window: first 3 NYSE trading days after the monthly
  snapshot date.

This retained route had the better promoted comparison result:

- Top50 balanced `blend_top2_50_top4_50`: CAGR 36.41%, max drawdown -30.56%.
- Dynamic Top20: CAGR 21.51%, max drawdown -23.14%.
- Top50 aggressive `top3_cap35_no_defense`: CAGR 32.42%, max drawdown -28.64%.

The narrower Top20 and aggressive Top50 profile names are no longer valid
runtime or replay profile surfaces. The shared implementation helper lives at
`src/us_equity_strategies/strategies/mega_cap_leader_rotation.py`.

Runtime can also select one of the named, deterministic variants through
`leader_rotation_profile_variant`:

- `top4_baseline`: no Top2 sleeve; top 4 names with a 25% single-name cap.
- `blend_top2_25_top4_75`: conservative live-design blend.
- `blend_top2_50_top4_50`: balanced offensive blend and current default shape.

The lower-level `blend_sleeves` override remains available for research or
custom replay, but production promotion should prefer the named variants above
so diagnostics and live-readiness evidence stay comparable.

For live observation, `leader_rotation_shadow_variants` can be enabled to emit
diagnostic-only target weights for the named variants. Shadow variants never
change the returned target positions; they are intended for comparing the
current balanced shape against the conservative and Top4 rollback alternatives.
Each shadow variant also reports `weight_delta_vs_active`, so operators can see
which target weights would increase or decrease versus the active runtime
variant. The diagnostics additionally summarize the largest single-name increase
and decrease to make monthly review faster. `turnover_delta_vs_active` estimates
the one-way target-weight turnover needed to switch from the active variant to
the shadow variant.

For artifact consumers, `leader_rotation_shadow_review_rows` provides a compact
tuple of row-shaped dictionaries. The companion
`leader_rotation_shadow_review_schema_version` is currently
`russell_top50_shadow_review.v1`. Row field order is defined by
`SHADOW_REVIEW_ROW_FIELDS`, and the same order is emitted in
`leader_rotation_shadow_review_row_fields` for downstream consumers. Current
fields:

- `schema_version`
- `active_variant`
- `shadow_variant`
- `selected_count`
- `realized_stock_weight`
- `safe_haven_weight`
- `turnover_delta_vs_active`
- `largest_increase_symbol`
- `largest_increase_delta`
- `largest_decrease_symbol`
- `largest_decrease_delta`
- `review_note`

`review_note` is a deterministic one-line summary for human review. It includes
the active variant, shadow variant, one-way turnover delta, and largest
single-name increase/decrease. It must not include account identifiers,
positions by account, tokens, or other sensitive runtime fields.

Example runtime review config:

```python
{
    "leader_rotation_profile_variant": "blend_top2_50_top4_50",
    "leader_rotation_shadow_variants": True,
}
```

To review a conservative override without changing the available rollback path,
set `leader_rotation_profile_variant` to `blend_top2_25_top4_75` and keep
`leader_rotation_shadow_variants` enabled. To roll back, set
`leader_rotation_profile_variant` to `top4_baseline`.

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
