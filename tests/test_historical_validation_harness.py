from __future__ import annotations

import copy
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from us_equity_strategies.research import historical_validation_harness as harness


WINDOW = {"after_session": "2026-08-25", "through_session": "2026-10-30"}
SYMBOLS = ("QQQM", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI")


def encoded(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()


def frozen(tmp_path, documents):
    root = tmp_path / "frozen"
    root.mkdir()
    manifest = {"schema_version": "qsl.historical_validation_harness.v1",
                "evidence_kind": "synthetic_fixture", "market_window": dict(WINDOW), "files": {}}
    for role, document in documents.items():
        raw = document if isinstance(document, bytes) else encoded(document)
        name = role + ".json"
        (root / name).write_bytes(raw)
        manifest["files"][role] = {"path": name, "sha256": hashlib.sha256(raw).hexdigest(),
                                   "size_bytes": len(raw)}
    return root, seal(root, manifest)


def seal(root, manifest):
    raw = encoded(manifest)
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def run(root, digest):
    return harness.check_harness(root, manifest_sha256=digest)


def codes(result):
    if isinstance(result, dict):
        return ({result["code"]} if "code" in result else set()).union(
            *(codes(value) for value in result.values()))
    if isinstance(result, list):
        return set().union(*(codes(value) for value in result))
    return set()


def market_documents():
    # An explicitly artificial calendar. It is not an exchange-session claim.
    days = [(date(2026, 8, 26) + timedelta(days=offset)).isoformat() for offset in range(66)]
    sessions = [days[0], days[-1]]
    result = {"calendar": {"schema_version": "qsl.market_sessions.v1",
              "calendar_id": "synthetic-only", "timezone": "America/New_York",
              "market_window": dict(WINDOW), "sessions": sessions,
              "non_sessions": [day for day in days if day not in sessions]}}
    for symbol in SYMBOLS:
        for basis in ("raw", "adjusted"):
            result[symbol + "." + basis] = {
                "schema_version": "qsl.market_prices.v1", "symbol": symbol, "basis": basis,
                "currency": "USD", "volume_basis": "raw_shares",
                "adjustment_convention": "none" if basis == "raw" else "split_and_cash_dividend",
                "rows": [{"session": day, "open": "10", "high": "11", "low": "9",
                          "close": "10", "volume": "100"} for day in sessions],
            }
        result[symbol + ".corporate_actions"] = {
            "schema_version": "qsl.market_actions.v1", "symbol": symbol, "currency": "USD", "rows": []}
    return result


def assert_no_authority(result):
    assert result["status"] in {"BLOCKED", "FAIL"}
    for key in ("historical_parity_established", "production_ready", "execution_authorized",
                "source_authenticity_established", "point_in_time_availability_verified"):
        assert result[key] is False


def test_missing_materials_are_independent_blockers(tmp_path):
    root, digest = frozen(tmp_path, {})
    result = run(root, digest)
    assert set(result["checks"]) == {"state_continuity", "order_mapping", "market_coverage"}
    assert all(value["status"] == "BLOCKED" for value in result["checks"].values())
    assert result["integrity"]["status"] == "PASS"
    assert_no_authority(result)


def test_market_rows_only_pass_against_the_declared_calendar(tmp_path):
    root, digest = frozen(tmp_path, market_documents())
    result = run(root, digest)
    market = result["checks"]["market_coverage"]
    assert market["local_conformance"] == "PASS", result
    assert market["coverage_for_declared_sessions"] is True
    assert market["actual_market_coverage_established"] is False
    assert market["corporate_actions_complete_verified"] is False
    assert market["raw_adjusted_consistency_verified"] is False
    assert market["status"] == "BLOCKED"
    assert result["checks"]["state_continuity"]["status"] == "BLOCKED"
    assert result["checks"]["order_mapping"]["status"] == "BLOCKED"
    assert_no_authority(result)


@pytest.mark.parametrize("raw,code", [
    (b'{"schema_version":1,"schema_version":2}', "DUPLICATE_JSON_KEY"),
    (b'{"value":NaN}', "NONFINITE_JSON_NUMBER"),
    (b'{"value":1e999}', "NONFINITE_JSON_NUMBER"),
    (b'{"schema_version":', "MALFORMED_JSON"),
])
def test_malformed_role_never_suppresses_other_checks(tmp_path, raw, code):
    documents = market_documents()
    documents["state_continuity"] = raw
    root, digest = frozen(tmp_path, documents)
    result = run(root, digest)
    assert result["checks"]["state_continuity"]["status"] == "FAIL"
    assert code in codes(result)
    assert result["checks"]["market_coverage"]["local_conformance"] == "PASS"
    assert_no_authority(result)


def test_row_gap_is_not_closed_by_updated_or_coverage_claim(tmp_path):
    documents = market_documents()
    raw = documents["QQQM.raw"]
    raw["rows"].pop()
    raw.update(updated="2026-10-31T23:59:59Z", coverage_end_verified=True,
               complete=True, last_session="2026-10-30")
    root, digest = frozen(tmp_path, documents)
    result = run(root, digest)
    role = result["checks"]["market_coverage"]["symbols"]["QQQM"]["raw"]
    assert role["status"] == "BLOCKED"
    assert role["missing_sessions"] == ["2026-10-30"]
    assert result["checks"]["market_coverage"]["coverage_for_declared_sessions"] is False
    assert_no_authority(result)


def test_calendar_omission_cannot_shrink_the_window(tmp_path):
    documents = market_documents()
    documents["calendar"]["non_sessions"].pop()
    root, digest = frozen(tmp_path, documents)
    result = run(root, digest)
    assert "CALENDAR_PARTITION_INCOMPLETE" in codes(result)
    assert result["checks"]["market_coverage"]["local_conformance"] != "PASS"


def test_fixed_adapters_missing_is_exercised_without_success_mocks(tmp_path, monkeypatch):
    root, digest = frozen(tmp_path, {"state_continuity": {}, "order_mapping": {}})
    empty = tmp_path / "package-without-sibling-prs"
    empty.mkdir()
    monkeypatch.setattr(harness, "_PACKAGE_DIR", empty)
    result = run(root, digest)
    for key in ("state_continuity", "order_mapping"):
        assert result["checks"][key]["status"] == "BLOCKED"
        assert "ADAPTER_UNAVAILABLE" in codes(result["checks"][key])
    assert_no_authority(result)


@pytest.mark.parametrize("role,test_module,factory,success_field,success_value", [
    ("state_continuity", "tests.test_historical_state_continuity", "fixture", "conformance_status", "PASS"),
    ("order_mapping", "tests.test_historical_order_mapping", "document", "local_consistency", "CONSISTENT"),
])
def test_real_sibling_checker_success_and_contradiction_propagate(
    tmp_path, role, test_module, factory, success_field, success_value,
):
    pytest.importorskip("us_equity_strategies.research.historical_" + role,
                        reason="Sibling draft checker is not part of this branch")
    fixtures = pytest.importorskip(test_module, reason="Sibling draft fixture is not part of this branch")
    document = getattr(fixtures, factory)()
    root, digest = frozen(tmp_path, {role: document})
    result = run(root, digest)
    assert result["checks"][role]["checker_result"][success_field] == success_value
    assert result["checks"][role]["status"] == "BLOCKED"
    assert_no_authority(result)
    if role == "state_continuity":
        document["runs"][0]["evaluations"][0]["head_before"]["payload"]["state"]["alpha_epoch"] = 123
    else:
        document["plan"]["payload"]["planned_quantity"] = "999"
    other = tmp_path / "contradiction"
    other.mkdir()
    bad_root, bad_digest = frozen(other, {role: document})
    bad = run(bad_root, bad_digest)
    assert bad["checks"][role]["local_conformance"] == "FAIL"
    assert bad["status"] == "FAIL"
    assert_no_authority(bad)


@pytest.mark.parametrize("replacement", ["../outside.json", "/tmp/outside.json", ".", "a/../b.json",
                                       "a//b.json", "a\\b.json"])
def test_manifest_paths_cannot_escape_or_select_modules(tmp_path, replacement):
    root, _ = frozen(tmp_path, {"state_continuity": {}})
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"]["state_continuity"]["path"] = replacement
    result = run(root, seal(root, manifest))
    assert result["checks"]["state_continuity"]["status"] == "FAIL"
    assert "UNSAFE_LOCAL_PATH" in codes(result)
    assert "checker_result" not in result["checks"]["state_continuity"]


@pytest.mark.parametrize("kind", ["root_symlink", "parent_symlink", "file_symlink", "hardlink", "fifo"])
def test_read_boundary_rejects_symlinks_shared_inodes_and_special_files(tmp_path, kind):
    root, digest = frozen(tmp_path, {"state_continuity": {}})
    if kind == "root_symlink":
        link = tmp_path / "root-link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    elif kind == "parent_symlink":
        link = tmp_path / "parent-link"
        link.symlink_to(tmp_path, target_is_directory=True)
        root = link / "frozen"
    else:
        item = root / "state_continuity.json"
        outside = tmp_path / "outside.json"
        outside.write_bytes(item.read_bytes())
        item.unlink()
        if kind == "file_symlink":
            item.symlink_to(outside)
        elif kind == "hardlink":
            os.link(outside, item)
        else:
            os.mkfifo(item)
    result = run(root, digest)
    assert result["status"] == "FAIL"
    assert "checker_result" not in result["checks"]["state_continuity"]


def test_hashes_are_verified_before_checker_import_or_invocation(tmp_path, monkeypatch):
    root, digest = frozen(tmp_path, {"state_continuity": {}})
    (root / "state_continuity.json").write_bytes(b'{"verified":true}')

    def forbidden(*_args, **_kwargs):
        pytest.fail("Adapter import must not occur after an input hash failure")

    monkeypatch.setattr(harness.importlib, "import_module", forbidden)
    result = run(root, digest)
    assert "INPUT_DIGEST_OR_SIZE_MISMATCH" in codes(result)
    assert result["checks"]["state_continuity"]["status"] == "FAIL"


def test_adapter_source_pin_is_verified_before_import(tmp_path, monkeypatch):
    root, digest = frozen(tmp_path, {"state_continuity": {}})
    code = tmp_path / "altered-source"
    code.mkdir()
    (code / "historical_state_continuity.py").write_text("raise RuntimeError('must never import')\n")
    monkeypatch.setattr(harness, "_PACKAGE_DIR", code)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Source mismatch must be detected before import")

    monkeypatch.setattr(harness.importlib, "import_module", forbidden)
    result = run(root, digest)
    assert "ADAPTER_SOURCE_REVISION_UNSUPPORTED" in codes(result)
    assert result["checks"]["state_continuity"]["status"] == "BLOCKED"


@pytest.mark.parametrize("mutation,code", [
    (lambda value: value.update(schema_version="future.v2"), "UNSUPPORTED_MANIFEST_SCHEMA"),
    (lambda value: value.update(adapter_module="untrusted.module"), "UNEXPECTED_MANIFEST_FIELDS"),
    (lambda value: value["market_window"].update(through_session="2026-08-25"), "MARKET_WINDOW_MISMATCH"),
])
def test_manifest_versions_import_selection_and_window_are_fixed(tmp_path, mutation, code):
    root, _ = frozen(tmp_path, {})
    value = json.loads((root / "manifest.json").read_text())
    mutation(value)
    result = run(root, seal(root, value))
    assert code in codes(result)
    assert_no_authority(result)


def test_wrong_external_manifest_digest_and_missing_role_file_are_distinct(tmp_path):
    root, digest = frozen(tmp_path, {"state_continuity": {}})
    assert "MANIFEST_DIGEST_MISMATCH" in codes(run(root, "0" * 64))
    (root / "state_continuity.json").unlink()
    result = run(root, digest)
    assert result["checks"]["state_continuity"]["status"] == "BLOCKED"
    assert "INPUT_FILE_MISSING" in codes(result)


@pytest.mark.parametrize("mutation,code", [
    (lambda value: value["rows"].append(copy.deepcopy(value["rows"][-1])), "DUPLICATE_OR_UNSORTED_PRICE_SESSION"),
    (lambda value: value["rows"][0].update(close="12"), "OHLC_BOUNDS_CONTRADICTION"),
    (lambda value: value["rows"][0].update(close="NaN"), "BOUNDED_DECIMAL_STRING_REQUIRED"),
    (lambda value: value["rows"][0].update(volume="-1"), "BOUNDED_DECIMAL_STRING_REQUIRED"),
    (lambda value: value["rows"][0].update(open=True), "BOUNDED_DECIMAL_STRING_REQUIRED"),
    (lambda value: value["rows"][0].update(volume="0.1"), "NONINTEGER_RAW_VOLUME"),
    (lambda value: value["rows"][0].update(session="2026-08-27"), "PRICE_ON_DECLARED_NON_SESSION"),
    (lambda value: value.update(volume_basis=[]), "MALFORMED_MARKET_RECORD"),
])
def test_bad_market_rows_are_local_failures_not_claimed_coverage(tmp_path, mutation, code):
    documents = market_documents()
    mutation(documents["QQQM.raw"])
    root, digest = frozen(tmp_path, documents)
    result = run(root, digest)
    market = result["checks"]["market_coverage"]
    assert market["symbols"]["QQQM"]["raw"]["status"] == "FAIL"
    assert code in codes(market)
    assert market["symbols"]["SCHD"]["raw"]["status"] == "PASS"
    assert market["coverage_for_declared_sessions"] is False
    assert_no_authority(result)


@pytest.mark.parametrize("missing", ["calendar", "QQQI.adjusted", "SGOV.corporate_actions"])
def test_partial_market_materials_do_not_fill_other_roles(tmp_path, missing):
    documents = market_documents()
    del documents[missing]
    root, digest = frozen(tmp_path, documents)
    result = run(root, digest)
    market = result["checks"]["market_coverage"]
    assert market["status"] == market["local_conformance"] == "BLOCKED"
    if missing != "SGOV.corporate_actions":
        assert market["coverage_for_declared_sessions"] is False
    assert market["corporate_actions_complete_verified"] is False


@pytest.mark.parametrize("mutation,code", [
    (lambda value: value["non_sessions"].append(value["sessions"][0]), "SESSION_LIST_DUPLICATE_OR_UNSORTED"),
    (lambda value: value["sessions"].append(value["sessions"][-1]), "SESSION_LIST_DUPLICATE_OR_UNSORTED"),
    (lambda value: value.update(schema_version="unknown_alpaca_schema"), "UNSUPPORTED_CALENDAR_SCHEMA"),
])
def test_calendar_validation_never_infers_weekday_sessions(tmp_path, mutation, code):
    documents = market_documents()
    mutation(documents["calendar"])
    root, digest = frozen(tmp_path, documents)
    result = run(root, digest)
    assert code in codes(result)
    assert result["checks"]["market_coverage"]["coverage_for_declared_sessions"] is False


def checklist():
    return {"schema_version": "qsl.local_readonly_feedback.v1", "template_state": "OBSERVED_REDACTED",
            "performed_on_user_machine": True, "observed_at_utc": "2026-10-31T12:00:00Z",
            "scope": {"listing_and_bounded_structure_only": True, "body_bytes_per_object_limit": 4096,
                      "maximum_body_requests": 24, "full_object_reads_allowed": False,
                      "tail_reads_allowed": False, "gcs_exports_or_uploads_allowed": False},
            "execution_summary": {"listing_commands_executed": 12, "metadata_describes_executed": 0,
                "body_requests_attempted": 0, "body_bytes_read": 0, "redaction_review_completed": True},
            "prefix_observations": [{"prefix_alias": alias, "status": "LIST_COMMAND_SUCCEEDED",
                "listed_object_count": 0, "output_limit": 20,
                "pagination": {"mode": "LIMITED_CLI_OR_RETAINED_JSON_API", "api_pages_observed": None,
                    "next_page_token_present": None, "output_cap_reached": False,
                    "stop_reason": "PAGINATION_UNKNOWN"}, "listing_complete_verified": False,
                "historical_versions_coverage_verified": False, "sample_object_aliases": [],
                "missing_or_blocked": []} for alias in (
                ["market_" + symbol for symbol in SYMBOLS] + ["runtime_" + broker + "_" + profile
                for broker in ("schwab", "ibkr", "longbridge") for profile in ("tqqq", "soxl")])],
            "object_structure_observations": [], "market_coverage": {
                "required_last_session": "2026-10-30", "post_cutoff_calendar_interval": ["2026-08-26", "2026-10-30"],
                "post_cutoff_status": "UNVERIFIED", "coverage_end_verified": False}}


def sampled_checklist():
    value = checklist()
    value["execution_summary"].update(metadata_describes_executed=1, body_requests_attempted=1,
                                      body_bytes_read=4096)
    value["prefix_observations"][0].update(listed_object_count=1, sample_object_aliases=["obj_001"])
    value["object_structure_observations"] = [{
        "object_alias": "obj_001", "prefix_alias": "market_QQQM", "role": "raw",
        "generation": "1001", "metageneration": "1", "metadata_status": "OBSERVED",
        "bounded_read_status": "BOUNDED_PREFIX_SAVED", "http_status": 206,
        "requested_range": "bytes=0-4095", "confirmed_range": "bytes 0-4095/8192",
        "body_bytes_read": 4096, "schema_status": "FIELDS_OBSERVED",
        "fields": [{"sanitized_path": "session", "observed_local_types": ["string"],
                    "complete_record_basis": "complete_csv_header", "dynamic_key_replaced": False}],
        "date_observations": [], "stop_or_missing_codes": [], "reviewed_for_sharing": True,
    }]
    return value


@pytest.mark.parametrize("case", ["listing_over_cap", "sample_reports_http_200"])
def test_review_observed_checklist_limit_and_http_contradictions_fail(tmp_path, case):
    value = sampled_checklist()
    if case == "listing_over_cap":
        value["prefix_observations"][0]["listed_object_count"] = 21
    else:
        value["object_structure_observations"][0]["http_status"] = 200
    root, digest = frozen(tmp_path, {"checklist": value})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == "FAIL"
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("field,value,expected", [
    ("requested_range", "bytes=4096-8191", "FAIL"),
    ("requested_range", None, "BLOCKED"),
    ("confirmed_range", "bytes 0-4095/4096", "FAIL"),
    ("confirmed_range", "bytes 0-4095/2000", "FAIL"),
    ("confirmed_range", None, "BLOCKED"),
    ("generation", 1001, "FAIL"),
    ("generation", None, "BLOCKED"),
    ("metageneration", "0", "FAIL"),
    ("schema_status", "UNVERIFIED", "BLOCKED"),
    ("body_bytes_read", 2048, "BLOCKED"),
    ("stop_or_missing_codes", ["JSON_PREFIX_INCOMPLETE"], "BLOCKED"),
])
def test_review_sample_range_versions_and_unverified_states_are_retained(tmp_path, field, value, expected):
    document = sampled_checklist()
    document["object_structure_observations"][0][field] = value
    root, digest = frozen(tmp_path, {"checklist": document})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == expected, result
    assert result["checklist"]["sample_results"][0]["local_conformance"] == expected
    assert_no_authority(result)


@pytest.mark.parametrize("field", ["listing_complete_verified", "historical_versions_coverage_verified"])
def test_review_limited_listing_cannot_claim_complete_inventory(tmp_path, field):
    document = checklist()
    document["prefix_observations"][0][field] = True
    root, digest = frozen(tmp_path, {"checklist": document})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == "FAIL"
    assert "CHECKLIST_COMPLETENESS_CLAIM_UNSUPPORTED" in codes(result)


def test_review_unobserved_prefix_and_sample_are_not_promoted(tmp_path):
    document = sampled_checklist()
    document["prefix_observations"][1].update(status="NOT_RUN", listed_object_count=None)
    document["object_structure_observations"][0]["schema_status"] = "UNVERIFIED"
    root, digest = frozen(tmp_path, {"checklist": document})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == "BLOCKED"
    assert result["checklist"]["prefix_results"]["market_SCHD"]["local_conformance"] == "BLOCKED"
    assert result["checklist"]["sample_results"][0]["local_conformance"] == "BLOCKED"


def test_review_complete_structural_sample_still_has_no_header_authentication(tmp_path):
    root, digest = frozen(tmp_path, {"checklist": sampled_checklist()})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == "PASS", result
    assert result["checklist"]["sample_results"][0]["metadata_header_binding_verified"] is False
    assert result["checklist"]["status"] == "BLOCKED"
    assert_no_authority(result)


def test_review_missing_listing_cannot_downgrade_observed_unsafe_body_read(tmp_path):
    document = sampled_checklist()
    document["prefix_observations"][0]["sample_object_aliases"] = None
    document["object_structure_observations"][0]["http_status"] = 200
    root, digest = frozen(tmp_path, {"checklist": document})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == "FAIL"
    assert result["checklist"]["sample_results"][0]["local_conformance"] == "FAIL"
    assert "BODY_READ_WITHOUT_206" in codes(result)


@pytest.mark.parametrize("mutation,expected,code", [
    (lambda value: value.update(template_state="NOT_EXECUTED_TEMPLATE"), "BLOCKED", "CHECKLIST_NOT_OBSERVED"),
    (lambda value: value["market_coverage"].update(coverage_end_verified=True), "FAIL", "CHECKLIST_COVERAGE_CLAIM_UNSUPPORTED"),
    (lambda value: value["execution_summary"].update(body_bytes_read=1), "FAIL", "CHECKLIST_OBSERVATION_BUDGET_EXCEEDED"),
    (lambda value: value["scope"].update(full_object_reads_allowed=0), "FAIL", "CHECKLIST_SCOPE_MISMATCH"),
])
def test_checklist_placeholders_claims_and_limits_never_authorize(tmp_path, mutation, expected, code):
    value = checklist()
    mutation(value)
    root, digest = frozen(tmp_path, {"checklist": value})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == expected
    assert code in codes(result)
    assert all(item["status"] == "BLOCKED" for item in result["checks"].values())
    assert_no_authority(result)


def test_valid_bounded_feedback_does_not_replace_state_order_or_prices(tmp_path):
    root, digest = frozen(tmp_path, {"checklist": checklist()})
    result = run(root, digest)
    assert result["checklist"]["local_conformance"] == "PASS"
    assert result["checklist"]["status"] == "BLOCKED"
    assert all(item["status"] == "BLOCKED" for item in result["checks"].values())
    assert_no_authority(result)


@pytest.mark.parametrize("kind", ["existing", "input", "symlink", "hardlink", "parent_symlink", "parent_traversal"])
def test_cli_cannot_overwrite_or_modify_frozen_inputs(tmp_path, capsys, kind):
    root, digest = frozen(tmp_path, {})
    original = (root / "manifest.json").read_bytes()
    output = tmp_path / "report.json"
    if kind == "existing":
        output.write_text("preserve")
    elif kind == "input":
        output = root / "new-file.json"
    elif kind == "symlink":
        output.symlink_to(root / "manifest.json")
    elif kind == "hardlink":
        os.link(root / "manifest.json", output)
    elif kind == "parent_symlink":
        parent = tmp_path / "linked-parent"
        parent.symlink_to(tmp_path, target_is_directory=True)
        output = parent / "report.json"
    else:
        output = root / ".." / "report.json"
    code = harness.main([str(root), "--manifest-sha256", digest, "--output", str(output)])
    result = json.loads(capsys.readouterr().out)
    assert code == 1 and result["status"] == "FAIL"
    assert (root / "manifest.json").read_bytes() == original
    assert not (root / "new-file.json").exists()
    if kind == "existing":
        assert output.read_text() == "preserve"


def test_standalone_cli_without_sibling_prs_is_blocked_not_import_failure(tmp_path):
    root, digest = frozen(tmp_path, {"state_continuity": {}, "order_mapping": {}})
    package = tmp_path / "isolated" / "us_equity_strategies" / "research"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "historical_validation_harness.py").write_bytes(Path(harness.__file__).read_bytes())
    output = tmp_path / "report.json"
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    completed = subprocess.run([
        sys.executable, "-m", "us_equity_strategies.research.historical_validation_harness", str(root),
        "--manifest-sha256", digest, "--output", str(output),
    ], cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(package.parent.parent)},
        text=True, capture_output=True, timeout=20)
    assert completed.returncode == 2, completed.stderr
    result = json.loads(output.read_text())
    for role in ("state_continuity", "order_mapping"):
        assert result["checks"][role]["status"] == "BLOCKED"
        assert "ADAPTER_UNAVAILABLE" in codes(result["checks"][role])
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    assert_no_authority(result)

