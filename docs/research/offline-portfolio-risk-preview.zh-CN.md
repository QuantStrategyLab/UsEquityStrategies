# 三档组合与风控状态离线预览

本包提供可测试的配置草稿与状态保存接口。三档计算和状态接口只接受人工输入，输出始终 `execution_authorized=false`、`settings_applied=false`。另有独立的真实账户输入只读核对入口，见下节；它不调用三档计算或状态接口。没有接入运行策略、账户服务、下单入口或配置发布器。合并代码不代表启用三档配置，也不能证明收益更好或真实账户风险合格。

## 真实账户输入的只读核对

`assess_ibkr_readonly_account_input(facts, contract, source=...)`复用原账户、币种、估值、资金及完整性检查，明确要求`synthetic=false`。原`adapt_ibkr_account_input`继续只接受人工输入；不通过改标记把真实报告送入人工三档预览。

新入口只核提交的输入。`source`绑定完整来源字节摘要、facts与contract摘要、来源引用及生产者版本，分别记录原观察时刻、实际收到时刻和已知的发布时间。哈希证明提交内容一致，不认证调用者声明的来源，也不证明资金完整性、风险政策或历史当时可知性。缺少发布时间保留null，不用收到时刻代替；收到时刻不得晚于本次判断截止时刻。重新读取旧报告不会刷新原观察时刻或延长既定时效。

合格输入的状态为`READY_READONLY_INPUT`，仅表示该只读合同通过。缺失已结算现金、预留、完整义务、资金基准、身份或时点时保留具体缺项，`spendable_cash`与可用持仓权重为null；普通现金余额不会补成已结算现金。所有结果保持`account_policy_bound=false`、`financial_risk_certified=false`、`execution_authorized=false`和`settings_applied=false`。此入口没有模型调用、券商访问、状态初始化或设置写入，也不产生订单草稿。

`scripts.inspect_readonly_account_input`消费已保留的单个运行报告与私有静态配置：

```bash
PYTHONPATH=src python -m scripts.inspect_readonly_account_input \
  --report /secure/review/report.json \
  --config /secure/review/account-config.json \
  --output /secure/review/readiness.json
```

此命令只在受保护Linux上使用，不发网络请求。输入为私有普通文件，输出目录权限700，输出文件以600权限只创建一次，存在即拒绝覆盖。stdout只给固定状态、缺项和输出摘要，完整核对结果留在私有文件；缺项退出码2，不算资金预览通过。

静态配置包含`account_contract`、`report_binding`和`source_ref`。`report_binding`须明确匹配报告的`platform`、`strategy_profile`、`account_scope`及`runtime_revision`；不能从待核报告反填预期账户身份。`account_contract`沿用原字段合同，真实缺项可以为null并得到缺项报告，不代填资金或风险数值。配置中不能含旧`cutoff_utc`：调用程序在本次实际读取后建立新的核对截止时刻，保存原配置字节摘要和新的输入摘要。该新核对不是历史决策回放，也不能将其接收记录倒填给旧研究。

真实三档的正向预览仍需合格的策略矩、联合情景、有效政策、费用/数量及账户状态来源；本次只读输入核对没有解除这些计算入口的人工限制。

## 输入与计算

| 接口 | 用途与所需证据 |
| --- | --- |
| `adapt_ibkr_account_input` | 把一个账户的净清算价值、证券市值、现金和明确的结算/预留/义务合同分开；缺失不当作零，购买力不当作结算现金。 |
| `build_three_level_previews` | 根据明确的策略资格、同模型均值与协方差、证券组成、风险政策及联合情景生成保守、均衡、进取草稿。 |
| `build_three_level_funded_previews` | 增加现金来源、费用、价格、数量步长与最小买入金额证据；对取整结果重算费用、现金和原风险约束。 |
| `preview_portfolio_settings` | 智能模式生成建议；高级模式可指定获准策略与权重、收紧现有上限。两种模式遵守同一约束。 |
| `preview_portfolio_risk_settings` | 复用原 `RiskEngine` 和 cycle-health 能力，把止损、熔断与恢复资格接入草稿。 |
| `persist_portfolio_risk_preview` / `read_portfolio_risk_state` | 复用 QPK 的 create-only 状态链，保存后完整读回，供离线重启与回执丢失验证。 |

以上函数均位于 `us_equity_strategies.research`。入口参数可参看对应人工 fixture 与测试，不能直接把 fixture 数字当作账户政策。

[离线备份恢复与配置草稿读回](offline-state-recovery-and-drafts.zh-CN.md)进一步提供完整链快照、空目录恢复、草稿保存及当前证据下的只读预检；不启用配置或授予真实消费者权限。

三档名称不会自动赋予预算。资金曲线使用明确提供的参考资金、指数和保留资金基准，计算平滑下降的风险比例。保留基准与原风险上限防止亏损后自动放宽风险；缺少来源时保留缺口。这里的预算是相同期限与口径下的模型 CVaR，不能解释成最大亏损或最大回撤保证。

Kelly 候选使用已有约束 QP，目标为 `mu.T @ w - (w.T @ H @ w)/(2*c)`，约束为非负权重且总和不超过一，剩余为明确的零收益现金。版本化数学合同区分：

- `SECOND_RAW_MOMENT_TAYLOR`：`H = Cov(U) + E[U]E[U].T`，对应对数财富的二阶 Taylor 近似。
- `MEAN_COVARIANCE_FURTHER_APPROX`：`H = Cov(U)`，进一步省略均值外积，并显式标记该省略。

两者都不是精确对数最优解，也不是扣除账户交易成本后的最优解。`c` 只在 QP 曲率中应用一次；有约束时，不等于先求完整解再乘比例。现有定义中的三档研究 `c` 值不构成有效账户风险政策。

## 订单费用的离线适配

`build_three_level_funded_previews`另接受显式人工费用合同`ORDER_COMPONENTS_V1`。
它按证券及BUY/SELL分别要求按股费用、每单费用、最低佣金、可选的成交额比例上限，
以及上限外的按股/成交额附加费；字段缺失、负费率、未知条款均拒绝。数量仍复用原步长向下舍入。
单个条件情景中每证券只有一张完整成交订单，零数量不下单也不收最低佣金。公式为：

`commission = min(max(minimum, per_order + quantity * per_share), notional * maximum_bps / 10000)`

上限显式为null时不封顶；然后另加`quantity * extra_per_share + notional * extra_bps / 10000`。
该合同是未舍入的Decimal估计，不声称完整复刻任何券商收费计划；实际成交佣金不填入这份估计合同。
它不支持部分成交重收费、跨订单/月成交量阶梯、交易所路由差异、返佣或未知收费舍入。
调用者须用来源明确的适用条款或明确标注的模型假设完整填入，不能把IBKR Tiered名称当作参数或权限证明。
官方规则类型可参看[IBKR美国股票费用](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php)，实际适用实体、通道和附加费仍须另核。

连续候选仍由原固定BPS参考模型生成。取整后逐订单算出精确模型费用，再将这张已固定订单的
费用代数转换为该情景的费率，复用原约束核重查成本后权重、联合CVaR与现金；不把该费率送回二分求解器。
最终同一费用只扣一次，卖出收入不桥接未结算买入资金。复核失败就不出草稿，
不声称非线性费用下的最优分配，也不自动放宽风险预算或改变候选。

人工事件状态入口使用新模型时，观察合同还须显式选择
`liquidation_fee_allocation=PRO_RATA_MARKET_VALUE_SINGLE_ACCOUNT_ORDER`：
按账户每证券一张卖单估计全部持仓清算费，再按证券市值分摊到持仓周期，避免每个周期重复收最低佣金。
原生数量、价格、市值与各周期总额均须对账；缺少该约定仍拒绝。该约定只描述人工清算估值，
不证明真实执行会按同一篮子合单，也不赋予保存、预检或配置采用的其他缺失资格。

## 状态与恢复合同

存储只允许调用者明确提供的人工离线目录，拒绝 cloud/object-store 后端、其他命名空间、目录外路径与已存在的符号链接逃逸。状态目录复用原 QPK 布局，没有另一套数据库或可变的 `latest` 文件。

调用者必须提供可信来源的账户/组合/政策身份及预期头部哈希。哈希只能校验内容连续性，不能证明来源可信。空目录不是已经观察到的 CLOSED 状态；预期链丢失、损坏、头部不匹配、未来状态或孤立后继均禁止新增风险。

同一冻结请求重复送达时仅恢复状态，不重新出配置草稿。同一天输入改变会被拒绝；本按日合同每个 `effective_session` 只接受一次转换。独立的[盘中多事件合同](intraday-risk-state.zh-CN.md)提供逐事件保存及人工初始化/迁移接缝，原按日入口保持不变。

写入异常后只做一次读回核对，不盲目重试写入。熔断为 OPEN 时跨重启保留；恢复资格通过也不会自动解除熔断。既有止损保护、高水位和原触发原因不能在后继状态中放宽或丢失。

## 离线验证

生产 QPK 与插件的固定版本保持不变。普通测试环境缺少证券净账研究模块时，相关新测试明确跳过；不能把这些跳过说成通过。已有 `research-security-netting-consumer` CI 使用固定研究 overlay `7840127862ea715e44c8f70b03155a399dc817de`，强制执行全部相关测试并拒绝跳过。

在独立虚拟环境安装项目依赖与该研究 overlay 后，可执行：

```bash
UES_REQUIRE_SECURITY_NETTING=1 PYTHONPATH=src python -m pytest -q -rs \
  tests/research/test_capital_adaptive_risk.py \
  tests/research/test_auto_portfolio_preview.py \
  tests/research/test_funded_portfolio_preview.py \
  tests/research/test_kelly_candidate_math.py \
  tests/research/test_portfolio_settings_preview.py \
  tests/research/test_portfolio_risk_events_preview.py \
  tests/research/test_portfolio_risk_state_io.py \
  tests/test_research_import_boundary.py
```

测试涵盖手算二阶矩、账户/现金/费用/数量合同、缺失政策、智能/高级模式一致性、止损熔断、链损坏、重复与并发请求；另在七个独立进程中验证正常重启、写入前中断和提交后回执丢失。进程中断不等于断电、跨机器或生产存储的持久性认证。账户与现金生产者 fixture 是冻结的通用函数投影，仅使用人工输入，不声称线上生产者仍与它一致。

## 剩余条件

1. **账户政策**：真实账户范围、资金基准、绝对及相对损失预算、风险期限、三档曲线参数、停止/减仓/恢复政策需有效来源或明确决定，不能由优化器代填。
2. **可信保留状态**：需真实消费者提供可信 checkpoint、资金基准和外部现金流来源；本包只核验明确提交的人工绑定。
3. **生产使用前的工程**：独立盘中接口已具备人工离线合同；原按日消费者的停写交接与日初基准、原生消费者的配置预览/生效读回，以及对应实际存储、券商输入、费用和数量规则仍需独立验证与授权。
4. **金融证据**：相关性、Sharpe、胜率、盈亏比、成本与复利指标必须来自同一合格路径。当前没有用这些接口新增真实回测，也没有证明某个组合能实现最优复利。

缺少这些条件时，交付终态为离线工程能力，不是无人值守真实交易资格。
