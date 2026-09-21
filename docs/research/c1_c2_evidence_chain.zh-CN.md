# C1/C2 组合复利研究证据链（第一批，research/shadow-only）

来源：`AGENTS.md`、系统架构 §4/§5 组合与研究边界、审计报告 §9.9 / §9.12 / §9.14 / §9.15。
本文件固定 **UsEquityStrategies 研究层** 对 C1/C2 的覆盖与剩余边界；不授予 paper/shadow/live，也不承诺最大 CAGR。

## 结论

| 阶段 | 研究验收（本批） | 说明 |
|---|---|---|
| C1 单策略/候选目标闭环 | **研究积木已达标** | raw 目标不因风险缩放被改写；`recommended_target_weights` + `reason_codes` 可追溯；`execution_authorized=false` |
| C2 可比较成员 | **研究积木已达标（声明式口径）** | ≥2 成员可在同一政策/预算下合并；身份/资格冲突 PARK；**一旦声明** as_of/币种/资金/成本/风险/数据 digest，不一致或缺省即 PARK |
| C3–C5 | 不在本批 | 固定预算优化比较、账户 shadow 零提交、人工启用分别后续 |

未覆盖但明确排除：全 Kelly、预测分数直接配权、新建 allocator/registry/DB、QPK 公共 API、broker/执行接口、生产 workflow/凭据/真实行情。

## C1 证据映射

| 验收点 | 现有实现 | 锁定测试 |
|---|---|---|
| raw target 不被风险缩放改写 | `portfolio_risk_budget.assess_portfolio_risk_budget`；输入 mapping 不被 mutate | `tests/test_portfolio_risk_budget.py::test_inputs_are_not_mutated_and_the_result_is_deterministic` |
| risk-scaled / recommended target | 同模块 `recommended_target_weights` + `risk_scalar`；超预算等比缩减并转入现金 | `test_leveraged_target_is_proportionally_reduced_and_redirected_to_cash` |
| 拒绝/缩减原因 | `APPROVE` / `REDUCE` / `PARKED` + `reason_codes` | `test_bad_input_fails_closed_as_parked`、`test_research_risk_assessment_is_a_no_order_status_only_contract` |
| 同底层重叠 / 有效敞口 / 换手 | look-through `underlying`、exposure factor、`max_one_way_risk_turnover` | `test_look_through_overlap_caps_tqqq_and_qqqm_together`、`test_one_way_risk_turnover_is_a_first_class_cap` |
| 整数股/小账户提示（不改信号） | `account_sizing` 仅 diagnostics 警告 | `tests/test_account_sizing.py` |
| 证据 digest / 身份 | `combo_evidence_aggregation` 绑定 `candidate_id` + digests | `tests/test_combo_evidence_aggregation.py` |
| no_order / 无晋级 | 聚合与 daily consumer 固定 `execution_authorized=false`、`promotion_authorized=false` | `tests/test_daily_combo_evidence.py` |

**C1 剩余边界（不阻塞本批研究验收）：**

- “可执行计划 + 成交/拒绝事实”的账户闭环属平台 RiskEngine / C4，不在本仓研究模块内造第二套 OMS。
- 成本与时点口径由上游冻结输入 / runner 绑定；`portfolio_risk_budget` 本身不读行情、不写成本模型。
- 调用方须自行保留 raw target 与 assessment 输出的并列证据；assessment 不回写账户。

## C2 证据映射

| 验收点 | 现有实现 | 锁定测试 |
|---|---|---|
| ≥2 候选同政策合并 | `research/virtual_combo_targets.construct_virtual_combo_target`（至少两 sleeve、预算和为 1） | `tests/test_virtual_combo_targets.py` |
| 资金守恒 / 策略预算上限 | 预算超限或非 fully-funded → PARKED；缩放后权重转入现金 | `test_fails_closed_when_a_strategy_exceeds_its_frozen_budget_limit`、gross/correlation REDUCE 用例 |
| 身份 / 政策篡改 fail-closed | frozen target / policy digest 失配 → PARKED | `test_fails_closed_if_a_frozen_target_or_policy_is_mutated` |
| 成分证据不合格 PARK | `aggregate_combo_evidence` 要求 `evidence_valid` + 资格状态 | `test_ineligible_component_parks_and_preserves_identity_refs` |
| 口径不一致 PARK | 可选字段：`as_of`、`quote_currency`、`capital_basis_digest`、`cost_model_digest`、`risk_policy_digest`、`data_scope_digest`；任一方声明后须全集一致 | `test_mismatched_as_of_parks_for_c2_comparability`、`test_partial_comparability_declaration_parks_incomplete` |
| 对齐日期的双策略研究先例 | `research/r3_joint_evidence`（TQQQ/SOXL 独立基线 + 成本情景） | `tests/test_r3_joint_evidence.py` |
| shadow 配置不接 live | `configs/us_equity_combo_leveraged_shadow_*.json`：`live_enable_candidate=false` | 配置契约阅读；策略入口不读账户下单 |

**C2 剩余边界：**

- 未声明可比字段时，聚合仍可 `READY_RESEARCH_ONLY`（兼容旧单字段工件）。**比较级研究**应声明完整可比字段，否则不得拼比较分数。
- 共享标的/因子/账户资源的“生产账户级”占用检查属 C4；本批只保证研究目标与 digests 不混比。
- 旧派生收益回放（`legacy_combo_derived_returns_replay`）仅探索假设，不是 C2 晋级证据。

## Shadow / 权限不变量

- 研究输出：`execution_authorized=false`；聚合另含 `promotion_authorized=false`。
- Shadow JSON：`promotion_state.live_enable_candidate=false`，不改变默认策略或 live 部署。
- 本批不接生产配置、平台 workflow、账户、凭据、通知、部署或真实行情。

## 建议的下一最小缺口（非本批写集）

1. C3：在固定成员预算基线下做离线风险预算比较，并显式校验换手成本与流动性约束。
2. 若比较级入口要强制口径：在 daily/record producer 侧默认写入六项可比字段，而不是在无字段旧工件上硬性 PARK。
3. C1 账户侧：平台禁止提交周期内把 raw/recommended/RiskEngine 拒绝绑到同一 intent 身份（UES 外）。
