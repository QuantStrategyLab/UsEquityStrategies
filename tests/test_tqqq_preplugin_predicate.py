"""Exact frozen old-core parity, artificial inputs and no runtime adoption.

Golden full-output digests were generated BEFORE the source refactor from the
original core SHA256 below. Case-generation version1 is the exact deterministic
Cartesian grid in this file; each digest binds all inputs AND every output field.
No old implementation is embedded or executed in permanent tests. Independent
local old-vs-new differential evidence is retained outside the repository.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import socket
import subprocess
import unittest
from dataclasses import asdict, fields
from unittest.mock import patch

from us_equity_strategies.strategies import tqqq_dual_drive_core as core

_ORIGINAL_SOURCE_SHA256 = "97511bcc27c9d9c6023690297e801190b27fd5fb25e1c707c22f79a4f9a8f9b8"
_DECISION_FIELDS = ('state', 'above_ma200', 'slope_ok', 'pullback_risk_on', 'reserved', 'target_tqqq_value', 'target_unlevered_value', 'target_boxx_value', 'macro_applied', 'macro_removed_value', 'macro_redirected_to_unlevered_value', 'crisis_applied', 'crisis_removed_value', 'volatility_triggered', 'volatility_entry_triggered', 'volatility_hysteresis_triggered', 'volatility_trigger_reason', 'volatility_applied', 'volatility_vetoed', 'volatility_veto_reason', 'volatility_source_value', 'volatility_retained_value', 'volatility_removed_value', 'volatility_retained_ratio', 'volatility_redirected_ratio')
_GOLDEN = {
    "market": (57600, 'cbc5069652d63f66c701cfd595955788bc1f2e96881fa3691c2482e7fef81f11'),
    "risk_phases": (60480, 'acfb83dcc2eea999f9c41474399c0d8ec96d6782321d5f021a397627ac866d05'),
    "legacy_quirks": (600, 'a05fbfff30b83e61acf3090b8265dc7a690aa946a0bea908f7b76545736bbdb7'),
}


def _values(**changes):
    value = {
        "qqq_price": 101.0, "ma200": 100.0, "latest_ma20": 100.0,
        "ma20_slope": 1.0, "pullback_rebound": 0.03, "pullback_rebound_threshold": 0.02,
        "current_tqqq_quantity": 0, "current_unlevered_quantity": 0,
        "require_ma20_slope": True, "allow_pullback": True,
        "strategy_equity": 1000.0, "initial_reserved": 20.0, "cash_reserve_floor": 0.0,
        "risk_on_cash_reserve_ratio": 0.02, "tqqq_weight": 0.45, "unlevered_weight": 0.45,
        "macro_active": False, "macro_route": None,
        "macro_leverage_scalar": 1.0, "macro_risk_asset_scalar": 1.0,
        "crisis_defense_enabled": True, "true_crisis_active": False,
        "volatility_enabled": False, "volatility_metric": None,
        "volatility_entry_threshold": 0.28, "volatility_exit_threshold": 0.24,
        "taco_veto_enabled": True, "taco_rebound_context_active": False,
        "retention_mode": None, "retention_ratio": 0.0,
    }
    value.update(changes)
    return value


def _market_cases():
    for price, tqqq, unlevered, ma20, slope, require, pullback, rebound, threshold in itertools.product(
        (99.0, 100.0, 101.0), (-1, 0, 1, False, True), (-1, 0, 1, False, True),
        (None, 99.0, 100.0, 101.0), (None, -1.0, 0.0, 1.0),
        (False, True), (False, True), (None, 0.0, 0.02, 0.03), (-1.0, 0.0, 0.02),
    ):
        yield _values(qqq_price=price, current_tqqq_quantity=tqqq,
                      current_unlevered_quantity=unlevered, latest_ma20=ma20,
                      ma20_slope=slope, require_ma20_slope=require, allow_pullback=pullback,
                      pullback_rebound=rebound, pullback_rebound_threshold=threshold)


def _risk_phase_cases():
    states = (
        {}, {"current_tqqq_quantity": 1, "ma20_slope": -1.0},
        {"current_unlevered_quantity": 1},
        {"current_tqqq_quantity": 1, "current_unlevered_quantity": 1, "qqq_price": 99.0},
        {"qqq_price": 100.0, "latest_ma20": 99.0},
        {"qqq_price": 100.0, "latest_ma20": 99.0, "current_tqqq_quantity": 1},
    )
    macros = (
        {}, {"macro_active": True, "macro_route": None, "macro_leverage_scalar": 0.5},
        {"macro_active": True, "macro_route": "delever", "macro_leverage_scalar": 0.5, "macro_risk_asset_scalar": 0.5},
        {"macro_active": True, "macro_route": "crisis", "macro_leverage_scalar": 0.0, "macro_risk_asset_scalar": 0.0},
        {"macro_active": True, "macro_route": "delever"},
        {"macro_active": True, "macro_route": "delever", "macro_leverage_scalar": -0.5},
        {"macro_active": True, "macro_route": "other", "macro_leverage_scalar": 1.5, "macro_risk_asset_scalar": 1.5},
    )
    for state, macro, crisis_enabled, crisis, volatility, metric, veto, rebound_context, retention, ratio in itertools.product(
        states, macros, (False, True), (False, True), (False, True),
        (None, 0.23, 0.24, 0.28, 0.30), (False, True), (False, True),
        (None, "none", "environment"), (0.0, 0.25, 1.0),
    ):
        yield _values(**(state | macro | {
            "crisis_defense_enabled": crisis_enabled, "true_crisis_active": crisis,
            "volatility_enabled": volatility, "volatility_metric": metric,
            "taco_veto_enabled": veto, "taco_rebound_context_active": rebound_context,
            "retention_mode": retention, "retention_ratio": ratio,
        }))


def _legacy_quirk_cases():
    quirks = (
        {}, {"initial_reserved": False, "cash_reserve_floor": True, "risk_on_cash_reserve_ratio": False},
        {"tqqq_weight": False, "unlevered_weight": False},
        {"tqqq_weight": True, "unlevered_weight": True, "risk_on_cash_reserve_ratio": False},
        {"require_ma20_slope": None, "allow_pullback": None, "ma20_slope": None},
        {"require_ma20_slope": 0, "allow_pullback": 0},
        {"macro_active": True, "macro_leverage_scalar": False, "macro_risk_asset_scalar": True},
        {"volatility_enabled": True, "volatility_metric": True, "retention_ratio": True},
    )
    for tqqq, unlevered, nav, quirk in itertools.product(
        (-1, 0, 1, False, True), (-1, 0, 1, False, True), (0, True, 1000), quirks,
    ):
        yield _values(**({"current_tqqq_quantity": tqqq, "current_unlevered_quantity": unlevered,
                         "strategy_equity": nav} | quirk))


def _digest_cases(module, factory):
    digest = hashlib.sha256()
    count = 0
    for value in factory():
        result = module.decide_tqqq_dual_drive(module.DualDriveCoreInput(**value))
        digest.update(json.dumps({"input": value, "decision": asdict(result)}, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False).encode() + b"\n")
        count += 1
    return count, digest.hexdigest()


class TqqqPrepluginPredicateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = patch.object(socket, "socket", side_effect=AssertionError("NETWORK_FORBIDDEN"))
        cls.process = patch.object(subprocess, "Popen", side_effect=AssertionError("PROCESS_FORBIDDEN"))
        cls.network.start()
        cls.process.start()

    @classmethod
    def tearDownClass(cls):
        cls.network.stop()
        cls.process.stop()

    def test_full_original_output_digest_across_exhaustive_market_truth_classes(self):
        self.assertEqual(_digest_cases(core, _market_cases), _GOLDEN["market"])

    def test_full_original_output_digest_across_later_risk_phases(self):
        self.assertEqual(_digest_cases(core, _risk_phase_cases), _GOLDEN["risk_phases"])

    def test_full_original_output_digest_across_zero_nav_and_accepted_bool_quirks(self):
        self.assertEqual(_digest_cases(core, _legacy_quirk_cases), _GOLDEN["legacy_quirks"])

    def test_public_decision_field_shape_is_preserved(self):
        self.assertEqual(tuple(field.name for field in fields(core.DualDriveCoreDecision)), _DECISION_FIELDS)

    def test_reusable_predicate_matches_the_real_core_preplugin_flags(self):
        for index, value in enumerate(_market_cases()):
            if index % 97:
                continue
            prior = value["current_tqqq_quantity"] > 0 or value["current_unlevered_quantity"] > 0
            predicate = core.decide_tqqq_preplugin_risk_on(prior_risk_active=prior, **{
                key: value[key] for key in (
                    "qqq_price", "ma200", "latest_ma20", "ma20_slope", "pullback_rebound",
                    "pullback_rebound_threshold", "require_ma20_slope", "allow_pullback",
                )
            })
            decision = core.decide_tqqq_dual_drive(core.DualDriveCoreInput(**value))
            self.assertEqual((predicate.above_ma200, predicate.slope_ok, predicate.pullback_risk_on),
                             (decision.above_ma200, decision.slope_ok, decision.pullback_risk_on), index)
            self.assertEqual(bool(predicate.trend_risk_active or predicate.pullback_risk_on),
                             decision.target_tqqq_value > 0, index)

    def test_reusable_predicate_uses_explicit_prior_risk_state_without_quantity_sentinels(self):
        helper = core.decide_tqqq_preplugin_risk_on
        market = {
            "qqq_price": 101.0, "ma200": 100.0, "latest_ma20": 100.0,
            "ma20_slope": -1.0, "require_ma20_slope": True, "allow_pullback": False,
            "pullback_rebound": None, "pullback_rebound_threshold": 0.02,
        }
        held = helper(prior_risk_active=True, **market)
        flat = helper(prior_risk_active=False, **market)
        self.assertTrue(held.trend_risk_active)
        self.assertFalse(flat.trend_risk_active)
        self.assertEqual((held.above_ma200, held.slope_ok, held.pullback_risk_on), (True, False, False))

    def test_pullback_eligibility_is_separate_from_the_trend_latch(self):
        for prior in (False, True):
            predicate = core.decide_tqqq_preplugin_risk_on(
                prior_risk_active=prior, qqq_price=100.0, ma200=100.0,
                latest_ma20=99.0, ma20_slope=1.0, require_ma20_slope=True,
                allow_pullback=True, pullback_rebound=0.03, pullback_rebound_threshold=0.02,
            )
            self.assertFalse(predicate.trend_risk_active)
            self.assertTrue(predicate.pullback_risk_on)
            self.assertTrue(predicate.trend_risk_active or predicate.pullback_risk_on)


if __name__ == "__main__":
    unittest.main()
