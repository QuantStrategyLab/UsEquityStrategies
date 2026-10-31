from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from us_equity_strategies.research.historical_gcs_intake import build_intake_plan, main


def discovery():
    return {
        "schema_version": "qsl.gcs_discovery_summary.v1",
        "evidence_kind": "operator_reported_discovery",
        "runtime": {
            "bucket_uri": "gs://offline-runtime-example",
            "project": "unverified-storage-project",
            "execution_prefix": "execution-reports/",
            "platforms": ["charles_schwab", "interactive_brokers", "longbridge"],
            "profiles": ["tqqq_growth_income", "soxl_soxx_trend_income"],
            "auxiliary_prefixes": ["strategy-artifacts/", "runtime_daily/"],
            "reported_counts": {"bucket_objects": 9000, "six_prefix_objects": 1000},
        },
        "market": {
            "bucket_uri": "gs://offline-market-example",
            "input_prefix": "research/input/example/",
            "reported_last_session": "2026-08-25",
            "required_components": ["raw", "adjusted", "corporate_actions"],
            "component_object_names_known": False,
            "tickers": {
                "QQQM": {"reported_first_session": "2020-10-13", "reported_no_internal_gaps": True,
                         "earlier_history_missing": False, "inception_gap_reported": False},
                "SCHD": {"reported_first_session": "2016-01-04", "reported_no_internal_gaps": None,
                         "earlier_history_missing": True, "inception_gap_reported": False},
                "DGRO": {"reported_first_session": "2016-01-04", "reported_no_internal_gaps": None,
                         "earlier_history_missing": True, "inception_gap_reported": False},
                **{ticker: {"reported_first_session": None, "reported_no_internal_gaps": None,
                            "earlier_history_missing": None, "inception_gap_reported": True,
                            "reported_missing_inception_sessions": "1-2"}
                   for ticker in ("SGOV", "SPYI", "QQQI")},
            },
        },
    }


def symbols(plan):
    return {row["ticker"]: row for row in plan["market"]["symbol_selections"]}


def codes(plan):
    return {row["code"] for row in plan["findings"]}


def test_plan_has_only_reported_prefixes_and_never_authorizes_operations():
    plan = build_intake_plan(discovery(), "2026-08-01", "2026-10-30")
    assert plan["status"] == "BLOCKED"
    assert plan["plan_state"] == "PLAN_CREATED"
    selections = plan["runtime"]["execution_selections"]
    assert len(selections) == 6
    assert len({row["directory_prefix"] for row in selections}) == 6
    assert {row["directory_prefix"] for row in selections} == {
        f"execution-reports/{platform}/{profile}/"
        for platform in ("charles_schwab", "interactive_brokers", "longbridge")
        for profile in ("tqqq_growth_income", "soxl_soxx_trend_income")
    }
    for row in selections:
        assert row["account_scope"] == "CANDIDATE_PREFIX_ONLY"
        assert row["account_alias"] is None
    for ticker, row in symbols(plan).items():
        assert row["directory_prefix"] == f"research/input/example/{ticker}/"
        assert row["required_component_roles"] == ["raw", "adjusted", "corporate_actions"]
        assert row["component_object_names"] is None
    assert plan["market"]["normalization_adapter_status"] == "UNVERIFIED"
    assert plan["market"]["existing_importer_compatibility_assumed"] is False
    assert plan["market"]["future_data_fill_allowed"] is False
    assert "MARKET_SCHEMA_AND_NORMALIZATION_ADAPTER_UNVERIFIED" in codes(plan)
    for key in ("export_authorized", "upload_authorized", "execution_authorized", "production_ready"):
        assert plan[key] is False
    assert {row["layer"] for row in plan["evidence_layers"]} == {"ledger_reconciliation", "price_recalculation"}


@pytest.mark.parametrize("first,expected", [("2026-08-01", "2026-08-26"), ("2026-09-01", "2026-09-01")])
def test_post_cutoff_gap_is_a_calendar_interval_not_a_session_count(first, expected):
    plan = build_intake_plan(discovery(), first, "2026-10-30")
    for row in symbols(plan).values():
        gaps = [gap for gap in row["coverage"]["gap_intervals"] if gap["reason"] == "AFTER_REPORTED_LAST_SESSION"]
        assert len(gaps) == 1
        assert gaps[0]["first_date"] == expected
        assert gaps[0]["last_date"] == "2026-10-30"
        assert gaps[0]["basis"] == "INCLUSIVE_CALENDAR_DATES_NOT_TRADING_SESSION_COUNTS"
        assert "session_count" not in gaps[0] and "trading_days" not in gaps[0]


@pytest.mark.parametrize("last", ["2026-08-24", "2026-08-25"])
def test_cutoff_or_earlier_never_removes_authentication_and_completeness_blockers(last):
    plan = build_intake_plan(discovery(), "2026-08-01", last)
    assert plan["status"] == "BLOCKED"
    assert all(not any(gap["reason"] == "AFTER_REPORTED_LAST_SESSION" for gap in row["coverage"]["gap_intervals"])
               for row in symbols(plan).values())
    assert {"SOURCE_AUTHENTICATION_UNVERIFIED", "POINT_IN_TIME_AVAILABILITY_UNVERIFIED",
            "OBJECT_AND_SESSION_COMPLETENESS_UNVERIFIED"} <= codes(plan)


def test_warmup_extends_the_required_window_without_changing_the_requested_window():
    plan = build_intake_plan(discovery(), "2026-08-01", "2026-08-25", warmup_first_session="2015-12-20")
    assert plan["requested_window"]["first_session"] == "2026-08-01"
    assert plan["requested_window"]["effective_first_session"] == "2015-12-20"
    assert plan["requested_window"]["window_shortened"] is False
    for ticker in ("SCHD", "DGRO"):
        gap = symbols(plan)[ticker]["coverage"]["gap_intervals"][0]
        assert (gap["first_date"], gap["last_date"]) == ("2015-12-20", "2016-01-03")
        assert gap["affects_warmup"] is True and gap["affects_requested_window"] is False
    qqqm = symbols(plan)["QQQM"]["coverage"]["gap_intervals"][0]
    assert qqqm["last_date"] == "2020-10-12"


def test_request_wholly_before_reported_first_does_not_become_a_shorter_window():
    plan = build_intake_plan(discovery(), "2020-10-01", "2020-10-12")
    gap = symbols(plan)["QQQM"]["coverage"]["gap_intervals"][0]
    assert (gap["first_date"], gap["last_date"]) == ("2020-10-01", "2020-10-12")
    assert plan["requested_window"]["last_session"] == "2020-10-12"
    assert plan["status"] == "BLOCKED"


def test_unknown_first_dates_and_reported_inception_gaps_do_not_invent_dates():
    plan = build_intake_plan(discovery(), "2011-01-03", "2026-08-25")
    for ticker in ("SGOV", "SPYI", "QQQI"):
        coverage = symbols(plan)[ticker]["coverage"]
        assert coverage["reported_first_session"] is None
        assert coverage["first_boundary_status"] == "UNVERIFIED"
        assert coverage["gap_intervals"] == []
        assert coverage["inception_gap"]["missing_session_dates"] is None
        assert coverage["inception_gap"]["reported_session_count_range"] == {"minimum": 1, "maximum": 2}
        assert coverage["inception_gap"]["status"] == "UNVERIFIED_OPERATOR_REPORT"


@pytest.mark.parametrize("first,last,warmup", [
    ("2026-10-30", "2026-08-25", None),
    ("2026-08-01", "2026-08-25", "2026-08-02"),
    ("2026-02-30", "2026-08-25", None),
    ("20260801", "2026-08-25", None),
    (True, "2026-08-25", None),
])
def test_malformed_or_reversed_dates_return_a_machine_failure(first, last, warmup):
    plan = build_intake_plan(discovery(), first, last, warmup_first_session=warmup)
    assert plan["status"] == "FAIL"
    assert plan["plan_state"] == "NOT_CREATED"
    assert plan["export_authorized"] is False


@pytest.mark.parametrize("prefix", ["", "/execution/", "../private/", "reports/../private/",
                                   "reports//nested/", "reports\\private/", "reports/%2e%2e/",
                                   "gs://other-bucket/path/", "reports?token=hidden", "reports/#fragment"])
def test_unsafe_execution_prefixes_are_rejected(prefix):
    data = discovery()
    data["runtime"]["execution_prefix"] = prefix
    plan = build_intake_plan(data, "2026-08-01", "2026-08-25")
    assert plan["status"] == "FAIL"
    assert "INVALID_DIRECTORY_PREFIX" in codes(plan)


@pytest.mark.parametrize("location", ["market", "auxiliary"])
def test_other_prefix_roles_have_the_same_traversal_guard(location):
    data = discovery()
    if location == "market":
        data["market"]["input_prefix"] = "../account-object/"
    else:
        data["runtime"]["auxiliary_prefixes"] = ["../account-object/"]
    assert build_intake_plan(data, "2026-08-01", "2026-08-25")["status"] == "FAIL"


@pytest.mark.parametrize("uri", ["https://example.org/bucket", "gs://bucket/private/path", "gs://user:secret@bucket", "gs://bucket?token=secret"])
def test_bucket_input_is_only_a_supported_bucket_uri(uri):
    data = discovery()
    data["runtime"]["bucket_uri"] = uri
    assert build_intake_plan(data, "2026-08-01", "2026-08-25")["status"] == "FAIL"


def test_supplied_authorization_and_completeness_flags_cannot_open_any_gate():
    data = discovery()
    for key in ("export_authorized", "upload_authorized", "execution_authorized", "production_ready"):
        data[key] = True
    data["authorization"] = {"data_export_authorized": True, "upload_authorized": True}
    data["provenance"] = {"objects_read_here": True, "source_attested_here": True}
    data["market"].update(point_in_time_availability_established=True, component_object_names_known=True)
    plan = build_intake_plan(data, "2026-08-01", "2026-08-25")
    assert plan["status"] == "BLOCKED"
    assert all(plan[key] is False for key in ("export_authorized", "upload_authorized", "execution_authorized", "production_ready"))
    assert plan["object_inspection_performed"] is False
    assert all(row["component_object_names"] is None for row in symbols(plan).values())


def test_input_is_unchanged_and_sensitive_object_suffixes_are_not_forwarded():
    data = discovery()
    sentinel = "raw-account-suffix-728391/private-object.json"
    data["runtime"]["reported_object_examples"] = [sentinel]
    before = copy.deepcopy(data)
    plan = build_intake_plan(data, "2026-08-01", "2026-08-25")
    assert data == before
    assert sentinel not in json.dumps(plan)
    assert "unverified-storage-project" not in json.dumps(plan)
    assert "trade_count" not in json.dumps(plan)


def test_mode_and_physical_account_isolation_are_separate_axes():
    plan = build_intake_plan(discovery(), "2026-08-01", "2026-08-25")
    selection = plan["runtime"]["selection_rules"]
    assert selection["execution_environments"] == ["live", "paper", "dry_run", "unknown"]
    assert selection["record_kinds"] == ["execution", "reconciliation", "diagnostic", "not_run", "heartbeat", "unknown"]
    assert selection["physical_account_domain_keys"] == ["platform", "execution_environment", "server", "lifecycle_id", "account_alias"]
    assert "profile" not in selection["account_alias_namespace_keys"]
    assert selection["cross_broker_account_merge_allowed"] is False
    assert selection["logical_account_scope_is_physical_account"] is False
    assert selection["dry_run_false_establishes_live_or_broker_paper"] is False
    assert selection["legacy_paper_label_establishes_broker_paper"] is False
    assert selection["bucket_project_is_runtime_source_project"] is False
    assert selection["projection_empty_or_missing_fills_establish_zero_fills"] is False


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(runtime=[]),
    lambda data: data["runtime"].update(platforms=["charles_schwab", "charles_schwab", "longbridge"]),
    lambda data: data["market"].update(tickers=[]),
    lambda data: data["market"]["tickers"]["QQQM"].update(reported_first_session="2027-01-01"),
])
def test_malformed_discovery_structures_return_failure_instead_of_raising(mutate):
    data = discovery()
    mutate(data)
    plan = build_intake_plan(data, "2026-08-01", "2026-08-25")
    assert plan["status"] == "FAIL"


def test_auxiliary_prefixes_require_reviewed_references_not_bucket_wide_selection():
    plan = build_intake_plan(discovery(), "2026-08-01", "2026-08-25")
    for item in plan["runtime"]["auxiliary_selections"]:
        assert item["selection_method"] == "STABLE_REFERENCE_OR_REVIEWED_ASSOCIATION_ONLY"
        assert item["recursive_export_allowed"] is False
    assert "LONGBRIDGE_PRODUCER_SCHEMA_UNVERIFIED" in codes(plan)
    assert "SCHWAB_SETTLED_CASH_SOURCE_REQUIRED" in codes(plan)


def test_cli_writes_a_new_plan_without_mutating_input_or_printing_object_suffixes(tmp_path, capsys):
    source, output = tmp_path / "discovery.json", tmp_path / "plan.json"
    data = discovery()
    data["runtime"]["reported_object_examples"] = ["secret-account-id/report-123.json"]
    source.write_text(json.dumps(data))
    before = source.read_bytes()
    assert main([str(source), "--first-session", "2026-08-01", "--last-session", "2026-10-30", "--output", str(output)]) == 2
    assert json.loads(output.read_text())["status"] == "BLOCKED"
    assert source.read_bytes() == before
    stdout = capsys.readouterr().out
    assert json.loads(stdout)["execution_prefix_count"] == 6
    assert "secret-account-id" not in stdout and "gs://" not in stdout


@pytest.mark.parametrize("alias", ["existing", "hardlink", "symlink"])
def test_cli_never_overwrites_existing_outputs_or_input_aliases(tmp_path, capsys, alias):
    source, output = tmp_path / "discovery.json", tmp_path / "plan.json"
    source.write_text(json.dumps(discovery()))
    if alias == "existing":
        output.write_text("preserve me")
    elif alias == "hardlink":
        os.link(source, output)
    else:
        output.symlink_to(source)
    before_input, before_output = source.read_bytes(), output.read_bytes()
    assert main([str(source), "--first-session", "2026-08-01", "--last-session", "2026-08-25", "--output", str(output)]) == 1
    assert source.read_bytes() == before_input and output.read_bytes() == before_output
    assert "OUTPUT_PATH_EXISTS" in json.loads(capsys.readouterr().out)["finding_codes"]


@pytest.mark.parametrize("raw", ['{"schema_version": "one", "schema_version": "two"}', '{"value": NaN}', '{invalid'])
def test_cli_malformed_json_produces_a_structured_failure(tmp_path, capsys, raw):
    source, output = tmp_path / "bad.json", tmp_path / "plan.json"
    source.write_text(raw)
    assert main([str(source), "--first-session", "2026-08-01", "--last-session", "2026-08-25", "--output", str(output)]) == 1
    assert json.loads(output.read_text())["status"] == "FAIL"
    assert json.loads(capsys.readouterr().out)["status"] == "FAIL"


def test_cli_refuses_a_symlink_in_output_parent_without_creating_a_target_file(tmp_path, capsys):
    source = tmp_path / "discovery.json"
    source.write_text(json.dumps(discovery()))
    before = source.read_bytes()
    target = tmp_path / "target-directory"
    target.mkdir()
    alias = tmp_path / "output-alias"
    alias.symlink_to(target, target_is_directory=True)
    output = alias / "plan.json"
    assert main([str(source), "--first-session", "2026-08-01", "--last-session", "2026-08-25", "--output", str(output)]) == 1
    assert not (target / "plan.json").exists()
    assert source.read_bytes() == before
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["status"] == "FAIL"
    assert "OUTPUT_PARENT_SYMLINK" in metadata["finding_codes"]
    assert metadata["export_authorized"] is False


def test_api_deep_metadata_returns_failure_without_leaking_or_authorizing():
    data = discovery()
    nested = "private-account-object-sentinel"
    for _ in range(15000):
        nested = [nested]
    data["operator_notes"] = nested
    plan = build_intake_plan(data, "2026-08-01", "2026-08-25")
    assert plan["status"] == "FAIL"
    assert "DISCOVERY_NESTING_LIMIT_EXCEEDED" in codes(plan)
    assert "private-account-object-sentinel" not in json.dumps(plan)
    assert all(plan[key] is False for key in ("export_authorized", "upload_authorized", "execution_authorized", "production_ready"))


def test_cli_deep_json_returns_a_failure_file_and_safe_metadata(tmp_path, capsys):
    source, output = tmp_path / "discovery.json", tmp_path / "plan.json"
    suffix = '"private-account-object-sentinel"'
    raw = json.dumps(discovery())[:-1] + ', "operator_notes": ' + "[" * 15000 + suffix + "]" * 15000 + "}"
    source.write_text(raw)
    assert main([str(source), "--first-session", "2026-08-01", "--last-session", "2026-08-25", "--output", str(output)]) == 1
    plan = json.loads(output.read_text())
    assert plan["status"] == "FAIL"
    assert "DISCOVERY_NESTING_LIMIT_EXCEEDED" in codes(plan)
    assert source.read_text() == raw
    stdout = capsys.readouterr().out
    assert "private-account-object-sentinel" not in stdout
    metadata = json.loads(stdout)
    assert metadata["status"] == "FAIL"
    assert all(metadata[key] is False for key in ("export_authorized", "upload_authorized", "execution_authorized", "production_ready"))
