"""Synthetic release-policy specification, not a production implementation.

No src module imports this reference model. Artificial intent records stand in
for the strategy-owned issuer which does not yet exist. The QPK local store
tests verify only the existing durable seam, not broker/command idempotency.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import tempfile
import unittest
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateChainError,
    StrategyRiskStateIdentity,
    StrategyRiskStateStore,
    build_strategy_risk_state_transition,
)
from us_equity_strategies.strategies.tqqq_dual_drive_core import (
    DualDriveCoreInput,
    decide_tqqq_dual_drive,
)


_CANDIDATE = "tqqq_qqq_guard_cash_release_intent_research_v1"
_REVISION = "cc05a78da902c3762a5766291213580cb458e698"
_CONFIG = "a" * 64
_SCHEMA = "tqqq_plugin_release_state.spec.v1"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _quantity(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("INVALID_QUANTITY")
    return value


def _session(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("NONCANONICAL_SESSION")
    return value


def _hash(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("INVALID_DIGEST")
    return value


@dataclass(frozen=True)
class _Frame:
    signal_session: str = "2026-09-30"
    effective_session: str = "2026-10-01"
    proposed_quantity: Decimal = Decimal("45")
    settled_quantity: Decimal = Decimal("11.25")
    pending_buy_quantity: Decimal = Decimal("0")
    pending_sell_quantity: Decimal = Decimal("0")
    price: Decimal = Decimal("10")
    nav: Decimal = Decimal("1000")
    restricted: bool = False
    new_risk_permitted: bool = True
    tightening_event: bool = False
    complete: bool = True
    split_factor: Decimal = Decimal("1")
    split_verified: bool = True
    split_event_sha256: str | None = None
    strategy_input_sha256: str = "b" * 64
    ai_audit: str = "absent"


@dataclass(frozen=True)
class _Intent:
    sequence: int = 2
    alpha_epoch: str = "alpha_epoch_2"
    issuer: str = "strategy"
    candidate: str = _CANDIDATE
    revision: str = _REVISION
    config_sha256: str = _CONFIG
    input_sha256: str = "b" * 64
    signal_session: str = "2026-09-30"
    effective_session: str = "2026-10-01"
    expires_session: str = "2026-10-01"
    false_to_true_edge: bool = True
    max_quantity: Decimal = Decimal("45")


@dataclass(frozen=True)
class _State:
    ceiling: Decimal = Decimal("11.25")
    last_sequence: int = 1
    last_alpha_epoch: str | None = "alpha_epoch_1"
    last_intent_sha256: str | None = "c" * 64
    latest_restriction_signal: str | None = "2026-09-28"
    effective_session: str = "2026-09-29"
    schema_version: str = _SCHEMA
    last_split_event_sha256: str | None = None


def _reference_decision(
    previous: _State | None,
    frame: _Frame,
    intent: _Intent | None = None,
    *,
    genesis_receipt: str | None = None,
) -> tuple[_State, Decimal, Decimal]:
    """Test-only oracle: ceiling, target quantity, new-buy allowance.

    Positive targets are candidate proposals, never commands. Snapshot data
    and verified intent provenance are assumptions requiring a real adapter.
    """
    _session(frame.signal_session)
    _session(frame.effective_session)
    if frame.signal_session >= frame.effective_session:
        raise ValueError("SIGNAL_NOT_BEFORE_EFFECTIVE_SESSION")
    if frame.complete is not True or any(type(value) is not bool for value in (
        frame.restricted, frame.new_risk_permitted, frame.tightening_event
    )):
        raise ValueError("INCOMPLETE_OR_INVALID_FRAME")
    for quantity in (
        frame.proposed_quantity, frame.settled_quantity,
        frame.pending_buy_quantity, frame.pending_sell_quantity,
        frame.price, frame.nav, frame.split_factor,
    ):
        _quantity(quantity)
    if frame.price <= 0 or frame.split_factor <= 0 or frame.split_verified is not True:
        raise ValueError("UNVERIFIED_PRICE_OR_SPLIT_BASIS")
    if frame.split_factor != 1:
        _hash(frame.split_event_sha256)
        if previous is not None and frame.split_event_sha256 == previous.last_split_event_sha256:
            raise ValueError("DUPLICATE_SPLIT_EVENT")
    elif frame.split_event_sha256 is not None:
        raise ValueError("SPLIT_EVENT_WITHOUT_BASIS_CHANGE")
    _hash(frame.strategy_input_sha256)
    outstanding = frame.settled_quantity + frame.pending_buy_quantity
    genesis = previous is None
    if genesis:
        if genesis_receipt != "d" * 64:
            raise ValueError("MISSING_INITIALIZATION_OR_PREDECESSOR")
        if outstanding or frame.pending_sell_quantity:
            raise ValueError("NONFLAT_ACCOUNT_IS_NOT_GENESIS")
        ceiling, sequence, epoch, intent_digest, restriction = Decimal(0), 0, None, None, None
    else:
        if genesis_receipt is not None:
            raise ValueError("CHAIN_CANNOT_REINITIALIZE")
        if previous.schema_version != _SCHEMA:
            raise ValueError("UNKNOWN_STATE_SCHEMA")
        _quantity(previous.ceiling)
        _session(previous.effective_session)
        if frame.effective_session <= previous.effective_session:
            raise ValueError("STALE_SESSION_USE_STORED_RECEIPT_FOR_RETRY")
        if type(previous.last_sequence) is not int or previous.last_sequence < 0:
            raise ValueError("INVALID_INTENT_WATERMARK")
        if previous.last_sequence > 0 and (not previous.last_alpha_epoch or not previous.last_intent_sha256):
            raise ValueError("MISSING_INTENT_PROVENANCE")
        if previous.last_intent_sha256 is not None:
            _hash(previous.last_intent_sha256)
        if previous.last_split_event_sha256 is not None:
            _hash(previous.last_split_event_sha256)
        if previous.last_sequence == 0 and (
            previous.ceiling != 0 or previous.last_alpha_epoch is not None or previous.last_intent_sha256 is not None
        ):
            raise ValueError("INVALID_UNCONSUMED_INTENT_STATE")
        ceiling = min(previous.ceiling * frame.split_factor, outstanding)
        sequence, epoch, intent_digest = (
            previous.last_sequence, previous.last_alpha_epoch, previous.last_intent_sha256
        )
        restriction = previous.latest_restriction_signal
        if restriction is not None:
            _session(restriction)
            if restriction > frame.signal_session:
                raise ValueError("FUTURE_RESTRICTION_STATE")

    if frame.restricted:
        tightened = frame.proposed_quantity < ceiling
        ceiling = min(ceiling, frame.proposed_quantity)
        if tightened or frame.tightening_event or genesis:
            restriction = frame.signal_session
    elif frame.tightening_event:
        raise ValueError("TIGHTENING_EVENT_REQUIRES_RESTRICTION")

    if intent is not None:
        if (intent.issuer, intent.candidate, intent.revision, intent.config_sha256) != (
            "strategy", _CANDIDATE, _REVISION, _CONFIG
        ):
            raise ValueError("FOREIGN_OR_NON_STRATEGY_INTENT")
        if type(intent.sequence) is not int or intent.sequence <= sequence:
            raise ValueError("REPLAYED_INTENT_VERSION")
        if not intent.alpha_epoch or intent.alpha_epoch == epoch:
            raise ValueError("OLD_ALPHA_EPOCH")
        if intent.false_to_true_edge is not True:
            raise ValueError("HOLD_REFRESH_IS_NOT_NEW_ALPHA")
        if intent.input_sha256 != frame.strategy_input_sha256:
            raise ValueError("INTENT_INPUT_MISMATCH")
        for session in (intent.signal_session, intent.effective_session, intent.expires_session):
            _session(session)
        if intent.signal_session != frame.signal_session or intent.effective_session != frame.effective_session:
            raise ValueError("INTENT_SESSION_MISMATCH")
        if frame.effective_session > intent.expires_session:
            raise ValueError("EXPIRED_INTENT")
        if frame.new_risk_permitted is not True:
            raise ValueError("CURRENT_PLUGIN_POLICY_BLOCKS_NEW_RISK")
        if not genesis:
            if restriction is not None and intent.signal_session <= restriction:
                raise ValueError("INTENT_PREDATES_RESTRICTION")
            if outstanding > previous.ceiling * frame.split_factor:
                raise ValueError("RECONCILE_EXCESS_PENDING_EXPOSURE")
        ceiling = min(_quantity(intent.max_quantity), frame.proposed_quantity)
        sequence, epoch, intent_digest = intent.sequence, intent.alpha_epoch, _digest(asdict(intent))

    target = min(frame.proposed_quantity, ceiling)
    allowance = max(Decimal(0), target - outstanding)
    last_split = frame.split_event_sha256 or (previous.last_split_event_sha256 if previous is not None else None)
    state = _State(ceiling, sequence, epoch, intent_digest, restriction, frame.effective_session, last_split_event_sha256=last_split)
    return state, target, allowance


def _deterministic_input(frame: _Frame) -> dict:
    values = asdict(frame)
    del values["ai_audit"]
    return values


def _identity(candidate: str = _CANDIDATE) -> StrategyRiskStateIdentity:
    return StrategyRiskStateIdentity("tqqq_growth_income", "synthetic", candidate, _CONFIG)


def _transition(previous=None, *, day="2026-10-01", input_value="frozen", state=None):
    return build_strategy_risk_state_transition(
        identity=_identity(), effective_session=day, input_sha256=_digest(input_value),
        state=state or {"schema_version": _SCHEMA, "ceiling_quantity": "11.25"},
        previous_transition=previous,
    )


class TqqqReleaseSpecificationTests(unittest.TestCase):
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

    def test_unchanged_core_reproduces_old_hold_restoration(self):
        value = DualDriveCoreInput(
            qqq_price=101, ma200=100, latest_ma20=100, ma20_slope=1,
            pullback_rebound=0, pullback_rebound_threshold=0,
            current_tqqq_quantity=11.25, current_unlevered_quantity=1,
            require_ma20_slope=True, allow_pullback=False,
            strategy_equity=1000, initial_reserved=20, cash_reserve_floor=0,
            risk_on_cash_reserve_ratio=.02, tqqq_weight=.45, unlevered_weight=.45,
            macro_active=True, macro_route="delever", macro_leverage_scalar=.5,
            macro_risk_asset_scalar=.5, crisis_defense_enabled=False,
            true_crisis_active=False, volatility_enabled=False, volatility_metric=None,
            volatility_entry_threshold=.28, volatility_exit_threshold=.24,
            taco_veto_enabled=False, taco_rebound_context_active=False,
            retention_mode=None, retention_ratio=0,
        )
        restricted = decide_tqqq_dual_drive(value)
        restored = decide_tqqq_dual_drive(replace(value, macro_active=False))
        self.assertEqual(restricted.target_tqqq_value, 112.5)
        self.assertEqual(restored.target_tqqq_value, 450)
        self.assertEqual(restored.state, "hold")

    def test_permission_only_release_cannot_buy(self):
        state, target, buy = _reference_decision(_State(), _Frame())
        self.assertEqual((state.ceiling, target, buy), (Decimal("11.25"), Decimal("11.25"), Decimal(0)))

    def test_price_and_nav_drift_do_not_raise_quantity(self):
        for price, nav, proposal in (("5", "2000", "90"), ("20", "500", "22.5")):
            with self.subTest(price=price, nav=nav):
                _, target, buy = _reference_decision(
                    _State(), replace(_Frame(), price=Decimal(price), nav=Decimal(nav), proposed_quantity=Decimal(proposal))
                )
                self.assertEqual((target, buy), (Decimal("11.25"), Decimal(0)))

    def test_split_adjusts_ceiling_and_holdings_together(self):
        state, target, buy = _reference_decision(
            _State(), replace(_Frame(), split_factor=Decimal(2), split_event_sha256="e" * 64, price=Decimal(5), settled_quantity=Decimal("22.5"))
        )
        self.assertEqual((state.ceiling, target, buy), (Decimal("22.5"), Decimal("22.5"), Decimal(0)))

    def test_unverified_or_duplicate_split_basis_blocks(self):
        with self.assertRaisesRegex(ValueError, "UNVERIFIED"):
            _reference_decision(_State(), replace(_Frame(), split_verified=False))
        # Exactly-once action application must be checked by frozen-input validator;
        # a same-session duplicate cannot recalculate the state with factor two.
        with self.assertRaisesRegex(ValueError, "STALE_SESSION"):
            _reference_decision(replace(_State(), effective_session="2026-10-01"), replace(_Frame(), split_factor=Decimal(2), split_event_sha256="e" * 64))

    def test_split_event_cannot_be_applied_twice_across_sessions(self):
        prior = replace(_State(), last_split_event_sha256="e" * 64)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_SPLIT"):
            _reference_decision(prior, replace(_Frame(), split_factor=Decimal(2), split_event_sha256="e" * 64))

    def test_pending_buys_consume_allowance(self):
        _, target, buy = _reference_decision(
            _State(ceiling=Decimal(20)), replace(_Frame(), settled_quantity=Decimal(15), pending_buy_quantity=Decimal(5))
        )
        self.assertEqual((target, buy), (Decimal(20), Decimal(0)))

    def test_pending_sells_are_not_available_headroom(self):
        _, target, buy = _reference_decision(
            _State(ceiling=Decimal(20)), replace(_Frame(), settled_quantity=Decimal(20), pending_sell_quantity=Decimal(5))
        )
        self.assertEqual((target, buy), (Decimal(20), Decimal(0)))

    def test_confirmed_sell_ratchets_ceiling_and_cannot_refill(self):
        state, target, buy = _reference_decision(
            _State(ceiling=Decimal(20)), replace(_Frame(), settled_quantity=Decimal(15))
        )
        self.assertEqual((state.ceiling, target, buy), (Decimal(15), Decimal(15), Decimal(0)))

    def test_partial_fill_counts_settled_and_remaining_buy_once(self):
        _, target, buy = _reference_decision(
            _State(ceiling=Decimal(20)), replace(_Frame(), settled_quantity=Decimal(17), pending_buy_quantity=Decimal(3))
        )
        self.assertEqual((target, buy), (Decimal(20), Decimal(0)))

    def test_confirmed_cancellation_does_not_replenish_old_intent(self):
        state, target, buy = _reference_decision(
            _State(ceiling=Decimal(20)), replace(_Frame(), settled_quantity=Decimal(15), pending_buy_quantity=Decimal(0))
        )
        self.assertEqual((state.ceiling, target, buy), (Decimal(15), Decimal(15), Decimal(0)))

    def test_further_restriction_reduces_existing_ceiling(self):
        state, target, buy = _reference_decision(
            _State(), replace(_Frame(), restricted=True, proposed_quantity=Decimal(5))
        )
        self.assertEqual((state.ceiling, target, buy), (Decimal(5), Decimal(5), Decimal(0)))

    def test_excess_pending_orders_cannot_be_hidden_by_fresh_intent(self):
        with self.assertRaisesRegex(ValueError, "EXCESS_PENDING"):
            _reference_decision(_State(), replace(_Frame(), pending_buy_quantity=Decimal(1)), _Intent())

    def test_initial_entry_requires_explicit_genesis_and_new_strategy_intent(self):
        frame = replace(_Frame(), settled_quantity=Decimal(0))
        _, target, buy = _reference_decision(None, frame, _Intent(), genesis_receipt="d" * 64)
        self.assertEqual((target, buy), (Decimal(45), Decimal(45)))
        _, target, buy = _reference_decision(None, frame, genesis_receipt="d" * 64)
        self.assertEqual((target, buy), (Decimal(0), Decimal(0)))

    def test_initial_entry_respects_existing_reduced_proposal(self):
        frame = replace(_Frame(), settled_quantity=Decimal(0), restricted=True, proposed_quantity=Decimal("11.25"))
        _, target, buy = _reference_decision(None, frame, _Intent(), genesis_receipt="d" * 64)
        self.assertEqual((target, buy), (Decimal("11.25"), Decimal("11.25")))

    def test_old_hold_or_pending_entry_is_not_genesis(self):
        for frame in (_Frame(), replace(_Frame(), settled_quantity=Decimal(0), pending_buy_quantity=Decimal(1))):
            with self.subTest(frame=frame), self.assertRaisesRegex(ValueError, "NONFLAT"):
                _reference_decision(None, frame, _Intent(), genesis_receipt="d" * 64)

    def test_risk_forced_flat_does_not_manufacture_entry(self):
        prior = replace(_State(), ceiling=Decimal(0))
        frame = replace(_Frame(), settled_quantity=Decimal(0))
        _, target, buy = _reference_decision(prior, frame)
        self.assertEqual((target, buy), (Decimal(0), Decimal(0)))
        with self.assertRaisesRegex(ValueError, "OLD_ALPHA_EPOCH"):
            _reference_decision(prior, frame, replace(_Intent(), alpha_epoch="alpha_epoch_1"))

    def test_fresh_alpha_edge_can_release_ceiling_once(self):
        state, target, buy = _reference_decision(_State(), _Frame(), _Intent(max_quantity=Decimal(30)))
        self.assertEqual((state.ceiling, target, buy, state.last_sequence), (Decimal(30), Decimal(30), Decimal("18.75"), 2))
        next_frame = replace(_Frame(), signal_session="2026-10-01", effective_session="2026-10-02", settled_quantity=Decimal(30))
        with self.assertRaisesRegex(ValueError, "REPLAYED_INTENT"):
            _reference_decision(state, next_frame, _Intent())

    def test_fresh_intent_still_respects_tighter_current_proposal(self):
        _, target, buy = _reference_decision(_State(), replace(_Frame(), proposed_quantity=Decimal(20)), _Intent())
        self.assertEqual((target, buy), (Decimal(20), Decimal("8.75")))

    def test_hold_timestamp_refresh_is_not_new_alpha(self):
        with self.assertRaisesRegex(ValueError, "HOLD_REFRESH"):
            _reference_decision(_State(), _Frame(), replace(_Intent(), false_to_true_edge=False))

    def test_fresh_intent_can_enter_within_already_reduced_current_cap(self):
        _, target, buy = _reference_decision(
            _State(), replace(_Frame(), restricted=True, proposed_quantity=Decimal(20)), _Intent()
        )
        self.assertEqual((target, buy), (Decimal(20), Decimal("8.75")))

    def test_blocked_current_plugin_policy_rejects_new_intent(self):
        with self.assertRaisesRegex(ValueError, "BLOCKS_NEW_RISK"):
            _reference_decision(_State(), replace(_Frame(), new_risk_permitted=False), _Intent())
        with self.assertRaisesRegex(ValueError, "BLOCKS_NEW_RISK"):
            _reference_decision(
                None, replace(_Frame(), settled_quantity=Decimal(0), new_risk_permitted=False),
                _Intent(), genesis_receipt="d" * 64,
            )

    def test_same_session_new_tightening_cannot_be_released_by_intent(self):
        with self.assertRaisesRegex(ValueError, "PREDATES_RESTRICTION"):
            _reference_decision(
                _State(), replace(_Frame(), restricted=True, tightening_event=True), _Intent()
            )

    def test_initialized_flat_chain_without_intent_can_later_accept_first_entry(self):
        flat = replace(_Frame(), settled_quantity=Decimal(0))
        state, _, _ = _reference_decision(None, flat, genesis_receipt="d" * 64)
        next_frame = replace(flat, signal_session="2026-10-01", effective_session="2026-10-02")
        next_intent = replace(_Intent(), signal_session="2026-10-01", effective_session="2026-10-02", expires_session="2026-10-02")
        _, target, buy = _reference_decision(state, next_frame, next_intent)
        self.assertEqual((target, buy), (Decimal(45), Decimal(45)))

    def test_exact_strategy_revision_config_candidate_and_input_required(self):
        mutations = (
            {"candidate": "tqqq_qqq_guard_cash_research_v1"}, {"revision": "1" * 40},
            {"config_sha256": "e" * 64}, {"input_sha256": "e" * 64},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _reference_decision(_State(), _Frame(), replace(_Intent(), **mutation))

    def test_expired_or_wrong_session_intent_rejected(self):
        for mutation in ({"expires_session": "2026-09-30"}, {"effective_session": "2026-10-02"}, {"signal_session": "2026-09-29"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _reference_decision(_State(), _Frame(), replace(_Intent(), **mutation))

    def test_intent_cannot_predate_latest_restriction(self):
        with self.assertRaisesRegex(ValueError, "PREDATES_RESTRICTION"):
            _reference_decision(replace(_State(), latest_restriction_signal="2026-09-30"), _Frame(), _Intent())

    def test_ai_or_plugin_cannot_issue_strategy_intent(self):
        for issuer in ("ai", "plugin", "human_review", "automation_approved"):
            with self.subTest(issuer=issuer), self.assertRaisesRegex(ValueError, "NON_STRATEGY"):
                _reference_decision(_State(), _Frame(), replace(_Intent(), issuer=issuer))

    def test_ai_agree_failure_and_missing_have_identical_quantity_results(self):
        frames = [replace(_Frame(), ai_audit=verdict) for verdict in ("agree", "ok", "failed", "absent")]
        results = [_reference_decision(_State(), frame) for frame in frames]
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len({_digest(_deterministic_input(frame)) for frame in frames}), 1)

    def test_missing_initialized_chain_fails_even_when_flat(self):
        with self.assertRaisesRegex(ValueError, "MISSING_INITIALIZATION"):
            _reference_decision(None, replace(_Frame(), settled_quantity=Decimal(0)), _Intent())

    def test_malformed_state_and_incomplete_snapshot_fail_closed(self):
        for prior in (replace(_State(), ceiling=Decimal("NaN")), replace(_State(), schema_version="old"), replace(_State(), last_sequence=True), replace(_State(), last_intent_sha256="bogus")):
            with self.subTest(prior=prior), self.assertRaises(ValueError):
                _reference_decision(prior, _Frame())
        with self.assertRaisesRegex(ValueError, "INCOMPLETE"):
            _reference_decision(_State(), replace(_Frame(), complete=False))

    def test_initialization_cannot_reset_an_existing_chain(self):
        with self.assertRaisesRegex(ValueError, "REINITIALIZE"):
            _reference_decision(_State(), _Frame(), _Intent(), genesis_receipt="d" * 64)

    def test_pinned_store_identical_append_returns_same_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            state, target, buy = _reference_decision(_State(), _Frame())
            wire = {key: str(value) if isinstance(value, Decimal) else value for key, value in asdict(state).items()}
            wire.update(target_quantity=str(target), new_buy_allowance=str(buy))
            transition = _transition(input_value=_deterministic_input(_Frame()), state=wire)
            self.assertEqual(store.append(transition).status.value, "created")
            duplicate = store.append(transition)
            self.assertEqual(duplicate.status.value, "already_appended")
            self.assertEqual(duplicate.transition.transition_sha256, transition.transition_sha256)
            self.assertEqual(len(store.load_chain(_identity())), 1)

    def test_pinned_store_conflicting_session_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            store.append(_transition())
            with self.assertRaises(StrategyRiskStateChainError):
                store.append(_transition(input_value="changed"))

    def test_pinned_store_missing_predecessor_and_competing_successor_rejected(self):
        first = _transition()
        second = _transition(first, day="2026-10-02", input_value="next")
        alternate = _transition(first, day="2026-10-02", input_value="different-next")
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            with self.assertRaisesRegex(StrategyRiskStateChainError, "missing"):
                store.append(second)
            store.append(first)
            store.append(second)
            with self.assertRaises(StrategyRiskStateChainError):
                store.append(alternate)

    def test_pinned_transition_cannot_cross_candidate_identity(self):
        with self.assertRaisesRegex(StrategyRiskStateChainError, "identity"):
            build_strategy_risk_state_transition(
                identity=_identity("another_candidate"), effective_session="2026-10-02",
                input_sha256="e" * 64, state={"ceiling_quantity": "45"}, previous_transition=_transition(),
            )


if __name__ == "__main__":
    unittest.main()
