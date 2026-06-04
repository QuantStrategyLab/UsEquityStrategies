# US Equity Notification, i18n, and Log Contract

[简体中文](us_equity_notification_i18n_contract.zh-CN.md)

_Updated: 2026-05-29_

This document defines the strategy-layer payload shape that downstream platform repositories should use for Telegram messages, dry-run reports, and audit logs. Strategy code owns structured facts and translation keys; platform repositories own final layout, routing, and compaction.

When a strategy consumes a sidecar plugin artifact, plugin-provided display text
must stay display-only. The strategy may pass through the plugin's
`localized_messages` and `log_record`, but position logic must continue to read
machine fields such as `canonical_route`, `suggested_action`, `reason_codes`,
and `position_control`.

## Strategy Output

Every runtime entrypoint that has notification content should expose the same payload in both places:

- `StrategyDecision.diagnostics["notification_context"]`
- `StrategyDecision.diagnostics["execution_annotations"]["notification_context"]`

The recommended top-level shape is:

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

TQQQ currently uses a compact `signal` context with `state` because the display text is still supplied by `signal_text_fn`; platforms should treat `state` as the stable machine key for that profile.

## i18n Rules

- `code` is the stable translation key. It must be machine-readable and should not contain rendered numbers.
- `params` contains preformatted values when exact display formatting matters, for example `"70.0%"`.
- `fallback` is the English fallback for runtimes that have no translator entry.
- Strategy code must not require a platform to parse `dashboard` text to understand signal state, benchmark numbers, cash, or holdings.
- If a translator returns the key unchanged, the entrypoint falls back to `fallback`.

## Rendered Display Fields

Entrypoints may also attach rendered text for backward-compatible platform renderers:

- `execution_annotations["signal_display"]`
- `execution_annotations["status_display"]`
- `execution_annotations["dashboard_text"]`

These fields are display outputs only. Logs and platform logic should prefer the structured `notification_context` and numeric diagnostics.

## Required Log Fields

Platform audit logs should keep a single structured event per strategy evaluation. Recommended minimum fields:

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

Rules:

- Keep `notification_context` as JSON, not stringified dashboard text.
- Store rendered Telegram/report text separately if needed.
- Do not put broker secrets, account numbers, access tokens, or raw account identifiers in this payload. If an account identifier is needed, use an already-hashed account id.
- Order-routing hints stay in platform repositories and should not be added to the strategy contract.

## Sidecar Plugin Messages

The `market_regime_control` plugin emits `strategy_plugin_messages.v1` and
`strategy_plugin_log.v1` display contracts. Strategy consumers should preserve
these fields under
`notification_context["risk_controls"]["market_regime_control"]`:

- `localized_messages`
- `log_record`
- `notification`

These fields let platform repositories render consistent English and Chinese
notifications/logs without duplicating route/action translation tables. They
are not trading inputs. SOXL/SOXX does not enable `market_regime_control` by
default; it can still receive the general market-regime notification artifact
outside the strategy runtime for manual review.

## Current Coverage

- `tqqq_growth_income` and `soxl_soxx_trend_income` attach `notification_context` to diagnostics and execution annotations.
- Weight-mode monthly profiles expose `signal` and `status` translation contexts through strategy metadata; entrypoints render them through the supplied translator and preserve the structured payload.
- Daily value-mode profiles also attach execution timing metadata: `signal_date`, `effective_date`, `execution_timing_contract`, and `signal_effective_after_trading_days`.
- Market-regime plugin consumers pass through plugin `localized_messages`,
  `log_record`, and `notification` for downstream rendering when a valid plugin
  artifact is present.
