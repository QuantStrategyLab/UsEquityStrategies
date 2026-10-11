# 研究与实盘一致性清单（RS-07，只读核查）

> 只读整理，不改生产、不改预算。日期 2026-10-11。目的是让研究回放能绑定"实盘到底跑的是哪个版本、哪份配置、哪种数据"。

## 1. 版本三方不一致

| 来源 | UES 版本 | 说明 |
| --- | --- | --- |
| IBKR `RUNTIME_TARGET_JSON.strategy_release.strategy_revision` | `4a3943883cd6…`（2026-09-17） | 来自既有预注册核查记录 |
| IBKR / Schwab 仓库安装 pin（`pyproject.toml`、`uv.lock`、`qsl.toml`） | `9d1544d1476d…`（2026-10-09） | 平台镜像按这个构建 |
| QRS `internal_dependency_matrix.json`（Schwab、IBKR 两项） | `4a3943883cd6…` | 落后于平台 pin |
| 研究代码（UES main） | 与 `9d1544d1` 的策略、manifest、入口文件完全相同 | 研究按安装 pin 回放 |

- `4a394388` 与 `9d1544d1` 在当前 UES 历史里**没有共同祖先**（仓库历史重建过）。两者在运行时相关文件上的差异只有三处：`entrypoints/__init__.py`（新增研究用的 `research_session_successor` 参数、`prefetched_market_history` 透传）、`strategies/tqqq_dual_drive_core.py`（抽出 `decide_tqqq_preplugin_risk_on`）、`strategies/nasdaq_sp500_smart_dca.py`。SOXL 策略文件和 manifest 在两版之间相同。
- QPK：IBKR、Schwab 安装的是 `155d74a6`；UES 研究 pin 是 `f6f2079f`。实盘指标函数 `runtime_inputs.py` 在这两版里完全相同。

## 2. 配置 SHA 不一致

`strategy_release.config_sha256`：SOXL `0fbd7b3b…`、TQQQ `90e8ded5…`。用 manifest 默认值复算对不上，说明实盘配置还有平台合并的覆盖项，或者哈希的规范化方式不同。

## 3. 指标输入在实盘里的真实来源（代码核查）

| 项 | IBKR | Schwab | 研究 v2 |
| --- | --- | --- | --- |
| 优先来源 | 外部市场信号包（`resolve_external_market_signal_inputs`），配置了就直接用 | — | 不用 |
| SOXL 历史价格 | yfinance `Adj Close`（拆股+分红复权，`auto_adjust=False` 取 Adj Close）；拿不到再用 IBKR `ADJUSTED_LAST`（同样含分红复权） | Schwab `get_price_history`，2 年日线（预计只做拆股调整，待导出确认） | 主：拆股复权价；敏感性：总收益指数（接近 IBKR） |
| 历史长度 | 请求 "420 D"，被换算成 2 年（约 504 根） | 2 年 | 最多 504，窗口初期最少 280 |
| `trend_ma_window` | 合并配置里的值，缺省时 150 | 运行配置里的值，缺省时 150 | manifest 的 140 |
| 其他指标参数 | QPK 默认值 | QPK 默认值 | QPK 默认值（测试保证与 manifest 一致） |

结论：IBKR 和 Schwab 在信号价格口径上本身就不同，回测标准要求拆股复权价，所以研究把拆股复权作为主口径，总收益作为 IBKR 敏感性。`trend_ma_window` 如果实盘缺省成 150，就与 manifest 的 140 不一致，必须导出确认。

## 4. 需要从实盘只读导出的内容（用来绑定版本和配置）

每个账户（Schwab live、IBKR u15998061 / u16608560 / u18308207 / u18336562 中跑 SOXL 或 TQQQ 的）：

1. **镜像与包版本**：当前承接流量的 Cloud Run revision 名、镜像 digest；容器内 `us-equity-strategies`、`quant-platform-kit` 的 `direct_url.json` 提交号。
2. **策略发布块**：`RUNTIME_TARGET_JSON` 中 `strategy_release` 全文（`strategy_revision`、`config_sha256`、`runtime_risk_limits`），以及计算 `config_sha256` 用的配置原文和规范化函数名。
3. **合并后的运行配置**：每个策略档（`soxl_soxx_trend_income`、`tqqq_growth_income`）最终传给策略的 `merged_runtime_config`，至少包括 `trend_ma_window`、RSI 与波动率参数、插件开关、收入层参数。
4. **信号输入来源**：最近 N 次运行（建议 ≥20 个交易日）每次实际走的分支（外部信号包 / yfinance / IBKR ADJUSTED_LAST）、返回的历史根数、首尾日期。
5. **指标快照**：同样这些运行的 SOXX `rsi14`、`rsi14_dynamic_threshold`、`ma_trend`、`realized_volatility_dynamic_threshold`，SOXL `ma_trend`，以及 TQQQ 的 QQQ MA200、MA20 斜率、回撤反弹值，用来逐日对比研究回放。
6. **目标与成交**：同期的目标权重、实际下单和成交（数量、价格、费用），用于校准换手和成本，也是证券级净交易账本（见 `kelly_security_level_ledger_design.zh-CN.md`）的验证数据。
7. **插件挂载**：`SCHWAB_STRATEGY_PLUGIN_MOUNTS_JSON` 和 IBKR 对应项，以及 market_regime_control 当日信号是否授权仓位（shadow 还是生效）。

拿到 1–3 后，研究可以把回放代码固定到实盘实际版本、复算 `config_sha256`；拿到 4–5 后可以做逐日指标对账；拿到 6 后才能校准成本模型。在这之前，所有研究结果都只是 `DEVELOPMENT_ONLY`。
