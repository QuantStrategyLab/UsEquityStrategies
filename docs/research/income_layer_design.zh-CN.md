# 收入层设计研究结论

_更新日期：2026-05-26_

## 结论

收入层不使用统一的 `1000000 USD` 启动门槛。`1000000 USD` 只作为大账户校准场景，用来验证组合层回撤是否能压到 SPY / QQQ 对照以内；真实运行参数按策略风险分别配置。

当前默认设计固定为：

| Profile | 模式 | 起点 | 平滑带 | 硬上限 | 默认收入篮子 |
| --- | --- | ---: | ---: | ---: | --- |
| `tqqq_growth_income` | `log_cap` | `250000` | `20%` | `50%` | `SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%` |
| `soxl_soxx_trend_income` | `log_cap` | `250000` | `20%` | `95%` | `SCHD 25% / DGRO 15% / SGOV 55% / SPYI 4% / QQQI 1%` |
| `global_etf_rotation` | `log_loss_budget` | `500000` | `10%` | `15%` | `SCHD 40% / DGRO 25% / SGOV 30% / SPYI 5%` |
| `russell_1000_multi_factor_defensive` | `log_loss_budget` | `400000` | `10%` | `20%` | `SCHD 45% / DGRO 30% / SGOV 25%` |
| `tech_communication_pullback_enhancement` | `log_loss_budget` | `250000` | `15%` | `30%` | `SCHD 40% / DGRO 25% / SGOV 20% / SPYI 10% / QQQI 5%` |
| `mega_cap_leader_rotation_top50_balanced` | `log_loss_budget` | `300000` | `15%` | `25%` | `SCHD 45% / DGRO 30% / SGOV 20% / SPYI 5%` |

启动门槛、平滑带和接近上限位置的图表见
[`income_layer_activation_drawdown_2026-05-26.svg`](./income_layer_activation_drawdown_2026-05-26.svg)。

## 设计规则

- 杠杆策略使用 `log_cap`：目标是让组合层回撤接近或不超过 SPY / QQQ 对照，同时尽量保留复利。
- 非杠杆策略使用 `log_loss_budget`：目标是账户规模变大后的波动钝化，而不是显著改变策略本身。
- `income_layer_start_usd` 必须按策略单独配置。杠杆策略更早启动，非杠杆策略更晚启动。
- `income_layer_activation_band_ratio` 用来解决门槛附近来回切换的问题。目标收入层比例在 `start` 到 `start * (1 + band)` 之间从 0 平滑放大到正常值。
- `income_layer_max_ratio` 是组合层风险预算，不是收益最大化参数。上限提高通常降低回撤，但也会降低长期 CAGR。
- 现有收入层采用 `max(current_income_layer_value, desired_income_layer_value)` 锁定已有收入资产，默认只增配，不主动减配。

## 杠杆策略实盘候选复核

研究输出：

`UsEquitySnapshotPipelines/data/output/levered_income_layer_candidate_compare_2026-05-26/`

选择规则：

- `1000000 USD` 初始权益作为大账户校准，不代表启动门槛。
- 必须通过全部 SPY 和 QQQ 标准窗口回撤约束。
- TQQQ 额外使用 `1000000 USD` 最大回撤不超过约 `15%` 的约束，匹配“100 万最多亏 15 万”的账户约束。
- 在通过约束的候选里按 CAGR 排序，若收益接近则优先保留更简单、更贴近当前生产路径的核心策略。

最终固定：

| Strategy | Version | CAGR | Max drawdown | SPY windows | QQQ windows | Avg income ratio | End income ratio | Decision |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| `tqqq_growth_income` | `start=250000, max=50%, current_tqqq basket` | `30.54%` | `-14.87%` | pass | pass | `39.03%` | `41.01%` | 采用 |
| `tqqq_growth_income` | `start=500000, max=60%, current_tqqq basket` | `31.21%` | `-15.93%` | pass | pass | `36.25%` | `42.34%` | 不采用：超过 15% 大账户亏损预算 |
| `tqqq_growth_income` | previous default `start=150000, max=50%` | `29.21%` | `-14.24%` | pass | pass | `42.63%` | `43.86%` | 被替换：收益较低且小账户更早进入收入层 |
| `soxl_soxx_trend_income` | `start=250000, max=95%, balanced_income basket` | `36.14%` | `-9.04%` | pass | pass | `76.02%` | `82.57%` | 采用 |
| `soxl_soxx_trend_income` | previous default `start=150000, max=90%, current_soxl basket` | `32.16%` | `-7.70%` | pass | pass | `78.52%` | `82.45%` | 被替换：收益明显较低 |

SOXL 核心 overlay 也做了窄候选复核：

| Core version | CAGR | Max drawdown | Note |
| --- | ---: | ---: | --- |
| current manifest：`SOXX 10d vol >= 55%, SOXL -> SOXX` | `49.74%` | `-42.31%` | 保留；与收入层组合后的 CAGR 最高 |
| `SOXX 10d vol >= 55%, SOXL -> BOXX` | `49.84%` | `-42.31%` | 核心略高，但加入收入层后不如当前 manifest |
| `SOXX 10d vol >= 50%, SOXL -> SOXX` | `48.48%` | `-42.31%` | 更频繁降档，收益低于当前 manifest |

因此 SOXL 本次只调整收入层，不改核心 `blend_gate_volatility_delever_*` 默认值。

## 核心默认参数复核

2026-05-26/27 在收入层默认值选定后，又对杠杆核心做了一轮小范围复核。复核刻意保持窄候选：

- TQQQ：围绕默认 `45% QQQ / 45% TQQQ / 8% BOXX / 2% cash` active mix 调整。
- SOXL/SOXX：围绕默认 `70% SOXL / 20% SOXX` full tier、`65% SOXL / 20% SOXX` mid tier、`15% SOXX` defensive tier 调整。
- 成交量压力 overlay：只允许把杠杆腿转到对应一倍标的，即 `TQQQ -> QQQ`、`SOXL -> SOXX`。

没有候选同时通过真实产品样本和长合成压力样本的 no-regression 规则。更保守的配比只能通过牺牲 CAGR 降低回撤；更高收益配比会让回撤变差。表面最好的 SOXL 成交量 overlay 在全样本改善 CAGR 和回撤，但在 2024-2026 反弹窗口 CAGR 拖累超过 11 pp，因此成交量只保留为 shadow / 通知观察项。

结论：TQQQ 和 SOXL/SOXX 的默认核心保持不变。本次复核不修改 `dual_drive_*`、`blend_gate_*`，也不加入基于成交量的可执行 overlay。

## 杠杆策略代表性扫参归档

研究输出：

`UsEquitySnapshotPipelines/data/output/income_layer_design_research_2026-05-26/`

代表性网格不是全量暴力扫参，只测试当前默认附近的关键替代项：

- 起始权益：`100000`、`250000`、`1000000`
- 变体：当前默认、起点上下移动、平滑带 `0% / 20% / 50%`、上限收紧 / 放宽、收入篮子偏现金 / 更均衡
- 评价：CAGR、最大回撤、是否通过 SPY / QQQ 窗口回撤约束、收入层平均 / 期末占比

旧默认参数结果：

| Strategy | Initial equity | CAGR | Max drawdown | SPY windows | QQQ windows | Avg income ratio | End income ratio |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `tqqq_growth_income` | `100000` | `41.44%` | `-23.78%` | fail | fail | `3.52%` | `14.84%` |
| `tqqq_growth_income` | `250000` | `34.21%` | `-18.20%` | fail | pass | `26.96%` | `32.67%` |
| `tqqq_growth_income` | `1000000` | `29.21%` | `-14.24%` | pass | pass | `42.63%` | `43.86%` |
| `soxl_soxx_trend_income` | `100000` | `100.20%` | `-29.98%` | fail | fail | `26.20%` | `61.47%` |
| `soxl_soxx_trend_income` | `250000` | `61.51%` | `-16.51%` | fail | fail | `55.69%` | `71.61%` |
| `soxl_soxx_trend_income` | `1000000` | `32.16%` | `-7.70%` | pass | pass | `78.52%` | `82.45%` |

归档读法：

- 小账户阶段不强行要求组合回撤不超过大盘。小账户的目标仍是增长层复利，收入层只在权益跨过门槛后逐步介入。
- 资金达到 `1000000 USD` 后，收入层配置必须把 TQQQ 和 SOXL 的组合层回撤压到 SPY / QQQ 窗口对照以内。
- TQQQ 从 `150000` 启动上移到 `250000`，能让小账户阶段少受收入层拖累；在 `1000000 USD` 校准下仍保持约 `-14.87%` 最大回撤。
- SOXL 从 `150000 / 90% / current_soxl` 调整为 `250000 / 95% / balanced_income`，在 `1000000 USD` 校准下 CAGR 从约 `32.16%` 提升到 `36.14%`，最大回撤从约 `-7.70%` 扩到 `-9.04%`，仍明显低于 15% 亏损预算并通过 SPY / QQQ 回撤窗口。

## 预设方向

后续如果需要暴露 preset，不改变当前默认，只增加配置模板：

| Preset | 适用账户 | 规则 |
| --- | --- | --- |
| `growth` | 小资金、目标快速复利 | 提高 `income_layer_start_usd`，降低收入层平均占比，接受组合回撤更接近权益层。 |
| `balanced` | 当前默认 | 保持现有门槛、平滑带和上限。 |
| `capital_preserve` | 大账户、亏损敏感 | 降低 `income_layer_start_usd` 或提高 `income_layer_max_ratio`，收入篮子更偏 SGOV / 防守资产。 |

当前不把 preset 做成新的 runtime 参数，原因是 `income_layer_*` 已经足够可配置；preset 更适合后续作为文档模板或 UI 层快捷选项。
