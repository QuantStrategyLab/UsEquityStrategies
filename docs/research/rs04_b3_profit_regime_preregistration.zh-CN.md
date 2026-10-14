# RS-04 B3 Phase 2 预注册

候选 `rs04-b3-profit-regime-v1`，2026-10-15 登记。范围仅 UES research；不注册生产插件，不改 B1/B2、生产 pin、实盘、shadow、账户、权限或凭据。代码及合成测试通过不能证明策略有收益优势。

唯一参数来源为 [`Registration`](../../src/us_equity_strategies/research/rs04_b3_config.py) 的不可变 `CONFIG`，报告输出完整参数及 SHA256；CLI 不接受调参。此次固定一个 P+状态分类+MR 候选，不执行 Phase 1 的 E/L/V 或 ATR 参数搜索，也不做 B1×B2×B3 参数组合。Kelly 的上游估计使用已有 `fit_fractional_kelly` 的训练期方法；本次合成 fixture 的 f*=4 只用于算术验收。

## 时点与信号

按 owner 决定：完成 t-1 收盘信号，t 收盘成交，t+1 才赚新权重收益。仅日线确认，不模拟盘中止损。QQQ/SOXX 的真实 OHLC 产生 Wilder ADX14、MA200 的20观察 log 斜率、RV20 对之前252观察的分位、Wilder RSI2；不得从 close 编造 high/low。交易 ETF 的 ATR14 用于保护。特征不得晚于决策可知，禁止重复或倒序 session。

TREND 连续3次 ADX≥25、价>MA200、斜率>0进入，连续3次 ADX<20退出。RANGE 连续5次 ADX≤18、斜率绝对值≤.02、RV分位<.8、价≥.98×MA200进入，连续3次 ADX>22退出。新状态保持至少5观察；hard risk、下跌趋势风险退出和 RANGE 波动退出立即生效。其余 TRANSITION 不增加风险、不新开 MR；已有 MR 离开 RANGE 即退出。

P 在真实首次成交固定 R=2×当时已知 ATR，盈利达2R后启用 `max(旧stop,含买费成本价,收盘峰值−3ATR)`。只收紧；部分卖出保留 episode/R/峰值；增加仓位更新成本但不重置 R。保护退出后冷却5 session，且需原趋势信号先关闭再重新出现才可恢复趋势仓位，避免旧目标立即买回。

MR 与趋势风险仓互斥，只有 RANGE、RSI2<10 新开；RSI2>60、5 session、离开 RANGE、初始2ATR stop、原风险或盈利保护触发均退出。名义≤10%，预计初始损失≤NAV .25%，还受剩余总cap/单标的cap约束。跳空可突破预计损失，不保证损失上限。释放预算留真实现金，不免费购买 BOXX。

## B1/B2 与证券账本

冻结合约 [`52e7c70`](https://github.com/QuantStrategyLab/UsEquityStrategies/blob/52e7c7062a9b2fa367d2e27cce1756efa8180e54/docs/research/b1_b2_freeze_decision_contract.zh-CN.md) 不修改。B3 先改变信号目标；B1 `min(1,.25*f*,.20/年化EWMA预测波动)` 只消费一次 Kelly，B2 原阶梯只消费一次：回撤≤10%为1，≤20%为.5，>20%为0。EWMA λ=.94，前63日样本方差初始化，之后零均值递推；预热/无效预测暴露0，仅用 t-1。

SOXL 总名义≤.97、SOXL≤.679、现金≥.03、有效≤2.328；TQQQ 总名义≤.98、原回撤预算.20。20%回撤预算不是跳空下的硬保证。BOXX 及其他 ETF 计入总名义、买卖两腿计费。

证券级账本先漂移再按净额整股成交，费用回馈净现金与后续 NAV，包含末次fill。按扣费后 NAV 解可用预算，保留现金；分红 ex-date 应收，pay-date 可花；拆股保值，不能正确处理的碎股现金补偿拒绝计算。episode 使用累计已实现+剩余浮盈+分红，部分卖出不清除历史。报告保留 open/closed 标记。

报告三条路径：不改 sizing 的 live-as-is baseline、仅 B1/B2 的诊断对照、B3→B1/B2。逐日研究私有结果含 B3目标、B1/B2因子、扣费后实际敞口，避免把多次降风险误报成信号优势。合成 CLI 使用虚构控制函数，明确不属于 live baseline。

## 真实数据、成本与评价

owner 指定的 baseline 是当前 live 规则原样回放。旧 `no_plugin_core` 关闭收入层、外部状态、保留比例、危机与 runtime gate，不能冒充此基线。必须绑定有效代码/配置、各历史输入及可用时点，并针对各自净权益调用原规则；只存一条目标 tape 不足以重放路径相关收入层。

依 d_unspec 提议明确登记 PROVISIONAL：Alpaca SIP raw+公司行动；每边佣金5bp、含价差滑点10bp，佣金取 max(5bp成交额,$1,$.005/股)；2×全部摩擦及最低票费。初始本金$100000。卖出规费及真实成交校准未定，不能声称实际净收费完整。真实源许可/PIT与当前live有效配置仍须有证据，不因提案自动具备。

PROVISIONAL holdout 为 2026-11-01—2028-10-31 XNYS sessions，冻结前绑定独立隔离管理员，期末一次开封。2023-03-28—2026-08-25 为已见 development，不冒充 untouched。2026-10-15 无法计算未来 holdout。

WFA 保留规格的504训练/126验证/126测试、5 purge/5 embargo，每126观察前移，至少4个非重叠测试折；特征预热不计收益。固定候选，不用 validation 改参数。持仓、净峰值、费用与 episode 连续，不在折边界免费恢复仓位。共同证券历史不足时不回填上市前资产。

owner 核心验收：B3 CAGR 至少保留正 baseline 的90%，且 MDD/盈利回吐明显降低。本候选把“明显”预登记为相对下降≥20%，不看结果后变更；这属于研究操作定义，不声称 owner 单独批准此数值。另保留 Phase 1 的证据门槛：完整live可比、≥4折、≥6个baseline盈利≥2%的63-session区块、MR≥30个闭合episode跨≥3段RANGE、误差评估与holdout方向一致、2×成本压力及累计trial统计。基线 CAGR≤0 时比较净log增长绝对差，不用比例。

必报 CAGR、含初始NAV的MDD、Sharpe、Sortino、Calmar、固定63-session配对回吐、证券单边换手、状态统计与合成暴露。Sharpe/rf和Sortino目标固定0，并明确现金假设。DSR/PBO 只在完整累计trial可核实且统计接口支持真实数据时计算；当前 pure helper 仍拒绝 `synthetic=False`，不得传 True 冒充合成来绕过。缺失时 null+UNCOMPUTABLE。没有可比基线或未来holdout，验收为证据不足，不建议 shadow。

## 工程复现

`PYTHONPATH=src python -m pytest -q tests/research/test_rs04_b3.py tests/test_research_import_boundary.py`

`PYTHONPATH=src python scripts/run_rs04_b3_research.py --synthetic --output /tmp/rs04-b3-synthetic.json`

上述只产生虚构数据。真实数据只能在已有批准的云端运行；Mac 不接收行情、逐日收益、成交或 checkpoint，公开 artifact 仅聚合结果及具体未完成原因。
