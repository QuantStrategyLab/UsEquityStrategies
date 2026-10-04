"""Artificial quantity snapshots only; no market, broker or execution proof."""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import subprocess
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal, localcontext
from unittest.mock import patch

from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateChainError,
    StrategyRiskStateIdentity,
    StrategyRiskStateStore,
    StrategyRiskStateTransition,
    build_strategy_risk_state_transition,
)
from us_equity_strategies.research.tqqq_plugin_release_retention import (
    TQQQ_PLUGIN_RELEASE_RETENTION_CANDIDATE,
    build_tqqq_plugin_retention_transition,
)


def _identity():
    return StrategyRiskStateIdentity(
        "tqqq_growth_income", "synthetic", TQQQ_PLUGIN_RELEASE_RETENTION_CANDIDATE, "a" * 64,
    )


def _frame(**changes):
    frame = {
        "signal_session": "2026-09-29", "effective_session": "2026-09-30",
        "snapshot_sha256": "b" * 64, "snapshot_complete": True,
        "corporate_actions_complete": True, "basis_sha256": "c" * 64,
        "proposed_quantity": "45", "settled_quantity": "11.25",
        "pending_buy_quantity": "0", "pending_sell_quantity": "0",
        "nominal_cap_quantity": "45", "effective_cap_quantity": "45",
        "account_cap_quantity": "45",
        "quantity_step": "0.01", "split": None,
        "initialization": {"mode": "migration", "receipt_sha256": "d" * 64},
    }
    frame.update(changes)
    return frame


def _root(**changes):
    return build_tqqq_plugin_retention_transition(identity=_identity(), frozen_input=_frame(**changes))


def _next(**changes):
    frame = _frame(signal_session="2026-09-30", effective_session="2026-10-01", initialization=None)
    frame.update(changes)
    return frame


def _build(previous=None, frame=None, identity=None):
    return build_tqqq_plugin_retention_transition(
        identity=identity if identity is not None else _identity(),
        frozen_input=frame if frame is not None else _next(),
        previous_transition=previous if previous is not None else _root(),
    )


def _target(transition):
    return Decimal(transition.state["target_quantity"])


class TqqqPluginRetentionTests(unittest.TestCase):
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

    def test_permission_restoration_does_not_raise_existing_quantity(self):
        transition = _build(frame=_next(proposed_quantity="45"))
        self.assertEqual((_target(transition), transition.state["new_buy_allowance"]), (Decimal("11.25"), "0"))

    def test_price_fall_proposal_cannot_refill_same_dollar_target(self):
        # 112.5USD /10=11.25 shares; /5=22.5. The lower price is already
        # reflected by the caller's effective-session quantity proposal.
        transition = _build(frame=_next(proposed_quantity=str(Decimal("112.5") / Decimal("5"))))
        self.assertEqual(_target(transition), Decimal("11.25"))

    def test_all_three_risk_caps_are_independent_and_can_tighten(self):
        for field in ("nominal_cap_quantity", "effective_cap_quantity", "account_cap_quantity"):
            with self.subTest(field=field):
                self.assertEqual(_target(_build(frame=_next(**{field: "5"}))), Decimal(5))

    def test_zero_free_cash_does_not_liquidate_already_funded_holdings(self):
        # Complete underlying snapshot has no spendable cash; total-exposure
        # caps still include funded holdings. It is not free_cash/price.
        snapshot = {"settled_quantity": "11.25", "free_cash_usd": "0", "price_usd": "10"}
        frozen_digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        transition = _build(frame=_next(snapshot_sha256=frozen_digest))
        self.assertEqual(_target(transition), Decimal(snapshot["settled_quantity"]))
        self.assertEqual(transition.state["new_buy_allowance"], "0")
        self.assertFalse(transition.state["reconciliation_required"])
        with self.assertRaises(ValueError):
            _build(frame=_next(cash_cap_quantity="0"))

    def test_pending_buys_are_counted_and_pending_sells_do_not_offset(self):
        prior = _root(settled_quantity="15", pending_buy_quantity="5", pending_sell_quantity="5")
        transition = _build(prior, _next(settled_quantity="17", pending_buy_quantity="3", pending_sell_quantity="5"))
        self.assertEqual(_target(transition), Decimal(20))
        self.assertEqual(transition.state["outstanding_quantity"], "20")
        self.assertEqual(transition.state["new_buy_allowance"], "0")

    def test_confirmed_sell_and_cancel_ratchet_without_refill(self):
        prior = _root(settled_quantity="15", pending_buy_quantity="5")
        canceled = _build(prior, _next(settled_quantity="15"))
        self.assertEqual(_target(canceled), Decimal(15))
        sold = _build(prior, _next(settled_quantity="10"))
        self.assertEqual(_target(sold), Decimal(10))

    def test_outstanding_orders_survive_a_lower_proposal(self):
        transition = _build(_root(settled_quantity="15", pending_buy_quantity="5"),
                            _next(settled_quantity="15", pending_buy_quantity="5", proposed_quantity="5"))
        self.assertEqual(_target(transition), Decimal(5))
        self.assertEqual(transition.state["outstanding_quantity"], "20")
        self.assertTrue(transition.state["reconciliation_required"])

    def test_flat_genesis_needs_explicit_receipt_and_has_no_buy_allowance(self):
        frame = _frame(settled_quantity="0", initialization={"mode": "genesis", "receipt_sha256": "e" * 64})
        transition = build_tqqq_plugin_retention_transition(identity=_identity().to_dict(), frozen_input=frame)
        self.assertEqual(_target(transition), Decimal(0))
        self.assertEqual(transition.state["new_buy_allowance"], "0")
        self.assertEqual(transition.state["initialization_mode"], "genesis")

    def test_nonflat_or_pending_account_is_not_genesis(self):
        for changes in ({}, {"settled_quantity": "0", "pending_buy_quantity": "1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _root(initialization={"mode": "genesis", "receipt_sha256": "e" * 64}, **changes)

    def test_migration_retains_at_most_current_capped_exposure(self):
        transition = _root(proposed_quantity="20", nominal_cap_quantity="10")
        self.assertEqual(_target(transition), Decimal(10))
        self.assertEqual(transition.state["initialization_mode"], "migration")

    def test_missing_receipt_never_guesses_first_run(self):
        with self.assertRaises(ValueError):
            _root(settled_quantity="0", initialization=None)

    def test_existing_chain_cannot_reinitialize(self):
        with self.assertRaises(ValueError):
            _build(frame=_frame(signal_session="2026-09-30", effective_session="2026-10-01"))

    def test_risk_forced_flat_stays_flat_without_new_intent_path(self):
        transition = _build(frame=_next(settled_quantity="0", proposed_quantity="45"))
        self.assertEqual(_target(transition), Decimal(0))
        for field, value in (("intent", {"issuer": "strategy"}), ("ai_audit", "agree"), ("approved_refresh", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                _build(frame=_next(**{field: value}))

    def test_quantity_rounding_never_adds_shares_or_cash_normalization(self):
        prior = _root(settled_quantity="11.259", quantity_step="0.001")
        self.assertEqual(_target(_build(prior, _next(settled_quantity="11.259", quantity_step="1"))), Decimal(11))
        self.assertEqual(_target(_build(prior, _next(settled_quantity="11.259", quantity_step="0.01"))), Decimal("11.25"))

    def test_verified_split_converts_ceiling_and_exposure_in_same_basis(self):
        split = {"previous_basis_sha256": "c" * 64, "factor": "2", "event_sha256": "e" * 64}
        transition = _build(frame=_next(settled_quantity="22.5", basis_sha256="f" * 64, split=split))
        self.assertEqual(_target(transition), Decimal("22.5"))
        self.assertEqual(transition.state["applied_split_event_sha256s"], ["e" * 64])
        self.assertEqual(transition.state["new_buy_allowance"], "0")

    def test_split_converts_pending_buys_without_creating_headroom(self):
        prior = _root(settled_quantity="10", pending_buy_quantity="1.25")
        transition = _build(prior, _next(settled_quantity="20", pending_buy_quantity="2.5",
                            basis_sha256="f" * 64, split={
                                "previous_basis_sha256": "c" * 64, "factor": "2", "event_sha256": "e" * 64,
                            }))
        self.assertEqual((_target(transition), transition.state["outstanding_quantity"]), (Decimal("22.5"), "22.5"))
        self.assertEqual(transition.state["new_buy_allowance"], "0")

    def test_basis_change_needs_exact_verified_split_and_history(self):
        for changes in (
            {"basis_sha256": "f" * 64},
            {"split": {"previous_basis_sha256": "c" * 64, "factor": "2", "event_sha256": "e" * 64}},
            {"basis_sha256": "f" * 64, "split": {"previous_basis_sha256": "1" * 64, "factor": "2", "event_sha256": "e" * 64}},
            {"corporate_actions_complete": False},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _build(frame=_next(**changes))

    def test_reusing_an_older_split_event_after_another_action_fails(self):
        first = _build(frame=_next(settled_quantity="22.5", basis_sha256="f" * 64,
                       split={"previous_basis_sha256": "c" * 64, "factor": "2", "event_sha256": "e" * 64}))
        second_frame = _next(settled_quantity="45", basis_sha256="1" * 64,
                           split={"previous_basis_sha256": "f" * 64, "factor": "2", "event_sha256": "2" * 64})
        second_frame.update(signal_session="2026-10-01", effective_session="2026-10-02")
        second = _build(first, second_frame)
        repeated = copy.deepcopy(second_frame)
        repeated.update(signal_session="2026-10-02", effective_session="2026-10-03", basis_sha256="3" * 64)
        repeated["split"] = {"previous_basis_sha256": "1" * 64, "factor": "2", "event_sha256": "e" * 64}
        with self.assertRaises(ValueError):
            _build(second, repeated)

    def test_split_that_requires_fractional_settlement_is_unsupported(self):
        with self.assertRaises(ValueError):
            _build(frame=_next(settled_quantity="5.625", quantity_step="1", basis_sha256="f" * 64,
                   split={"previous_basis_sha256": "c" * 64, "factor": "0.5", "event_sha256": "e" * 64}))

    def test_exhausted_split_history_fails_without_truncation_or_reset(self):
        prior = _root()
        state = prior.state
        state["applied_split_event_sha256s"] = [f"{index:064x}" for index in range(32)]
        bounded = build_strategy_risk_state_transition(identity=prior.identity,
            effective_session=prior.effective_session, input_sha256=prior.input_sha256, state=state)
        with self.assertRaises(ValueError):
            _build(bounded, _next(settled_quantity="22.5", basis_sha256="f" * 64,
                   split={"previous_basis_sha256": "c" * 64, "factor": "2", "event_sha256": "e" * 64}))
        self.assertEqual(len(bounded.state["applied_split_event_sha256s"]), 32)

    def test_malformed_quantity_sessions_and_snapshot_fail_closed(self):
        for changes in (
            {"settled_quantity": "NaN"}, {"settled_quantity": "-1"}, {"settled_quantity": True},
            {"settled_quantity": "1e1000"}, {"settled_quantity": "-0"},
            {"settled_quantity": "0.0000000000000000001"},
            {"quantity_step": "0"}, {"quantity_step": "0.10"},
            {"proposed_quantity": 45}, {"snapshot_complete": "true"}, {"snapshot_sha256": "bogus"},
            {"signal_session": "20261001"}, {"signal_session": "2026-10-01"},
            {"pending_sell_quantity": "20"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _build(frame=_next(**changes))

    def test_bounded_arithmetic_does_not_inherit_ambient_decimal_precision(self):
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(_target(_build()), Decimal("11.25"))
        with self.assertRaises(ValueError):
            _root(settled_quantity="1000000000000000000", pending_buy_quantity="1")
        with self.assertRaises(ValueError):
            _build(_root(settled_quantity="1000000000000000000",
                         proposed_quantity="1000000000000000000", nominal_cap_quantity="1000000000000000000",
                         effective_cap_quantity="1000000000000000000", account_cap_quantity="1000000000000000000"),
                   _next(basis_sha256="f" * 64,
                         split={"previous_basis_sha256": "c" * 64, "factor": "1000000000000000000", "event_sha256": "e" * 64}))

    def test_input_permutation_is_deterministic_and_inputs_are_not_mutated(self):
        frame = _next()
        frozen_copy = copy.deepcopy(frame)
        prior = _root()
        prior_wire = prior.to_dict()
        result = _build(prior, frame)
        reordered = dict(reversed(list(frame.items())))
        self.assertEqual(result, _build(prior, reordered))
        self.assertEqual(frame, frozen_copy)
        self.assertEqual(prior.to_dict(), prior_wire)

    def test_quantity_invariants_over_small_synthetic_grid(self):
        for settled in ("0", "5", "11.25", "20"):
            for pending in ("0", "1.25", "5"):
                for proposal in ("0", "5", "45"):
                    frame = _next(settled_quantity=settled, pending_buy_quantity=pending,
                                  proposed_quantity=proposal, account_cap_quantity="10")
                    result = _build(frame=frame)
                    with self.subTest(settled=settled, pending=pending, proposal=proposal):
                        self.assertLessEqual(_target(result), min(Decimal("11.25"), Decimal(10),
                            Decimal(proposal), Decimal(settled) + Decimal(pending)))
                        self.assertEqual(result.state["new_buy_allowance"], "0")

    def test_foreign_identity_and_non_transition_predecessor_fail_closed(self):
        for changes in ({"candidate_id": "tqqq_qqq_guard_cash_research_v1"}, {"account_scope": "foreign"}, {"config_sha256": "e" * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _build(identity=replace(_identity(), **changes))
        with self.assertRaises(ValueError):
            _build(previous={"state": _root().state})

    def test_even_valid_qpk_digest_does_not_admit_malformed_strategy_state(self):
        prior = _root()
        for field, value in (("schema_version", "other"), ("retained_ceiling_quantity", "99"),
                             ("new_buy_allowance", "1"), ("target_quantity", "12"),
                             ("reconciliation_required", "false"), ("initialization_receipt_sha256", "bogus"),
                             ("initialization_mode", "genesis"),
                             ("applied_split_event_sha256s", ["e" * 64, "e" * 64])):
            wire = prior.state
            wire[field] = value
            forged = build_strategy_risk_state_transition(identity=prior.identity, effective_session=prior.effective_session,
                input_sha256=prior.input_sha256, state=wire)
            with self.subTest(field=field), self.assertRaises(ValueError):
                _build(previous=forged)

    def test_mutated_frozen_qpk_instance_is_reparsed(self):
        prior = _root()
        object.__setattr__(prior, "transition_sha256", "e" * 64)
        with self.assertRaises(ValueError):
            _build(previous=prior)

    def test_same_session_returns_exact_stored_result_and_append_receipt(self):
        with tempfile.TemporaryDirectory() as path:
            store = StrategyRiskStateStore(local_dir=path)
            prior = _root()
            store.append(prior)
            frame = _next()
            transition = _build(prior, frame)
            self.assertEqual(store.append(transition).status.value, "created")
            stored = store.load_chain(_identity())[-1]
            retry = _build(stored, frame)
            self.assertEqual(retry.to_dict(), stored.to_dict())
            self.assertEqual(store.append(retry).status.value, "already_appended")
            self.assertEqual(len(store.load_chain(_identity())), 2)

    def test_store_append_failure_produces_no_durable_receipt(self):
        with tempfile.TemporaryDirectory() as path:
            store = StrategyRiskStateStore(local_dir=path)
            prior = _root()
            store.append(prior)
            result = _build(prior)
            with patch.object(StrategyRiskStateStore, "_create_text", side_effect=OSError("synthetic write failure")):
                with self.assertRaises(OSError):
                    store.append(result)
            self.assertEqual(store.load_chain(_identity()), (prior,))

    def test_same_session_changed_holdings_snapshot_or_caps_is_rejected(self):
        transition = _build()
        for changes in ({"settled_quantity": "5"}, {"snapshot_sha256": "e" * 64}, {"account_cap_quantity": "5"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _build(transition, _next(**changes))

    def test_same_session_split_retry_does_not_convert_ceiling_twice(self):
        frame = _next(settled_quantity="22.5", basis_sha256="f" * 64,
                      split={"previous_basis_sha256": "c" * 64, "factor": "2", "event_sha256": "e" * 64})
        transition = _build(frame=frame)
        self.assertEqual(_build(transition, frame).transition_sha256, transition.transition_sha256)
        self.assertEqual(_target(_build(transition, frame)), Decimal("22.5"))

    def test_stale_session_missing_predecessor_and_competing_successor(self):
        prior = _root()
        transition = _build(prior)
        with self.assertRaises(ValueError):
            _build(transition, _frame(initialization=None))
        with tempfile.TemporaryDirectory() as path:
            store = StrategyRiskStateStore(local_dir=path)
            with self.assertRaises(StrategyRiskStateChainError):
                store.append(transition)
            store.append(prior)
            store.append(transition)
            alternate = _build(prior, _next(proposed_quantity="5"))
            with self.assertRaises(StrategyRiskStateChainError):
                store.append(alternate)

    def test_parsed_identity_and_transition_wire_roundtrip(self):
        prior = StrategyRiskStateTransition.from_dict(_root().to_dict())
        transition = _build(prior, identity=_identity().to_dict())
        self.assertEqual(StrategyRiskStateTransition.from_dict(transition.to_dict()), transition)


if __name__ == "__main__":
    unittest.main()
