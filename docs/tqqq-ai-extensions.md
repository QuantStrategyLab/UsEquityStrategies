# TQQQ AI Extension Architecture

This document defines the first AI-extension architecture for
`tqqq_growth_income`. The base strategy remains the source of truth for the
normal QQQ/TQQQ/BOXX allocation. The current strategy package only declares the
extension contract and emits diagnostics; it does not apply AI outputs to target
weights.

## Default state

AI extensions are **off by default**.

```python
{
    "ai_extensions": {
        "enabled": False,
        "mode": "off",
        "apply_order": ("crisis_regime_guard", "taco_panic_rebound"),
        "modules": {
            "taco_panic_rebound": {"enabled": False, "mode": "paper"},
            "crisis_regime_guard": {"enabled": False, "mode": "paper"},
        },
    }
}
```

When the config is missing, disabled, or malformed, the runtime should keep the
base `tqqq_growth_income` target weights. This is the fail-safe behavior.

## Future layer order

If a future extension engine is accepted after research and paper review, the
target allocation should be built in this order:

1. `tqqq_growth_income` calculates the base target values.
2. `crisis_regime_guard`, if enabled, may reduce growth-layer risk.
3. `taco_panic_rebound`, if enabled, may use a small idle sleeve for TQQQ.
4. A final risk-limit step normalizes symbols, caps exposure, and applies the
   normal rebalance threshold.

The order is intentional: systemic risk controls must run before opportunity
overlays. The implementation merged in this repository remains diagnostics-only.

## Budget model

| Layer | Default budget | Funding source | Purpose |
| --- | ---: | --- | --- |
| Base growth layer | QQQ 45% / TQQQ 45% | Main strategy equity | Normal trend-following exposure |
| Base reserve | BOXX 8% / cash 2% | Main strategy equity | Idle and execution buffer |
| TACO overlay | 5% account sleeve | BOXX/cash only | Small policy-shock rebound sleeve |
| Crisis guard | No separate sleeve | Growth-layer multiplier | Defensive risk reduction |

V1 TACO must not take capital from the base QQQ/TQQQ growth book. If BOXX/cash
is too small, the overlay is capped by available idle capital. This keeps the
module additive but bounded.

## Module responsibilities

### `taco_panic_rebound`

Purpose: capture a small TQQQ rebound after tariff/trade-war policy shocks.

V1 rules:

- target asset: `TQQQ`
- safe asset: `BOXX`
- default sleeve: `0.05`
- trigger mode: `price_stress_only`
- AI mode: `classify_only`
- event family: `tariff_trade_war`
- minimum confidence: `0.80`
- VIX does not veto or reduce position size
- macro indicators do not veto or reduce position size

The market-price gate opens the news scanner. AI classifies the event. Rules,
not the model, decide whether to trade.

### `crisis_regime_guard`

Purpose: identify systemic crisis regimes such as credit freezes, bank stress,
liquidity stress, or bubble unwind risk.

V1 design state:

- default disabled
- default mode `paper`
- no separate capital sleeve
- output should be a risk multiplier or a no-op
- it should reduce risk, not add risk
- it must be researched separately before live use

The first implementation should record diagnostics only. It should not reduce
TQQQ or QQQ until the crisis-regime backtest is accepted.

## Runtime signal contract

Runtime repositories should provide AI outputs as structured data, not free-form
text. The preferred location is:

```python
StrategyContext.state["ai_extension_signals"]
```

`StrategyContext.artifacts["ai_extension_signals"]` or
`StrategyContext.market_data["ai_extension_signals"]` can be used by adapters
that already route auxiliary data through those fields.

Example:

```json
{
  "taco_panic_rebound": {
    "event_family": "tariff_trade_war",
    "polarity": "shock",
    "confidence": 0.88,
    "target_overlay_exposure": 0.25,
    "expires_at": "2026-04-23",
    "reason": "Tariff shock with QQQ price stress"
  },
  "crisis_regime_guard": {
    "systemic_crisis_regime": false,
    "risk_multiplier": 1.0,
    "confidence": 0.64,
    "reason": "No broad credit or liquidity stress confirmed"
  }
}
```

The model output is advisory. The strategy layer must still enforce position
caps, funding source, stop rules, duplicate-event checks, and rebalance
thresholds.

## How to enable

Start in paper mode:

```json
{
  "ai_extensions": {
    "enabled": true,
    "mode": "paper",
    "modules": {
      "taco_panic_rebound": {
        "enabled": true,
        "mode": "paper",
        "sleeve_ratio": 0.05,
        "trigger_mode": "price_stress_only",
        "use_vix_for_position": false,
        "use_macro_veto": false
      },
      "crisis_regime_guard": {
        "enabled": false,
        "mode": "paper"
      }
    }
  }
}
```

The strategy repository treats `live` as a contract value only. Setting it here
still has no position effect until a separate extension engine is implemented,
backtested across short/medium/long windows, and explicitly accepted for live
use.

```json
{
  "ai_extensions": {
    "enabled": true,
    "mode": "live",
    "modules": {
      "taco_panic_rebound": {
        "enabled": true,
        "mode": "live",
        "sleeve_ratio": 0.05
      }
    }
  }
}
```

`crisis_regime_guard` must stay paper-only until a separate 2000/2008/2020/2022
regime backtest is accepted.

## Repository split

- `UsEquityStrategies`: strategy contract, default disabled config, extension
  diagnostics, and fail-safe no-op behavior.
- `UsEquitySnapshotPipelines`: research/backtest scripts and event calendars.
- Platform runtime repositories: news collection, OpenAI API calls, secret
  handling, signal persistence, paper/live mode selection, and execution.

This split keeps AI, news, secrets, and broker execution out of the shared
strategy core while still allowing every platform to consume the same extension
contract.
