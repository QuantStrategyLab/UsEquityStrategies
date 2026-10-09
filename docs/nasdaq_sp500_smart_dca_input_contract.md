# nasdaq_sp500_smart_dca — frozen input contract (B07)

Profile: `nasdaq_sp500_smart_dca`  
Recorded: 2026-10-09 Asia/Shanghai (HKT)  
Baseline: UES after Phase A/B (`prefetched_market_history` + Schwab adopt)

## Smart vs ordinary

| Mode | `smart_multiplier_enabled` | Market IO |
| --- | --- | --- |
| Ordinary DCA | `false` (default) | No history/indicators required |
| Smart DCA | `true` | **Fail-closed** unless snapshot and/or prefetch supplied (Phase C) |

Smart resolution order per signal symbol:

1. `technical_indicator_snapshot` / `derived_indicators` (complete payload → `_indicator_from_payload`)
2. `prefetched_market_history[symbol]` → `_extract_close_series` → `_indicator_from_series`
3. Otherwise → `ValueError` (no `market_history(broker_client, symbol)` live call)

`broker_client` remains a kwarg for API compatibility but is **ignored in smart mode**.

## `prefetched_market_history` Mapping shape

Type: `Mapping[str, object]` keyed by signal symbol (case-insensitive; `.US` suffix stripped via `_normalize_symbol`).

Each value must be accepted by `_extract_close_series`:

| Form | Behavior |
| --- | --- |
| `pandas.Series` | Coerced to float; NaNs dropped. DatetimeIndex preferred (Schwab loader). |
| `pandas.DataFrame` | Uses `close` column if present; else first column. |
| Iterable of mappings | Each item’s `close` key used. |
| Iterable of objects | `getattr(item, "close", item)` then coerce float. |
| Iterable of scalars | Coerced to float closes (synthetic index). |

Empty / all-NaN series yields NaN indicators and will not invent fills.

## `technical_indicator_snapshot` (preferred)

Per-symbol mapping (or flat single-symbol mapping) with at least resolvable `close`/`price` and `sma200`/`ma200`. Optional: `sma50`, `high252`, `drawdown_252d`, `sma200_gap`, `rsi14`. Incomplete payload is skipped; smart mode then requires prefetch for that symbol.

## Platform duty (input builder)

- Prefetch signal symbols (default `QQQ`, `SPY`) into `prefetched_market_history`, **or**
- Supply complete `technical_indicator_snapshot` / `derived_indicators`
- Do not rely on strategy-side broker fetch for smart mode

Schwab Phase B: `fetch_reference_history` already materializes prefetch for this profile.
