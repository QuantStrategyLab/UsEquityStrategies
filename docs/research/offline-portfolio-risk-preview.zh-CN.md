# 三档组合与风控状态离线预览

本包提供可测试的配置草稿与状态保存接口。全部计算只接受人工输入，输出始终 `execution_authorized=false`、`settings_applied=false`。没有接入运行策略、账户服务、下单入口或配置发布器。合并代码不代表启用三档配置，也不能证明收益更好或真实账户风险合格。

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
