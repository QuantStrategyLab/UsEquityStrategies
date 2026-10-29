# UsEquityStrategies

[研究与契约索引](docs/research/README.md)：英文概要、完整原文与历史运行参考。

UsEquityStrategies 是 QuantStrategyLab 美股交易背后的策略库：保存策略实现、manifest 和运行元数据，供执行平台仓库通过 strategy loader 加载。它消费 QuantPlatformKit 和 `UsEquitySnapshotPipelines` 产出的已验证 snapshot artifact，但不持有券商凭据、不直接提交订单、也不拥有部署设置——这些留在执行平台仓库（CharlesSchwabPlatform、InteractiveBrokersPlatform、LongBridgePlatform、FirstradePlatform）。

[English README](README.md)

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## 期权退役与收入预算迁移

生产入口不再默认提供或生成自营期权增强。显式旧配方、预算残留及已知期权义务会被明确拒绝并要求迁移；旧控制项只接受字面布尔 `false` 与空配方作为无操作值。缺少账户信息不证明没有期权持仓。历史研究回放独立保留，见[期权退役合同](docs/option-overlay-retirement.md)。

权重式收入分配从已有投资目标预算筹资，按标的最低目标去重并保留隐含现金，不会把原60%投资自动放大到100%。禁用时仍保留已有收入持仓的最低目标。预算不足会在返回可执行目标前中止整个策略决策，也可能延迟该周期原有的有效减风险目标；不能据此保证减仓订单一定执行，独立平台保护仍然必要。TQQQ/SOXL保留原金额计划与内嵌风控，见[收入预算合同](docs/income-budget-contract.md)。

## 这个仓库是什么

UsEquityStrategies 是 QuantStrategyLab 的美股策略包。为 QuantStrategyLab 美股执行平台提供可复用策略实现和运行元数据。

它属于一套多仓库量化系统中的一层：

- **策略包**：保存可复用策略代码、元数据和运行入口。
- **Snapshot 流水线**：生成 feature snapshot、ranking、回测和发布证据。
- **执行平台**：把策略接到券商、dry-run 检查、通知和 live 部署控制。
- **共享基础设施**：维护契约、配置、适配器、插件和审计 workflow，供多仓复用。

本仓库负责策略代码和元数据，不保存券商凭据，不直接提交订单，也不替代 live enable 前需要看的 snapshot 和回测证据。

## 配置

Export these environment variables before importing or running the offline R3 evidence runner to use existing local files. Relative defaults resolve from the working directory.

| Variable | Purpose | Required | Default |
|---|---|---|---|
| `UES_R3_OUTPUT_ROOT` | Offline evidence output directory | Optional | `.local/r3_joint_evidence` |
| `UES_R3_CONTRACT_PATH` | Local acceptance contract | Optional | `.local/r3_joint_evidence/acceptance-contract.md` |
| `UES_R3_WORKER_PROMPT_PATH` | Local worker prompt | Optional | `.local/r3_joint_evidence/worker-prompt.md` |
| `UES_R3_PRIVATE_ROOT` | Private research input root | Optional | `.local/private_research` |


## 策略 profile

### 普通 runtime 策略

这些 profile 可以基于 market history、portfolio snapshot 或其他运行时输入执行，不需要单独先生成 feature snapshot。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `tqqq_growth_income` | TQQQ Growth Income | QQQ/TQQQ dual-drive，带防守和 income sleeve。 |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | 半导体 ETF 趋势策略。 |
| `nasdaq_sp500_smart_dca` | Nasdaq 100 / S&P 500 Smart DCA | 面向宽基美股 ETF 的买入型 DCA profile；默认月度定额定投，可配置周/月/季频率和智能倍数。 |
| `ibit_smart_dca` | IBIT Smart DCA | 面向 IBIT 比特币现货 ETF 的买入型 DCA profile；默认月度定额定投，可配置周/月/季频率和智能倍数。 |

### Snapshot-backed 策略

这些 profile 依赖 `UsEquitySnapshotPipelines` 生成的 artifact；下游平台使用前，应先确认对应产物已经验证和提升。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `global_etf_rotation` | Global ETF Rotation | 基于 feature snapshot 的 ETF 轮动，带每日 canary 防守和 BIL safe haven。 |
| `russell_top50_leader_rotation` | Russell Top50 Leader Rotation | 基于 feature snapshot 的 Russell Top50 leader rotation。 |

### 研究侧候选

研究侧 profile 可以保留在代码里用于复现和后续评审，但不应该出现在当前可配置 live profile 中。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | 已归档 research-only，不再是 catalog/entrypoint 可运行 profile。 |

## 如何接到执行平台

执行平台通过 strategy loader 和 runtime metadata 消费本策略包。当前下游平台：CharlesSchwabPlatform、InteractiveBrokersPlatform、LongBridgePlatform 和 FirstradePlatform。

券商凭据、dry-run/live 开关、订单提交和部署配置都应放在执行平台仓库里，而不是放在策略仓库里。

## QSL 架构角色

- **层级**：`策略库`。
- **职责**：美股策略实现包。
- **事实源/归属**：runtime-enabled 美股策略代码、manifests、execution metadata。
- **消费对象**：QuantPlatformKit 和已验证 US snapshot artifacts。
- **禁止事项**：持有券商凭据或部署设置。

## 策略证据和 live enablement

README 只作为项目地图，不替代最新表现数据。启用或调整 live profile 前，需要重新运行相关 snapshot/backtest pipeline，并分别看短、中、长周期的收益、最大回撤、相对基准收益、换手、数据新鲜度和 artifact 版本。证据过期、不完整，或者 profile 仍标记为 research-only，就不要放进 live runtime settings。

## 仓库结构

- `src/`：库代码和运行时代码。
- `tests/`：单元测试、契约测试和回归测试。
- `docs/`：运行手册、设计说明、证据和集成契约。
- `.github/workflows/`：CI、定时任务、发布或部署 workflow。

## 快速开始

```bash
python -m pip install -e .
python -m pytest -q
```

## 延伸文档

- [`docs/tqqq-ai-extensions.md`](docs/tqqq-ai-extensions.md)
- [`docs/us-equity-contract-gap-matrix.md`](docs/us-equity-contract-gap-matrix.md)
- [`docs/us-equity-notification-i18n-contract.md`](docs/us-equity-notification-i18n-contract.md)
- [`docs/us-equity-notification-i18n-contract.zh-CN.md`](docs/us-equity-notification-i18n-contract.zh-CN.md)
- [`docs/us-equity-portability-checklist.md`](docs/us-equity-portability-checklist.md)
- [`docs/us-equity-runtime-archive.zh-CN.md`](docs/us-equity-runtime-archive.zh-CN.md)（[English](docs/us-equity-runtime-archive.md)）
- [`docs/us-equity-strategy-status.zh-CN.md`](docs/us-equity-strategy-status.zh-CN.md)（[English](docs/us-equity-strategy-status.md)）
- [`docs/research/ibit-smart-dca.md`](docs/research/ibit-smart-dca.md)
- [`docs/us-equity-strategy-template.md`](docs/us-equity-strategy-template.md)

## 安全和贡献说明

- AI 助手与维护者遵循 [AGENTS.md](AGENTS.md)。

- 不要把密钥、账户标识、token、Cookie 或券商凭据提交到 Git，也不要写进日志。
- 改动尽量小，并配套测试或可复现证据。
- 涉及策略行为的改动，请附上验证命令或产物路径。

## 社区和安全

- 贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，确认 PR 范围、本地校验和文档要求。
- 讨论、issue 和 review 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 涉及密钥、自动化、券商/交易所或云资源的漏洞请按 [SECURITY.md](SECURITY.md) 私密报告；不要为 secret 或实盘风险开公开 issue。

## 许可证

详见 [LICENSE](LICENSE)。
