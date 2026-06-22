# 美股策略状态与研究手册

_更新日期：2026-06-23_

这份文档只记录当前可配置的美股策略 profile、输入形态和研究状态，不记录任何账户或服务正在运行的 profile。部署单元当前跑什么属于私有运行信息，应留在云端配置或私有运行记录里。

策略实现以 `UsEquityStrategies` 为准；feature snapshot、研究回测和插件 artifact 由 `UsEquitySnapshotPipelines` 负责；券商连接、下单、通知和日志由各平台仓库负责。

完整归档索引见 [us_equity_runtime_archive.zh-CN.md](./us_equity_runtime_archive.zh-CN.md)。

## 当前可配置 profiles

这 6 条 profile 是当前 `runtime_enabled` `us_equity` 集合。它们按共享文档规范设计为通用策略，平台侧通过同一份 catalog、manifest、entrypoint 和 runtime adapter 契约接入；是否部署启用仍由各部署配置和风控决定。`global_etf_confidence_vol_gate` 现在只是 `global_etf_rotation` 的 legacy alias，不再是独立 runtime profile。

| Profile | 中文定位 | 输入类型 | 特点 | 当前建议 |
| --- | --- | --- | --- | --- |
| `global_etf_rotation` | 全球 ETF 防守轮动 | 直接运行输入 | 季度 Top2 ETF 轮动，默认启用 SMA250 置信度 + 相对波动门控；每日 canary 防守，弱市切 `BIL`。 | 默认保留；当前推荐档。`market_regime_control` 可接收通知/证据 artifact，但本地 apply 开关默认关闭，自动仓位影响等待长周期推广包。 |
| `tqqq_growth_income` | TQQQ 增长收益 | 直接运行输入 | `QQQ` / `TQQQ` 双轮增长，默认 `45% / 45% / 8% BOXX / 2% cash`；`QQQM` 可作为低单价交易代理；高波动降杠杆触发时默认读取 `market_regime_control` 的确定性 retention context，采用 `tqqq_step_softzero_0.25_0.50`。 | 小账户最容易落地；需要策略级 `market_regime_control` artifact。 |
| `soxl_soxx_trend_income` | SOXL/SOXX 半导体趋势收益 | 直接运行输入 | 以 `SOXX` 140 日趋势闸门控制 `SOXL` / `SOXX` / `BOXX`；默认用 `SOXX` 10 日年化实际波动率的 252 日滚动 95 分位阈值，边界 `50%`-`75%`，样本不足时回退固定 `55%`；触发后读取确定性 `soxl_step_rebound_0.25_0.50` retention context，剩余 `SOXL` 转向 `SOXX`；插件也支持 `soxl_step_softzero_rebound_0.25_0.50` 保守切换；并叠加收入层。 | 半导体高弹性直接输入策略；默认启用策略级 `market_regime_control`，但 `risk_reduced` 仓位影响默认关闭。 |
| `nasdaq_sp500_smart_dca` | 纳斯达克 / 标普定投 | 直接运行输入 | 只买不卖；默认月度按 `base_investment_usd` 定额买入 `QQQM/SPLG`，可配置周/月/季频率和重试窗口；定投账号默认不预留现金；可打开智能倍数，用 `QQQ/SPY` 的 200 日均线距离、252 日回撤和 RSI 过热状态调整本期金额；现金不足以覆盖本期金额时不投。 | 适合没有原生定投功能的平台账户长期积累；默认月度窗口运行。 |
| `ibit_smart_dca` | IBIT 比特币 ETF 定投 | 直接运行输入 | 只买不卖；默认月度按 `base_investment_usd` 定额买入 `IBIT`，可配置周/月/季频率和重试窗口；定投账号默认不预留现金；可打开智能倍数，优先用外部 `derived_indicators` 里的 AHR999 周期指标调整本期金额，缺失时才回退到 BTC 价格历史回撤规则；不维护额外现金池，执行日现金不足就不投。 | 适合专门跑 IBIT 积累的账户；默认定额普通定投，智能倍数只是可选配置；BTC/AHR999 数据源应由外部信号层或平台适配层维护。 |
| `russell_top50_leader_rotation` | 罗素 Top50 领涨轮动 | feature snapshot | 固定 `50% Top2 cap50 + 50% Top4 cap25` 袖子混合，不默认趋势降仓。 | 当前保留的无杠杆龙头轮动路线；`market_regime_control` 本地 apply 开关默认关闭，自动仓位影响先保持通知/证据模式。 |

## 已移除的重复/较弱研究 profile 暴露

按“如果比同类 runtime-enabled 策略表现差，就不要继续保留可运行入口”的口径，下面 6 条已经从 catalog、manifest、entrypoint、runtime adapter、snapshot publish 和平台 rollout 暴露中移除：

| 已移除 profile | 移除原因 |
| --- | --- |
| `russell_1000_multi_factor_defensive` | 年化只小幅跑赢大盘，最大回撤与大盘接近，实盘价值弱于定投大盘，移除可运行入口。 |
| `mega_cap_leader_rotation_top50_balanced` | 名称不再贴切；策略仍保留但改名为 `russell_top50_leader_rotation`，旧 profile 不再兼容。 |
| `mega_cap_leader_rotation_dynamic_top20` | 同期 CAGR 21.51%、最大回撤 -23.14%；收益明显弱于 `russell_top50_leader_rotation` 的 36.41%。 |
| `dynamic_mega_leveraged_pullback` | CAGR 30.96%、最大回撤 -34.80%；2x 产品和事件反弹路线更复杂，未优于当前保留路线。 |
| `tech_communication_pullback_enhancement` | 行业限制在科技/通信，收益明显低于 `russell_top50_leader_rotation`，最大回撤也没有改善；策略实现和 bundled config 仅作为离线研究归档保留。 |

历史研究输出可以继续作为离线证据查看，但这些名字不再是有效 `STRATEGY_PROFILE`，也不再保留平台 replay adapter。

## 收入层默认启用口径

除 `nasdaq_sp500_smart_dca` / `ibit_smart_dca` 这类只买不卖的现金定投 profile 外，保留的组合型 runtime profile 默认都启用收入层，且下游策略配置可以覆盖任意 `income_layer_*` 参数；需要关闭时设置 `income_layer_enabled = false`。当前默认统一使用 `log_total_drawdown_budget`，先按账户规模给出目标总回撤预算，再用核心策略压力回撤和收入篮子压力回撤反推出收入层比例。`income_layer_activation_band_ratio` 会在 `start` 到 `start * (1 + band)` 之间把正常目标比例从 0 平滑放大到 1，避免门槛附近来回卡住。

这些默认收入层是 live 配置：它们来自已归档的收入层回测 / 复核证据，并继续作为普通 ETF 目标仓位执行。`SPYI` / `QQQI` 留在收入层，因为策略只买卖 ETF 本身，不直接选择期权合约。直接期权层现在有默认设置，但所有未通过真实期权链验证的 recipe 都保持 research-only；配置默认可见，总开关默认开启，live gate 会在 `promotion_evidence = false` 时阻断真实期权订单意图。

| Profile | 模式 | 起点 | 平滑带 | 硬上限 | 默认收入篮子 |
| --- | --- | ---: | ---: | ---: | --- |
| `tqqq_growth_income` | `log_total_drawdown_budget` | `250000` | `20%` | `55%` | `SCHD 30% / DGRO 20% / SGOV 40% / SPYI 8% / QQQI 2%` |
| `soxl_soxx_trend_income` | `log_total_drawdown_budget` | `150000` | `20%` | `95%` | `SCHD 15% / DGRO 10% / SGOV 70% / SPYI 4% / QQQI 1%` |
| `global_etf_rotation` | `log_total_drawdown_budget` | `500000` | `10%` | `15%` | `SCHD 40% / DGRO 25% / SGOV 30% / SPYI 5%` |
| `russell_top50_leader_rotation` | `log_total_drawdown_budget` | `300000` | `15%` | `25%` | `SCHD 45% / DGRO 30% / SGOV 25%` |

## 已归档回测摘要

不同策略的样本区间、输入数据和交易假设不同，不能直接按 CAGR 排名。表格只记录当前仍有决策价值的证据。

| 策略 / 研究版本 | 样本 | CAGR | 最大回撤 | 关键结论 | 数据出处 |
| --- | --- | ---: | ---: | --- | --- |
| `tqqq_growth_income` 的可执行近似版 `video_like_pullback_next_close`，5 bps 单边成本 | 2017-01-03 至 2026-04-10 | 33.96% | -31.48% | 明显优于 QQQ CAGR，回撤接近 QQQ；远低于 TQQQ 买入持有的 -81.66% 回撤。当前默认策略在此基础上加入动态波动率回调门槛，因此这是近似证据。 | `InteractiveBrokersPlatform/research/results/video_qqq_tqqq_dual_drive_comparison.csv` |
| `soxl_soxx_trend_income` 日频回放，100k 初始权益，5 bps 成本 | 2024-01-31 至 2026-05-04 | 98.03% | -39.29% | SOXX 140 日闸门下的半导体趋势收益路径已经有可复查 summary；收入层在权益跨过 150k 后开始逐步参与。 | `UsEquitySnapshotPipelines/data/output/soxl_soxx_trend_income_archive_2026-05-04/summary.csv` |
| QQQ 买入持有基准 | 2017-01-03 至 2026-04-10 | 20.18% | -35.12% | TQQQ 双轮策略的主要比较基准。 | 同上 |
| TQQQ 买入持有参考 | 2017-01-03 至 2026-04-10 | 37.77% | -81.66% | 收益高但回撤过深，只作风险参照。 | 同上 |
| Top50 `blend_top2_50_top4_50`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 36.41% | -30.56% | 当前最强无杠杆候选之一；回撤可接受但 Top2 袖子带来集中风险，需要 paper 观察。 | `UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_top50_concentration_variants/concentration_variant_summary.csv` |
| Top50 `top2_cap50_no_defense`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 39.83% | -38.79% | 收益最高但两只股票 50/50 太集中，只作为 aggressive research 证据，不作为默认。 | `UsEquitySnapshotPipelines/docs/mega-cap-leader-rotation-dynamic-validation.md` |
| `global_etf_confidence_vol_gate` production-like 研究 | 2015-01-05 至 2026-05-06 | 14.77% | -23.35% | 相比同口径 Top2/SMA250 的 13.60% CAGR、-23.35% 回撤，收益和 Sharpe 改善；仍未跑赢 QQQ 长期 CAGR，因此只作为 Global ETF 自身增强候选。 | [`docs/research/global_etf_confidence_vol_gate.md`](./research/global_etf_confidence_vol_gate.md) |
| Crisis unified response historical research，含旧 5% 反弹袖子 | 1999-03-10 至 2026-04-16 | 23.89% | -56.04% | 相比合成 TQQQ 基线显著降低 2000/2008 级别灾难回撤；但该历史版本包含旧反弹袖子，不等于当前 defense-only shadow plugin。 | `UsEquitySnapshotPipelines/data/output/crisis_response_audit_trial/external_fragility10_severe10_fin_credit/summary.csv` |

暂时没有写进正式表的内容：

- `global_etf_rotation`：已切到 SMA250 置信度 + 相对波动门控的保留版；最新回测 CAGR 13.91%，最大回撤 -23.29%，已替代原先的等权默认档。`global_etf_confidence_vol_gate` 仅保留为同一 runtime profile 的 legacy alias / 回放名。

## 研究中但未进入运行 profile 的方向

| 研究方向 | 当前状态 | 不直接部署的原因 |
| --- | --- | --- |
| `crisis_response_shadow` 插件 | 可作为 `tqqq_growth_income` 的 `shadow` 插件候选，只写信号、日志和通知上下文。 | 现在是 defense-only 黑天鹅观察流，不下单、不改 allocation；需要稳定 shadow 日志后再做 evidence review。 |
| 事件反弹 / MAGS 路线 | 保持 research-only，不作为运行策略 profile。 | 对 MAGS 的正贡献不稳定，且事件反弹预算不应该混进黑天鹅逃命插件。 |
| `QQQ` / `SPY` LEAPS 增长增强层 | 已有 option overlay 意图框架；组合型 live profile 默认带 `option_*` 设置，但 `spy_leaps_growth_v1` / `qqq_leaps_growth_v1` 等 recipe 仍是 research candidate，当前会以 `research_only_recipe` 跳过，不产生真实订单意图，研究设计见 [`docs/research/index_leaps_growth_overlay.zh-CN.md`](./research/index_leaps_growth_overlay.zh-CN.md)。 | 属于有限权利金预算的增长增强层，不是当前低回撤收入层的直接替代；需要真实期权链回测后才能把 recipe 晋级为 live。 |
| AI 审计 / AI 上下文 | 不进入交易路径。 | 回测结果来自确定性指标，不依赖 AI；AI 可以辅助离线 review、总结新闻或检查文档，但不能作为自动买卖开关。 |
| Russell 1000 代理长周期回测 | 研究待补。 | 2017 年前缺少可靠 point-in-time Russell 1000 / Top50 数据，需要代理构造并明确后视偏差。 |
| Russell Top50 leader rotation paper 观察 | 当前保留候选。 | 历史结果强，且无杠杆，但 Top2 袖子仍有集中风险；要确认 snapshot、整数股、换手和通知稳定。 |

## 上线判断原则

- 策略能切换，不等于应该立刻实盘。
- 回测表里没有归档结果的策略，不能只凭记忆或口头结果写成结论。
- 小账户要优先看整数股偏差：多股票策略和高价 ETF 会明显偏离权重回测。
- 插件必须保持 sidecar：开启时只附加信号/日志/建议，关闭时基础策略照常运行。
- `shadow` 不影响交易；`paper` 只记模拟账；`advisory` 需要人工确认；`live` 才允许平台在风控限制下影响执行。
