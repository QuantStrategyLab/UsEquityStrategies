#!/usr/bin/env bash
# Local verification for the C2/C3/C4 research-only correctness slice.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="src${PYTHONPATH:+:${PYTHONPATH}}"
python3 -m pytest \
  tests/test_combo_evidence_aggregation.py \
  tests/test_daily_combo_evidence.py \
  tests/test_c3_fixed_budget_baseline_comparison.py \
  tests/test_c4_shadow_zero_submit_cycle.py \
  tests/test_c3_batch_a_existing_member_baseline.py \
  tests/test_portfolio_risk_budget.py \
  -q --tb=short
python3 -m ruff check \
  src/us_equity_strategies/combo_evidence_aggregation.py \
  src/us_equity_strategies/research/c3_fixed_budget_baseline_comparison.py \
  src/us_equity_strategies/research/c4_shadow_zero_submit_cycle.py \
  tests/test_combo_evidence_aggregation.py \
  tests/test_daily_combo_evidence.py \
  tests/test_c3_fixed_budget_baseline_comparison.py \
  tests/test_c4_shadow_zero_submit_cycle.py
python3 -m compileall -q \
  src/us_equity_strategies/combo_evidence_aggregation.py \
  src/us_equity_strategies/research/c3_fixed_budget_baseline_comparison.py \
  src/us_equity_strategies/research/c4_shadow_zero_submit_cycle.py
git diff --check
