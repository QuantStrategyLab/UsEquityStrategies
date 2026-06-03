# UsEquityStrategies

[English README](README.md)

> ⚠️ 投资有风险，不构成投资建议，仅供学习交流用途。

## 这个项目做什么

UsEquityStrategies 是 QuantStrategyLab 体系中的**策略包**。提供 QuantStrategyLab 平台共用的美股策略实现和执行元数据，包括 ETF 轮动和收益型配置模块。

## 适合谁使用

- 希望阅读、复现或扩展 QuantStrategyLab 相关模块的工程师和研究人员。
- 在阅读详细 runbook 或 workflow 前，需要先理解项目入口的运维人员。
- 在启用自动化前，需要确认项目职责、安全边界和证据要求的 reviewer。

## 当前状态

面向 live 的策略包。任何 live profile 都应有近期短/中/长周期验证支持。

## 仓库结构

- `src/`：主要库代码和运行时代码。
- `tests/`：单元测试和契约测试。
- `docs/`：详细设计说明、运行手册和证据文档。
- `.github/workflows/`：CI、定时任务和部署 workflow。

## 快速开始

从全新 clone 开始：

```bash
python -m pip install -e .
python -m pytest -q
```

如果命令需要凭据，请先阅读相关 workflow 或 runbook，并把密钥配置在 Git 之外。

## 部署和运行

在执行平台仓库中安装本包，或让平台策略加载器指向本仓库。执行凭据应留在平台仓库或密钥系统中，不应放在这里。

建议先手工运行或 dry-run。只有在日志、产物、权限和回滚步骤都检查过之后，才启用定时任务或 live 执行。

## 策略表现与证据边界

策略表现应以可复现回测和快照产物为准，不能只看 README 描述。live 使用前需要在短/中/长周期比较收益、最大回撤和相对基准表现；未通过回撤或基准门槛的策略应保留在研究状态。

README 不应该承诺固定收益或过期指标。实际使用前，请重新运行对应测试、回测或流水线任务。

## 安全注意事项

- 不要把 API key、券商凭据、OAuth token、Cookie 或账户标识提交到 Git。
- 新策略或平台变更在 live 前必须先跑 dry-run 或 paper 流程。
- 启用定时任务前，需要人工检查生成的订单、产物和日志。

## 参与贡献

请保持改动小、可复现，并用最小必要测试覆盖。涉及策略的改动，需要附上验证行为的证据产物或命令。

## 许可证

如仓库包含 [LICENSE](LICENSE)，请以该文件为准。
