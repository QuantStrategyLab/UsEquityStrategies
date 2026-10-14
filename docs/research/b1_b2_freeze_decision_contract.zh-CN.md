# B1/B2 冻结决策合同草案

状态：**DRAFT / NOT_FROZEN / DEVELOPMENT_ONLY**。本文件把已有提案变成可逐项评审的决策单；没有选择新参数、批准试验、取得私有数据、写入台账或改变实盘风险门。追踪 [QRS #632](https://github.com/QuantStrategyLab/QuantRuntimeSettings/issues/632)、[QPK #659](https://github.com/QuantStrategyLab/QuantPlatformKit/issues/659) 和 [UES #588](https://github.com/QuantStrategyLab/UsEquityStrategies/issues/588)。B4另见[独立草案](bayesian_kelly_preregistration_proposal.zh-CN.md)，尾部约束另见[CVaR决策合同](cvar_exposure_constraint_decision_contract.zh-CN.md)；B5不进入本批。

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
|[Batch A历史报告CLI](../../scripts/run_c3_batch_a_existing_member_baseline.py)、[v2合同](batch_a_v2_contracts.zh-CN.md)|已有typed SMA200控制成员、来源重绑定、净ledger及#590可选十二项report-only诊断|不是RS-04完整核心路径；`ASSUMED_ZERO_USD_CASH`、来源已见development，许可/PIT/OOS未独立成立。同路径gross/完整trial缺失时cost drag/DSR明确不可计算|

旧[RS-04冻结预注册](fractional_kelly_preregistration.zh-CN.md)的252/63/5、5/10/15bps、`next_close`、BOXX、变体许可与历史manifest只提供对照来源，不转授给新B1/B2。不得根据旧默认或报告例子代填新scope的cap、数据或窗口。

## 决策记录与状态

每项冻结记录包含：具体决定、作用域、版本、来源材料及实际内容摘要、责任人/独立reviewer和批准范围、依赖、有效期/停止职责。首路径单独标`PATH_SELECTED`；其余未决定值保持`UNSPECIFIED / NOT_FROZEN`，外部证明另标`NOT_ATTESTED`。不存在的内容不填hash，private值保留于受保护位置，公开仅记不敏感的字段/状态/摘要。填表、代码合并或CI成功均不自动变为FROZEN。

|ID|待决定及必须绑定的内容|当前状态|
|---|---|---|
|D01|strategy key/profile/variant、单策略或组合scope、core-only与完整插件的精确边界、原始配置与作用域risk/cash cap来源|首路径RS-04 SOXL/TQQQ research core：PATH_SELECTED；完整scope/profile/variant/参数未冻，cap来源NOT_ATTESTED|
|D02|上述实际consumer/生成器及immutable源码、runner、lock/已安装依赖；输入/输出适用性和A2接入差分|core生成器首路径：PATH_SELECTED；#593测量源码可绑定，实际runner/安装依赖/完整适用性仍UNSPECIFIED / NOT_ATTESTED|
|D03|B1完整候选ID及角色：固定50%、quarter/half Kelly、EWMA、HAR、vol-target+Kelly cap哪些独立行；每行参数和拒绝条件|UNSPECIFIED|
|D04|B2固定基础sizing与独立对照：原阶梯、连续drawdown、normalized cushion哪些行；禁止默认B1×B2笛卡尔积|UNSPECIFIED|
|D05|同频净收益/超额收益、现金/FX、日历/年化、初始NAV与完整测试sessions；资产基准及不匹配/丢弃数处理|UNSPECIFIED|
|D06|target-vol单位/预测期、EWMA零均值或其他形式、decay、初值、warmup、决策更新时点与缺值规则|UNSPECIFIED|
|D07|HAR真正intraday RV来源/许可、采样/时区/时段/清洗/可用时点、日周月窗口、拟合/重拟合/warmup与失败规则|NOT_ATTESTED；缺合同PARKED|
|D08|B2原scope `D_max`、阶梯阈值/边界与硬gate来源；净NAV峰值延续、资金流调整、成本反馈、重启/恢复/停放规则|UNSPECIFIED / NOT_ATTESTED|
|D09|信号可用时点、`next_close`/`next_open`的主/敏感性角色、公司行动/终端未成交/末次fill及实际费时点|UNSPECIFIED|
|D10|commission/spread/slippage/融资、内部与外层成本/现金归属；成本档位属报告情景还是选择trial，不重复扣费|UNSPECIFIED|
|D11|真实source/license/PIT/coverage、raw/split/total-return价口径、公司行动与现金可用时点、实际manifest身份|NOT_ATTESTED|
|D12|train/test/purge/embargo、warmup与尾部未覆盖、untouched holdout边界/实际摘要/隔离管理员和未查看事实|UNSPECIFIED / NOT_ATTESTED|
|D13|净log增长主终点及paired difference；各指标report/ranking/veto角色、效应/统计阈值、bootstrap/PBO/PSR/DSR/MinTRL假设与停止规则|UNSPECIFIED|
|D14|CVaR描述与约束分离：tail-budget来源/单位/窗口、成本现金齐次条件、缺覆盖/不可计算规则及B1消费位置|UNSPECIFIED / NOT_ATTESTED|
|D15|每个候选/模型/参数/成本角色/敏感性/重复的trial单位；失败/拒绝/中断记录、全累计journal及同频net Sharpe方差/无Sharpe处理|UNSPECIFIED / NOT_ATTESTED|
|D16|研究owner/reviewer真实独立绑定、预注册批准和真实数据运行批准、有效期/输出位置/停止与复审权限|NOT_ATTESTED|

## 比较形式的DRAFT，不能直接运行

B1可复用原提案的零均值EWMA形式 `v_next=lambda*v_current+(1-lambda)*r_t²`，但D06未填前不选lambda/初值/warmup。HAR只在D07真实RV合同齐备后登记；只有日线就PARKED，不把平方close return改称RV，也不静默切换预测器。

B1的vol-target与Kelly组合如被选择，须显式成为D03中的一行，按相同预测期计算 `min(existing_cap,clip(target_vol/forecast_vol,0,1),clip(c*f_star,0,1))`。`c`最多0.5且只消费一次；0.5是Kelly倍数上限，不是所有策略共同50%资金暴露。固定50%对照也受同一作用域硬cap，不因名为baseline而豁免；候选不能提高既有预算。

B2形式沿原提案：净财富`W>0`、包含初始NAV的峰值`M>=W`，`d=1-W/M`、`alpha=1-D_max`；连续drawdown `clip(1-d/D_max,0,1)`与normalized cushion `clip((W-alpha*M)/(D_max*W),0,1)`分别作为待选行。`D_max`必须来自原scope、不能更宽。资金流存在时须先定净投资指数/峰值的具体解释，不能直接混用美元W和资金流调整指数；成本后峰值/决策截止规则冻结后才算。不因亏损重置M，跳空/离散费用可突破floor，不宣称硬回撤保证。

所有候选先受原hard gate、cash reserve、产品上限、breaker和veto约束，未来输出仅`DEVELOPMENT_ONLY/live_ready=False/no_order=True/budget_effect=NONE`的封顶/降低备注。CVaR缺预算/尾部或预测异常不意味着解除既有约束；具体现金处置/恢复需D06/D08/D14明定，否则研究停放。

## 冻结前的三组真实事实

1. **Scope与来源/限额包**：D01/D02/D05/D07/D09–D11/D14的具体candidate、实际输入/许可/PIT、风险/现金/尾部预算及适用runner来源。报告示例和旧数据许可不替代。
2. **Holdout包**：D12真实边界/内容摘要、隔离管理员/未查看证明及对数据修订/重新冻结的处置。旧已见development不能迁移成未见OOS。
3. **Journal与责任包**：D15/D16含全部失败等状态的真实累计journal、独立完整性审查、同频Sharpe口径及真实owner/reviewer批准。旧七行48声明不是已核验全集，缺Sharpe不补0。

参数决策也必须填入，三个事实包不自动选择它们。只要求该候选真正用到的指标/数据；逐笔统计、容量等不机械加为共同前置。三包齐备与记录可追溯后，独立review核对批准范围与内容，绑定真实版本再标FROZEN；新增研究或修订登记新身份，不改旧台账行。

## 未来合成验收与停止

以下是**尚未执行**的验收设计；具体fixture/seed、容差、模型及收集命令由冻结后的实现PR绑定，不填一个未经批准的默认seed。

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
