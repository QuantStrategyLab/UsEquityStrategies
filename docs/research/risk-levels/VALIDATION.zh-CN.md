# 实际验证记录

仅针对基线 `4330d3119ae5d83ce6de0b4a6d7b58d96f049c02` 加本目录设计文件。Python 3.13.7；未安装或升级依赖。

| 命令 | 退出码 | 实际结果 |
|---|---:|---|
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 docs/research/risk-levels/verify_design.py` | 0 | PASS_SCOPED_DESIGN_AND_INTERFACE_CHECKS；30参数、三份schema、两份示例；18个负例均被拒绝；六个缺口单元均无金融数值 |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/research/test_bayesian_kelly.py tests/research/test_cvar_exposure_constraint.py tests/test_portfolio_risk_budget.py` | 0 | `116 passed, 137 subtests passed in 0.80s`；原内核测试，无真实市场回放 |
| `git diff --check`（暂存文件另以 `git diff --cached --check`核对） | 0 | 无空白错误 |
| README本地Markdown链接存在性核对（Python pathlib） | 0 | 引用的仓库文件均存在 |

接口探针使用人工观察和获准的 TQQQ 核心名称（不是市场数据），当前B4对c=.10/.40返回 `FRACTION_OR_VETO_INVALID`，对.25返回 `AVAILABLE_RESEARCH_ONLY`。初次探针使用自定义核心名被内核以 `ORDERED_CORE_IDS_REQUIRED` 拒绝；修正探针名称后通过，没有修改内核。CVaR只验证既有函数边界分位与损失符号，risk-budget只验证合成目标被收紧且不授予执行。

本机 `quant_platform_kit.research_stats` 不存在。QPK产品pin和共享环境保持原样；无DSR/PBO或三档收益回测。相关测试通过不能补齐PIT、金融资格、真实trial、R4入账、线上采用或Grok Bot复核。

所有仓库新增文件都在 `docs/research/risk-levels/`；主checkout原dirty列表未变化：`docs/research/global_etf_absolute_volatility.md` 和未跟踪 `build/`。未读取其中内容或收进提交。
