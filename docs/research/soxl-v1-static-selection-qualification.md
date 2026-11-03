# SOXL v1 static-selection qualification

This correction concerns only `run_soxl_core_optimization` (SMA sensitivity)
and `run_soxl_volatility_scaling` (relative volatility). Their frozen v1
candidates, costs, windows, selectors, simulations, winners and numerical
metrics remain unchanged. It introduces no new causal candidate and grants
no runtime, sizing, trading, promotion or data-access authority.

## What the existing tests measure

Both branches choose one winner from all three validation windows, whose
latest raw input index is 583. They then report that same winner on these
test windows:

- F1: 413–454, before selection data ends
- F2: 499–540, before selection data ends
- F3: 585–626, after selection data ends

These are retrospective validation-selected test diagnostics. Disjoint
windows do not make F1/F2 candidate selection causal. Changing later
validation metrics can change the winner applied to those earlier tests.

The simulators use lagged close signals with next-open execution. That
price-signal timing remains valid within the declared simulation model;
it does not establish causal candidate selection or historically available
inputs. `selection_qualification` reports these two scopes separately,
including each test's index comparison. Indices are not an attestation of
authoritative sessions, point-in-time data, source availability or rights.

## Corrected qualification exits

- SMA's existing `r3_eligibility_status` is `FAIL`, with
  `CANDIDATE_SELECTION_NOT_CAUSAL_WALK_FORWARD` in `failure_codes`
- Relative volatility's existing broad `evidence_gates.no_lookahead` is
  `false`. It still participates in the existing aggregate gate, so numeric
  successes cannot produce a qualified recommendation
- Both branches return `research_recommendation=null` and the existing
  `NO_IMPROVEMENT` outcome. Here that means no qualified recommendation;
  it must not be interpreted as a change to the historical numerical result
- `retrospective_characterization_thresholds_passed` retains the old
  numerical threshold judgment as a diagnostic, with no qualification
  authority. SMA also retains its numerical cost/return screen as
  `retrospective_metric_eligibility_status`

`evidence_valid=true` means the declared offline computation completed its
existing checks. It does not mean causal WFA, untouched holdout, point-in-time
input qualification, full research acceptance or live adoption. Prior holdout
exposure remains `UNASSESSED`; evaluation order inside one invocation cannot
establish an untouched holdout across previous invocations or candidate families.

## Existing bundles and consumers

The schemas, persistence names and set-once protocol remain v1. Old bundles
remain byte-preserved and integrity-readable. Loaders verify hashes and
canonical bytes; they are not qualification gates. A historical bundle's
`no_lookahead=true` or R3 `PASS` must not be used as causal WFA evidence for
these two static-selection branches. Corrected results require a separate
output directory; an existing bundle cannot be silently overwritten.

The bounded source review found no shared or production qualification
consumer for these two schemas. A newly identified consumer requires a
separate review of its actual acceptance path. The RSI2 adapter checks its
own RSI2 schema; RSI2 already uses prefix selection per fold, and its
implementation, gates and result remain unchanged by this correction.

A future causal study needs a separately identified candidate/protocol with
each fold's selection data preceding that fold's test, plus its own input,
holdout-exposure and research-acceptance evidence. This patch does not create
that study or relabel the old static results as one.
