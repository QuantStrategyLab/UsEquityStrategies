# SOXL / TQQQ 独立完整功能研究候选：参数快照

本轮选择先研究独立候选，不把它称为此前的“原优化版”或任何实盘配置。相邻的 [JSON 参数快照](independent_soxl_tqqq_full_features_20260924.json) 固定 UES `8da15399164eff850209b7712a244f8916ec28a9` 两份 manifest 展开的全部默认参数，分别命名为 `independent-soxl-full-manifest-v1` 和 `independent-tqqq-full-manifest-v1`。这只是未选参的起始规则，不是收益或仓位优化结论。

为匹配研究入口，快照把 `managed_symbols` 写成 JSON 数组，显式加入 `signal_effective_after_trading_days=1`，并把 TQQQ 回放入口缺键时视为启用的 `dual_drive_macro_risk_governor_enabled` 明确写为 `true`。JSON 不包含由本地适配器注入的 `translator` / `signal_text_fn` 函数。每份 `runtime_config` 以排序键、紧凑分隔符、UTF-8、`ensure_ascii=false` 和禁止非有限值的规范 JSON 计算 `config_sha256`。QPK 来源版本是当前 UES 锁定的 `c3dcf473c517853342cc893bb79d141e90208d8d`；后续代码或依赖变动须在实际运行身份中另行记录。

期权、收入层、市场状态及保留策略等启用功能保持 manifest 原值；开关为真并不授予插件影响仓位的权限。当前合成回放对未模拟的期权与目标插件明确拒绝；不能为了得到曲线而暗中关闭它们或沿用这两个候选 ID。若需要简化变体，应另建身份并单独评价。

参数快照还不是完整经济身份：逐日可见的指标和插件输入、期权成交、公司行为、现金收益与外部现金流、真实或假设成本、日历和观察窗口尚未冻结。2016–2024 年既有资料已用于历史研究，只能标为 development，不能重新当成未见样本。在这些来源及执行合同明确前，不生成完整成员收益、组合最优权重或成本后复利结论；任何回放仍为 research-only，不能授权 paper、shadow 或 live。
