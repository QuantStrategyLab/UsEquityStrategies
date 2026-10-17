"""F0-F7 only: explicitly chosen synthetic inputs, never risk/runtime defaults."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from us_equity_strategies.research.risk_preset_contract import validate_packet


FIXTURES = Path(__file__).parent / "fixtures" / "risk_preset_offline"
SCHEMA = "qsl.risk_preset.offline.v1-candidate"
ASOF = "2030-01-02T00:00:00Z"
OBSERVED = "2030-01-02T01:00:00Z"
CLOCK = "2030-01-02T02:00:00Z"
WINDOW = {"effective_from": "2030-01-01T00:00:00Z", "expires_at": "2030-02-01T00:00:00Z"}
INPUT_SHA = hashlib.sha256(b"fixture-only synthetic input; no research data").hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def f0():
    return (json.loads((FIXTURES / "UNCONFIGURED_FIXTURE.json").read_bytes()),
            json.loads((FIXTURES / "OFFLINE_VALIDATOR_INPUTS.json").read_bytes()))


def check(packet, context):
    return validate_packet(packet, **context)


def accepted(packet, context):
    before = deepcopy((packet, context))
    assert check(packet, context) == {"valid": True, "issues": []}
    assert (packet, context) == before


def rejected(packet, context, code=None):
    first = check(packet, context)
    assert first["valid"] is False and first["issues"]
    assert check(packet, context) == first
    assert set(first) == {"valid", "issues"}
    if code:
        assert code in {issue["code"] for issue in first["issues"]}, first


def definition(version, scope):
    """All values/times/revisions below are deliberately fixture-only choices."""
    selector = {"preset_id": "fixture-preset", "config_version": version}
    denominator = {"kind": "SYNTHETIC_NAV", "fixture_id": scope["fixture_id"],
                   "input_sha256": INPUT_SHA, "asof": ASOF, "baseline_ref": "synthetic-baseline"}

    def limit(value, unit, mode="ENABLED"):
        return {"mode": mode, "value": value, "unit": unit,
                "denominator": deepcopy(denominator), "source_ref": deepcopy(selector)}

    result = {
        "config_version": version, "preset_id": selector["preset_id"], "scope": deepcopy(scope),
        "model_binding": {"method": "synthetic-rs06-clip-scale", "fraction": 0.25,
                          "preregistration_ref": "synthetic-plan", "ues_revision": "fixture-ues-v1",
                          "qpk_revision": "fixture-qpk-v1", "evaluator_version": "fixture-evaluator-v1"},
        "input_semantics": {"input_sha256": INPUT_SHA, "currency": "TEST_USD", "asof": ASOF,
                            "baseline_ref": "synthetic-baseline", "return_semantics": "SYNTHETIC_SIMPLE_RETURNS",
                            "cash_semantics": "SYNTHETIC_CASH_SECURITY"},
        "constraints": {
            "cash_symbol": "TEST_CASH",
            "max_effective_risk_exposure": limit(2.0, "LOOKTHROUGH_NAV_RATIO"),
            "max_symbol_weights": {"TEST_RISK": limit(0.75, "PORTFOLIO_WEIGHT")},
            "max_underlying_effective_exposure": {"TEST_UNDERLYING": limit(2.0, "LOOKTHROUGH_NAV_RATIO")},
            "max_one_way_risk_turnover": limit(None, "ONE_WAY_RISK_BUY_WEIGHT", "DISABLED"),
            "min_available_cash_weight": limit(0.0, "SPENDABLE_CASH_NAV_RATIO"),
            "max_security_gross": limit(1.0, "SECURITY_GROSS_NAV_RATIO"),
            "max_security_net": limit(1.0, "SECURITY_NET_NAV_RATIO"),
        },
        "window": deepcopy(WINDOW),
        "qualification_rules": {"max_age_seconds": 7200, "required_qualification": "QUALIFIED"},
    }
    result["content_sha256"] = digest(result)
    return result


def ref(defn):
    return {"preset_id": defn["preset_id"],
            "definition_ref": {"config_version": defn["config_version"], "content_sha256": defn["content_sha256"]}}


def synthetic(*, effective=False, requested="new"):
    packet, context = f0()
    packet["scope"] = {"layer": "RESEARCH", "synthetic": True, "fixture_id": "synthetic-F1-F7",
                       "strategy_profile": "fixture-strategy", "strategy_revision": "fixture-strategy-v1"}
    packet["explanation"] = "仅用于离线合成测试。"
    old, new = (definition(v, packet["scope"]) for v in ("old", "new"))
    packet["definitions"] = [old, new]
    packet["requested"] = ref(new if requested == "new" else old)
    packet["request_status"] = {"status": "UNKNOWN", "reason_codes": ["READBACK_MISSING"],
                                "missing_parameters": [], "processed_at": None, "evidence_key": None}
    entry = {"scope": deepcopy(packet["scope"]), "allowed_definition_refs": [ref(old), ref(new)],
             "allowed_evidence_key_digests": {}, "asset_ids": ["TEST_RISK", "TEST_CASH"],
             "currency_ids": ["TEST_USD"], "underlying_ids": ["TEST_UNDERLYING"],
             "preset_ids": ["fixture-preset"], "explanations": [packet["explanation"]],
             "preregistrations": {"synthetic-plan": deepcopy(old["model_binding"])}}
    context["fixture_manifest"]["fixtures"] = {"synthetic-F1-F7": entry}
    context["fixture_clock"] = CLOCK
    if effective:
        packet["effective"] = ref(old)
        packet["freshness"] = "FRESH"
        packet["source"] = {"consumer": "fixture-observer", "mode": "SYNTHETIC_RESEARCH",
                            "ues_revision": "fixture-ues-v1", "qpk_revision": "fixture-qpk-v1",
                            "evaluator_version": "fixture-evaluator-v1", "input_sha256": INPUT_SHA,
                            "evidence_ref": "obs-old"}
        packet["timing"] = {"asof": ASOF, "available_at": "2030-01-02T00:30:00Z",
                            "observed_at": OBSERVED, "valid_until": "2030-01-03T00:00:00Z"}
        packet["capital_basis"] = {"fixture_id": packet["scope"]["fixture_id"],
                                   "denominator_kind": "SYNTHETIC_NAV", "nav_value": 100.0,
                                   "currency": "TEST_USD", "asof": ASOF, "coverage": "COMPLETE", "fx_ref": None}
        packet["final_constraints"] = {
            "binding": {"effective_definition_ref": ref(old), "scope": deepcopy(packet["scope"]), "input_sha256": INPUT_SHA},
            "completeness": "COMPLETE", "items": deepcopy(old["constraints"])}
        packet["risk_assessment"] = {"status": "APPROVE", "reason_codes": [], "risk_scalar": 1.0,
                                     "recommended_target_weights": {"TEST_RISK": 0.0, "TEST_CASH": 1.0},
                                     "metrics": {"nominal_weight": {"TEST_RISK": 0.0, "TEST_CASH": 1.0},
                                                 "effective_risk_exposure": 0.0,
                                                 "underlying_effective_exposure": {"TEST_UNDERLYING": 0.0},
                                                 "one_way_risk_turnover": 0.0}}
        packet["upstream"] = [{"stage": "RS06", "subject": "fixture-run", "status": "COMPUTED",
                               "reason_codes": [], "evidence_ref": "obs-old"}]
        seal_observation(packet, context)
    return packet, context


OBSERVATION_FIELDS = ("effective", "scope", "source", "timing", "capital_basis", "upstream",
                      "risk_assessment", "final_constraints")


def entry_of(packet, context):
    return context["fixture_manifest"]["fixtures"][packet["scope"]["fixture_id"]]


def seal_observation(packet, context, qualification="QUALIFIED"):
    record = {"type": "EFFECTIVE_OBSERVATION", "qualification": qualification,
              **{k: deepcopy(packet[k]) for k in OBSERVATION_FIELDS}}
    context["evidence_table"]["obs-old"] = record
    entry_of(packet, context)["allowed_evidence_key_digests"]["obs-old"] = digest(record)


def acknowledge(packet, context, status="APPLIED"):
    packet["request_status"] = {"status": status, "reason_codes": ["FIXTURE_HANDLED"],
                                "missing_parameters": [], "processed_at": OBSERVED, "evidence_key": "request"}
    record = {"type": "REQUEST_STATE", "scope": deepcopy(packet["scope"]),
              "requested": deepcopy(packet["requested"]), "request_status": deepcopy(packet["request_status"])}
    context["evidence_table"]["request"] = record
    entry_of(packet, context)["allowed_evidence_key_digests"]["request"] = digest(record)


def rehash(packet, context, index):
    defn = packet["definitions"][index]
    old_ref = ref(defn)
    defn["content_sha256"] = digest({k: v for k, v in defn.items() if k != "content_sha256"})
    new_ref = ref(defn)
    admitted = entry_of(packet, context)["allowed_definition_refs"]
    admitted[admitted.index(old_ref)] = new_ref
    for key in ("requested", "effective"):
        if packet[key] == old_ref:
            packet[key] = deepcopy(new_ref)


def test_F0_exact_frozen_unconfigured_and_unchanged():
    expected = {"OFFLINE_CONTRACT_CANDIDATE.json": "547b2de228c1a3641adf2bb756a47ad3cb69f10a9e45b583bbb3728accc5cf98",
                "UNCONFIGURED_FIXTURE.json": "749438cd749ff2ae3e2ee4cb8c2e9e9117825cfc14637404604279ab19b99252",
                "OFFLINE_VALIDATOR_INPUTS.json": "0a9c31e85e98e35393f932d2e2026f49a95fce5782c351446aaa29f726d70352"}
    for name, sha in expected.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == sha
    accepted(*f0())


def test_F1_selected_incomplete_keeps_missing_keys_without_fabricating_definition():
    packet, context = synthetic()
    packet["definitions"] = []
    entry_of(packet, context)["allowed_definition_refs"] = []
    packet["requested"]["definition_ref"] = None
    packet["request_status"] = deepcopy(f0()[0]["request_status"])
    packet["request_status"]["reason_codes"] = ["VALUES_UNSPECIFIED"]
    packet["request_status"]["missing_parameters"] = [
        k for k in packet["request_status"]["missing_parameters"] if k not in ("PRESET_SELECTION", "STRATEGY_SCOPE_BINDING")]
    context["fixture_clock"] = None  # No definition, processing or observation time predicate.
    accepted(packet, context)
    assert packet["requested"]["definition_ref"] is None
    assert packet["definitions"] == []


@pytest.mark.parametrize("root", [None, [], True, 1, "packet"])
def test_F2_wrong_root_returns_issues(root):
    rejected(root, f0()[1])


@pytest.mark.parametrize("path,value", [
    (("extra",), 0), (("status",), "APPLIED"), (("scope", "account_id"), "forbidden"),
    (("scope", "layer"), "PAPER"), (("scope", "layer"), "PRODUCTION"),
    (("scope", "fixture_id"), "unlisted"), (("scope", "strategy_revision"), "main"),
    (("scope", "synthetic"), 1), (("execution_authorized",), True), (("display_only",), 1),
    (("research_boundary", "live_ready"), True), (("request_status", "extra"), "x"),
    (("request_status", "missing_parameters"), ["UNKNOWN_KEY"]), (("timing",), []),
    (("definitions",), {}), (("upstream",), {}), (("explanation",), "unapproved display text"),
])
def test_F2_closed_shape_scope_authority(path, value):
    packet, context = f0()
    target = packet
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    rejected(packet, context)


def test_F2_custom_objects_and_cycles_rejected_without_methods():
    class Hostile(dict):
        def __eq__(self, other):
            raise AssertionError("custom equality called")

        def items(self):
            raise AssertionError("custom mapping called")

    rejected(Hostile(), f0()[1])
    packet, context = f0()
    packet["definitions"].append(packet)
    rejected(packet, context)


def test_F2_custom_metaclass_equality_is_never_invoked():
    class HostileType(type):
        def __eq__(cls, other):
            raise AssertionError("custom type equality invoked")

    class NotJSON(metaclass=HostileType):
        pass

    rejected(NotJSON(), f0()[1])


def test_F2_all_nested_wrong_types_return_issues_safely():
    """Exercise packet AND independently supplied sidecar/evidence shapes."""
    packet, context = synthetic(effective=True, requested="old")
    acknowledge(packet, context)
    inputs = {"packet": packet, **context}

    def paths(value, prefix=()):
        yield prefix
        if type(value) is dict:
            for key, child in value.items():
                yield from paths(child, (*prefix, key))
        elif type(value) is list:
            for index, child in enumerate(value):
                yield from paths(child, (*prefix, index))

    for path in paths(inputs):
        if not path:
            continue
        for wrong in (None, True, 0, [], {}):
            changed = deepcopy(inputs)
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = wrong
            result = validate_packet(**changed)
            assert type(result["valid"]) is bool, path
            assert result["valid"] or result["issues"], path


def test_F2_boolean_cannot_masquerade_as_one_in_independent_evidence():
    packet, context = synthetic(effective=True)
    context["evidence_table"]["obs-old"]["risk_assessment"]["risk_scalar"] = True
    record = context["evidence_table"]["obs-old"]
    entry_of(packet, context)["allowed_evidence_key_digests"]["obs-old"] = digest(record)
    rejected(packet, context)


def test_F2_unused_manifest_entries_still_have_closed_shapes():
    packet, context = f0()
    context["fixture_manifest"]["fixtures"]["malformed-entry"] = []
    rejected(packet, context)


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -float("inf"),
                                   10**400, "25%", None, -0.1, 1.1, {}, []])
def test_F3_bare_scalar_rejects_invalid_numbers(value):
    packet, context = synthetic(effective=True)
    packet["risk_assessment"]["risk_scalar"] = value
    rejected(packet, context)


@pytest.mark.parametrize("field,value", [("nav_value", 0), ("nav_value", True), ("nav_value", "100"),
                                        ("currency", "USD"), ("fx_ref", "https://example.invalid")])
def test_F3_capital_types_domains_and_currency(field, value):
    packet, context = synthetic(effective=True)
    packet["capital_basis"][field] = value
    rejected(packet, context)


@pytest.mark.parametrize("field,value", [("mode", "DISABLED"), ("value", 0), ("value", True),
                                        ("unit", "PERCENT"), ("denominator", None), ("source_ref", "unlisted")])
def test_F3_required_constraint_wire(field, value):
    packet, context = synthetic()
    packet["definitions"][1]["constraints"]["max_effective_risk_exposure"][field] = value
    rehash(packet, context, 1)
    rejected(packet, context)


def test_F3_zero_retained_disabled_turnover_distinct_from_unspecified():
    packet, context = synthetic(effective=True)
    accepted(packet, context)
    assert packet["risk_assessment"]["metrics"]["effective_risk_exposure"] == 0.0
    assert packet["final_constraints"]["items"]["min_available_cash_weight"]["value"] == 0.0
    packet["definitions"][1]["constraints"]["max_one_way_risk_turnover"]["mode"] = "UNSPECIFIED"
    rehash(packet, context, 1)
    rejected(packet, context, "INCOMPLETE_DEFINITION")


@pytest.mark.parametrize("change", ["cash_wrapper", "fraction_boolean", "fraction_too_large", "symbol_zero",
                                    "underlying_negative", "unknown_asset", "wrong_basis_input"])
def test_F3_definition_identifiers_fraction_and_map_domains(change):
    packet, context = synthetic()
    defn = packet["definitions"][1]
    if change == "cash_wrapper":
        defn["constraints"]["cash_symbol"] = {"mode": "ENABLED", "value": "TEST_CASH"}
    elif change.startswith("fraction"):
        defn["model_binding"]["fraction"] = True if change == "fraction_boolean" else 0.6
    elif change == "symbol_zero":
        defn["constraints"]["max_symbol_weights"]["TEST_RISK"]["value"] = 0
    elif change == "underlying_negative":
        defn["constraints"]["max_underlying_effective_exposure"]["TEST_UNDERLYING"]["value"] = -1
    elif change == "unknown_asset":
        defn["constraints"]["max_symbol_weights"]["UNLISTED"] = defn["constraints"]["max_symbol_weights"].pop("TEST_RISK")
    else:
        defn["constraints"]["max_security_net"]["denominator"]["input_sha256"] = "0" * 64
    rehash(packet, context, 1)
    rejected(packet, context)


@pytest.mark.parametrize("weights", [{"TEST_RISK": True}, {"UNKNOWN": 0.0}, {"TEST_RISK": None},
                                     {"TEST_RISK": 0.7, "TEST_CASH": 0.7}, {"TEST_RISK": -0.1}])
def test_F3_weights_are_finite_bare_long_only_allowlisted(weights):
    packet, context = synthetic(effective=True)
    packet["risk_assessment"]["recommended_target_weights"] = weights
    rejected(packet, context)


def test_F4_hash_alone_never_effective_or_applied():
    packet, context = synthetic()
    accepted(packet, context)
    assert packet["request_status"]["status"] == "UNKNOWN"
    assert packet["freshness"] == "MISSING" and packet["effective"] is None
    packet["request_status"]["status"] = "APPLIED"
    rejected(packet, context)


def test_F4_complete_request_cannot_claim_unconfigured_even_with_admitted_ack():
    packet, context = synthetic()
    acknowledge(packet, context, "NOT_CONFIGURED")
    rejected(packet, context)


def test_F5_new_unknown_with_old_fresh_and_distinct_binding_is_valid():
    packet, context = synthetic(effective=True)
    accepted(packet, context)
    assert packet["requested"] != packet["effective"]
    acknowledge(packet, context, "REJECTED")
    accepted(packet, context)
    acknowledge(packet, context, "NOT_APPLIED")
    accepted(packet, context)


def test_F5_unconfigured_request_can_retain_old_fresh_effective():
    packet, context = synthetic(effective=True)
    packet["requested"]["definition_ref"] = None
    packet["request_status"] = deepcopy(f0()[0]["request_status"])
    packet["request_status"]["reason_codes"] = ["VALUES_UNSPECIFIED"]
    packet["request_status"]["missing_parameters"] = [k for k in packet["request_status"]["missing_parameters"]
                                                       if k not in ("PRESET_SELECTION", "STRATEGY_SCOPE_BINDING")]
    accepted(packet, context)


def test_F5_request_window_cannot_replace_old_effective_window():
    packet, context = synthetic(effective=True)
    packet["definitions"][1]["window"] = {"effective_from": "2031-01-01T00:00:00Z", "expires_at": "2031-02-01T00:00:00Z"}
    rehash(packet, context, 1)
    accepted(packet, context)
    assert packet["source"]["evidence_ref"] == "obs-old"


def test_F5_request_processing_time_is_not_old_observation_time():
    packet, context = synthetic(effective=True)
    acknowledge(packet, context, "REJECTED")
    packet["request_status"]["processed_at"] = CLOCK
    record = context["evidence_table"]["request"]
    record["request_status"] = deepcopy(packet["request_status"])
    entry_of(packet, context)["allowed_evidence_key_digests"]["request"] = digest(record)
    accepted(packet, context)
    assert packet["timing"]["observed_at"] == OBSERVED
    assert packet["request_status"]["processed_at"] != packet["timing"]["observed_at"]


def test_F5_applied_stale_is_observation_not_readiness():
    packet, context = synthetic(effective=True, requested="old")
    acknowledge(packet, context)
    context["fixture_clock"] = "2030-01-02T04:00:00Z"
    packet["freshness"] = "STALE"
    accepted(packet, context)
    assert packet["execution_authorized"] is False


@pytest.mark.parametrize("qualification,freshness", [("UNKNOWN", "UNKNOWN"), ("UNQUALIFIED", "UNKNOWN")])
def test_F5_unknown_qualification_preserved(qualification, freshness):
    packet, context = synthetic(effective=True)
    packet["freshness"] = freshness
    packet["risk_assessment"]["status"] = "PARKED"
    seal_observation(packet, context, qualification)
    accepted(packet, context)


@pytest.mark.parametrize("field,value", [("observed_at", "2030-01-02T03:00:00Z"),
                                        ("available_at", "2030-01-02T02:30:00Z"),
                                        ("asof", "2030-01-02T01:30:00Z"),
                                        ("valid_until", "2030-01-01T00:00:00Z"),
                                        ("asof", "2030-01-02T00:00:00"),
                                        ("asof", "2030-01-02T01:00:00+01:00")])
def test_F5_causality_availability_expiry_and_utc(field, value):
    packet, context = synthetic(effective=True)
    packet["timing"][field] = value
    seal_observation(packet, context)
    rejected(packet, context)


@pytest.mark.parametrize("clock", [None, "2030-02-01T00:00:00Z", "2030-01-02T00:30:00Z", True])
def test_F5_expired_policy_and_missing_or_early_clock(clock):
    packet, context = synthetic(effective=True)
    context["fixture_clock"] = clock
    rejected(packet, context)


def test_F5_observation_expiry_means_stale_not_policy_expiry():
    packet, context = synthetic(effective=True)
    context["fixture_clock"] = "2030-01-03T00:00:00Z"
    packet["freshness"] = "STALE"
    accepted(packet, context)
    packet["freshness"] = "FRESH"
    rejected(packet, context)


def test_F5_expired_observation_history_can_remain_without_current_effective():
    packet, context = synthetic(effective=True)
    context["fixture_clock"] = "2030-03-01T00:00:00Z"
    empty = f0()[0]
    for key in ("effective", "source", "timing", "capital_basis", "upstream", "final_constraints", "risk_assessment", "freshness"):
        packet[key] = deepcopy(empty[key])
    accepted(packet, context)


def test_F5_request_evidence_cannot_acknowledge_another_request():
    packet, context = synthetic(effective=True, requested="old")
    acknowledge(packet, context)
    packet["requested"] = ref(packet["definitions"][1])
    rejected(packet, context)


def test_F5_old_observation_cannot_bind_new_request():
    packet, context = synthetic(effective=True)
    packet["effective"] = deepcopy(packet["requested"])
    rejected(packet, context)


@pytest.mark.parametrize("mutation", ["unadmitted_ref", "hash", "input", "source", "final_ref", "looser_cap",
                                      "partial", "no_evidence", "tampered_evidence", "copied_digest", "wrong_type"])
def test_F6_independent_reference_source_final_constraints_and_evidence(mutation):
    packet, context = synthetic(effective=True)
    if mutation == "unadmitted_ref":
        entry_of(packet, context)["allowed_definition_refs"] = []
    elif mutation == "hash":
        packet["definitions"][0]["model_binding"]["fraction"] = 0.3
    elif mutation == "input":
        packet["source"]["input_sha256"] = "0" * 64
        seal_observation(packet, context)
    elif mutation == "source":
        packet["source"]["ues_revision"] = "main"
        seal_observation(packet, context)
    elif mutation == "final_ref":
        packet["final_constraints"]["binding"]["effective_definition_ref"] = deepcopy(packet["requested"])
        seal_observation(packet, context)
    elif mutation == "looser_cap":
        packet["final_constraints"]["items"]["max_effective_risk_exposure"]["value"] = 3.0
        seal_observation(packet, context)
    elif mutation == "partial":
        packet["final_constraints"]["completeness"] = "PARTIAL"
        seal_observation(packet, context)
    elif mutation == "no_evidence":
        context["evidence_table"] = {}
    elif mutation == "tampered_evidence":
        context["evidence_table"]["obs-old"]["qualification"] = "UNKNOWN"
    elif mutation == "copied_digest":
        packet["source"]["evidence_ref"] = "copied"
        context["evidence_table"]["copied"] = deepcopy(context["evidence_table"]["obs-old"])
    else:
        context["evidence_table"]["obs-old"]["type"] = "REQUEST_STATE"
        entry_of(packet, context)["allowed_evidence_key_digests"]["obs-old"] = digest(context["evidence_table"]["obs-old"])
    before = deepcopy((packet, context))
    rejected(packet, context)
    assert (packet, context) == before


def test_F6_manifest_schema_and_missing_enum_agreement():
    packet, context = f0()
    context["fixture_manifest"]["schema_version"] = "other"
    rejected(packet, context)
    packet, context = f0()
    context["fixture_manifest"]["missing_parameter_keys"].append("NEW")
    rejected(packet, context)


def test_F6_unreferenced_evidence_is_not_an_unchecked_raw_blob():
    packet, context = synthetic(effective=True)
    historical = deepcopy(context["evidence_table"]["obs-old"])
    historical["timing"] = {"not_a_timestamp": "raw"}
    context["evidence_table"]["history"] = historical
    entry_of(packet, context)["allowed_evidence_key_digests"]["history"] = digest(historical)
    rejected(packet, context)


@pytest.mark.parametrize("field", ["risk_scalar", "recommended_target_weights", "nominal_weight", "effective_risk_exposure"])
def test_F6_unknown_assessment_inputs_cannot_approve(field):
    packet, context = synthetic(effective=True)
    target = packet["risk_assessment"] if field in ("risk_scalar", "recommended_target_weights") else packet["risk_assessment"]["metrics"]
    target[field] = None
    seal_observation(packet, context)
    rejected(packet, context)


def test_F6_definition_evidence_source_wire_and_tighter_final_caps():
    packet, context = synthetic(effective=True)
    old = packet["definitions"][0]
    old["constraints"]["max_security_gross"]["source_ref"] = "obs-old"
    rehash(packet, context, 0)
    packet["final_constraints"]["binding"]["effective_definition_ref"] = deepcopy(packet["effective"])
    packet["final_constraints"]["items"] = deepcopy(old["constraints"])
    packet["final_constraints"]["items"]["max_security_gross"]["value"] = 0.9
    seal_observation(packet, context)
    accepted(packet, context)


def test_F7_nested_parked_and_unknown_raw_status_reasons_preserved():
    packet, context = synthetic(effective=True)
    packet["upstream"] += [
        {"stage": "strict", "subject": "fixture-run", "status": "SUCCEEDED", "reason_codes": [], "evidence_ref": "obs-old"},
        {"stage": "RS06.fold", "subject": "fold-2", "status": "PARKED", "reason_codes": ["SYNTHETIC_SINGULAR"], "evidence_ref": "obs-old"},
        {"stage": "budget", "subject": "fixture-run", "status": "FUTURE_UNKNOWN_STATE", "reason_codes": ["RAW_REASON"], "evidence_ref": "obs-old"}]
    packet["risk_assessment"]["status"] = "PARKED"
    packet["risk_assessment"]["risk_scalar"] = 0.0
    packet["risk_assessment"]["recommended_target_weights"] = None
    seal_observation(packet, context)
    accepted(packet, context)
    assert packet["upstream"][2]["status"] == "PARKED"
    assert packet["upstream"][3]["reason_codes"] == ["RAW_REASON"]
    packet["upstream"].append(deepcopy(packet["upstream"][0]))
    seal_observation(packet, context)
    rejected(packet, context)
