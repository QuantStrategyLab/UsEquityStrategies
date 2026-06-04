# UsEquityStrategies

[English README](README.md)

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## 这个仓库是什么

UsEquityStrategies 是 QuantStrategyLab 的美股策略包。为 QuantStrategyLab 美股执行平台提供可复用策略实现和运行元数据。

它属于一套多仓库量化系统中的一层：

- **策略包**：保存可复用策略代码、元数据和运行入口。
- **Snapshot 流水线**：生成 feature snapshot、ranking、回测和发布证据。
- **执行平台**：把策略接到券商、dry-run 检查、通知和 live 部署控制。
- **共享基础设施**：维护契约、配置、适配器、插件和审计 workflow，供多仓复用。

本仓库负责策略代码和元数据，不保存券商凭据，不直接提交订单，也不替代 live enable 前需要看的 snapshot 和回测证据。

## 策略 profile

### 普通 runtime 策略

这些 profile 可以基于 market history、portfolio snapshot 或其他运行时输入执行，不需要单独先生成 feature snapshot。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `global_etf_rotation` | Global ETF Rotation | 使用 market history 的 runtime-enabled ETF 轮动。 |
| `tqqq_growth_income` | TQQQ Growth Income | QQQ/TQQQ dual-drive，带防守和 income sleeve。 |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | 半导体 ETF 趋势策略。 |
| `nasdaq_sp500_smart_dca` | Nasdaq/S&P 500 Smart DCA | 面向宽基美股 ETF 的买入型 DCA profile。 |

### Snapshot-backed 策略

这些 profile 依赖 `UsEquitySnapshotPipelines` 生成的 artifact；下游平台使用前，应先确认对应产物已经验证和提升。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `russell_1000_multi_factor_defensive` | Russell 1000 Multi-Factor | 基于 feature snapshot 的 runtime-enabled 美股大盘股选择器。 |
| `mega_cap_leader_rotation_top50_balanced` | Mega Cap Leader Rotation Top50 Balanced | 基于 feature snapshot 的 mega-cap leader rotation。 |

### 研究侧候选

研究侧 profile 可以保留在代码里用于复现和后续评审，但不应该出现在当前可配置 live profile 中。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | 已归档 research-only，不再是 catalog/entrypoint 可运行 profile。 |

## 如何接到执行平台

执行平台通过 strategy loader 和 runtime metadata 消费本策略包。当前下游平台：CharlesSchwabPlatform、InteractiveBrokersPlatform、LongBridgePlatform 和 FirstradePlatform。

券商凭据、dry-run/live 开关、订单提交和部署配置都应放在执行平台仓库里，而不是放在策略仓库里。

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

- [`docs/tqqq_ai_extensions.md`](docs/tqqq_ai_extensions.md)
- [`docs/us_equity_contract_gap_matrix.md`](docs/us_equity_contract_gap_matrix.md)
- [`docs/us_equity_notification_i18n_contract.md`](docs/us_equity_notification_i18n_contract.md)
- [`docs/us_equity_notification_i18n_contract.zh-CN.md`](docs/us_equity_notification_i18n_contract.zh-CN.md)
- [`docs/us_equity_portability_checklist.md`](docs/us_equity_portability_checklist.md)
- [`docs/us_equity_runtime_archive.zh-CN.md`](docs/us_equity_runtime_archive.zh-CN.md)
- [`docs/us_equity_strategy_status.zh-CN.md`](docs/us_equity_strategy_status.zh-CN.md)
- [`docs/us_equity_strategy_template.md`](docs/us_equity_strategy_template.md)

## 安全和贡献说明

- 不要把密钥、账户标识、token、Cookie 或券商凭据提交到 Git，也不要写进日志。
- 改动尽量小，并配套测试或可复现证据。
- 涉及策略行为的改动，请附上验证命令或产物路径。

## 社区和安全

- 贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，确认 PR 范围、本地校验和文档要求。
- 讨论、issue 和 review 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 涉及密钥、自动化、券商/交易所或云资源的漏洞请按 [SECURITY.md](SECURITY.md) 私密报告；不要为 secret 或实盘风险开公开 issue。

## 许可证

详见 [LICENSE](LICENSE)。
