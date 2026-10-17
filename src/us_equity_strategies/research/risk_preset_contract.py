"""Pure synthetic RESEARCH packet validation; no consumer or risk calculation."""

import hashlib
import json
import math
import re
from datetime import datetime


SCHEMA_VERSION = "qsl.risk_preset.offline.v1-candidate"
_MISSING = (
    "PRESET_SELECTION", "COMPLETE_DEFINITION", "STRATEGY_SCOPE_BINDING", "MODEL_BINDING",
    "COMMON_FRACTION", "INPUT_SEMANTICS", "CONSTRAINT_VALUES", "DEFINITION_WINDOW", "QUALIFICATION_RULES",
)
_PACKET = "schema_version definitions requested request_status effective scope freshness upstream source timing capital_basis final_constraints risk_assessment explanation display_only execution_authorized research_boundary"
_SCOPE = "layer synthetic fixture_id strategy_profile strategy_revision"
_STATUS = "status reason_codes missing_parameters processed_at evidence_key"
_TIMING = "observed_at asof available_at valid_until"
_SOURCE = "consumer mode ues_revision qpk_revision evaluator_version input_sha256 evidence_ref"
_CAPITAL = "fixture_id denominator_kind nav_value currency asof coverage fx_ref"
_ASSESSMENT = "status reason_codes risk_scalar recommended_target_weights metrics"
_METRICS = "nominal_weight effective_risk_exposure underlying_effective_exposure one_way_risk_turnover"
_SNAPSHOT = "effective scope source timing capital_basis upstream risk_assessment final_constraints"
_MODEL = "method fraction preregistration_ref ues_revision qpk_revision evaluator_version"
_INPUT = "input_sha256 currency asof baseline_ref return_semantics cash_semantics"
_CONSTRAINTS = "cash_symbol max_effective_risk_exposure max_symbol_weights max_underlying_effective_exposure max_one_way_risk_turnover min_available_cash_weight max_security_gross max_security_net"
_UNITS = {
    "max_effective_risk_exposure": "LOOKTHROUGH_NAV_RATIO",
    "max_symbol_weights": "PORTFOLIO_WEIGHT",
    "max_underlying_effective_exposure": "LOOKTHROUGH_NAV_RATIO",
    "max_one_way_risk_turnover": "ONE_WAY_RISK_BUY_WEIGHT",
    "min_available_cash_weight": "SPENDABLE_CASH_NAV_RATIO",
    "max_security_gross": "SECURITY_GROSS_NAV_RATIO",
    "max_security_net": "SECURITY_NET_NAV_RATIO",
}
_FORBIDDEN = {"account_id", "broker_account", "account_number", "platform", "target_name"}
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)\Z")


class _Issue(ValueError):
    def __init__(self, path, code):
        self.path, self.code = path, code


def _require(condition, path, code):
    if not condition:
        raise _Issue(path, code)


def _object(value, fields, path, optional=""):
    required = set(fields.split())
    _require(type(value) is dict and required <= value.keys()
             and value.keys() <= required | set(optional.split()), path, "CLOSED_SHAPE")
    return value


def _list(value, path):
    _require(type(value) is list, path, "JSON_ARRAY_REQUIRED")
    return value


def _text(value, path):
    _require(type(value) is str and 0 < len(value) <= 256, path, "TEXT_REQUIRED")
    return value


def _token(value, path):
    _text(value, path)
    _require(_TOKEN.fullmatch(value) is not None, path, "OPAQUE_KEY_REQUIRED")
    return value


def _strings(value, path):
    _list(value, path)
    for item in value:
        _text(item, path)
    _require(len(value) == len(set(value)), path, "DUPLICATE_VALUE")
    return value


def _number(value, path, *, minimum=0, maximum=None, positive=False):
    _require(type(value) is int or type(value) is float, path, "FINITE_NUMBER_REQUIRED")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, path, "FINITE_NUMBER_REQUIRED")
    _require(value >= minimum and (maximum is None or value <= maximum)
             and (not positive or value > 0), path, "NUMBER_DOMAIN")
    return value


def _json(value, path, ancestors=()):
    """Reject custom objects before invoking any of their methods or equality."""
    kind = type(value)
    _require(any(kind is allowed for allowed in (dict, list, str, int, float, bool, type(None))),
             path, "ORDINARY_JSON_REQUIRED")
    if kind in (int, float):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        _require(finite, path, "FINITE_NUMBER_REQUIRED")
    if kind in (dict, list):
        _require(id(value) not in ancestors and len(ancestors) < 64, path, "CYCLIC_OR_DEEP_JSON")
        ancestors = (*ancestors, id(value))
        if kind is dict:
            for key, item in value.items():
                _require(type(key) is str, path, "JSON_STRING_KEY_REQUIRED")
                _require(key not in _FORBIDDEN, path, "REAL_IDENTITY_FORBIDDEN")
                _json(item, path, ancestors)
        else:
            for item in value:
                _json(item, path, ancestors)


def _sha(value, path):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, path, "SHA256_REQUIRED")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def _same(left, right):
    # Python equality conflates True with 1; independent wire bindings must not.
    return _digest(left) == _digest(right)


def _instant(value, path):
    _require(type(value) is str and _UTC.fullmatch(value) is not None, path, "EXPLICIT_UTC_REQUIRED")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _Issue(path, "EXPLICIT_UTC_REQUIRED") from None


def _selector(value, path, *, incomplete=False):
    _object(value, "preset_id definition_ref", path)
    _token(value["preset_id"], path)
    if incomplete and value["definition_ref"] is None:
        return
    reference = _object(value["definition_ref"], "config_version content_sha256", path)
    _token(reference["config_version"], path)
    _sha(reference["content_sha256"], path)


def _definition_ref(definition):
    return {"preset_id": definition["preset_id"],
            "definition_ref": {"config_version": definition["config_version"],
                               "content_sha256": definition["content_sha256"]}}


def _inspect(packet, manifest, evidence, clock):
    for value, path in ((packet, "packet"), (manifest, "fixture_manifest"),
                        (evidence, "evidence_table"), (clock, "fixture_clock")):
        _json(value, path)
    _object(packet, _PACKET, "packet")
    _object(manifest, "schema_version fixtures missing_parameter_keys", "fixture_manifest")
    _require(packet["schema_version"] == manifest["schema_version"] == SCHEMA_VERSION,
             "schema_version", "SCHEMA_MISMATCH")
    _require(set(_strings(manifest["missing_parameter_keys"], "fixture_manifest.missing_parameter_keys")) == set(_MISSING),
             "fixture_manifest.missing_parameter_keys", "MISSING_ENUM_MISMATCH")
    _require(type(manifest["fixtures"]) is dict and type(evidence) is dict, "fixture_manifest", "CLOSED_SHAPE")
    for fixture_key, entry in manifest["fixtures"].items():
        _token(fixture_key, "fixture_manifest.fixture_id")
        _object(entry, "scope allowed_definition_refs allowed_evidence_key_digests asset_ids currency_ids",
                "fixture_manifest.fixture", "underlying_ids preset_ids explanations preregistrations")
        entry_scope = _object(entry["scope"], _SCOPE, "fixture_manifest.scope")
        _require(entry_scope["fixture_id"] == fixture_key and entry_scope["layer"] == "RESEARCH"
                 and entry_scope["synthetic"] is True, "fixture_manifest.scope", "SYNTHETIC_RESEARCH_ONLY")
        for field in ("strategy_profile", "strategy_revision"):
            if entry_scope[field] is not None:
                _token(entry_scope[field], "fixture_manifest.scope")
        for field in ("asset_ids", "currency_ids", "underlying_ids", "preset_ids"):
            for key in _strings(entry.get(field, []), "fixture_manifest." + field):
                _token(key, "fixture_manifest." + field)
        for reference in _list(entry["allowed_definition_refs"], "fixture_manifest.allowed_definition_refs"):
            _selector(reference, "fixture_manifest.allowed_definition_refs")
        _require(type(entry["allowed_evidence_key_digests"]) is dict, "fixture_manifest.evidence", "CLOSED_SHAPE")
        for key, sha in entry["allowed_evidence_key_digests"].items():
            _token(key, "fixture_manifest.evidence")
            _sha(sha, "fixture_manifest.evidence")
        if "explanations" in entry:
            _strings(entry["explanations"], "fixture_manifest.explanations")
        else:
            _require(fixture_key == "unconfigured-research-fixture", "fixture_manifest.explanations", "DISPLAY_TEXT_NOT_ADMITTED")
        plans = entry.get("preregistrations", {})
        _require(type(plans) is dict, "fixture_manifest.preregistrations", "CLOSED_SHAPE")
        for key, model in plans.items():
            _token(key, "fixture_manifest.preregistrations")
            _object(model, _MODEL, "fixture_manifest.preregistrations")
            _number(model["fraction"], "fixture_manifest.preregistrations.fraction", positive=True, maximum=0.5)
            _require(model["preregistration_ref"] == key, "fixture_manifest.preregistrations", "PREREGISTRATION_MISMATCH")
            for field in ("method", "preregistration_ref", "ues_revision", "qpk_revision", "evaluator_version"):
                _token(model[field], "fixture_manifest.preregistrations")
    scope = _object(packet["scope"], _SCOPE, "scope")
    _require(scope["layer"] == "RESEARCH" and scope["synthetic"] is True, "scope", "SYNTHETIC_RESEARCH_ONLY")
    _token(scope["fixture_id"], "scope.fixture_id")
    _require(scope["fixture_id"] in manifest["fixtures"], "scope", "FIXTURE_NOT_ADMITTED")
    fixture = manifest["fixtures"][scope["fixture_id"]]
    _require(_same(scope, fixture["scope"]), "scope", "SCOPE_MISMATCH")
    assets, currencies = fixture["asset_ids"], fixture["currency_ids"]
    underlyings, presets = fixture.get("underlying_ids", []), fixture.get("preset_ids", [])
    explanations = _strings(fixture.get("explanations", ["研究预设尚未配置。"]), "fixture_manifest.explanations")
    _require(type(packet["explanation"]) is str and packet["explanation"] in explanations,
             "explanation", "DISPLAY_TEXT_NOT_ADMITTED")
    _require(packet["display_only"] is True and packet["execution_authorized"] is False,
             "research_boundary", "AUTHORITY_FORBIDDEN")
    boundary = _object(packet["research_boundary"], "synthetic no_order live_ready budget_effect", "research_boundary")
    _require(boundary["synthetic"] is True and boundary["no_order"] is True
             and boundary["live_ready"] is False and boundary["budget_effect"] == "NONE",
             "research_boundary", "AUTHORITY_FORBIDDEN")
    admitted = _list(fixture["allowed_definition_refs"], "fixture_manifest.allowed_definition_refs")
    _require(len({_digest(r) for r in admitted}) == len(admitted), "fixture_manifest.allowed_definition_refs", "DUPLICATE_VALUE")
    digests = fixture["allowed_evidence_key_digests"]
    for key, record in evidence.items():
        _token(key, "evidence_table.key")
        _require(key in digests, "evidence_table", "EVIDENCE_NOT_ADMITTED")
        _require(type(record) is dict, "evidence_table.record", "CLOSED_SHAPE")
        kind = record.get("type")
        if kind == "REQUEST_STATE":
            _object(record, "type scope requested request_status", "evidence_table.record")
            _object(record["request_status"], _STATUS, "evidence_table.request_status")
            if record["requested"] is not None:
                _selector(record["requested"], "evidence_table.requested", incomplete=True)
        elif kind == "EFFECTIVE_OBSERVATION":
            _object(record, "type qualification " + _SNAPSHOT, "evidence_table.record")
            _require(record["qualification"] in ("QUALIFIED", "UNKNOWN", "UNQUALIFIED"),
                     "evidence_table.qualification", "UNSUPPORTED_QUALIFICATION")
            _selector(record["effective"], "evidence_table.effective")
        else:
            raise _Issue("evidence_table.record", "TYPED_EVIDENCE_REQUIRED")
        _object(record["scope"], _SCOPE, "evidence_table.scope")
        _require(_same(record["scope"], scope), "evidence_table.scope", "SCOPE_MISMATCH")
        _require(_digest(record) == digests[key], "evidence_table.record", "EVIDENCE_DIGEST_MISMATCH")

    def evidence_ref(key, kind):
        _token(key, "evidence_ref")
        _require(key in evidence and key in digests, "evidence_ref", "EVIDENCE_MISSING")
        record = evidence[key]
        _require(record["type"] == kind, "evidence_ref", "EVIDENCE_TYPE_MISMATCH")
        return record

    definitions = _list(packet["definitions"], "definitions")
    lookup = {}
    now = _instant(clock, "fixture_clock") if clock is not None else None

    def resolve(selection):
        _selector(selection, "definition_ref")
        identity = (selection["preset_id"], selection["definition_ref"]["config_version"])
        _require(identity in lookup, "definition_ref", "DEFINITION_MISSING")
        result = lookup[identity]
        _require(_definition_ref(result) == selection and selection in admitted,
                 "definition_ref", "DEFINITION_NOT_ADMITTED")
        return result

    def number_map(value, allowed, path, *, weights=False):
        _require(type(value) is dict and value.keys() <= set(allowed), path, "NUMERIC_MAP_REQUIRED")
        for number in value.values():
            _number(number, path, maximum=1 if weights else None)
        if weights:
            _require(sum(value.values()) <= 1, path, "WEIGHTS_NOT_FULLY_FUNDED")

    def constraints(value, definition, path):
        _object(value, _CONSTRAINTS, path)
        _require(type(value["cash_symbol"]) is str and value["cash_symbol"] in assets,
                 path + ".cash_symbol", "CASH_IDENTIFIER_REQUIRED")
        semantics = definition["input_semantics"]
        basis = {"kind": "SYNTHETIC_NAV", "fixture_id": scope["fixture_id"],
                 "input_sha256": semantics["input_sha256"], "asof": semantics["asof"],
                 "baseline_ref": semantics["baseline_ref"]}
        for field, unit in _UNITS.items():
            items = value[field]
            if field in ("max_symbol_weights", "max_underlying_effective_exposure"):
                allowed = assets if field == "max_symbol_weights" else underlyings
                _require(type(items) is dict and items.keys() <= set(allowed), path + "." + field, "LIMIT_MAP_REQUIRED")
                items = list(items.values())
            else:
                items = [items]
            for item in items:
                label = path + "." + field
                _object(item, "mode value unit denominator source_ref", label)
                _require(item["unit"] == unit, label, "UNIT_MISMATCH")
                _require(_same(item["denominator"], basis), label, "DENOMINATOR_MISMATCH")
                source = item["source_ref"]
                if type(source) is dict:
                    _object(source, "preset_id config_version", label)
                    _require(source == {"preset_id": definition["preset_id"], "config_version": definition["config_version"]},
                             label, "CONSTRAINT_SOURCE_MISMATCH")
                else:
                    record = evidence_ref(source, "EFFECTIVE_OBSERVATION")
                    _object(record["source"], _SOURCE, label)
                    _require(record["effective"] == _definition_ref(definition)
                             and record["source"]["input_sha256"] == semantics["input_sha256"], label, "CONSTRAINT_SOURCE_MISMATCH")
                mode = item["mode"]
                _require(mode in ("ENABLED", "DISABLED", "UNSPECIFIED"), label, "LIMIT_MODE")
                if mode == "DISABLED":
                    _require(field == "max_one_way_risk_turnover" and item["value"] is None, label, "REQUIRED_CAP_NOT_DISABLED")
                elif mode == "UNSPECIFIED":
                    _require(item["value"] is None, label, "LIMIT_MODE")
                    raise _Issue(label, "INCOMPLETE_DEFINITION")
                else:
                    positive = field in ("max_effective_risk_exposure", "max_symbol_weights", "max_underlying_effective_exposure", "max_one_way_risk_turnover")
                    upper = 1 if field in ("max_symbol_weights", "max_one_way_risk_turnover", "min_available_cash_weight") else None
                    _number(item["value"], label, maximum=upper, positive=positive)

    fractions = []
    for definition in definitions:
        _object(definition, "config_version preset_id scope model_binding input_semantics constraints window qualification_rules content_sha256", "definitions.item")
        _token(definition["config_version"], "definitions.config_version")
        _require(definition["preset_id"] in presets, "definitions.preset_id", "PRESET_NOT_ADMITTED")
        _require(_same(definition["scope"], scope) and scope["strategy_profile"] is not None
                 and scope["strategy_revision"] is not None, "definitions.scope", "SCOPE_MISMATCH")
        _sha(definition["content_sha256"], "definitions.content_sha256")
        _require(_digest({k: v for k, v in definition.items() if k != "content_sha256"}) == definition["content_sha256"],
                 "definitions.content_sha256", "DEFINITION_DIGEST_MISMATCH")
        _require(_definition_ref(definition) in admitted, "definitions", "DEFINITION_NOT_ADMITTED")
        identity = (definition["preset_id"], definition["config_version"])
        _require(identity not in lookup, "definitions", "DUPLICATE_DEFINITION")
        lookup[identity] = definition
        model = _object(definition["model_binding"], _MODEL, "definitions.model_binding")
        _number(model["fraction"], "definitions.model_binding.fraction", maximum=0.5, positive=True)
        fractions.append(model["fraction"])
        for key in ("method", "preregistration_ref", "ues_revision", "qpk_revision", "evaluator_version"):
            _token(model[key], "definitions.model_binding." + key)
            _require(model[key].lower() not in ("main", "git-main", "head", "latest"), "definitions.model_binding." + key, "SOURCE_IDENTITY_REQUIRED")
        plans = fixture.get("preregistrations", {})
        _require(type(plans) is dict and model["preregistration_ref"] in plans
                 and _same(plans[model["preregistration_ref"]], model), "definitions.model_binding", "PREREGISTRATION_MISMATCH")
        semantics = _object(definition["input_semantics"], _INPUT, "definitions.input_semantics")
        _sha(semantics["input_sha256"], "definitions.input_semantics")
        _require(semantics["currency"] in currencies and semantics["return_semantics"] == "SYNTHETIC_SIMPLE_RETURNS"
                 and semantics["cash_semantics"] == "SYNTHETIC_CASH_SECURITY", "definitions.input_semantics", "INPUT_SEMANTICS_MISMATCH")
        _token(semantics["baseline_ref"], "definitions.input_semantics.baseline_ref")
        _instant(semantics["asof"], "definitions.input_semantics.asof")
        window = _object(definition["window"], "effective_from expires_at", "definitions.window")
        start, end = (_instant(window[k], "definitions.window." + k) for k in ("effective_from", "expires_at"))
        _require(start < end and now is not None, "definitions.window", "WINDOW_OR_CLOCK_REQUIRED")
        rules = _object(definition["qualification_rules"], "max_age_seconds required_qualification", "definitions.qualification_rules")
        _number(rules["max_age_seconds"], "definitions.qualification_rules.max_age_seconds")
        _require(rules["required_qualification"] == "QUALIFIED", "definitions.qualification_rules", "UNSUPPORTED_QUALIFICATION")
        constraints(definition["constraints"], definition, "definitions.constraints")
    _require(not fractions or all(f == fractions[0] for f in fractions), "definitions.model_binding.fraction", "COMMON_FRACTION_MISMATCH")

    def request_state(requested, state):
        complete = False
        if requested is not None:
            _selector(requested, "requested", incomplete=True)
            _require(requested["preset_id"] in presets, "requested", "PRESET_NOT_ADMITTED")
            if requested["definition_ref"] is not None:
                resolve(requested)
                complete = True
        _object(state, _STATUS, "request_status")
        reasons = _strings(state["reason_codes"], "request_status.reason_codes")
        missing = _strings(state["missing_parameters"], "request_status.missing_parameters")
        _require(set(missing) <= set(_MISSING), "request_status.missing_parameters", "UNKNOWN_MISSING_KEY")
        if complete:
            _require(not missing and state["status"] != "NOT_CONFIGURED", "request_status", "COMPLETE_REQUEST_HAS_MISSING_PARAMETERS")
        else:
            expected_missing = set(_MISSING)
            if requested is not None:
                expected_missing.remove("PRESET_SELECTION")
            if scope["strategy_profile"] is not None and scope["strategy_revision"] is not None:
                expected_missing.remove("STRATEGY_SCOPE_BINDING")
            _require(state["status"] == "NOT_CONFIGURED" and set(missing) == expected_missing
                     and "VALUES_UNSPECIFIED" in reasons
                     and (requested is not None or "PRESET_NOT_SELECTED" in reasons), "request_status", "NOT_CONFIGURED_REQUIRED")
        _require(state["status"] in ("NOT_CONFIGURED", "NOT_APPLIED", "APPLIED", "REJECTED", "UNKNOWN"), "request_status", "REQUEST_STATUS")
        if state["evidence_key"] is None:
            _require(state["processed_at"] is None, "request_status", "REQUEST_EVIDENCE_REQUIRED")
            if complete:
                _require(state["status"] == "UNKNOWN" and "READBACK_MISSING" in reasons, "request_status", "REQUEST_ACK_MISSING")
        else:
            record = evidence_ref(state["evidence_key"], "REQUEST_STATE")
            processed = _instant(state["processed_at"], "request_status.processed_at")
            _require(now is not None and processed <= now, "request_status.processed_at", "REQUEST_TIME")
            _require(_same(record["requested"], requested) and _same(record["request_status"], state),
                     "request_status", "REQUEST_EVIDENCE_BINDING")

        return complete

    for record in evidence.values():
        if record["type"] == "REQUEST_STATE":
            request_state(record["requested"], record["request_status"])
    requested, state = packet["requested"], packet["request_status"]
    complete = request_state(requested, state)

    effective = packet["effective"]
    if state["status"] == "APPLIED":
        _require(complete and effective == requested, "request_status", "APPLIED_REFERENCE_MISMATCH")
    timing = _object(packet["timing"], _TIMING, "timing")
    upstream = _list(packet["upstream"], "upstream")

    def observation(snapshot, *, current):
        effective = snapshot["effective"]
        timing = _object(snapshot["timing"], _TIMING, "timing")
        upstream = _list(snapshot["upstream"], "upstream")
        definition = resolve(effective)
        _require(now is not None, "fixture_clock", "EXPLICIT_UTC_REQUIRED")
        source = _object(snapshot["source"], _SOURCE, "source")
        semantics, model = definition["input_semantics"], definition["model_binding"]
        _token(source["consumer"], "source.consumer")
        _require(source["mode"] == "SYNTHETIC_RESEARCH"
                 and all(source[k] == model[k] for k in ("ues_revision", "qpk_revision", "evaluator_version"))
                 and source["input_sha256"] == semantics["input_sha256"], "source", "SOURCE_BINDING_MISMATCH")
        record = evidence_ref(source["evidence_ref"], "EFFECTIVE_OBSERVATION")
        _require(all(_same(record[k], snapshot[k]) for k in _SNAPSHOT.split()), "effective", "OBSERVATION_EVIDENCE_BINDING")
        if not current:
            _require(_same(snapshot, record), "evidence_table.record", "OBSERVATION_EVIDENCE_BINDING")
        instants = {k: _instant(v, "timing." + k) for k, v in timing.items()}
        start, end = (_instant(definition["window"][k], "definitions.window") for k in ("effective_from", "expires_at"))
        _require(start <= instants["asof"] <= instants["available_at"] <= instants["observed_at"] <= now
                 and instants["observed_at"] < end and (not current or now < end)
                 and instants["observed_at"] < instants["valid_until"] <= end,
                 "timing", "OBSERVATION_CAUSALITY_OR_POLICY_WINDOW")
        _require(timing["asof"] == semantics["asof"], "timing.asof", "INPUT_ASOF_MISMATCH")
        capital = _object(snapshot["capital_basis"], _CAPITAL, "capital_basis")
        _number(capital["nav_value"], "capital_basis.nav_value", positive=True)
        _require(capital["fixture_id"] == scope["fixture_id"] and capital["denominator_kind"] == "SYNTHETIC_NAV"
                 and capital["currency"] == semantics["currency"] and capital["currency"] in currencies
                 and capital["asof"] == timing["asof"] and capital["coverage"] == "COMPLETE" and capital["fx_ref"] is None,
                 "capital_basis", "CAPITAL_BINDING_MISMATCH")
        final = _object(snapshot["final_constraints"], "binding completeness items", "final_constraints")
        binding = _object(final["binding"], "effective_definition_ref scope input_sha256", "final_constraints.binding")
        _require(_same(binding, {"effective_definition_ref": effective, "scope": scope, "input_sha256": source["input_sha256"]}),
                 "final_constraints.binding", "FINAL_BINDING_MISMATCH")
        _require(final["completeness"] == "COMPLETE", "final_constraints", "COMPLETE_LIMITS_REQUIRED")
        constraints(final["items"], definition, "final_constraints.items")
        _require(final["items"]["cash_symbol"] == definition["constraints"]["cash_symbol"], "final_constraints.items", "CASH_IDENTIFIER_MISMATCH")
        for field in _UNITS:
            policy, actual = definition["constraints"][field], final["items"][field]
            if field in ("max_symbol_weights", "max_underlying_effective_exposure"):
                _require(policy.keys() <= actual.keys(), "final_constraints.items", "COMPLETE_LIMITS_REQUIRED")
                pairs = [(policy[k], actual[k]) for k in policy]
            else:
                pairs = [(policy, actual)]
            for cap, limit in pairs:
                if cap["mode"] == "ENABLED":
                    _require(limit["mode"] == "ENABLED" and (limit["value"] >= cap["value"] if field == "min_available_cash_weight"
                                                             else limit["value"] <= cap["value"]), "final_constraints.items", "FINAL_LIMIT_LOOSENED")
        seen = set()
        for row in upstream:
            _object(row, "stage subject status reason_codes evidence_ref", "upstream.item")
            for field in ("stage", "subject", "status"):
                _text(row[field], "upstream." + field)
            _strings(row["reason_codes"], "upstream.reason_codes")
            identity = (row["stage"], row["subject"])
            _require(identity not in seen, "upstream", "DUPLICATE_STAGE_SUBJECT")
            seen.add(identity)
            row_record = evidence_ref(row["evidence_ref"], "EFFECTIVE_OBSERVATION")
            _require(_same(row_record, record), "upstream", "UPSTREAM_OBSERVATION_MISMATCH")
        assessment = _object(snapshot["risk_assessment"], _ASSESSMENT, "risk_assessment")
        _text(assessment["status"], "risk_assessment.status")
        _strings(assessment["reason_codes"], "risk_assessment.reason_codes")
        if assessment["risk_scalar"] is not None:
            _number(assessment["risk_scalar"], "risk_assessment.risk_scalar", maximum=1)
        if assessment["recommended_target_weights"] is not None:
            number_map(assessment["recommended_target_weights"], assets, "risk_assessment.recommended_target_weights", weights=True)
        metrics = _object(assessment["metrics"], _METRICS, "risk_assessment.metrics")
        if metrics["nominal_weight"] is not None:
            number_map(metrics["nominal_weight"], assets, "metrics.nominal_weight", weights=True)
        if metrics["underlying_effective_exposure"] is not None:
            number_map(metrics["underlying_effective_exposure"], underlyings, "metrics.underlying_effective_exposure")
        for field in ("effective_risk_exposure", "one_way_risk_turnover"):
            if metrics[field] is not None:
                _number(metrics[field], "metrics." + field)
        qualified = record["qualification"] == "QUALIFIED"
        if assessment["status"] == "APPROVE":
            _require(qualified and assessment["risk_scalar"] is not None and assessment["recommended_target_weights"] is not None
                     and all(v is not None for v in metrics.values()), "risk_assessment", "APPROVE_REQUIRES_COMPLETE_QUALIFIED_INPUT")
        age = (now - instants["observed_at"]).total_seconds()
        expected = ("UNKNOWN" if not qualified else "STALE" if now >= instants["valid_until"]
                    or age > definition["qualification_rules"]["max_age_seconds"] else "FRESH")
        if current:
            _require(snapshot["freshness"] == expected, "freshness", "FRESHNESS_MISMATCH")

    for record in evidence.values():
        if record["type"] == "EFFECTIVE_OBSERVATION":
            observation(record, current=False)
    if effective is None:
        _require(packet["freshness"] == "MISSING" and not upstream
                 and all(v is None for v in timing.values())
                 and all(packet[k] is None for k in ("source", "capital_basis", "final_constraints", "risk_assessment")),
                 "effective", "MISSING_OBSERVATION_REQUIRED")
        return
    observation(packet, current=True)


def validate_packet(packet, *, fixture_manifest, evidence_table, fixture_clock):
    """Inspect explicit parsed JSON inputs and return validity/issues only."""
    try:
        _inspect(packet, fixture_manifest, evidence_table, fixture_clock)
    except _Issue as issue:
        return {"valid": False, "issues": [{"path": issue.path, "code": issue.code}]}
    return {"valid": True, "issues": []}
