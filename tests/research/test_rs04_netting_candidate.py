"""Synthetic cash-funded examples; execute the installed QPK netter, without mocks."""
from dataclasses import replace
from datetime import date
import math
import os
import unittest
import json
import copy
import pytest

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


requires_security_netting = pytest.mark.skipif(
    SecurityNettingSpec is None and os.environ.get("UES_REQUIRE_SECURITY_NETTING") != "1",
    reason="production QPK pin lacks research-only security netting",
)


def _synthetic_forward_state():
    from us_equity_strategies.research.rs04_netting_candidate import new_synthetic_r0_r3_state
    return new_synthetic_r0_r3_state(
        calendar=["2026-01-15", "2026-01-16", "2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23"],
        code_sha256="a" * 64, source_contract_sha256="b" * 64)


def _synthetic_forward_input(state, index, *, levered=.5, other=.2, boxx=.1, shock=0., boxx_return=0.):
    day = state["calendar"][index]
    profiles = {}
    for profile, unlevered in (("TQQQ", "QQQM"), ("SOXL", "SOXX")):
        profiles[profile] = {
            "full_target": {profile: levered, unlevered: other, "BOXX": boxx},
            "security_returns": {profile: shock, unlevered: 0., "BOXX": boxx_return},
        }
    return dict(schema_version="qsl.synthetic_r0_r3_snapshot.v1", scope="ARTIFICIAL_FIXTURE",
                input_id=f"artificial-{index}", revision=1, session=day,
                event_at=day+"T21:00:00+00:00", first_seen_at=day+"T21:01:00+00:00",
                persisted_at=day+"T21:02:00+00:00", decision_cutoff=day+"T21:03:00+00:00",
                code_sha256=state["code_sha256"], config_sha256=state["config_sha256"],
                source_contract_sha256=state["source_contract_sha256"], profiles=profiles)


def _synthetic_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _synthetic_step(state, payload):
    from us_equity_strategies.research.rs04_netting_candidate import step_synthetic_r0_r3
    return step_synthetic_r0_r3(state, _synthetic_bytes(payload), code_sha256="a"*64,
                               source_contract_sha256="b"*64)


@requires_security_netting
def test_synthetic_forward_pending_own_band_hand_math_and_endpoint(monkeypatch):
    import us_equity_strategies.research.rs04_netting_candidate as module
    monkeypatch.setattr(module, "run_registered_r0_r3_batch", lambda **kw: pytest.fail("historical batch called"))
    state = _synthetic_forward_state()
    first, _ = _synthetic_step(state, _synthetic_forward_input(state, 1, shock=.9, boxx_return=.4))
    assert len(first["books"]) == 4
    for book in first["books"].values():
        assert book["nav"] == book["cash"] == 1.
        assert book["fees"] == book["trade_events"] == 0
        assert book["pending"]["fill_session"] == "2026-01-20"
    second, _ = _synthetic_step(first, _synthetic_forward_input(first, 2, shock=.2, boxx_return=.1))
    for book in second["books"].values():
        assert book["nav"] == pytest.approx(.9996)
        assert book["cash"] == pytest.approx(.1996)
        assert book["fees"] == pytest.approx(.0004)
    prior_units = dict(second["books"]["TQQQ:R3"]["units"])
    third, receipt = _synthetic_step(second, _synthetic_forward_input(
        second, 3, levered=.55, other=.18, boxx=.1, shock=.2, boxx_return=.1))
    band = third["books"]["TQQQ:R3"]
    full = third["books"]["TQQQ:R0"]
    assert band["security_values"] == pytest.approx({"TQQQ": .6, "QQQM": .2, "BOXX": .11})
    assert band["units"] == prior_units  # HOLD is unchanged total-return units, no reweighting.
    assert band["fees"] == pytest.approx(.0004)
    assert band["pending"]["kind"] == "HOLD_POSITIONS"
    assert abs(.55-full["security_values"]["TQQQ"]/full["nav"]) > .01
    assert abs(.55-band["security_values"]["TQQQ"]/band["nav"]) < .01
    assert receipt["cells"]["TQQQ:R3"]["executed_fees"] == 0.
    assert receipt["cells"]["TQQQ:R0"]["executed_fees"] > 0.
    assert receipt["cells"]["TQQQ:R0"]["input_sha256"] == receipt["cells"]["TQQQ:R3"]["input_sha256"]
    assert all(sum(b["security_values"].values()) > 0 and b["pending"] for b in third["books"].values())
    assert state == _synthetic_forward_state()  # all transitions are non-mutating and paired atomically.


@requires_security_netting
def test_synthetic_forward_duplicate_is_exactly_once_even_after_later_inputs():
    state = _synthetic_forward_state(); p1 = _synthetic_forward_input(state, 1)
    first, r1 = _synthetic_step(state, p1)
    second, _ = _synthetic_step(first, _synthetic_forward_input(first, 2))
    duplicate, old_receipt = _synthetic_step(second, p1)
    assert duplicate == second and old_receipt == r1
    changed = copy.deepcopy(p1); changed["profiles"]["TQQQ"]["full_target"]["TQQQ"] = .51
    before = _synthetic_bytes(second)
    with pytest.raises(ValueError): _synthetic_step(second, changed)
    changed = copy.deepcopy(p1); changed["revision"] = 2
    with pytest.raises(ValueError): _synthetic_step(second, changed)
    assert _synthetic_bytes(second) == before


@requires_security_netting
def test_synthetic_forward_receipt_mutation_cannot_change_committed_pending():
    state = _synthetic_forward_state()
    first, receipt = _synthetic_step(state, _synthetic_forward_input(state, 1))
    before = _synthetic_bytes(first)
    receipt["cells"]["TQQQ:R3"]["next_pending"]["weights"]["TQQQ"] = .01
    assert _synthetic_bytes(first) == before


@requires_security_netting
def test_synthetic_forward_future_fill_snapshot_never_changes_prior_pending():
    state = _synthetic_forward_state()
    first, _ = _synthetic_step(state, _synthetic_forward_input(state, 1))
    before = _synthetic_bytes(first)
    up, _ = _synthetic_step(first, _synthetic_forward_input(first, 2, levered=.7, shock=3., boxx_return=.5))
    down, _ = _synthetic_step(first, _synthetic_forward_input(first, 2, levered=.1, shock=-.5, boxx_return=-.2))
    for name in up["books"]:
        assert up["books"][name]["security_values"] == down["books"][name]["security_values"]
        assert up["books"][name]["fees"] == down["books"][name]["fees"] == pytest.approx(.0004)
    assert _synthetic_bytes(first) == before


@requires_security_netting
def test_synthetic_forward_checkpoint_carrier_real_encoded_sizes(record_property):
    from quant_platform_kit.common.strategy_risk_state import (
        StrategyRiskStateIdentity, build_strategy_risk_state_transition)
    state = _synthetic_forward_state()
    for i in (1, 2, 3): state, _ = _synthetic_step(state, _synthetic_forward_input(state, i))
    four_size = len(_synthetic_bytes(state))
    record_property("four_book_canonical_bytes", four_size)
    # A capacity assessment, not an implemented pair codec or a saved checkpoint.
    for profile in ("TQQQ", "SOXL"):
        pair = copy.deepcopy(state)
        pair["books"] = {k:v for k,v in pair["books"].items() if k.startswith(profile+":")}
        for item in pair["inputs"]:
            item["receipt"]["cells"] = {k:v for k,v in item["receipt"]["cells"].items() if k.startswith(profile+":")}
        pair["pair_profile"] = profile
        size = len(_synthetic_bytes(pair)); record_property(profile+"_pair_canonical_bytes", size)
        identity = StrategyRiskStateIdentity("synthetic.r0r3", "synthetic.fixture", profile.lower(), "c"*64)
        if size <= 8192:
            transition = build_strategy_risk_state_transition(identity=identity, effective_session=state["completed_session"],
                input_sha256="d"*64, state=pair)
            assert len(transition.state_json.encode()) == size
        else:
            with pytest.raises(ValueError):
                build_strategy_risk_state_transition(identity=identity, effective_session=state["completed_session"],
                    input_sha256="d"*64, state=pair)
    assert len(state["inputs"]) == 3 and len(state["books"]) == 4


@pytest.mark.parametrize("mutation", ["late", "missing", "out_of_order", "code", "config", "source", "nonfinite", "unknown_field"])
def test_synthetic_forward_invalid_input_leaves_all_four_books_unchanged(mutation):
    state = _synthetic_forward_state(); payload = _synthetic_forward_input(state, 1)
    if mutation == "late": payload["persisted_at"] = payload["session"]+"T21:04:00+00:00"
    elif mutation == "missing": del payload["first_seen_at"]
    elif mutation == "out_of_order": payload = _synthetic_forward_input(state, 2)
    elif mutation in ("code", "config", "source"): payload[mutation+"_sha256" if mutation != "source" else "source_contract_sha256"] = "c"*64
    elif mutation == "nonfinite": payload["profiles"]["SOXL"]["security_returns"]["SOXL"] = float("nan")
    else: payload["broker"] = "not permitted"
    before = _synthetic_bytes(state)
    with pytest.raises(ValueError): _synthetic_step(state, payload)
    assert _synthetic_bytes(state) == before


@requires_security_netting
def test_synthetic_forward_fee_cash_failure_does_not_partially_commit_other_books():
    state = _synthetic_forward_state(); p = _synthetic_forward_input(state, 1)
    p["profiles"]["SOXL"]["full_target"] = {"SOXL": 1., "SOXX": 0., "BOXX": 0.}
    first, _ = _synthetic_step(state, p); before = _synthetic_bytes(first)
    with pytest.raises(ValueError, match="FEE_CASH_INSUFFICIENT"):
        _synthetic_step(first, _synthetic_forward_input(first, 2))
    assert _synthetic_bytes(first) == before


@requires_security_netting
def test_synthetic_forward_strict_codec_restore_continues_identically():
    from us_equity_strategies.research.rs04_netting_candidate import (
        encode_synthetic_r0_r3_state, decode_synthetic_r0_r3_state)
    state = _synthetic_forward_state()
    for i in (1, 2): state, _ = _synthetic_step(state, _synthetic_forward_input(state, i))
    wire = encode_synthetic_r0_r3_state(state)
    restored = decode_synthetic_r0_r3_state(wire, code_sha256="a"*64, source_contract_sha256="b"*64)
    p = _synthetic_forward_input(state, 3, shock=.2, boxx_return=.1)
    assert _synthetic_step(restored, p) == _synthetic_step(state, p)
    corrupt = bytearray(wire); corrupt[-2] ^= 1
    with pytest.raises(ValueError): decode_synthetic_r0_r3_state(bytes(corrupt), code_sha256="a"*64, source_contract_sha256="b"*64)
    with pytest.raises(ValueError): decode_synthetic_r0_r3_state(wire, code_sha256="c"*64, source_contract_sha256="b"*64)
    # Duplicate JSON keys and extra codec fields are not silently normalized.
    with pytest.raises(ValueError): decode_synthetic_r0_r3_state(b'{"body":{},"body":{},"sha256":"x"}', code_sha256="a"*64, source_contract_sha256="b"*64)


@requires_security_netting
def test_synthetic_forward_memory_commit_unknown_and_serialized_boundary_recovery():
    from us_equity_strategies.research.rs04_netting_candidate import (
        SyntheticR0R3MemorySession, encode_synthetic_r0_r3_state)
    state = _synthetic_forward_state(); original = encode_synthetic_r0_r3_state(state)
    session = SyntheticR0R3MemorySession(original, code_sha256="a"*64, source_contract_sha256="b"*64)
    prepared, receipt = session.prepare(_synthetic_bytes(_synthetic_forward_input(state, 1)))
    assert session.state_bytes == original and session.status == "PREPARED"
    # Simulated crash before commit: authoritative readback is original bytes.
    session.mark_unknown()
    restored = SyntheticR0R3MemorySession.from_bytes(session.to_bytes(), code_sha256="a"*64, source_contract_sha256="b"*64)
    with pytest.raises(ValueError): restored.prepare(_synthetic_bytes(_synthetic_forward_input(state, 2)))
    with pytest.raises(ValueError): restored.resolve_readback(b"corrupt")
    assert restored.status == "UNKNOWN"
    restored.resolve_readback(original)
    assert restored.status == "READY" and restored.state_bytes == original
    same_prepared, same_receipt = restored.prepare(_synthetic_bytes(_synthetic_forward_input(state, 1)))
    assert same_prepared == prepared and same_receipt == receipt
    # Simulated crash after commit, before acknowledgement: readback equals prepared bundle.
    restored.mark_unknown()
    restored = SyntheticR0R3MemorySession.from_bytes(restored.to_bytes(), code_sha256="a"*64, source_contract_sha256="b"*64)
    restored.resolve_readback(prepared)
    assert restored.state_bytes == prepared and restored.status == "READY"
    duplicate, duplicate_receipt = restored.prepare(_synthetic_bytes(_synthetic_forward_input(state, 1)))
    assert duplicate == prepared and duplicate_receipt == receipt and restored.status == "READY"


@requires_security_netting
def test_synthetic_forward_existing_local_carrier_pair_atomicity_capacity_and_restore(tmp_path, record_property):
    import us_equity_strategies.research.rs04_netting_candidate as module
    from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateStore, StrategyRiskStateIdentity
    state = _synthetic_forward_state(); root = tmp_path/"checkpoint_fixture_SYNTHETIC_ONLY"
    module.save_synthetic_r0_r3_checkpoint(state, fixture_root=root)
    memory = module.SyntheticR0R3MemorySession(module.encode_synthetic_r0_r3_state(state),
        code_sha256="a"*64, source_contract_sha256="b"*64)
    for index in (1, 2, 3):
        expected, _ = _synthetic_step(state, _synthetic_forward_input(state, index))
        memory.prepare(_synthetic_bytes(_synthetic_forward_input(state, index)))
        memory.commit_local_checkpoint(fixture_root=root)
        state = expected
        readback = module.load_synthetic_r0_r3_checkpoint(fixture_root=root,
            config_sha256=state["config_sha256"], code_sha256="a"*64, source_contract_sha256="b"*64)
        assert readback == module.encode_synthetic_r0_r3_state(expected) == memory.state_bytes
    stores = []
    for profile in ("TQQQ", "SOXL"):
        store = StrategyRiskStateStore(local_dir=root, namespace="synthetic.r0r3.prototype")
        identity = StrategyRiskStateIdentity("synthetic.r0r3", "synthetic.fixture", profile.lower(), state["config_sha256"])
        chain = store.load_chain(identity)
        assert len(chain) == 4 and set(chain[-1].state["state"]["books"]) == {profile+":R0", profile+":R3"}
        assert len(chain[-1].state["state"]["inputs"]) == 3  # no dedup/provenance pruning to fit.
        record_property(profile+"_actual_carrier_state_bytes", len(chain[-1].state_json.encode()))
        assert len(chain[-1].state_json.encode()) <= 8192
        stores.append((store, identity))
    module.save_synthetic_r0_r3_checkpoint(state, fixture_root=root)  # exact duplicate does not append.
    assert all(len(store.load_chain(identity)) == 4 for store,identity in stores)
    oversized, _ = _synthetic_step(state, _synthetic_forward_input(state, 4))
    with pytest.raises(ValueError, match="maximum serialized size"):
        module.save_synthetic_r0_r3_checkpoint(oversized, fixture_root=root)
    assert all(len(store.load_chain(identity)) == 4 for store,identity in stores)  # both preflight before either append.
    assert module.load_synthetic_r0_r3_checkpoint(fixture_root=root, config_sha256=state["config_sha256"],
        code_sha256="a"*64, source_contract_sha256="b"*64) == memory.state_bytes


@requires_security_netting
def test_synthetic_forward_existing_carrier_partial_profile_unknown_stops_new_id(tmp_path, monkeypatch):
    import us_equity_strategies.research.rs04_netting_candidate as module
    from quant_platform_kit.common.strategy_risk_state import StrategyRiskStateStore
    state = _synthetic_forward_state(); root = tmp_path/"checkpoint_fixture_SYNTHETIC_ONLY"
    original = module.encode_synthetic_r0_r3_state(state)
    module.save_synthetic_r0_r3_checkpoint(state, fixture_root=root)
    memory = module.SyntheticR0R3MemorySession(original, code_sha256="a"*64, source_contract_sha256="b"*64)
    memory.prepare(_synthetic_bytes(_synthetic_forward_input(state, 1)))
    append = StrategyRiskStateStore.append
    def fail_second_profile(self, transition):
        if transition.identity.candidate_id == "soxl":
            raise OSError("artificial second-profile write failure")
        return append(self, transition)
    monkeypatch.setattr(StrategyRiskStateStore, "append", fail_second_profile)
    with pytest.raises(OSError): memory.commit_local_checkpoint(fixture_root=root)
    assert memory.status == "UNKNOWN" and memory.state_bytes == original
    with pytest.raises(ValueError, match="PARTIAL_UNKNOWN"):
        module.load_synthetic_r0_r3_checkpoint(fixture_root=root, config_sha256=state["config_sha256"],
            code_sha256="a"*64, source_contract_sha256="b"*64)
    with pytest.raises(ValueError, match="UNRESOLVED"):
        memory.prepare(_synthetic_bytes(_synthetic_forward_input(state, 2)))
    with pytest.raises(ValueError): module.save_synthetic_r0_r3_checkpoint(state, fixture_root=root)


@requires_security_netting
def test_synthetic_forward_existing_carrier_after_write_unknown_readback_and_corruption(tmp_path, monkeypatch):
    import us_equity_strategies.research.rs04_netting_candidate as module
    state = _synthetic_forward_state(); root = tmp_path/"checkpoint_fixture_SYNTHETIC_ONLY"
    module.save_synthetic_r0_r3_checkpoint(state, fixture_root=root)
    memory = module.SyntheticR0R3MemorySession(module.encode_synthetic_r0_r3_state(state),
        code_sha256="a"*64, source_contract_sha256="b"*64)
    proposed, _ = memory.prepare(_synthetic_bytes(_synthetic_forward_input(state, 1)))
    load = module.load_synthetic_r0_r3_checkpoint; calls = 0
    def fail_after_commit(**kwargs):
        nonlocal calls
        calls += 1
        if calls > 1: raise OSError("artificial committed-but-readback-unknown")
        return load(**kwargs)
    monkeypatch.setattr(module, "load_synthetic_r0_r3_checkpoint", fail_after_commit)
    with pytest.raises(OSError): memory.commit_local_checkpoint(fixture_root=root)
    assert memory.status == "UNKNOWN"
    monkeypatch.setattr(module, "load_synthetic_r0_r3_checkpoint", load)
    recovered = load(fixture_root=root, config_sha256=state["config_sha256"], code_sha256="a"*64, source_contract_sha256="b"*64)
    assert recovered == proposed
    memory.resolve_readback(recovered)
    assert memory.status == "READY" and memory.state_bytes == proposed
    file = next(root.rglob("*.json")); file.write_text("artificial corrupted fixture")
    with pytest.raises(ValueError):
        load(fixture_root=root, config_sha256=state["config_sha256"], code_sha256="a"*64, source_contract_sha256="b"*64)


@requires_security_netting
def test_synthetic_bundle_file_checkpoint_keeps_complete_four_book_state_above_8k(tmp_path, record_property):
    import us_equity_strategies.research.rs04_netting_candidate as module
    state = _synthetic_forward_state(); root = tmp_path/"checkpoint_fixture_SYNTHETIC_ONLY"
    module.save_synthetic_r0_r3_file_checkpoint(state, fixture_root=root)
    for index in (1, 2, 3, 4):
        state, _ = _synthetic_step(state, _synthetic_forward_input(state, index))
        module.save_synthetic_r0_r3_file_checkpoint(state, fixture_root=root)
    encoded = module.encode_synthetic_r0_r3_state(state)
    record_property("four_step_full_checkpoint_bytes", len(encoded))
    assert len(encoded) > 8192
    readback = module.load_synthetic_r0_r3_file_checkpoint(fixture_root=root,
        config_sha256=state["config_sha256"], code_sha256="a"*64, source_contract_sha256="b"*64)
    assert readback == encoded
    restored = module.decode_synthetic_r0_r3_state(readback, code_sha256="a"*64, source_contract_sha256="b"*64)
    assert len(restored["inputs"]) == 4 and len(restored["books"]) == 4
    assert all(b["pending"] and sum(b["security_values"].values()) > 0 for b in restored["books"].values())
    before = {p.name:p.read_bytes() for p in root.glob("checkpoint.v*.json")}
    module.save_synthetic_r0_r3_file_checkpoint(state, fixture_root=root)
    assert {p.name:p.read_bytes() for p in root.glob("checkpoint.v*.json")} == before
    with pytest.raises(ValueError): module.save_synthetic_r0_r3_file_checkpoint(_synthetic_forward_state(), fixture_root=root)


@requires_security_netting
@pytest.mark.parametrize("failure", ["before_link", "after_link", "readback"])
def test_synthetic_bundle_file_checkpoint_unknown_same_id_restart_single_fee(tmp_path, monkeypatch, failure, record_property):
    import os
    import us_equity_strategies.research.rs04_netting_candidate as module
    state = _synthetic_forward_state(); root = tmp_path/"checkpoint_fixture_SYNTHETIC_ONLY"
    module.save_synthetic_r0_r3_file_checkpoint(state, fixture_root=root)
    state, _ = _synthetic_step(state, _synthetic_forward_input(state, 1))
    module.save_synthetic_r0_r3_file_checkpoint(state, fixture_root=root)
    original = module.encode_synthetic_r0_r3_state(state)
    memory = module.SyntheticR0R3MemorySession(original, code_sha256="a"*64, source_contract_sha256="b"*64)
    payload = _synthetic_bytes(_synthetic_forward_input(state, 2))
    proposed, _ = memory.prepare(payload)
    link = os.link; read = module._synthetic_file_read_head; count = 0
    if failure in ("before_link", "after_link"):
        def fail_link(source, target, *args, **kwargs):
            # This injection point is AFTER temporary flush/fsync, with complete bytes.
            assert Path(source).read_bytes() == proposed
            record_property("temporary_write_complete_before_failure", True)
            if failure == "after_link": link(source, target, *args, **kwargs)
            raise OSError("artificial commit response lost")
        from pathlib import Path
        monkeypatch.setattr(os, "link", fail_link)
    else:
        def fail_read(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2: raise OSError("artificial readback failure after commit")
            return read(*args, **kwargs)
        monkeypatch.setattr(module, "_synthetic_file_read_head", fail_read)
    with pytest.raises(OSError): memory.commit_local_checkpoint(fixture_root=root, carrier="bundle_file")
    assert memory.status == "UNKNOWN" and memory.state_bytes == original
    with pytest.raises(ValueError, match="UNRESOLVED"):
        memory.prepare(_synthetic_bytes(_synthetic_forward_input(state, 3)))
    memory = module.SyntheticR0R3MemorySession.from_bytes(memory.to_bytes(),
        code_sha256="a"*64, source_contract_sha256="b"*64)
    monkeypatch.setattr(os, "link", link); monkeypatch.setattr(module, "_synthetic_file_read_head", read)
    readback = module.load_synthetic_r0_r3_file_checkpoint(fixture_root=root,
        config_sha256=state["config_sha256"], code_sha256="a"*64, source_contract_sha256="b"*64)
    memory.resolve_readback(readback)
    if failure == "before_link":
        assert readback == original
        memory.prepare(payload)  # exact original ID/content only, after definite old-state readback.
        memory.commit_local_checkpoint(fixture_root=root, carrier="bundle_file")
    assert memory.state_bytes == proposed
    restarted = module.SyntheticR0R3MemorySession(memory.state_bytes, code_sha256="a"*64, source_contract_sha256="b"*64)
    duplicate, _ = restarted.prepare(payload)
    assert duplicate == proposed and restarted.status == "READY"
    decoded = module.decode_synthetic_r0_r3_state(duplicate, code_sha256="a"*64, source_contract_sha256="b"*64)
    assert all(b["fees"] == pytest.approx(.0004) for b in decoded["books"].values())
    assert decoded["version"] == 2 and len(list(root.glob("checkpoint.v*.json"))) == 3


@requires_security_netting
@pytest.mark.parametrize("corruption", ["bytes", "missing_parent", "conflicting_version"])
def test_synthetic_bundle_file_checkpoint_corrupt_or_conflicting_chain_parks(tmp_path, corruption):
    import us_equity_strategies.research.rs04_netting_candidate as module
    state = _synthetic_forward_state(); root = tmp_path/"checkpoint_fixture_SYNTHETIC_ONLY"
    module.save_synthetic_r0_r3_file_checkpoint(state, fixture_root=root)
    state, _ = _synthetic_step(state, _synthetic_forward_input(state, 1))
    module.save_synthetic_r0_r3_file_checkpoint(state, fixture_root=root)
    files = sorted(root.glob("checkpoint.v*.json"))
    if corruption == "bytes": files[-1].write_text("corrupt artificial fixture")
    elif corruption == "missing_parent": files[0].unlink()
    else: (root/("checkpoint.v00000001."+"f"*64+".json")).write_bytes(files[-1].read_bytes())
    with pytest.raises(ValueError): module.load_synthetic_r0_r3_file_checkpoint(fixture_root=root,
        config_sha256=state["config_sha256"], code_sha256="a"*64, source_contract_sha256="b"*64)


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


# R0-R3 engineering fixtures: artificial bars, original strategy generators,
# actual native netter/fees, one preregistered batch, no real market-page reads.
def r0_r3_real_metadata_fixture():
    """Frozen public metadata shape only; no raw market observations."""
    metadata = {'schema_version': 'qsl.research.raw_sip_input.v1',
        'source': 'alpaca.stocks.bars.v2_and_corporate_actions.v1', 'feed': 'sip',
        'price_adjustment': 'raw', 'calendar': 'XNYS', 'timezone': 'America/New_York',
        'currency': 'USD', 'license': 'user_attested_non_commercial_private_research',
        'window_start': '2021-12-01', 'window_end': '2026-08-25',
        'historical_pit_attested': False, 'historical_context': 'NOT_BOUND',
        'live_config_snapshot': 'LIVE_CONFIG_URI_UNAVAILABLE', 'research_only': True, 'no_order': True}
    action_counts = {'SOXL': 16, 'SOXX': 21, 'BOXX': 1, 'QQQ': 21, 'TQQQ': 18, 'QQQM': 19}
    return {'source_packet_sha256': '150f6d6b5318f7dbbcf171b9c76fde567a349ee1eed6b49c184d1f33c9e35124',
        'source_verified_objects': 13, 'source_verified_bytes': 780174,
        'manifest_metadata': metadata, 'actual_raw_bodies_not_included': True,
        'groups': [{'symbol': s, 'kind': kind,
            'count': (917 if s == 'BOXX' else 1187) if kind == 'bars' else action_counts[s],
            'complete_pagination': True, 'pages': 1}
            for s in action_counts for kind in ('bars', 'actions')]}


def r0_r3_fixture(tmp_path):
    import hashlib
    import json
    from pathlib import Path
    from datetime import timedelta
    from dataclasses import asdict
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
    from us_equity_strategies.research.partial_rebalance import SecurityPolicy, RetrospectivePolicyContext
    from us_equity_strategies.research.rs04_netting_candidate import _r0_r3_sha

    days = []
    d = date(2023, 1, 2)
    while len(days) < 600:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    symbols = ('TQQQ', 'QQQ', 'QQQM', 'SOXL', 'SOXX', 'BOXX')
    bars = {}
    for j, symbol in enumerate(symbols):
        price, raw = 100., []
        for i, day in enumerate(days):
            # Independent oscillations avoid a singular two-core training Sigma.
            r = (.0001 if symbol == 'BOXX' else .0003 + .008 * math.sin(i * (.37 + j*.071)))
            price *= 1 + r
            if i == 50:
                price /= 2
            raw.append([day.isoformat(), price * .9999, price])
        bars[symbol] = raw
    body = {'sessions': [d.isoformat() for d in days], 'bars': bars,
        'actions': {s: {'splits': [[days[50].isoformat(), 2.]],
                        'dividends': [[days[70].isoformat(), .01]]} for s in symbols}}
    identity = hashlib.sha256(b'artificial-R0-R3-identity-packet-v1').hexdigest()
    metadata = {'schema_version': 'qsl.research.raw_sip_input.v1',
        'source': 'alpaca.stocks.bars.v2_and_corporate_actions.v1', 'feed': 'sip',
        'price_adjustment': 'raw', 'calendar': 'XNYS', 'timezone': 'America/New_York',
        'currency': 'USD', 'license': 'user_attested_non_commercial_private_research',
        'window_start': days[0].isoformat(), 'window_end': days[-1].isoformat(),
        'historical_pit_attested': False, 'historical_context': 'NOT_BOUND',
        'live_config_snapshot': 'LIVE_CONFIG_URI_UNAVAILABLE', 'research_only': True, 'no_order': True}
    projection = {'source_packet_sha256': identity, 'source_verified_objects': 13,
        'source_verified_bytes': len(json.dumps(body).encode()), 'manifest_metadata': metadata,
        'actual_raw_bodies_not_included': True, 'groups': [
            {'symbol': s, 'kind': kind, 'count': 600 if kind == 'bars' else 2,
             'complete_pagination': True, 'pages': 1} for s in symbols for kind in ('bars', 'actions')]}
    root = Path(__file__).resolve().parents[2]
    import quant_platform_kit
    implementation = {'qpk_package_source': hashlib.sha256(
        Path(quant_platform_kit.__file__).read_bytes()).hexdigest()}
    for rel in ('research/rs04_netting_candidate.py', 'research/security_netting_runner.py',
                'research/partial_rebalance.py', 'research/kelly_core_return_generator.py',
                'research/portfolio_kelly_walk_forward.py', 'research/bayesian_kelly.py',
                'research/tqqq_trial_journal.py', 'manifests/__init__.py',
                'strategies/tqqq_growth_income.py', 'strategies/soxl_soxx_trend_income.py'):
        implementation[rel] = hashlib.sha256((root/'src/us_equity_strategies'/rel).read_bytes()).hexdigest()
    policies, configs, last = {}, {}, {}
    for key, sleeve in (('TQQQ', 'QQQM'), ('SOXL', 'SOXX')):
        context = RetrospectivePolicyContext(
            (SecurityPolicy(key, True, 3., key, .99), SecurityPolicy(sleeve, True, 1., key, .99),
             SecurityPolicy('BOXX', False, 1., 'BILL', .99)), 3., ((key, 3.),), (),
            'fixture-original-no-plugin-policy', 'fixture-classification', True,
            'DECISION_TIME_TARGET', None, 'RISK_BUY_WEIGHT', 'NAV_FRACTION',
            'RS04:' + key, 'fixture-original-no-plugin-policy', _r0_r3_sha(implementation), '0'*64,
            identity, 'ARTIFICIAL_FIXTURE')
        rules = {d.isoformat(): {'mandatory_reduce': False, 'forced_target': None,
            'forced_source': None, 'forced_reason': None, 'vetoed_symbols': []} for d in days[500:-1]}
        rules[days[-2].isoformat()] = {'mandatory_reduce': True,
            'forced_target': {key:0.,sleeve:0.,'BOXX':0.},
            'forced_source': context.source, 'forced_reason':'fixture-original-terminal-risk-reduction',
            'vetoed_symbols': []}
        config = {'scope': context.scope, 'exact_source': context.exact_source, 'outer_f': .8,
            'scope_cap': .8, 'scope_cap_source': context.source,
            'hard_policy': {k: v for k, v in asdict(context).items()
                            if k not in RetrospectivePolicyContext.__annotations__}, 'decision_rules': rules}
        configs[key] = json.loads(json.dumps(config))
        policies[key] = replace(context, config_sha256=_r0_r3_sha(configs[key]))
        last[key] = CloseTarget(days[-2], days[-1], {key: 0., sleeve: 0., 'BOXX': 0.})
    args = dict(projection=projection, identity_packet_sha256=identity, dataset_class='ARTIFICIAL_FIXTURE',
        input_body_sha256=_r0_r3_sha(body), implementation_sha256=implementation, policies=policies,
        original_scope_caps={'TQQQ': .8, 'SOXL': .8},
        original_scope_cap_source='fixture-original-no-plugin-policy',
        original_outer_f={'TQQQ': .8, 'SOXL': .8}, original_policy_configs=configs,
        last_close_targets=last, requested_start=days[500], requested_end=days[-1],
        source_revision='fixture-source-revision', trial_id='fixture-batch',
        store=PerformanceStore(local_root=tmp_path/'journal'), load_input_body=lambda: body)
    return args, body, days


def r0_r3_positive_risk_prices(body):
    """Separate artificial positive-return case; BOXX/actions/cash/caps unchanged.

    The original oscillating case stays in the zero-f refusal regressions. This
    manufactured risk-price path is engineering input, never historical policy
    or evidence of model performance.
    """
    import copy
    result = copy.deepcopy(body)
    for j, symbol in enumerate(('TQQQ', 'QQQ', 'QQQM', 'SOXL', 'SOXX')):
        splits = dict(result['actions'][symbol]['splits'])
        price = 100.
        for i, row in enumerate(result['bars'][symbol]):
            price *= 1 + .002 + .0003*math.sin(i*(.37+j*.071))
            price /= splits.get(row[0], 1.)
            row[1], row[2] = price*.9999, price
    return result


def r0_r3_positive_fixture(tmp_path):
    """Explicit alternate artificial body, with original .99 caps and5bps."""
    import hashlib
    import json
    from us_equity_strategies.research.rs04_netting_candidate import _r0_r3_sha
    args, original, days = r0_r3_fixture(tmp_path)
    body = r0_r3_positive_risk_prices(original)
    identity = hashlib.sha256(b'artificial-positive-risk-path-engineering-v1').hexdigest()
    projection = args['projection'] | {'source_packet_sha256': identity,
        'source_verified_bytes': len(json.dumps(body).encode())}
    policies = {k: replace(v, source_identity=identity) for k,v in args['policies'].items()}
    return args | {'projection': projection, 'identity_packet_sha256': identity,
        'policies': policies, 'input_body_sha256': _r0_r3_sha(body),
        'source_revision': 'ARTIFICIAL_POSITIVE_RISK_PATH_ENGINEERING_ONLY',
        'load_input_body': lambda: body}, body, days


@unittest.skipIf(SecurityNettingSpec is None, 'requires the explicit QPK research overlay')
class R0R3CallerEngineeringTests(unittest.TestCase):
    # unittest supplies no tmp_path; each test's root lives only in harness OUT.
    def fixture(self):
        import tempfile
        from pathlib import Path
        return r0_r3_positive_fixture(Path(tempfile.mkdtemp(prefix='r0-r3-positive-')))

    def test_complete_batch_native_fees_training_registration_and_resume(self):
        from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch, R0_R3_ROWS
        from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialStatus
        args, body, days = self.fixture()
        original_args, original_body, _ = r0_r3_fixture(args['store'].local_root.parent/'comparison')
        self.assertEqual(body['bars']['BOXX'], original_body['bars']['BOXX'])
        self.assertEqual(body['actions'], original_body['actions'])
        self.assertEqual(args['original_scope_caps'], original_args['original_scope_caps'])
        self.assertEqual(args['original_outer_f'], original_args['original_outer_f'])
        self.assertEqual(args['original_policy_configs'], original_args['original_policy_configs'])
        loaded = []
        def load():
            record = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', args['trial_id'])
            self.assertIs(record.status, ResearchTrialStatus.STARTED)
            loaded.append(True)
            return body
        args['load_input_body'] = load
        result = run_registered_r0_r3_batch(**args)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(loaded, [True])
        self.assertEqual(len(result['subresults']), 8)
        engineering = result['engineering']
        train = engineering['training']
        self.assertEqual(train['sessions'], [d.isoformat() for d in days[500:563]])
        self.assertEqual(train['count'], 63)
        self.assertEqual(train['covariance_fits'], 1)
        self.assertEqual(train['posterior_training_observations'], 0)
        self.assertEqual(engineering['evaluation_sessions'], [d.isoformat() for d in days[563:]])
        self.assertEqual([d['n'] for d in engineering['model_decisions']], list(range(37)))
        self.assertEqual(engineering['model_decisions'][0]['status'], 'PRIOR_ONLY')
        self.assertEqual(engineering['checkpoint']['sigma'], train['sigma'])
        total_fees = 0.
        for cell, packet in engineering['cells'].items():
            profile = cell.split(':')[1]
            self.assertEqual(engineering['opening_books'][profile]['f'], 0.)
            self.assertEqual(packet['initial_nav'], 1.)
            self.assertEqual(packet['ledger'][0]['security_start'], dict.fromkeys(packet['spec']['bps_per_side'], 0.))
            for row, decision in zip(packet['ledger'], packet['rs04_research']['decisions'], strict=True):
                fees = sum(abs(v)*.0005 for v in row['net_trades'].values())
                self.assertAlmostEqual(row['fees'], fees, places=13)
                self.assertAlmostEqual(row['nav_end'], row['nav_pre_trade']-fees, places=12)
                self.assertGreaterEqual(min(row['virtual_cash_end'].values()), 0.)
                self.assertEqual(decision['decision']['scope'], 'RETROSPECTIVE_RESEARCH_ONLY')
                self.assertTrue(decision['original_policy']['synthetic_only'])
                total_fees += fees
            for previous, decision in zip(packet['ledger'], packet['rs04_research']['decisions'][1:]):
                self.assertEqual(decision['snapshot']['nav'], previous['nav_end'])
                self.assertEqual(dict(decision['snapshot']['security_values']), previous['security_end'])
            self.assertEqual(packet['rs04_research']['decisions'][-1]['decision']['reason'], 'MANDATORY_REDUCTION')
            self.assertEqual(packet['ledger'][-1]['security_end'], dict.fromkeys(packet['spec']['bps_per_side'], 0.))
            mode = packet['rs04_research']['mode']
            self.assertEqual(mode, 'PROPORTIONAL' if cell.startswith(R0_R3_ROWS[2]) else
                             'BAND_HOLD' if cell.startswith(R0_R3_ROWS[3]) else 'FULL')
            self.assertIsNone(packet['rs04_research']['historical_available_at'])
            ledger = result['subresults'][cell]
            self.assertEqual(ledger['initial_nav'], 1.)
            self.assertEqual(ledger['initial_cash'], 1.)
        self.assertGreater(total_fees, 0.)
        self.assertEqual(len(engineering['cells']), 8)  # R1 has no duplicated control
        resumed = run_registered_r0_r3_batch(**(args | {'load_input_body': lambda: self.fail('recomputed terminal')}))
        self.assertTrue(resumed['resumed_without_compute'])
        self.assertEqual(result['subresults'], resumed['subresults'])
        self.assertEqual(loaded, [True])
        # A changed frozen parameter cannot reuse this immutable identity.
        with self.assertRaises(ValueError):
            run_registered_r0_r3_batch(**(args | {'original_outer_f': {'TQQQ': .7, 'SOXL': .8}}))

    def test_metadata_source_raw_actions_pagination_and_exact_real_identity(self):
        import copy
        import json
        from us_equity_strategies.research.rs04_netting_candidate import admit_r0_r3_source_metadata
        args, _, _ = self.fixture()
        projection = args['projection']
        admitted = admit_r0_r3_source_metadata(projection, identity_packet_sha256=args['identity_packet_sha256'],
                                                dataset_class='ARTIFICIAL_FIXTURE')
        self.assertEqual(admitted['manifest_metadata']['price_adjustment'], 'raw')
        for key, bad in [('schema_version', 'v2'), ('price_adjustment', 'all'),
                         ('historical_pit_attested', True), ('calendar', 'UNKNOWN')]:
            with self.subTest(key=key):
                p = copy.deepcopy(projection); p['manifest_metadata'][key] = bad
                with self.assertRaises(ValueError):
                    admit_r0_r3_source_metadata(p, identity_packet_sha256=args['identity_packet_sha256'], dataset_class='ARTIFICIAL_FIXTURE')
        for field, value in [('complete_pagination', False), ('count', -1), ('pages', 0)]:
            p = copy.deepcopy(projection); p['groups'][0][field] = value
            with self.assertRaises(ValueError):
                admit_r0_r3_source_metadata(p, identity_packet_sha256=args['identity_packet_sha256'], dataset_class='ARTIFICIAL_FIXTURE')
        real = r0_r3_real_metadata_fixture()
        safe = admit_r0_r3_source_metadata(real, identity_packet_sha256=real['source_packet_sha256'], dataset_class='REAL_ARCHIVED_RAW_ACTIONS')
        self.assertNotIn('source_packet', safe)
        self.assertEqual(safe['dataset_class'], 'REAL_ARCHIVED_RAW_ACTIONS')
        self.assertEqual(sum(g['count'] for g in safe['groups'] if g['kind']=='bars'), 6852)

    def test_missing_policy_and_real_source_binding_are_registered_before_body(self):
        from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch
        args, _, _ = self.fixture()
        for label, changes, reason in [
            ('policy', {'policies': None}, 'UNAVAILABLE_ORIGINAL_POLICY'),
            ('postfee', {'policies': {k: replace(v, cap_basis='ACTUAL_POST_FEE') for k,v in args['policies'].items()}}, 'UNAVAILABLE_ORIGINAL_POLICY'),
            ('configdigest', {'policies': {k: replace(v, config_sha256='a'*64) for k,v in args['policies'].items()}}, 'UNAVAILABLE_EXACT_ORIGINAL_POLICY'),
            ('boundedturnover', {'policies': {k: replace(v, max_one_way_risk_turnover=.5) for k,v in args['policies'].items()}}, 'UNAVAILABLE_ORIGINAL_POLICY'),
        ]:
            with self.subTest(label=label):
                opts = args | changes | {'trial_id': label, 'load_input_body': lambda: self.fail('body before refusal')}
                with self.assertRaisesRegex(ValueError, reason):
                    run_registered_r0_r3_batch(**opts)
                record = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', label)
                self.assertEqual(record.status.value, 'rejected')
                again = run_registered_r0_r3_batch(**opts)
                self.assertTrue(again['resumed_without_compute'])
                self.assertEqual(again['status'], 'rejected')
        real = r0_r3_real_metadata_fixture()
        with self.assertRaisesRegex(ValueError, 'UNAVAILABLE_ORIGINAL_POLICY_BINDING_OR_BASIS'):
            run_registered_r0_r3_batch(**(args | {'trial_id':'real-refused','projection':real,
                'identity_packet_sha256':real['source_packet_sha256'], 'dataset_class':'REAL_ARCHIVED_RAW_ACTIONS',
                'load_input_body':lambda:self.fail('real body opened')}))
        record = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', 'real-refused')
        self.assertFalse(record.synthetic)
        self.assertEqual(record.status.value, 'rejected')

    def test_real_admission_computes_using_artificial_body_only_and_keeps_real_labels(self):
        """Exercise the real-contract branch with manufactured prices/actions.

        No archived body is opened. Packet identity metadata only is reproduced;
        the generated input and source_revision explicitly identify this as a test.
        """
        import json
        from dataclasses import asdict
        from datetime import timedelta
        from us_equity_strategies.research.rs04_netting_candidate import (
            _r0_r3_sha, run_registered_r0_r3_batch)
        args, _, _ = self.fixture()
        real = r0_r3_real_metadata_fixture()
        first, inception, end = date(2021, 12, 1), date(2022, 12, 28), date(2026, 8, 25)
        candidates, d = [], first
        while d <= end:
            if d.weekday() < 5:
                candidates.append(d)
            d += timedelta(days=1)
        # Artificial declared calendar; this is not a reconstruction of XNYS.
        before = [d for d in candidates if d < inception]
        after = [d for d in candidates if d >= inception]
        # Explicit deterministic removal avoids using any market calendar/data.
        def fixed_dates(seq, count, keep):
            discard = set(d for d in seq if d not in keep)
            discard = set(sorted(discard)[:len(seq)-count])
            return [d for d in seq if d not in discard]
        days = fixed_dates(before, 270, {first}) + fixed_dates(after, 917, {inception, date(2023, 3, 28), end})
        symbols = ('TQQQ', 'QQQ', 'QQQM', 'SOXL', 'SOXX', 'BOXX')
        bars, actions = {}, {}
        counts = {g['symbol']: g['count'] for g in real['groups'] if g['kind'] == 'actions'}
        for j, symbol in enumerate(symbols):
            dates = days[270:] if symbol == 'BOXX' else days
            price, raw = 100., []
            for i, day in enumerate(dates):
                price *= 1 + (.0001 if symbol == 'BOXX' else .0003 + .008*math.sin(i*(.37+j*.071)))
                raw.append([day.isoformat(), price*.9999, price])
            bars[symbol] = raw
            actions[symbol] = {'splits': [], 'dividends': [[day.isoformat(), .01] for day in dates[20:20+counts[symbol]]]}
        body = {'sessions': [d.isoformat() for d in days], 'bars': bars, 'actions': actions}
        policies, configs, terminal = {}, {}, {}
        for key, old in args['policies'].items():
            context = replace(old, synthetic_only=False, dataset_class='REAL_ARCHIVED_RAW_ACTIONS',
                              source_identity=real['source_packet_sha256'])
            config = dict(args['original_policy_configs'][key])
            config['hard_policy'] = {k: v for k, v in asdict(context).items()
                                    if k not in type(context).__annotations__}
            config['decision_rules'] = {d.isoformat(): {'mandatory_reduce': False,
                'forced_target': None, 'forced_source': None, 'forced_reason': None,
                'vetoed_symbols': []} for d in days[:-1] if d >= date(2023, 3, 28)}
            config['decision_rules'][days[-2].isoformat()] = {'mandatory_reduce': True,
                'forced_target': {spec.symbol: 0. for spec in context.securities},
                'forced_source': context.source, 'forced_reason': 'artificial-test-terminal-reduction',
                'vetoed_symbols': []}
            configs[key] = json.loads(json.dumps(config))
            policies[key] = replace(context, config_sha256=_r0_r3_sha(configs[key]))
            terminal[key] = CloseTarget(days[-2], days[-1], {spec.symbol: 0. for spec in context.securities})
        loaded = []
        def artificial_input():
            record = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1',
                                                       'artificial-body-real-contract')
            self.assertEqual(record.status.value, 'started')
            self.assertFalse(record.synthetic)
            loaded.append(True)
            return body
        request = args | {'projection': real,
            'identity_packet_sha256': real['source_packet_sha256'], 'dataset_class': 'REAL_ARCHIVED_RAW_ACTIONS',
            'policies': policies, 'original_policy_configs': configs, 'last_close_targets': terminal,
            'requested_start': date(2023, 3, 28), 'requested_end': end,
            'input_body_sha256': _r0_r3_sha(body), 'load_input_body': artificial_input,
            'source_revision': 'ARTIFICIAL_BODY_TEST_ONLY_NO_REAL_SOURCE_READ',
            'trial_id': 'artificial-body-real-contract'}
        with self.assertRaisesRegex(ValueError, 'HARD_CAP_SYMBOL'):
            run_registered_r0_r3_batch(**(request | {
                'trial_id': 'original-oscillating-real-shape-policy-refused',
                'load_input_body': lambda: body}))
        refused = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1',
            'original-oscillating-real-shape-policy-refused')
        self.assertEqual(refused.status.value, 'rejected')
        self.assertFalse(refused.synthetic)
        original_boxx = [list(row) for row in body['bars']['BOXX']]
        body = r0_r3_positive_risk_prices(body)
        self.assertEqual(body['bars']['BOXX'], original_boxx)
        result = run_registered_r0_r3_batch(**(request | {
            'input_body_sha256': _r0_r3_sha(body),
            'source_revision': 'ARTIFICIAL_POSITIVE_RISK_PATH_REAL_CONTRACT_TEST_ONLY'}))
        self.assertEqual(loaded, [True])
        self.assertEqual(result['status'], 'succeeded')
        self.assertFalse(result['trial']['synthetic'])
        self.assertEqual(len(result['subresults']), 8)
        self.assertTrue(all(not cell['synthetic'] for cell in result['subresults'].values()))
        for packet in result['engineering']['cells'].values():
            self.assertEqual(packet['rs04_research']['historical_available_at'], None)
            self.assertEqual(packet['rs04_research']['PIT'], 'NOT_ATTESTED')
            for decision in packet['rs04_research']['decisions']:
                self.assertFalse(decision['original_policy']['synthetic_only'])
        stored = args['store'].load_research_ledger('us_equity', 'r0_r3_batch_additive_audit_v1',
            'artificial-body-real-contract', 'artificial-body-real-contract', 1)
        self.assertFalse(stored.synthetic)

    def test_failed_rejected_aborted_and_unknown_started_never_recompute(self):
        from unittest.mock import patch
        from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
        from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch
        args, _, _ = self.fixture()
        for error, status in [(RuntimeError('fixture_failure'), 'failed'),
                              (ValueError('fixture_refusal'), 'rejected'), (KeyboardInterrupt(), 'aborted')]:
            opts = args | {'trial_id': status}
            def load():
                raise error
            with self.assertRaises(type(error)):
                run_registered_r0_r3_batch(**(opts | {'load_input_body':load}))
            record = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', status)
            self.assertEqual(record.status.value, status)
            result = run_registered_r0_r3_batch(**(opts | {'load_input_body':lambda:self.fail('replay failure')}))
            self.assertEqual(result['status'], status)
        original = PerformanceStore.save_research_trial
        def ambiguous(store, record):
            original(store, record)
            raise OSError('fixture-write-response-unknown')
        opts = args | {'trial_id':'ambiguous-start','load_input_body':lambda:self.fail('unknown start computed')}
        with patch.object(PerformanceStore, 'save_research_trial', ambiguous):
            with self.assertRaisesRegex(OSError, 'unknown'):
                run_registered_r0_r3_batch(**opts)
        result = run_registered_r0_r3_batch(**opts)
        self.assertEqual(result['status'], 'started')
        self.assertTrue(result['resumed_without_compute'])

    def test_input_schema_action_counts_and_digest_not_silently_repaired(self):
        import copy
        from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch, _r0_r3_sha
        args, body, days = self.fixture()
        with self.assertRaisesRegex(ValueError, 'UNAVAILABLE_TRAINING_PREFIX_OR_EVALUATION'):
            run_registered_r0_r3_batch(**(args | {'trial_id':'insufficient-prefix','requested_start':days[-20]}))
        for kind in ('digest', 'actions', 'bars'):
            broken = copy.deepcopy(body)
            if kind == 'actions': broken['actions']['QQQ']['splits'] = []
            elif kind == 'bars': broken['bars']['SOXL'].pop()
            else: broken['bars']['QQQ'][0][2] *= 1.1
            options = args | {'trial_id':kind,'load_input_body':lambda:broken}
            if kind != 'digest': options['input_body_sha256'] = _r0_r3_sha(broken)
            with self.assertRaisesRegex(ValueError, 'UNAVAILABLE_'):
                run_registered_r0_r3_batch(**options)

    def test_original_generators_future_tail_and_training_sigma_are_causal(self):
        import copy
        from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch, _r0_r3_sha
        from us_equity_strategies.research.portfolio_kelly_walk_forward import ledoit_wolf
        from us_equity_strategies.research.kelly_core_return_generator import generate_soxl_core_path, generate_tqqq_core_path
        from unittest.mock import patch
        args, body, _ = self.fixture()
        # Wrap original numerical implementations; native running/fees are never mocked.
        with patch('us_equity_strategies.research.portfolio_kelly_walk_forward.ledoit_wolf', wraps=ledoit_wolf) as fit, \
             patch('us_equity_strategies.research.kelly_core_return_generator.generate_soxl_core_path', wraps=generate_soxl_core_path) as soxl, \
             patch('us_equity_strategies.research.kelly_core_return_generator.generate_tqqq_core_path', wraps=generate_tqqq_core_path) as tqqq:
            a = run_registered_r0_r3_batch(**args)['engineering']
            self.assertEqual(fit.call_count, 1)
            self.assertEqual([len(c) for c in fit.call_args.args[0]], [63,63])
            self.assertEqual((soxl.call_count, tqqq.call_count), (1,1))
            self.assertEqual(soxl.call_args.kwargs['trade_start'], args['requested_start'])
            self.assertEqual(tqqq.call_args.kwargs['trade_start'], args['requested_start'])
        changed = copy.deepcopy(body)
        for rows in changed['bars'].values():
            for row in rows[-2:]: row[1] *= 1.03; row[2] *= 1.03
        b = run_registered_r0_r3_batch(**(args | {'trial_id':'tail-change',
            'input_body_sha256':_r0_r3_sha(changed),'load_input_body':lambda:changed}))['engineering']
        self.assertEqual(a['training'], b['training'])
        self.assertEqual(a['model_decisions'][:-1], b['model_decisions'][:-1])
        for key in a['cells']:
            self.assertEqual(a['cells'][key]['ledger'][:-2], b['cells'][key]['ledger'][:-2])


@unittest.skipIf(SecurityNettingSpec is None, 'requires the explicit QPK research overlay')
class R0R3ZeroFractionRegressionTests(unittest.TestCase):
    def original_fixture(self):
        import tempfile
        from pathlib import Path
        return r0_r3_fixture(Path(tempfile.mkdtemp(prefix='zero-f-original-')))

    def run_captured(self, args, label):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as caller
        from us_equity_strategies.research import bayesian_kelly as b4
        captured = {'models': [], 'policies': []}
        native, size = caller.run_offline_security_netting, b4.b4_fractional_target
        def record_size(moments, **kwargs):
            result = size(moments, **kwargs)
            captured['models'].append({'n': moments.n, 'status': result.status, 'targets': result.targets})
            return result
        def record_native(rows, **kwargs):
            policy = kwargs['rs04_research_policy']
            captured['policies'].append({'profile': policy.profile, 'mode': policy.mode,
                'full_targets': [dict(d.full_target) for d in policy.decisions],
                'mandatory': [d.mandatory_reduce for d in policy.decisions],
                'symbol_caps': {s.symbol: s.max_weight for s in policy.decisions[0].hard_policy_context.securities}})
            return native(rows, **kwargs)
        try:
            with patch.object(caller, 'run_offline_security_netting', record_native), \
                 patch.object(b4, 'b4_fractional_target', record_size):
                with self.assertRaisesRegex(ValueError, 'HARD_CAP_SYMBOL'):
                    caller.run_registered_r0_r3_batch(**args)
        finally:
            (Path(tempfile.gettempdir())/(label+'.json')).write_text(json.dumps(captured, indent=2)+'\n')
        terminal = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', args['trial_id'])
        self.assertEqual(terminal.status.value, 'rejected')
        return captured

    def test_explicit_original_zero_f_retains_native_boxx_and_cap_refusal(self):
        import copy
        from us_equity_strategies.research.rs04_netting_candidate import _r0_r3_sha
        from quant_platform_kit.research_stats.security_netting import compose_security_targets
        args, _, _ = self.original_fixture()
        configs = copy.deepcopy(args['original_policy_configs'])
        configs['TQQQ']['outer_f'] = 0.
        args = args | {'original_outer_f': {'TQQQ': 0., 'SOXL': .8}, 'original_policy_configs': configs,
            'policies': {k: replace(v, config_sha256=_r0_r3_sha(configs[k])) for k, v in args['policies'].items()},
            'trial_id': 'original-zero-f-must-refuse-cap'}
        capture = self.run_captured(args, 'EXPLICIT_ZERO_F')
        first = capture['policies'][0]
        spec = SecurityNettingSpec(bps_per_side={s: 5. for s in ('TQQQ','QQQM','BOXX')},
            outer_cash_mode='merge_into_security', outer_cash_symbol='BOXX', cash_like_symbols=('BOXX',))
        expected, _ = compose_security_targets(f=0., inner_weights={'TQQQ': .8, 'QQQM': 0., 'BOXX': .1}, spec=spec)
        self.assertEqual(first['full_targets'][0], expected)
        self.assertEqual(first['symbol_caps']['BOXX'], .99)
        self.assertEqual(expected['BOXX'], 1.)
        self.assertFalse(first['mandatory'][0])

    def test_available_b4_zero_is_not_prior_only_and_retains_native_boxx(self):
        args, _, _ = self.original_fixture()
        capture = self.run_captured(args, 'B4_AVAILABLE_ZERO')
        policy = capture['policies'][1]
        self.assertEqual(policy['profile'], 'TQQQ')
        self.assertEqual(capture['models'][0]['status'], 'PRIOR_ONLY')
        self.assertIsNone(capture['models'][0]['targets'])
        self.assertEqual(policy['full_targets'][0], {'BOXX': 0., 'TQQQ': 0., 'QQQM': 0.})
        zeros = [(i, m) for i, m in enumerate(capture['models']) if m['status'] == 'AVAILABLE' and m['targets'][0] == 0.]
        self.assertTrue(zeros)
        for i, model in zeros:
            self.assertGreater(model['n'], 0)
            self.assertEqual(policy['full_targets'][i], {'BOXX': 1., 'TQQQ': 0., 'QQQM': 0.})
            self.assertFalse(policy['mandatory'][i])
        self.assertEqual(policy['symbol_caps']['BOXX'], .99)


@unittest.skipIf(SecurityNettingSpec is None, 'requires the explicit QPK research overlay')
class RootACoreOnlyBatchTests(unittest.TestCase):
    def fixture(self):
        import tempfile
        from pathlib import Path
        args, body, days = r0_r3_fixture(Path(tempfile.mkdtemp(prefix='root-a-core-')))
        return args | {'policies': None, 'original_scope_caps': None,
            'original_scope_cap_source': None, 'original_policy_configs': None,
            'original_outer_f': {'TQQQ': 1., 'SOXL': 1.},
            'core_only_source_binding': ROOT_A_CORE_ONLY_BINDING,
            'trial_id': 'root-a-fixed-partial-batch'}, body, days

    def test_no_b4_cap_runs_six_cells_and_preregisters_two_unavailable(self):
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as caller
        args, body, days = self.fixture()
        loaded = []
        def load():
            row = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', args['trial_id'])
            self.assertEqual(row.status.value, 'started')
            self.assertEqual(row.actual_params['active_rows'], ['R0_ORIGINAL_FULL','R2_B5_HALF_ONLY','R3_B5_BAND_ONLY'])
            self.assertIsNone(row.actual_params['original_scope_caps'])
            self.assertEqual(len(row.actual_params['unavailable_cells']), 2)
            loaded.append(True)
            return body
        with patch('us_equity_strategies.research.portfolio_kelly_walk_forward.ledoit_wolf', side_effect=AssertionError('R1 must not fit')), \
             patch('us_equity_strategies.research.bayesian_kelly.initialise_b4', side_effect=AssertionError('R1 must not initialise')), \
             patch('us_equity_strategies.research.bayesian_kelly.b4_fractional_target', side_effect=AssertionError('R1 must not emit')):
            result = caller.run_registered_r0_r3_batch(**(args | {'load_input_body':load}))
        self.assertEqual(result['status'], 'PARTIAL_COMPLETED_R1_UNAVAILABLE')
        self.assertFalse(result['whole_comparison_complete'])
        self.assertEqual(result['terminal_target_role'],
            'EXPLICIT_ZERO_MODEL_BOUNDARY_NOT_ACTUAL_STRATEGY_TERMINAL')
        self.assertEqual(len(result['subresults']), 6)
        self.assertEqual(set(result['unavailable_cells']), {'R1_B4_ONLY:TQQQ','R1_B4_ONLY:SOXL'})
        self.assertEqual(loaded, [True])
        self.assertEqual(result['engineering']['evaluation_sessions'], [d.isoformat() for d in days[563:]])
        self.assertEqual(result['engineering']['training']['count'], 63)
        self.assertEqual(result['engineering']['training']['covariance_fits'], 0)
        self.assertIsNone(result['engineering']['training']['sigma'])
        self.assertEqual(result['engineering']['model_decisions'], [])
        self.assertIsNone(result['engineering']['checkpoint'])
        resumed = caller.run_registered_r0_r3_batch(**(args | {'load_input_body':lambda:self.fail('partial batch recomputed')}))
        self.assertTrue(resumed['resumed_without_compute'])
        self.assertEqual(resumed['subresults'], result['subresults'])
        self.assertEqual(resumed['unavailable_cells'], result['unavailable_cells'])
        with self.assertRaises(ValueError):
            caller.run_registered_r0_r3_batch(**(args | {'original_scope_caps':{'TQQQ':.97,'SOXL':1.},
                'load_input_body':lambda:self.fail('later cap silently reran batch')}))


    def test_core_targets_native_fees_and_partial_book_semantics(self):
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as caller
        from us_equity_strategies.research import kelly_core_return_generator as gen
        args, _, days = self.fixture()
        paths = {}
        def wrap(key, fn):
            def invoke(*a, **kw):
                paths[key] = fn(*a, **kw)
                return paths[key]
            return invoke
        with patch.object(gen, 'generate_tqqq_core_path', wrap('TQQQ', gen.generate_tqqq_core_path)), \
             patch.object(gen, 'generate_soxl_core_path', wrap('SOXL', gen.generate_soxl_core_path)):
            result = caller.run_registered_r0_r3_batch(**args)
        for cell, packet in result['engineering']['cells'].items():
            row_id, profile = cell.split(':')
            previous = None
            for j, (row, receipt) in enumerate(zip(packet['ledger'], packet['rs04_research']['decisions'], strict=True)):
                expected = dict(paths[profile].weights[564+j]) if j < 36 else args['last_close_targets'][profile].weights
                expected = {k: expected.get(k, 0.) for k in row['security_start']}
                self.assertEqual(receipt['full_target'], expected)
                self.assertFalse(receipt['mandatory_reduce'])
                self.assertNotIn('securities', receipt['original_policy'])
                self.assertNotIn('max_effective_risk_exposure', receipt['original_policy'])
                self.assertEqual(receipt['original_policy']['source_mapping_sha256'], ROOT_A_CORE_ONLY_BINDING['mapping_sha256'])
                self.assertAlmostEqual(row['fees'], sum(abs(v)*.0005 for v in row['net_trades'].values()), places=13)
                self.assertAlmostEqual(row['nav_end'], row['nav_pre_trade']-row['fees'], places=12)
                self.assertGreaterEqual(min(row['virtual_cash_end'].values()), 0.)
                if previous is not None:
                    self.assertEqual(receipt['snapshot']['nav'], previous['nav_end'])
                    self.assertEqual(receipt['snapshot']['security_values'], previous['security_end'])
                if row_id == 'R2_B5_HALF_ONLY':
                    current = {k: v/receipt['snapshot']['nav'] for k,v in receipt['snapshot']['security_values'].items()}
                    for k,v in receipt['decision']['target_weights']:
                        self.assertAlmostEqual(v, (current[k]+expected[k])*.5, places=13)
                if row_id == 'R3_B5_BAND_ONLY':
                    current = {k:v/receipt['snapshot']['nav'] for k,v in receipt['snapshot']['security_values'].items()}
                    within = all(abs(current[k]-expected[k]) <= .01 for k in current)
                    self.assertEqual(receipt['decision']['kind'] == 'HOLD_POSITIONS', within)
                    if within:
                        self.assertEqual(row['fees'], 0.)
                        self.assertTrue(all(v == 0. for v in row['net_trades'].values()))
                previous = row
        ledger = args['store'].load_research_ledger('us_equity', 'r0_r3_batch_additive_audit_v1',
            args['trial_id'], args['trial_id'], 1)
        self.assertEqual(ledger.initial_nav, 6.)
        self.assertEqual(ledger.initial_cash, 6.)
        self.assertEqual(len(result['subresults']), 6)
        self.assertEqual(result['engineering']['evaluation_sessions'][0], days[563].isoformat())

    def test_enabled_unknown_or_wrong_source_config_refuses_before_body(self):
        import copy
        from us_equity_strategies.research import rs04_netting_candidate as caller
        args, _, _ = self.fixture()
        wrong = copy.deepcopy(ROOT_A_CORE_ONLY_BINDING)
        wrong['config_sha256'] = '0'*64
        enabled = ROOT_A_CORE_ONLY_BINDING | {'market_regime_control_enabled': True}
        source = copy.deepcopy(ROOT_A_CORE_ONLY_BINDING)
        source['source_sha256']['manifests/__init__.py'] = '0'*64
        for label, binding in [('config', wrong), ('enabled', enabled), ('source', source)]:
            with self.subTest(label=label):
                options = args | {'core_only_source_binding': binding, 'trial_id':label,
                    'load_input_body':lambda:self.fail('unadmitted source read body')}
                with self.assertRaisesRegex(ValueError, 'SOURCE_CONFIG_BINDING'):
                    caller.run_registered_r0_r3_batch(**options)
                terminal = args['store'].load_research_trial('us_equity','r0_r3_batch_additive_audit_v1',label)
                self.assertEqual(terminal.status.value, 'rejected')
                self.assertEqual(caller.run_registered_r0_r3_batch(**options)['status'], 'rejected')
        changed = dict(args['implementation_sha256']); changed['research/rs04_netting_candidate.py'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'ADAPTER_IDENTITY'):
            caller.run_registered_r0_r3_batch(**(args | {'implementation_sha256':changed,
                'trial_id':'wrong-adapter','load_input_body':lambda:self.fail('bad adapter read body')}))

    def archive_registration(self, args, body):
        import json
        import hashlib
        import tempfile
        from pathlib import Path
        from us_equity_strategies.research.rs04_netting_candidate import r0_r3_parser_identity
        root = Path(tempfile.mkdtemp(prefix='artificial-source13-'))
        inputs, hashes = [], {}
        for symbol in ('TQQQ','QQQ','QQQM','SOXL','SOXX','BOXX'):
            for kind in ('bars','actions'):
                path = root/kind/symbol/'page-001.json';path.parent.mkdir(parents=True)
                if kind == 'bars':
                    payload = {'symbol':symbol,'bars':[{'t':d+'T00:00:00Z','o':o,'c':c} for d,o,c in body['bars'][symbol]],'next_page_token':None}
                else:
                    ca = body['actions'][symbol]
                    payload = {'symbol':symbol,'corporate_actions':{
                        'forward_splits':[{'ex_date':d,'old_rate':1.,'new_rate':v} for d,v in ca['splits']],
                        'cash_dividends':[{'symbol':symbol,'ex_date':d,'rate':v} for d,v in ca['dividends']]},'next_page_token':None}
                raw = json.dumps(payload).encode();path.write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
                hashes[f'{kind}/{symbol}/page-001.json'] = digest
                inputs.append({'symbol':symbol,'kind':kind,'complete_pagination':True,'pages':[{'bytes':len(raw),'sha256':digest}]})
        raw = json.dumps({'inputs':inputs}).encode();(root/'manifest.json').write_bytes(raw)
        hashes['manifest.json'] = hashlib.sha256(raw).hexdigest()
        return root, {'source_packet_sha256':args['identity_packet_sha256'],
            'manifest_sha256':hashes['manifest.json'],'object_sha256':hashes,'parser_sha256':r0_r3_parser_identity()}

    def test_source_objects_register_before_parser_without_preknown_body_hash(self):
        from us_equity_strategies.research import rs04_netting_candidate as caller
        args, body, _ = self.fixture()
        raw_root, registration = self.archive_registration(args, body)
        called = []
        def parse():
            started = args['store'].load_research_trial('us_equity','r0_r3_batch_additive_audit_v1',args['trial_id'])
            self.assertEqual(started.status.value, 'started')
            self.assertIsNone(started.actual_params['input_body_sha256'])
            self.assertIsNone(started.actual_params['last_close_targets'])
            self.assertEqual(started.actual_params['last_close_target_rule'],
                'SOURCE_LAST_TWO_EVALUATION_SESSIONS_ZERO_SECURITY_TARGET')
            self.assertEqual(started.actual_params['input_source_registration'], registration)
            self.assertEqual(started.input_id, 'sha256:'+caller._r0_r3_sha(registration))
            called.append(True)
            return raw_root
        options = args | {'input_body_sha256':None,'input_source_registration':registration,
            'last_close_targets':None,'load_input_body':None,'load_source_root':parse}
        result = caller.run_registered_r0_r3_batch(**options)
        self.assertEqual(called, [True])
        self.assertEqual(result['derived_input_body_sha256'], caller._r0_r3_sha(body))
        for opening in result['engineering']['opening_books'].values():
            self.assertEqual(opening['last_close_target']['fill_session'], body['sessions'][-1])
            self.assertEqual(opening['last_close_target']['decision_session'], body['sessions'][-2])
            self.assertTrue(all(v == 0. for v in opening['last_close_target']['weights'].values()))
        self.assertIn(caller._r0_r3_sha(body), result['trial']['run_id'])
        self.assertIsNone(result['trial']['actual_params']['input_body_sha256'])
        resumed = caller.run_registered_r0_r3_batch(**(options | {'load_source_root':lambda:self.fail('parsed twice')}))
        self.assertEqual(resumed['derived_input_body_sha256'], result['derived_input_body_sha256'])
        self.assertEqual(resumed['subresults'], result['subresults'])
        self.assertEqual(len(result['subresults']), 6)
        self.assertFalse(result['whole_comparison_complete'])

    def test_wrong_source_or_parser_version_and_receipt_fail_closed(self):
        import copy
        from us_equity_strategies.research import rs04_netting_candidate as caller
        args, body, _ = self.fixture()
        _, registration = self.archive_registration(args, body)
        for field in ('parser_sha256','source_packet_sha256','manifest_sha256'):
            with self.subTest(field=field):
                wrong = registration | {field:'0'*64}
                with self.assertRaisesRegex(ValueError, 'SOURCE_OR_PARSER_REGISTRATION'):
                    caller.run_registered_r0_r3_batch(**(args | {'input_body_sha256':None,
                        'input_source_registration':wrong,'trial_id':field,
                        'load_input_body':lambda:self.fail('wrong source/parser callback')}))
        wrong = copy.deepcopy(registration);wrong['object_sha256']['bars/TQQQ/page-001.json']='0'*64
        with self.assertRaisesRegex(ValueError, 'REGISTERED_ARCHIVE_ROOT_CALLBACK_REQUIRED'):
            caller.run_registered_r0_r3_batch(**(args | {'input_body_sha256':None,
                'input_source_registration':registration,'trial_id':'wrong-receipt',
                'load_input_body':lambda:{'body':body,'source_registration':wrong}}))


    def test_actual_object_hash_failure_is_rejected_and_never_reparsed(self):
        from us_equity_strategies.research import rs04_netting_candidate as caller
        args, body, _ = self.fixture()
        raw_root, registration = self.archive_registration(args, body)
        page = raw_root/'bars/TQQQ/page-001.json'
        page.write_bytes(page.read_bytes()+b' ')
        parsed = []
        def parse():
            parsed.append(True)
            row = args['store'].load_research_trial('us_equity','r0_r3_batch_additive_audit_v1',args['trial_id'])
            self.assertEqual(row.status.value, 'started')
            return raw_root
        options = args | {'input_body_sha256':None,'input_source_registration':registration,
            'load_input_body':None,'load_source_root':parse}
        with self.assertRaisesRegex(ValueError, 'ARCHIVE_PAGE_MISMATCH'):
            caller.run_registered_r0_r3_batch(**options)
        self.assertEqual(parsed, [True])
        again = caller.run_registered_r0_r3_batch(**(options | {'load_source_root':lambda:self.fail('bad source reparsed')}))
        self.assertEqual(again['status'],'rejected')
        self.assertTrue(again['resumed_without_compute'])
        self.assertEqual(parsed, [True])


ROOT_A_CORE_ONLY_BINDING = {'mapping_sha256': '8bb756d55f24166f0e7ddd583eb389049f79a03176fa0eb8e82944570a59c9d5', 'source_sha256': {'manifests/__init__.py': '8c22b5c7b2d1df95cdad7f3d90573523462fc8d38f69b8ddb8f9f49969f7db30', 'research/kelly_core_return_generator.py': '37ad7ebe19d4ad9d6ff8c77294a084e7cd703a34105fa8ce011f0bddf9b35671', 'strategies/tqqq_growth_income.py': '90e8ded53a2e4389f6060d88cfd232046d90e6775dda47379e8d06415b2cbf42', 'strategies/tqqq_dual_drive_core.py': '1868f4298c1228b458b9b4b6904ff6bf3cf1dcacd46fcf4e469a6f968ee6f211', 'strategies/soxl_soxx_trend_income.py': '9675f56e9ea0d4be36751b52ec22951ddc29e2546e305a1e5b1aab5c6d60baf4', 'income_layer.py': '33fb7b447fcf31b97fce49e498ee57459632c474033267f04fe8e24cb18208b4', 'volatility_delever_retention.py': '148743f0bea5285ec98aca19072c528cb4435c5b52cbc5271b4347ce4fa30aef', 'research/fractional_kelly_walk_forward.py': '7e5892d59fd9c1243a5433b4c87b616d0805ed14130d24d65850bbfc8734ec74'}, 'config_sha256': '651f8f342e42a5c94f0dcebb2a4381ced9fb127acf86c1be7dbabb8ea72bc8b1'}


class R0R3SameDayDividendAdmissionTests(unittest.TestCase):
    """Artificial action-only admission; intercept both strategy generators."""

    def fixture(self, *, splits=(), dividends=()):
        days = ('2020-01-02', '2020-01-03', '2020-01-06')
        symbols = ('BOXX', 'QQQ', 'QQQM', 'SOXL', 'SOXX', 'TQQQ')
        body = {'sessions': list(days),
            'bars': {s: [[d, 100., 100.] for d in days] for s in symbols},
            'actions': {s: {'splits': [], 'dividends': []} for s in symbols}}
        body['actions']['QQQ'] = {'splits': [list(x) for x in splits],
                                  'dividends': [list(x) for x in dividends]}
        source = {'manifest_metadata': {'window_start': days[0], 'window_end': days[-1]},
            'groups': [{'symbol': s, 'kind': kind,
                        'count': len(body['bars'][s]) if kind == 'bars' else
                        sum(len(v) for v in body['actions'][s].values())}
                       for s in symbols for kind in ('bars', 'actions')]}
        return body, source

    def generate(self, body, source):
        from unittest.mock import patch
        from us_equity_strategies.research.rs04_netting_candidate import _r0_r3_generate
        module = 'us_equity_strategies.research.kelly_core_return_generator.'
        with patch(module + 'generate_tqqq_core_path', return_value='artificial-tqqq') as tqqq, \
                patch(module + 'generate_soxl_core_path', return_value='artificial-soxl') as soxl:
            result = _r0_r3_generate(body, source, trade_start=date(2020, 1, 2))
        return result, tqqq, soxl

    def test_same_day_dividends_reach_original_sum_without_normalizing(self):
        from us_equity_strategies.research.kelly_core_return_generator import RawBar, total_return_series
        body, source = self.fixture(dividends=(('2020-01-03', 1.), ('2020-01-03', 2.)))
        result, tqqq, soxl = self.generate(body, source)
        self.assertEqual(result[0], {'TQQQ': 'artificial-tqqq', 'SOXL': 'artificial-soxl'})
        self.assertEqual(tqqq.call_count, 1)
        self.assertEqual(soxl.call_count, 1)
        actions = tqqq.call_args.args[1]['QQQ']
        self.assertEqual(actions.dividends, ((date(2020, 1, 3), 1.), (date(2020, 1, 3), 2.)))
        self.assertEqual(soxl.call_args.args[1]['QQQ'], actions)
        self.assertEqual(len(body['actions']['QQQ']['dividends']), 2)
        bars = tuple(RawBar(d, 100., 100.) for d in result[1])
        returns = total_return_series(bars, actions, result[1])
        self.assertAlmostEqual(returns[1], .03)
        self.assertEqual(returns[2], 0.)

    def test_unique_and_same_day_zero_dividends_are_valid(self):
        for dividends in ((('2020-01-03', 1.), ('2020-01-06', 2.)),
                          (('2020-01-03', 0.), ('2020-01-03', 2.))):
            with self.subTest(dividends=dividends):
                body, source = self.fixture(dividends=dividends)
                _, tqqq, soxl = self.generate(body, source)
                self.assertEqual(tqqq.call_count, 1)
                self.assertEqual(soxl.call_count, 1)
                self.assertEqual(len(tqqq.call_args.args[1]['QQQ'].dividends), 2)

    def test_existing_negative_cases_still_reject_before_generators(self):
        from unittest.mock import patch
        from us_equity_strategies.research.rs04_netting_candidate import _r0_r3_generate
        cases = (
            ('duplicate_split', {'splits': (('2020-01-03', 2.), ('2020-01-03', 3.))}, 'UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE'),
            ('descending_dividend', {'dividends': (('2020-01-06', 1.), ('2020-01-03', 2.))}, 'UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE'),
            ('descending_split', {'splits': (('2020-01-06', 2.), ('2020-01-03', 3.))}, 'UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE'),
            ('negative_dividend', {'dividends': (('2020-01-03', -1.),)}, 'UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE'),
            ('zero_split', {'splits': (('2020-01-03', 0.),)}, 'UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE'),
            ('negative_split', {'splits': (('2020-01-03', -1.),)}, 'UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE'),
            ('nonfinite_dividend', {'dividends': (('2020-01-03', float('inf')),)}, 'INVALID_RAW_ACTION'),
            ('nonfinite_split', {'splits': (('2020-01-03', float('nan')),)}, 'INVALID_RAW_ACTION'),
            ('boolean_dividend', {'dividends': (('2020-01-03', True),)}, 'INVALID_RAW_ACTION'),
            ('outside_calendar', {'dividends': (('2020-01-04', 1.),)}, 'UNAVAILABLE_RAW_ACTION_ORDER_OR_VALUE'),
        )
        module = 'us_equity_strategies.research.kelly_core_return_generator.'
        for label, actions, code in cases:
            with self.subTest(case=label), patch(module + 'generate_tqqq_core_path') as tqqq, \
                    patch(module + 'generate_soxl_core_path') as soxl:
                body, source = self.fixture(**actions)
                with self.assertRaisesRegex(ValueError, code):
                    _r0_r3_generate(body, source, trade_start=date(2020, 1, 2))
                tqqq.assert_not_called()
                soxl.assert_not_called()

    def test_same_day_records_still_bound_by_registered_count(self):
        body, source = self.fixture(dividends=(('2020-01-03', 1.), ('2020-01-03', 2.)))
        next(g for g in source['groups'] if g['symbol'] == 'QQQ' and g['kind'] == 'actions')['count'] = 1
        with self.assertRaisesRegex(ValueError, 'UNAVAILABLE_INPUT_COUNTS_OR_ACTION_SCHEMA'):
            self.generate(body, source)


@unittest.skipIf(SecurityNettingSpec is None, 'requires the explicit QPK research overlay')
class QQQPairSensitivityTests(unittest.TestCase):
    def fixture(self, *, effect=False):
        base = RootACoreOnlyBatchTests()
        args, body, days = base.fixture()
        j = 300 if effect else 100
        body['actions']['QQQ'] = {'splits': [], 'dividends': [
            [days[i].isoformat(), 1. if effect and i == j else 0.]
            for i in (10, 20, 30, j, j, j + 50)]}
        if effect:
            args['requested_start'] = days[430]
            for i, row in enumerate(body['bars']['QQQ']):
                price = 100. if i < j else 99.
                if i == 428:
                    price = 99.001
                row[1:] = [price, price]
        for group in args['projection']['groups']:
            if group['symbol'] == 'QQQ' and group['kind'] == 'actions':
                group['count'] = 6
        raw_root, registration = base.archive_registration(args, body)
        from us_equity_strategies.research import rs04_netting_candidate as caller
        options = {key: args[key] for key in ('projection', 'identity_packet_sha256',
            'dataset_class', 'implementation_sha256', 'core_only_source_binding',
            'requested_start', 'requested_end', 'source_revision', 'store')}
        options.update(input_source_registration=registration, batch_id='artificial-pair-ab',
            load_source_root=lambda: raw_root,
            transform_sha256=caller.qqq_pair_transform_identity())
        return options, body

    def test_projection_has_independent_identity_and_honest_source_counts(self):
        from us_equity_strategies.research import rs04_netting_candidate as caller
        options, body = self.fixture()
        original = caller._r0_r3_sha(body)
        a, pa = caller._qqq_pair_projection(body, 'A_TWO_RECORDS')
        b, pb = caller._qqq_pair_projection(body, 'B_ONE_EVENT_ASSUMPTION')
        self.assertEqual(caller._r0_r3_sha(body), original)
        self.assertEqual(a, body)
        self.assertEqual(pa['raw_counts']['QQQ:actions'], pb['raw_counts']['QQQ:actions'])
        self.assertEqual(pa['derived_counts']['QQQ:actions'], 6)
        self.assertEqual(pb['derived_counts']['QQQ:actions'], 5)
        self.assertNotEqual(pa['derived_body_sha256'], pb['derived_body_sha256'])
        self.assertEqual(a['bars'], b['bars'])
        self.assertEqual(a['sessions'], b['sessions'])
        self.assertEqual(next(g for g in options['projection']['groups'] if g['symbol'] == 'QQQ' and g['kind'] == 'actions')['count'], 6)

    def test_two_started_before_single_parse_no_difference_skips_ledgers(self):
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as caller
        options, _ = self.fixture()
        root_callback = options['load_source_root']
        gates = []
        def source_root():
            for suffix in ('-A-double', '-B-single'):
                trial = options['store'].load_research_trial('us_equity',
                    'r0_r3_qqq_pair_sensitivity_v1', options['batch_id'] + suffix)
                self.assertEqual(trial.status.value, 'started')
                self.assertEqual(trial.actual_params['input_semantics'], 'HYPOTHETICAL_QQQ_PAIR_SENSITIVITY')
                self.assertEqual(next(g for g in trial.actual_params['source']['groups']
                    if g['symbol'] == 'QQQ' and g['kind'] == 'actions')['count'], 6)
            gates.append(True)
            return root_callback()
        with patch.object(caller, 'parse_registered_r0_r3_archive', wraps=caller.parse_registered_r0_r3_archive) as parser, \
                patch.object(caller, 'run_offline_security_netting', side_effect=AssertionError('no ledger needed')):
            result = caller.run_registered_qqq_pair_sensitivity(**(options | {'load_source_root': source_root}))
        self.assertEqual(gates, [True])
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(result['status'], 'COMPLETED_NO_RELEVANT_DIFFERENCE_NO_LEDGER')
        self.assertEqual(result['ledger_cells_executed'], 0)
        self.assertTrue(result['comparison']['calendar_equal'])
        self.assertEqual({v['status'] for v in result['trials'].values()}, {'aborted'})
        with self.assertRaisesRegex(ValueError, 'SENSITIVITY_TRIAL_ALREADY_EXISTS'):
            caller.run_registered_qqq_pair_sensitivity(**(options | {'load_source_root': lambda: self.fail('duplicate raw read')}))

    def test_evaluation_difference_runs_only_affected_profile_original_ledgers(self):
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as caller
        options, _ = self.fixture(effect=True)
        with patch('us_equity_strategies.research.bayesian_kelly.initialise_b4', side_effect=AssertionError('no B4')), \
                patch.object(caller, 'parse_registered_r0_r3_archive', wraps=caller.parse_registered_r0_r3_archive) as parser:
            result = caller.run_registered_qqq_pair_sensitivity(**options)
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(result['status'], 'COMPLETED_HYPOTHETICAL_SENSITIVITY_LEDGERS')
        self.assertEqual(result['comparison']['changed_profiles'], ['TQQQ'])
        self.assertEqual(result['ledger_cells_executed'], 6)
        self.assertEqual({v['status'] for v in result['trials'].values()}, {'succeeded'})
        self.assertNotEqual(result['trials']['A_TWO_RECORDS']['run_id'], result['trials']['B_ONE_EVENT_ASSUMPTION']['run_id'])
        for value in result['engineering'].values():
            self.assertEqual(len(value['cells']), 3)
            self.assertEqual(value['training']['covariance_fits'], 0)
            self.assertEqual(value['opening_books']['TQQQ']['nav'], 1.)
            self.assertEqual(value['opening_books']['TQQQ']['cash'], 1.)
            for packet in value['cells'].values():
                self.assertTrue(all(v == 5. for v in packet['spec']['bps_per_side'].values()))

    def test_unknown_transform_refuses_before_registration_and_source_read(self):
        from us_equity_strategies.research import rs04_netting_candidate as caller
        options, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, 'SENSITIVITY_TRANSFORM_IDENTITY'):
            caller.run_registered_qqq_pair_sensitivity(**(options | {'transform_sha256': '0' * 64,
                'load_source_root': lambda: self.fail('wrong transform read source')}))
        for suffix in ('-A-double', '-B-single'):
            self.assertIsNone(options['store'].load_research_trial('us_equity',
                'r0_r3_qqq_pair_sensitivity_v1', options['batch_id'] + suffix))

    def test_exact_evaluation_gate_uses_reversal_not_unused_history_or_visibility(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from us_equity_strategies.research import rs04_netting_candidate as caller
        days = tuple(date(2001, 1, i) for i in range(1, 10))
        path = SimpleNamespace(weights=tuple({'X': .5} for _ in days),
            asset_returns={'X': tuple(0. for _ in days)}, first_signal=days[1])
        a = {'paths': {'TQQQ': path, 'SOXL': deepcopy(path)}, 'sessions': days,
             'eligible': list(range(2, 8)), 'train': [2, 3], 'evaluate': [4, 5, 6, 7]}
        b = deepcopy(a)
        b['paths']['TQQQ'].weights[2]['X'] = .6
        self.assertEqual(caller._qqq_pair_compare_signal_inputs(a, b)['changed_profiles'], [])
        b['paths']['TQQQ'].weights[4]['X'] += 1e-14
        comparison = caller._qqq_pair_compare_signal_inputs(a, b)
        self.assertEqual(comparison['changed_profiles'], ['TQQQ'])
        self.assertFalse(comparison['profiles']['TQQQ']['opening_identity_equal'])
        self.assertEqual(comparison['profiles']['TQQQ']['evaluation_target_dates_above_visibility_tolerance'], 0)
        b['evaluate'] = [5, 6, 7]
        self.assertFalse(caller._qqq_pair_compare_signal_inputs(a, b)['calendar_equal'])

    def test_registration_swap_after_started_rejects_before_generation_or_ledger(self):
        from copy import deepcopy
        from unittest.mock import patch
        from us_equity_strategies.research import rs04_netting_candidate as caller
        options, body = self.fixture()
        registered = deepcopy(options['input_source_registration'])
        changed = deepcopy(body)
        changed['bars']['QQQ'][0][1] *= 1.01
        for i in (3, 4):
            changed['actions']['QQQ']['dividends'][i][1] = .125
        other_root, other_registration = RootACoreOnlyBatchTests().archive_registration(options, changed)
        callbacks = []
        def swap_after_started():
            for suffix in ('-A-double', '-B-single'):
                row = options['store'].load_research_trial('us_equity',
                    'r0_r3_qqq_pair_sensitivity_v1', options['batch_id'] + suffix)
                self.assertEqual(row.status.value, 'started')
                self.assertEqual(row.actual_params['input_source_registration'], registered)
            options['input_source_registration'].update(deepcopy(other_registration))
            callbacks.append(True)
            return other_root
        with patch.object(caller, '_r0_r3_prepare_signal_inputs', side_effect=AssertionError('swapped archive generated')) as generated, \
                patch.object(caller, 'run_offline_security_netting', side_effect=AssertionError('swapped archive ledger')) as netter:
            with self.assertRaisesRegex(ValueError, 'UNAVAILABLE_ARCHIVE_OR_PARSER_IDENTITY'):
                caller.run_registered_qqq_pair_sensitivity(**(options | {'load_source_root': swap_after_started}))
        self.assertEqual(callbacks, [True])
        generated.assert_not_called()
        netter.assert_not_called()
        for suffix in ('-A-double', '-B-single'):
            row = options['store'].load_research_trial('us_equity',
                'r0_r3_qqq_pair_sensitivity_v1', options['batch_id'] + suffix)
            self.assertEqual(row.status.value, 'rejected')
            self.assertEqual(row.actual_params['input_source_registration'], registered)
            self.assertIsNone(row.run_id)
        with self.assertRaisesRegex(ValueError, 'SENSITIVITY_TRIAL_ALREADY_EXISTS'):
            caller.run_registered_qqq_pair_sensitivity(**(options | {
                'load_source_root': lambda: self.fail('rejected swap reparsed')}))

    def test_nested_caller_registration_mutation_does_not_poison_frozen_source(self):
        from copy import deepcopy
        from us_equity_strategies.research import rs04_netting_candidate as caller
        options, body = self.fixture()
        registered = deepcopy(options['input_source_registration'])
        source_root = options['load_source_root']
        changed = deepcopy(body)
        changed['bars']['QQQ'][0][1] *= 1.01
        _, other_registration = RootACoreOnlyBatchTests().archive_registration(options, changed)
        def mutate_nested():
            options['input_source_registration']['object_sha256'].update(other_registration['object_sha256'])
            return source_root()
        result = caller.run_registered_qqq_pair_sensitivity(**(options | {'load_source_root': mutate_nested}))
        self.assertEqual(result['status'], 'COMPLETED_NO_RELEVANT_DIFFERENCE_NO_LEDGER')
        self.assertEqual(result['source_registration'], registered)
        self.assertEqual(result['derivation']['A_TWO_RECORDS']['raw_body_sha256'], caller._r0_r3_sha(body))
        for row in result['trials'].values():
            self.assertEqual(row['actual_params']['input_source_registration'], registered)
        self.assertNotEqual(options['input_source_registration'], registered)


def test_TV1_late_declared_first_seen_real_admission_gap(tmp_path):
    from us_equity_strategies.research.rs04_netting_candidate import admit_r0_r3_source_metadata
    args, body, days = r0_r3_fixture(tmp_path)
    # Declared synthetic example only: no authenticated producer/arrival claim.
    declared = {'observed_at': '2030-01-02T21:00:00+00:00',
        'declared_available_at': '2030-01-02T21:01:00+00:00',
        'first_seen': '2030-01-02T21:07:00+00:00', 'revision': 'r1'}
    admitted = admit_r0_r3_source_metadata(args['projection'] | declared,
        identity_packet_sha256=args['identity_packet_sha256'], dataset_class='ARTIFICIAL_FIXTURE')
    assert declared['declared_available_at'] < '2030-01-02T21:05:00+00:00' < declared['first_seen']
    assert admitted['PIT'] == 'NOT_ATTESTED'
    assert admitted['observed_at'] == declared['observed_at']
    assert admitted['declared_available_at'] == declared['declared_available_at']
    assert admitted['source_time_basis'] == 'CALLER_DECLARED_NOT_AUTHENTICATED'
    # Canonical declarations preserve linkage, but the late record remains
    # NOT_ATTESTED. This API cannot certify availability at an actual cutoff.
    assert admitted.get('first_seen') == declared['first_seen'], (
        'FIRST_SEEN_CONTRACT_UNCOVERED: public admission has no canonical first_seen '
        'or authenticated producer linkage; early availability cannot be certified')


@unittest.skipIf(
    SecurityNettingSpec is None and os.environ.get('UES_REQUIRE_SECURITY_NETTING') != '1',
    'requires the explicit QPK research overlay')
def test_TV2_registered_version_hash_immutability(tmp_path):
    import copy
    from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch, _r0_r3_sha
    args, body, days = RootACoreOnlyBatchTests().fixture()
    args['source_revision'] = 'r1'
    args['projection'] = args['projection'] | {'first_seen': '2030-01-02T21:02:00+00:00'}
    first = run_registered_r0_r3_batch(**args)
    assert first['trial']['source_revision'] == 'r1'
    before = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', args['trial_id']).to_dict()
    revised_body = copy.deepcopy(body)
    revised_body['bars']['TQQQ'][0][2] += .01
    revised_hash = _r0_r3_sha(revised_body)
    assert revised_hash != args['input_body_sha256']
    def forbidden_second_parse():
        raise AssertionError('conflicting revision must be refused before parsing/computation')
    revised = args | {'source_revision': 'r2', 'input_body_sha256': revised_hash,
        'projection': args['projection'] | {'first_seen': '2030-01-02T21:06:00+00:00'},
        'load_input_body': forbidden_second_parse}
    with unittest.TestCase().assertRaisesRegex(ValueError, '^research_trial_conflict$'):
        run_registered_r0_r3_batch(**revised)
    assert args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', args['trial_id']).to_dict() == before
    assert first['PIT'] == 'NOT_ATTESTED'
    # Journal immutability passes; declared arrival times are not authenticated.


@unittest.skipIf(
    SecurityNettingSpec is None and os.environ.get('UES_REQUIRE_SECURITY_NETTING') != '1',
    'requires the explicit QPK research overlay')
def test_TV3_duplicate_journal_resume_unknown_first_seen(tmp_path):
    from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch
    args, body, days = RootACoreOnlyBatchTests().fixture()
    args['source_revision'] = 'r1'
    args['projection'] = args['projection'] | {'observed_at': '2020-01-02T21:00:00+00:00',
        'declared_available_at': '2020-01-02T21:01:00+00:00', 'first_seen': None}
    first = run_registered_r0_r3_batch(**args)
    def forbidden_duplicate_parse():
        raise AssertionError('duplicate journal resume must not parse or compute again')
    second = run_registered_r0_r3_batch(**(args | {'load_input_body': forbidden_duplicate_parse}))
    assert not first['resumed_without_compute']
    assert second['resumed_without_compute']
    assert second['trial'] == first['trial']
    assert second['subresults'] == first['subresults']
    assert second['PIT'] == 'NOT_ATTESTED'
    assert second['trial']['run_id'] == first['trial']['run_id']
    assert second['trial']['param_version'] == first['trial']['param_version']
    assert 'first_seen' not in second['trial']['actual_params']['source']
    # Absence stays UNKNOWN; neither old observed time nor current clock is proof.


def test_TV1_declared_time_validation_and_legacy_identity(tmp_path):
    from us_equity_strategies.research.rs04_netting_candidate import admit_r0_r3_source_metadata
    args, body, days = r0_r3_fixture(tmp_path)
    def admit(projection):
        return admit_r0_r3_source_metadata(projection,
            identity_packet_sha256=args['identity_packet_sha256'], dataset_class='ARTIFICIAL_FIXTURE')
    legacy = admit(args['projection'])
    assert 'first_seen' not in legacy
    assert 'source_time_basis' not in legacy
    assert admit(args['projection'] | dict(observed_at=None, declared_available_at=None, first_seen=None)) == legacy
    zulu = args['projection'] | {'first_seen': '2030-01-02T21:07:00Z',
        'source_time_basis': 'AUTHENTICATED_PRODUCER'}
    normalized = admit(zulu)
    assert normalized['first_seen'] == '2030-01-02T21:07:00+00:00'
    assert normalized == admit(args['projection'] | {'first_seen': normalized['first_seen']})
    assert normalized['source_time_basis'] == 'CALLER_DECLARED_NOT_AUTHENTICATED'
    assert normalized['PIT'] == legacy['PIT'] == 'NOT_ATTESTED'
    assert 'observed_at' not in normalized and 'declared_available_at' not in normalized
    # Reuse the current explicit UTC contract; never call str() on arbitrary objects.
    for field, invalid in [('observed_at', '2030-01-02T21:00:00'),
            ('declared_available_at', 'not-a-time'), ('first_seen', object()),
            ('first_seen', True), ('first_seen', '2030-01-02'),
            ('first_seen', '2030-01-03T05:07:00+08:00')]:
        with unittest.TestCase().assertRaisesRegex(ValueError, '^RS04_EXPLICIT_UTC_TIME_REQUIRED$'):
            admit(args['projection'] | {field: invalid})


@unittest.skipIf(
    SecurityNettingSpec is None and os.environ.get('UES_REQUIRE_SECURITY_NETTING') != '1',
    'requires the explicit QPK research overlay')
def test_TV1_registered_declared_time_binding_and_resume(tmp_path):
    from us_equity_strategies.research.rs04_netting_candidate import run_registered_r0_r3_batch
    args, body, days = RootACoreOnlyBatchTests().fixture()
    declared = {'observed_at': '2030-01-02T21:00:00+00:00',
        'declared_available_at': '2030-01-02T21:01:00+00:00',
        'first_seen': '2030-01-02T21:07:00+00:00'}
    args['projection'] = args['projection'] | declared
    first = run_registered_r0_r3_batch(**args)
    source = first['trial']['actual_params']['source']
    assert all(source[k] == v for k, v in declared.items())
    assert source['source_time_basis'] == 'CALLER_DECLARED_NOT_AUTHENTICATED'
    assert source['PIT'] == first['PIT'] == 'NOT_ATTESTED'
    def forbidden_parse():
        raise AssertionError('duplicate or changed declaration cannot parse or recompute')
    # Equivalent UTC spellings keep the same identity and real persisted result.
    duplicate = run_registered_r0_r3_batch(**(args | {'load_input_body': forbidden_parse,
        'projection': args['projection'] | {'first_seen': '2030-01-02T21:07:00Z'}}))
    assert duplicate['resumed_without_compute']
    assert duplicate['trial'] == first['trial']
    assert duplicate['subresults'] == first['subresults']
    for field in declared:
        changed = args['projection'] | {field: '2030-01-02T21:08:00+00:00'}
        with unittest.TestCase().assertRaisesRegex(ValueError, '^research_trial_conflict$'):
            run_registered_r0_r3_batch(**(args | {'projection': changed, 'load_input_body': forbidden_parse}))
        stored = args['store'].load_research_trial('us_equity', 'r0_r3_batch_additive_audit_v1', args['trial_id'])
        assert stored.to_dict() == first['trial']


@requires_security_netting
@pytest.mark.parametrize("mutation", ["candidate_pending_swap", "malformed_cached_pending"])
def test_syn001_reviewer_pending_counterexamples(tmp_path, mutation):
    # Original reviewer body; this execution is AUTHOR reproduction, not prior independent PASS.
    from us_equity_strategies.research import rs04_netting_candidate as m
    CODE = 'd580c05d89d318763a67c9fa2c36843ca65e13569f9c2a5cbae292309461bf32'
    SOURCE = 'cafcb07c8c010621aaa27be04a836f7915d38fecbba6296dca69c2eae302f153'
    IDENTITY = dict(code_sha256=CODE, source_contract_sha256=SOURCE)
    def initial():
        from us_equity_strategies.research import rs04_netting_candidate as m
        return m.new_synthetic_r0_r3_state(calendar=['2031-01-02','2031-01-03','2031-01-06','2031-01-07','2031-01-08','2031-01-09','2031-01-10'],**IDENTITY)
    def payload(state,index,shock=0.,boxx=0.):
        day=state['calendar'][index]
        return dict(schema_version='qsl.synthetic_r0_r3_snapshot.v1',scope='ARTIFICIAL_FIXTURE',input_id='independent-'+str(index),revision=1,
            session=day,event_at=day+'T21:00:00+00:00',first_seen_at=day+'T21:01:00+00:00',persisted_at=day+'T21:02:00+00:00',decision_cutoff=day+'T21:03:00+00:00',
            code_sha256=CODE,config_sha256=state['config_sha256'],source_contract_sha256=SOURCE,
            profiles={p:dict(full_target={p:.5,s:.2,'BOXX':.1},security_returns={p:shock,s:0.,'BOXX':boxx}) for p,s in [('TQQQ','QQQM'),('SOXL','SOXX')]})
    def wire(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    def step(state,index,**kw):
        from us_equity_strategies.research import rs04_netting_candidate as m
        return m.step_synthetic_r0_r3(state,wire(payload(state,index,**kw)),**IDENTITY)
    def save(tmp_path,name,value):(tmp_path/name).write_text(json.dumps(value,indent=2)+'\n')
    one,_=step(initial(),1);two,_=step(one,2)
    assert two['books']['TQQQ:R0']['pending']['kind']=='TARGET_WEIGHTS'
    assert two['books']['TQQQ:R3']['pending']['kind']=='HOLD_POSITIONS'
    corrupt=copy.deepcopy(two)
    if mutation=='candidate_pending_swap':
        corrupt['books']['TQQQ:R0']['pending']=copy.deepcopy(corrupt['books']['TQQQ:R3']['pending'])
    else:
        corrupt['inputs'][-1]['receipt']['cells']['TQQQ:R0']['next_pending']='NOT_A_PENDING_OBJECT'
    try:
        encoded=m.encode_synthetic_r0_r3_state(corrupt)
        restored=m.decode_synthetic_r0_r3_state(encoded,**IDENTITY)
    except ValueError:return
    proof={'mutation':mutation,'codec_accepted':True,'code_source_config_IDs_unchanged':True,'state_version':restored['version'],
        'latest_R0_recorded_pending':restored['inputs'][-1]['receipt']['cells']['TQQQ:R0']['next_pending'],
        'current_R0_pending':restored['books']['TQQQ:R0']['pending'],'checkpoint_body_sha256':m._synthetic_sha(restored)}
    if mutation=='candidate_pending_swap':
        valid,valid_r=step(two,3,shock=.1,boxx=.02);bad,bad_r=step(restored,3,shock=.1,boxx=.02)
        proof.update(valid_R0_step_fee=valid_r['cells']['TQQQ:R0']['executed_fees'],
            corrupt_R0_step_fee=bad_r['cells']['TQQQ:R0']['executed_fees'],
            valid_R0_nav=valid['books']['TQQQ:R0']['nav'],corrupt_R0_nav=bad['books']['TQQQ:R0']['nav'])
    else:
        _,replay=m.step_synthetic_r0_r3(restored,wire(payload(restored,2)),**IDENTITY)
        proof['replayed_R0_next_pending']=replay['cells']['TQQQ:R0']['next_pending']
    save(tmp_path,'CODEC_'+mutation+'.json',proof)
    pytest.fail('SYN-001: codec accepts internally inconsistent candidate pending / cached receipt')


@requires_security_netting
@pytest.mark.parametrize("mutation", [
    "current_target", "current_owner", "latest_string", "latest_target", "extra_pending_key",
    "historical_next_string", "historical_next_target", "historical_previous_string",
    "historical_previous_target", "first_previous", "cross_candidate", "cross_profile_hold",
    "wrong_fill_session", "wrong_decision_session", "wrong_universe", "legacy_wire_version",
])
def test_syn001_pending_chain_rejected_at_encoder_decoder_and_cached_replay(mutation):
    import us_equity_strategies.research.rs04_netting_candidate as module
    state = _synthetic_forward_state()
    for index in (1, 2, 3):
        state, _ = _synthetic_step(state, _synthetic_forward_input(state, index))
    valid_before = _synthetic_bytes(state)
    corrupt = copy.deepcopy(state)
    latest = corrupt["inputs"][-1]["receipt"]["cells"]
    early = corrupt["inputs"][0]["receipt"]["cells"]["TQQQ:R0"]
    later = corrupt["inputs"][1]["receipt"]["cells"]["TQQQ:R0"]
    if mutation == "current_target": corrupt["books"]["TQQQ:R0"]["pending"]["weights"]["TQQQ"] = .52
    elif mutation == "current_owner": corrupt["books"]["TQQQ:R3"]["pending"]["profile"] = "SOXL"
    elif mutation == "latest_string": latest["TQQQ:R0"]["next_pending"] = "NOT_A_PENDING_OBJECT"
    elif mutation == "latest_target": latest["TQQQ:R0"]["next_pending"]["weights"]["TQQQ"] = .52
    elif mutation == "extra_pending_key": latest["TQQQ:R3"]["next_pending"]["unexpected"] = True
    elif mutation == "historical_next_string": early["next_pending"] = "broken"
    elif mutation == "historical_next_target": early["next_pending"]["weights"]["TQQQ"] = .52
    elif mutation == "historical_previous_string": later["previous_pending"] = "broken"
    elif mutation == "historical_previous_target": later["previous_pending"]["weights"]["TQQQ"] = .52
    elif mutation == "first_previous":
        early["previous_pending"] = dict(profile="TQQQ", candidate="R0", decision_session=state["calendar"][0],
            fill_session=state["calendar"][1], kind="TARGET_WEIGHTS", weights={"TQQQ": .5, "QQQM": .2, "BOXX": .1})
    elif mutation == "cross_candidate": latest["TQQQ:R3"]["next_pending"] = copy.deepcopy(latest["TQQQ:R0"]["next_pending"])
    elif mutation == "cross_profile_hold": latest["TQQQ:R3"]["next_pending"] = copy.deepcopy(latest["SOXL:R3"]["next_pending"])
    elif mutation == "wrong_fill_session": later["previous_pending"]["fill_session"] = state["calendar"][3]
    elif mutation == "wrong_decision_session": latest["TQQQ:R3"]["next_pending"]["decision_session"] = state["calendar"][2]
    elif mutation == "wrong_universe": latest["TQQQ:R0"]["next_pending"]["weights"] = {"SOXL": .5, "SOXX": .2, "BOXX": .1}
    else: corrupt["schema_version"] = "qsl.synthetic_r0_r3_memory.v1"
    corrupt_before = _synthetic_bytes(corrupt)
    with pytest.raises(ValueError): module.encode_synthetic_r0_r3_state(corrupt)
    # A freshly computed valid checksum is deliberately NOT an internal semantic proof.
    forged_wire = _synthetic_bytes(dict(body=corrupt, sha256=module._synthetic_sha(corrupt)))
    with pytest.raises(ValueError): module.decode_synthetic_r0_r3_state(forged_wire,
        code_sha256="a"*64, source_contract_sha256="b"*64)
    with pytest.raises(ValueError): _synthetic_step(corrupt, _synthetic_forward_input(state, 2))
    assert _synthetic_bytes(corrupt) == corrupt_before
    assert _synthetic_bytes(state) == valid_before


@requires_security_netting
def test_syn001_normal_owned_pending_and_cached_receipt_remain_independent(record_property):
    state = _synthetic_forward_state()
    first, first_receipt = _synthetic_step(state, _synthetic_forward_input(state, 1))
    second, second_receipt = _synthetic_step(first, _synthetic_forward_input(first, 2))
    for name, book in second["books"].items():
        profile, candidate = name.split(":")
        pending = book["pending"]
        assert (pending["profile"], pending["candidate"]) == (profile, candidate)
        assert pending == second_receipt["cells"][name]["next_pending"]
        assert second_receipt["cells"][name]["previous_pending"] == first_receipt["cells"][name]["next_pending"]
        assert pending["kind"] == ("TARGET_WEIGHTS" if candidate == "R0" else "HOLD_POSITIONS")
    duplicate, old_receipt = _synthetic_step(second, _synthetic_forward_input(state, 1))
    assert duplicate == second and old_receipt == first_receipt
    old_receipt["cells"]["TQQQ:R0"]["next_pending"]["weights"]["TQQQ"] = .01
    assert duplicate == second and second["books"]["TQQQ:R0"]["pending"]["weights"]["TQQQ"] == .5
    # Record modules actually loaded in this guarded test process, rather than
    # treating a dependency inventory as an actual-import receipt.
    import sys
    import hashlib
    from pathlib import Path
    bindings = []
    for name, module in sorted(sys.modules.items()):
        if not name.startswith(("us_equity_strategies", "quant_platform_kit")):
            continue
        file = getattr(module, "__file__", None)
        if file:
            path = Path(file)
            origin = getattr(getattr(module, "__spec__", None), "origin", None)
            bindings.append(dict(module=name, file=str(path), origin=origin,
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    assert any(x["module"].endswith("rs04_netting_candidate") for x in bindings)
    assert any(x["module"].endswith("security_netting_runner") for x in bindings)
    record_property("actual_loaded_module_bindings", bindings)
