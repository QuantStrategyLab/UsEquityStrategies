# Range RSI2 retrospective study

This is a frozen research screen, not a production strategy, an untouched out-of-sample test, or promotion evidence. It uses separate SOXL and TQQQ accounts and reports all 64 preregistered paths. It does not choose a winning threshold or alter live risk settings. RSI2 entry thresholds of 5, 10 and 15 are indicator levels; each candidate's mean-reversion sleeve is 10% of equity.

The range filter requires the underlying close within 5% of its 200-session simple moving average and a 20-session efficiency ratio at or below 0.3. A flat sleeve enters at its RSI2 threshold and exits at RSI2 of at least 70, loss of range eligibility, or after five held sessions. The frozen comparison includes the trend-only baseline, each standalone and combined candidate, and `trend_range_hold`, which adds the same 10% exposure whenever the range condition holds. Each profile is evaluated at one- and two-session lags and at 2 bps commission plus 5 bps slippage and 5 bps commission plus 10 bps slippage. All fills use adjusted opens, sell first and constrain buys to cash. Fractional adjusted shares, zero cash interest and no tax are assumptions, not broker execution evidence.

The frozen preregistration SHA-256 is `d2f4346e8ea9e0cd29af23e5175c1f98a63d88601512d636b7f4b191fd1fed8b`. SOXL/SOXX input SHA-256 is `b476bb54cc6221b2b616afb08dae3d9bf5aa6763d412565e2eb1abf0e5777c8a`; TQQQ/QQQ input SHA-256 is `d2f68dcdf46fae5954cfc27691ae9dcfaba4b05e425752275fb3b20d13eaa838`. QQQ is a research proxy for the production QQQM sleeve. The retrospectively adjusted prices are not point-in-time publication evidence; benchmark changes, exchange-certified calendars, actual fills, taxes and broker partial fills are not reconstructed.

Given explicit local files whose bytes match those hashes, run the frozen study and link the resulting research ledgers as follows:

```bash
python -m us_equity_strategies.research.range_rsi2_study \
  --preregistration /path/to/preregistration.json \
  --preregistration-sha256 d2f4346e8ea9e0cd29af23e5175c1f98a63d88601512d636b7f4b191fd1fed8b \
  --soxl-input /path/to/soxl-soxx-adjusted-ohlcv.csv \
  --soxl-sha256 b476bb54cc6221b2b616afb08dae3d9bf5aa6763d412565e2eb1abf0e5777c8a \
  --tqqq-input /path/to/tqqq-qqq-adjusted-ohlcv.csv \
  --tqqq-sha256 d2f68dcdf46fae5954cfc27691ae9dcfaba4b05e425752275fb3b20d13eaa838 \
  --output-dir /path/to/new-study-output

python -m us_equity_strategies.research.range_rsi2_lifecycle \
  --preregistration /path/to/preregistration.json \
  --soxl-input /path/to/soxl-soxx-adjusted-ohlcv.csv \
  --tqqq-input /path/to/tqqq-qqq-adjusted-ohlcv.csv \
  --output-dir /path/to/new-study-output
```

The runner verifies the frozen preregistration and both input hashes, creates and reads back all 64 QPK STARTED trials before performance calculations, performs actual-data prefix and future-perturbation checks, and writes every path and summary. An incomplete run records the failing scenario as FAILED and other unlinked scenarios as ABORTED; an interrupted run records ABORTED. An existing registry is never acquired or modified by a refused rerun. The lifecycle linker independently recomputes each full path and its frozen metrics before accepting its bytes, creates a QPK research result and validated daily ledger, and records a terminal outcome. When a terminal write or its readback cannot be resolved, the linker reports PERSISTENCE_UNCERTAIN rather than claiming a terminal status. QPK returns exclude the initial marked session; the frozen study includes the first session's return relative to flat initial cash. Both conventions remain explicit. Ledger trade cashflows are derived from recorded cash and fees; they are not independently reconciled broker fills. Registry timestamps are local assertions and do not independently attest prior registration.

The original `results.json` remains a snapshot from before ledger linkage and therefore reports that linkage as pending. Read `qpk_lifecycle_results.json` for the terminal readback and independently recomputed path status; do not infer completion merely from the presence of a summary file.

The reproduction of the frozen adjusted-price data for 2011-01-03 through 2026-10-30 matched the prior 64-row summary, period, annual and bootstrap files byte for byte. The table shows the primary RSI2 threshold of 10 with the lower cost scenario and one-session lag; it is a subset of the full 64 paths, not a selection or validation of a winner.

| Profile | Research path | CAGR | Maximum drawdown | Executed sleeve entries |
| --- | --- | ---: | ---: | ---: |
| SOXL | Trend | 24.36% | -73.92% | 0 |
| SOXL | Trend + RSI2 10 | 24.20% | -73.95% | 38 |
| SOXL | Trend + range hold | 23.95% | -75.60% | 113 |
| TQQQ | Trend | 19.00% | -41.51% | 0 |
| TQQQ | Trend + RSI2 10 | 19.68% | -41.95% | 49 |
| TQQQ | Trend + range hold | 19.86% | -43.11% | 136 |

`range_rsi2_forward` separates actual production-builder trend targets, the research sleeve contribution, and a research-only nominal risk cap and cash remainder. That cap is not the central QPK risk gate and does not establish leveraged exposure approval or execution authority. A prospective observation is accepted only as research evidence when an independently trusted verifier authenticates the registration, source bytes and publication, observation timestamps, and evaluation clock; registration must precede availability and the forward window must begin after registration. Missing, stale, mismatched or unverified evidence blocks the observation. Even an accepted observation remains `NOT_QUALIFIED`; no prospective result is asserted by this retrospective run.
