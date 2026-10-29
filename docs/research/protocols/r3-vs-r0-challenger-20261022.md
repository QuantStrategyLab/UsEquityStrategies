# R3 vs R0 挑战者协议（2026-10-22）

状态：内容冻结、本地预注册；**NOT_ARMED / NOT_RUNNING**。本文件与 [JSON 协议](R3_VS_R0_CHALLENGER_20261022.json) 是同一次研究定义，JSON 为完整机器可读口径。未采集或运行真实数据，未启动 trial；本地哈希不证明外部登记时间或不可修改存储。主线程审核、PR、tag 和实际 ARM 尚未执行。

## 依据与冻结身份

按本次 2026-10-22 19:39 SGT 裁定及 Dot 审计 M2 准备。沿用旧 M2 `a25512cdc50dc27a77ff7d729d9ca7a2edf548d5759a1eac7006079ec180eb88` 的 R3/R0 问题与数值，不采用另一个 R0/R1 提案。原协议和旧政策字节不改。

经济参考 commit：`04fcb6099c8dea524a331435395e98b00ad21932`；tree：`c51ac84f5f575b8cc5e5b21a7607aab49fc74dc2`。源文件逐一通过 Git blob 读取与 SHA-256 固定，详见 `version_binding.verified_git_blobs`；核心源/配置/mapping 常量完整保存在 `core_only_frozen_constants`。当前 checkout `eae5bcbd4397582b13d176d4833c02c23689b3df` 仅是准备环境，不替代经济参考或真实运行版本。

## 问题、候选与控制

交易节流的成本与操作收益是否持续，净增长/尾部代价是否在可接受边界内？控制 `R0_ORIGINAL_FULL/FULL` 对挑战者 `R3_B5_BAND_ONLY/BAND_HOLD`，绝对 pre-fee NAV 权重带固定 **0.01**，含 BOXX 证券比较。TQQQ（TQQQ/QQQM/BOXX）和 SOXL（SOXL/SOXX/BOXX）分别配对；四本账相互依赖，不合并资金或虚构分散。两臂同一 no-plugin core、原 outer f=1、输入、时钟、费用和源绑定。

不引入 R1、half、Bayesian 拟合、新 reserve、参数/窗口网格。保留核心趋势/波动规则、只多头、不融资、现金与资金守恒拒绝；不另造风险数值。实际风险边界/穿透系数仍缺来源绑定，因此尚不能 ARM 或给金融资格结论。

## 日历、可用时间与终点

使用 XNYS、America/New_York；正式日历版本与会话边界在 ARM 前固定。D0 是 ARM UTC 后首个开盘的正式会话，首次成交在下一会话收盘；自首次成交/估值起固定 **252 个连续正式交易日**，不因显著性延长或更换起点。暖启动仅用不变核心源必需的最短已见前缀，单独冻结 manifest，不把它计入前向，不新增 63 日训练等待。

信号用 D 收盘后实际已取得且在冻结 cutoff 前持久化的版本，沿用 `next_close`；旧持仓先承担下一收盘收益，再在该时点净额成交。真实 received/first_seen/persisted、源版本与 decision input-prefix digest 必须记录；生产者、可信时钟、cutoff、日历/暖启动 digest 当前均 **UNBOUND**。`first_seen` 不能由 caller、mtime 或事后估计生成；修订追加，不能覆盖旧决策。

2023–2026 已见窗口、原 2021–2026 包及以后已审阅观察都仅作 development 诊断，不能重切或改名 OOS。未来获取/私有保存许可及批准环境未核实，本协议不授予采集权，不新造 collector，不在 Mac 保存真实研究输入。

第 252 日按实际数量、现金及 BOXX 共同收盘盯市，不虚构终点零目标或强制清仓，不成交窗外 pending。披露终点持仓/待执行决策与未知清仓成本。现有回溯 consumer 的终点清仓行为不等于已符合本协议，须独立核定前向接线。

## 净账与成本情景

每本账 NAV1、真实现金1、零证券与零外部现金流，现金收益0。只有原 **5bps 单边、1x 单一情景**；不是券商实费校准。逐证券同账净成交 `fee_t = Σ 0.0005 × abs(net_trade_value)`，BOXX 计价且交易收费，真实现金只扣一次；不跨券商净额、不把 BOXX 当免费现金。净财富含费用，gross 只诊断。现金不足/guard 拒绝原样保留，不补本金、不降费、不放宽 cap、不借款。强制减险不得被 band 截断。

换手为 `Σ_t Σ_s abs(net_trade_value_s,t)/NAV_pretrade_t`，双边 L1 不除2；费用分别报 `Σfee/NAV0` 与 `Σ(fee/NAV_pretrade)`，不从费用反推旧换手。不新增费用压力网格。

## 主终点与固定检验

每 profile：`d_t = log(W_R3,t/W_R3,t-1) − log(W_R0,t/W_R0,t-1)`；**paired Δg = mean(d_t)**，单位为每交易日自然对数，另报 `Σd_t` 终点对数财富差。不是平均累计 log 财富水平。只允许同 252 日、有限且正 NAV 的配对净 simple returns；不能再把 log return 传入 log1p。

复用冻结 `kelly_disclosures.paired_stationary_bootstrap`：同索引 circular stationary block bootstrap，平均块长 **20**、重采样 **2000**、seed **20261021**；每 profile 双侧 **97.5%** 区间，两个预声明终点作保守 Bonferroni 名义校正。只复用函数，不套用同模块其他 outer-layer 成本说明。块重采样有小样本/状态变换局限，不能视为每日 iid、精确有限样本保证或未来盈利概率。

一次固定终点分析，期间只监控来源与安全，不作滚动显著性选型。四段连续 63 日按预定边界描述费用/换手持续性，不作四次独立检验、不择最佳片段，也不把一年前向称为普遍持续。重叠标签按完整时间跨度隔离，不能虚增样本。

## 辅助指标、改善与退让

联合报告 DSR（源齐备才算）、含 NAV0 的 MDD/恢复时间及终点右删失、CVaR(95%)、费用、换手、证券/穿透风险/BOXX/真实现金暴露、不可行率、HOLD/非零净成交/强制减险与终点 pending。

MDD 用正损失比例；CVaR95 用最差 5% 日净 simple return 的分数尾部均值取负，越大越差，同时披露有符号尾部收益。风险因子缺失写 null/UNCOMPUTABLE，不当0。不可行率分母保留全部252个计划配对槽，按资金/价格/数据/guard 原因分列，缺失后续财富不插补。操作计数只表明模型活动，不等实测券商订单或人工节省。

沿旧 M2 的零退让判据：最小净增长改善0、最大净增长/MDD损失/CVaR95损失退让均0；费用与双边换手须严格下降，完整披露幅度，不另设事后实质改善阈值。每 profile 只有在来源/风险/PIT/账务完整、252对齐、净增长区间下界≥0、费用和换手严格下降、MDD及CVaR损失不升、硬违规0时，才给研究 `STRICT_DOMINANCE` 描述。全研究描述要求两个 profile 都满足，不合并财富或挑赢家；这些不是实盘风险预算。其余如实报不可行、证据不足、成本节省与增长/尾部取舍或无成本收益；保留R0研究参考，任何标签均无晋级权。

DSR 当前 **null / UNCOMPUTABLE / COMPLETE_CUMULATIVE_TRIAL_SOURCE_NOT_VERIFIED**。必须核实累计全部尝试、失败/拒绝/中止/修复/敏感性及收益/Sharpe方差、矩与相关性处理；不以旧声明计数替代来源、不插0、不以 synthetic=True 绕过真实数据准入。

## 试验记录、失败与停止

一个配对研究、两个相关 profile、四本依赖账，不称四个独立 trial。`trial_accounting.known_history_index` 记录历史六单元、R1费用拒绝、返修/新候选、QQQ事件敏感性、旧M2协议；这只是已知来源索引，不伪称完整台账。历史总尝试数与有效独立trial数均 null，须原owner绑定精确journal ID/digest。修复不覆盖失败，相关单元/恢复/诊断不重复充独立试验；使用敏感性作选型则登记选择身份。

失败记录字段含 study/trial/parent/candidate、源/配置/输入/日历/成本hash、登记/开始/终态时间、原始失败与reason、repair/sensitivity、dependence group、结果digest及缺失原因。任何未知写入/STARTED不盲重跑。发生许可、可信时间/输入、身份、资金、账务、guard、非正财富、泄漏、成本遗漏、未登记选型或事后改阈值，停止受影响证据路径，封存首个失败；不得删日、补收益、重设D0或同trial重启制造完整样本。可在固定窗交证据不足。

## 预注册哈希与启动缺项

JSON 精确字节 SHA-256：

`7d10f4c3af1aaa0e640d795f300be4915685a9bb78e6d570969d8afee24a31db`

序列化：UTF-8、sort_keys=True、indent=2、ensure_ascii=False、allow_nan=False、LF、单个结尾换行。完整文件 hash 在本 Markdown 与报告外置，不在 JSON 自包含。ARM 必须引用此 hash；发布 hash 后不得改原字节。任何经济/窗口/阈值变化用保留原文的新不可变版本，且只接受该版本冻结后真正未见的数据。

未完成：可信 first_seen 生产者/代码/build/时钟/存储、未来数据许可与环境、精确日历/cutoff/暖启动、前向consumer及依赖实际构建、风险政策/系数数值来源、完整累计DSR源、主线程审核及单独ARM。DSR缺源可持续标不可计算，其余启动前置不齐不得采集/观察。协议不要求先修全平台或等待R1；若需产品接缝，另定范围并等M1写集释放。

## JSON 字段索引

- `activation_blockers`
- `authority`
- `book_and_cost`
- `data_admission`
- `existing_interfaces_and_specific_gaps`
- `frozen_decision_rules`
- `frozen_utc`
- `interpretation_rule`
- `missing_failure_and_peeking`
- `next_owner_and_gates`
- `one_question`
- `primary_endpoint`
- `protocol_id`
- `qualifications`
- `registration`
- `schema`
- `scope`
- `scope_now`
- `secondary_endpoints`
- `secondary_metric_contract`
- `status`
- `trial_accounting`
- `unseen_window`
- `version_binding`
