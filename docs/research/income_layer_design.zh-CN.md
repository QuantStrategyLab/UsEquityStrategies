# 收入层设计研究结论

_更新日期：2026-05-26_

## 结论

收入层不使用统一的 `1000000 USD` 启动门槛。`1000000 USD` 只作为大账户校准场景，用来验证组合层回撤是否能压到 SPY / QQQ 对照以内；真实运行参数按策略风险分别配置。

当前默认设计保持为：

| Profile | 模式 | 起点 | 平滑带 | 硬上限 | 默认收入篮子 |
| --- | --- | ---: | ---: | ---: | --- |
| `tqqq_growth_income` | `log_cap` | `150000` | `20%` | `50%` | `SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%` |
| `soxl_soxx_trend_income` | `log_cap` | `150000` | `20%` | `90%` | `SCHD 20% / DGRO 10% / SGOV 65% / SPYI 4% / QQQI 1%` |
| `global_etf_rotation` | `log_loss_budget` | `500000` | `10%` | `15%` | `SCHD 40% / DGRO 25% / SGOV 30% / SPYI 5%` |
| `russell_1000_multi_factor_defensive` | `log_loss_budget` | `400000` | `10%` | `20%` | `SCHD 45% / DGRO 30% / SGOV 25%` |
| `tech_communication_pullback_enhancement` | `log_loss_budget` | `250000` | `15%` | `30%` | `SCHD 40% / DGRO 25% / SGOV 20% / SPYI 10% / QQQI 5%` |
| `mega_cap_leader_rotation_top50_balanced` | `log_loss_budget` | `300000` | `15%` | `25%` | `SCHD 45% / DGRO 30% / SGOV 20% / SPYI 5%` |

## 设计规则

- 杠杆策略使用 `log_cap`：目标是让组合层回撤接近或不超过 SPY / QQQ 对照，同时尽量保留复利。
- 非杠杆策略使用 `log_loss_budget`：目标是账户规模变大后的波动钝化，而不是显著改变策略本身。
- `income_layer_start_usd` 必须按策略单独配置。杠杆策略更早启动，非杠杆策略更晚启动。
- `income_layer_activation_band_ratio` 用来解决门槛附近来回切换的问题。目标收入层比例在 `start` 到 `start * (1 + band)` 之间从 0 平滑放大到正常值。
- `income_layer_max_ratio` 是组合层风险预算，不是收益最大化参数。上限提高通常降低回撤，但也会降低长期 CAGR。
- 现有收入层采用 `max(current_income_layer_value, desired_income_layer_value)` 锁定已有收入资产，默认只增配，不主动减配。

## 杠杆策略代表性扫参

研究输出：

`UsEquitySnapshotPipelines/data/output/income_layer_design_research_2026-05-26/`

代表性网格不是全量暴力扫参，只测试当前默认附近的关键替代项：

- 起始权益：`100000`、`250000`、`1000000`
- 变体：当前默认、起点上下移动、平滑带 `0% / 20% / 50%`、上限收紧 / 放宽、收入篮子偏现金 / 更均衡
- 评价：CAGR、最大回撤、是否通过 SPY / QQQ 窗口回撤约束、收入层平均 / 期末占比

默认参数结果：

| Strategy | Initial equity | CAGR | Max drawdown | SPY windows | QQQ windows | Avg income ratio | End income ratio |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `tqqq_growth_income` | `100000` | `41.44%` | `-23.78%` | fail | fail | `3.52%` | `14.84%` |
| `tqqq_growth_income` | `250000` | `34.21%` | `-18.20%` | fail | pass | `26.96%` | `32.67%` |
| `tqqq_growth_income` | `1000000` | `29.21%` | `-14.24%` | pass | pass | `42.63%` | `43.86%` |
| `soxl_soxx_trend_income` | `100000` | `100.20%` | `-29.98%` | fail | fail | `26.20%` | `61.47%` |
| `soxl_soxx_trend_income` | `250000` | `61.51%` | `-16.51%` | fail | fail | `55.69%` | `71.61%` |
| `soxl_soxx_trend_income` | `1000000` | `32.16%` | `-7.70%` | pass | pass | `78.52%` | `82.45%` |

读法：

- 小账户阶段不强行要求组合回撤不超过大盘。小账户的目标仍是增长层复利，收入层只在权益跨过门槛后逐步介入。
- 资金达到 `1000000 USD` 后，默认配置能把 TQQQ 和 SOXL 的组合层回撤压到 SPY / QQQ 窗口对照以内。
- TQQQ 在 `250000 USD` 起始权益时，把 `income_layer_max_ratio` 提到 `65%` 可以通过 SPY / QQQ 回撤窗口，但 CAGR 从 `34.21%` 降到 `31.59%`。默认保留 `50%`，因为它更适合作为增长账户折中。
- SOXL 在 `1000000 USD` 起始权益时，`balanced_income` 篮子能把 CAGR 从 `32.16%` 提到 `33.12%`，但回撤从 `-7.70%` 扩到 `-8.00%`。默认仍保留更偏 SGOV 的 `current_soxl`，因为半导体杠杆核心本身波动更高。

## 预设方向

后续如果需要暴露 preset，不改变当前默认，只增加配置模板：

| Preset | 适用账户 | 规则 |
| --- | --- | --- |
| `growth` | 小资金、目标快速复利 | 提高 `income_layer_start_usd`，降低收入层平均占比，接受组合回撤更接近权益层。 |
| `balanced` | 当前默认 | 保持现有门槛、平滑带和上限。 |
| `capital_preserve` | 大账户、亏损敏感 | 降低 `income_layer_start_usd` 或提高 `income_layer_max_ratio`，收入篮子更偏 SGOV / 防守资产。 |

当前不把 preset 做成新的 runtime 参数，原因是 `income_layer_*` 已经足够可配置；preset 更适合后续作为文档模板或 UI 层快捷选项。

