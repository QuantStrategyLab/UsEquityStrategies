# Global ETF：单一绝对波动缩放研究候选

## 范围

在既有 history 回测入口 `UsEtfRotationBacktestRunner.run` 的 params 中显式设置
`research_absolute_volatility=True` 才启用；默认及 False 完全沿用原策略。
字符串、数字不能替代布尔开关。生产 snapshot、manifest、交易入口不消费此模块。
本批实现固定 126 个交易日收益、15% 年化目标，不搜索参数；人工候选工程实现不构成晋级或交易授权。

## 未来独立 OOS 合同（2026-09-18 冻结）

在启动日前锁定以下合同；到期后不得事后改参、换基准或放宽通过规则。

| 项 | 冻结值 |
|---|---|
| 候选 | UES `global_etf_absolute_volatility`（`research_absolute_volatility=True`） |
| 窗口 | **2026-10-01** 至 **2027-09-30**（含端点按既有 session/日历语义） |
| 费用 | 默认 **10 bps**（买卖全腿，复用既有模拟器） |
| 比较基准 | 原策略（开关关闭）、**VOO**、**BIL** |
| 记录指标 | 波动、最大回撤、收益代价；允许 `NO_IMPROVEMENT` / `INCOMPLETE` |
| 权限 | `research_only` / `no_order`；不授予 shadow、paper、live |
| `locked` | **true**（相对本未来窗口；2017–2024 learning 仍非本 OOS） |

未到窗口开始前：可只读核验输入/代码身份，不得用历史 learning 回填本窗口，不得为通过调参。
AI 真模型评审仍须另授权与额度条件；本冻结不触发 `resume_deferred`。

## 计算

先调用原 `build_target_weights`，保留选股、相对分配、持仓奖励与季度调仓规则。
仅当原结果为 `rebalance`，用截至信号日最近 127 个对齐收盘价计算 126 个日收益。
以原非 BIL 目标权重加权，按样本标准差（ddof=1）乘 sqrt(252) 得到 sigma。
缩放系数为 min(1, 0.15/sigma)，sigma=0 时为 1。同比例缩减风险资产，差额转入 BIL。
已有 BIL 权重保留，低波动不增加风险；hold、emergency、纯避险结果原样返回。
缺失或非法价格、重复报价、历史不足均不能用填零、前向填充或延长历史窗口修补。
波动检查针对最近窗口中被选中的风险资产，其他基础输入仍由原策略及模拟器检查。

这是风险资产收益贡献的缩放，不是完整组合的波动上限，也不保证最大回撤。
BIL 仍有收益、波动和交易成本。窗口和目标记录在回测结果 params 中。

## 来源与方法差异

[作者学术主页](https://sites.google.com/view/alanmoreira/)提供摘要与
[Moreira–Muir 原论文](https://amoreira2.github.io/alan-moreira.github.io/VolPortfolios_published.pdf)。
原论文采用上月方差倒数缩放，本候选不是其直接复现，也不能承接其收益结论。
[Cederburg 等的研究](https://www.lehigh.edu/~xuy219/research/COWY.pdf)提供样本外未系统胜出的反向证据。
固定作者摘要已接入 AAB；[run35136606479](https://github.com/QuantStrategyLab/AIAuditBridge/actions/runs/35136606479) 认证通过，因 quota deferred 未执行模型。本候选由人工实现；AI 评审与金融验收仍待完成；旧 NBER 案例仍停止。

## 验证与限制

- 合成数据检查缩放数学、权重守恒、低波动不增仓、hold/避险不变、缺价拒绝及信号时点截断。
- 实际 runner 合成周期验证开关被消费、参数被记录、关闭时与基线一致。
- 复用既有模拟器的现金、份额漂移和买卖全腿费用；当前默认费用为 10 bps。
- runner 按月末检查，原策略在季度月末正常调仓，不宣称每日波动保护。
- close-only lag-one：以 d 收盘信号和 d 收盘价格模拟 d→d+1 持仓，属于研究近似，不证明实时可成交。
- 真实比较按上方已冻结未来 OOS 合同执行；2017–2024 learning / VOO·BIL 比较不得改称本窗口 OOS。
- 本批没有真实行情回测、参数择优或模型生成代码试验。人工工程实现不等于系统 AI 联网设计已成功。
