# 策略发布前评估

`scripts/assess_strategy_release.py` 是策略包对 QuantPlatformKit 发布门禁的通用只读入口。它适用于每个策略、经纪商目标和插件组合。

调用方必须显式提供候选发布编号、已提交的 source revision、配置、风险规则、证据包、完整插件包和所有目标平台。脚本只输出脱敏诊断：`ready: true` 仅说明可由受控发布流程生成 manifest，`ready: false` 则禁止把该策略重载到任何平台。

它不会生成 manifest、修改参数、部署服务或下单。正式平台重载仍须在所有目标分别回传同一 release identity 后，经过观察期才允许进入 ACTIVE。

SOXL 当前没有仓库内可验证的 promotion 证据包，因此应以 `evidence_package_missing` 保持纸面阻断，先补全可复现回测与人工验收，再讨论重载。
