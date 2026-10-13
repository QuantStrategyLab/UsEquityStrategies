# CVaR尾部预算与B1上限决策合同草案

状态：**DRAFT / NOT_FROZEN / REPORT_ONLY**。原审计p6要求“CVaR95从报告升级为约束，按尾部损失预算倒推仓位上限”，位置是B3报告与B1上限。本文件定义可审查输入、适用条件和拒绝边界，不实现约束、不选择预算/阈值、不发出仓位或执行权限。见[B1/B2决策合同](b1_b2_freeze_decision_contract.zh-CN.md)、[B4独立草案](bayesian_kelly_preregistration_proposal.zh-CN.md)，追踪[QPK #659](https://github.com/QuantStrategyLab/QuantPlatformKit/issues/659)及[QRS #632](https://github.com/QuantStrategyLab/QuantRuntimeSettings/issues/632)。B5不进入此范围。

## 原文、现状与不变边界

原PDF12页SHA256 `f60ce29f267722245cf62e308883e11b001f38efc3ee2de9d6c861806acb1bfb`，p6明确尾部约束、p10要求预算只能降低/封顶。已合并[QPK B3](https://github.com/QuantStrategyLab/QuantPlatformKit/blob/b23511cbb28b72743e35512ef4b3c73f1cd43c23/src/quant_platform_kit/research_stats/unified_report.py)的 `cvar95_return_per_period` 是signed-return经验尾均值、`descriptive_only=True`；UES `0e66469c95a72c6248eae3ea474223d9ff6e1df5`的#590只把它接入已有历史CLI，不包含尾部预算或B1约束。

后续接入顺序是**同频净收益与来源/覆盖 → B3尾部描述 → 已冻结tail-budget约束判定 → B1研究目标受限 → 仍取原scope硬cap与veto**。任何一步未知，不用0、null或经验样本“全正”解除原风险门。不存在实际tail-budget内容时保持NOT_FROZEN；文档不把它变成实盘政策。

## 精确统计量及单位的DRAFT

现有B3对N个同频简单净收益每条给质量`1/N`，取最差5%质量并拆分边界观察，不四舍五入成至少一个完整“亏损交易”。零收益观察保留；它是period-return统计，不是closed-trade或人口风险保证。原legacy评估器取最差若干整日的近似定义不偷偷替换，冻结记录须绑定实际统计实现。

建议先区分两种候选单位，**本文件未选其中之一**：

|量|需冻结的含义|
|---|---|
|`R(w)`|精确scope、同预测期/窗口、已经计成本与现金/FX的组合简单净收益，资金暴露w的收益路径；初始NAV不是一个收益观察|
|`L(w)`|与R同频的损失；若决定为`-R(w)`，正表示亏损，负表示收益。是否另采用非负损失变换须另登记，不隐式clip|
|`ES_0.95(L)`|损失上尾5%质量的经验期望；只有同样本且`L=-R`时，等于`-cvar95_return_per_period(R)`。尾概率0.05是既有统计定义，不是风险通过阈值|
|`B_tail`|真实原scope的最大允许同频尾部损失预算：净收益小数比例或同币种金额何者、对应NAV/FX/时点，及批准来源。二者不能直接混用|
|角色|描述report、研究目标cap或veto分别声明；经验ES不自动提供置信度/独立样本量或晋级结论|

为负或零的ES不是可安全除数，也不能证明没有未来尾部风险。样本覆盖、可用截止、重复/修订、有效观察、经验尾质量及缺数规则须逐项报告；不足或未冻结标准时约束不可计算，不把估计器默认30观察当尾部风险准入规则。

## 比例倒推的条件

只有冻结并核验下列条件时，才有本草案的**条件性数学关系**：同样本、同频率/货币、确定零收益现金、非负w、相同单位风险路径，且全部净现金/成本随暴露严格正齐次，使`R(w)=w*R(1)`、`L(w)=w*L(1)`。经验ES满足`ES(wL)=wES(L)`。

在预算已被绑定、`B_tail>=0`、`e=ES(L(1))>0`且有覆盖资格时，数学条件才可写作 `w<=B_tail/e`；研究上限至多为该值与原scope上限的共同最小值，仍受现金、产品、breaker/veto限制。这不是已批准预算或本系统已可运行的上限。`e<=0`、非有限、缺预算/单位不符时不算比例、不输出无限或新增风险目标。

以下任一情况都不能沿用上述比例：非零/随机现金腿、固定/最低佣金、不同暴露导致换手或信号/持仓漂移改变、融资/冲击非线性、公司行动/费用币种未对齐、初始持仓/外部资金流改变路径，或gross/net overlay与自融资权重反馈混用。尤其不能从已经扣费的一个net样本先除/乘w，冒充其他暴露的净损失。

不满足齐次性时，先在同一冻结A2/现金/净成本合同下定义各已登记暴露对应的`R(w)`；是否允许数值求解、单调性/可行集合/精度及失败规则仍须决定。不得默认二分、扫描新候选或以一处可行点代表`[0,w]`都安全；未定义或未验证就标`NOT_APPLICABLE / UNCOMPUTABLE`，保持原cap不放宽，而不是伪造实盘上限。

## 待冻结决策表

每行保存具体值或材料位置、scope/版本、真实来源摘要、owner/reviewer批准范围、依赖与有效期；private金额/账户在受保护位置保存。当前所有具体参数未选，不填写假摘要。

|ID|必须决定/绑定|当前状态|
|---|---|---|
|T01|candidate/profile/variant、证券/账户或模型scope、资金暴露单位与原hard/risk/cash/product cap|UNSPECIFIED / NOT_ATTESTED|
|T02|R/L的净收益/损失符号、频率/预测期/窗口、现金/外部流/FX/NAV分母、未平仓边界|UNSPECIFIED|
|T03|既有经验fractional-tail实现的版本、alpha是否沿用、真实样本/可用时点与覆盖规则、观察/尾质量身份|UNSPECIFIED / NOT_ATTESTED|
|T04|tail-budget值/单位/币种/期限、原scope批准来源与风险角色；无预算不填0|NOT_ATTESTED|
|T05|净路径对w正齐次的实际合同/证明，或精确非齐次路径评估及已登记求解/可行性规则|UNSPECIFIED|
|T06|费用/价差/冲击/融资/现金随暴露变化、终端fill、成本反馈与真实/假设成本的分界|UNSPECIFIED|
|T07|缺数、非有限、覆盖不足、e<=0、预算/身份变更时unknown/PARKED、原cap优先及恢复行为|UNSPECIFIED|
|T08|B3输出记录与B1消费位置、report/cap/veto角色；不得自动进入排序/晋级或live sizing|UNSPECIFIED|
|T09|如果使用不确定性/压力修正：方法/窗口/seed/置信/效果角色及trial计数，不能擅设额外安全系数|UNSPECIFIED|
|T10|实际数据/PIT、untouchedholdout、全累计journal、runner依赖和独立owner/reviewer批准与停止职责|NOT_ATTESTED|

## 三组事实与未来验收

外部事实与B1/B2共用三组，不另建平台：①精确scope、实际来源/许可/PIT与原cap/现金/**tail-budget**；②实际holdout内容/边界、隔离管理员与未查看事实；③完整累计含失败等状态的journal、计数口径与独立owner/reviewer。若只验证纯合成算术，不能据此认证真实预算/尾部来源；进入新研究仍先满足其冻结边界。

以下均为**未执行的合成验收设计**：

|案例/性质|预期验证，非真实策略数字|
|---|---|
|手排尾部与fractional边界|同一个固定人工净收益样本核对R和L符号、边界权重、含零/无亏损情形，不把正收益日当交易|
|齐次输入|同样本、零现金和齐次费用的已冻结fixture，核对非负w的ES比例关系；合法B/e仅在正有限e和已绑定budget下成立|
|非齐次反例|固定佣金/现金收益/动态换手等人工路径违反比例时拒绝shortcut，不添加未登记扫描|
|缺项/极端|缺预算、尾部覆盖不足、e<=0、非有限/溢出、币种频率错配均明确不可计算，不补0或放宽cap|
|风险与消费者|同一B3记录被B1消费后不超过原硬cap；unknown/旧veto/breaker优先；不改变legacy/report默认或生产pin|
|因果与成本|未来/迟到修订不倒灌旧决策；净成本不二扣、entry/exit/末次fill完整，缺gross不假造cost drag|
|可重放完整性|绑定model/data/runner/budget身份，重复消费/失败/中断有记录；不同候选/敏感性按冻结规则计trial|

未来实现的文件/数值参数、合成fixture、收集命令及实际测试证据由相应冻结后的实现PR给出；本批不创建计算模块。来源或预算身份漂移、单位/齐次证明失败、不可计算被当作通过、原风险被覆盖时停止研究并保留失败，不能为变绿调整预算或drop坏尾部。E1仍独立验收运营正确性与统计边际，本文件不授权paper/live/交易/预算增加/候选启用。
