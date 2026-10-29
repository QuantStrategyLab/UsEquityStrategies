# 试验集过拟合报告：DSR + PBO（RS-02，研究-only）

模块：`us_equity_strategies.research.trial_overfitting_report`。

- `compute_trial_set_overfitting(...)`：纯函数，输入为成功试验的**带日期**单期净收益；先按 `年化 rf / 252` 扣成超额收益，再调用 QPK `research_stats.deflated_sharpe_ratio` 与 `probability_of_backtest_overfitting`（CSCV，单期 Sharpe）。
- `report_journaled_tqqq_trial_set_overfitting(...)`：只读本地 `PerformanceStore` 中已记账的**合成** TQQQ SMA 试验；真实/历史账本直接拒绝（`REAL_RESEARCH_DATA_UNQUALIFIED`）。不模拟、不选参、不写入。

## 规则

- 必须传入研究的**全部**试验 id（含失败/拒绝/中止）。非成功试验没有收益路径，只计数并披露，不插补；DSR 的有效试验数 = 成功试验数（试验相关性未估计，已在 `evaluation_contract` 写明）。
- 各试验必须在**完全相同的交易日序列**上比较。默认不对齐时 DSR/PBO 为 `UNCOMPUTABLE`（`TRIAL_SESSIONS_NOT_ALIGNED`）；调用方可显式给 `start_session`/`end_session`，边界不在某个账本里则报 `RESEARCH_WINDOW_INVALID`，不做静默取交集。
- 任一试验 Sharpe 无定义 → DSR `UNCOMPUTABLE`（`TRIAL_SHARPE_UNDEFINED`）。
- 已安装的 QPK 没有 `research_stats`（或缺 PBO）时，相应指标为 `UNCOMPUTABLE`（`QPK_RESEARCH_STATS_UNAVAILABLE`），不会填 0，也不在 UES 内另写一套替代算法。
- 输出恒为 `research_only=true`、`promotion_eligible=false`、`live_ready=false`、`size_zero_required=true`、`no_order=true`。

## QPK 版本与 pin

UES 的 QPK pin（`f6f2079`）**不变**。`f6f2079..442b729` 之间还包含执行翻译/策略契约重构与 LongBridge 鉴权改动，升级 pin 不能保证生产行为不变，因此本改动保持研究-only：

- 主 CI job 使用原 pin，验证缺少 `research_stats` 时的 fail-closed 路径；
- 独立的 `research-stats-integration` job 仅在该 job 内重新安装 QPK `442b729`（QPK 无运行依赖，依赖解析照常开启，不用 `--no-deps`），并要求真实 DSR/PBO 路径测试不得跳过。

运行时 `entrypoints/`、`strategies/` 及 runtime 模块不 import 任何 research 代码，由 `tests/test_research_import_boundary.py`（AST + 子进程）强制。
