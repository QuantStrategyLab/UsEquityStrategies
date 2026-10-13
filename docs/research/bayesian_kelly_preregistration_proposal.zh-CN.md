# B4：贝叶斯 Kelly 后验更新的独立预注册草案

状态：**PROPOSAL / NOT_FROZEN**。任务为 [UES #588](https://github.com/QuantStrategyLab/UsEquityStrategies/issues/588)，总审计任务为 [QRS #632](https://github.com/QuantStrategyLab/QuantRuntimeSettings/issues/632)。用户新授权覆盖本轮 B4 研究准备和草案；本文件没有冻结模型、批准新试验、运行研究或生成收益。

排序保持为：**B1/B2 可靠对照与候选、holdout 隔离及累计 trial ledger 冻结 → 独立 B4 合成研究 → E1 独立晋级审查**。B1/B2 当前仍未冻结；允许先准备本草案，不据此提前实施或运行 B4。未来产物固定 `DEVELOPMENT_ONLY`、`live_ready=False`、`no_order=True`、`budget_effect=NONE`，只可作为封顶或降低既有预算的研究备注；不交易、不提高预算、不启用候选或访问真实研究数据。

## 依据与许可边界

- UES 阅读基线为 `26e77dad6e5d5d54a15c6d9fadd94d46b94c3a0f`：[RS-04 预注册](fractional_kelly_preregistration.zh-CN.md)、[评估器说明](fractional_kelly_walk_forward.zh-CN.md)、[RS-06 组合预注册](portfolio_kelly_preregistration.zh-CN.md)和[累计账本](kelly_trial_ledger.json)。这些是历史版本和授权记录，不能充当当前运行状态或 B4 许可。
- RS-04 的冻结候选、变体、数据窗口、manifest 和私有运行许可不转授给 B4。旧 `no_plugin_core` 也不等于完整实盘插件一致性；无法复现的插件、实际配置覆盖及部署版本仍需独立核实。已见历史仍为 development，不能改名成为 untouched holdout。
- 跨库依据为 QPK `f8d8b4045276ce9e0c491eefd8506c544c19575b` 的 [B1/B2 草案](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/b1_b2_preregistration_proposal.zh-CN.md)、[A2 成交与成本](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/research_execution_models_v1.zh-CN.md)、[A3 指标语义](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/window_metrics_semantics.zh-CN.md)、[B3 净收益报告](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/unified_net_report.zh-CN.md)和 [Kelly 单位合同](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/kelly_contract_v2.zh-CN.md)。这些版本提供测量依据，不自动成为 B4 运行依赖。
- 原 B1/B2 文档不包含 B4 的范围约束保持原文；B4 由本独立任务准备。这里不修改旧批准、参数、账本、生产 pin、配置、候选状态或 B5，也不做自动候选扫描。

## 研究问题与未决定项

拟回答：在相同已冻结候选、时点、现金、成本和风险限制下，用后验表达估计不确定性的研究 sizing，是否相对 B1/B2 的可靠对照改善净对数增长及风险表现？这不是“贝叶斯一定优于样本估计”的结论。

以下项目尚无绑定事实。**全部 `UNSPECIFIED`，不能以旧默认值、论文例子、实现便利或观察结果代选。**

| 冻结项 | 必须决定并绑定的内容 | 当前状态 |
| --- | --- | --- |
| 研究身份 | 正式 preregistration id、候选 profile / variant、单策略或组合范围、模型版本及候选完整列表 | UNSPECIFIED |
| 观测定义 | 频率、日历、时区、收益与现金口径、观察单位、决策可用时刻、数据 source / PIT 身份 | UNSPECIFIED |
| 先验 | 分布族、支持域、是否 proper、参数化、先验有效样本量及独立来源依据 | UNSPECIFIED |
| 似然 | 条件观测模型、相关性与尾部假设、参数单位、是否容许非平稳及模型适用范围 | UNSPECIFIED |
| 超参及数值方法 | 固定超参、拟合范围、解析或数值更新、精度与收敛条件；若使用随机方法，其算法、seed 和 RNG 版本 | UNSPECIFIED |
| 更新规则 | batch 或 incremental、更新频率、窗口、遗忘 / decay、重拟合、warmup 与折间状态延续 | UNSPECIFIED |
| 决策规则 | 使用后验或 posterior predictive 的方式、损失 / 效用函数、点估计与不确定性处理、fractional 系数 | UNSPECIFIED |
| 损失与单位 | 损失定义、尾部 / 破产支持、可信 `loss_fraction_per_unit` 是否需要及其来源；风险份额到资金暴露的换算 | UNSPECIFIED |
| 风险限制 | 已有作用域的 position / risk / cash / product cap、drawdown / breaker / veto 来源和批准摘要；不得新设更宽上限 | UNSPECIFIED |
| 数据与分折 | 合成生成过程与 seed、train / test / purge / embargo、现金腿、价格与公司行动口径、缺失及覆盖规则 | UNSPECIFIED |
| 时点与成本 | 信号截止、成交 timing、commission / slippage / 压力档、内部与外层成本对账 | UNSPECIFIED |
| 对照与指标角色 | 已冻结 B1/B2 行的精确版本，ranking / report / veto 角色、效应与统计阈值 | UNSPECIFIED |
| 异常与恢复 | 缺失、非有限、零方差、似然不可用、后验不正规 / 数值失败、破产、停放和恢复策略 | UNSPECIFIED |
| 隔离与审查 | holdout 管理人及边界、真实 ledger 计数口径、immutable runner / 依赖、owner 与独立 reviewer 绑定 | UNSPECIFIED |

先验的统计独立性、经验 Bayes / 超参拟合是否允许以及拟合数据边界必须单独决定。禁止在 holdout 或比较结果上调先验后，仍称其为预先注册；每种被允许的先验、似然、更新或敏感性组合都须显式列入候选和 trial 计数。

## 后验、因果时点与可审计状态

一般的 Bayes 身份为 `p(theta | D_t, M) ∝ p(D_t | theta, M) p(theta | M)`；这仅说明待研究关系。`M`、先验与似然均未选定，不隐含 iid、共轭形式或把 `mu / variance` 代入后验均值就是已批准的 Kelly 决策。

每次决策只能使用截止时刻已实际可用的 `D_t`。观察发生时间、发布时间 / 可用时间与决策时间分别记录；迟到公司行动、修订行情或上下文不能倒灌此前状态。样本边界、权重与观察 id 必须可追踪，不能使用尚未完结 bar 的完成值，也不能重复消费已经纳入后验的观察。

batch 与 incremental 只在**同一个冻结模型、同一有序数据、相同条件似然、窗口与权重规则**下讨论一致性。带遗忘、滚动窗口或自适应超参时，不默认等价；应先定义预期关系再验证。折间重置先验或携带后验的规则必须预先冻结，不得只保留盈利折的状态，或把同一训练数据同时作为 prior 拟合与 likelihood 更新而重复计算。

未来纯函数输出须允许重放审查：模型与参数身份、prior / posterior 状态、已消费观察及可用截止、batch / incremental 与窗口模式、数值诊断、状态版本和研究暴露的单位 / cap 来源。实际内容摘要在产物存在后生成；本草案不填写数据、后验、holdout、ledger 或 owner 批准 hash。状态序列化不得夹带账户凭据；不存在 checkpoint 时保持未知，不造一个成功状态。

缺失或异常不能补零、静默删亏损观察、替换预测器、用请求日期冒充来源日期或把更新失败伪成可信后验。错误须保留观察覆盖和原因，结果 `PARKED / UNCOMPUTABLE`；具体已有风险的现金处置、停放与恢复规则仍为 `UNSPECIFIED`，未冻结时不启动研究。不能把 posterior 标准差、方差或概率单位混用，不把收敛诊断当作经济优势。

后验给出的目标不得覆盖既有 hard gate、cash reserve、kill switch 或风险 veto。禁止本 B4 新增融资杠杆、做空暴露或 full Kelly 候选；不排除既定 B1/B2 范围中的杠杆 ETF 产品自身的杠杆。产品身份、fractional 系数与 cap 仍为 `UNSPECIFIED`，须来自冻结候选；fractional 只应用一次，不能重新归一化满仓。若使用 QPK 二元 Kelly 的 `bet_loss_risk_share`，不得直接当作 `capital_exposure`，也不得由平均亏损臆造硬损失上限。

## 对照、成交、成本与测量

B4 必须先绑定 B1/B2 的可靠对照版本及完整测量合同；不把尚未冻结的 B1/B2 草案当作运行基线。RS-04 的旧参数只是历史依据，不自动复制成 B4 参数。各比较行须用相同会话、可用信息、fold、现金、初始 NAV、成本与作用域风险限制；不静默取日期交集或删除不可计算行。

沿用已审查的 A2 语义：completed-close 信号不能声称在同一收盘前成交；`same_close` 仅是显式假设，`next_close` / `next_open` 的选择及是否做敏感性比较仍未冻结。不 forward-fill 成交价，记录 terminal 未成交；实际 terminal fill 的费用计在真实成交时点，不因移动到区间外而遗漏。

成本按 pre-fill NAV 的证券交易名义额逐边收取，开盘费前 / 收盘费后顺序按 A2 合同；不在已有净收益上重复扣成本。当前 [UES A2 adapter](a2_execution_model_adapter.zh-CN.md) 只支持单证券与零收益现金、合成或一致复权的 OHLC；它不是 raw 公司行动、多证券、真实 BOXX 或完整插件回放。需要未实现能力时先解决适配合同，否则停放。成本是否反馈目标状态亦须绑定，不能将独立 gross-weight overlay 冒充自融资净仓位模型。

报告复用 A3/B3：同频率、未年化净 Sharpe `ddof=1`，净每期 log growth、PSR、累计 trial DSR、MinTRL、fractional-tail CVaR95、含初始 NAV 的最大回撤和同路径 gross / net 成本拖累。日级胜率不冒充逐笔胜率；已有窗口指标默认 `ddof=0` 不改。缺失统计证据或不可计算时保留 `None` 及原因，不能用 0 表示通过；不同仓位路径的增长差不是成本 drag。

主终点、paired difference、bootstrap 规则、统计假设与阈值仍为 `UNSPECIFIED`。后验可信区间不能冒充 PSR / DSR 或 E1 通过概率；累计 trial 校正不能因使用 Bayes 方法而取消。预测校准 / 误差、数值失败、风险 veto、停放、覆盖、未成交与换手需作为已冻结角色的诊断报告。

## Trial、数据身份与未解决外部证据

既有 `kelly_trial_ledger.json` 的 `48` 是所阅读版本的声明，未独立核实为当前全量 trial 总数。B4 草案本身不新增 trial、不修改账本；未来实际运行先满足冻结依赖，再按批准口径登记。成功、失败、拒绝、中断、超参 / 模型变体和必要敏感性均保留；是否允许结果驱动选择、重复运行如何计数以及成本档的角色须预先指定，不能把整场扫描算一次或从累计账本删除失败项。

DSR 所需全部累计同频净成本 Sharpe、`ddof=1` 样本方差、真实 ledger 内容摘要及完整性需独立核验。B3 的 caller-supplied digest 只是声明，不证明真实累计计数；缺少失败试验的可计算 Sharpe 时，冻结其处理规则并报告不可计算，不补造数值。

当前 B4 的数据来源 / license / PIT / 公司行动可用性、完整策略路径、候选身份、holdout 边界与内容 hash、隔离管理员及未查看证明、owner id / 身份 / 时间 / 有效期 / 批准摘要均 `UNSPECIFIED / NOT_ATTESTED`。本轮仅纯合成准备，既有真实数据许可不转授；未来合成 seed / 生成器也须登记，不能把合成 PASS 当作 source 或 edge 证明。

实际部署镜像 digest、已安装依赖与 `direct_url`、配置 / risk 指纹等未核实事实继续 `UNKNOWN`；源码 main SHA、研究 CI 或 staging 数据发布不代替策略运行证明。E1 的运营正确性和统计边际分别验收，运行日数不替代统计优势；C2 的独立授权与执行证据不因本草案成立。

## 后续实现归属与合成验收

只拟议一个 UES 纯函数模块及其测试，不建框架或新目录。以下文件**尚未创建**；实际 implementation owner 和 independent reviewer 均 `UNSPECIFIED`，由主智能体在冻结依赖满足后的实现批次指定单一写入归属。

| 未来文件 | 单一归属与职责 |
| --- | --- |
| `src/us_equity_strategies/research/bayesian_kelly.py` | B4 实现 owner：冻结模型下的纯后验更新、可审计状态与受已有 cap 约束的研究 sizing；不读账户、抓取数据、写 ledger 或下单 |
| `tests/research/test_bayesian_kelly.py` | 同一 B4 owner 写 meaningful 合成回归；独立 reviewer 审查。复用现有 A2/A3/B3 合同，不另造测量标准 |

B1/B2、A2/A3/B3、旧预注册、trial ledger、pin、runner 与生产文件归各既有 owner；本草案不取得这些文件的修改权。若未来整合必须改共享文件，先由主智能体单独分配其归属并审查，不同时写入。

未来验收至少覆盖：冻结模型的手算后验；明确适用条件下 batch / incremental 一致性；观察去重与更新次序；未来 bar 或迟到数据扰动不改变此前状态 / 暴露；缺失 / 非有限 / 无有效历史及后验数值失败；重放 / checkpoint 一致性；单一 fractional 与 cap / 旧 veto 优先；原有成交、成本、terminal 行为及相同对照报告。此清单**尚未执行**，不得写成通过或真实 edge 结果。

### 可复用的验证命令

草案编写时仅确认以下既有命令及路径在 UES 当前基线受到支持；最终 PR 的现有工程与研究合成回归以实际 CI 日志为准。这些命令也可供**未来冻结并核验依赖后的隔离合成验证**复用。生产 QPK pin 保持不变；现有 CI 的 research-stats 与 research-execution overlay 是两条不同的已登记环境，不能假定任一旧 overlay 覆盖 B4 所需全部组合。先绑定并核实实际安装的 immutable QPK / UES 来源、lock、版本和 import 能力；依赖缺失停放，不能把 skip 当作通过。

```bash
PYTHONPATH=src UES_REQUIRE_QPK_RESEARCH_STATS=1 python -m pytest -q -rs \
  tests/research/test_fractional_kelly_preregistration.py \
  tests/research/test_fractional_kelly_walk_forward.py \
  tests/research/test_trial_overfitting_report.py

PYTHONPATH=src UES_REQUIRE_QPK_EXECUTION_MODELS=1 python -m pytest -q -rs \
  tests/research/test_execution_model_adapter.py

PYTHONPATH=src python -m pytest -q tests/test_research_import_boundary.py
```

上述环境变量强制对应能力验收，不能授权真实数据。新 B4 模型及 `test_bayesian_kelly.py` 尚未创建，B4 研究未运行；当前不提供冒充可执行的 B4 测试命令，创建后由实现 PR 给出实际收集列表和结果。草案编写阶段的链接 / 格式与限定文件 diff 检查，以及最终 PR 的既有 CI 通过，都不代表 B4 实现或研究验收。

## 冻结与停止条件

实施及新试验前须补齐：B1/B2 对照、候选清单和 holdout / 累计 ledger 冻结证据；本表全部模型与参数决定；数据可用时点、测量和异常处置合同；精确 runner / 依赖身份；owner / reviewer 批准绑定。未具备时保持 `NOT_FROZEN`，不运行合成新研究，更不读取私有数据。

身份漂移、未来信息泄漏、未经批准数据、先验 / 数据重复消费、成本或状态对账失败、旧上限被覆盖、缺失 trial 记录均停止研究并保留失败证据。修复涉及模型或冻结合同变更时登记新版本及 trial 身份，不事后调参救结果。停止仅隔离研究产物，不解除 breaker、不改变生产配置或预算。B4 的研究结论还须交 E1 独立审查，本草案不构成晋级或 NONE→enable 授权。
