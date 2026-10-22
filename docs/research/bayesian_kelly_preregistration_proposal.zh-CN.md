# B4：贝叶斯 Kelly 后验更新的独立预注册草案

状态：**PROPOSAL / NOT_FROZEN**。任务为 [UES #588](https://github.com/QuantStrategyLab/UsEquityStrategies/issues/588)，总审计任务为 [QRS #632](https://github.com/QuantStrategyLab/QuantRuntimeSettings/issues/632)。用户新授权覆盖本轮 B4 研究准备和草案；本文件没有冻结模型、批准新试验、运行研究或生成收益。

排序保持为：**B1/B2 可靠对照与候选、holdout 隔离及累计 trial ledger 冻结 → 独立 B4 合成研究 → E1 独立晋级审查**。B1/B2 当前仍未冻结；允许先准备本草案，不据此提前实施或运行 B4。未来产物固定 `DEVELOPMENT_ONLY`、`live_ready=False`、`no_order=True`、`budget_effect=NONE`，只可作为封顶或降低既有预算的研究备注；不交易、不提高预算、不启用候选或访问真实研究数据。

当前本地进度（不改变本草案的 `NOT_FROZEN` 状态）：独立 worktree 已有 `us_equity_strategies.research.bayesian_kelly` 的合成实现，以及 [B4 预测校准评分器](b4_predictive_calibration.zh-CN.md)。评分器只检验给定固定 `Sigma` 的预测分布，不验证 `Sigma`/训练身份是否诚实，也未接真实数据或作为 B4 冻结/晋级依据。下文“尚未创建”“未运行”等语句保留为草案编写时的历史状态。

## 依据与许可边界

- UES 阅读基线为 `26e77dad6e5d5d54a15c6d9fadd94d46b94c3a0f`：[RS-04 预注册](fractional_kelly_preregistration.zh-CN.md)、[评估器说明](fractional_kelly_walk_forward.zh-CN.md)、[RS-06 组合预注册](portfolio_kelly_preregistration.zh-CN.md)和[累计账本](kelly_trial_ledger.json)。这些是历史版本和授权记录，不能充当当前运行状态或 B4 许可。
- RS-04 的冻结候选、变体、数据窗口、manifest 和私有运行许可不转授给 B4。旧 `no_plugin_core` 也不等于完整实盘插件一致性；无法复现的插件、实际配置覆盖及部署版本仍需独立核实。已见历史仍为 development，不能改名成为 untouched holdout。
- 跨库依据为 QPK `f8d8b4045276ce9e0c491eefd8506c544c19575b` 的 [B1/B2 草案](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/b1_b2_preregistration_proposal.zh-CN.md)、[A2 成交与成本](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/research_execution_models_v1.zh-CN.md)、[A3 指标语义](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/window_metrics_semantics.zh-CN.md)、[B3 净收益报告](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/unified_net_report.zh-CN.md)和 [Kelly 单位合同](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/f8d8b4045276ce9e0c491eefd8506c544c19575b/docs/kelly_contract_v2.zh-CN.md)。这些版本提供测量依据，不自动成为 B4 运行依赖。
- 原 B1/B2 文档不包含 B4 的范围约束保持原文；B4 由本独立任务准备。这里不修改旧批准、参数、账本、生产 pin、配置、候选状态或 B5，也不做自动候选扫描。

### 原报告已取得与当前工程接续

原《Quant 系统复利数学与工程审计报告》12页现已取得，SHA256 `f60ce29f267722245cf62e308883e11b001f38efc3ee2de9d6c861806acb1bfb`。p5明确参数不确定性Kelly建议 `f=m/(sigma²+tau²)`，位置为UES RS-04新增方案、“第二波新任务B4”；p6要求新增研究预注册/累计台账，p10限制预算只降低/封顶、Kelly倍数≤0.5。原文没有选先验/似然/超参/更新/状态/效用，不因获得原式自动冻结模型。

上述26e77/f8历史依据保留，不冒称当前安装。当前文档批次读取UES `0e66469c95a72c6248eae3ea474223d9ff6e1df5`，QPK测量参考为 `b23511cbb28b72743e35512ef4b3c73f1cd43c23`。UES#590仅为既有历史CLI的默认关闭B3消费者，四项周期outcome不是B4后验；其独立研究overlay不代表任何B4模型已安装/执行，缺gross/trials仍为不可计算。

原PDFp9–10要求A2/A3先于B1/B2冻结计算；“B1/B2可靠对照 → B4 → E1”是当前#588后续批准的执行安排，不写成原文已明确的完整依赖。当前可先完善[三组事实与B1/B2决策](b1_b2_freeze_decision_contract.zh-CN.md)及[CVaR→B1约束](cvar_exposure_constraint_decision_contract.zh-CN.md)的DRAFT；不调整既定顺序或自动扩入B5。

## B4原式的变量、状态与决策合同补充

本节是依据p5原式提出的**DRAFT语义合同**，不是报告已经冻结的模型。原式可作为二阶增长近似的研究目标，不单独证明任意收益分布下精确最大化预期log效用。

仅当F02/F07定义的财富因子确为`1+f*U`时，有限周期的二阶Taylor为`E[log(1+f*U)|D_t] ≈ f*m_t - (f²/2)*E[U²|D_t]`，其中`E[U²|D_t]=sigma_t²+tau_t²+m_t²`；正有限分母、未加约束时该截断式的驻点为`m_t/(sigma_t²+tau_t²+m_t²)`，仍不是精确log效用的最优解。
原报告的`m_t/(sigma_t²+tau_t²)`还需忽略`m_t²`等相应小增量/连续时间近似，现金腿与财富因子必须在F02/F07使用同一定义，不能将未归一化的策略减现金简单收益直接代入不同的效用口径；本补充不改原推荐式、不选择模型/参数/额外候选，精确效用决定仍为F07 `UNSPECIFIED`。

### 同频条件方差与均值不确定性

设未来同一预测期的观测为`U_(t+h)`；DRAFT提出以精确scope策略收益相对明确现金腿的超额收益定义U，是否采用、现金/费用/FX口径由决策F01/F02冻结，不能将raw-net与excess混用。`D_t`只含决策截止前实际可用信息，`Theta`为尚未选择模型的参数/状态。先分别定义：

```text
mu(Theta,D_t) = E[U_(t+h) | Theta,D_t]
m_t           = E[mu(Theta,D_t) | D_t]
sigma_t²      = E[Var(U_(t+h) | Theta,D_t) | D_t]
tau_t²        = Var(mu(Theta,D_t) | D_t)
V_predictive  = sigma_t² + tau_t² = Var(U_(t+h) | D_t)
f_uncertain   = m_t / (sigma_t² + tau_t²)
```

这是全方差分解：sigma²为后验平均条件创新方差，tau²为条件均值不确定性方差；三个量必须同收益基准、预测期及小数单位，方差不是标准差。若输入已经是`V_predictive`，它已含tau²，**不能再加tau²**；也不把不属于同一分解的样本方差与标准误平方直接拼接。`sample_variance/N`只有在冻结模型证明其对应上述tau²时才可使用，不能因实现便利叫作后验。

在合法模型、正有限分母下，m=0可产生真实零原式目标；m<0的原式目标为负，后续long-only处置必须明确，不能新增做空。缺失/非有限、负方差或零分母不算成零风险/无限仓位，标不可计算并研究停放；具体容差/现金/恢复规则仍须冻结。对正m，在其余条件固定时增大tau²不应提高原式目标；这是一条未来算术性质检查，不是已验证策略收益。

### 原式输出与一次fractional

必须在候选记录中分别命名：原式`f_uncertain`、是否已经fractional的研究目标、`c`消费位置、裁切后的资金暴露及原scope cap来源。原式不自带“已乘半Kelly”标签；也不能在上游已经乘c后由下游再次乘c。已定研究家族的fractional倍数最多0.5、只应用一次；0.5不是全策略统一50%资金暴露。若原式内已有其他收缩/效用约束，如何与这一界限结合必须在F07明确，不能以“替代拍定c”解除上限或默认重归一化满仓。

旧hard gate、cash reserve、产品限额、drawdown/breaker/veto及更严scope cap始终优先，未来输出不高于其既有预算上限。本批仅`DEVELOPMENT_ONLY/live_ready=False/no_order=True/budget_effect=NONE`备注；数学目标、研究备注和真实发单分别处理，不增加预算/融资/候选启用。

### 具体冻结表

以下补充原冻结表的可核验记录结构，不代填其仍UNSPECIFIED的值。每行须绑定决定、scope/版本、材料/实际摘要、owner/独立reviewer批准范围、依赖与有效期；private值不进入公开账本。不存在的状态、观察、holdout或批准不填hash。

|ID|必须决定与可审查产物|当前状态|
|---|---|---|
|F01|准确candidate/profile/variant及新preregistration/model身份、完整对照/敏感性列表；旧RS-04结果与许可不得覆盖|UNSPECIFIED|
|F02|U的raw-net或excess选择、现金/FX/成本、观察频率/日历/预测期、观测/发布/决策可用截止与PIT来源|UNSPECIFIED / NOT_ATTESTED|
|F03|Theta和先验族/支持/proper性/参数化/有效样本量；独立来源或经验Bayes拟合边界，不用holdout调先验|UNSPECIFIED|
|F04|似然族/支持/尾部/相关性/非平稳、mu与条件方差的函数；若收益支持使log效用无定义必须明定处理，不能静默截尾|UNSPECIFIED|
|F05|sigma²/tau²具体后验估计器及全方差拆分、输入已有predictive variance的标记；均值/标准差/方差单位核验|UNSPECIFIED|
|F06|超参/数值算法/精度/收敛/RNG、warmup/空历史是否允许仅prior；任何模型/参数变体的trial角色|UNSPECIFIED|
|F07|原式是近似目标或精确效用决策何者；效用/损失、fractional状态和唯一消费位置、c≤0.5、负均值/原cap映射|UNSPECIFIED|
|F08|batch/incremental、更新频率/窗口/遗忘/重拟合、折间prior重置或后验延续、预热及可用观察权重|UNSPECIFIED|
|F09|稳定观察身份、修订/迟到/重复消费规则、消耗顺序与watermark；不得合并未知重复或倒灌旧决策|UNSPECIFIED|
|F10|checkpoint版本/模型参数/输入与已消费范围、序列化/原子持久化、重启和旧state拒绝；恢复不能重算成新成功|UNSPECIFIED|
|F11|缺数/非有限/零负方差、不正规posterior、数值失败/停放/恢复、错误记录；已知零与unknown分开|UNSPECIFIED|
|F12|原scope风险/现金cap、B1/B2可靠对照与CVaR约束角色/条件、成交/末次fill/净成本/现金反馈|UNSPECIFIED / NOT_ATTESTED|
|F13|主终点/paired difference/校准与失败诊断、report/ranking/veto角色、统计阈值/依赖假设及停止规则|UNSPECIFIED|
|F14|untouchedholdout实际内容/隔离/未查看、完整累计journal/计数/无Sharpe处理、immutable runner+安装依赖、真实owner/reviewer与授权期限|NOT_ATTESTED|

### 状态转移的DRAFT验收边界

未来实现应能审查下列动作，具体存储和规则由F08–F11决定，不在本批创建state schema或框架：

|动作|未来验收要验证的边界|
|---|---|
|初始化|模型/prior/参数身份绑定；空历史资格显式，不造已更新posterior或自动风险目标|
|接受新观察|先验证身份、可用截止、次序/权重与覆盖，再按冻结模型更新；保存已消费范围与成功/失败事实|
|重复/修订/迟到|重复不再计入；修订按预注册规则登记，不能覆盖此前决策或把同训练集既拟prior又更新likelihood两次|
|更新失败|保留原因/覆盖与明确状态；是否保留旧checkpoint或停止恢复由冻结规则定，不把旧posterior伪装为新成功|
|持久化/重启|同输入/state重放一致，checkpoint身份漂移拒绝；写成功回执未知不重复消费、不造新批准|
|跨fold|只按冻结规则重置或carry；不删除亏损fold状态或看到holdout后换先验|

FROZEN模型的合成验收设计至少为：独立手算后验及三个量；正m下tau增大不提高目标与tau=0的合法退化；同模型/有序数据/窗口/权重下明确的batch与incremental关系；未来信息与迟到修订扰动；重复/重启/写入未知；缺数/非有限/零方差/不收敛；一次fractional与原cap/veto优先；同会话净成本/terminal-fill/B3报告对照。带遗忘/窗口/自适应超参不假设天然batch等价。具体fixture/seed/容差/收集命令仍未冻结，**这些设计均未执行**。

外部事实收敛为与[B1/B2合同](b1_b2_freeze_decision_contract.zh-CN.md)相同三组：scope+source许可/PIT+原cap/尾部预算；真实untouchedholdout内容/隔离/未查看；完整累计journal及独立owner/reviewer。B4还需本表具体模型/参数决定；收到三组事实不自动选模型，数学原式与工程CI不替代研究edge或E1。

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
