# Research-only 策略存档

更新日期：2026-04-18

这些 profile 不硬删除。代码、manifest、entrypoint、runtime adapter 和历史研究证据继续保留，方便复盘、回放和必要时重新验证；但 catalog metadata 的 `status` 已改为 `research_only`，因此不会进入平台 runtime rollout allowlist，也不应再作为 `STRATEGY_PROFILE` 部署。

| Profile | 存档原因 | 保留范围 |
| --- | --- | --- |
| `mega_cap_leader_rotation_dynamic_top20` | Top50 balanced 已成为更优先的无杠杆运行候选；Top20 主要作为更保守的历史对照。 | 策略实现、feature snapshot 合约、entrypoint、runtime adapter、历史回测说明。 |
| `mega_cap_leader_rotation_aggressive` | Top50 top3/cap35 高集中分支容易放大参数敏感性；当前只保留为 aggressive research evidence。 | 策略实现、aggressive snapshot 合约、entrypoint、runtime adapter、Top50 top3/cap35 验证结果。 |
| `dynamic_mega_leveraged_pullback` | 2x 单股产品路线更复杂，MAGS/TACO 未进入正式运行逻辑；Top50 balanced 是更干净的当前候选。 | 2x 产品回调策略实现、snapshot 合约、entrypoint、runtime adapter、风险预算研究。 |

恢复任何存档 profile 前，需要重新跑当前数据、补齐 snapshot artifact、更新平台 rollout allowlist，并在 PR 或运行手册里记录具体命令、输入和结果。
