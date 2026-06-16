# 美股策略通知、i18n 与日志契约

[English](us_equity_notification_i18n_contract.md)

_更新日期：2026-05-29_

这份文档定义策略层输出给下游平台仓库的结构化通知契约。策略层负责事实、数值、机器码和翻译参数；平台仓库负责 Telegram / dry-run / 审计日志的最终排版、推送和压缩展示。

当策略消费侧车插件 artifact 时，插件提供的中英文文案只能作为展示层字段。策略可以透传插件的 `localized_messages` 和 `log_record`，但仓位逻辑必须继续读取 `canonical_route`、`suggested_action`、`reason_codes`、`position_control` 等机器字段。

## 策略输出位置

有通知内容的 runtime entrypoint 应在两个位置同时暴露同一份结构：

- `StrategyDecision.diagnostics["notification_context"]`
- `StrategyDecision.diagnostics["execution_annotations"]["notification_context"]`

推荐顶层结构：

```python
notification_context = {
    "signal": {
        "code": "signal_blend_gate_risk_on",
        "fallback": "SOXX above 140d gated entry, hold SOXL 70.0% + SOXX 20.0%",
        "params": {
            "trend_symbol": "SOXX",
            "window": 140,
            "soxl_ratio": "70.0%",
            "soxx_ratio": "20.0%",
        },
    },
    "status": {
        "code": "market_status_blend_gate_risk_on",
        "fallback": "RISK-ON (SOXX+SOXL)",
        "params": {"asset": "SOXX+SOXL"},
    },
    "benchmark": {
        "symbol": "SOXX",
        "price": 275.21,
        "long_trend_value": 241.30,
        "entry_line": 260.60,
        "exit_line": 236.47,
    },
    "portfolio": {
        "total_equity": 1000000.0,
        "raw_buying_power": 120000.0,
        "reserved_cash": 30000.0,
        "investable_cash": 90000.0,
        "holdings_order": ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
        "holdings": {
            "SOXL": {"market_value": 0.0, "quantity": 0.0},
            "SOXX": {"market_value": 420000.0, "quantity": 1000.0},
        },
    },
}
```

`tqqq_growth_income` 当前的 `signal` 更轻量，主要输出 `state`，因为展示文案仍由 `signal_text_fn` 提供；平台侧应把 `state` 当作这个 profile 的稳定机器字段。

## i18n 规则

- `code` 是稳定翻译 key，必须适合机器读取，不要把渲染后的数值写进 key。
- `params` 放翻译参数；如果展示格式必须固定，可以传入已经格式化好的字符串，例如 `"70.0%"`。
- `fallback` 是没有翻译表时的英文兜底文案。
- 策略代码不能要求平台解析 `dashboard` 文本来获取信号状态、指标、现金或持仓。
- 如果 translator 返回原 key，entrypoint 会回退到 `fallback`。

## 已渲染展示字段

为了兼容现有平台 renderer，entrypoint 可以继续写入已渲染文本：

- `execution_annotations["signal_display"]`
- `execution_annotations["status_display"]`
- `execution_annotations["dashboard_text"]`

这些字段只是展示结果。日志、审计和平台逻辑应优先使用结构化 `notification_context` 与数值 diagnostics。

## 日志格式

平台审计日志建议每次策略评估保留一条结构化事件。推荐最小字段：

```json
{
  "event": "strategy_evaluation",
  "strategy_profile": "soxl_soxx_trend_income",
  "as_of": "2026-05-26",
  "signal_date": "2026-05-26",
  "effective_date": "2026-05-27",
  "execution_timing_contract": "next_trading_day",
  "target_mode": "value",
  "signal_code": "signal_blend_gate_risk_on",
  "status_code": "market_status_blend_gate_risk_on",
  "notification_context": {},
  "execution_annotations": {},
  "income_layer": {
    "applied": true,
    "ratio": 0.270,
    "mode": "log_total_drawdown_budget",
    "start_usd": 150000.0,
    "max_ratio": 0.95,
    "account_drawdown_budget_ratio": 0.35,
    "account_stress_drawdown_ratio": 0.35
  }
}
```

规则：

- `notification_context` 保持 JSON 结构，不要只存字符串化的 dashboard。
- Telegram / report 的最终文案如果需要留档，应单独存。
- 不要把券商密钥、账户号、访问 token 或原始账户标识写入该 payload；如果需要账户标识，只能使用已经哈希过的 account id。
- 下单顺序、订单约束等券商执行细节继续属于平台仓库，不进入策略契约。

## 侧车插件通知文案

`market_regime_control` 插件会输出 `strategy_plugin_messages.v1` 和
`strategy_plugin_log.v1` 展示契约。策略消费端应在
`notification_context["risk_controls"]["market_regime_control"]` 下保留这些字段：

- `localized_messages`
- `log_record`
- `notification`

这些字段让平台仓库可以统一渲染英文和中文通知 / 日志，不需要重复维护 route/action 翻译表。它们不是交易输入。TQQQ 和 SOXL/SOXX 的交易逻辑读取
`canonical_route`、`position_control` 和
`position_control.volatility_delever_context` 等机器字段；本地化文案只用于展示。SOXL/SOXX 默认启用
`market_regime_control` 的 `risk_off` 和确定性波动率降杠杆 retention context；`risk_reduced` 仓位影响在默认策略配置中仍关闭。

## 当前覆盖

- `tqqq_growth_income` 和 `soxl_soxx_trend_income` 已把 `notification_context` 写入 diagnostics 与 execution annotations。
- 月频 weight-mode profile 通过策略 metadata 输出 `signal` / `status` 翻译上下文；entrypoint 使用传入 translator 渲染，同时保留结构化 payload。
- 日频 value-mode profile 还会输出执行时间契约字段：`signal_date`、`effective_date`、`execution_timing_contract`、`signal_effective_after_trading_days`。
- 市场状态插件消费者在存在有效插件 artifact 时，会透传插件 `localized_messages`、`log_record` 和 `notification`，供下游平台渲染。
