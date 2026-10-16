# 离线复利研究：成交、指标与输入冻结规范

状态：规范已选定；候选、参数、真实输入及运行批准仍 `NOT_FROZEN / NOT_ATTESTED`。仅研究，不产生订单或修改生产配置。对应 #575、#588、QuantPlatformKit#659；沿用 [B1/B2 决定合同](b1_b2_freeze_decision_contract.zh-CN.md) 和 [B4 预注册](bayesian_kelly_preregistration_proposal.zh-CN.md)，不重写旧试验身份。

## 成交时点

主比较统一 `next_close`：完成交易日 d 收盘后可用的信号，在下一实际交易日 d+1 收盘成交；新持仓从 d+1 收盘到 d+2 收盘这段收益开始生效。成交费用在实际 fill 时点扣除，最后一个可成交收盘也计费；没有下一交易日就不成交。按输入的有序交易 sessions 前进，不按日历日加一；来源的完整日历/coverage 必须另有证明，不能仅凭相邻记录推断没有漏日。

`next_open` 只作另行登记的敏感性 trial：旧持仓先承担隔夜 gap，再在下一实际交易日开盘成交，新持仓承担当日 open→close。缺 open 拒绝，不用 close 或前值代替。原 `NEXT_TQQQ_OPEN` 表示这一类，不能与主时点混比。

`same_close_research_only` 仅作假设对照，不能晋级。A2 内部旧 enum `same_close` 在报告边界映射为这个名称；不把旧 trial 的 hash 改写成新身份。ETF 原默认保持旧行为并显式标记；主比较必须显式选择 `next_close`，不能静默迁移旧基线。

新 ETF 结果及 RS-04 `CorePath`、A2 `ExecutionPath` 都提供 `timing_execution`。输出到 `qsl.backtest_report.v1` 时填 `timing.execution`；信号、available 和实际 fill 时点分开记录。ETF/A2 比较入口拒绝混 timing、混 sessions 和混费用合同，禁止 inner join、补零或选出表现较好的时点充作主结果。声明时点和通过合成测试均不证明真实 runner 已采用。

## 几何增长、Calmar 和最大回撤

唯一计算定义为 QPK `qpk.net_compounding.v1`，实现于 `research_stats.unified_report.build_unified_net_report` 的显式 `periods_per_year` 选项；旧生产 pin 不升级。传入同频、完整、同窗口的**净简单收益** r_t，真实零观察保留，初始 NAV 不占一次观察。股票日线选 P=252，n 为收益观察数；初始状态占位零在进入该接口前剔除，不能删真实空仓日。现金、费用、FX、公司行动和外部流入/流出必须在收益构造时按冻结合同处理。

- 净 log 增长 g=`sum(log1p(r_t))/n`；几何 CAGR=`expm1(P*g)`。合并 folds 时按真实 OOS 净路径拼接，重叠/缺口按预注册规则处理，不算术平均各折 CAGR。
- W_0=1，W_t=product(1+r_i)，峰值含 W_0。带符号 MDD=`min(W_t/max(W_0..W_t)-1)`≤0；报告损失幅度时明确写 `abs(MDD)`，不能混用符号比较器。
- Calmar=`net_cagr/abs(MDD)`，同一窗口同一路径。负 CAGR 保留负比率；MDD=0 时 `null/UNCOMPUTABLE`，不能以无穷大或 0 代替。缺失/非有限/空输入或 r≤-1 拒绝，数值失败不进入门槛。
- 本比较策略/基准 sessions 必须完全一致；现有生命周期 v2 的 inner-join 诊断保留，不能直接当作已满足此合同。周期胜率/盈亏比不是逐笔统计；真正闭合交易单位、fills、费用、FX 和对账继续属于 #659 Q06。

净 log 增长为主终点；CAGR、Calmar、MDD 为本次共同报告项。每项是否排序或否决、阈值、方向、配对基线、成本情景和不可计算处理，在查看 holdout 前由 scope owner 冻结；这里不编造门槛或 PASS。合成 B1/B2 harness 只报 gross，不得直接改名为净指标。

## B1/B2 共用冻结包

填入既有 D01–D16，绑定真实材料，不能仅写一个 hash 字符串：

|内容|必须绑定|缺失时|
|---|---|---|
|scope 与身份|策略/profile/variant、完整候选与各自角色、core-only 边界、owner/独立 reviewer、批准范围|NOT_FROZEN；不把旧 RS-04 许可转授|
|源码与 runner|不可变 UES/QPK/QSP commit、依赖锁定与实际 import 身份、入口/命令/输出位置、合格执行器|不计算真实试验；源码合并不等于采用|
|数据|合法来源、许可/保留范围、精确对象 generation 与真实内容 hash、sessions/日历/coverage、时区/PIT/available、raw/复权价与公司行动|不下载、不补数据、不捏造 manifest|
|初态、现金和费用|初始持仓/NAV/峰值、资金流处理、内层/外层现金与 BOXX 角色、费用/FX/融资、terminal fill、净额/成本反馈适用性|停放；不能将独立 NAV overlay 当自融资账本|
|共同风险边界|同一 product cap、total gross cap、cash reserve、其他暴露及来源；更严限制优先；breakers/veto 语义|任一缺失则 UNCOMPUTABLE，不能补 1 或更换生产限制|
|B1|固定50%受原 cap；c∈{.25,.5}仅乘一次；EWMA+Kelly 行、target-vol 单位、decay/初值/warmup、f_star 估计范围和时点|数值仍由 owner 决定；HAR 缺真实 intraday RV 合同为 PARKED|
|B2|单一共用 base sizing、原阶梯阈值与连续 D_max、净 NAV peak 延续/资金流/重启/缺失规则|先裁 base 到共同预算再乘因子；不做 B1×B2；不宣称跳空下硬 floor|
|统计与留出|train/test/purge/embargo、warmup、untouched holdout 边界/真实内容 hash/管理员/隔离与未查看证明、主/敏感性/费用情景角色及停止规则|已见 development 不改名 OOS；旧污染窗口不修饰成干净|
|trial journal|实际累计内容与完整性核对；候选/参数/成本角色/时点/attempt ID；失败、拒绝、停放、中断、放弃和修订均保留；同频净 Sharpe 全集|旧七行48声明不是审计全集；缺 Sharpe 不补0；不改旧行|
|运行 envelope|精确 submitter workflow/ref/project/scopes、独立研究身份、预算/时长/重试/输出/停止职责及单次授权|本离线任务不执行或花费；worker 身份不等于 submitter|

共同目标上限=`min(product_cap, total_gross_cap-other_exposure, 1-cash_reserve-other_exposure)`。这是目标约束，价格漂移/跳空/离散费用的行为须在真实净模型单独证明。生产原限制不由研究代码改变。

## B4 冻结包与顺序

先冻结 B1/B2 并取得可靠净对照、完整 holdout/journal 事实，之后冻结 B4，再做纯后验模块和合成验收，最后交独立 E1。B4 保留原式 `m/(sigma²+tau²)` 的研究提案，不擅选 Beta 胜率模型或超参；先验/似然、三个量估计器、观测/决策频率、utility/损失、更新/遗忘/重置、可用时点、去重/修订/重启、数值与缺失失败、已有 cap 和单次 fractional 系数都须指定。若 sigma² 已是 posterior-predictive variance，不能再加 tau² 重复计算。

B4 验收要有手算后验、冻结模型的批量/逐步更新关系、未来扰动不改前缀、重复消费/重启/缺失/非有限反例，以及同窗口净成本/现金/cap 对账。未冻结时保留 `UNSPECIFIED`，不预造后验结果。CVaR 的 descriptive report 仍不能替代真实 tail-budget→B1 约束。状态始终 `DEVELOPMENT_ONLY/live_ready=False/no_order=True/budget_effect=NONE`，不扩 B5、自动扫参或生产启用。
