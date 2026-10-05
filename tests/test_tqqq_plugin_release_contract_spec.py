"""Synthetic release-policy specification, not a production implementation.

No src module imports this reference model. Artificial intent records stand in
for the strategy-owned issuer which does not yet exist. The QPK local store
tests verify only the existing durable seam, not broker/command idempotency.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
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
    decide_tqqq_preplugin_risk_on,
)


_CANDIDATE = "tqqq_qqq_guard_cash_release_intent_research_v1"
_REVISION = "416356b4816390a674e8af7a4aba2e9f17d2d474"
_PREDICATE_SHA256 = "d57745b0de59291a08293f9f07ba60152d5362b1f323d4b7cc088973930e110c"
_SCHEMA = "tqqq_plugin_release_state.spec.v1"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


# A content-bound fixture policy, not an installed/deployed source identity.
_CONFIG = _digest({
    "candidate": _CANDIDATE, "source_revision": _REVISION,
    "predicate_source_sha256": _PREDICATE_SHA256,
    "policy": "synthetic.separate_trend_latch.independent_false_bootstrap.v2",
    "expiry": "one_effective_session", "inputs": "toy_indicator_observations_only",
    "predicate": {"ma200": 100, "require_ma20_slope": True, "allow_pullback": True,
                  "pullback_rebound": .2, "pullback_rebound_threshold": .1},
    "max_quantity": "45",
})


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


_TOY_SCHEMA = "tqqq_plugin_release_state.synthetic.v2"
# An explicit illustrative fixture schedule, not a validated venue calendar.
_TOY_DAYS = ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02")


@dataclass(frozen=True)
class _ToyObservation:
    signal_session: str
    effective_session: str
    expires_session: str
    price: float
    ma20: float
    slope: float
    account_scope: str = "synthetic"
    source_revision: str = _REVISION
    predicate_sha256: str = _PREDICATE_SHA256
    config_sha256: str = _CONFIG


def _toy_observation(day, *, price, ma20, slope):
    return _ToyObservation(_TOY_DAYS[day], _TOY_DAYS[day + 1], _TOY_DAYS[day + 1], price, ma20, slope)


def _toy_alpha(prior, observation, input_sha256):
    """Test-only issuer over toy indicators; no warmup/calendar/PIT validator."""
    if (observation.account_scope, observation.source_revision, observation.predicate_sha256,
            observation.config_sha256) != ("synthetic", _REVISION, _PREDICATE_SHA256, _CONFIG):
        raise ValueError("FOREIGN_OBSERVATION_SCOPE_OR_SOURCE")
    source = Path(__file__).resolve().parents[1] / "src/us_equity_strategies/strategies/tqqq_dual_drive_core.py"
    if hashlib.sha256(source.read_bytes()).hexdigest() != _PREDICATE_SHA256:
        raise ValueError("FROZEN_PREDICATE_SOURCE_CHANGED")
    for session in (observation.signal_session, observation.effective_session, observation.expires_session):
        _session(session)
    if not observation.signal_session < observation.effective_session == observation.expires_session:
        raise ValueError("EXPIRED_OR_UNSUPPORTED_TOY_SESSION")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (
        observation.price, observation.ma20, observation.slope,
    )) or observation.price <= 0 or observation.ma20 <= 0:
        raise ValueError("INVALID_TOY_INDICATORS")
    observation_digest = _digest(asdict(observation))
    alpha = dict(prior) if prior is not None else {
        "trend_latch": None, "eligibility": None, "armed": False,
        "sequence": 0, "epoch": None, "last_false_observation_sha256": None,
    }
    def predicate(trend):
        return decide_tqqq_preplugin_risk_on(
            prior_risk_active=trend, qqq_price=observation.price, ma200=100,
            latest_ma20=observation.ma20, ma20_slope=observation.slope,
            pullback_rebound=.2, pullback_rebound_threshold=.1,
            require_ma20_slope=True, allow_pullback=True,
        )
    if alpha["trend_latch"] is None:
        alternatives = (predicate(False), predicate(True))
        if all(not (value.trend_risk_active or value.pullback_risk_on) for value in alternatives):
            alpha.update(trend_latch=False, eligibility=False, armed=True,
                         last_false_observation_sha256=observation_digest)
        # Forced true and ambiguous history both stay unarmed/unknown.
        return alpha, None
    result = predicate(alpha["trend_latch"])
    eligibility = result.trend_risk_active or result.pullback_risk_on
    edge = alpha["armed"] and alpha["eligibility"] is False and eligibility
    if not eligibility:
        alpha["last_false_observation_sha256"] = observation_digest
    intent = None
    if edge:
        alpha["sequence"] += 1
        alpha["epoch"] = _digest({
            "identity": _identity().to_dict(), "sequence": alpha["sequence"],
            "false": alpha["last_false_observation_sha256"], "true": observation_digest,
        })
        intent = _Intent(
            sequence=alpha["sequence"], alpha_epoch=alpha["epoch"],
            input_sha256=input_sha256, signal_session=observation.signal_session,
            effective_session=observation.effective_session, expires_session=observation.expires_session,
        )
    alpha.update(trend_latch=result.trend_risk_active, eligibility=eligibility)
    return alpha, intent


def _run_toy_cycle(store, observation, *, initialize=False, settled="0", nav="1000",
                   policy_mode="normal", cooldown_active=False, ai_audit="absent"):
    """The used fixture consumer; returns only after the real local QPK append.

    No src caller imports this function. Indicators, caps, reconciliation and
    cooldown observations are synthetic assumptions, never actual authority.
    """
    if store.local_dir is None or store.cloud_prefix_uri is not None:
        raise ValueError("ONLY_LOCAL_SYNTHETIC_STORE")
    if policy_mode not in {"normal", "reduced", "blocked"} or type(cooldown_active) is not bool:
        raise ValueError("INVALID_TOY_RISK_MODE")
    frame = _Frame(signal_session=observation.signal_session, effective_session=observation.effective_session,
                   settled_quantity=Decimal(settled), nav=Decimal(nav), ai_audit=ai_audit,
                   restricted=policy_mode != "normal", new_risk_permitted=policy_mode != "blocked" and not cooldown_active)
    frozen_input = {"observation": asdict(observation), "frame": _deterministic_input(frame),
                    "policy_mode": policy_mode, "cooldown_active": cooldown_active, "initialize": initialize}
    chain = store.load_chain(_identity())
    previous = chain[-1] if chain else None
    frozen_input["predecessor_sha256"] = (previous.previous_transition_sha256
        if previous is not None and previous.effective_session == frame.effective_session
        else previous.transition_sha256 if previous is not None else None)
    input_sha256 = _digest(frozen_input)
    if previous is not None and previous.effective_session == frame.effective_session:
        if previous.input_sha256 != input_sha256:
            raise ValueError("FROZEN_INPUT_CONFLICT")
        return store.append(previous)
    if previous is not None:
        if previous.state.get("schema_version") != _TOY_SCHEMA:
            raise ValueError("WRONG_SYNTHETIC_STATE_SCHEMA")
        release = dict(previous.state["release"])
        release["ceiling"] = Decimal(release["ceiling"])
        prior_release = _State(**release)
        prior_alpha = previous.state["alpha"]
    else:
        prior_release, prior_alpha = None, None
    alpha, issued = _toy_alpha(prior_alpha, observation, input_sha256)
    frame = replace(frame, strategy_input_sha256=input_sha256)
    # An edge seen while blocked is recorded in the issued watermark, but its
    # version cannot be repackaged as a new edge when permission returns.
    accepted = issued if frame.new_risk_permitted else None
    release, target, allowance = _reference_decision(
        prior_release, frame, accepted, genesis_receipt="d" * 64 if initialize else None,
    )
    blocked = not frame.new_risk_permitted
    release_state = ("blocked" if blocked else policy_mode if accepted is not None
                     or target == frame.proposed_quantity else "await_fresh_intent")
    release_wire = asdict(release)
    release_wire["ceiling"] = str(release.ceiling)
    transition = build_strategy_risk_state_transition(
        identity=_identity(), effective_session=frame.effective_session, input_sha256=input_sha256,
        state={"schema_version": _TOY_SCHEMA, "alpha": alpha, "release": release_wire,
               "policy_mode": policy_mode, "release_state": release_state,
               "issued_intent": asdict(issued) | {"max_quantity": str(issued.max_quantity)} if issued else None,
               "target_quantity": str(target), "new_buy_allowance": str(allowance)},
        previous_transition=previous,
    )
    return store.append(transition)


class TqqqProspectiveSyntheticSpecificationTests(unittest.TestCase):
    """Toy indicator observations only: these tests do not implement R1."""

    def _cycle(self, store, day, *, price=99, ma20=100, slope=0, **changes):
        observation = _toy_observation(day, price=price, ma20=ma20, slope=slope)
        return _run_toy_cycle(store, observation, **changes)

    def test_pullback_overall_is_not_the_next_trend_prior(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            first = self._cycle(store, 0, price=99, ma20=98, slope=1, initialize=True)
            self.assertIsNone(first.transition.state["alpha"]["trend_latch"])
            self.assertEqual(first.transition.state["new_buy_allowance"], "0")
            # Bootstrap from an independent false, then observe a pullback.
            self._cycle(store, 1)
            pullback = self._cycle(store, 2, price=99, ma20=98, slope=1)
            self.assertFalse(pullback.transition.state["alpha"]["trend_latch"])
            self.assertTrue(pullback.transition.state["alpha"]["eligibility"])
            after = self._cycle(store, 3, price=101, slope=0)
            self.assertFalse(after.transition.state["alpha"]["trend_latch"])
            self.assertFalse(after.transition.state["alpha"]["eligibility"])
            self.assertEqual(after.transition.state["alpha"]["sequence"], 1)

    def test_unknown_forced_true_cannot_issue_until_independent_false_then_true(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            true = self._cycle(store, 0, price=101, slope=1, initialize=True)
            self.assertIsNone(true.transition.state["alpha"]["eligibility"])
            self.assertFalse(true.transition.state["alpha"]["armed"])
            self.assertEqual(true.transition.state["alpha"]["sequence"], 0)
            self._cycle(store, 1)
            entered = self._cycle(store, 2, price=101, slope=1)
            self.assertEqual(entered.transition.state["alpha"]["sequence"], 1)
            self.assertEqual(entered.transition.state["new_buy_allowance"], "45")
            held = self._cycle(store, 3, price=101, slope=0, settled="45")
            self.assertEqual(held.transition.state["alpha"]["sequence"], 1)
            self.assertEqual(held.transition.state["new_buy_allowance"], "0")

    def test_risk_liquidation_permission_cooldown_and_nav_do_not_create_epoch(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            self._cycle(store, 0, initialize=True)
            entered = self._cycle(store, 1, price=101, slope=1)
            epoch = entered.transition.state["alpha"]["epoch"]
            blocked = self._cycle(store, 2, price=101, slope=0, settled="0", policy_mode="blocked", cooldown_active=True)
            self.assertEqual(blocked.transition.state["release_state"], "blocked")
            restored = self._cycle(store, 3, price=101, slope=0, nav="2000", ai_audit="agree")
            self.assertEqual(restored.transition.state["release_state"], "await_fresh_intent")
            self.assertEqual(restored.transition.state["alpha"]["epoch"], epoch)
            self.assertEqual(restored.transition.state["alpha"]["sequence"], 1)
            self.assertEqual(restored.transition.state["new_buy_allowance"], "0")

    def test_edge_seen_while_blocked_is_not_repackaged_when_unblocked(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            self._cycle(store, 0, initialize=True)
            blocked = self._cycle(store, 1, price=101, slope=1, policy_mode="blocked")
            self.assertEqual(blocked.transition.state["alpha"]["sequence"], 1)
            self.assertEqual(blocked.transition.state["new_buy_allowance"], "0")
            restored = self._cycle(store, 2, price=101, slope=1)
            self.assertEqual(restored.transition.state["alpha"]["sequence"], 1)
            self.assertEqual(restored.transition.state["new_buy_allowance"], "0")

    def test_append_failure_exposes_no_positive_result_and_restart_replays_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            self._cycle(store, 0, initialize=True)
            with patch.object(StrategyRiskStateStore, "append", side_effect=OSError("SYNTHETIC_WRITE_FAILURE")):
                with self.assertRaisesRegex(OSError, "SYNTHETIC_WRITE_FAILURE"):
                    self._cycle(store, 1, price=101, slope=1)
            self.assertEqual(len(store.load_chain(_identity())), 1)
            appended = self._cycle(store, 1, price=101, slope=1)
            restarted = self._cycle(StrategyRiskStateStore(local_dir=root), 1, price=101, slope=1)
            self.assertEqual(restarted.status.value, "already_appended")
            self.assertEqual(restarted.transition, appended.transition)
            self.assertEqual(len(store.load_chain(_identity())), 2)
            with self.assertRaisesRegex(ValueError, "FROZEN_INPUT_CONFLICT"):
                self._cycle(store, 1, price=101, slope=1, nav="1001")

    def test_expired_and_wrong_scope_observations_are_rejected_before_append(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            self._cycle(store, 0, initialize=True)
            observation = _toy_observation(1, price=101, ma20=100, slope=1)
            for invalid in (
                replace(observation, expires_session="2026-09-28"),
                replace(observation, account_scope="foreign"),
                replace(observation, source_revision="0" * 40),
                replace(observation, config_sha256="0" * 64),
            ):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    _run_toy_cycle(store, invalid)
            self.assertEqual(len(store.load_chain(_identity())), 1)

    def test_uninitialized_missing_chain_does_not_recover_as_genesis(self):
        with tempfile.TemporaryDirectory() as root:
            store = StrategyRiskStateStore(local_dir=root)
            with self.assertRaisesRegex(ValueError, "MISSING_INITIALIZATION"):
                self._cycle(store, 1, price=101, slope=1)
            self.assertEqual(store.load_chain(_identity()), ())


if __name__ == "__main__":
    unittest.main()
