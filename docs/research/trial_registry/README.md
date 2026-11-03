# 失败、依赖与累计族登记结构

`r_math_04.json` 是 R-MATH-03 所定位七个本地 PerformanceStore 的元数据投影。原生 STARTED/terminal/ledger 是事实来源；本文件不是第二套权威登记簿，也不是完整累计试验族。它不登记新 trial、不修改历史状态、不含收益或新 trial 结果。

当前投影保留九个不同原生身份：成功 4、拒绝 2、中止 2、STARTED 1；18 份 ledger 分为 4 份批次加总审计账和 14 份依赖子账。加总账不是选定策略路径，子账不自动增加独立试验数。上述数字都是局部库存，不能与旧 Kelly 的 48 相加或作为累计 K。

## 结构与未知值

- `source_objects` 保存显式 store、原生对象路径、字节 SHA-256 和允许导出的元数据；不导出 actual_params 全文、daily values、Sharpe 或收益。脚本解析 ledger JSON 以取得身份，但不使用其逐日数值计算指标。
- `trials` 按 `SHA256(domain + NUL + strategy_profile + NUL + trial_id)` 识别身份。STARTED/terminal 合为一个槽；跨 store 同身份同字节副本合并并保留每个位置，冲突拒绝。相同 source/input/参数指纹的新 ID 全部保留，只列相同指纹组，不据此去重。
- 原生 `status/reason_code` 原样保留，`failed/rejected/aborted` 不带成功 result 身份；STARTED 为 `attempt_state=UNKNOWN_TERMINAL`，terminal-only 明示 `missing_started=true`。本轮不回填事件时间或首次故障；未知均为 null。
- `ledger_bindings` 只有在同 store 的 domain/trial_id/run_id/param_version/input_id 精确对应唯一成功父槽时绑定。不同 profile 是机械上的依赖子账，不是额外 trial。无法绑定的 ledger-only 留为 `UNRESOLVED_LEDGER_ONLY`。`observed_dependence_groups` 只记录已见批次内的子账，独立数仍 null；不是全族依赖估计。所有绑定的可比性均未认证。
- `native.candidate_config_id` 是技术配置标识，不冒充经济候选。study/candidate/parent/repair/sensitivity/selection_used 等经济或选择关系缺证时保持 null。QQQ A/B 的原生依赖假设标签及中止原因保留；M1 的同指纹两个 ID 不被擅自认定为重试、修复或独立候选。
- `cumulative_family.member_identity_keys` 是全部局部观察身份，包含失败和未知；该列表不是已冻结的可比候选集合。完整来源、owner/截止点、旧 48 映射、遗漏声明、选型决定、候选/控制、成员纳入理由、窗/日历、净 1x/费用/rf/前缀、依赖方法、consumer/build 与真实准入均为待补字段。结构没有自动认证入口，`status=NEED_USER`。

793 个历史评价观察、855 个 ledger days 与 M2 未来 252 会话不能互换；不同费用、rf、年化或收益定义也不能直接拼族。本模块只实现登记和结构校验，不接收统计数组，不计算 DSR/PBO，不改现有统计 consumer、QPK pin、M2 或协议。

`raw_cumulative_attempts`、`financial_selection_candidates`、`effective_independent_trials` 均为 null。DSR 固定保留 `value=null / UNCOMPUTABLE / COMPLETE_CUMULATIVE_TRIAL_SOURCE_NOT_VERIFIED`，缺路不填 0。源文件哈希一致只能证明同字节，不能证明历史完整、经济可比或统计有效。

## 使用与校验

生成只读元数据投影（显式列出需要读取的每个 store；stdout 重定向到新的报告文件，不得覆盖原生路径）：

```sh
PYTHONPATH=src python -m us_equity_strategies.research.trial_family_registry \
  --store /absolute/existing/performance > /tmp/trial-registry.json
```

内部结构校验可离线运行，不需要本机私有 store：

```sh
PYTHONPATH=src python -m us_equity_strategies.research.trial_family_registry \
  --validate docs/research/trial_registry/r_math_04.json
```

加 `--verify-sources` 时，重新只读枚举已列 store，核查当前原生对象与整个投影一致；遗漏失败、对象改变或新增槽会失败。该模式要求七个本机目录存在，不作 CI 前提。两种校验都不能证明显式 store 之外不存在历史；即使校验 PASS，累计族仍 NEED_USER，DSR 仍不可计算。
