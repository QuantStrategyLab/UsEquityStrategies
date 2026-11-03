# R3 B4 预测校准：NLL / CRPS / PIT（仅研究）

模块：`us_equity_strategies.research.b4_predictive_calibration`
测试：`tests/research/test_b4_predictive_calibration.py`（合成状态与观测，无网络）

## 做什么

B4 在给定、固定的创新协方差 `Sigma` 下，对每个 core 的对数毛收益给出高斯预测：

```text
y_t = log(1 + r_t) ~ Normal(mean_log_j, predictive_cov_log[j][j])
```

本模块把这个预测分布与已实现观测逐条对照，回答三个问题：

1. **概率是否校准**：PIT 是否接近 Uniform(0,1)，中心区间覆盖率是否接近名义水平；
2. **预测是否比只看均值更准**：NLL / CRPS 相对零均值、扩展样本均值两个基线是否更低；
3. **预测是否偷看未来**：每条预测所用的 B4 状态，其输入在 `forecast_at` 之前是否已经可见。

它是评分器，不是模型拟合器，也不是数据加载器。`Sigma`、`training_identity` 和训练窗口仍由调用方提供并声明，评分器无法验证它们是否真的来自独立训练段。

## 评分公式

对单条 `(y, mu, sigma^2)`，令 `z = (y - mu) / sigma`：

```text
NLL   = 0.5 * ln(2*pi*sigma^2) + 0.5 * z^2
CRPS  = sigma * [ z*(2*Phi(z) - 1) + 2*phi(z) - 1/sqrt(pi) ]
PIT   = Phi(z)
```

- NLL、CRPS 越低越好。
- PIT 在模型正确时应为 Uniform(0,1)；`pit_mean` 偏离 0.5 指示均值偏差，`pit_variance` 高于 1/12 指示方差低估，低于 1/12 指示方差高估。
- `pit_coverage` 使用中心区间：名义 50% 对应 PIT 在 `[0.25, 0.75]` 的比例，80% 对应 `[0.10, 0.90]`，90% 对应 `[0.05, 0.95]`，95% 对应 `[0.025, 0.975]`。
- `pit_ks_distance` 是 PIT 经验分布到 Uniform(0,1) 的 Kolmogorov-Smirnov 距离；`pit_ks_threshold_5pct = 1.36 / sqrt(n)` 只是渐近 iid 阈值，序列相关时偏乐观，仅作诊断。
- 两个基线共享 B4 的**同一个预测方差**，因此 NLL/CRPS 差值只反映均值部分：
  - 零均值：`mu = 0`；
  - 扩展样本均值：`mu = (sum_y + compensation) / n`，只使用该状态已接纳的观测；`n = 0` 时取 0。
- `share_b4_nll_better_than_*` 是逐条胜率，不是显著性检验。

## PIT 安全协议

每条 `B4ForecastRecord` 显式携带：

- `state`：预测时使用的 B4 状态；
- `realized`：之后发生的 `B4Observation`；
- `forecast_at`：预测形成的时间。

评分器逐条检查：

1. 状态内每条已接纳观测的 `observed_at`、`available_at`、`decision_cutoff` 都不晚于 `forecast_at`；
2. 状态的 `training_cutoff` 不晚于 `forecast_at`；
3. 已实现观测严格晚于 `forecast_at`、训练截止时间和状态 watermark；
4. 输入的 `observed_at` 严格递增，`observation_id` 唯一；
5. 整条序列的 core 顺序、`Sigma`、`training_identity`、`training_cutoff` 必须一致，否则拒绝。

这些检查证明“状态里可见的输入在预测时已经可见”，但不能证明调用方声明的 `Sigma` 和训练身份诚实。后者在输出中标记为 `CALLER_ATTESTATION_NOT_VERIFIED`。

## API

```python
from us_equity_strategies.research.b4_predictive_calibration import (
    B4ForecastRecord,
    score_b4_predictive_calibration,
)

records = [
    B4ForecastRecord(state=state_at_t, realized=observation_t_plus_1,
                     forecast_at=datetime(...)),
    ...
]
result = score_b4_predictive_calibration(records, bins=10)
```

输出：

- `status`：`COMPUTED` 或 `UNCOMPUTABLE`；不可计算时不返回任何数字；
- `cores[core]`：该 core 的聚合指标（NLL/CRPS 均值、PIT 统计与直方图、覆盖率、KS、z 统计、基线差值、逐条胜率）；
- `records[*]`：逐条预测和实现的明细，含 `state_n`、`source_prefix_sha256`；
- `scored_series_sha256`：规范化后的输入/评分序列摘要；
- 恒定 `research_only=True`、`promotion_eligible=False`、`data_identity="CALLER_SUPPLIED"`、`live_ready=False`、`no_order=True`、`budget_effect="NONE"`；
- `joint_scoring="NOT_COMPUTED_MARGINAL_ONLY"`：双 core 结果是两个边际评分，不是联合评分。

以下情况直接拒绝：空序列、错误的记录/观测类型、aware timestamp 缺失、维度或 core 顺序不符、`bins < 2`、重复 ID、乱序观测、混合 provenance、状态含未来信息、预测不早于实现。

以下情况返回 `UNCOMPUTABLE`：B4 矩不可计算、预测方差非正、NLL/CRPS/基线出现非有限值。不可计算时不会退化为 0 或“跳过”。

## 合成 sanity（不是 OOS，不是收益证据）

固定数据生成过程：`mu = -0.0004`、`sigma = 0.02`、500 个会话、`B4 Sigma = 0.0004`、种子 7。`mu` 取 B4 的初始先验均值 `-0.5*(sigma + sigma/kappa0)`（`kappa0 = 1`），因此“模型一致”情形只考察固定 `Sigma` 下的预测校准。每个会话用当前 B4 状态预测下一个对数收益，再接纳实现值。这只是校准诊断，不代表真实 SOXL/TQQQ 数据。

| 情形 | NLL | CRPS | PIT 均值 | PIT 方差 | 覆盖率 50/80/90/95 | KS | 5% 阈值 | 超阈 |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | :---: |
| 模型一致 | -2.469 | 0.01157 | 0.508 | 0.0854 | 49.4% / 78.6% / 89.0% / 95.0% | 0.0448 | 0.0608 | 否 |
| 实现波动 ×1.5 | -1.821 | 0.01798 | 0.516 | 0.1234 | 34.2% / 59.2% / 72.0% / 79.6% | 0.1226 | 0.0608 | 是 |
| 均值上移 +0.5σ | -2.468 | 0.01158 | 0.510 | 0.0854 | 49.2% / 78.4% / 89.0% / 95.0% | 0.0479 | 0.0608 | 否 |

基线差（B4 − 基线，负值偏向 B4）：

| 情形 | NLL vs 零均值 | NLL vs 样本均值 | CRPS vs 零均值 | CRPS vs 样本均值 |
| --- | ---: | ---: | ---: | ---: |
| 模型一致 | +0.00420 | -0.00030 | +0.000057 | -0.000005 |
| 实现波动 ×1.5 | +0.00955 | -0.00068 | +0.000101 | -0.000009 |
| 均值上移 +0.5σ | -0.11405 | -0.000033 | -0.001420 | +0.000002 |

解读：

- 模型一致时 PIT 均匀、覆盖率接近名义值，KS 不超阈；这是评分器自身的 sanity，不是策略有效性证据。
- 实现波动是假设的 1.5 倍时，覆盖率明显不足、PIT 方差偏高、KS 超阈。说明固定 `Sigma` 的假设一旦错误，评分器会检测到；B4 本身不会自动修正 `Sigma`。
- 均值恒定上移时，B4 的后验均值会随观测更新，覆盖率仍接近名义值；相对零均值的 NLL 改善主要来自这个自适应均值。
- 相对样本均值的差异很小且符号不稳定，不能据此宣称 B4 的收缩均值优于样本均值。基线共享预测方差，比较的只是均值部分。

## 边界

- 仅研究；不接运行时、插件、券商或账户，不产生订单或预算变更。
- 仅边际评分。B4 的联合协方差可用，但本模块不计算联合 NLL/CRPS/PIT；双 core 输出不是组合风险或联合校准结论。
- 预测律是“给定固定 `Sigma` 的对数收益高斯分布”。`Sigma` 估计不确定性、序列相关、波动聚集和模型设定错误都在评分器之外；KS 阈值也不修正序列相关。
- 不做参数拟合、不做 `Sigma` 重估、不做 DSR/PSR/PBO，不改变 RS-04 的试验集。
- 真实数据需要调用方先物化 B4 状态和观测，并按预注册窗口提供 `forecast_at`；当前切片未接真实数据。
- 运行时入口/策略不得导入本模块；边界沿用 `tests/test_research_import_boundary.py`。

## 已验证

- 13 项测试：
  - NLL、CRPS、PIT 与手算一致；
  - 模型一致时 PIT 均匀、覆盖率名义、KS 不超阈；
  - 实现波动放大时覆盖率下降、KS 超阈；
  - 样本均值基线只使用状态已接纳的前缀，prior-only 时退化为零均值；
  - 状态含未来信息、预测不早于实现、训练截止晚于预测均被拒绝；
  - 混合 `Sigma`/训练身份、重复 ID、乱序实现均被拒绝；
  - 双 core 按边际评分；
  - B4 矩错误、非正方差、NLL 溢出均返回 `UNCOMPUTABLE` 且不产生数字；
  - 公开输出 JSON 可序列化，且不含授权/订单字段。
- Ruff 通过（仅有 `pyproject.toml` 顶层 linter 配置弃用警告）。
- 已随 PR #649 squash 合并到 main（`68ba57a4`）；CI run `39739109111` 的 5 个 job 全部通过，`research-stats-integration` 使用 overlay `dd17abb` 实际执行。未接真实数据。

## 尚未做

1. 在真实 SOXL/TQQQ 的预注册窗口上生成 B4 状态和实现观测，检查 `forecast_at`、`available_at`、watermark 的实际数据口径。
2. 联合评分：双 core 的联合 NLL/CRPS、组合 PIT，以及 `Sigma` 估计不确定性下的校准；这些会改变当前“仅边际”的结论范围。
3. 与 R5 成本感知 Kelly、RS-04 滚动前推的接口：本模块只评分预测分布，不直接产生仓位或复利结论。

## 真实输入准备与只读账户核对的区别

[账户输入只读核对](offline-portfolio-risk-preview.zh-CN.md#真实账户输入的只读核对)可以保留来源摘要、原观察时刻、实际接收时刻及新的核对截止时刻；它没有产生B4状态或收益观察。账户某一时刻的净值，不等于剔除外部资金流后的策略收益；原始ETF行情也不能直接冒充策略core收益。已见归档不重新命名为独立样本外。

真实评分前先作有界资格核对：是否有对应core、同收益/费用口径的连续输入，实际训练与评分边界，以及预测时所能获得的输入来源。缺少这些资料时保留不足，不用文件创建时间或今天的读取时刻倒填历史available_at。后续如资料齐备，复用本评分器已有的零均值及扩展样本均值基线，事先固定单个比较问题和停止条件；不增加预测器、先验搜索或第二套评分框架。
