# Batch2 最小研究候选与经济合同

任务 B2A-CONTRACT。本文只定义可复用的研究候选和经济记账边界。它不填写真实费率、仓位、历史收益，也不授予纸面、影子或实盘权限。

起草时读到的锚点：UES 分支 `qsl/soxl-cost-model-vps-20260924` 的 ref 为 `1a9d2985d34a4d7e17fc41c0e39528476105948a`；QPK `main` 的 ref 为 `c3dcf473c517853342cc893bb79d141e90208d8d`。会话开始时工作区已有两份未提交成本补丁：`src/us_equity_strategies/research/optimized_strategy_replay.py` 与 `tests/test_optimized_strategy_replay.py`。本批不修改它们。下文里的 replay 行为来自当时读到的工作区文本。干净 HEAD 与含这两份差异的工作区是两套代码身份。

主审计 §9.15.61–.80 与 `QSL_SYSTEM_ARCHITECTURE.md` 在本机不可见。本文不声称已补读。

## 1. 顶层成员

Batch2 顶层只有两个成员，各自是一条完整策略：

| 成员 | manifest | `required_inputs` | manifest 声明的 `managed_symbols` |
| --- | --- | --- | --- |
| SOXL 策略 | `soxl_soxx_trend_income` | `derived_indicators`、`portfolio_snapshot` | `SOXL`、`SOXX`、`BOXX`、`SCHD`、`DGRO`、`SGOV`、`SPYI`、`QQQI` |
| TQQQ 策略 | `tqqq_growth_income` | `benchmark_history`、`portfolio_snapshot` | `TQQQ`、`QQQM`、`BOXX`、`SCHD`、`DGRO`、`SGOV`、`SPYI`、`QQQI` |

TQQQ manifest 还声明 `benchmark_symbol = QQQ`。SOXL 通道是派生指标，replay 在调用方给出配置后才确定具体指标名：趋势标的上的 `price` 与 `ma_trend` 始终需要；RSI、布林和已实现波动只在对应开关打开时成为必需项。TQQQ replay 要求信号日及其之前的 `QQQ` K 线，可见根数随波动降杠杆窗口变化，且拒绝把 `benchmark_symbol` 改成其他代码。

这两份 manifest 的 `default_config` 还展开了收入层、期权覆盖，以及市场状态控制；TQQQ 另含 AI 扩展、危机防御和波动降杠杆相关开关。`income_layer_default_config` 与 `option_overlay_default_config` 的键清单本次未逐项展开，记为 `DECLARED_BY_HELPER`。`BOXX`、收入层证券、期权和这些插件留在成员范围内。fixture 若遇到未模拟的期权或目标插件会失败关闭（`UNSIMULATED_OPTION_OVERLAY`、`UNSIMULATED_TARGET_PLUGIN`），这只说明该 runner 不能假装已经交易了它们。

`managed_symbols` 是 manifest 文本里的声明全集。实际优化参数、开关取值和生产版本均为 `UNKNOWN`。`ResearchTrialRecord` 在参数未知时保持 `actual_params = null`，禁止用 manifest 默认值、V7 冻结档或 typed baseline 版本填成“已优化”。`soxl_soxx_core_only_p2_v7` 是另一条研究 manifest，不替代本成员。单个 ETF 的买入持有不是本候选的成员。

组合外壳复用 `historical_combo_p2_candidate`：两条 leg 的 `strategy_id` 分别为上述 profile，`research_only` 为真，`candidate_state` 为 `FROZEN_RESEARCH_CANDIDATE`，`promotion_recommendation` 为空，`p4_paper_authorized`、`p5_shadow_authorized`、`p6_live_authorized` 均为假。leg 权重在声明前是 `UNKNOWN`，不得写入一条“最佳权重”候选。

## 2. 冻结身份

一套经济身份至少绑定下列材料，再做规范 JSON 的 SHA-256，称为 `economic_identity_sha256`：

- UES 与 QPK 的代码 revision。工作区若含未提交差异，还要绑定该差异摘要；只写 HEAD 不能代表含成本补丁的文本。
- `domain = us_equity` 与一个 `strategy_profile`。
- 完整运行参数及开关摘要的摘要。参数未知时该槽为 null，不发行“优化历史已完成”的 digest。
- 输入摘要、`window_start` / `window_end`、`calendar_id`、`periods_per_year`。
- `cost_source` 与成本输入摘要。来源未知就停，不把 `TYPED_BASELINE_ZERO` 写成真实费率来源。
- 执行合同：信号在交易日 t 已知，成交在下一交易日 t+1，净值按 t 或 t+1 当日收盘标记。`signal_effective_after_trading_days` 固定为 1，`execution_timing_contract` 为 `next_trading_day`，`nav_mark_field` 为 `close`。t+1 的成交字段（`open` 或 `close`）属于身份的一部分。
- 复权 / 公司行为合同与现金合同。两者目前都是 `UNKNOWN`，见第 4 节。

任一绑定字段不同，`economic_identity_sha256` 必须不同。QPK `PerformanceStore._research_key` 只对 `domain`、`strategy_profile`、`trial_id` 做 SHA-256，用作 `research_trial/{digest}/` 的存储定位。它不是经济身份。同一存储槽只能保存同一 trial 的一致对象；内容冲突时现有存储拒绝（`research_trial_conflict`）。定位 digest 不得拿去发表第二套参数、成本或区间。

`optimized_strategy_replay` 的 `run_id` 覆盖 profile、参数集、单一 `source_revision`、日历、有效配置、初始现金与数量、输入、`PromotionCostModel` 和执行字段。它可以作为 fixture 运行摘要。它还没有同时绑定 UES 与 QPK 两套 revision，也没有复权合同，因此不能直接充当本节的经济身份。

QPK 身份字段拒绝 `unknown`、`default`、`none`、`null`、`na`、`n/a` 这类占位字符串。文档里的 `UNKNOWN` 是停笔结论，不是写入这些字段的值。

## 3. 三类证据，不可改标记晋级

| 证据类 | 现有入口 | 本候选中的地位 |
| --- | --- | --- |
| `batch_a_v2` | `batch_a_member_pack` | 可复用校验与 digest，不是完整优化历史 |
| `fixture_replay` | `optimized_strategy_replay` | 可复用 staging、`ResearchTrialRecord`、`ResearchDailyLedger`、`PerformanceStore` |
| `optimized_history` | 尚无满足第 2、4 节的对象 | 唯一可以称为完整优化历史的类别；当前不可发行 |

`qsl.c3-batch-a-frozen-member-pack.v2` 用 typed baseline 从 `SOXX`/`SOXL` 与 `QQQ`/`TQQQ` 价格快照生成 `soxl_core`、`tqqq_core` 和 `cash_sleeve`。成本场景是 `TYPED_BASELINE_ZERO`，现金收益政策是 `ASSUMED_ZERO_USD_CASH`，现金日收益被要求为零。包保持 `research_only`，且 `execution_authorized` 为假。它证明的是这条 SMA200 typed 基线在给定快照上的策略日收益序列，并排除预热期；它不证明第 1 节的完整成员，也不覆盖 `BOXX`、收入层、期权和插件。

`replay_optimized_strategy` 的模块说明写明：它直接调用风险门前的决策构造器，供合成账本记录目标；`evidence_use` 必须为 `fixture`，`promotion_eligible` 必须为假，结果里 `live_executable` 与 `risk_gate_applied` 均为假。`persist_optimized_strategy_trial` 可以写入 `SUCCEEDED`，同时 `synthetic` 仍为真。QPK 注明合成存储不是授权，终态本身也不晋级。把 `synthetic`、`promotion_eligible` 或 P2 的推荐字段改成另一个标记，不改变证据类。

`compare_fixed_member_budget_baselines` 只重组已经对齐的成员日收益。它不优化权重。成功输出保持 `execution_authorized`、`promotion_authorized` 为假，`no_order` 为真。失败路径返回 `status = PARKED`。

## 4. 每日账本：沿用 QPK，不另起一套

研究账本只使用 `ResearchDailyLedger` / `ResearchLedgerDay` / `ResearchPositionMark`。不新增第二套账本类型。

已覆盖、且构造时强制闭合的关系：

- 期初会话有 `initial_cash`、`initial_positions`（代码、数量、估值）、`initial_nav`。该日不是收益观测。
- 其后每个会话有期末 `cash`、期末持仓数量与估值、`trade_net_cashflow`、`fees`、`nav`、`daily_return`。
- `nav = cash + 持仓估值之和`（绝对误差 `1e-9`）。
- 期末现金 = 上一日现金 + `trade_net_cashflow` − `fees`。
- `daily_return = 当日 nav / 上一日 nav − 1`，要求精确相等。
- 数量为零当且仅当估值为零；`fees >= 0`；`nav > 0`；会话日期严格递增；`cost_inputs` 非空。
- `synthetic` 显式保存。fixture 路径写 `true`。

由此可以重建的只有：上一日期末现金视为次日期初现金，上一日期末持仓视为次日期初持仓。这个重建没有单独的期初字段。

当前类型没有、因此不能声称已入账的项目：

| 经济事项 | 停笔结论 |
| --- | --- |
| 参考价与成交价分列 | `LEDGER_PRICE_FIELDS_ABSENT`。replay 内部用参考价定数量，用滑点加冲击恶化成交价，但标记只有收盘估值 |
| 佣金与不利成交分列 | `LEDGER_COST_SPLIT_ABSENT`。`fees` 在 replay 里是佣金；不利成交进入 `trade_net_cashflow` |
| 外部出入金 | `EXTERNAL_FLOW_UNKNOWN`。现金等式没有外部现金流字段 |
| 拆股、分红、复权 | `CORPORATE_ACTION_UNKNOWN`。replay 缺口含 `NO_CORPORATE_ACTIONS` |
| 现金利息或现金合同 | `CASH_CONTRACT_UNKNOWN`。`ASSUMED_ZERO_USD_CASH` 只属于 Batch A 现金袖 |

上述任一事项仍为未知时，结论停在 `LEDGER_INCOMPLETE_FOR_OPTIMIZED_HISTORY`。允许保存带 `synthetic = true` 的 fixture trial；不允许把该账本登记为 `optimized_history`。`SUCCEEDED` 还要求真实的 `actual_params`，所以参数未知时连成功的研究 trial 也不能闭合。

费用只计一次。成员日收益若已经按该成员的 `cost_model_digest` 计入内部佣金和不利成交，组合层不得再按同一成交重收。`c3_fixed_budget_baseline_comparison` 要求 `member_costs_already_embedded` 为真，并记下 `member_costs_recharged = false`。组合 `capital_path` 上的再平衡费用是另一层增量，而且只是调仓前名义换手近似，不是逐笔现金成交账本。整数股、现金留存、杠杆和流动性闸门在该比较里保持未计算或未授权。

## 5. 回撤转换与收益分列

现有 `max_drawdown`（replay 的 `BacktestResult`，以及 C3 `_metrics`）按 `nav / peak − 1` 取运行最小值，因而 `<= 0`。报告边界使用一对纯转换，不另建指标库：

- `drawdown_signed = nav / peak − 1`，要求 `drawdown_signed <= 0`。
- `drawdown_magnitude = -drawdown_signed`，要求 `drawdown_magnitude >= 0`。
- 写入 `max_drawdown` 的值是 `drawdown_signed`。幅度只在需要非负回撤的下游出现，例如 replay 仅在 `max_drawdown < 0` 时用其绝对值计算 Calmar。

峰值或净值未知时不做转换，不补零。

收益分三列，互不覆盖：

1. **声明预算路径**：C3 默认指标，`metrics_basis = RAW_FIXED_MEMBER_BUDGETS_UNSCALED`，未做组合风险缩放或组合调仓收费；输入成员收益是否已扣成员成本，由其成本身份决定，这里的“原始”不等于毛收益。
2. **风险缩放路径**：仅当 `capital_path_options.apply_risk_scaling` 为真且缩放输入齐全时，使用缩放后预算重算收益，不拿默认指标代替。
3. **费用后路径**：先说明成员收益已包含的成本，再单列组合调仓的增量费用；缺少适用费率时不计算真实净成本。Batch A 的 `TYPED_BASELINE_ZERO` 只是零成本基线。

`capital_path` 的 `fee_fractions`、`one_way_turnovers` 与 `pre_trade_fee_notionals` 描述名义换手近似。它们不提供第 4 节的现金、成交价和公司行为。

## 6. 本地离线验收与 PARKED

验收不采集行情、不读配置或凭据、不交易、不部署。只核对契约是否自洽。

| 检查 | 通过 | PARKED |
| --- | --- | --- |
| 成员范围 | 两腿分别指向第 1 节的完整 manifest，并保留 `BOXX`、收入层、期权与插件声明 | 成员被收成单标的买入持有，或静默删掉上述范围 |
| 参数 | `actual_params` 为调用方完整对象，或在未知时保持 null | 用默认值、V7 或 typed baseline 冒充优化参数或生产版本 |
| 身份 | 第 2 节字段齐全，且不同身份的 `economic_identity_sha256` 不同 | `IDENTITY_INCOMPLETE` 或 `IDENTITY_DIGEST_COLLISION` |
| 证据类 | 类别是 `batch_a_v2`、`fixture_replay` 或尚不可发行的 `optimized_history` | 仅修改标记后声称晋级 |
| 账本 | 第 4 节已覆盖的等式成立；缺口保持具名 | 把缺口账本称为完整优化历史 |
| 费用 | 成员内部费用与组合增量费用只计一次；组合费用标明名义换手 | `FEE_DOUBLE_COUNT`，或把 `capital_path` 当成现金成交账本 |
| 回撤 | 有符号回撤 `<= 0`，幅度 `>= 0`，转换关系成立 | 符号混用 |
| 权限 | 研究只读、执行与晋级标志保持关闭 | 任一执行或晋级标志为真 |

直接 PARKED 的原因码：`ACTUAL_PARAMS_UNKNOWN`、`PRODUCTION_VERSION_UNKNOWN`、`ADJUSTMENT_CONTRACT_UNKNOWN`、`CASH_CONTRACT_UNKNOWN`、`EXTERNAL_FLOW_UNKNOWN`、`CORPORATE_ACTION_UNKNOWN`、`BATCH_A_V2_NOT_OPTIMIZED_HISTORY`、`FIXTURE_REPLAY_NOT_OPTIMIZED_HISTORY`、`SYNTHETIC_LEDGER_NOT_PROMOTABLE`、`LABEL_ONLY_PROMOTION_REJECTED`、`LEDGER_INCOMPLETE_FOR_OPTIMIZED_HISTORY`、`FEE_DOUBLE_COUNT`、`IDENTITY_DIGEST_COLLISION`、`CAPITAL_PATH_NOT_CASH_LEDGER`。

当前可执行的离线结论是：第 1 节成员范围和第 4 节 QPK 闭合关系可以陈述；完整优化历史停在 `LEDGER_INCOMPLETE_FOR_OPTIMIZED_HISTORY`，因为实际参数、生产版本、复权合同、现金合同、外部出入金和公司行为仍是 `UNKNOWN`。

## 7. 已落地的身份入口

`build_optimized_member_identity` 与 `validate_optimized_member_identity` 在 `us_equity_strategies.research.optimized_member_identity`。调用方自行提交 UES 与 QPK 的 40 位 revision、工作区有补丁时的 64 位补丁摘要、非占位参数对象、配置与输入摘要、窗口、日历、非负成本，以及 t 信号、t+1 成交、收盘估值。模块不读 git，不生成日收益，也不写 `ResearchDailyLedger`。

校验器要求配置摘要与提交的参数对象实际匹配；每年期数沿用 QPK 账本支持的 252 或 365.25。它仍无法证明调用方提交的是完整的真实优化参数，来源验证留给历史生产者。

输出 schema 为 `qsl.us-equity-optimized-member-identity.v1`，`economic_identity_sha256` 是去掉自身后的规范 JSON SHA-256。`research_only` 为真，执行与晋级为假，`evidence_scope` 为 `IDENTITY_ONLY_NO_HISTORICAL_RETURNS`。复权、现金、公司行为、外部现金流和股数只保存声明，`contract_proof` 固定为 `DECLARATION_ONLY`，不能证明来源真实。typed SMA200、Batch A v2 与 fixture replay 的 schema 或 `evidence_use` 在这里会被拒绝。

仍缺 trial 生产者，以及账本上的参考价/成交价分列、佣金与不利成交分列、外部出入金和公司行为事件字段。因此该对象不是完整优化历史，也不是生产等价。合成身份测试通过同样不表示历史有效。
