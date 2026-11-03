# R-DATA-01：研究数据时间与完整试验记录

本补丁只改研究代码和合成测试，不重跑真实 trial，不改 R0/R1/R3 结果、策略、费用或历史 receipt。
本地测试通过不等于金融、PIT、独立样本外或生产采用资格通过。

## R1：逐 session 的 first_seen 与决策截止

原 `run_registered_r0_r3_batch` 已把调用方提供的 `first_seen` 保存在
`trial.actual_params.source`，缺失时不补写。原 `validate_r0_r3_source_availability`
只是元数据诊断；原 R1 计算把日期拼成 23:00 UTC，不能证明真实历史到达时间。

新参数 `pit_session_inputs` 在同一批次的 STARTED 记录里持久化，参与参数指纹和
immutable trial 身份。它包含：

- `schema_version = qsl.research.pit_session_inputs.v1`。
- 调用方声明的 `producer_id`、`source_revision`；revision 必须与 trial 一致。
- 按原始 sessions 顺序完整提供的 `sessions` 列表，每项有 `session`、
  `payload_sha256`、`event_at`、`first_seen`、`persisted_at`、`decision_cutoff`。

`session_payload_digests(body)` 对该 session 的全部原始 bars、当日生效的 splits/
dividends（含空列表）取 canonical JSON SHA256。全量输入正文仍由原有 body digest
或 source registration 绑定。时间必须是显式 UTC，事件与截止均在该 session 的 UTC
日期内，满足 `event_at <= first_seen <= persisted_at <= decision_cutoff`。
此最小接口只支持当日数据当日可用；不隐式处理迟到、跨日截止或多版本修订。

验证在 core generator、协方差拟合和 B4 更新之前执行，覆盖暖启动、63 日训练和评估。
缺失、重复、未知时间、迟到或内容冲突直接拒绝整个 trial，不跳过 session、不倒填历史。
提供此参数时，B4 使用所给的截止时间，观测可用时间取 `persisted_at`，决策记录保留
对应声明。未来 bars/actions 扰动测试同时检查已有 R1 原生早期目标不变。

这是明确输入合同，不是生产者认证。它不连接真实数据源，也不把声明变成可信证据。
旧档案和不提供此参数的调用保留原来的 retrospective 行为和指纹；不补历史 first_seen，
也不声称这些调用受到新合同保护。`PIT = NOT_ATTESTED` 保持。
真实未来研究必须显式提供这个输入，并另行验证生产者、交易日历、来源许可与保存证据。

## R6：可失败的前视冒烟

复用 `run_lookahead_smoke`，新增 `assert_no_lookahead`，只扰动 cutoff 之后的数据。
未来窗口泄漏导致信号改变时抛出 `LEAKAGE_DETECTED`；没有未来数据、无效/no-op
扰动或没有触发信号也不能通过。正常截断输入必须通过。

它是单个反例测试，不是“没有前视偏差”的全面证明。合成测试和实际 R1 原生路径
扰动测试分开记录；不运行真实实验。

## R2：purge 与 embargo

复用 `WalkForwardPlan`，所有区间采用半开索引 `[start, end)`，按 session 而非自然日计数。
`purge_sessions >= label_horizon_sessions >= 1`；最后一个训练标签结束位置严格早于验证起点。
外层测试块间隔为 `embargo_sessions`；本次补上同一间隔在内层验证块的应用。
内层训练/验证全部限制在外层训练范围，外层测试数据不参与内层选择。
较短的内层训练窗仍按原合同省略，不能补用外层测试数据凑样本。

这不是一个新的随机 K-fold splitter，也不是已见窗口转为独立 OOS 的办法。
滚动前向评估可在较晚时间使用已经结束的过去窗口，不意味着可以在当前折使用未来数据。
没有用真实数据选窗口、调参数或新增实验。

## R4：所有状态的试验台账

沿用原生 `PerformanceStore` 的 STARTED/terminal/result/ledger，不建立第二份经济结果。
共用 journal、optimized replay journal、QQQ pair journal 在研究开始前及终态读回后，
在独立的 `research_trial_events/<trial digest>/started.jsonl` / `terminal.jsonl`
新增 create-once 的单事件 JSONL UTC 时间旁证，不混入原有经济 JSON 对象集合。
失败、拒绝、中止均保留原生 terminal；`KeyboardInterrupt`、`SystemExit`、
`GeneratorExit` 按中止处理。存储错误保留原异常，不掩盖未知写入结果。

`read_trial_inventory(store)` 只读枚举本地 `research_trial/*/started.json`，不要求
调用方先筛出成功 ID。每条提供 status、reason、尝试/结束时间、参数 SHA256、参数版本标识、
source revision、input ID、数据窗口、原生记录位置、结果定位信息与 ledger 位置。
一个原生 trial 计一次，R1 两个依赖 book 不计成两个独立 trial。
历史记录缺时间仍为 null，不按文件 mtime、数据日期或本次读取时刻伪造。

进程硬退出可能只剩 STARTED；照样计入尝试，终态保持未知，重启不得重算或自动改成失败。
新旁证写入失败时研究不会继续计算；旁证缺失不抹掉已经写成的原生 trial。
库存数量是原始尝试数，不是独立/effective trial 数。成功子集也不能代表全部选择历史：
现有 DSR 报告在 `recorded_trial_count` 大于有收益序列的 trial 数时，保持不可计算
`INCOMPLETE_TRIAL_RETURN_HISTORY`，不补零或只报成功 DSR。
PBO 仍仅描述明确提供的完整收益序列子集；没有新增或声称合格的 DSR/PBO 估计。

## 离线复现

本仓库研究 netting CI 已使用隔离 QPK overlay
`7840127862ea715e44c8f70b03155a399dc817de`；研究插件 pin 是
`3416da47580be1537183e378f3ca9efacc6f9b8c`。不修改生产依赖。
令 `QPK_SRC`、`QSP_SRC` 指向这两个既有本地快照的 `src`：

```sh
PYTHONPATH="src:$QPK_SRC:$QSP_SRC" python3 -m pytest -q \
  tests/research/test_r_data_foundation.py \
  tests/research/test_lookahead_smoke.py \
  tests/research/test_fractional_kelly_walk_forward.py \
  tests/research/test_trial_overfitting_report.py \
  tests/research/test_tqqq_core_trial_journal.py \
  tests/research/test_tqqq_guard_cash_trial_journal.py
```

`test_r_data_foundation.py` 禁止 socket 网络访问，复用仓库人工 fixture 与临时本地 store，
不依赖真实账户或真实数据。具体命令、退出码、通过数、运行环境和 PR 信息见本卡执行报告。
