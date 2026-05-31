# Global ETF Confidence Vol Gate Research


## 中文摘要

- 用途：本文档围绕 `Global ETF Confidence Vol Gate Research`，用于理解 `UsEquityStrategies` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Candidate`、`Production-Like Backtest Snapshot`、`Interpretation`、`Rollout Recommendation`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
_Updated: 2026-05-08_

## Candidate

`global_etf_confidence_vol_gate` is the legacy comparison alias for the same signal package now retained by `global_etf_rotation`.

- Universe, canary basket, safe haven, quarterly cadence, 13612W momentum, and hold bonus stay aligned with `global_etf_rotation`.
- The variant uses `sma_period=250`.
- It starts from the Top2 selection and normally stays equal-weight `50 / 50`.
- It shifts to `75 / 25` Top1/Top2 only when:
  - Top1 momentum z-gap versus Top2 is at least `1.0`.
  - Top1 trailing 126-trading-day annualized volatility is no more than `1.3x` Top2 volatility.

## Production-Like Backtest Snapshot

The research run used daily close history through 2026-05-07, quarterly rebalances, daily canary checks, and 5 bps turnover cost. The comparison below uses the same SMA250 baseline as the candidate and matches the current default `global_etf_rotation` defaults.

| Strategy | Sample | CAGR | Max drawdown | Volatility | Sharpe | Final equity |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Top2 SMA250 baseline | 2015-01-05 to 2026-05-06 | 13.60% | -23.35% | 19.21% | 0.762 | 4.242 |
| Ungated confidence 75/25 | 2015-01-05 to 2026-05-06 | 14.43% | -28.98% | 21.55% | 0.737 | 4.605 |
| Confidence + relative volatility gate | 2015-01-05 to 2026-05-06 | 14.77% | -23.35% | 19.59% | 0.803 | 4.763 |

## Interpretation

The ungated confidence rule improved CAGR but widened drawdown. The relative volatility gate filtered several high-confidence Top1 cases where the leader was much more volatile than the runner-up, bringing max drawdown back to the SMA250 Top2 baseline while preserving higher CAGR and Sharpe.

This does not make the profile a QQQ replacement: QQQ buy-and-hold still has a higher long-run CAGR in the same broad research window. The retained default Global ETF profile still keeps the same risk profile; this note is only the audit trail for the parameter set that was folded into that default and the alias kept for regression checks.

## Rollout Recommendation

- Keep `global_etf_rotation` as the default defensive profile.
- Keep `global_etf_confidence_vol_gate` only as an explicit comparison alias for regression checks.
- Use paper or small allocation first if you run the `75 / 25` comparison path; integer-share runtimes may drift more because the candidate can target unequal weights.
