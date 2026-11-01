# 试验集过拟合报告：DSR + PBO（RS-02，研究-only）

模块：`us_equity_strategies.research.trial_overfitting_report`。

- `compute_trial_set_overfitting(...)`：纯函数，输入为成功试验的**带日期**单期净收益；先按 `年化 rf / 252` 扣成超额收益，再调用 QPK `research_stats.deflated_sharpe_ratio` 与 `probability_of_backtest_overfitting`（CSCV，单期 Sharpe）。
- `report_journaled_tqqq_trial_set_overfitting(...)`：只读本地 `PerformanceStore` 中已记账的**合成** TQQQ SMA 试验；真实/历史账本直接拒绝（`REAL_RESEARCH_DATA_UNQUALIFIED`）。不模拟、不选参、不写入。

独立的 [真实账本规范化适配器](normalized-real-ledger-adapter.md) 接受调用方显式转换的账本与来源字节，核验身份、费用及净值算术，并输出条件性候选集统计。它不改变上述合成入口的限制，也不证明来源真实性、映射正确性或完整试验族覆盖；主 DSR/PBO 保持空值。

## 规则

- 必须传入研究的**全部**试验 id（含失败/拒绝/中止）。非成功试验没有收益路径，只计数并披露，不插补；统计器可用但记录数大于成功数时，DSR 为 `UNCOMPUTABLE`（`INCOMPLETE_TRIAL_RETURN_HISTORY`），PBO 仍使用成功试验的收益路径。当全部记录均成功且 DSR 可计算时，有效试验数等于成功试验数；试验相关性未估计。
- 每条收益路径的日期必须为 `date` 且严格递增、不得重复；收益必须为非布尔的有限数值。畸形输入以 `TRIAL_RETURN_INPUT_INVALID` 拒绝，在调用统计器之前检查；有效的空或短路径仍按原有统计器和不足样本规则处理，不填零。
- 各试验必须在**完全相同的交易日序列**上比较。默认不对齐时 DSR/PBO 为 `UNCOMPUTABLE`（`TRIAL_SESSIONS_NOT_ALIGNED`）；调用方可显式给 `start_session`/`end_session`，边界不在某个账本里则报 `RESEARCH_WINDOW_INVALID`，不做静默取交集。
- 任一试验 Sharpe 无定义 → DSR `UNCOMPUTABLE`（`TRIAL_SHARPE_UNDEFINED`）。
- 已安装的 QPK 没有 `research_stats`（或缺 PBO）时，相应指标为 `UNCOMPUTABLE`（`QPK_RESEARCH_STATS_UNAVAILABLE`），不会填 0，也不在 UES 内另写一套替代算法。
- 输出恒为 `research_only=true`、`promotion_eligible=false`、`live_ready=false`、`size_zero_required=true`、`no_order=true`。

## QPK 版本与 pin

UES 的实际依赖以 [`pyproject.toml`](../../pyproject.toml) 和锁定文件为准；研究统计能力不要求更改生产依赖。研究验证环境与生产安装分别记录：

- 主 CI job 使用包声明的固定版本，验证能力缺失时保留不可计算状态的路径；
- 独立的 `research-stats-integration` job 使用 [CI 配置](../../.github/workflows/ci.yml) 声明的不可变 QPK revision，并校验实际安装来源；所需 DSR/PBO 集成测试不得因能力缺失而跳过。

运行时 `entrypoints/`、`strategies/` 及 runtime 模块不 import 任何 research 代码，由 `tests/test_research_import_boundary.py`（AST + 子进程）强制。

