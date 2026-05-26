# Income Layer Design Conclusions

_Updated: 2026-05-26_

## Decision

The income layer does not use one global `1000000 USD` activation threshold. `1000000 USD` is only the large-account calibration scenario used to verify that combined drawdown can stay inside the SPY / QQQ reference windows. Runtime defaults are set per strategy.

Current defaults:

| Profile | Mode | Start | Activation band | Hard cap | Default income basket |
| --- | --- | ---: | ---: | ---: | --- |
| `tqqq_growth_income` | `log_cap` | `250000` | `20%` | `50%` | `SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%` |
| `soxl_soxx_trend_income` | `log_cap` | `250000` | `20%` | `95%` | `SCHD 25% / DGRO 15% / SGOV 55% / SPYI 4% / QQQI 1%` |
| `global_etf_rotation` | `log_loss_budget` | `500000` | `10%` | `15%` | `SCHD 40% / DGRO 25% / SGOV 30% / SPYI 5%` |
| `russell_1000_multi_factor_defensive` | `log_loss_budget` | `400000` | `10%` | `20%` | `SCHD 45% / DGRO 30% / SGOV 25%` |
| `tech_communication_pullback_enhancement` | `log_loss_budget` | `250000` | `15%` | `30%` | `SCHD 40% / DGRO 25% / SGOV 20% / SPYI 10% / QQQI 5%` |
| `mega_cap_leader_rotation_top50_balanced` | `log_loss_budget` | `300000` | `15%` | `25%` | `SCHD 45% / DGRO 30% / SGOV 20% / SPYI 5%` |

Activation and near-cap visualization:
[`income_layer_activation_drawdown_2026-05-26.svg`](./income_layer_activation_drawdown_2026-05-26.svg).

## Design Rules

- Leveraged profiles use `log_cap`: the account-level goal is to keep combined drawdown near or inside SPY / QQQ while preserving compound growth.
- Non-leveraged profiles use `log_loss_budget`: the goal is to damp account volatility as account size grows, not to materially rewrite the core strategy.
- `income_layer_start_usd` is strategy-specific. Leveraged strategies start earlier; non-leveraged strategies generally start later or use a smaller cap.
- `income_layer_activation_band_ratio` prevents threshold churn. The normal target ratio is multiplied from 0 to 1 between `start` and `start * (1 + band)`.
- `income_layer_max_ratio` is a risk-budget parameter, not a pure return-maximization parameter. Raising the cap usually reduces drawdown and long-run CAGR.
- Existing income holdings are locked with `max(current_income_layer_value, desired_income_layer_value)`, so the layer adds capital by default instead of force-selling income assets down.

## Leveraged Profile Review

Research output:

`UsEquitySnapshotPipelines/data/output/levered_income_layer_candidate_compare_2026-05-26/`

Selection rules:

- `1000000 USD` initial equity is a large-account calibration case, not the activation threshold.
- Candidates must pass all SPY and QQQ standard-window drawdown constraints.
- TQQQ additionally uses a roughly `15%` max drawdown constraint at `1000000 USD`, matching the "at most 150k loss on a 1M account" budget.
- Among candidates that pass, rank by CAGR; if return is close, prefer the simpler path closest to the current production core.

Final selected defaults:

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

## Reading the Defaults

- Small-account mode does not require combined drawdown to beat the broad market. The account is still mainly in growth-compounding mode until it crosses the threshold.
- At `1000000 USD`, the income layer must pull TQQQ and SOXL combined drawdown inside the SPY / QQQ reference windows.
- TQQQ moved from `150000` to `250000` start to reduce early income drag while keeping about `-14.87%` drawdown in the `1000000 USD` calibration.
- SOXL moved from `150000 / 90% / current_soxl` to `250000 / 95% / balanced_income`; in the `1000000 USD` calibration, CAGR improved from about `32.16%` to `36.14%`, while max drawdown widened from about `-7.70%` to `-9.04%` and stayed well inside the loss budget.
