"""Dual-target boundary, causal decisions, and cash/fee conservation only."""
import math
import unittest
from dataclasses import replace
from statistics import variance

from us_equity_strategies.research.rs04_mechanism_comparison import (
    CORE_TARGETS, ROWS, Fixture, budget_target, combine_targets, comparison_sessions,
    run_candidate, synthetic_panel,
)


class MechanismComparisonTests(unittest.TestCase):
    def test_dual_targets_share_one_security_book_and_budget(self):
        target, assessment = combine_targets(CORE_TARGETS["TQQQ"], CORE_TARGETS["SOXL"], (.5, .5))
        self.assertEqual(assessment["status"], "REDUCE")
        for symbol, expected in {"TQQQ": .25, "SOXL": .25, "BOXX": .48}.items():
            self.assertAlmostEqual(target[symbol], expected)
        self.assertAlmostEqual(sum(target.values()), .98)
        # Same-security contributions also merge before budgeting/trading.
        same, _ = combine_targets(CORE_TARGETS["TQQQ"], CORE_TARGETS["TQQQ"], (.5, .5))
        self.assertAlmostEqual(same["TQQQ"], .5)
        self.assertAlmostEqual(same["BOXX"], .48)

    def test_reduce_only_scale_moves_removed_risk_to_boxx_once(self):
        target, _ = combine_targets(CORE_TARGETS["TQQQ"], CORE_TARGETS["SOXL"], (.5, .5), .4)
        self.assertAlmostEqual(target["TQQQ"], .1)
        self.assertAlmostEqual(target["SOXL"], .1)
        self.assertAlmostEqual(target["BOXX"], .78)
        self.assertAlmostEqual(sum(target.values()), .98)
        for scale in (-1., 1.01, float("nan")):
            with self.assertRaisesRegex(ValueError, "COMBINATION_INVALID"):
                combine_targets({}, {}, (.5, .5), scale)

    def test_ewma_initialization_update_and_next_close_timeline(self):
        f = replace(Fixture(), warmup=3, evaluation=2, initial_nav=1000.)
        panel = {"TQQQ": [.01, -.02, .03, .5, -.1], "SOXL": [-.02, .03, .01, -.2, .2],
                 "BOXX": [.001]*5}
        rows, decisions = comparison_sessions(panel, ROWS[3], f)
        ref = [.25*a+.25*b+.48*c for a, b, c in zip(panel["TQQQ"], panel["SOXL"], panel["BOXX"])]
        v = variance(ref[:3])
        self.assertAlmostEqual(decisions[0]["variance"], v)
        self.assertAlmostEqual(decisions[1]["variance"], .94*v+.06*ref[3]**2)
        self.assertEqual(decisions[0]["known_through_index"], 2)
        self.assertLess(rows[0].decision_session, rows[0].session)
        self.assertEqual(rows[1].decision_session, rows[0].session)
        result = run_candidate(panel, ROWS[3], 0., f)
        # Fill-session jump cannot be earned by the newly bought targets.
        self.assertEqual(result["netting"]["ledger"][0]["gross_return"], 0.)

    def test_hand_two_fills_single_fee_and_boxx_pnl(self):
        f = replace(Fixture(), warmup=3, evaluation=2, initial_nav=1000.)
        panel = {"TQQQ": [.01, -.02, .03, .5, .1], "SOXL": [.01, -.02, .03, -.2, 0.],
                 "BOXX": [.001, .001, .001, .3, .01]}
        result = run_candidate(panel, ROWS[0], 100., f)
        first, second = result["netting"]["ledger"]
        self.assertAlmostEqual(first["fees"], 9.8)  # (500+480)*.01
        self.assertAlmostEqual(first["nav_end"], 990.2)
        self.assertAlmostEqual(first["virtual_cash_end"]["inner_residual_cash"], 10.2)
        self.assertAlmostEqual(second["security_pnl"]["TQQQ"], 50.)
        self.assertAlmostEqual(second["security_pnl"]["BOXX"], 4.8)
        self.assertEqual(second["outer_cash_pnl"], 0.)
        self.assertAlmostEqual(second["nav_pre_trade"], 1045.)
        self.assertAlmostEqual(second["net_trades"]["TQQQ"], -27.5)
        self.assertAlmostEqual(second["net_trades"]["BOXX"], 16.8)
        self.assertAlmostEqual(second["fees"], .443)  # fee on net trades, once
        self.assertAlmostEqual(second["nav_end"], 1044.557)
        self.assertAlmostEqual(sum(second["virtual_cash_end"].values()), 20.457)
        for row in (first, second):
            self.assertAlmostEqual(sum(row["security_end"].values())+sum(row["virtual_cash_end"].values()), row["nav_end"])
            self.assertAlmostEqual(row["nav_pre_trade"]-row["fees"], row["nav_end"])
            self.assertTrue(all(v >= 0 for v in row["virtual_cash_end"].values()))
        # Pre-fee target cap is not a false claim about post-fee actual weights.
        self.assertGreater(result["summary"]["max_end_nominal_risk"], .5)
        # Dual-core hand case: one joint cash book, never average two NAVs.
        panel["SOXL"][-1] = -.02
        dual = run_candidate(panel, ROWS[2], 100., f)["netting"]["ledger"]
        self.assertAlmostEqual(dual[0]["security_end"]["TQQQ"], 250.)
        self.assertAlmostEqual(dual[0]["security_end"]["SOXL"], 250.)
        self.assertAlmostEqual(dual[0]["security_end"]["BOXX"], 480.)
        self.assertAlmostEqual(dual[0]["fees"], 9.8)
        self.assertAlmostEqual(dual[1]["nav_pre_trade"], 1015.)
        self.assertAlmostEqual(dual[1]["net_trades"]["TQQQ"], -21.25)
        self.assertAlmostEqual(dual[1]["net_trades"]["SOXL"], 8.75)
        self.assertAlmostEqual(dual[1]["net_trades"]["BOXX"], 2.4)
        self.assertAlmostEqual(dual[1]["fees"], .324)
        self.assertAlmostEqual(dual[1]["nav_end"], 1014.676)
        self.assertAlmostEqual(sum(dual[1]["virtual_cash_end"].values()), 19.976)

    def test_insufficient_cash_rejects_without_resizing_or_borrowing(self):
        panel = synthetic_panel("low_correlation")["returns"]
        with self.assertRaisesRegex(ValueError, "FEE_CASH_INSUFFICIENT"):
            run_candidate(panel, ROWS[2], 10000.)

    def test_future_perturbation_preserves_decisions_and_net_prefix(self):
        panel = synthetic_panel("vol_gap_recovery")["returns"]
        future = {s: list(v) for s, v in panel.items()}
        cut = 150
        future["TQQQ"][cut:] = [.30]*(len(panel["TQQQ"])-cut)
        future["SOXL"][cut:] = [-.20]*(len(panel["SOXL"])-cut)
        before = run_candidate(panel, ROWS[3], 25.)
        after = run_candidate(future, ROWS[3], 25.)
        k = cut-Fixture().warmup
        self.assertEqual(before["decisions"][:k+1], after["decisions"][:k+1])
        self.assertEqual(before["netting"]["ledger"][:k], after["netting"]["ledger"][:k])
        self.assertNotEqual(before["decisions"][k+1]["variance"], after["decisions"][k+1]["variance"])

    def test_all_target_budgets_and_seeded_book_conservation(self):
        panel = synthetic_panel("downturn_correlation_jump")["returns"]
        for row_id in ROWS:
            with self.subTest(row_id=row_id):
                result = run_candidate(panel, row_id, 25.)
                for d in result["decisions"]:
                    self.assertLessEqual(d["target_nominal_risk"], .5+1e-12)
                    self.assertLessEqual(d["target_effective_risk"], 1.5+1e-12)
                    self.assertLessEqual(d["scale"], 1.)
                    self.assertEqual(budget_target(d["target"])["status"], "APPROVE")
                for row in result["netting"]["ledger"]:
                    self.assertLess(max(abs(v) for v in row["reconciliation_errors"].values()), 1e-8)
                    self.assertTrue(all(v >= 0 for v in row["virtual_cash_end"].values()))
                    self.assertAlmostEqual(row["fees"], sum(row["fee_by_security"].values()))
                self.assertAlmostEqual(Fixture().initial_nav*math.prod(1+r for r in result["netting"]["net_returns"]),
                                       result["summary"]["final_nav"])

    def test_malformed_alignment_rejected(self):
        panel = synthetic_panel("low_correlation")["returns"]
        panel["BOXX"] = panel["BOXX"][:-1]
        with self.assertRaisesRegex(ValueError, "PANEL_ALIGNMENT"):
            comparison_sessions(panel, ROWS[0])


if __name__ == "__main__":
    unittest.main()
