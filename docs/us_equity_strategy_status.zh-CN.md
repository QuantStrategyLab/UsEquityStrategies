# 美股策略状态与研究手册

_更新日期：2026-05-04_

这份文档只记录当前可配置的美股策略 profile、输入形态和研究状态，不记录任何账户或服务正在运行的 profile。部署单元当前跑什么属于私有运行信息，应留在云端配置或私有运行记录里。

策略实现以 `UsEquityStrategies` 为准；feature snapshot、研究回测和插件 artifact 由 `UsEquitySnapshotPipelines` 负责；券商连接、下单、通知和日志由各平台仓库负责。

完整归档索引见 [us_equity_runtime_archive.zh-CN.md](./us_equity_runtime_archive.zh-CN.md)。

## 当前可配置 profiles

这 6 条 profile 是当前 `runtime_enabled` `us_equity` 集合。它们按共享文档规范设计为通用策略，平台侧通过同一份 catalog、manifest、entrypoint 和 runtime adapter 契约接入；是否实盘启用仍由各部署配置和风控决定。

| Profile | 中文定位 | 输入类型 | 特点 | 当前建议 |
| --- | --- | --- | --- | --- |
| `global_etf_rotation` | 全球 ETF 防守轮动 | 直接运行输入 | 季度 Top2 ETF 轮动，每日 canary 防守，弱市切 `BIL`。 | 可切换；偏低波动防守线。 |
| `tqqq_growth_income` | TQQQ 增长收益 | 直接运行输入 | `QQQ` / `TQQQ` 双轮增长，默认 `45% / 45% / 8% BOXX / 2% cash`；`QQQM` 可作为低单价交易代理。 | 小账户最容易落地；不需要 snapshot artifact。 |
| `soxl_soxx_trend_income` | SOXL/SOXX 半导体趋势收益 | 直接运行输入 | 以 `SOXX` 140 日趋势闸门控制 `SOXL` / `SOXX` / `BOXX`；剩余资金停 BOXX，可叠加收入层。 | 半导体高弹性直接输入策略；波动高于宽基。 |
| `tech_communication_pullback_enhancement` | 科技通信回调增强 | feature snapshot | 科技/通信个股月频选择，受控回调入场，保留 BOXX 缓冲。 | 需要月度 snapshot；适合先小比例或观察运行。 |
| `russell_1000_multi_factor_defensive` | Russell 1000 多因子防守 | feature snapshot | Russell 1000 price-only 多因子，SPY 趋势 + breadth 防守，默认 24 股。 | 可切换但更适合大账户；长周期代理研究仍需补归档。 |
| `mega_cap_leader_rotation_top50_balanced` | Top50 平衡龙头轮动 | feature snapshot | 固定 `50% Top2 cap50 + 50% Top4 cap25` 袖子混合，不默认趋势降仓。 | 当前保留的无杠杆龙头轮动路线；建议 paper 观察。 |

## 已移除的重复/较弱研究 profile 暴露

按“如果比同类 runtime-enabled 策略表现差，就不要继续保留可运行入口”的口径，下面 3 条已经从 catalog、manifest、entrypoint、runtime adapter、snapshot publish 和平台 rollout 暴露中移除：

| 已移除 profile | 移除原因 |
| --- | --- |
| `mega_cap_leader_rotation_dynamic_top20` | 同期 CAGR 21.51%、最大回撤 -23.14%；收益明显弱于 `mega_cap_leader_rotation_top50_balanced` 的 36.41%。 |
| `mega_cap_leader_rotation_aggressive` | Top50 top3/cap35 CAGR 32.42%、最大回撤 -28.64%；仍弱于 Top50 balanced，且更集中。 |
| `dynamic_mega_leveraged_pullback` | CAGR 30.96%、最大回撤 -34.80%；2x 产品和 MAGS/TACO 路线更复杂，未优于当前保留路线。 |

历史研究输出可以继续作为离线证据查看，但这些名字不再是有效 `STRATEGY_PROFILE`，也不再保留平台 replay adapter。

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
| Crisis unified response historical research，含旧 5% TACO 袖子 | 1999-03-10 至 2026-04-16 | 23.89% | -56.04% | 相比合成 TQQQ 基线显著降低 2000/2008 级别灾难回撤；但该历史版本包含 TACO，不等于当前 defense-only shadow plugin。 | `UsEquitySnapshotPipelines/data/output/crisis_response_audit_trial/external_fragility10_severe10_fin_credit/summary.csv` |

暂时没有写进正式表的内容：

- `global_etf_rotation`：已完成阈值 4 版本复核，归档索引已更新为可保留版本；该版 CAGR 13.25%，最大回撤 -23.29%，优于 SPY 的回撤。

## 研究中但未进入实盘的方向

| 研究方向 | 当前状态 | 不直接实盘的原因 |
| --- | --- | --- |
| `crisis_response_shadow` 插件 | 可作为 `tqqq_growth_income` 的 `shadow` 插件候选，只写信号、日志和通知上下文。 | 现在是 defense-only 黑天鹅观察流，不下单、不改 allocation；需要稳定 shadow 日志后再做 evidence review。 |
| TACO rebound / MAGS 路线 | 保持 research-only，不作为运行策略 profile。 | 对 MAGS 的正贡献不稳定，且事件反弹预算不应该混进黑天鹅逃命插件。 |
| AI 审计 / AI 上下文 | 不进入交易路径。 | 回测结果来自确定性指标，不依赖 AI；AI 可以辅助离线 review、总结新闻或检查文档，但不能作为自动买卖开关。 |
| Russell 1000 代理长周期回测 | 研究待补。 | 2017 年前缺少可靠 point-in-time Russell 1000 / Top50 数据，需要代理构造并明确后视偏差。 |
| Top50 balanced paper 观察 | 当前保留候选。 | 历史结果强，且无杠杆，但 Top2 袖子仍有集中风险；要确认 snapshot、整数股、换手和通知稳定。 |

## 上线判断原则

- 策略能切换，不等于应该立刻实盘。
- 回测表里没有归档结果的策略，不能只凭记忆或口头结果写成结论。
- 小账户要优先看整数股偏差：多股票策略和高价 ETF 会明显偏离权重回测。
- 插件必须保持 sidecar：开启时只附加信号/日志/建议，关闭时基础策略照常运行。
- `shadow` 不影响交易；`paper` 只记模拟账；`advisory` 需要人工确认；`live` 才允许平台在风控限制下影响执行。
