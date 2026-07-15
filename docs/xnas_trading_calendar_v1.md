# XNAS TradingCalendarV1

Checked-in caller-owned calendar artifact: `backtest/xnas_trading_calendar_v1.json`.

- schema `us_equity.trading_calendar.v1`, exchange `XNAS`, timezone `America/New_York`
- revision `xnas-2025-research-v1`, coverage 2025-01-02..2025-07-21, 137 sessions
- canonical JSON and SHA-256 digest are validated before use
- regular and approved half-day close instants are explicit; no runtime fallback or bar-date inference
- updates require an independent reviewed PR
