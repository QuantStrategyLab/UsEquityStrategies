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
from us_equity_strategies.research.cvar_exposure_constraint import weighted_cvar
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


@unittest.skipIf(SecurityNettingSpec is None, "requires the explicit QPK research overlay")
class B4TwoCoreConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from datetime import timedelta
        import random
        from us_equity_strategies.research.kelly_core_return_generator import (
            RawBar, generate_soxl_core_path, generate_tqqq_core_path,
        )
        days, day = [], date(2001, 1, 2)
        while len(days) < 420:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)

        def walk(seed, drift, vol):
            rng, price, bars = random.Random(seed), 100., []
            for d in days:
                price *= math.exp(rng.gauss(drift, vol))
                bars.append(RawBar(d, price, price))
            return bars

        def leveraged(bars):
            return [RawBar(b.session, b.close ** 3 / 1e4, b.close ** 3 / 1e4) for b in bars]

        qqq, soxx, boxx = walk(21, .002, .006), walk(22, .002, .006), walk(23, .0001, .0001)
        cls.paths = {
            "TQQQ": generate_tqqq_core_path(
                {"QQQ": qqq, "TQQQ": leveraged(qqq), "QQQM": qqq, "BOXX": boxx},
                {}, days, trade_start=days[400]),
            "SOXL": generate_soxl_core_path(
                {"SOXX": soxx, "SOXL": leveraged(soxx), "BOXX": boxx},
                {}, days, trade_start=days[400]),
        }
        cls.days = tuple(days)

    def args(self):
        from datetime import datetime, time, timezone, timedelta
        from us_equity_strategies.research.bayesian_kelly import B4Spec
        calendar = self.days[403:410]
        closes = {d: datetime.combine(d, time(21), timezone.utc) for d in self.days}
        books, terminal, specs = {}, {}, {}
        for key, path in self.paths.items():
            i = path.sessions.index(calendar[1])
            j = path.sessions.index(calendar[-1])
            books[key] = InitialNettingBook(calendar[0], 100., 0., path.weights[i], {"BOXX": 100.}, 0.)
            terminal[key] = CloseTarget(calendar[-2], calendar[-1], path.weights[j + 1])
            specs[key] = SecurityNettingSpec(
                bps_per_side={s: (2. if s == "BOXX" else 10.) for s in path.asset_returns},
                outer_cash_mode="merge_into_security", outer_cash_symbol="BOXX", cash_like_symbols=("BOXX",))
        return dict(core_paths=self.paths, initial_books=books, last_close_targets=terminal,
            trading_sessions=calendar, observation_closes=closes,
            observation_available_at={k: dict(closes) for k in self.paths},
            decision_cutoffs={d: closes[d] + timedelta(minutes=1) for d in calendar[:-1]},
            model_spec=B4Spec(("TQQQ", "SOXL"), closes[calendar[0]] - timedelta(seconds=1)),
            sigma=((.04, .01), (.01, .05)), training_identity="synthetic-training-only",
            scope_caps={"TQQQ": .6, "SOXL": .6}, cap_source="synthetic-original-target-cap",
            vetoes={d: {"TQQQ": False, "SOXL": False} for d in calendar[:-1]},
            control_outer_exposures={k: tuple(OuterExposure(a, b, .25)
                for a, b in zip(calendar, calendar[1:])) for k in self.paths},
            synthetic_fee_specs=specs, fee_cash_role="inner_residual_cash",
            execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE)

    def run_candidate(self, args):
        from us_equity_strategies.research import rs04_netting_candidate as candidate
        self.assertTrue(hasattr(candidate, "run_rs04_bayesian_candidate"), "B4_CONSUMER_NOT_IMPLEMENTED")
        return candidate.run_rs04_bayesian_candidate(**args)

    def test_actual_generators_feed_two_independent_native_books(self):
        args = self.args()
        result = self.run_candidate(args)
        self.assertEqual(result["status"], "DEVELOPMENT_ONLY")
        self.assertEqual(set(result["strategies"]), {"TQQQ", "SOXL"})
        self.assertTrue(result["no_order"])
        self.assertFalse(result["live_ready"])
        self.assertEqual(result["budget_effect"], "NONE")
        for key, book in result["strategies"].items():
            self.assertEqual(len(book["netting"]["ledger"]), 6)
            self.assertEqual(book["initial_book"]["nav"], 100.)
            self.assertEqual(book["netting"]["ledger"][0]["decision_session"], args["trading_sessions"][0].isoformat())
            self.assertEqual(book["netting"]["ledger"][-1]["inner_target"], args["last_close_targets"][key].weights)
            RS04NettingCandidateTests.assert_book(self, book)

    def test_independent_lognormal_oracle_fractional_is_consumed_once(self):
        args = self.args(); result = self.run_candidate(args)
        for index, trace in enumerate(result["decisions"], 1):
            self.assertEqual(trace["n"], index)
            self.assertTrue(trace["fractional_applied_once"])
            self.assertEqual(trace["c"], .25)
            for k, key in enumerate(("TQQQ", "SOXL")):
                logs = [math.log1p(args["core_paths"][key].gross_returns[
                    args["core_paths"][key].sessions.index(d)]) for d in args["trading_sessions"][:index]]
                variance = args["sigma"][k][k]
                m = (-variance + math.fsum(logs)) / (1 + index)
                p = variance * (1 + 1 / (1 + index))
                mean = math.expm1(m + p / 2)
                second = math.exp(2 * m + 2 * p) - 2 * math.exp(m + p / 2) + 1
                expected = min(.6, .25 * max(0., mean) / second)
                self.assertAlmostEqual(trace["targets"][key], expected, places=12)
                row = result["strategies"][key]["netting"]["ledger"][index - 1]
                self.assertAlmostEqual(row["f_target"], expected, places=12)
                if expected > 0.:
                    self.assertNotAlmostEqual(row["f_target"], expected * .25, places=10)

    def test_original_netter_is_exact_and_fees_feed_the_next_book(self):
        args = self.args(); result = self.run_candidate(args)
        for key, report in result["strategies"].items():
            path = args["core_paths"][key]
            lo, hi = path.sessions.index(args["trading_sessions"][1]), path.sessions.index(args["trading_sessions"][-1]) + 1
            window = replace(path, sessions=path.sessions[lo:hi], gross_returns=path.gross_returns[lo:hi],
                turnover=path.turnover[lo:hi], weights=path.weights[lo:hi],
                close_fill_turnover=path.close_fill_turnover[lo:hi],
                asset_returns={s: r[lo:hi] for s, r in path.asset_returns.items()})
            exposures = tuple(OuterExposure(a, b, row["targets"][key])
                for a, b, row in zip(args["trading_sessions"], args["trading_sessions"][1:], result["decisions"]))
            control = run_rs04_netting_candidate(core_path=window, initial_book=args["initial_books"][key],
                last_close_target=args["last_close_targets"][key], trading_sessions=args["trading_sessions"],
                outer_exposures=exposures, synthetic_fee_spec=args["synthetic_fee_specs"][key],
                fee_cash_role=args["fee_cash_role"], execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE)
            self.assertEqual(report, control)
            rows = report["netting"]["ledger"]
            self.assertGreater(sum(r["fees"] for r in rows), 0.)
            for before, after in zip(rows, rows[1:]):
                self.assertEqual(before["nav_end"], after["nav_start"])

    def test_future_fill_returns_do_not_enter_previous_decision(self):
        args = self.args(); a = self.run_candidate(args)
        paths = {}
        for key, path in args["core_paths"].items():
            at = path.sessions.index(args["trading_sessions"][3])
            returns = {s: tuple(v + .02 if j >= at and s != "BOXX" else v for j, v in enumerate(series))
                       for s, series in path.asset_returns.items()}
            gross = tuple(math.fsum(w * returns[s][i] for s, w in weights.items())
                          for i, weights in enumerate(path.weights))
            paths[key] = replace(path, asset_returns=returns, gross_returns=gross)
        args["core_paths"] = paths; b = self.run_candidate(args)
        self.assertEqual(a["decisions"][:3], b["decisions"][:3])
        for key in paths:
            self.assertEqual(a["strategies"][key]["netting"]["ledger"][:2], b["strategies"][key]["netting"]["ledger"][:2])
        self.assertNotEqual(a["decisions"][3:], b["decisions"][3:])

    def test_prior_only_is_parked_without_a_model_target(self):
        args = self.args()
        args["model_spec"] = replace(args["model_spec"], training_cutoff=args["observation_closes"][args["trading_sessions"][0]])
        result = self.run_candidate(args); first = result["decisions"][0]
        self.assertEqual(first["n"], 0)
        self.assertEqual(first["status"], "PRIOR_ONLY_PARKED")
        self.assertIsNone(first["targets"])
        self.assertFalse(first["fractional_applied_once"])
        for report in result["strategies"].values():
            self.assertEqual(report["netting"]["ledger"][0]["f_target"], 0.)

    def test_missing_cap_identity_timing_or_veto_is_unavailable_not_default(self):
        for field in ("scope_caps", "cap_source", "training_identity", "observation_available_at", "vetoes"):
            with self.subTest(field=field):
                args = self.args(); args[field] = None
                result = self.run_candidate(args)
                self.assertTrue(result["status"].startswith("UNAVAILABLE_"))
                self.assertIsNone(result["strategies"])

    def test_late_vector_is_not_zero_filled_or_inner_joined(self):
        from datetime import timedelta
        args = self.args(); times = dict(args["observation_available_at"]["SOXL"])
        times[args["trading_sessions"][0]] += timedelta(days=7)
        args["observation_available_at"]["SOXL"] = times
        result = self.run_candidate(args)
        self.assertEqual(result["status"], "UNAVAILABLE_OBSERVATION_AT_CUTOFF")
        self.assertIsNone(result["strategies"])

    def test_cap_and_veto_are_final_and_not_rescaled(self):
        args = self.args(); args["scope_caps"] = {"TQQQ": .001, "SOXL": .002}
        # Isolate target admission from fee funding: a zero-outer exit cannot
        # invent residual cash to pay fees in the unchanged merged-role netter.
        args["synthetic_fee_specs"] = {k: replace(spec, bps_per_side={s: 0. for s in spec.bps_per_side})
                                      for k, spec in args["synthetic_fee_specs"].items()}
        veto = args["trading_sessions"][1]; args["vetoes"][veto]["SOXL"] = True
        result = self.run_candidate(args)
        for row in result["decisions"]:
            self.assertLessEqual(row["targets"]["TQQQ"], .001)
            self.assertLessEqual(row["targets"]["SOXL"], .002)
        self.assertEqual(result["decisions"][1]["targets"]["SOXL"], 0.)

    def test_veto_exit_cannot_borrow_or_silently_fund_a_fee(self):
        args = self.args()
        args["vetoes"][args["trading_sessions"][1]]["SOXL"] = True
        with self.assertRaisesRegex(ValueError, "FEE_CASH_INSUFFICIENT"):
            self.run_candidate(args)

    def test_prior_only_does_not_liquidate_an_existing_nonzero_outer_book(self):
        args = self.args()
        args["model_spec"] = replace(args["model_spec"], training_cutoff=args["observation_closes"][args["trading_sessions"][0]])
        args["initial_books"]["TQQQ"] = replace(args["initial_books"]["TQQQ"], f=.1)
        result = self.run_candidate(args)
        self.assertEqual(result["status"], "UNAVAILABLE_PRIOR_ONLY_NONZERO_OPENING")
        self.assertIsNone(result["strategies"])

    def test_bad_alignment_covariance_and_cutoff_fail_closed(self):
        args = self.args(); args["core_paths"] = dict(args["core_paths"])
        args["core_paths"]["SOXL"] = replace(args["core_paths"]["SOXL"], sessions=args["core_paths"]["SOXL"].sessions[:-1])
        with self.assertRaisesRegex(ValueError, "B4_REFERENCE_ALIGNMENT"):
            self.run_candidate(args)
        args = self.args(); args["sigma"] = ((0., 0.), (0., .05))
        with self.assertRaises(ValueError): self.run_candidate(args)
        args = self.args(); d = args["trading_sessions"][0]
        args["decision_cutoffs"][d] = args["observation_closes"][args["trading_sessions"][1]]
        with self.assertRaisesRegex(ValueError, "B4_DECISION_TIMELINE"):
            self.run_candidate(args)

    def test_reference_history_cannot_be_warmup_or_partial(self):
        args = self.args(); args["core_paths"] = dict(args["core_paths"])
        args["core_paths"]["SOXL"] = replace(args["core_paths"]["SOXL"], first_signal=None)
        result = self.run_candidate(args)
        self.assertEqual(result["status"], "UNAVAILABLE_CORE_HISTORY")
        self.assertIsNone(result["strategies"])


if __name__ == "__main__":
    unittest.main()


def b5_rs04_policy(args, mode='FULL'):
    """Independent fixture composition, not the implementation's compose helper."""
    from us_equity_strategies.research.security_netting_runner import (
        DecisionSnapshot, RS04DecisionInput, RS04ResearchPolicy,
    )
    from us_equity_strategies.research.partial_rebalance import HardPolicyContext, SecurityPolicy
    universe = tuple(sorted(args['synthetic_fee_spec'].bps_per_side))
    risk = tuple(s for s in universe if s != 'BOXX')
    context = HardPolicyContext(tuple(SecurityPolicy(s, s in risk, 1., s, 1.) for s in universe),
        1., tuple((s, 1.) for s in risk), (), 'synthetic-existing-policy', 'synthetic-classification',
        True, 'DECISION_TIME_TARGET', None, 'RISK_BUY_WEIGHT', 'NAV_FRACTION')
    original = args['initial_book']
    opening = DecisionSnapshot(original.session, original.session.isoformat()+'T21:00:00+00:00',
        original.nav, tuple((s, original.security_values.get(s, 0.)) for s in universe), original.cash, True)
    targets = (*args['core_path'].weights[1:], args['last_close_target'].weights)
    inputs = []
    for exposure, inner in zip(args['outer_exposures'], targets, strict=True):
        target = {s: exposure.f_target*inner.get(s, 0.) + ((1.-exposure.f_target) if s == 'BOXX' else 0.) for s in universe}
        inputs.append(RS04DecisionInput(exposure.decision_session, exposure.fill_session,
            exposure.decision_session.isoformat()+'T22:00:00+00:00',
            exposure.fill_session.isoformat()+'T21:00:00+00:00',
            exposure.decision_session.isoformat()+'T21:00:00+00:00',
            'synthetic-core-outer-frozen-target', tuple(target.items()), context, False))
    return RS04ResearchPolicy(mode, universe, opening, tuple(inputs))


@unittest.skipIf(SecurityNettingSpec is None, "requires the explicit QPK research overlay")
class B5FlatConsumerTests(unittest.TestCase):
    def run_b5(self, args, mode='FULL'):
        from us_equity_strategies.research.rs04_netting_candidate import run_rs04_partial_rebalance_candidate
        return run_rs04_partial_rebalance_candidate(**args,
            research_policy=b5_rs04_policy(args, mode), report_periods_per_year=252)

    def test_layered_flat_physical_identity_first_and_independent_hand_dollars(self):
        for fixture in (generated_example, opposing_example, boxx_example):
            with self.subTest(fixture=fixture.__name__):
                args = fixture()
                old = run_rs04_netting_candidate(**args)['netting']
                new = self.run_b5(args)['netting']
                self.assertEqual(new['spec']['outer_cash_mode'], 'separate')
                self.assertEqual(new['spec']['cash_like_symbols'], ['BOXX'])
                for left, right in zip(old['ledger'], new['ledger'], strict=True):
                    for field in ('security_pre', 'security_end', 'net_trades', 'fee_by_security'):
                        for symbol in set(left[field]) | set(right[field]):
                            self.assertAlmostEqual(left[field].get(symbol, 0.), right[field].get(symbol, 0.), places=11)
                    for field in ('nav_pre_trade', 'fees', 'nav_end', 'gross_return', 'net_return'):
                        self.assertAlmostEqual(left[field], right[field], places=11)
                    self.assertEqual(right['f_target'], 1.)
                self.assertAlmostEqual(old['final_nav'], new['final_nav'], places=11)
                self.assertIn('unified_net_report', new)
        hand = self.run_b5(opposing_example())['netting']['ledger']
        # Opening actual=(40,30,cash30), target=(.4,.55,cash.05).
        # Only BOXX +25 trades, at20bps costs .05; actual cash4.95.
        self.assertAlmostEqual(hand[0]['net_trades']['SOXL'], 0.)
        self.assertAlmostEqual(hand[0]['net_trades']['BOXX'], 25.)
        self.assertAlmostEqual(hand[0]['fees'], .05)
        self.assertAlmostEqual(hand[0]['nav_end'], 99.95)
        self.assertAlmostEqual(hand[1]['net_trades']['SOXL'], -.02)
        self.assertAlmostEqual(hand[1]['net_trades']['BOXX'], -.0275)
        self.assertAlmostEqual(hand[1]['nav_end'], 99.949745)

    def test_opening_f_zero_and_one_converts_actual_cash_and_boxx(self):
        for f in (0., 1.):
            args = opposing_example()
            inner = args['initial_book'].inner_weights
            values = {'SOXL': 100.*f*inner['SOXL'], 'BOXX': 100.*(f*inner['BOXX']+1.-f)}
            args['initial_book'] = replace(args['initial_book'], f=f, security_values=values,
                                          cash=100.*f*(1.-sum(inner.values())))
            old = run_rs04_netting_candidate(**args)['netting']
            new = self.run_b5(args)['netting']
            self.assertEqual(new['ledger'][0]['security_start'], values)
            self.assertAlmostEqual(old['final_nav'], new['final_nav'], places=11)
            self.assertAlmostEqual(sum(new['ledger'][0]['security_start'].values())+args['initial_book'].cash, 100.)

    def test_original_builder_compose_netter_are_used(self):
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as candidate
        from quant_platform_kit.research_stats import security_netting as qpk
        with patch.object(candidate, 'build_rs04_netting_sessions', wraps=candidate.build_rs04_netting_sessions) as build:
            with patch.object(qpk, 'compose_security_targets', wraps=qpk.compose_security_targets) as compose:
                with patch.object(qpk, 'net_security_session', wraps=qpk.net_security_session) as net:
                    result = self.run_b5(opposing_example())
        self.assertEqual(build.call_count, 1)
        self.assertGreaterEqual(compose.call_count, 2)
        self.assertEqual(net.call_count, len(result['netting']['ledger']))

    def test_actual_two_generators_can_feed_b5_without_b4_model_cross(self):
        # Reuse only the synthetic generator fixture; do not run the B4 consumer.
        B4TwoCoreConsumerTests.setUpClass()
        fixture = B4TwoCoreConsumerTests()
        original = fixture.args()
        for key, path in fixture.paths.items():
            calendar = original['trading_sessions']
            lo, hi = path.sessions.index(calendar[1]), path.sessions.index(calendar[-1])+1
            window = replace(path, sessions=path.sessions[lo:hi], weights=path.weights[lo:hi],
                gross_returns=path.gross_returns[lo:hi], turnover=path.turnover[lo:hi],
                close_fill_turnover=path.close_fill_turnover[lo:hi],
                asset_returns={s: r[lo:hi] for s, r in path.asset_returns.items()})
            # f=.5 and a reserved core residual chosen from the existing actual
            # core weights. No target is silently shrunk to create fee cash.
            book = original['initial_books'][key]
            opening = dict(book.inner_weights)
            cash = 50.*(1.-sum(opening.values()))
            values = {s: 50.*opening.get(s, 0.)+ (50. if s == 'BOXX' else 0.) for s in path.asset_returns}
            args = dict(core_path=window, initial_book=replace(book, f=.5, security_values=values, cash=cash),
                last_close_target=original['last_close_targets'][key], trading_sessions=calendar,
                outer_exposures=original['control_outer_exposures'][key],
                synthetic_fee_spec=replace(original['synthetic_fee_specs'][key], bps_per_side={s: 0. for s in path.asset_returns}),
                fee_cash_role='inner_residual_cash', execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE)
            result = self.run_b5(args)
            self.assertEqual(len(result['netting']['ledger']), 6)
            self.assertTrue(result['no_order'])
            self.assertNotIn('checkpoint', result)

    def test_opening_and_frozen_target_cannot_be_replaced_with_defaults(self):
        from us_equity_strategies.research.rs04_netting_candidate import run_rs04_partial_rebalance_candidate
        args = opposing_example()
        policy = b5_rs04_policy(args)
        for bad in (replace(policy, opening=replace(policy.opening, cash=0.)),
                    replace(policy, decisions=(replace(policy.decisions[0], full_target_source=''), *policy.decisions[1:])),
                    replace(policy, decisions=(replace(policy.decisions[0], full_target=(('SOXL', .2), ('BOXX', .55))), *policy.decisions[1:]))):
            with self.assertRaises(ValueError):
                run_rs04_partial_rebalance_candidate(**args, research_policy=bad, report_periods_per_year=252)

    def test_three_modes_are_consumed_by_the_explicit_rs04_entry(self):
        half = self.run_b5(opposing_example(), 'PROPORTIONAL')['netting']
        band = self.run_b5(opposing_example(), 'BAND_HOLD')['netting']
        full = self.run_b5(opposing_example(), 'FULL')['netting']
        self.assertAlmostEqual(half['ledger'][0]['security_end']['SOXL'], 40.)
        self.assertAlmostEqual(half['ledger'][0]['security_end']['BOXX'], 42.5)
        self.assertAlmostEqual(half['ledger'][0]['fees'], .025)
        self.assertAlmostEqual(half['ledger'][0]['nav_end'], 99.975)
        self.assertEqual(band['ledger'][0], full['ledger'][0])
        # After the first .05 fee, FULL restores its old target and pays again.
        # The band correctly holds the actual fee-depleted book on the second fill.
        self.assertTrue(all(v == 0. for v in band['ledger'][1]['net_trades'].values()))
        self.assertEqual(band['ledger'][1]['fees'], 0.)
        self.assertAlmostEqual(band['final_nav'], 99.95)
        self.assertAlmostEqual(full['final_nav'], 99.949745)
        self.assertEqual(half['rs04_research']['mode'], 'PROPORTIONAL')
        self.assertEqual(band['rs04_research']['mode'], 'BAND_HOLD')


@unittest.skipIf(SecurityNettingSpec is None, "requires the explicit QPK research overlay")
class CVaRGeneratedConsumerTests(unittest.TestCase):
    def test_same_equal_weight_sample_matches_existing_b3_signed_return_tail(self):
        from quant_platform_kit.research_stats.unified_report import build_unified_net_report

        # Thirty observations put 1.5 atoms in the tail, exercising its boundary.
        # These identical simple samples cross-check arithmetic, not horizons.
        for returns in [(-.1, -.04, *([.01] * 28)), (.01, .04, *([.1] * 28))]:
            with self.subTest(returns=returns):
                descriptive = build_unified_net_report(returns).metric("cvar95_return_per_period")
                self.assertEqual(descriptive.status, "COMPUTED")
                es = weighted_cvar(tuple(-r for r in returns), (1. / len(returns),) * len(returns), .95)
                self.assertAlmostEqual(es, -descriptive.value, places=14)

    @classmethod
    def setUpClass(cls):
        # Executes both actual no-plugin core generators on synthetic bars.
        B4TwoCoreConsumerTests.setUpClass()

    def generated_args(self, key):
        from test_security_netting_runner import cvar_case
        fixture = B4TwoCoreConsumerTests()
        original = fixture.args()
        path = fixture.paths[key]
        calendar = original['trading_sessions']
        lo, hi = path.sessions.index(calendar[1]), path.sessions.index(calendar[-1])+1
        window = replace(path, sessions=path.sessions[lo:hi], weights=path.weights[lo:hi],
            gross_returns=path.gross_returns[lo:hi], turnover=path.turnover[lo:hi],
            close_fill_turnover=path.close_fill_turnover[lo:hi],
            asset_returns={s: r[lo:hi] for s, r in path.asset_returns.items()})
        book = original['initial_books'][key]
        opening = dict(book.inner_weights)
        values = {s: 50.*opening.get(s, 0.)+(50. if s == 'BOXX' else 0.) for s in path.asset_returns}
        args = dict(core_path=window, initial_book=replace(book, f=.5, security_values=values,
            cash=50.*(1.-sum(opening.values()))), last_close_target=original['last_close_targets'][key],
            trading_sessions=calendar, outer_exposures=original['control_outer_exposures'][key],
            synthetic_fee_spec=replace(original['synthetic_fee_specs'][key],
                bps_per_side={s: 0. for s in path.asset_returns}), fee_cash_role='inner_residual_cash',
            execution_convention=NEXT_CLOSE_RESEARCH_CANDIDATE)
        policy = b5_rs04_policy(args)
        _, template = cvar_case(key, 'QQQM' if key == 'TQQQ' else 'SOXX', budget=.1)
        request = template['rs04_research_policy'].decisions[0].cvar_request
        request = replace(request, original_policy=policy.decisions[0].hard_policy_context,
            cost_spec=replace(request.cost_spec, bps_by_symbol=dict(args['synthetic_fee_spec'].bps_per_side)),
            scenarios=replace(request.scenarios, symbols=policy.security_universe))
        inputs = []
        for info in policy.decisions:
            sources = {'scenarios': request.scenarios.source, 'tail_policy': request.tail_policy.source,
                'cost': 'synthetic-explicit-zero-cost-generator-control',
                'original_policy': info.hard_policy_context.source, 'anchor': request.anchor.source}
            known = tuple((name, source, info.full_target_available_at, info.full_target_available_at)
                          for name, source in sources.items())
            inputs.append(replace(info, cvar_request=replace(request, known_inputs=known)))
        return args, replace(policy, decisions=tuple(inputs), cvar_descriptor=request.scenarios.descriptor)

    def test_tqqq_real_generator_entry_preserves_full_physical_book(self):
        self.check_generated('TQQQ', {'TQQQ', 'QQQM', 'BOXX'})

    def test_soxl_real_generator_entry_preserves_full_physical_book(self):
        self.check_generated('SOXL', {'SOXL', 'SOXX', 'BOXX'})

    def check_generated(self, key, universe):
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as candidate
        from quant_platform_kit.research_stats import security_netting as qpk
        args, policy = self.generated_args(key)
        self.assertEqual(set(args['core_path'].asset_returns), universe)
        # Explicit zero-cost generated control; the separate hand oracle tests
        # bind nonzero risk/BOXX fees and the before-next-fill endpoint.
        expected = run_rs04_netting_candidate(**args)['netting']
        with patch.object(candidate, 'build_rs04_netting_sessions', wraps=candidate.build_rs04_netting_sessions) as build:
            with patch.object(qpk, 'compose_security_targets', wraps=qpk.compose_security_targets) as compose:
                with patch.object(qpk, 'net_security_session', wraps=qpk.net_security_session) as net:
                    result = candidate.run_rs04_cvar_candidate(**args, research_policy=policy, report_periods_per_year=252)
        self.assertEqual(build.call_count, 1)
        self.assertGreaterEqual(compose.call_count, len(expected['ledger']))
        self.assertEqual(net.call_count, len(expected['ledger']))
        actual = result['netting']
        for left, right, receipt in zip(expected['ledger'], actual['ledger'], actual['rs04_research']['decisions'], strict=True):
            self.assertEqual(receipt['cvar']['constraint']['status'], 'PROPOSAL_FEASIBLE')
            self.assertEqual(right['inner_target'], receipt['full_target'])
            for field in ('security_pre', 'security_end', 'net_trades', 'fee_by_security'):
                for symbol in universe:
                    self.assertAlmostEqual(left[field].get(symbol, 0.), right[field].get(symbol, 0.), places=11)
            for field in ('fees', 'nav_end', 'gross_return', 'net_return'):
                self.assertAlmostEqual(left[field], right[field], places=11)
            self.assertEqual(right['outer_cash_pnl'], 0.)
        self.assertEqual(result['candidate'], 'RS04_CVAR_TWO_SESSION_CONSTRAINT')
        self.assertEqual(result['financial'], 'NOT_READY')
        self.assertFalse(result['live_ready'])

    def test_entry_requires_explicit_request_and_missing_budget_is_unavailable(self):
        from us_equity_strategies.research.rs04_netting_candidate import run_rs04_cvar_candidate
        args, policy = self.generated_args('TQQQ')
        with self.assertRaisesRegex(ValueError, 'RS04_CVAR_EXPLICIT_SCOPED_REQUEST_REQUIRED'):
            run_rs04_cvar_candidate(**args, research_policy=replace(policy, cvar_descriptor=None), report_periods_per_year=252)
        first = policy.decisions[0]
        bad = replace(first.cvar_request, tail_policy=replace(first.cvar_request.tail_policy, budget=None))
        with self.assertRaisesRegex(ValueError, 'RS04_CVAR_UNAVAILABLE:missing matching tail budget'):
            run_rs04_cvar_candidate(**args, research_policy=replace(policy,
                decisions=(replace(first, cvar_request=bad), *policy.decisions[1:])), report_periods_per_year=252)
