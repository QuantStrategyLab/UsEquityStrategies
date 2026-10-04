"""Pure no-rearm quantity prerequisite, not a complete plugin-release adapter.

Consumes caller-validated frozen snapshots; builds the existing QPK state
transition without reading/writing storage or issuing orders. There is no
alpha extractor, strategy-intent acceptance, or positive buy allowance here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext

from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateIdentity,
    StrategyRiskStateTransition,
    build_strategy_risk_state_transition,
)


TQQQ_PLUGIN_RELEASE_RETENTION_CANDIDATE = "tqqq_qqq_guard_cash_release_intent_research_v1"
TQQQ_PLUGIN_RELEASE_RETENTION_SCHEMA = "tqqq_plugin_release_retention.v1"
_MAX_SPLITS = 32
_INPUT_FIELDS = frozenset({
    "signal_session", "effective_session", "snapshot_sha256", "snapshot_complete",
    "corporate_actions_complete", "basis_sha256", "proposed_quantity", "settled_quantity",
    "pending_buy_quantity", "pending_sell_quantity", "nominal_cap_quantity",
    "effective_cap_quantity", "account_cap_quantity",
    "quantity_step", "split", "initialization",
})
_QUANTITY_FIELDS = frozenset({
    "proposed_quantity", "settled_quantity", "pending_buy_quantity", "pending_sell_quantity",
    "nominal_cap_quantity", "effective_cap_quantity", "account_cap_quantity",
    "quantity_step",
})
_STATE_FIELDS = frozenset({
    "schema_version", "signal_session", "basis_sha256", "retained_ceiling_quantity",
    "target_quantity", "new_buy_allowance", "outstanding_quantity", "bounded_proposal_quantity",
    "quantity_step", "reconciliation_required", "initialization_mode",
    "initialization_receipt_sha256", "applied_split_event_sha256s", "no_rearm_only",
})


def _fields(value: object, expected: frozenset[str], name: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{name} has invalid fields")
    return dict(value)


def _digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid lowercase SHA-256 digest")
    return value


def _session(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("session must be a canonical ISO date")
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise ValueError("session must be a canonical ISO date")


def _text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _quantity(value: object, *, positive: bool = False) -> Decimal:
    # Bound size/exponents before arithmetic; do not inherit a float or coerce
    # a boolean. The wire representation is exact, canonical and JSON-safe.
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError("quantity must be a bounded canonical decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid quantity") from exc
    if (not result.is_finite() or result < 0 or result > Decimal("1e18")
            or result.as_tuple().exponent < -18 or _text(result) != value
            or (positive and result <= 0)):
        raise ValueError("quantity is noncanonical or outside supported bounds")
    return result


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _identity(value: object) -> StrategyRiskStateIdentity:
    wire = value.to_dict() if isinstance(value, StrategyRiskStateIdentity) else value
    parsed = StrategyRiskStateIdentity.from_dict(wire)
    if (wire != parsed.to_dict() or parsed.strategy_profile != "tqqq_growth_income"
            or parsed.candidate_id != TQQQ_PLUGIN_RELEASE_RETENTION_CANDIDATE):
        raise ValueError("identity must exactly match the isolated TQQQ candidate")
    return parsed


def _input(value: object) -> tuple[dict, dict[str, Decimal]]:
    frame = _fields(value, _INPUT_FIELDS, "frozen input")
    if frame["snapshot_complete"] is not True or frame["corporate_actions_complete"] is not True:
        raise ValueError("complete reconciled snapshot and corporate-action basis are required")
    if _session(frame["signal_session"]) >= _session(frame["effective_session"]):
        raise ValueError("completed signal session must precede effective session")
    _digest(frame["snapshot_sha256"])
    _digest(frame["basis_sha256"])
    quantities = {key: _quantity(frame[key], positive=key == "quantity_step") for key in _QUANTITY_FIELDS}
    if quantities["pending_sell_quantity"] > quantities["settled_quantity"]:
        raise ValueError("pending sells exceed settled long quantity")
    if frame["initialization"] is not None:
        initialization = _fields(frame["initialization"], frozenset({"mode", "receipt_sha256"}), "initialization")
        if not isinstance(initialization["mode"], str) or initialization["mode"] not in {"genesis", "migration"}:
            raise ValueError("unsupported initialization mode")
        _digest(initialization["receipt_sha256"])
        frame["initialization"] = initialization
    if frame["split"] is not None:
        split = _fields(frame["split"], frozenset({"previous_basis_sha256", "factor", "event_sha256"}), "split")
        _digest(split["previous_basis_sha256"])
        _digest(split["event_sha256"])
        if _quantity(split["factor"], positive=True) == 1:
            raise ValueError("split must change the quantity basis")
        frame["split"] = split
    return frame, quantities


def _state(transition: StrategyRiskStateTransition) -> dict:
    state = _fields(transition.state, _STATE_FIELDS, "previous retention state")
    if state["schema_version"] != TQQQ_PLUGIN_RELEASE_RETENTION_SCHEMA or state["no_rearm_only"] is not True:
        raise ValueError("unsupported retention state")
    if _session(state["signal_session"]) >= transition.effective_session:
        raise ValueError("invalid previous signal session")
    _digest(state["basis_sha256"])
    _digest(state["initialization_receipt_sha256"])
    if not isinstance(state["initialization_mode"], str) or state["initialization_mode"] not in {"genesis", "migration"}:
        raise ValueError("invalid prior initialization")
    events = state["applied_split_event_sha256s"]
    if not isinstance(events, list) or len(events) > _MAX_SPLITS:
        raise ValueError("invalid split history")
    for event in events:
        _digest(event)
    if len(set(events)) != len(events):
        raise ValueError("duplicate split history")
    ceiling = _quantity(state["retained_ceiling_quantity"])
    target = _quantity(state["target_quantity"])
    outstanding = _quantity(state["outstanding_quantity"])
    proposal = _quantity(state["bounded_proposal_quantity"])
    step = _quantity(state["quantity_step"], positive=True)
    if (ceiling > min(outstanding, proposal) or target != ceiling or _floor(target, step) != target
            or (state["initialization_mode"] == "genesis" and ceiling != 0)
            or state["new_buy_allowance"] != "0"
            or type(state["reconciliation_required"]) is not bool
            or state["reconciliation_required"] != (outstanding > target)):
        raise ValueError("inconsistent previous retention quantities")
    return state


def build_tqqq_plugin_retention_transition(
    *,
    identity: StrategyRiskStateIdentity | Mapping[str, object],
    frozen_input: Mapping[str, object],
    previous_transition: StrategyRiskStateTransition | None = None,
) -> StrategyRiskStateTransition:
    """Ratchet a long TQQQ ceiling, without accepting any strategy intent.

    The complete frozen bundle referred to by ``snapshot_sha256`` must be
    validated by the caller. It includes decision-time positions, attributed
    orders/reservations, cancellation/fill status, prices/NAV, cash and funding,
    all cap effective
    times, and corporate-action evidence. Digests and completeness assertions
    are not evidence validators. Initialization receipts likewise reference
    caller-validated reconciliation; absence is never treated as first run.

    Load the predecessor from QPK's existing store. Append this result there
    before using it as a candidate proposal. An identical same-session retry
    returns the parsed stored transition; any changed input fails. An empty
    chain after initialization must not be given another initialization receipt.
    This helper cannot detect erased storage or make order delivery idempotent.
    The three cap quantities bound TOTAL exposure, including funded holdings.
    Free cash divided by price is not a total cap or a liquidation instruction.
    There is no cash-spending path here; a future fresh-intent consumer must
    enforce incremental affordability separately, including costs/reservations.
    """
    with localcontext() as context:
        context.prec = 80
        scope = _identity(identity)
        frame, quantities = _input(frozen_input)
        input_sha256 = hashlib.sha256(json.dumps(
            frame, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode()).hexdigest()
        prior = None
        if previous_transition is not None:
            if not isinstance(previous_transition, StrategyRiskStateTransition):
                raise ValueError("previous_transition must be a QPK transition")
            # Frozen dataclasses can still be constructed/mutated dishonestly;
            # verify the QPK content-addressed wire boundary again, not just type.
            previous_transition = StrategyRiskStateTransition.from_dict(previous_transition.to_dict())
            if previous_transition.identity != scope:
                raise ValueError("previous transition identity mismatch")
            prior = _state(previous_transition)
            if frame["effective_session"] == previous_transition.effective_session:
                if input_sha256 != previous_transition.input_sha256:
                    raise ValueError("same-session frozen input conflict; reconcile stored receipt")
                return previous_transition
            if frame["effective_session"] < previous_transition.effective_session:
                raise ValueError("stale effective session")
            if frame["signal_session"] <= prior["signal_session"]:
                raise ValueError("signal session must advance")
            if frame["initialization"] is not None:
                raise ValueError("existing chain cannot reinitialize")

        outstanding = quantities["settled_quantity"] + quantities["pending_buy_quantity"]
        _quantity(_text(outstanding))
        bounded_proposal = min(quantities[key] for key in (
            "proposed_quantity", "nominal_cap_quantity", "effective_cap_quantity",
            "account_cap_quantity",
        ))
        step = quantities["quantity_step"]
        if prior is None:
            initialization = frame["initialization"]
            if initialization is None:
                raise ValueError("explicit initialization receipt or predecessor is required")
            if frame["split"] is not None:
                raise ValueError("initialization must reconcile the current basis without applying a split")
            if initialization["mode"] == "genesis":
                if outstanding or quantities["pending_sell_quantity"]:
                    raise ValueError("genesis requires a complete flat account without pending risk")
                ceiling = Decimal(0)
            else:
                ceiling = min(outstanding, bounded_proposal)
            events = []
        else:
            initialization = {"mode": prior["initialization_mode"], "receipt_sha256": prior["initialization_receipt_sha256"]}
            events = list(prior["applied_split_event_sha256s"])
            ceiling = _quantity(prior["retained_ceiling_quantity"])
            split = frame["split"]
            if split is None:
                if frame["basis_sha256"] != prior["basis_sha256"]:
                    raise ValueError("unverified quantity basis change")
            else:
                if (split["previous_basis_sha256"] != prior["basis_sha256"]
                        or frame["basis_sha256"] == prior["basis_sha256"]
                        or split["event_sha256"] in events or len(events) >= _MAX_SPLITS):
                    raise ValueError("split basis mismatch, duplicate event, or exhausted history")
                ceiling *= _quantity(split["factor"], positive=True)
                _quantity(_text(ceiling))
                if _floor(ceiling, step) != ceiling or any(
                    _floor(quantities[key], step) != quantities[key]
                    for key in ("settled_quantity", "pending_buy_quantity", "pending_sell_quantity")
                ):
                    raise ValueError("split requires unsupported fractional settlement")
                events.append(split["event_sha256"])
            ceiling = min(ceiling, outstanding, bounded_proposal)

        ceiling = _floor(ceiling, step)
        state = {
            "schema_version": TQQQ_PLUGIN_RELEASE_RETENTION_SCHEMA,
            "signal_session": frame["signal_session"], "basis_sha256": frame["basis_sha256"],
            "retained_ceiling_quantity": _text(ceiling), "target_quantity": _text(ceiling),
            "new_buy_allowance": "0", "outstanding_quantity": _text(outstanding),
            "bounded_proposal_quantity": _text(bounded_proposal), "quantity_step": _text(step),
            "reconciliation_required": outstanding > ceiling,
            "initialization_mode": initialization["mode"],
            "initialization_receipt_sha256": initialization["receipt_sha256"],
            "applied_split_event_sha256s": events, "no_rearm_only": True,
        }
        return build_strategy_risk_state_transition(
            identity=scope, effective_session=frame["effective_session"], input_sha256=input_sha256,
            state=state, previous_transition=previous_transition,
        )
