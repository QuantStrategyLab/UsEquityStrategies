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
- 默认 `synthetic=True`。真实数据只允许白名单 {SOXL, TQQQ}，且须提供与 [预注册](fractional_kelly_preregistration.zh-CN.md) 完全一致的 `RealDataAuthorization`（预注册 id + 数据清单）；其他一律 `REAL_DATA_REQUIRES_APPROVAL`，预注册数据源未确认时 `PREREGISTRATION_PENDING_DATA_CONFIRMATION`。
- 结果只能作为 `kelly_ready` **上限备注**（`kelly_cap_note.usage = MAY_ONLY_LOWER_OR_CAP_NEVER_RAISE_EXISTING_BUDGET`），`budget_effect = NONE`；不提高任何预算，不授权晋级/纸面/影子/实盘，报告恒为 `live_ready=false`、`no_order=true`、`DEVELOPMENT_ONLY`。
- 已安装 QPK 无 `research_stats` 时整体 `UNCOMPUTABLE`（`QPK_RESEARCH_STATS_UNAVAILABLE`），不输出任何数字。
- 运行时入口/策略不导入本模块（`tests/test_research_import_boundary.py`）。
- 给出 `report_context` 时输出 `qsl.backtest_report.v1`（QPK `BacktestReportV1` 校验，CI 中再用 JSON Schema 校验）。

## QPK 版本

与 RS-02 相同：UES 的 QPK pin 不变；主 `test` job 在旧 pin 下只跑合同/降级测试，依赖 `research_stats` 的测试在 `research-stats-integration` job（QPK `442b729`）中强制运行。

## 若要在真实数据上运行，需要

1. 候选已获批准（SOXL、TQQQ，2026-10-11）；数据源与窗口仍需用户确认后写入预注册（单独审阅的 PR）。
2. 数据：该策略逐会话满仓收益（含拆股/分红处理说明）、可选的策略内部换手序列、同日期基准收益、现金收益序列（或明确声明按 0 计）、可选 R8 路径（收益 + 换手）；数据清单 SHA-256、来源、许可、日历。
3. 预先冻结并记录：训练/测试/purge 会话数、是否锚定、`c` 集合、基础费率与档位、信号截止与成交时点（须符合回测标准 v1 §4，见 UES #575）、选择规则（本评估只报告不选择）。
4. 数据身份：已见历史只能是 `development`；不能作为 OOS 结论，更不能据此提高预算。
5. 输出须经人工复核后，才可写入对应候选的 `kelly_ready` 上限备注。

## RS-04d：无插件核心收益路径与私有运行器

- `research/kelly_core_return_generator.py`：从**原始未复权**日线加公司行动，构建实盘 SOXL / TQQQ **仅核心规则**（`no_plugin_core`，所有插件/叠加层关闭）的满仓毛收益和逐日换手。
  - 单标的总收益为 `(close_t·拆股比 + 分红_t)/close_{t−1} − 1`，信号用前向构建的总收益指数。
  - 成交时点 `next_close`：t 日收盘出信号，t+1 收盘成交，t+2 起计收益。
  - `turnover[k]` 是 k 日开始时的单边证券成交额/NAV（现金腿不计），供 QPK 费用档位压力使用。
  - 旧 `turnover` 保持 close 成交后移到下一 earning session 的合同；最后 close 已成交但没有下一会话时，旧费用路径会漏掉该笔费用。新增 `CorePath.close_fill_turnover` 只供显式测量：记录真实 close 的单边证券成交额/pre-fill NAV，包含末次成交，`window` 同会话切片。
  - 显式调用 `path.measure_close_fill_costs(cost_model)` 并注入 QPK `research_stats.execution_models.CostModel` 才生成 at-fill 净收益。close 成本在当日毛收益后扣，金额为 `fill_turnover × (1 + gross_return) × 每边费率`，相对当日起始 NAV，仅转换一次。尚未加载该接口的旧 QPK pin 会明确报 `QPK_EXECUTION_MODELS_REQUIRED`；本变更不改 pin。
  - 该测量是 `no_weight_feedback` 的独立 NAV 费用 overlay，费用不反馈到原毛仓位、策略决策或参考权益；不是多资产自融资净仓位、外层现金/证券净额模型，也不代表完整 A2 适用性或新研究冻结。默认生成器的毛收益、旧换手、私有 runner、路径摘要和历史报告不消费该诊断，不改写旧结果。没有下一会话的末日新信号不虚构成交；原已排到末日 close 的成交正常计费。
  - 预热完成后，任何策略失败一律报错，不静默跳过。
- `research/kelly_private_runner.py`：只能在批准的私有数据环境运行（检测到 `CI`/`GITHUB_ACTIONS` 拒绝）。
  - 校验归档 `manifest.json` 的 SHA-256 与预注册一致，逐页核对 SHA-256 和字节数。
  - 只接受 `forward_splits`、`cash_dividends`，其他公司行动类型一律拒绝。
  - 结果写入 0700/0600 私有目录，stdout 只输出状态。
- 评估器新增：各档 `cvar_95_daily`（最差 5% 日收益的均值），以及 Kelly 变体的 DSR。DSR 的试验集是本次评估的全部仓位方案（c=0.25、c=0.5、满仓、固定 50%），用 1x 费后超额收益计算。
