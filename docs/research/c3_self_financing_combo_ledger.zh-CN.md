# C3 合成 SOXL/TQQQ 自筹资金账本

本模块只服务一个离线验收案例：两条虚拟策略成员袖 `SOXL`、`TQQQ`，加上 USD 现金。输入是调用方明确给出的合成日净收益、日期、本金、固定目标权重、固定风险缩放比例、调仓日索引、组合层 bps 和费用承担成员。它不读取行情，不连接账户，也不授权下单或晋级。

`build_soxl_tqqq_self_financing_ledgers` 一次返回三本 `ResearchDailyLedger`：

| 键 | 权重 | 组合层费用 |
| --- | --- | --- |
| `raw_fixed_budget` | 调用方声明的 SOXL / TQQQ / 现金 | 0 |
| `risk_scaled` | `scale_member_budgets_to_cash` 按固定比例缩小两条风险腿，差额进入现金 | 0 |
| `risk_scaled_with_synthetic_combo_fee` | 与上一本相同的缩放后权重 | 声明的 bps，按实际风险腿成交绝对金额 |

三本账的 `synthetic` 都是 `true`。`cost_source` 固定为 `SYNTHETIC_ASSUMPTION_NOT_A_LIVE_CHARGE`。这是合成假设，不是真实收费。`cost_inputs` 里的 `combo_fee_bps` 只记录该本账实际使用的费率；前两本为 0。`cash_daily_return` 固定写 0。

输入日期、收益、权重、本金、风险比例、调仓日和费率共同生成合成输入摘要；三本账各有独立 `trial_id`，不同输入不会挤占同一个本地存储键。摘要只用于区分离线实验，不证明行情来源或策略历史真实性。

## 持仓和现金

持仓只有虚拟成员袖。初始虚拟单位价格为 1；以后按成员净收益变动。没有调仓时单位数量不变，调仓时才按目标金额与当日单位价格改变数量。这些单位不是 ETF 股数，也没有整股取整。现金写在账本的 `cash` 字段里，不另造一条现金持仓。

初始日已经按**该本账自己的目标权重**完成配置。因此没有初始建仓费，初始日也不是收益观测。`session_dates` 与两条成员收益一一对应，并且必须严格晚于初始日、自身严格递增。调仓索引 0 指第一天收益日，不是初始日。

## 每个收益日的顺序

1. 用当日 SOXL、TQQQ 净收益更新已有袖子。现金乘以 0，金额不变。
2. 若这一天在预先声明的调仓索引里，再调回该本账的固定目标权重。
3. 不在索引里的日子只记录漂移，成交额和费用都是 0。

当日成交只看当日收益之后的持仓和事先声明的目标、费率、费用承担成员。后面日期的收益不参与这一天的决策。

成员净收益已经包含该成员自己的内部费用。本模块按原样使用，不二次扣减。组合层费用只出现在第三本账，并且只在声明的调仓日、只对声明的风险腿成交收取。`SOXL` 与 `TQQQ` 才是风险腿。把现金放进费用承担成员会被拒绝。

## 自筹费用

设收益后的三腿金额之和为扣费前 NAV。费用承担风险腿的目标金额使用扣费后 NAV。费用等于 `bps / 10000` 乘以这些腿的 `|目标金额 − 收益后金额|`。扣费后 NAV 要同时满足：它等于扣费前 NAV 减去这笔费用，并且 SOXL、TQQQ、现金都能按目标权重放在这个 NAV 上。费用从可用 NAV 里出，不是在目标金额之外另加一笔。

这个解和 `simulate_fixed_budget_capital_path` 不同。后者把费率乘在调仓前的权重差上，并写明那是 pre-trade notional 近似，不会把成交额收缩到付得起费用的结果。本模块不调用那条近似来冒充现金账。零费时两边都不收费，日收益可以对照；正费时两边不必相等。

现金日收益固定为 0。这是合成假设。本模块不提供、也不读取真实现金利率。

合成组合费率必须非负且低于 10,000 bps。这个输入范围只保证账本求解有界，不代表任何真实适用费率。

## QPK 等式

账本类型仍是 `ResearchDailyLedger`、`ResearchLedgerDay`、`ResearchPositionMark`，不改 QPK。构造时沿用它已有的闭合关系：

- `cash_t = cash_prev + trade_net_cashflow - fees`
- `nav = cash + 持仓估值`
- `daily_return = 当日 nav / 上一日 nav - 1`，要求精确相等

`trade_net_cashflow` 是风险腿买卖引起的现金变动：买入为负，卖出为正。

## 机器精度残差

理论现金为 0 时，调仓前 NAV、调仓后 NAV、现金、成交净额和费用的有限浮点相减，可能留下几个 ULP 的负残差。账本保留这个有限原值，不钳成 0。QPK 用绝对 `1e-9` 核对 `cash_t = cash_prev + trade_net_cashflow - fees`；把大本金上的残差改成 0，可能让这条等式超出容差。

负现金的接受尺度与 `optimized_strategy_replay._rebalance` 一样，是资金尺度的 4 个 ULP。尺度取上述 NAV、现金、现金流和费用中较大的绝对值，不用一笔较小的成交单独定阈值，也不放宽成百分比容差。初始现金用同一标准，对照初始 NAV 和两条腿的金额。超出几个 ULP 的负现金仍是实质借款，拒绝码为 `SELF_FINANCING_CASH_NEGATIVE`。费用求解器不因这条容差改写。

## 边界

目标可以是全现金，或只留一条风险腿。风险缩放比例为 0 时，缩放后的两本账把全部金额放进现金。正费率但没有调仓日、费用承担成员为空、成员不是风险腿、权重不闭合、收益与日期对不齐，都直接拒绝，不补一笔默认费用。几个 ULP 以内的负现金残差不是实质借款；再往下的资金缺口仍然拒绝。

## 独立的固定预算三账报告

`c3_fixed_budget_baseline_comparison.report_self_financing_combo_baselines`
接受至少两组已声明的固定预算及其上述三本实际 `ResearchDailyLedger`。
它只比较这些固定配置，不选权重、不估计 Kelly、不运行真实行情回测。
原 `compare_fixed_member_budget_baselines` 的每日固定权重、ddof=0、自然日
CAGR 和可选 pre-trade notional 费用近似保持原义，不被新报告替换。

所有参数均显式提供：

```python
report_self_financing_combo_baselines(
    *, baselines, soxl_net_returns, tqqq_net_returns,
    initial_session_date, session_dates, initial_capital, as_of,
    asset_risk_specs, risk_policy,
    annual_risk_free_rate, annual_minimum_acceptable_return,
)
```

每个 `baselines` 项包含 `baseline_id`、`target_weights`、`risk_scalar`、
`rebalance_indices`、`combo_fee_bps`、`fee_bearing_members` 和 `ledgers`。
ID 必须唯一且按序提供，`ledgers` 必须完整包含原始、缩放、缩放付费三账。
`target_weights` 明确包含 SOXL、TQQQ、CASH，包括权重为 0 的成员。
共同参数声明两条成员净收益、初始本金和完整的预期 session 序列。
合成回归沿用既有 50/50、60/40 固定预算例，并覆盖全现金和单腿边界。

消费者调用现有小账本构造器复算这些声明，只用于逐本核对传入的 typed
账是否与声明完全一致；它没有另建资金引擎。缺账、缺 session、非有限收益、
未来或迟到/重复日期、改费用/调仓/权重后仍传旧账，均拒绝整个报告。
`as_of` 是调用方声明的上界，所有 session 都不能晚于它。不输出“只剩成功
基线”的子集，不修补输入，也不重新选择配置。这些检查验证合成算术和声明
一致性；它们不认证实际生成配置、数据许可、真实缺失交易日、publication
或 available_at。真实共同根和 PIT 仍需独立输入证据。

静态风险诊断复用 `assess_portfolio_risk_budget`。输入的风险缩放比例必须
与其在调用方原预算下的建议一致；推荐的现金/风险权重也与现有缩放函数
对照。报告同时保留原始账作为对照和两本风险缩放账，不修改预算或引擎。
风险政策的现金标的仅作该诊断的符号映射，不会把合成零收益 CASH 改成
BOXX 或其他标的的真实价格历史。诊断约束初始/调仓目标，未宣称持续约束
漂移后的风险敞口；没有新 allocator、每日免费调仓或实盘资金授权。

新报告的 schema 为 `qsl.c3-self-financing-baseline-metric-report.v1`。
每组 `books` 记录三账的初始/终止 NAV、初始/终止现金、实际组合费用和
独立 `metric_report`，并保留固定目标、缩放目标、调仓/费用声明及风险诊断。
组合收益来自每本共同账户的净 NAV，不能相加单策略指标。成员费用已在输入
净收益中，组合实际费用已在账本净 NAV 中；报告均不二扣，现金收益也不
额外计提。输入和三账保持只读；相同声明重算报告可复现，不创建存储状态。

## Declared-session 指标合同

上述每本账调用
`tqqq_core_trial_journal.compute_declared_session_research_metrics`，只接受
`synthetic=True`、`calendar_id="synthetic_declared_sessions"`、252 年化基数
及无外部现金流的账户。新合同版本是
`synthetic_declared_session_account_metrics_v1`，明确把 252 步/年标为合成
年化假设。它保留原 calendar 标签，不认证交易所日历或真实年度历史。
原 `compute_synthetic_research_metrics` 仍只接受 synthetic XNYS/252，并
保留 `synthetic_account_metrics_v1` 的逐字段输出和拒绝边界；新入口也拒绝
XNYS 或真实数据，二者不能互相改标签混用。

两入口复用同一已有算术：signed simple account net returns、包含零收益步、
初始 NAV 参与 MDD 而不多算一条收益、sample ddof=1 波动与 Sharpe、全样本
MAR shortfall RMS 的 Sortino。rf 和 MAR 分别是显式年简单小数，除以 252
得到步长率，CAGR 使用 252/N。常量、短样本或零分母用 None 和具体原因，
不补 0；亏损保留负号。DSR/PBO 为 `NOT_COMPUTED`，有效独立样本为
`NOT_ESTIMATED`：这些单路径和固定预算比较没有完整 trial 集合及真实依赖/
多重试验假设。报告始终 research/synthetic、report-only、非晋级、不可实盘
或配仓。真实净成本 OOS、联合风险资格与成员资格不由合成测试授予。
