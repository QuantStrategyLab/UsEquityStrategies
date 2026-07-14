# TQQQ research simulator v1

This is a caller-owned, offline, research-only vertical slice for
`tqqq_growth_income`. It consumes sorted daily bars (including QQQ and every
symbol in the manifest's managed universe), uses the existing planner at each
close, and executes its dollar targets at the following open. The inclusive
window is marked at each close; the final close plan is discarded.

The simulator has fixed $100,000 cash, fractional long-only holdings, and zero
commission/slippage. It never downloads data, writes files, calls a broker, or
changes live entrypoints. `source_revision` and canonical UTC
`computed_at` (`YYYY-MM-DDTHH:MM:SS.ffffffZ`) are explicit inputs. Run and
parameter IDs are SHA-256 based on canonical input/config bytes. Results are
immutable and expose deterministic canonical JSON through `to_wire()`.

Required validation rejects missing/duplicate/unsorted/non-finite/non-positive
bars, missing symbols, unsupported targets, insufficient 200-day warmup, empty
windows, and invalid metadata. Metrics are total return, annualized return and
volatility (252), max drawdown, and zero-risk-free Sharpe; Sharpe is `None` when
there are too few observations or zero volatility.
