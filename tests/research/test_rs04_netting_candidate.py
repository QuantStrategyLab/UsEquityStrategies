"""Synthetic cash-funded examples; execute the installed QPK netter, without mocks."""
from dataclasses import replace
from datetime import date
import math
import os
import unittest

try:
    from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec
except ModuleNotFoundError:
    if os.environ.get("UES_REQUIRE_SECURITY_NETTING") == "1":
        raise
    SecurityNettingSpec = None  # production pin has no research netter
from us_equity_strategies.research.kelly_core_return_generator import CorePath, _replay
from us_equity_strategies.research.rs04_netting_candidate import (
    NEXT_CLOSE_RESEARCH_CANDIDATE, CloseTarget, InitialNettingBook, OuterExposure,
    build_rs04_netting_sessions, run_rs04_netting_candidate,
)

# Friday -> Tuesday crosses a weekend and the Monday MLK exchange holiday.
CALENDAR = (date(2026, 1, 16), date(2026, 1, 20), date(2026, 1, 21), date(2026, 1, 22))
TARGETS = ({"SOXL": .6, "BOXX": .2}, {"SOXL": .2, "BOXX": .5},
           {"SOXL": .4, "BOXX": .3})


def synthetic_spec(a=0., boxx=0.):
    return SecurityNettingSpec(bps_per_side={"SOXL": a, "BOXX": boxx},
        outer_cash_mode="merge_into_security", outer_cash_symbol="BOXX", cash_like_symbols=("BOXX",))


def generated_example():
    # Use the actual core replay on synthetic returns; never call a strategy or private runner.
    full = _replay("SOXL", CALENDAR,
        {"SOXL": (0., 1., .1, -.2), "BOXX": (0., .1, .02, .04)},
        ("SOXL", "BOXX"), lambda i, held: TARGETS[i], None)
    # CorePath.window rejects an empty earning book; this explicit synthetic slice
    # preserves the genuine initial cash observation, rather than pre-holding for free.
    path = replace(full, sessions=full.sessions[1:], gross_returns=full.gross_returns[1:],
        weights=full.weights[1:], turnover=full.turnover[1:],
        close_fill_turnover=full.close_fill_turnover[1:],
        asset_returns={s: r[1:] for s, r in full.asset_returns.items()})
    return dict(core_path=path, trading_sessions=CALENDAR,
        initial_book=InitialNettingBook(CALENDAR[0], 100., 1., {}, {}, 100.),
        last_close_target=CloseTarget(CALENDAR[-2], CALENDAR[-1], TARGETS[-1]),
        outer_exposures=tuple(OuterExposure(a, b, 1.) for a, b in zip(CALENDAR, CALENDAR[1:])),
        synthetic_fee_spec=synthetic_spec(), fee_cash_role="inner_residual_cash",
        execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE)


def opposing_example():
    days = CALENDAR[:3]
    path = CorePath("SOXL", "no_plugin_core", days[1:], (0., 0.), (0., 0.),
        ({"SOXL": .5, "BOXX": .125}, {"SOXL": .8, "BOXX": .1}),
        {"SOXL": (0., 0.), "BOXX": (0., 0.)}, days[0])
    return dict(core_path=path, trading_sessions=days,
        initial_book=InitialNettingBook(days[0], 100., .8,
            {"SOXL": .5, "BOXX": .125}, {"SOXL": 40., "BOXX": 30.}, 30.),
        last_close_target=CloseTarget(days[-2], days[-1], {"SOXL": .8, "BOXX": .1}),
        outer_exposures=tuple(OuterExposure(a, b, .5) for a, b in zip(days, days[1:])),
        synthetic_fee_spec=synthetic_spec(100., 20.), fee_cash_role="inner_residual_cash",
        execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE)


def boxx_example():
    args = opposing_example()
    w = {"SOXL": .8, "BOXX": .1}
    args["core_path"] = replace(args["core_path"], weights=(w, w), gross_returns=(.01, .02),
        asset_returns={"SOXL": (0., 0.), "BOXX": (.1, .2)})
    args["initial_book"] = InitialNettingBook(CALENDAR[0], 100., .5, w,
        {"SOXL": 40., "BOXX": 55.}, 5.)
    args["synthetic_fee_spec"] = synthetic_spec()
    return args


@unittest.skipIf(SecurityNettingSpec is None, "requires the explicit QPK research overlay")
class RS04NettingCandidateTests(unittest.TestCase):
    def assert_book(self, result):
        book = result["netting"]
        nav = book["initial_nav"]
        for row in book["ledger"]:
            self.assertAlmostEqual(row["nav_start"], nav)
            self.assertAlmostEqual(row["nav_pre_trade"], nav + sum(row["security_pnl"].values()))
            self.assertEqual(row["outer_cash_pnl"], 0.)
            self.assertAlmostEqual(row["nav_end"], row["nav_pre_trade"] - row["fees"])
            self.assertAlmostEqual(row["fees"], sum(row["fee_by_security"].values()))
            self.assertAlmostEqual(row["nav_end"], sum(row["security_end"].values())
                                   + sum(row["virtual_cash_end"].values()))
            cash_pre = row["nav_pre_trade"] - sum(row["security_pre"].values())
            self.assertAlmostEqual(cash_pre - sum(row["net_trades"].values()) - row["fees"],
                                   sum(row["virtual_cash_end"].values()))
            for s in set(row["security_pre"]) | set(row["security_end"]):
                self.assertAlmostEqual(row["security_pre"].get(s, 0.) + row["net_trades"].get(s, 0.),
                                       row["security_end"].get(s, 0.))
            self.assertTrue(all(v >= 0. for v in row["virtual_cash_end"].values()))
            self.assertLess(max(abs(v) for v in row["reconciliation_errors"].values()), 1e-10)
            nav = row["nav_end"]
        self.assertAlmostEqual(book["initial_nav"] * math.prod(1. + r for r in book["net_returns"]), nav)

    def test_generated_core_zero_fee_full_exposure_and_hand_timeline(self):
        args = generated_example()
        result = run_rs04_netting_candidate(**args)
        rows = result["netting"]["ledger"]
        # Jan16 decision -> Jan20 close fill -> Jan21 earning: no same-day gain on Jan20's 100% jump.
        for row, expected in zip(rows, (100., 106.4, 104.272), strict=True):
            self.assertAlmostEqual(row["nav_end"], expected)
        for got, expected in zip(result["netting"]["net_returns"], args["core_path"].gross_returns, strict=True):
            self.assertAlmostEqual(got, expected)
        for row, expected in zip(rows, (80., 77.52, 48.7312), strict=True):
            self.assertAlmostEqual(sum(abs(t) for t in row["net_trades"].values()), expected)
        for row, turnover in zip(rows, args["core_path"].close_fill_turnover, strict=True):
            self.assertAlmostEqual(row["net_security_turnover"], turnover)
        self.assertEqual(rows[0]["decision_session"], "2026-01-16")
        self.assertEqual(rows[0]["inner_target"], TARGETS[0])
        self.assertEqual(rows[1]["inner_target"], TARGETS[1])
        self.assertEqual(rows[-1]["inner_target"], TARGETS[-1])
        self.assertAlmostEqual(rows[-1]["security_end"]["SOXL"], 41.7088)
        self.assertAlmostEqual(rows[-1]["security_end"]["BOXX"], 31.2816)
        self.assertAlmostEqual(rows[-1]["virtual_cash_end"]["inner_residual_cash"], 31.2816)
        self.assertEqual(result["timeline"][0]["subsequent_earning_session"], "2026-01-21")
        self.assertIsNone(result["timeline"][-1]["subsequent_earning_session"])
        self.assertFalse(result["research_frozen"])
        self.assertFalse(result["live_ready"])
        self.assertTrue(result["no_order"])
        self.assert_book(result)

    def test_opposite_inner_outer_trades_and_fee_once(self):
        result = run_rs04_netting_candidate(**opposing_example())
        first, last = result["netting"]["ledger"]
        # Inner buys $24 SOXL and outer sells $24; only BOXX +$25 remains.
        self.assertAlmostEqual(first["net_trades"]["SOXL"], 0.)
        self.assertAlmostEqual(first["net_trades"]["BOXX"], 25.)
        self.assertAlmostEqual(first["inner_unnetted_turnover"], .26)
        self.assertAlmostEqual(first["outer_unnetted_turnover"], .57)
        self.assertAlmostEqual(first["net_security_turnover"], .25)
        self.assertAlmostEqual(first["fees"], .05)
        self.assertAlmostEqual(first["virtual_cash_end"]["inner_residual_cash"], 4.95)
        self.assertAlmostEqual(first["nav_end"], 99.95)
        # Actual depleted cash feeds the next day; even at zero returns the fill is nonzero.
        self.assertAlmostEqual(last["net_trades"]["SOXL"], -.02)
        self.assertAlmostEqual(last["net_trades"]["BOXX"], -.0275)
        self.assertAlmostEqual(last["fees"], .000255)
        self.assertAlmostEqual(last["nav_end"], 99.949745)
        self.assert_book(result)

    def test_merged_boxx_return_is_earned_once(self):
        result = run_rs04_netting_candidate(**boxx_example())
        first, last = result["netting"]["ledger"]
        self.assertAlmostEqual(first["security_pnl"]["BOXX"], 5.5)
        self.assertAlmostEqual(first["nav_end"], 105.5)
        self.assertAlmostEqual(last["security_pnl"]["BOXX"], 11.605)
        self.assertAlmostEqual(last["nav_end"], 117.105)
        self.assert_book(result)

    def test_future_return_and_terminal_target_do_not_change_earlier_rows(self):
        args = generated_example()
        earlier = run_rs04_netting_candidate(**args)
        p = args["core_path"]
        args["core_path"] = replace(p, gross_returns=(*p.gross_returns[:-1], .2 * .9 + .5 * .4),
            asset_returns={"SOXL": (*p.asset_returns["SOXL"][:-1], .9),
                           "BOXX": (*p.asset_returns["BOXX"][:-1], .4)})
        args["last_close_target"] = replace(args["last_close_target"], weights={"BOXX": .2})
        later = run_rs04_netting_candidate(**args)
        self.assertEqual(earlier["netting"]["ledger"][:-1], later["netting"]["ledger"][:-1])
        self.assertNotEqual(earlier["netting"]["final_nav"], later["netting"]["final_nav"])

    def test_future_core_observation_keeps_prior_decisions_and_ledger(self):
        outputs = []
        for future in (-.2, .9):
            returns = {"SOXL": (0., 1., .1, future), "BOXX": (0., .1, .02, .04)}
            # The synthetic signal reads only the current completed observation.
            def decide(i, held):
                return TARGETS[i] if returns["SOXL"][i] >= 0. else {"BOXX": .6}
            full = _replay("SOXL", CALENDAR, returns, ("SOXL", "BOXX"), decide, None)
            args = generated_example()
            args["core_path"] = replace(full, sessions=full.sessions[1:], weights=full.weights[1:],
                gross_returns=full.gross_returns[1:], turnover=full.turnover[1:],
                close_fill_turnover=full.close_fill_turnover[1:],
                asset_returns={s: r[1:] for s, r in full.asset_returns.items()})
            outputs.append(run_rs04_netting_candidate(**args))
        self.assertEqual(outputs[0]["netting"]["ledger"][:-1], outputs[1]["netting"]["ledger"][:-1])
        self.assertEqual(outputs[0]["netting"]["ledger"][-1]["inner_target"],
                         outputs[1]["netting"]["ledger"][-1]["inner_target"])

    def test_builds_native_sessions_and_does_not_consume_legacy_turnover(self):
        args = generated_example()
        build_args = {k: v for k, v in args.items() if k != "fee_cash_role"}
        rows = build_rs04_netting_sessions(**build_args)
        from us_equity_strategies.research.security_netting_runner import NettingSession
        self.assertTrue(all(type(row) is NettingSession for row in rows))
        baseline = run_rs04_netting_candidate(**args)
        args["core_path"] = replace(args["core_path"], turnover=(999., 999., 999.),
                                     close_fill_turnover=(888., 888., 888.))
        self.assertEqual(baseline, run_rs04_netting_candidate(**args))

    def test_missing_first_last_and_convention_fail(self):
        for key in ("initial_book", "last_close_target", "execution_convention"):
            with self.subTest(key=key):
                args = generated_example(); del args[key]
                with self.assertRaises(TypeError):
                    run_rs04_netting_candidate(**args)
        for key, code in (("initial_book", "INITIAL_BOOK_REQUIRED"),
                          ("last_close_target", "LAST_CLOSE_TARGET_REQUIRED"),
                          ("execution_convention", "EXPLICIT_RESEARCH_CONVENTION_REQUIRED")):
            with self.subTest(key=key):
                args = generated_example(); args[key] = None
                with self.assertRaisesRegex(ValueError, code):
                    run_rs04_netting_candidate(**args)

    def test_rejects_implicit_or_legacy_timing(self):
        for convention in ("next_close", "legacy", "same_close", "next_open"):
            args = generated_example(); args["execution_convention"] = convention
            with self.subTest(convention=convention), self.assertRaisesRegex(ValueError, "EXPLICIT_RESEARCH_CONVENTION_REQUIRED"):
                run_rs04_netting_candidate(**args)

    def test_explicit_calendar_and_prior_decisions_are_required(self):
        cases = [
            ("trading_sessions", CALENDAR[1:], "TRADING_SESSION_ALIGNMENT"),
            ("trading_sessions", (CALENDAR[0], CALENDAR[1], CALENDAR[1], CALENDAR[3]), "TRADING_SESSION_ORDER"),
            ("outer_exposures", (), "OUTER_EXPOSURES_REQUIRED"),
            ("outer_exposures", (OuterExposure(CALENDAR[1], CALENDAR[1], 1.),)
             + generated_example()["outer_exposures"][1:], "OUTER_EXPOSURE_TIMELINE"),
            ("last_close_target", CloseTarget(CALENDAR[-1], CALENDAR[-1], TARGETS[-1]), "LAST_CLOSE_TARGET_TIMELINE"),
        ]
        for key, value, code in cases:
            with self.subTest(code=code):
                args = generated_example(); args[key] = value
                with self.assertRaisesRegex(ValueError, code):
                    run_rs04_netting_candidate(**args)

    def test_initial_book_must_match_actual_securities_and_earning_weights(self):
        for book in (replace(generated_example()["initial_book"], cash=99.),
                     replace(generated_example()["initial_book"], inner_weights={"SOXL": .1}),
                     replace(generated_example()["initial_book"], security_values={"SOXL": 100.}, cash=0.)):
            args = generated_example(); args["initial_book"] = book
            with self.subTest(book=book), self.assertRaisesRegex(ValueError, "INITIAL_BOOK"):
                run_rs04_netting_candidate(**args)

    def test_missing_returns_or_unproven_core_path_fail(self):
        args = generated_example(); p = args["core_path"]
        for path, code in (
            (replace(p, asset_returns={"SOXL": p.asset_returns["SOXL"]}), "SECURITY_RETURN_COVERAGE"),
            (replace(p, gross_returns=(0., .9, -.02)), "CORE_GROSS_RECONCILIATION"),
            (replace(p, first_signal=None), "CORE_DECISION_HISTORY_REQUIRED"),
            (replace(p, weights=({}, {"SOXL": float("nan")}, TARGETS[1])), "INNER_WEIGHTS_INVALID"),
        ):
            with self.subTest(code=code):
                args["core_path"] = path
                with self.assertRaisesRegex(ValueError, code):
                    run_rs04_netting_candidate(**args)

    def test_merge_mode_and_declared_fee_coverage_are_required(self):
        for spec in (SecurityNettingSpec(bps_per_side={"SOXL": 0., "BOXX": 0.}),
                     SecurityNettingSpec(bps_per_side={"SOXL": 0.}, outer_cash_mode="merge_into_security",
                                         outer_cash_symbol="BOXX", cash_like_symbols=("BOXX",))):
            args = generated_example(); args["synthetic_fee_spec"] = spec
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                run_rs04_netting_candidate(**args)

    def test_insufficient_cash_is_rejected_without_liquidation_or_borrowing(self):
        args = generated_example()
        args["synthetic_fee_spec"] = synthetic_spec(100., 20.)
        args["last_close_target"] = replace(args["last_close_target"], weights={"SOXL": .5, "BOXX": .5})
        with self.assertRaisesRegex(ValueError, "FEE_CASH_INSUFFICIENT"):
            run_rs04_netting_candidate(**args)


if __name__ == "__main__":
    unittest.main()
