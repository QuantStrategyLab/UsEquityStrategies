# 美股策略状态与研究手册

_更新日期：2026-04-18_

这份文档用来快速回答三个问题：

1. 现在三个美股平台可以切换哪些 `STRATEGY_PROFILE`。
2. 每条策略属于直接运行输入还是 snapshot 输入。
3. 已归档研究回测显示了什么，哪些还只是研究候选。

策略实现以本仓库 `UsEquityStrategies` 为准；feature snapshot、研究回测和插件 artifact 由 `UsEquitySnapshotPipelines` 负责；券商连接、下单、通知和日志由各平台仓库负责。

## 配置口径

这份开源文档只记录可配置的策略 profile、输入形态和研究状态，不记录任何账户或服务当前实际运行的 profile。

| 平台服务 | 当前 profile | 运行说明 |
| --- | --- | --- |
***REMOVED***
***REMOVED***
***REMOVED***
***REMOVED***

切换策略时由平台仓库设置 `STRATEGY_PROFILE` 和必要的 snapshot/config env；不要把某个 live service 的当前 profile 写进策略文档。

## 可以配置的策略 profiles

这 9 条 profile 当前在 `ibkr`、`schwab`、`longbridge` 三个平台的能力矩阵里都是 `eligible=true` 且 `enabled=true`。这表示平台契约已经放开，不表示每条都适合直接上实盘。

| Profile | 中文定位 | 输入类型 | 特点 | 当前建议 |
| --- | --- | --- | --- | --- |
| `global_etf_rotation` | 全球 ETF 防守轮动 | 直接运行输入 | 季度 Top2 ETF 轮动，每日 canary 防守，弱市切 `BIL`。 | 可切换；偏低波动防守线。 |
***REMOVED***
***REMOVED***
***REMOVED***
| `russell_1000_multi_factor_defensive` | Russell 1000 多因子防守 | feature snapshot | Russell 1000 price-only 多因子，SPY 趋势 + breadth 防守，默认 24 股。 | 可切换但更适合大账户；长周期代理研究仍需补归档。 |
| `mega_cap_leader_rotation_dynamic_top20` | 动态 Top20 龙头轮动 | feature snapshot | 从历史动态 mega-cap Top20 里选 4 只，单票 25%，QQQ 破 200 日线时降到 50% 股票仓位。 | 可切换；比 Top50 系列保守。 |
| `mega_cap_leader_rotation_aggressive` | Top50 激进龙头轮动 | feature snapshot | 动态 Top50 中选 top3，单票 35%，默认不做 QQQ 趋势降仓。 | 可切换但偏激进；建议先 paper/shadow。 |
| `mega_cap_leader_rotation_top50_balanced` | Top50 平衡龙头轮动 | feature snapshot | 固定 `50% Top2 cap50 + 50% Top4 cap25` 袖子混合，不默认趋势降仓。 | 当前最有吸引力的无杠杆研究候选；建议 paper 观察。 |
| `dynamic_mega_leveraged_pullback` | Mega 2x 回调策略 | feature snapshot + 日频行情 | 动态 mega-cap 池里选 top3 的 2x 产品，用 QQQ 200SMA/ATR 风控，剩余停 BOXX。 | 可切换但暂不建议实盘；MAGS/TACO 未合并到 live。 |

## 已归档回测摘要

不同策略的样本区间、输入数据和交易假设不同，下面不能直接按 CAGR 排名。表格只用于说明“当前文档里有哪些可复查证据”。

| 策略 / 研究版本 | 样本 | CAGR | 最大回撤 | 关键结论 | 数据出处 |
| --- | --- | ---: | ---: | --- | --- |
| `tqqq_growth_income` 的可执行近似版 `video_like_pullback_next_close`，5 bps 单边成本 | 2017-01-03 至 2026-04-10 | 33.96% | -31.48% | 明显优于 QQQ CAGR，回撤接近 QQQ；远低于 TQQQ 买入持有的 -81.66% 回撤。当前默认策略在此基础上加入动态波动率回调门槛，因此这是近似证据，不是逐行完全相同的 运行时回测。 | `InteractiveBrokersPlatform/research/results/video_qqq_tqqq_dual_drive_comparison.csv` |
| QQQ 买入持有基准 | 2017-01-03 至 2026-04-10 | 20.18% | -35.12% | TQQQ 双轮策略的主要比较基准。 | 同上 |
| TQQQ 买入持有参考 | 2017-01-03 至 2026-04-10 | 37.77% | -81.66% | 收益高但回撤过深，只作风险参照。 | 同上 |
| `dynamic_mega_leveraged_pullback` robust default，2x 产品模型 | 2017-10-02 至 2026-04-13 | 30.96% | -34.80% | 回撤接近 QQQ，但 CAGR 明显更高；弱年亏损仍需要接受。 | `UsEquitySnapshotPipelines/data/output/dynamic_mega_leveraged_pullback_optimization_research/baseline_check/summary.csv` |
| 同期 QQQ 基准 | 2017-10-02 至 2026-04-13 | 19.25% | -35.12% | Mega 2x 回调策略的主要比较基准。 | 同上 |
| `mega_cap_leader_rotation_dynamic_top20` | 2017-10-02 至 2026-04-13 | 21.51% | -23.14% | 动态 Top20 四股版本更稳，收益提升有限但回撤明显低于 QQQ。 | `UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_universe_top20_backtest/summary.csv` |
| Top50 `top3_cap35_no_defense`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 32.42% | -28.64% | 对应 `mega_cap_leader_rotation_aggressive` 的接近默认形态；收益高于 Top20，但集中度更高。 | `UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_top50_long_cycle_validation/validation_summary.csv` |
| Top50 `blend_top2_50_top4_50`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 36.41% | -30.56% | 当前最强无杠杆候选之一；回撤可接受但 Top2 袖子带来集中风险，需要 paper 观察。 | `UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_top50_concentration_variants/concentration_variant_summary.csv` |
| Top50 `top2_cap50_no_defense`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 39.83% | -38.79% | 收益最高但两只股票 50/50 太集中，作为 aggressive research，不作为默认。 | `UsEquitySnapshotPipelines/docs/mega-cap-leader-rotation-dynamic-validation.md` |
| Crisis unified response historical research，含旧 5% TACO 袖子 | 1999-03-10 至 2026-04-16 | 23.89% | -56.04% | 相比合成 TQQQ 基线显著降低 2000/2008 级别灾难回撤；但该历史版本包含 TACO，不等于当前 defense-only shadow plugin。 | `UsEquitySnapshotPipelines/data/output/crisis_response_audit_trial/external_fragility10_severe10_fin_credit/summary.csv` |

暂时没有写进正式表的内容：

- `soxl_soxx_trend_income`：当前策略逻辑和测试覆盖完整，但本仓库没有归档一份可直接复查的长期 backtest summary。历史口头结果需要重新跑并提交 summary 后才能写成正式指标。
- `global_etf_rotation`、`russell_1000_multi_factor_defensive`、`tech_communication_pullback_enhancement`：当前有策略逻辑、snapshot/publish 流水线或命令，但缺少一份统一归档的 promoted backtest 表。后续要补齐 `summary.csv` 或验证文档。

## 研究中但未进入实盘的方向

| 研究方向 | 当前状态 | 不直接实盘的原因 |
| --- | --- | --- |
***REMOVED***
| TACO rebound | 已从 Crisis 插件拆出，MAGS 路线保持 research-only；未来若要做，优先单独研究 TQQQ 左侧/事件反弹 overlay。 | TACO 更像事件反弹预算，不应该混进黑天鹅逃命插件；对 MAGS 的正贡献不稳定。 |
| AI 审计 / AI 上下文 | 不进入交易路径。 | 回测结果来自确定性指标，不依赖 AI；AI 可以辅助离线 review、总结新闻或检查文档，但不能作为自动买卖开关。 |
| Russell 1000 代理长周期回测 | 研究待补。 | 2017 年前缺少可靠 point-in-time Russell 1000 / Top50 数据，需要代理构造并明确后视偏差。 |
| Top50 balanced paper 观察 | 候选最强，但需要 paper/shadow 观察。 | 历史结果强，且无杠杆，但 Top2 袖子仍有集中风险；要确认 snapshot、整数股、换手和通知稳定。 |

## 上线判断原则

- 策略能切换，不等于应该立刻实盘。
- 回测表里没有归档结果的策略，不能只凭记忆或口头结果写成结论。
- 小账户要优先看整数股偏差：多股票策略和高价 ETF 会明显偏离权重回测。
- 插件必须保持 sidecar：开启时只附加信号/日志/建议，关闭时基础策略照常运行。
- `shadow` 不影响交易；`paper` 只记模拟账；`advisory` 需要人工确认；`live` 才允许平台在风控限制下影响执行。
