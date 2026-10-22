# R-RISK-01 / R-RISK-02：风险等级研究参数与 schema

状态：**RESEARCH_SCHEMA_APPROVED / NOT_ARMED**。用户于 2026-10-22 21:07 批准 c=0.10/0.25/0.40 与 schema；R-RISK-02 在基线 UES `b018372bdbdf7e10a863099963e1dd4f5cbde88c` 上扩展研究内核参数域。本目录由 R-RISK-01 `e259e84d` 的设计演进为 `r-risk-01.research.v2`，只收紧 c 的枚举与区间，不更改其余参数默认值、覆盖权限或二次确认字段。既有账户预算、生产配置和执行授权不变；其他风险数值仍是研究建议。Grok Bot schema 复核待完成，三档六个对照均 `NOT_RUN_BLOCKED`，真实 trial 未运行。

## 1. 使用方式与建议值

简单模式只选择保守、稳健或进取；系统在**既有账户预算内**建议策略目标、真实现金和再平衡动作。三个名称是同一受限策略集合的相对档位，尤其“保守”不代表保本。高级模式只覆盖允许的字段，始终显示系统建议和覆盖值。新增等级用稳定 id 和新版本，不修改既有 id 的历史含义。

以下数值是事先提出的产品设计假设，**没有通过历史结果挑选或校准**。目标波动不是必须用满的风险额度，未达到目标也不补仓。现有预算低于建议时取更严限制；预算未知就不产生可执行建议。不存在靠选择“进取”提高账户预算的路径。

| 参数（小数比例） | 保守 `conservative` | 稳健 `balanced` | 进取 `growth` |
|---|---:|---:|---:|
| 年化波动目标/上限 | 6% | 10% | 15% |
| 峰谷最大回撤容忍（损失为正） | 8% | 12% | 18% |
| 监测用日净损失 CVaR(95%) 上限 | 1% | 2% | 3.5% |
| 执行前两会话 CVaR(95%) 预算 | 1.5% | 3% | 5% |
| fractional Kelly `c` | 0.10 | 0.25 | 0.40 |
| 任一非现金证券名义权重上限 | 15% | 25% | 35% |
| 杠杆 ETF 合计名义权重上限 | 10% | 20% | 30% |
| 总有效风险敞口上限（穿透倍数×权重） | 0.50 | 0.75 | 1.00 |
| 单经济底层有效敞口上限 | 0.30 | 0.50 | 0.70 |
| 战略真实现金下限（费用后） | 20% | 10% | 5% |
| M1 费用预留（费用前，额外） | 2/2001≈0.09995% | 同左 | 同左 |
| 费用前总现金下限 | ≈20.09995% | ≈10.09995% | ≈5.09995% |
| 再平衡：绝对权重 band | 2 个百分点 | 3 个百分点 | 4 个百分点 |
| 再平衡：相对目标 band | 20% | 25% | 30% |
| 日净损失熔断 / 峰谷回撤熔断 | 2% / 6% | 3% / 10% | 4% / 15% |
| 单策略止损 / 止盈 / trailing 距离 | 3% / 10% / 4% | 5% / 15% / 6% | 7% / 20% / 8% |
| 恢复观察期（完整交易会话） | 5 | 5 | 5 |
| 升级冷静期 | 48 小时 | 48 小时 | 48 小时 |

全档默认 long-only、无融资，证券总名义权重≤1；高级模式也不能改变。TQQQ/SOXL 日收益直接使用自身价格/策略收益，不再乘 3；3 只用于静态穿透敞口。本卡不增加期权或其他资产。两个独立券商账户分别计算，不跨账净额、借现金或相加 NAV。若同账户并存，合计杠杆 cap 同时约束 TQQQ/SOXL，相关情景必须联合评价。

`c` 是 Kelly 比例，不是最终仓位。二阶近似、零现金无摩擦且未触及约束时，沿理想 Kelly 方向的理论增长约为 `(c-c²/2) A`、波动约为 `c` 倍；相对完整 Kelly 的近似增长比为 `2c-c²`，三档约为 19%/43.75%/64%。这里只说明比例的权衡，A 未估计，不预测真实收益。选 0.10/0.25/0.40 是为了保留明显的风险差异，并将建议硬上限压在 0.40；不是样本最优。固定 Sigma、均值估计错误、尾部、交易费用和约束都会破坏这个近似。fractional Kelly 不能保证峰谷回撤界；参照 [Busseti/Ryu/Boyd 原论文](https://web.stanford.edu/~boyd/papers/kelly.html) 的假设区别，不引入论文的求解器。

R-RISK-02 的 B4 内核只接受离散集合 **c∈{0.10, 0.25, 0.40}**，范围为 [0.10, 0.40]，区间内的其他数值同样拒绝；0、负数、.50、非有限值、字符串和 bool 均不接受。R1/M1 仍固定 0.25。c 只在每腿原始 proxy 上乘一次，然后执行原 cap；不得静默改档、截断 c 或提供非法值默认值。不能先算已封顶的 0.25 目标再乘 0.4 或 1.6，也不能用 fixed-f 冒充 c；两者均会改变约束语义。内核准入不代表三档消费者已接入或回测已完成。RS-06 的协方差 QP 已存在，但其 excess/cash 模型和 B4 gross 二阶 proxy 不相同，不能偷偷切换以凑齐三档。

## 2. 指标、约束与现金口径

- 日收益是同币种、同会话、费用及现金流口径明确的完整策略/账户净简单收益；现金不得用 BOXX 替代。波动报告为日标准差×sqrt(252)，估计窗口固定 252 个完成会话，至少 252 个有效观察，缺失不补 0。拟合只用决策时可用的训练数据，波动约束对完整目标求值；本卡不新写 vol-target 求解器。已有链不能证明目标满足时标为 `UNBOUND_VOLATILITY_CONSUMER`，不宣称已生效。
- `g = mean(log1p(r_net))`，单位自然对数/交易会话；`delta_g = g(level)-g(frozen_reference)`。另报 `252*delta_g` 为年化 log 差，不能当 CAGR 差；参考为同档同成本、同现金/初始状态的冻结基线，尚未绑定时不能填数字。三档各自也必须列出净增长绝对值。
- MDD 为从初始 NAV 一并纳入高水位的最大 `1-NAV/high_watermark`，费用后、现金流调整；容忍值是触发政策，不保证跳空后实际损失不超限。
- 报告日 CVaR：`weighted_cvar(losses=-r_net, probabilities=1/N, alpha=.95)`；取最坏 5% 概率质量并拆分边界，允许负值，不把全正收益截成零。固定 252 会话的监测统计不是未来风险保证。
- 执行前 CVaR：复用既有两会话 `PRE_NEXT_FILL_AT_T_PLUS_2` 端点，从决策 NAV 到下一次成交及再下一次成交前估值，包含成交前原持仓漂移、一次预测成本、现金和联合情景。日监测上限与两会话预算是**分别提出的参数**，不做 sqrt(2) 或简单倍数换算。场景覆盖、概率质量或未来成本不合格时不可计算。
- 换手是实际同账户、逐证券净成交后的 `sum_t(sum_s(abs(net_trade_value_s,t))/NAV_pretrade_t)`，双向 L1，不除 2，不从 fee/bps 倒推。风险买入换手是 `portfolio_risk_budget` 的另一指标，不混用。

费用后现金必须≥战略现金下限。费用前另保留 M1 的 `2/2001` NAV，约 0.1%，不能算收益现金或再投资额度。预留不是额外费用，不重复扣现金；实际只由原 netter 扣一次。M1 固定比例源自证券目标上界 1999/2001、单边 5bps 研究成本及完整买卖换手界，是原假设下的费用前资金边界，不是券商实费保证。高级模式不得降低这部分预留。未来须同时核对实际现金≥战略下限+预测费用、费用前 reserve、未决义务及费用后权重；不足则原资金拒绝保持。

现有 M1 只支持固定比例，不实现自定义战略现金底线，也未认证所有费用后 cap。本卡不通过修改 M1 数值实现新 cash floor。BOXX 是收费证券，需自己的估值、风险分类和交易费用；`is_cash=True` 只留给显式真现金伪资产，未来 adapter 必须与旧研究政策区分。

所有上限逐维取 `min(等级建议/用户覆盖, 原账户批准上限, 产品/策略限制)`，所有现金下限取 `max(等级/覆盖, 原账户下限)` 后满足费用。所谓硬区间只是 schema 的设计边界，**不能授权突破更严的账户限制**。未知预算/约束不填0或1，不做满仓归一化。多维混合变化不能用“净风险下降”抵消某一维放宽。

## 3. 复用接口与调用边界

下表全部指向本 worktree 的实际代码。当前产品 pin 及环境不调整。

| 职责 | 现有入口 / 输入 → 输出 | 当前限制与未来接缝 |
|---|---|---|
| B4 更新、矩 | [bayesian_kelly.py](../../../src/us_equity_strategies/research/bayesian_kelly.py) `initialise_b4` / `update_b4` / `b4_predictive_moments`：B4Spec(core_ids,training_cutoff), 固定训练 Sigma, training_identity；B4Observation(observation_id,revision,observed_at,available_at,core_ids,values[log_gross_returns]), decision_cutoff → B4State/B4Moments | 固定 Sigma 的条件模型；prior-only 无目标。训练63期是旧 R1 合同，不是本设计的252监测窗；不能偷换。保存 prefix SHA、n、模型 id、时间和修订 |
| B4 比例 | 同文件 `b4_fractional_target(moments,c,scope_cap,cap_source,veto)` → B4Target(status,targets,raw_second_order,c,fractional_applied_once,...) | 只接受 .10/.25/.40；每腿 `c*max(mu,0)/E[R²]` 再 cap；非完整相关组合优化。R1/M1 继续固定 .25，完整三档消费者未绑定 |
| 当前 R1 消费 | [rs04_netting_candidate.py](../../../src/us_equity_strategies/research/rs04_netting_candidate.py) `run_rs04_bayesian_candidate`：两 CorePath、独立 InitialNettingBook/CloseTarget、sessions、可用时点/cutoff、spec/Sigma/caps/veto、control exposures、fee specs → 两组账本/决策/checkpoint | 内部固定 c=.25，独立账户；`run_registered_r0_r3_batch` 另有 journal/原 M1。不能用本 schema 直接传新等级，也不宣称调用了 joint QP/risk-budget/CVaR |
| 既有联合 Kelly | [portfolio_kelly_walk_forward.py](../../../src/us_equity_strategies/research/portfolio_kelly_walk_forward.py) `fit_portfolio_kelly(assets,strategy,cash,fractions,methods)`；已有 `constrained_kelly_qp`、`clip_scale` → 权重/fit 状态 | 训练 excess returns、收缩均值/LW、现有 KKT QP；非 B4 posterior。仅未来单独冻结比较才选用，禁止新 solver 或切换目标冒充 R1 |
| 静态风险预算 | [portfolio_risk_budget.py](../../../src/us_equity_strategies/portfolio_risk_budget.py) `assess_portfolio_risk_budget(target_weights,asset_risk_specs,policy,current_weights)` → status/metrics/recommended_target_weights | PortfolioRiskBudgetPolicy(cash_symbol,max_effective_risk_exposure,max_symbol_weights,max_underlying_effective_exposure,max_one_way_risk_turnover)。仅研究 APPROVE/REDUCE/PARKED；没有杠杆 ETF **合计名义 cap** 的专用字段、vol/MDD/breaker/现金费用模型，不能声称自动覆盖 |
| 联合尾部约束 | [cvar_exposure_constraint.py](../../../src/us_equity_strategies/research/cvar_exposure_constraint.py) `constrain_tail_target(proposal,approved_anchor,policy,scenario_set,decision_book=...,bps_spec=...,hard_policy=...,forced_target=...)` → ConstraintDecision | Action/ApprovedAnchor、TailPolicy(TailDescriptor,budget,source,approved,synthetic_policy)、JointScenario(fill_returns,earn_returns,probability)、DecisionBook、CostSpec、HardPolicy/ExposureCap。已有 convex segment 内的收紧，不是全局 solver；不支持的离散/费用/硬约束明示拒绝。合计名义 cap 可在未来用杠杆证券系数1、其他0的 ExposureCap 表达；全证券系数1表达现金上界；TARGET_ONLY/POST_FEE 不混用 |
| CVaR 接入 B5 | 同上 R1 文件 `run_rs04_cvar_candidate` + `security_netting_runner` 现有 research policy | 明确 synthetic 两会话 consumer，不证明真实 R1 已采用。mandatory_reduce/bypass_b5_throttle 不能被 band 吞掉 |
| 交易/账本 | 现有 QPK `research_stats.security_netting` 的 `compose_security_targets` / `run_offline_security_netting` 与原 M1 prefee reserve | 原净额、费用、数量漂移、D更新→D+1 close成交→D+2收益保留。此处描述研究时钟，不认证真实 PIT |
| 风险预设呈现 | [risk_preset_offline_validator.zh-CN.md](../risk_preset_offline_validator.zh-CN.md) 的 `validate_risk_preset_offline` | 仅冻结的合成 wire/共同 c，不接受本三档 schema；未来明确版本 adapter，不改旧快照或冒充已支持 |
| DSR | QPK `research_stats.deflated_sharpe_ratio`；UES [trial_overfitting_report.py](../../../src/us_equity_strategies/research/trial_overfitting_report.py) | UES wrapper 限合成，且 n_effective=成功数，不能满足完整累计 trials 的新要求；本卡不绕过检查、不复制 DSR 算法 |

未来映射输入至少包含：`definition_revision/hash`、`mode/level/overrides`、批准的账户 scope/风险预算版本与来源、NAV/币种/真实可用现金/持仓及未决义务的时点、策略许可集合及版本、核心权重/毛收益/训练窗口/来源摘要、B4 状态、联合场景与成本/端点、当前 breaker 状态。账户数据由既有受限服务提供，本卡不读取。

未来输出至少包含：系统建议/请求覆盖/有效参数及逐项限制来源、按证券目标名义/穿透敞口、真实现金/费用预留、B4状态/一次缩放标记、风险与尾部状态/原因、再平衡动作及强制减险标记、所需确认/冷静期、定义/账户预算/数据/目标 hash、审计事件引用。输出目标只代表研究建议，实盘仍须原 execution/risk gate。

未来顺序：身份和授权核验 → 等级与覆盖解析 → 既有模型一次 c → 原 compose/静态预算/费用现金收紧 → 用既有两会话 CVaR 完整评价拟执行动作（包括 HOLD 和强制减险）→ band 只能在可行的原持仓与目标之间选择 → 对**最终动作**再用原预算/尾部/资金门核验 → 原 netter 记账；最终不满足即无新增风险动作。后段约束改变目标后不能假定前段已自动有效，也不能迭代成新 solver。需要新接线的位置另开实施卡。

## 4. 再平衡、熔断与恢复

默认 `band`：任一非现金证券的 `abs(w_target-w_current)` **大于** `max(absolute_band, relative_band*abs(w_target))` 时整体目标请求再平衡；边界等于阈值则 HOLD，比较容差必须在实施时沿既有函数冻结。目标为0但当前非零若来自止损/熔断/禁用策略等强制减险，跳过 band；一般小差异仍用绝对带。选择 daily/weekly 时只影响常规动作，不能延迟减险。band 默认随风险等级放宽，目的是减少微小交易，不声称降低 CVaR/费用或提高收益；实际换手与净增长待测。

breaker 输入用账户净日收益、连续高水位回撤和尾部/波动监测，日损失≥阈值、回撤≥阈值、CVaR/波动超预算、或原 RiskEngine 触发即停止新增风险。日 CVaR/波动不足252期时监测 `UNAVAILABLE`，不假造风险合格。MDD breaker 比容忍度更低，为响应保留距离，跳空仍可能越过。止损按策略 sleeve 费用后、资金流调整的入场净值计；止盈同基准，trailing 为该持有周期高水位跌幅。具体原消费者未绑定，所有新增阈值目前仅设计。

`NORMAL → HALTED → OBSERVING → AWAITING_RESUME → NORMAL`。进入 HALTED 立即锁定风险增加，保留原可授权的减险通道；不自动承诺平仓、撤单或成交。数据/持仓/未决状态未知、订单结果不明时沿原停止条件处理。强制退出不受 band、half 或升级冷静期阻挡。

恢复需：原因已排除、数据/资金/未决义务完整；连续5个完整会话日损失小于原阈值一半、日CVaR/波动低于其上限80%，无新异常；仍有未修复高水位回撤 breach 时不能自动恢复。止损关闭的单策略也不能随档位切换自行重开。账户高水位不因切档/复位而重置。深度回撤可由用户明确选择更低风险恢复方案，但不得清除原熔断记录、重设基线或放宽账户硬限；尚未解决则保持 HALTED。最后由用户确认恢复，继承既有更严规则；不自动按旧高仓位补回。

## 5. 等级、覆盖、确认与审计

- 降级：定义中的限制立即生效，取消待升级请求；重审所有覆盖，清掉会放宽新档的旧覆盖，保留更严项。这里“立即”指停止新风险和使限制有效，实际减仓仍须原持仓/交易权限及可用市场，不能宣称立即成交。
- 升级：用户选择并确认风险变化，建立48小时冷静期；到期后需新的预览与二次确认，不能自动生效。期间任一数据/参数/预算/目标 hash 变更，旧确认失效并重审；经济参数变化重新开始冷静期。确认超时15分钟、确认者身份或作用域不符、breaker 活跃均不生效。
- 同档覆盖：提高 c/名义或有效敞口/容忍阈值、降低现金、延长保护触发阈值等，只要任一维放宽，实盘前均须二次确认。策略切换/权重、止盈、band/方式或基准变化因非单调，默认 `UNKNOWN_OR_MIXED`，同样确认。不能仅以回测风险下降豁免。纯降低风险且不增加任何其他维度者立即收紧；未知时保持旧有效值，拒绝新增风险。
- 一键恢复系统建议：取消覆盖后生成同档建议及差异；有增险项仍走同样确认/冷静规则，减险立即执行。切换简单/高级模式本身不改变持仓或有效值。简单模式若尚有覆盖，必须显示“使用自定义值”，不能静默隐藏；用户可恢复建议。
- 基准仅影响比较显示，不改变持仓、账户 NAV、高水位、损失单位或风控门；变更需审计。若同时改变模型基准/尾部描述符，属于新政策，原确认全部失效。
- 服务端从认证上下文写入 actor，不信任前端传来的 `confirmed=true`。确认绑定 scope、旧/新等级、完整 overrides/定义/账户预算/数据/预览目标 SHA256 与用途，由服务端校验；不能重放用于别的账户或改过的参数。确认不授权突破硬预算或绕过熔断。

定义摘要为 parameters.json 的原始 UTF-8 文件字节 SHA256（含末尾 LF）；参数/请求/预览绑定对象用 `json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)` 的 UTF-8 字节，无末尾 LF。预览摘要排除摘要字段及确认回执本身，保留经济输入、限制来源与目标；不能把显示格式变化当经济参数变化。未绑定的摘要为 null，不能用全零占位充当真实身份。

审计必填：`event_id, request_id, event_type, actor_id, actor_role, occurred_at_utc, scope_ref, from_level, to_level, mode_before, mode_after, definition_sha256, previous_effective_sha256, requested_parameters_sha256, account_budget_sha256, preview_sha256, risk_direction, reason_codes, confirmation_ref, cooldown_until_utc, processed_status, observed_effective_sha256, observed_at_utc, supersedes_event_id`。区分请求/批准/实际生效观察，未知生效不记 APPLIED；拒绝、取消、降级、过期、恢复建议和熔断恢复都保留。事件 append-only 复用现有审计存储，本卡不建服务/数据库或写远端日志。

## 6. 高级模式 schema

[parameters.json](parameters.json) 是本版唯一默认参数表：每个参数都有 `defaults_by_level`、`hard_limits`、`user_override_allowed`、`secondary_confirmation`，并注明单位和风险方向。[parameter-catalogue.schema.json](parameter-catalogue.schema.json) 约束该表；[risk-settings.schema.json](risk-settings.schema.json) 校验用户选择与覆盖；P4 schema 用本地注册的 `urn:qsl:r-risk-01:risk-settings:research-v2` 复用请求 schema，校验器不得为它联网加载。JSON Schema `default` 只作说明，不会自动填值。原始请求缺少的覆盖不填0，解析时按定义版本取建议。

范围覆盖策略选择/相对权重、Kelly与仓位上限、现金、杠杆与底层、再平衡、止损止盈/熔断、基准。策略默认 `system_eligible` / `system_recommended` 是按原许可池计算的模式，不是任意激活 TQQQ/SOXL；选 explicit 要求名单已获准且有可用模型，否则不可计算。显式权重是 sleeve 风险分配比例、总和≤1，未分配部分留真现金；最终证券权重仍由既有模型和硬 cap 决定，禁止归一化用满剩余资金。

跨字段语义由未来原消费者校验：策略名单与权重一一对应、账户限制不能放宽、breaker≤MDD容忍、战略现金+预留<1、名义/有效/底层 cap 一并满足、预留不扣两次、禁止单独覆盖 readonly 字段、精确截止与确认身份。JSON Schema 只能验证形状/独立硬区间，不证明上述金融/授权语义；本卡没有实现执行器。新增参数或枚举必须提升 definition_version、补齐四类字段、审查并重新确认。

## 7. P4 简单/高级模式数据契约（不实现前端）

[p4-preview.schema.json](p4-preview.schema.json) 定义只读预览响应，[examples](examples/) 提供有效请求与缺证据预览。未来输入是 `risk-settings`，响应必须有：

1. `mode,level,definition_version,definition_sha256,scope_ref,request_id`；`parameter_comparison` 每个参数给 `recommended_value,override_value,has_override,effective_value,limiting_sources,risk_direction,confirmation_required`。`null` 表示未知且有状态，不用0冒充；模式切换保留 overrides。
2. `requested/effective` 状态分开；`application_status`、`confirmation_state`、冷静期、原因列表、实际生效 hash/时间。只读本设计所有 execution/live 标记恒 false。
3. `estimates.system_recommended` 与 `estimates.with_overrides` 各自给 g、Δg、MDD、CVaR95日/两会话、换手；`estimates.override_minus_recommended` 给两配置差。Δg 的参考基线必须相同。每项 `status,value,unit,reason_codes`；证据状态为 DESIGN_ONLY/SYNTHETIC/SEEN_DEVELOPMENT/PROSPECTIVE_UNQUALIFIED/QUALIFIED_RESEARCH，附数据截止、窗口、成本/模型/基准/数据/trial 绑定与可比性。
4. 只有同币种/窗口/成本/现金/模型/基线且数据合格时才显示可比数字。`UNCOMPUTABLE` 必须 value=null 且给具体原因；设计或合成时醒目标记“仅设计/合成”。不生成误导性的收益预测，不把历史 MDD 显示成未来承诺。

预估可来自未来已获准的相同模型/情景离线比较，必须标明 estimated，显示窗口和局限；不能每次拖动滑杆新跑历史选优。探索过的配置也进入 trial 计数，前端不得推荐“最优档”。缺证据仍能展示建议表和覆盖差异，金融指标统一显示“暂不可估”。

## 8. 三档对照、R4 与 DSR

本次结论：**仅设计；未运行三档真实或合成收益回测**。[comparison-gaps.json](comparison-gaps.json) 列出 TQQQ/SOXL×三档的六个单元，g/Δg/MDD/CVaR/换手全部为 null，并逐单元列原因。这比用 ETF 买入持有收益冒充策略结果更能保留现有研究合同。

具体缺口：

- G01：B4 参数域已扩展；R1/M1 仍固定 c=.25，完整三档消费者尚未接入。
- G02：三档的名义杠杆合计/战略真现金/波动/熔断规则尚无完整消费者，费用后 cap 与 joint CVaR 的真实接入未验收。
- G03：本卡未收到绑定本协议的完整 CorePath、联合情景、来源许可/PIT、训练/评估边界和同合同冻结基线；不读取旧私有 RAW/券商或新下载数据。旧已见 R1/M1 不代表本三档协议。
- G04：现有 `kelly_trial_ledger.json` 可核对累计48个早期 sizing trials，但不是截至今天的完整累计历史；缺后续试验 IDs、失败状态、同窗净收益和 trial Sharpe 分布。
- G05：本机默认 QPK 为旧 checkout，`quant_platform_kit.research_stats` 不可用；不升级共享环境或产品 pin。
- G06：c 与 schema 已获用户批准；实际风险预算身份/来源和 Grok Bot schema 复核仍未绑定，不具备真实 trial 激活条件。

[r4-trial-ledger.json](r4-trial-ledger.json) 是**本卡给 R4 的待入账清单/缺口附件**，不是第二套权威 PerformanceStore：记录六项 NOT_RUN_BLOCKED 设计单元，真实执行数/合成收益试验数均为0，旧账48仅作已核下界且不加6冒充54。权威 R4 仍为既有 PerformanceStore；本卡写集不含该 store，没有向它追加试验或声称登记成功。后续具备条件时由原 R4 journal 在任何数据/收益读取前登记新试验、六个 cell 与共同 batch 的关联及失败/中止状态，并给回读引用；此项尚未完成。不能重用旧 trial_id、覆盖旧拒绝或复制收益到新档位。

未来一次预先冻结问题：同一成本/现金/时钟/基线下描述三档风险与增长取舍，不选赢家、不扫 c/band/window 网格；两 profile 保持分账。252个评估会话是本建议的预注册候选长度，warmup63仅沿原B4训练，不够时不可运行；真正未见数据须在批准和冻结后开始，历史重放只标 SEEN_DEVELOPMENT。报告每个 cell 的 g/Δg/MDD、日CVaR、两会话风险评价、L1换手/费用和缺失，区分未成交/拒绝与合法零仓；失败不删除、窗口不挑交集。

DSR 复用 QPK，不在本卡实现。使用**完整累计可比研究族**的 trial IDs/成功及失败状态/成功路径 Sharpe 分布；依赖性未估计时，保守地以完整已登记试验总数作为 `n_effective_trials`，披露该保守近似与失败无路径，绝不为失败臂造收益；若统计入口的范围/假设不支持该计数则不可计算。跨窗口不能静默合并为同窗样本，需事先冻结可比族/统计口径。已知48是下界，不能作为完整累计N输入；仅成功6条或一条被选择试验均不够。当前 `DSR=UNCOMPUTABLE`，不把 Sharpe 或 Δg 乘任意“折扣系数”。DSR 是选择偏差调整的统计量，不是收益保证，参照 [Bailey/López de Prado 原论文](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)。

本卡的合成输入只用于证实当前 B4 接受/拒绝域、CVaR 符号和 risk-budget 不授予执行；不产生收益比较，不计作金融 trial，不证明 OOS、金融资格或实盘资格。

## 9. 交付与待复核

可复核命令：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 docs/research/risk-levels/verify_design.py`。它校验所有 schema/示例/默认值，拒绝越界覆盖和未知字段，核对六个缺口和源文件摘要，并只调用现有纯函数做合成接口探针；无网络、账户、PerformanceStore 写入或收益回放。[verification.json](verification.json) 是实际输出，不冒充三档回测。

Grok Bot 待审：数值与期限、离散 c 域与原 R1/M1 消费者的边界、现金费用前后与POST_FEE约束、两类CVaR口径、降级覆盖清理/升级48h和恢复、完整累计DSR/R4入账条件。当前工具没有 Grok Bot 调用入口，未发消息、未伪造 reviewer PASS；[review-request.zh-CN.md](review-request.zh-CN.md) 为可交付审阅材料。本轮批准覆盖 c 与 schema，不授予真实 trial 或预算变更权限。后续需完成 Grok Bot 复核及数据/许可/联合 CVaR/消费者绑定，再另卡进入三档对照。本卡没有前端或实盘接入。
