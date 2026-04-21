# 美股策略状态与研究手册

_更新日期：2026-04-18_

这份文档用来快速回答三个问题：

1. 现在三个美股平台可以切换哪些 `STRATEGY_PROFILE`，哪些只保留为研究存档。
2. 每条策略属于直接运行输入还是 snapshot 输入。
3. 已归档研究回测显示了什么，哪些还只是研究候选。

策略实现以本仓库 `UsEquityStrategies` 为准；feature snapshot、研究回测和插件 artifact 由 `UsEquitySnapshotPipelines` 负责；券商连接、下单、通知和日志由各平台仓库负责。Research-only 存档清单见 `docs/research/archived_profiles.zh-CN.md`。

## 配置口径

这份开源文档只记录可配置的策略 profile、输入形态和研究状态，不记录任何账户或服务当前实际运行的 profile。每个部署单元当前跑什么属于部署私有信息，应留在私有运行记录或云端配置里。

切换策略时由平台仓库设置 `STRATEGY_PROFILE` 和必要的 snapshot/config env；不要把某个 live service 的当前 profile 写进策略文档。详细切换步骤看 `QuantPlatformKit/docs/us_equity_live_switch_runbook.zh-CN.md`。

## 可以配置的策略 profiles

这 6 条 profile 是当前策略目录里的 `runtime_enabled` `us_equity` profiles。平台能否实际启用由各平台能力矩阵和部署配置决定；能切换不等于适合直接上实盘。

| Profile | 中文定位 | 输入类型 | 特点 | 当前建议 |
| --- | --- | --- | --- | --- |
| `global_etf_rotation` | 全球 ETF 防守轮动 | 直接运行输入 | 季度 Top2 ETF 轮动，每日 canary 防守，弱市切 `BIL`。 | 可切换；偏低波动防守线。 |
| `tqqq_growth_income` | TQQQ 增长收益 | 直接运行输入 | `QQQ` / `TQQQ` 双轮增长，默认 `45% / 45% / 8% BOXX / 2% cash`；`QQQM` 可作为低单价交易代理。 | 小账户最容易落地；直接输入策略，不需要 snapshot artifact。 |
| `soxl_soxx_trend_income` | SOXL/SOXX 半导体趋势收益 | 直接运行输入 | 以 `SOXX` 140 日趋势闸门控制 `SOXL` / `SOXX` / `BOXX`；剩余资金停 BOXX，可叠加收入层。 | 半导体高弹性直接输入策略；波动高于宽基。 |
| `tech_communication_pullback_enhancement` | 科技通信回调增强 | feature snapshot | 科技/通信个股月频选择，受控回调入场，保留 BOXX 缓冲。 | 需要月度 snapshot；适合先小比例或观察运行。 |
| `russell_1000_multi_factor_defensive` | Russell 1000 多因子防守 | feature snapshot | Russell 1000 price-only 多因子，SPY 趋势 + breadth 防守，默认 24 股。 | 可切换但更适合大账户；长周期代理研究仍需补归档。 |
| `mega_cap_leader_rotation_top50_balanced` | Top50 平衡龙头轮动 | feature snapshot | 固定 `50% Top2 cap50 + 50% Top4 cap25` 袖子混合，不默认趋势降仓。 | 无杠杆研究候选；建议 paper 观察。 |

## Research-only 存档 profiles

这些 profile 不硬删除：策略定义、manifest、entrypoint、runtime adapter 和历史回测证据仍保留，方便复盘和回放。但 metadata 状态已改为 `research_only`，不会进入平台 runtime rollout allowlist，也不应再作为 `STRATEGY_PROFILE` 部署。

| Profile | 存档原因 | 保留内容 | 后续判断 |
| --- | --- | --- | --- |
| `mega_cap_leader_rotation_dynamic_top20` | Top50 balanced 是更强的当前运行候选；Top20 主要价值是历史保守分支对照。 | 动态 Top20 snapshot 合约、entrypoint、adapter、历史回测证据。 | 只作为 research/replay，不再建议切换运行。 |
| `mega_cap_leader_rotation_aggressive` | 高集中 Top50 top3/cap35 分支容易成为参数化候选；当前保留 Top50 balanced 作为主候选。 | Aggressive snapshot 合约、entrypoint、adapter、top3/cap35 证据。 | 有新验证前不作为平台线路。 |
| `dynamic_mega_leveraged_pullback` | 2x 单股产品路线复杂，且 MAGS/TACO 未进入正式逻辑；Top50 balanced 更干净。 | 2x 产品回调逻辑、snapshot 合约、entrypoint、adapter、风险预算研究。 | 保留研究回放，不接 MAGS/TACO 到运行时。 |

## 已归档回测摘要

不同策略的样本区间、输入数据和交易假设不同，下面不能直接按 CAGR 排名。表格只用于说明“当前文档里有哪些可复查证据”。

| 策略 / 研究版本 | 样本 | CAGR | 最大回撤 | 关键结论 | 数据出处 |
| --- | --- | ---: | ---: | --- | --- |
| `tqqq_growth_income` 的可执行近似版 `video_like_pullback_next_close`，5 bps 单边成本 | 2017-01-03 至 2026-04-10 | 33.96% | -31.48% | 明显优于 QQQ CAGR，回撤接近 QQQ；远低于 TQQQ 买入持有的 -81.66% 回撤。当前默认策略在此基础上加入动态波动率回调门槛，因此这是近似证据，不是逐行完全相同的运行时回测。 | `InteractiveBrokersPlatform/research/results/video_qqq_tqqq_dual_drive_comparison.csv` |
| QQQ 买入持有基准 | 2017-01-03 至 2026-04-10 | 20.18% | -35.12% | TQQQ 双轮策略的主要比较基准。 | 同上 |
| TQQQ 买入持有参考 | 2017-01-03 至 2026-04-10 | 37.77% | -81.66% | 收益高但回撤过深，只作风险参照。 | 同上 |
| `dynamic_mega_leveraged_pullback` robust default，2x 产品模型 | 2017-10-02 至 2026-04-13 | 30.96% | -34.80% | 已作为 `research_only` 存档；结果可复查，但不再作为平台可切换 profile。 | `UsEquitySnapshotPipelines/data/output/dynamic_mega_leveraged_pullback_optimization_research/baseline_check/summary.csv` |
| 同期 QQQ 基准 | 2017-10-02 至 2026-04-13 | 19.25% | -35.12% | Mega 2x 回调策略的主要比较基准。 | 同上 |
| `mega_cap_leader_rotation_dynamic_top20` | 2017-10-02 至 2026-04-13 | 21.51% | -23.14% | 已作为 `research_only` 存档；动态 Top20 四股版本更稳，但收益提升有限。 | `UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_universe_top20_backtest/summary.csv` |
| Top50 `top3_cap35_no_defense`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 32.42% | -28.64% | 对应已存档的 `mega_cap_leader_rotation_aggressive` 分支；收益高于 Top20，但集中度更高。 | `UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_top50_long_cycle_validation/validation_summary.csv` |
| Top50 `blend_top2_50_top4_50`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 36.41% | -30.56% | 当前最强无杠杆候选之一；回撤可接受但 Top2 袖子带来集中风险，需要 paper 观察。 | `UsEquitySnapshotPipelines/data/output/mega_cap_leader_rotation_dynamic_top50_concentration_variants/concentration_variant_summary.csv` |
| Top50 `top2_cap50_no_defense`，21 日 universe lag | 2017-10-02 至 2026-04-16 | 39.83% | -38.79% | 收益最高但两只股票 50/50 太集中，作为 aggressive research，不作为默认。 | `UsEquitySnapshotPipelines/docs/mega-cap-leader-rotation-dynamic-validation.md` |
| Crisis unified response historical research，含旧 5% TACO 袖子 | 1999-03-10 至 2026-04-16 | 23.89% | -56.04% | 相比合成 TQQQ 基线显著降低 2000/2008 级别灾难回撤；但该历史版本包含 TACO，不等于当前 defense-only shadow plugin。 | `UsEquitySnapshotPipelines/data/output/crisis_response_audit_trial/external_fragility10_severe10_fin_credit/summary.csv` |

暂时没有写进正式表的内容：

- `soxl_soxx_trend_income`：当前策略逻辑和测试覆盖完整，但本仓库没有归档一份可直接复查的长期 backtest summary。历史口头结果需要重新跑并提交 summary 后才能写成正式指标。
- `global_etf_rotation`、`russell_1000_multi_factor_defensive`、`tech_communication_pullback_enhancement`：当前有策略逻辑、snapshot/publish 流水线或命令，但缺少一份统一归档的 promoted backtest 表。后续要补齐 `summary.csv` 或验证文档。

## 研究中但未进入实盘的方向

| 研究方向 | 当前状态 | 不直接实盘的原因 |
| --- | --- | --- |
| `crisis_response_shadow` 插件 | 可作为 `tqqq_growth_income` 的 `shadow` 插件候选，只写信号、日志和通知上下文。 | 现在是 defense-only 黑天鹅观察流，不下单、不改 allocation；需要 20 个交易日以上稳定 shadow 日志后再做 evidence review。 |
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
