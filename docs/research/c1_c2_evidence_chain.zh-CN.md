# C1/C2/C3 组合复利研究证据链（research/shadow-only）

来源：`AGENTS.md`、系统架构 §4/§5 组合与研究边界、审计报告 §9.9 / §9.12 / §9.14 / §9.15.17。
本文件固定 **UsEquityStrategies 研究层** 对 C1–C3 的覆盖与剩余边界；不授予 paper/shadow/live，也不承诺最大 CAGR。

## 结论

| 阶段 | 研究验收（当前） | 说明 |
|---|---|---|
| C1 单策略/候选目标闭环 | **研究积木已达标** | raw 目标不因风险缩放被改写；`recommended_target_weights` + `reason_codes` 可追溯；`execution_authorized=false` |
| C2 可比较成员 | **研究积木已达标（声明式口径）** | ≥2 成员可在同一政策/预算下合并；身份/资格冲突 PARK；**一旦声明** as_of/币种/资金/成本/风险/数据 digest，不一致或缺省即 PARK |
| C3 固定成员预算基线比较 | **研究积木已达标（离线固定预算）** | ≥2 成员对齐收益 + ≥2 声明固定预算；输出净值/回撤/波动/尾部代理；可选换手声明与风险快照；不自动优化 |
| C4–C5 | 不在本批 | 账户 shadow 零提交、人工启用分别后续 |

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
- 旧派生收益回放（`legacy_combo_derived_returns_replay`）仅探索假设，不是 C2/C3 晋级证据。

## C3 证据映射

| 验收点 | 现有实现 | 锁定测试 |
|---|---|---|
| ≥2 冻结成员 + ≥2 声明固定预算 | `research/c3_fixed_budget_baseline_comparison.compare_fixed_member_budget_baselines` | `tests/test_c3_fixed_budget_baseline_comparison.py` |
| 不自动优化 / 不 Kelly / 不分数配权 | 仅按声明 `member_budget_weights` 线性再组合；`boundaries.optimization=DISABLED_FIXED_BUDGETS_ONLY` | `test_compares_two_fixed_budgets_without_authorizing_execution` |
| 对齐日期与完整可比口径 | 成员必须提供六项 C2 可比字段且完全一致；日期序列必须逐日对齐 | `test_mismatched_comparability_parks_fail_closed`、`test_date_alignment_mismatch_parks`、`test_incomplete_comparability_fields_park` |
| 资金守恒 | 每个基线预算必须覆盖同一成员集合且和为 1 | `test_budget_not_fully_funded_parks` |
| 净值/收益、回撤、波动、尾部代理 | `metrics.terminal_nav` / `cumulative_return` / `max_drawdown` / `annualized_volatility` / `tail_loss_proxy_min_daily_return` | 正例断言 |
| 换手声明完整性 | `declared_one_way_turnover` 要么全基线声明，要么全不声明 | `test_partial_turnover_declaration_parks` |
| 集中度/同底层（可选快照） | 复用 `assess_portfolio_risk_budget`；诊断 PARK 则整体 PARK | `test_concentration_snapshot_reuses_portfolio_risk_budget` |
| 成本口径 | 不二次扣费；绑定共享 `cost_model_digest`（`EMBEDDED_IN_MEMBER_RETURNS_VIA_COST_MODEL_DIGEST`） | 边界字段断言 |
| 权限不变量 | `research_only` / `shadow_only` / `execution_authorized=false` / `promotion_authorized=false` / `no_order=true` + member/baseline/policy digests | 正例与 PARK 用例 |

**C3 诚实边界（已标记，不偷偷放松）：**

- 整数股 / 小账户：`integer_share_sizing=DIAGNOSTIC_ONLY_NOT_COMPUTED`（继续依赖 `account_sizing` 诊断，不在此重算可执行股数）。
- 现金预留 / 杠杆扩大：`cash_reserve_enforcement` 与 `leverage_expansion` 明确未计算、未授权。
- 实时流动性闸：`live_liquidity_gates=NOT_COMPUTED`。
- 成本与换手：本模块不重建成交账本；成员收益须已按声明成本模型冻结。部分声明换手 → PARK。
- `legacy_combo_derived_returns_replay` 仍是探索性派生收益回放，**不能**替代本 C3 模块作为可比基线证据。

## Shadow / 权限不变量

- 研究输出：`execution_authorized=false`；聚合与 C3 另含 `promotion_authorized=false`、`no_order=true`。
- Shadow JSON：`promotion_state.live_enable_candidate=false`，不改变默认策略或 live 部署。
- 本批不接生产配置、平台 workflow、账户、凭据、通知、部署或真实行情。

## 建议的下一最小缺口（非本批写集）

1. C4：单一组合 consumer 读取实际账户/在途订单与 RiskEngine，产出零提交目标与组合风险贡献证据。
2. 若要强制比较级入口默认写入六项可比字段：在 daily/record producer 侧补齐，而不是在无字段旧工件上硬性 PARK。
3. 若现有模块外需要“按约束再搜索预算”：仅在固定基线比较证明不足后另开研究，仍禁止全 Kelly / 分数直接配权 / 扩大杠杆。
