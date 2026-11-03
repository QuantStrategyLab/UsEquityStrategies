# B1/B2 冻结决策合同草案

状态：**DRAFT / NOT_FROZEN / DEVELOPMENT_ONLY**。本文件把已有提案变成可逐项评审的决策单，并记录 D03–D08 的 owner 批准参数；没有批准试验、取得私有数据、写入台账或改变实盘风险门。追踪 [QRS #632](https://github.com/QuantStrategyLab/QuantRuntimeSettings/issues/632)、[QPK #659](https://github.com/QuantStrategyLab/QuantPlatformKit/issues/659) 和 [UES #588](https://github.com/QuantStrategyLab/UsEquityStrategies/issues/588)。B4另见[独立草案](bayesian-kelly-preregistration-proposal.zh-CN.md)，尾部约束另见[CVaR决策合同](cvar-exposure-constraint-decision-contract.zh-CN.md)；B5不进入本批。

## 原文与已有工程的边界

原《Quant 系统复利数学与工程审计报告》12页现已取得，SHA256 `f60ce29f267722245cf62e308883e11b001f38efc3ee2de9d6c861806acb1bfb`。p4提出连续drawdown/cushion，p5要求HAR-RV与EWMA对照，p9–10明确A2/A3先于B1/B2、预注册冻结后才计算；p6/p10要求新研究计入累计试验、预算只能降低或封顶、Kelly倍数不超过0.5。报告中的示意收益、旧48次声明和当时UNKNOWN的GitHub状态不是本次输入证明。

读取基线UES `0e66469c95a72c6248eae3ea474223d9ff6e1df5`、QPK `b23511cbb28b72743e35512ef4b3c73f1cd43c23`。A2/A3/B3原语与UEs#590默认关闭的历史CLI消费已合并并通过最终PR/main CI；它证明工程合同，不表示B1/B2已执行、实际收费完整或策略具备优势。[QPK原B1/B2提案](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/b23511cbb28b72743e35512ef4b3c73f1cd43c23/docs/b1_b2_preregistration_proposal.zh-CN.md)的未冻结参数继续保留。

现项目次序是测量适用性与冻结事实齐备 → **B1/B2可靠对照 → B4独立研究 → E1**。原PDF把B4称第二波，未写出这条完整额外依赖；这里记录后续批准的执行安排。C2/候选启用和真实数据运行仍有独立准入，本草案不授予。

## 已选择首个核查路径

用户本轮委托助手选择首条研究路径，[#588 当前记录](https://github.com/QuantStrategyLab/UsEquityStrategies/issues/588#issuecomment-6162055942)已选择现有 **RS-04 SOXL/TQQQ research core**。这只闭合首个核查路径，不表示单策略/组合范围、profile/variant、参数、cap、runner或新数据许可全部冻结，也不等于完整实盘插件一致性。以下保留三条已有输入/调用合同作适用性比较，不再把“首路径未选”作为阻塞。

当前工程来源为 UES [#593](https://github.com/QuantStrategyLab/UsEquityStrategies/pull/593) 合并 `48026f5af85aa11b0e3b9f61316f33e8f94d4a39`：实际末次 close fill 的费用测量入口已补齐，默认旧路径、输出、账本与生产 pin 保持。工程完成不证明该研究 runner 已采用新入口或现金/净仓位会计已完整。

|已有路径|可复用输入与行为|实施B1/B2前要绑定的适用性|
|---|---|---|
|[RS-04 core生成器](../../src/us_equity_strategies/research/kelly_core_return_generator.py)：`generate_soxl_core_path` / `generate_tqqq_core_path`、`CorePath`|有序sessions、gross returns、旧turnover、逐资产returns/weights；raw bars加公司行动；显式`close_fill_turnover`及`measure_close_fill_costs(CostModel)`|首个核查路径已选；多资产核心非完整插件/live parity。旧turnover仍后移到下一earning session；新入口按真实close计费并包含末次fill，仅为`no_weight_feedback` NAV overlay。不能将其当完整现金/费用反馈/净额结算模拟；仍须绑定variant、现金与公司行动、内外费用及实际runner采用，旧RS结果不回写|
|[A2 `replay_targets`](../../src/us_equity_strategies/research/execution_model_adapter.py)|调用方冻结的完成收盘目标、单证券、零收益现金、合成或一致复权open/close；同sessions的gross/net及真实fill turnover|不生成信号；不支持raw公司行动、BOXX现金、多证券漂移或完整插件。费用为独立NAV overlay、不反馈gross目标权重，不能当自融资净仓位模型|
|[Batch A历史报告CLI](../../scripts/run_c3_batch_a_existing_member_baseline.py)、[v2合同](batch-a-v2-contracts.zh-CN.md)|已有typed SMA200控制成员、来源重绑定、净ledger及#590可选十二项report-only诊断|不是RS-04完整核心路径；`ASSUMED_ZERO_USD_CASH`、来源已见development，许可/PIT/OOS未独立成立。同路径gross/完整trial缺失时cost drag/DSR明确不可计算|

旧[RS-04冻结预注册](fractional-kelly-preregistration.zh-CN.md)的252/63/5、5/10/15bps、`next_close`、BOXX、变体许可与历史manifest只提供对照来源，不转授给新B1/B2。不得根据旧默认或报告例子代填新scope的cap、数据或窗口。

## 决策记录与状态

每项冻结记录包含：具体决定、作用域、版本、来源材料及实际内容摘要、责任人/独立reviewer和批准范围、依赖、有效期/停止职责。首路径单独标`PATH_SELECTED`；其余未决定值保持`UNSPECIFIED / NOT_FROZEN`，外部证明另标`NOT_ATTESTED`。不存在的内容不填hash，private值保留于受保护位置，公开仅记不敏感的字段/状态/摘要。填表、代码合并或CI成功均不自动变为FROZEN。

|ID|待决定及必须绑定的内容|当前状态|
|---|---|---|
|D01|strategy key/profile/variant、单策略或组合scope、core-only与完整插件的精确边界、原始配置与作用域risk/cash cap来源|首路径RS-04 SOXL/TQQQ research core：PATH_SELECTED；完整scope/profile/variant/参数未冻，cap来源NOT_ATTESTED|
|D02|上述实际consumer/生成器及immutable源码、runner、lock/已安装依赖；输入/输出适用性和A2接入差分|core生成器首路径：PATH_SELECTED；#593测量源码可绑定，实际runner/安装依赖/完整适用性仍UNSPECIFIED / NOT_ATTESTED|
|D03|B1完整候选ID及角色：固定50%、quarter/half Kelly、EWMA、HAR、vol-target+Kelly cap哪些独立行；每行参数和拒绝条件|**OWNER_APPROVED 2026-10-14**：B1 只登记四行 `fixed_50_subject_to_original_cap`、`kelly_c_0.25_once`（主）、`kelly_c_0.5_once`（对照）、`ewma_vol_target_and_kelly_cap`（c=0.25）；c 只在 {0.25, 0.5} 取一次，再与原 cap 取 min，c>0.5 拒绝；不另开纯 vol-target 行；HAR 不登记（见 D07）|
|D04|B2固定基础sizing与独立对照：原阶梯、连续drawdown、normalized cushion哪些行；禁止默认B1×B2笛卡尔积|**OWNER_APPROVED 2026-10-14**：B2 基础 sizing = `kelly_c_0.25_once` 的输出；只对照 `original_ladder` 与 `continuous_drawdown`；`normalized_cushion` 不进入本 trial 集；禁止 B1×B2 笛卡尔积|
|D05|同频净收益/超额收益、现金/FX、日历/年化、初始NAV与完整测试sessions；资产基准及不匹配/丢弃数处理|**OWNER_APPROVED 2026-10-14（部分）**：主终点为同频日净 log 增长，现金对照为同频现金；初始 NAV=1.0。资产基准、不匹配/丢弃数处理、FX 仍 UNSPECIFIED；费用见 D10（UNSPECIFIED）|
|D06|target-vol单位/预测期、EWMA零均值或其他形式、decay、初值、warmup、决策更新时点与缺值规则|**OWNER_APPROVED 2026-10-14**：target_vol 年化 20%（日 `0.20/sqrt(252)`）；零均值 EWMA `v_next=λv+(1-λ)r²`，日简单收益，λ=0.94；初值 = 训练前缀前 63 个 session 的样本方差；warmup 63 个 session，期间暴露 0（PARKED）；session t 的预测只用 t-1 及以前的收益；缺值/非有限 → PARKED 为 0，不切换 HAR。敏感性（仅报告）：target_vol 30%、λ=0.97|
|D07|HAR真正intraday RV来源/许可、采样/时区/时段/清洗/可用时点、日周月窗口、拟合/重拟合/warmup与失败规则|**OWNER_APPROVED 2026-10-14：PARKED**。无授权 intraday RV，HAR 不登记、不运行|
|D08|B2原scope `D_max`、阶梯阈值/边界与硬gate来源；净NAV峰值延续、资金流调整、成本反馈、重启/恢复/停放规则|**OWNER_APPROVED 2026-10-14**：`D_max`=min(原 scope, 0.20)=0.20（原 scope 无回撤上限，UNSPECIFIED）；阶梯：回撤 ≤10% 满仓、≤20% 半仓、>20% 为 0；净投资指数峰值含初始 NAV，亏损不重置，外部资金不消除回撤。作用域 cap 见下方 B1/B2 批准值。成本反馈与重启/恢复规则仍 UNSPECIFIED|
|D09|信号可用时点、`next_close`/`next_open`的主/敏感性角色、公司行动/终端未成交/末次fill及实际费时点|UNSPECIFIED|
|D10|commission/spread/slippage/融资、内部与外层成本/现金归属；成本档位属报告情景还是选择trial，不重复扣费|UNSPECIFIED|
|D11|真实source/license/PIT/coverage、raw/split/total-return价口径、公司行动与现金可用时点、实际manifest身份|UNSPECIFIED / NOT_ATTESTED|
|D12|train/test/purge/embargo、warmup与尾部未覆盖、untouched holdout边界/实际摘要/隔离管理员和未查看事实|UNSPECIFIED / NOT_ATTESTED|
|D13|净log增长主终点及paired difference；各指标report/ranking/veto角色、效应/统计阈值、bootstrap/PBO/PSR/DSR/MinTRL假设与停止规则|UNSPECIFIED|
|D14|CVaR描述与约束分离：tail-budget来源/单位/窗口、成本现金齐次条件、缺覆盖/不可计算规则及B1消费位置|UNSPECIFIED / NOT_ATTESTED|
|D15|每个候选/模型/参数/成本角色/敏感性/重复的trial单位；失败/拒绝/中断记录、全累计journal及同频net Sharpe方差/无Sharpe处理|UNSPECIFIED / NOT_ATTESTED|
|D16|研究owner/reviewer真实独立绑定、预注册批准和真实数据运行批准、有效期/输出位置/停止与复审权限|NOT_ATTESTED|

### B1/B2 批准值与来源（2026-10-14，owner 批准）

生效值 = min(原 scope 值, 提案值)。研究口径按 IBKR runtime 风控。

|项目|原 scope 值|提案|生效值|来源|
|---|---|---|---|---|
|总名义暴露上限（SOXL 路径）|0.97|1.00|**0.97**|IBKR `soxl_soxx_trend_income` runtime_risk_limits `total_nominal_exposure_cap`；`reserved_cash_ratio` 0.03|
|总名义暴露上限（TQQQ 路径）|0.98（`cash_reserve_ratio` 0.02）|1.00|**0.98**|UES `manifests/__init__.py` tqqq_growth_income 默认配置（`ca599d5`）|
|SOXL 单标的名义上限|0.679|1.00|**0.679**|IBKR runtime_risk_limits `nominal_caps.SOXL`|
|`D_max`|UNSPECIFIED|0.20|**0.20**|原 scope 无回撤上限|
|Kelly 倍数 c|≤0.5（审计）|0.25 主 / 0.5 对照|**0.25 / 0.5**|审计 p6/p10|

来源：IBKR 平台仓库变量 `CLOUD_RUN_SERVICE_TARGETS_JSON`（更新于 2026-09-25T15:30:24Z），由 owner 侧只读核对，与 2026-10-10 快照一致。本文不记录账户标识。

仍为 UNSPECIFIED / NOT_ATTESTED：费用与滑点（D10）、数据源/许可/PIT（D11）、holdout（D12）、CVaR（D14）、trial journal（D15）、reviewer 绑定（D16）。本批准不授权真实数据运行、不改生产 pin、不提高任何预算。


## 比较形式的DRAFT，不能直接运行

B1采用 D06 批准的零均值EWMA形式 `v_next=lambda*v_current+(1-lambda)*r_t²`、lambda、初值与 warmup；session t 的预测只用 t-1 及以前的数据。HAR按 D07 保持PARKED，不登记、不运行；不把平方close return改称RV，也不静默切换预测器。

B1的vol-target与Kelly组合已登记为D03中的 `ewma_vol_target_and_kelly_cap`（c=0.25），按相同预测期计算 `min(existing_cap,clip(target_vol/forecast_vol,0,1),clip(c*f_star,0,1))`。`c`最多0.5且只消费一次，0.25为主、0.5仅为对照；0.5是Kelly倍数上限，不是所有策略共同50%资金暴露。固定50%对照也受同一作用域硬cap，不因名为baseline而豁免；候选不能提高既有预算。

B2形式沿原提案：净财富`W>0`、包含初始NAV的峰值`M>=W`，`d=1-W/M`、`alpha=1-D_max`；D04只登记原阶梯与连续drawdown `clip(1-d/D_max,0,1)`；normalized cushion `clip((W-alpha*M)/(D_max*W),0,1)`仅保留为原提案形式，不进入本 trial 集。`D_max`按D08批准为0.20，原scope存在更严上限时不能放宽。资金流存在时按D08使用净投资指数/峰值，不能直接混用美元W和资金流调整指数；成本后峰值/决策截止规则冻结后才算。不因亏损重置M，跳空/离散费用可突破floor，不宣称硬回撤保证。

所有候选先受原hard gate、cash reserve、产品上限、breaker和veto约束，未来输出仅`DEVELOPMENT_ONLY/live_ready=False/no_order=True/budget_effect=NONE`的封顶/降低备注。CVaR缺预算/尾部或预测异常不意味着解除既有约束；具体现金处置/恢复需D06/D08/D14明定，否则研究停放。

## 冻结前的三组真实事实

1. **Scope与来源/限额包**：D01/D02/D05/D07/D09–D11/D14的具体candidate、实际输入/许可/PIT、风险/现金/尾部预算及适用runner来源。报告示例和旧数据许可不替代。
2. **Holdout包**：D12真实边界/内容摘要、隔离管理员/未查看证明及对数据修订/重新冻结的处置。旧已见development不能迁移成未见OOS。
3. **Journal与责任包**：D15/D16含全部失败等状态的真实累计journal、独立完整性审查、同频Sharpe口径及真实owner/reviewer批准。旧七行48声明不是已核验全集，缺Sharpe不补0。

参数决策也必须填入，三个事实包不自动选择它们。只要求该候选真正用到的指标/数据；逐笔统计、容量等不机械加为共同前置。三包齐备与记录可追溯后，独立review核对批准范围与内容，绑定真实版本再标FROZEN；新增研究或修订登记新身份，不改旧台账行。

## 未来合成验收与停止

下表仍是研究冻结前的验收设计。任何 D 行都没有因此变成 FROZEN。合成反例的种子、容差和收集命令绑定在下一节，只约束工程 fixture；不填真实费率、风险 cap、PIT、holdout 或 journal。

|验收|应证明的合同|
|---|---|
|因果预测|未来bar/RV/迟到公司行动扰动不改变此前预测、权重及净收益；观测/可用/信号/fill时点分开|
|波动与缺失|手算递推、单位/频率相同、warmup/零负或非有限预测处理；失败不自动换HAR/EWMA、不产生无限暴露|
|回撤状态|两连续函数边界/单调性、原阶梯精确边界、资金流不消除投资亏损、checkpoint恢复/重复事件及峰值延续|
|成交与净成本|同输入三时点显式差分；entry/exit/末次close费用、无下期fill、现金/公司行动、成本反馈和同路径gross/net对账|
|原cap优先|fractional只一次且≤0.5；固定50%亦被原cap裁切；breaker/veto不被连续缩放或CVaR缺值覆盖|
|测量|同sessions含零观察与初始NAV、B3 raw-net ddof1与legacy年化字段分开；缺gross/完整trials则null；周期诊断不当逐笔成绩|
|试验完整性|预注册先于计算；每个attempt先登记，失败/拒绝/中断保留；不只挑成功结果进入累计统计或删覆盖失败行|

身份漂移、未批准输入、未来泄漏、数量/成本/峰值对账失败、原cap被放宽、冻结范围外候选或缺trial记录均停止研究并保留证据。停止只隔离研究产物，不解除生产breaker、不改pin/预算/候选。实现和研究成功仍需E1独立运营/统计双轴验收，文档/工程CI不是edge或部署批准。

## RS-04 现金、成交与费用角色

状态与本文件相同：**DRAFT / NOT_FROZEN / DEVELOPMENT_ONLY**。这里写的是当前 UES 源码已经做到的调用边界，不是实盘等价，也不是完整自融资净仓位。生产 pin 不改。`live_ready=False`、`no_order=True`、`budget_effect=NONE`。

|角色|当前合同|明确不是|
|---|---|---|
|内层残余现金|`_replay` 里未投入的权重差额收益为 0|不是 BOXX，也不是外层 Kelly 的现金腿|
|内层 BOXX|核心路径里的可交易资产；内层换手在它被买卖时计入|不因名为现金而免内层费用|
|外层现金腿|评估器把整条策略毛收益当一个虚拟资产，`(1-f)` 乘调用方另行提供的现金序列。私有 runner 在真跑时把 BOXX 总收益放在这个位置，且外层现金腿不计费|不替代内层残余现金；不与内层 BOXX 轧差|
|A2 现金|`replay_targets` 的单证券剩余仓位，收益固定为 0|不是 BOXX，不是融资，不是多资产漂移|
|legacy turnover|close 成交额后移到下一 earning session。私有 runner 和旧费用压力消费这一列|会漏掉没有下一 session 的末次 fill；不能与下面两列相加|
|close-fill turnover|真实 close 的单边证券成交额 / pre-fill NAV，含末次 fill。只有 `measure_close_fill_costs` 使用，`charge_at=end`，`no_weight_feedback`|不改毛权重、决策或参考权益；不是分券种净额|
|外层交易额|`abs(外层权重变化) + 外层权重 × 内层 legacy turnover`，再交给 QPK `cost_stress_recompute`|不使用 close-fill 列；不与内层 fill 相加或抵消|
|A2 fill turnover|单证券实际成交日换手。`legacy_turnover` 只作差分|两列不能同时扣费|

公司行动只在核心路径的总收益里按 `(close_t·拆股比 + 分红)/close_{t-1}-1` 进入。A2 拒绝 `raw_unadjusted`。三套时点 `same_close` / `next_close` / `next_open` 必须在同一输入上分开报告；`same_close` 仍是收盘后信号用同一收盘价的假设。观测、可用、信号、fill 分相：完成收盘的观测和“收盘后可用”可以落在同一 session index，fill 在 `next_close` 上是下一 index。这不是 15:45 实盘时钟。

## Runner 差分

|调用面|实际消费|未消费 / 未证明|
|---|---|---|
|`kelly_private_runner.run_private`|窗口内 `gross_returns`、后移 `turnover`、外层 BOXX 现金序列、旧预注册里的费率参数|不调用 `measure_close_fill_costs`，写出的 core path 不含 `close_fill_turnover`。本批没有运行它|
|close-fill 测量|显式注入的 QPK `CostModel`|不进入默认 wire、路径摘要或私有 runner|
|A2 `replay_targets`|调用方已冻结的单证券目标|不生成 RS-04 信号，不处理 raw 公司行动或 BOXX|
|外层 `evaluate_fractional_kelly_walk_forward`|策略毛收益流、调用方现金序列、可选内层 legacy turnover|不读取 close-fill 净收益，不把内外交易轧差|

因此：测量入口存在，不等于 runner 已采用；core-only 路径存在，不等于 live parity。`runner_adoption=NOT_ATTESTED`。

## 已批准参数与 trial 单位（未冻结）

状态：**OWNER_APPROVED / NOT_FROZEN**。参数决定以 D03–D08 和上方批准值表为准，完整预注册仍未冻结。`research/b1_b2_rs04_preparation.py` 的 `RECOMMENDED_PROPOSAL` 保留原合成准备建议，不是这些批准值的机器可读绑定。旧 RS-04 的 252/63/5、5/10/15 bps、旧 manifest 和已见 development 窗口不转入本 scope。

|项|批准决定|未完成的绑定与停止规则|
|---|---|---|
|B1 行，且只列这些行|`fixed_50_subject_to_original_cap`；`kelly_c_0.25_once`；`kelly_c_0.5_once`；`ewma_vol_target_and_kelly_cap`|不另开纯波动目标行，除非 D03 另批。不与 B2 做笛卡尔积|
|HAR-RV|`PARKED`|只有日线平方收益时不得改名为 RV，也不得自动改跑 EWMA|
|B2 第一对照，不与全部 B1 行交叉|`original_ladder` 与 `continuous_drawdown` 共用 D04 的 `kelly_c_0.25_once` 输出作为基础 sizing|`normalized_cushion` 不进入同一 trial 集。阶梯阈值 10% / 20% 与 `D_max`=0.20 已由 D08 批准；合成边界不是实盘 gate，实际 runner 适用性仍须绑定|
|主时点|`next_close`|`next_open` 与 `same_close` 各是独立敏感性 trial；`same_close` 不是主结果|
|EWMA 形式|`v_next = lambda·v_current + (1-lambda)·r_t²`，单位是每观察简单收益的平方，不年化|D06批准年化 target_vol 20%、lambda 0.94、前63个session样本方差初值与63个session warmup，session t 只用 t-1 及以前的数据；调用方不提供有限正初值或足够 warmup 时预测停放、暴露尺度为 0|
|`c`|0.25为主、0.5仅为对照；只在 `{0.25, 0.5}` 中取一次，再与原 cap 取 min|`c>0.5` 拒绝。固定 50% 也先被原 cap 裁切。作用域 cap、`target_vol`、`D_max` 的批准值见上表及 D06/D08；实际输入/runner 绑定仍未完成，cap 缺失时暴露是 null，不补 1|
|费用档|在 D10 被证明之前只作 `REPORT_SCENARIO_NOT_SELECTION_TRIAL`，不乘进选择 trial 数|费率 NOT_ATTESTED。若有人按某一档做了选择，该档必须改记为选择 trial。不把旧 5/10/15 bps 写成本 scope 的费率|
|trial 单位|一次 attempt = RS-04 research-core 路径 + 候选行 + 参数绑定 + 成本角色 + 时点角色 + attempt 序号|`COMPUTED`、`PARKED`、`REJECTED`、`INTERRUPTED` 都计入。预注册之前不能登记，登记之前不能计算。禁止只保留成功行、删除或覆盖失败行|
|PIT / holdout / journal|无推荐值|三项都是 NOT_ATTESTED。没有边界、未查看证明或累计账本时停止，不补 hash、不把已见 development 改称 holdout、缺 Sharpe 不补 0|

成本角色本身不是第二条经济 trial：同一候选行的 legacy、close-fill 与外层交易额必须分列，不能相加后当成更真实的成本。证券级轧差仍没有 QPK 接口。

## 本批绑定的合成反例

模块 `src/us_equity_strategies/research/b1_b2_rs04_preparation.py`，测试 `tests/research/test_b1_b2_rs04_synthetic_acceptance.py`。

|绑定|值|不是|
|---|---|---|
|fixture 种子|`20261014`|不是研究 RNG 的冻结种子。QPK 草案里未执行的 `20261013` 仍未采用|
|绝对容差|`1e-12`|不放宽对账失败|
|费用与 cap 夹具|测试可以传入明确标注的合成数|`NOT_A_FEE_SCHEDULE` / `NOT_A_RISK_CAP`，不能抄进 D10 或 D08|

覆盖：未来收益与迟到公司行动不改变前缀毛收益、权重和 close-fill；EWMA 手算递推、缺值/非有限/零初值停放且不切换 HAR；连续回撤与 cushion 的边界和单调、阶梯的精确边界、资金流不消除投资回撤、checkpoint 后峰值延续；三时点、末次 fill、无下期成交、费用不反馈毛权重；fractional 只乘一次且不超过 0.5，固定 50% 被原 cap 裁切，breaker/veto 与缺失 CVaR 都不放宽 cap；缺 gross 或完整 trials 时测量为 null，B3 的 ddof1 与 legacy 年化字段分开，周期诊断不是逐笔成绩；attempt 先登记，失败行保留。

收集命令：

```bash
PYTHONPATH=src python -m pytest -q tests/research/test_b1_b2_rs04_synthetic_acceptance.py
```

主 CI 的生产 pin 没有 A2/B3 overlay 时，成交与 QPK null 两条跳过，其余反例仍运行。`research-execution-integration` 与 `research-unified-net-consumer` 分别在对应 overlay 上强制运行这两条。跳过或通过都不把本文件标成 FROZEN。

## QPK 跨库缺口

本 PR 只改 UES。下面的符号是已读到的现有接口；缺的能力不在 UES 里假造同名 API。生产 pin `f6f2079f6b53d3cbb3b72bdcc5c2476ed8f170f1` 不因这些 overlay SHA 而改变。

|现有符号|读到的 overlay|没有的合同|
|---|---|---|
|`CostModel.net_return(gross, security_turnover, charge_at)`|`5c0693c8735f5ed04c8943ab1fab00c82496b22c`|没有内层/外层现金角色、多资产现金余额、分券种轧差或融资|
|`FillModel.resolve(signal_index, available_index, bar_count)`|同上|没有现金余额、BOXX 或多资产目标|
|`cost_stress_recompute(gross, traded, ...)`|`442b729dcc4be5100bc2cba5b67850ad676bfbc0`|只收汇总交易额，不能把外层 `f` 与内层证券权重轧成一笔净交易|
|`build_unified_net_report`|`b6b51d5c32594c47cc1b3ebc0c1ed226e9db887f`|缺 gross 时 cost drag 为 UNCOMPUTABLE，缺 trials 时 DSR 为 UNCOMPUTABLE；没有 RS-04 角色参数，caller-supplied digest 不是账本证明|

`docs/research/kelly-security-level-ledger-design.zh-CN.md` 仍是设计。在 QPK 提供并经准入的证券级净交易接口之前，RS-04 的现金/费用角色保持上表的并列差分，研究在这一缺口上停在 `QPK_INTERFACE_ABSENT`，不把三列 turnover 相加。


## 离线比较的共同上限（2026-10-16）

合成 harness 的 B1 与 B2 现在共用显式 `ScopeLimits`：总名义仓位上限、现金预留和其他既有暴露；产品上限仍取 B1 的同一个 `existing_cap`，不允许 B2 单独绕开。两表的目标上限为 `min(existing_cap, total_gross_cap - other_gross_exposure, 1 - cash_reserve - other_gross_exposure)`。B2 基础暴露先裁切到该上限，再乘回撤因子。任一上限缺失时两表均 `UNCOMPUTABLE`，不补 1、不当现金回测成绩；其他暴露本身已超过预算时停止。

这些是每次决策目标的合成约束；价格漂移、跳空和费用仍可能改变随后权重，不宣称逐时刻实盘保证。实际账户限额来源保持 `NOT_ATTESTED`，本模块不能读取或改变生产限额。回归夹具中的 .679/.97/.03 只复现已报告缺口，不能转成通用默认。完整成交、净成本和输入冻结规范见后续 N6 规范；当前 gross 表不能充当净 CAGR/Calmar 晋级结果。
