# 策略仓库拥有研究任务

本次本地迁移基线为 UsEquityStrategies `1fc21ccce5819f678e935ece839db48301466b72`。改动尚未提交、发布、部署或连接真实助手。

研究调用入口迁入本仓库：

- `us_equity_strategies.research.soxl_new_research`：冻结来源、SOXL 候选补丁、隔离测试与既有 QPK 数值研究流程。
- `us_equity_strategies.research.global_etf_review`：固定作者材料与固定候选的 advisory 审查；不修改候选。
- `us_equity_strategies.research.soxl_manual_learning`：人工触发的固定参数研究、保存验证、质量尾部和 paired shadow。
- `scripts.soxl_financial_explanation`：固定输入的确定性解释，不调用模型。保持独立启动脚本，避免预加载本仓库包污染冻结版本的数值运行环境。

通用调用使用 QuantPlatformKit 的 V2 任务客户端。SOXL 参数边界保存在 `research/watcher_task.py`；共享层只保存通用任务格式。历史 task schema、来源 hash、冻结数值代码、手续费、验证和人工审批条件保持原有含义。当前生产者身份使用实际策略仓库，不改写历史 producer 记录。

API 适合短解释；常驻助手适合候选设计和较长审查。模型、模式和 profile 在批准配置中明确指定，调用方不内置模型厂商或 VPS 路由。常驻结果记录 `model_requested` 与 `model_verification=unavailable`，不声称平台证明了实际模型。

Global ETF 的 `run-v2.json` 绑定来源、候选、请求与路由；完成后复用结果，等待时只读取原任务。提交响应丢失且没有任务 ID 时停止自动触发。SOXL codegen 同样保存任务身份。手动研究的 `--resume-ai-task-id` 只用于原来的人工学习请求；客户端重新校验原始请求，材料或路由变化不能采用旧结果。所有等待状态均不启动数值研究。

本地测试使用 synthetic 材料和假客户端。真实 Docker／冻结研究集成测试没有在本机执行。现有 CI 与依赖固定版本尚不含新的共享接口；新模块需要本批经过核验的 QPK 和 PersonalAIService wheel，不能直接从公共索引获取同名未发布包代替。旧 AIAuditBridge workflow 已退役，新 owner workflow 已补齐，当前不能切换生产入口。

Global ETF 的新工作流草稿位于 .github/workflows/global-etf-review.yml，归属策略仓库，默认 AI_SERVICE_RELEASE_READY 未启用；保留冻结候选 commit、main/人工触发/单次尝试与 Docker 检查。YAML 和 diff 检查通过，尚未运行云端工作流，消费者当前固定 QPK 版本尚不含新版任务客户端，须在批准发布后更新准确版本。旧 AAB 工作流已退役，云端尚未切换。

## 所有者流程收尾

漂移流程使用本仓库的新 reusable workflow，模型审查改为可配置三角色；来源、快照和金融准入保持原有边界。策略研究的新手动 workflow 默认关闭，通过 AI_SERVICE_RELEASE_READY 与经过批准的云端环境启用，未增加交易或自动采用权限。共享依赖必须使用准确批准的源码/产物；当前未发布，不能改成一个不存在的远端 SHA。
