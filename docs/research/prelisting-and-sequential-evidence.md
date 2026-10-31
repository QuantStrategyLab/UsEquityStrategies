# Prelisting proxy and sequential evidence protocol

This protocol defines retrospective, research-only diagnostics. It grants no
trading, risk-budget, parameter-selection or promotion authority. Registration
in a source file is a declaration of methods, not independent proof that the
declaration preceded all inspection of the data. The 2026 period and the prior
64-path experiment are development data, not untouched out-of-sample evidence.

## Source and tradability boundaries

An observed ETF path starts no earlier than the first supported listed session;
prelisting ETF prices, positions and fills are unavailable. A proxy-signal
diagnostic evaluates indicators on observed QQQ or SOXX bars without representing
the proxy signal as a leveraged ETF trade. A hypothetical 3x path is a separate
synthetic instrument: daily compounding, annual expense, financing and tracking
assumptions must be supplied and recorded before construction. The model is not
an ETF return history, does not reconstruct the actual benchmark or management
process, and may not silently splice synthetic and observed returns. Missing
source history, adjustments, corporate actions or model assumptions must be
reported as a gap. A retrospectively adjusted series does not prove historical
publication availability or permit independent corporate-action reconciliation.
`proxy_signal_diagnostics` reuses the frozen indicator implementation and
returns unknown for a range signal until its lookbacks are warm. Its supplied
first tradable session must come from independently verified instrument data;
the first row of an incomplete price file is not evidence of listing. The
synthetic close-only helper charges each declared annual drag divided by 252
per session and stops on an unmodeled loss of 100% or more. Neither helper
produces orders or a tradable ETF path.

Before examining the new extended histories, two fixed illustrative annual
drag scenarios are declared: expense 1%, financing 4%, tracking 0%; and expense
1%, financing 8%, tracking 2%. These are constant hypothetical stress inputs,
not actual historical ETF charges or a claim that funding costs were constant.
Report both scenarios and do not fit them to outcomes. Use post-listing through
2025 overlap only for an ex-post diagnostic of paired synthetic-versus-observed
tracking differences; no 2026 fitting and no splice to actual tradable returns.
The underlying ETF and leveraged fund may follow different changing benchmarks,
so even the overlap comparison is not index-equivalent. Provider-supplied quote
OHLC and adjusted close are retrospective. Multiplying quote OHLC by the ratio
of adjusted close to quote close is an explicit adjusted-price convention, not
evidence of original unadjusted prices or a verified corporate-action ledger.

A separate six-case floating-rate extension is declared before its outcomes:
1% annual management, financing of twice the prior available DFF plus a
0%, 0.5%, or 1% annual spread, and tracking drag 0% or 0.5% annually. Costs
accrue over calendar days between proxy sessions on a 365-day basis. The DFF
observation must precede the ending session and be no more than seven calendar
days old. This is an assumed availability lag using revised data, not a
historical point-in-time feed or actual fund financing. Report every scenario.
A separate 144-path synthetic reference extension is frozen before running:
the six floating-rate assumptions, two frozen commission/slippage cases
(2+5 and 5+10 bps), one- and two-session lags, trend, trend plus RSI2 threshold
10, and trend-range-hold for each underlying proxy. Synthetic leveraged open
is the previous synthetic close times one plus three times the underlying
overnight return; synthetic close is the previous synthetic close times one
plus three times the underlying close-to-close return less calendar-accrued
costs. Nonpositive marks stop the run. These hypothetical opens are model
assumptions, not real ETF fills. Each path starts after indicator warmup and
ends strictly before leveraged ETF listing. Report every path and failure;
this is not production parity or a historical leveraged-ETF return series.
When the trend rule is active, the frozen reference allocates 45% to the
synthetic leveraged instrument and 45% to QQQ, or 70% to the synthetic
instrument and 20% to SOXX. The balance is cash, subject to the frozen overlay
and transaction-cost rules. SOXX's predecessor history is a sector-proxy input,
not an exact SOXL benchmark reconstruction.
QQQ and SOXX are observed ETFs only from their own first provider quote; index
series are separate price-index diagnostics, not investable total-return paths.
Before 2010-10-15, the SOXX predecessor IGW tracked a different semiconductor
index; its history is a sector proxy, not the contemporaneous SOXL benchmark.
Subsequent SOXX/SOXL benchmark changes were not simultaneous in 2021.

## Fixed paired 2026 analysis

Compare the frozen trend model with trend plus RSI2 threshold 10 for both assets,
both existing cost scenarios and both declared execution lags. Preserve each
path's actual pre-2026 portfolio state; do not restart with cash at January 1.
Report all eight paired comparisons, their matched daily-return inputs and
calendar, and the relevant 2026 start/end dates. This is a retrospective
diagnostic of the simplified frozen study, not production strategy performance
or a new parameter search.

## Sequential Bayesian diagnostic

Use non-overlapping calendar-month observations of paired net log-return
differences. Fix the Gaussian likelihood variance from completed pre-2026
months. The Gaussian prior for the paired log-return difference is centered at
zero, with one effective prior observation in the primary result. Fixed
sensitivity results use 0.25 and 4 effective prior observations. This paired
model does not reuse the single-asset B4 kernel's different prior convention.
Add one matured month at a time. Distinguish outcome end,
availability and decision cutoff; an assumed retrospective timing convention
is not evidence of actual historical publication. Missing, revised, duplicate,
out-of-order or future observations cannot silently update the prefix. Publish
the entire posterior sequence and its conditional model assumptions, not just
a favorable terminal result. Monthly observations can remain serially dependent
and nonstationary; a fixed estimated variance ignores its estimation uncertainty
and selection of prior research. Posterior updating is not Bayesian optimization
and confers no sizing or execution permission. Independent prospective
observations require authenticated timestamps and separately frozen inputs.
The diagnostic treats a month as completed only after a session in the next
month appears in the input; this convention does not certify exchange-calendar
completeness. Alternatively a caller may supply a bound, full-year session
calendar; all observed dates must match its expected sessions through the cutoff,
and a month can then be included after its last listed session. In calendar
mode, `assumed_available_at` is the first listed session strictly after that
month's last session. It comes from the bound calendar, independently of later
return rows, so extending or truncating the return paths cannot change an earlier
month's availability date. A missing successor session rejects the diagnostic
with `CALENDAR_NEXT_SESSION_REQUIRED`; it never substitutes the outcome date.

`through` is a date-only retrospective cutoff that assumes the specified
session has ended. It does not establish an intraday decision time. A month-end
cutoff may include the completed outcome while recording its assumed
availability in the next session; this does not make the posterior available
for a decision at the earlier cutoff. The next-session date is a conservative
research convention, not an authenticated publication or delivery timestamp.
Calendar identity and session-list hashes are recorded, but caller-supplied
calendar identity is not independent exchange certification or publication
proof. The two timing modes are distinct revisions and their outputs must be
retained. Results generated before the successor-session correction remain
separate historical research artifacts.
It verifies the two expected path SHA-256 values supplied by the
caller, but their independent registration and source authenticity are separate
requirements. To reproduce a pair, run:

```sh
python -m us_equity_strategies.research.paired_bayesian_update \
  --baseline BASELINE.csv --baseline-sha256 BASELINE_SHA256 \
  --candidate CANDIDATE.csv --candidate-sha256 CANDIDATE_SHA256 \
  --through 2026-10-30 > paired-result.json
```

The output is retrospective, non-executable and ineligible for promotion. It
preserves the original path's preperiod carry. A source-digest assertion does
not prove the timestamp at which those bytes became available.
