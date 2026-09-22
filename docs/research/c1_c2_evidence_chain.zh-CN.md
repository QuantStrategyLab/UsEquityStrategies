# C1–C4 组合复利研究证据链（research/shadow-only）

来源：`AGENTS.md`、系统架构 §4/§5 组合与研究边界、审计报告 §9.9 / §9.12 / §9.14 / §9.15.17–§9.15.21。
本文件固定 **UsEquityStrategies 研究层** 对 C1–C4 的覆盖与剩余边界；不授予 paper/shadow/live，也不承诺最大 CAGR。

## 结论

| 阶段 | 研究验收（当前） | 说明 |
|---|---|---|
| C1 单策略/候选目标闭环 | **研究积木已达标** | raw 目标不因风险缩放被改写；`recommended_target_weights` + `reason_codes` 可追溯；`execution_authorized=false` |
| C2 可比较成员 | **研究积木已达标（强制完整口径）** | ≥2 成员可在同一政策/预算下合并；身份/资格冲突 PARK；**必须**提供完整 as_of/币种/资金/成本/风险/数据 digest，缺证据、非法值或不一致即 PARK |
| C3 固定成员预算基线比较 | **研究积木已达标（离线固定预算）** | ≥2 成员对齐收益 + ≥2 声明固定预算；输出净值/回撤/波动/尾部代理；**metrics 仅代表原始固定预算、未应用风险缩放/未重建再平衡费用**；可选换手声明与风险快照；不自动优化 |
| Batch A（现有 SOXL/TQQQ+现金基线） | **PARKED / 证据不足** | C3 consumer 与 gate 已接线；本 worktree 缺冻结对齐日收益与现金序列，拒绝编造；见下文 Batch A 节 |
| C4 Shadow/禁止提交周期 | **研究积木已达标（物化快照 consumer）** | 只读已物化账户/在途订单/RiskEngine；FRESH 必须绑定可解析、带时区、非未来的 UTC 时点且账户/订单/风险时点同一瞬间；成功态可验证零提交；缺漏/过期/不一致/未知订单/非 APPROVE/`execution_authorized=true` → PARKED |
| C5 | 不在本批 | 人工批准后的有限启用仍后续 |

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

- 真实“可执行计划 + 成交/拒绝事实”的账户闭环仍属平台 RiskEngine/执行链；本仓 C4 仅消费已物化快照并产出零提交 shadow 证据，不造第二套 OMS。
- 成本与时点口径由上游冻结输入 / runner 绑定；`portfolio_risk_budget` 本身不读行情、不写成本模型。
- 调用方须自行保留 raw target 与 assessment 输出的并列证据；assessment 不回写账户。

## C2 证据映射

| 验收点 | 现有实现 | 锁定测试 |
|---|---|---|
| ≥2 候选同政策合并 | `research/virtual_combo_targets.construct_virtual_combo_target`（至少两 sleeve、预算和为 1） | `tests/test_virtual_combo_targets.py` |
| 资金守恒 / 策略预算上限 | 预算超限或非 fully-funded → PARKED；缩放后权重转入现金 | `test_fails_closed_when_a_strategy_exceeds_its_frozen_budget_limit`、gross/correlation REDUCE 用例 |
| 身份 / 政策篡改 fail-closed | frozen target / policy digest 失配 → PARKED | `test_fails_closed_if_a_frozen_target_or_policy_is_mutated` |
| 成分证据不合格 PARK | `aggregate_combo_evidence` 要求 `evidence_valid` + 资格状态 | `test_ineligible_component_parks_and_preserves_identity_refs` |
| 口径不一致 / 缺证据 PARK | **强制**六项：`as_of`（ISO 日期）、`quote_currency`（`^[A-Z]{3}$`）、`capital_basis_digest`、`cost_model_digest`、`risk_policy_digest`、`data_scope_digest`；共同缺失、部分声明、非法 digest/时点/币种或不一致 → PARK | `test_jointly_missing_comparability_fields_park`、`test_shared_partial_comparability_subset_parks_incomplete`、`test_mismatched_as_of_parks_for_c2_comparability`、`test_illegal_as_of_or_currency_parks_incomplete` |
| 对齐日期的双策略研究先例 | `research/r3_joint_evidence`（TQQQ/SOXL 独立基线 + 成本情景） | `tests/test_r3_joint_evidence.py` |
| shadow 配置不接 live | `configs/us_equity_combo_leveraged_shadow_*.json`：`live_enable_candidate=false` | 配置契约阅读；策略入口不读账户下单 |

**C2 剩余边界：**

- 缺六项可比字段即 PARK（`COMPONENT_COMPARABILITY_MISSING` / `INCOMPLETE`）；不再把“共同未声明”当作可研究兼容路径。
- 共享标的/因子/账户资源的“生产账户级”占用检查：C4 只校验已物化身份/时点/digest 与零提交不变量，不新建账户级 allocator。
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
| 风险缩放 / 再平衡费用 | **默认** raw metrics：`metrics_basis=RAW_FIXED_MEMBER_BUDGETS_UNSCALED`；`risk_scaling_applied_to_returns=NOT_APPLIED`；`rebalance_fee_reconstruction=NOT_COMPUTED`。可选 `capital_path_options`（显式 `apply_risk_scaling` / `cash_member_id` / `rebalance_fee_bps` / `rebalance_indices`）才在 `capital_path` 块内计算漂移、增量组合费用与缩放后现金/敞口；成员成本不二次扣除 | `tests/test_c3_fixed_budget_baseline_comparison.py`、`tests/test_c3_capital_path.py` |
| 权限不变量 | `research_only` / `shadow_only` / `execution_authorized=false` / `promotion_authorized=false` / `no_order=true` + member/baseline/policy digests | 正例与 PARK 用例 |

**C3 诚实边界（已标记，不偷偷放松）：**

- 整数股 / 小账户：`integer_share_sizing=DIAGNOSTIC_ONLY_NOT_COMPUTED`（继续依赖 `account_sizing` 诊断，不在此重算可执行股数）。
- 现金预留 / 杠杆扩大：`cash_reserve_enforcement` 与 `leverage_expansion` 明确未计算、未授权。
- 实时流动性闸：`live_liquidity_gates=NOT_COMPUTED`。
- 成本与换手：默认路径不重建成交账本或再平衡费用；成员收益须已按声明成本模型冻结。`declared_one_way_turnover` 只是声明，不改写 raw metrics。部分声明换手 → PARK。
- 可选资本路径：仅当调用方显式提供组合层 `rebalance_fee_bps` 与 `rebalance_indices` 时才重建**增量**组合再平衡费用；缺任一输入则 PARK 或保持 `NOT_COMPUTED`，不用 `declared_one_way_turnover` 冒充费率。成员净收益路径下禁止再扣成员成本。
- 集中度诊断给出的 `risk_scalar` / `recommended_target_weights` **不得**读成默认 raw metrics 的已实现缩放收益；仅在 `capital_path_options.apply_risk_scaling=true` 且提供现金成员时写入 `capital_path`。
- `legacy_combo_derived_returns_replay` 仍是探索性派生收益回放，**不能**替代本 C3 模块作为可比基线证据。

## C4 证据映射

| 验收点 | 现有实现 | 锁定测试 |
|---|---|---|
| 仅消费已物化快照 | `research/c4_shadow_zero_submit_cycle.consume_c4_shadow_zero_submit_cycle`；不访问 broker/网络/凭据/workflow | `tests/test_c4_shadow_zero_submit_cycle.py` |
| 版本/时点/digest 绑定 | 账户 `account_digest`、订单 `orders_digest`、RiskEngine `assessment_digest` + 共享 `account_id`/`strategy_id`；`as_of` 必须可解析、带时区、非未来，并归一为同一 UTC 瞬间（`as_of_relation=ACCOUNT_ORDERS_RISK_IDENTICAL_UTC`）；`freshness=FRESH` | 正例、mismatch/stale、`test_fresh_snapshot_rejects_illegal_naive_or_future_as_of`、`test_account_and_orders_as_of_must_share_explainable_instant` |
| 未知订单 / 非 APPROVE / 执行授权语义 | 订单 `outcome` 必须在已知集合；RiskEngine 必须 `status=APPROVE` 且 `execution_authorized=false`，否则 PARKED | `test_unknown_order_outcome_parks`、`test_risk_engine_reject_or_execution_authorized_true_parks` |
| 可验证零提交 | 成功与 PARK 均固定 `proposed_orders=[]`、`submission_attempted=false`、`execution_permitted=false`、`no_order=true` | 正例与 fail-closed 用例 |
| 风险贡献复用 | 可选研究目标权重走既有 `assess_portfolio_risk_budget`；推荐权重不进入 `proposed_orders` | `test_research_weights_never_become_orders_even_when_reduced` |
| 权限不变量 | `research_only` / `shadow_only` / `execution_authorized=false` / `promotion_authorized=false` + account/orders/risk/member/policy/input digests | 正例断言 |

**C4 诚实边界（已标记，不偷偷放松）：**

- 整数股 / 小账户、现金预留、杠杆扩大、实时流动性：与 C3 相同，标记未计算/未授权。
- 真实成交账本：`fill_ledger_reconstruction=NOT_COMPUTED_REQUIRES_PLATFORM_FACTS`；本模块不重建 fills。
- 输入必须由上游平台/控制面物化；本 consumer 不拉账户、不下单、不重试。
- 研究 `APPROVE`、影子建议或目标权重均不授予执行权；C5 人工启用不在本批。

## Shadow / 权限不变量

- 研究输出：`execution_authorized=false`；聚合与 C3/C4 另含 `promotion_authorized=false`、`no_order=true`。
- C4 另固定可验证零提交字段：`proposed_orders=[]`、`submission_attempted=false`、`execution_permitted=false`。
- Shadow JSON：`promotion_state.live_enable_candidate=false`，不改变默认策略或 live 部署。
- 本批不接生产配置、平台 workflow、真实账号、凭据、通知、部署或真实行情。

## Batch A（§9.15.21 + §9.15.27）：现有 SOXL/TQQQ + 含现金固定预算基线

来源：审计 §9.9 / §9.15.18 / §9.15.20–§9.15.21 / §9.15.27。目标是用冻结成员证据验证 C3 比较能否形成第一版可复核研究结果；不新增策略/优化器。

| 项 | 当前结论 |
|---|---|
| C3 离线积木 | 已达标（见上节） |
| 数据合同 | **v2 唯一新入口**：见 `docs/research/batch_a_v2_contracts.zh-CN.md` |
| Batch A 端到端研究案例 | **PARKED / 等待获准云端 v2 物化**（无真实采集/GCS 读写前不得宣称业务解除） |
| Consumer | `research/c3_batch_a_existing_member_baseline.evaluate_batch_a_existing_member_baselines` |
| 物化 | `batch_a_dataset` / `batch_a_member_pack` / `scripts/run_batch_a_from_gcs.py` |
| CLI | `scripts/run_c3_batch_a_existing_member_baseline.py` |
| 锁定测试 | `tests/test_c3_batch_a_existing_member_baseline.py`、`tests/test_batch_a_v2_contracts.py` |

**活动边界：**

1. 只接受 `qsl.c3-batch-a-frozen-member-pack.v2`；拒绝 v1 与自动转换。
2. 现金腿仅为 `ASSUMED_ZERO_USD_CASH`；代表权重使用 `USD_CASH`，不把 BOXX 当现金。
3. 成员收益必须来自冻结策略路径（typed SMA200 + 声明成本模型），禁止标的涨跌幅冒充。
4. 旧 R3 `PRIVATE_ROOT` / `run_r3_from_gcs` 已退出 Batch A 活动入口；历史模块保留但不作为本 consumer 前置。
5. `legacy_combo_derived_returns_replay` 明确拒绝作为 Batch A 证据。

成功态仍固定 `research_only` / `shadow_only` / `execution_authorized=false` / `promotion_authorized=false` / `no_order=true`。合同测试可用显式 fixture pack 验证接线，**不**冒充已批准云端研究结果。

## 建议的下一最小缺口（非本批写集）

1. **D1 采集器**：在 `UsEquitySnapshotPipelines` 按 v2 合同采集 Alpaca SIP 并 create-only 写入 `research/v2/input/<dataset_id>/`。
2. **R1**：获准云端一次采集→锁定→物化→比较；首个材料失败不重采。
3. C5：仅对冻结成员、账户、总风险预算和再平衡规则做人工批准后的有限启用；仍由单一执行者与既有风控执行。
4. 若现有模块外需要“按约束再搜索预算”：仅在固定基线比较证明不足后另开研究，仍禁止全 Kelly / 分数直接配权 / 扩大杠杆。
