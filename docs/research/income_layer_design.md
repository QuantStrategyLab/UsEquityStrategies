# Income Layer Design Conclusions


## 中文摘要

- 完整中文版见 [`income_layer_design.zh-CN.md`](income_layer_design.zh-CN.md)；本节保留在英文文件顶部，方便从当前文件直接找到中文入口。
- 用途：本文档围绕 `Income Layer Design Conclusions`，用于理解 `UsEquityStrategies` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Decision`、`Design Rules`、`Leveraged Profile Review`、`Core Default Review`、`Reading the Defaults`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
_Updated: 2026-06-04_

## Decision

The income layer does not use one global `1000000 USD` activation threshold. `1000000 USD` is only the large-account calibration scenario used to verify that combined drawdown can stay inside the SPY / QQQ reference windows. Runtime defaults are set per strategy.

Current defaults:

| Profile | Mode | Start | Activation band | Hard cap | Default income basket |
| --- | --- | ---: | ---: | ---: | --- |
| `tqqq_growth_income` | `log_total_drawdown_budget` | `250000` | `20%` | `55%` | `SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%` |
| `soxl_soxx_trend_income` | `log_total_drawdown_budget` | `150000` | `20%` | `95%` | `SCHD 15% / DGRO 10% / SGOV 70% / SPYI 4% / QQQI 1%` |
| `global_etf_rotation` | `log_total_drawdown_budget` | `500000` | `10%` | `15%` | `SCHD 40% / DGRO 25% / SGOV 30% / SPYI 5%` |
| `russell_1000_multi_factor_defensive` | `log_total_drawdown_budget` | `400000` | `10%` | `20%` | `SCHD 45% / DGRO 30% / SGOV 25%` |
| `mega_cap_leader_rotation_top50_balanced` | `log_total_drawdown_budget` | `300000` | `15%` | `25%` | `SCHD 45% / DGRO 30% / SGOV 25%` |

`tech_communication_pullback_enhancement` is removed from runtime exposure. Its
strategy implementation and bundled config remain only as archived research, so
it has no current income-layer default.

Activation and near-cap visualization:
[`income_layer_activation_drawdown_2026-05-26.svg`](./income_layer_activation_drawdown_2026-05-26.svg).

## Design Rules

- Defaults use `log_total_drawdown_budget`: first set a target account-level drawdown budget from account size, then reverse it into an income-layer ratio from core-strategy and income-basket stress drawdown assumptions.
- The income layer is enabled by default; set `income_layer_enabled = false` to disable it.
- Leveraged profiles start from about a `45%` small-account stress budget, then tighten by account doubling toward about `30%`, and continue toward about `25%` for larger accounts.
- Non-leveraged profiles use a softer account-level budget curve so the income layer does not rewrite the core strategy too early.
- `income_layer_start_usd` is strategy-specific. Leveraged strategies start earlier; non-leveraged strategies generally start later or use a smaller cap.
- `income_layer_activation_band_ratio` prevents threshold churn. The normal target ratio is multiplied from 0 to 1 between `start` and `start * (1 + band)`.
- `income_layer_max_ratio` is a risk-budget parameter, not a pure return-maximization parameter. Raising the cap usually reduces drawdown and long-run CAGR.
- Existing income holdings are locked with `max(current_income_layer_value, desired_income_layer_value)`, so the layer adds capital by default instead of force-selling income assets down.

## Account-Budget Parameter Design

`base_drawdown_budget` equals each strategy's estimated core stress drawdown, so the target income-layer ratio starts continuously from 0 just above `start`. The budget then tightens smoothly by `drawdown_budget_decay_per_double * log2(nav / start)`, with `min_drawdown_budget` as the large-account floor. The income ratio is reversed from:

`income_ratio = (core_stress_drawdown - account_budget) / (core_stress_drawdown - income_stress_drawdown)`

| Profile | Role | Core stress drawdown | Income stress drawdown | Account budget curve | Income cap | Rationale |
| --- | --- | ---: | ---: | --- | ---: | --- |
| `tqqq_growth_income` | Broad-market leveraged growth | `45%` | `8%` | Starts at `45%`, tightens `5%` per double, floor `25%` | `55%` | Small accounts can accept near-core volatility; around `500k` the budget tightens near `40%`, around `2M` near `30%`, while preserving compounding. |
| `soxl_soxx_trend_income` | Semiconductor leveraged trend | `45%` | `6%` | Starts at `45%`, tightens `5%` per double, floor `25%` | `95%` | SOXL has sharper path risk and a more SGOV-heavy income basket, so the cap is high enough to satisfy large-account budgets. |
| `global_etf_rotation` | Defensive ETF rotation | `30%` | `8%` | Starts at `30%`, tightens `1.5%` per double, floor `26.7%` | `15%` | The core already has canary/BIL defense; the floor matches the drawdown achievable with the 15% income cap. |
| `russell_1000_multi_factor_defensive` | Defensive multi-factor stocks | `30%` | `8%` | Starts at `30%`, tightens `1.5%` per double, floor `25.6%` | `20%` | Single-stock equity risk is higher than Global ETF, so the cap and achievable floor are slightly more defensive. |
| `mega_cap_leader_rotation_top50_balanced` | Concentrated leader rotation | `35%` | `8%` | Starts at `35%`, tightens `2%` per double, floor `28.25%` | `25%` | Top2/Top4 concentration needs a Tech-like budget curve, but the cap is lower to avoid muting strong trend capture too much. |

## Leveraged Profile Review

Research output:

`UsEquitySnapshotPipelines/data/output/levered_income_layer_candidate_compare_2026-05-26/`

Selection rules:

- `1000000 USD` initial equity is a large-account calibration case, not the activation threshold.
- Candidates must pass all SPY and QQQ standard-window drawdown constraints.
- TQQQ additionally uses a roughly `15%` max drawdown constraint at `1000000 USD`, matching the "at most 150k loss on a 1M account" budget.
- Among candidates that pass, rank by CAGR; if return is close, prefer the simpler path closest to the current default core.

Defaults selected on 2026-05-26 are archived below. The 2026-06-04 defaults now use account-level `log_total_drawdown_budget`; the current-default table above is authoritative:

| Strategy | Version | CAGR | Max drawdown | SPY windows | QQQ windows | Avg income ratio | End income ratio | Decision |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| `tqqq_growth_income` | `start=250000, max=50%, current_tqqq basket` | `30.54%` | `-14.87%` | pass | pass | `39.03%` | `41.01%` | selected |
| `tqqq_growth_income` | `start=500000, max=60%, current_tqqq basket` | `31.21%` | `-15.93%` | pass | pass | `36.25%` | `42.34%` | rejected: exceeds 15% large-account loss budget |
| `tqqq_growth_income` | previous default `start=150000, max=50%` | `29.21%` | `-14.24%` | pass | pass | `42.63%` | `43.86%` | replaced: lower return and earlier small-account income drag |
| `soxl_soxx_trend_income` | `start=250000, max=95%, balanced_income basket` | `36.14%` | `-9.04%` | pass | pass | `76.02%` | `82.57%` | selected |
| `soxl_soxx_trend_income` | previous default `start=150000, max=90%, current_soxl basket` | `32.16%` | `-7.70%` | pass | pass | `78.52%` | `82.45%` | replaced: materially lower return |

SOXL core overlay review:

| Core version | CAGR | Max drawdown | Note |
| --- | ---: | ---: | --- |
| current manifest: `SOXX 10d vol >= 55%, SOXL -> SOXX` | `49.74%` | `-42.31%` | kept; best combined result after income layer |
| `SOXX 10d vol >= 55%, SOXL -> BOXX` | `49.84%` | `-42.31%` | core-only CAGR is slightly higher, but combined income-layer result is worse |
| `SOXX 10d vol >= 50%, SOXL -> SOXX` | `48.48%` | `-42.31%` | more frequent de-levering lowers return |

Therefore the SOXL core `blend_gate_volatility_delever_*` defaults stay unchanged; only the income-layer defaults changed.

A lightweight 2026-06-04 refresh using Nasdaq real history and official yield proxies moved the SOXL income layer to the earlier, more SGOV-heavy `start=150000, max=95%, log_factor=0.50` version. In that sample it produced about `38.73%` CAGR and `-9.28%` max drawdown while still passing the SPY drawdown-window constraint.

## Core Default Review

Follow-up research on 2026-05-26/27 retested the leveraged cores after the
income-layer defaults were selected. The review intentionally stayed narrow:

- TQQQ mix variants around the default `45% QQQ / 45% TQQQ / 8% BOXX / 2% cash`
  active mix.
- SOXL/SOXX mix variants around the default `70% SOXL / 20% SOXX` full tier,
  `65% SOXL / 20% SOXX` mid tier, and `15% SOXX` defensive tier.
- Volume-pressure overlays that only redirected leveraged exposure into the
  matching unlevered ETF (`TQQQ -> QQQ`, `SOXL -> SOXX`).

No candidate passed the no-regression rule across real-product and long
synthetic stress windows. Softer mixes reduced drawdown only by giving up CAGR;
higher-return mixes worsened drawdown. The best-looking SOXL volume overlay
improved full-sample CAGR and drawdown, but lagged the 2024-2026 rebound window
by more than 11 pp of CAGR, so volume remains a shadow/notification signal.

Decision: keep the default TQQQ and SOXL/SOXX cores unchanged. Do not change
`dual_drive_*`, `blend_gate_*`, or add volume-based executable overlays from
this review.

## Reading the Defaults

- Small-account mode does not require combined drawdown to beat the broad market. The account is still mainly in growth-compounding mode until it crosses the threshold.
- At `1000000 USD`, the income layer must pull TQQQ and SOXL combined drawdown inside the SPY / QQQ reference windows.
- TQQQ moved from `150000` to `250000` start to reduce early income drag while keeping about `-14.87%` drawdown in the `1000000 USD` calibration.
- SOXL first moved from `150000 / 90% / current_soxl` to `250000 / 95% / balanced_income`; the 2026-06-04 refresh then moved it to `150000 / 95% / SGOV-heavy`, using the higher SGOV weight to offset the earlier activation's return drag.
