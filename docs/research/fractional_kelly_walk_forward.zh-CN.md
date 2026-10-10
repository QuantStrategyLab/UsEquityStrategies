# RS-04 单策略分数 Kelly 滚动前推评估器（仅研究）

模块：`us_equity_strategies.research.fractional_kelly_walk_forward`
测试：`tests/research/test_fractional_kelly_walk_forward.py`（只用合成收益）

## 做什么

对一个已定义策略的逐会话满仓收益，按滚动前推（walk-forward）折：

1. **只用训练段**估计超额均值 `mu`（相对现金）与样本方差 `sigma2`（ddof=1）；
   用正部收缩 `lambda = max(0, 1 − 1/t²)`（`t = mu / sqrt(sigma2/n)`）把 `mu` 向 0 收缩；
2. 连续 Kelly 比例 `f* = mu_shrunk / sigma2`，实际暴露 `f = clip(c·f*, 0, 1)`，`c` 默认 {0.25, 0.5}，`c ≥ 1` 拒绝；
3. 训练段后留 `purge_sessions`（≥1）间隔，把 `f` 原样用于测试段，余量放现金，每会话开头再平衡到 `f`。

训练样本少于 30、单边（无正负两种收益）或零方差的折记为 `PARKED`，暴露 0（全现金），与 QPK Kelly 合同 v2 的样本下限一致。

## 对照组（同一拼接测试窗口、同一费用档）

| 名称 | 定义 |
| --- | --- |
| `kelly_c0.25` / `kelly_c0.5` | 上述分数 Kelly |
| `full_exposure` | 恒定 100% 策略 |
| `fixed_50pct` | 恒定 50% 策略 + 50% 现金，逐会话再平衡 |
| `benchmark` | 调用方提供的基准，买入持有，首日从现金建仓计费 |
| `r8` | 调用方提供的 R8 路径；**本模块不重算 R8**。只给收益时视为已扣自身费用，仅 1x 档可算；同时给换手时才做 2x/3x 压力 |

费用：经 QPK `research_stats.cost_stress_recompute`，按交易名义额单边 `cost_bps_per_side × 档位` 扣（从现金建仓、日内漂移再平衡、折间换档；若提供策略内部换手，则按 `f` 缩放后一并计入），现金腿不计费。

## 硬边界

- 不加杠杆、不做空：`0 ≤ f ≤ 1`。
- 本版本只接受 `synthetic=True`；真实/私有数据抛 `REAL_DATA_REQUIRES_APPROVAL`。
- 结果只能作为 `kelly_ready` **上限备注**（`kelly_cap_note.usage = MAY_ONLY_LOWER_OR_CAP_NEVER_RAISE_EXISTING_BUDGET`），`budget_effect = NONE`；不提高任何预算，不授权晋级/纸面/影子/实盘，报告恒为 `live_ready=false`、`no_order=true`、`DEVELOPMENT_ONLY`。
- 已安装 QPK 无 `research_stats` 时整体 `UNCOMPUTABLE`（`QPK_RESEARCH_STATS_UNAVAILABLE`），不输出任何数字。
- 运行时入口/策略不导入本模块（`tests/test_research_import_boundary.py`）。
- 给出 `report_context` 时输出 `qsl.backtest_report.v1`（QPK `BacktestReportV1` 校验，CI 中再用 JSON Schema 校验）。

## QPK 版本

与 RS-02 相同：UES 的 QPK pin 不变；主 `test` job 在旧 pin 下只跑合同/降级测试，依赖 `research_stats` 的测试在 `research-stats-integration` job（QPK `442b729`）中强制运行。

## 若要在真实数据上运行，需要

1. **用户明确批准**具体候选、数据源与窗口，并解除 `synthetic=True` 限制（单独 PR，含审阅）。
2. 数据：该策略逐会话满仓收益（含拆股/分红处理说明）、可选的策略内部换手序列、同日期基准收益、现金收益序列（或明确声明按 0 计）、可选 R8 路径（收益 + 换手）；数据清单 SHA-256、来源、许可、日历。
3. 预先冻结并记录：训练/测试/purge 会话数、是否锚定、`c` 集合、基础费率与档位、信号截止与成交时点（须符合回测标准 v1 §4，见 UES #575）、选择规则（本评估只报告不选择）。
4. 数据身份：已见历史只能是 `development`；不能作为 OOS 结论，更不能据此提高预算。
5. 输出须经人工复核后，才可写入对应候选的 `kelly_ready` 上限备注。
