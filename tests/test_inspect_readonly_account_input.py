"""Only invented reports and account identifiers are used."""
import json
from datetime import datetime, timezone
import socket
import stat

import pytest

from scripts.inspect_readonly_account_input import inspect_report
from scripts import inspect_readonly_account_input as inspector


def case():
    facts = dict(schema_version="ibkr_account_snapshot.v1", account_ids=["TEST_ACCOUNT"],
                 observed_at="2000-01-04T12:00:00Z", currency="USD", net_assets="100",
                 cash=[dict(currency="USD", source_tag="CashBalance", cash_balance="100")],
                 broker_reported_positions=[], broker_reported_positions_scope="stocks_only")
    report = dict(platform="interactive_brokers", strategy_profile="TEST_PROFILE",
                  account_scope="TEST_SCOPE", diagnostics=dict(runtime_revision="TEST_REVISION"),
                  summary=dict(account_facts=facts))
    binding = {key: report[key] for key in ("platform", "strategy_profile", "account_scope")}
    binding["runtime_revision"] = "TEST_REVISION"
    contract = dict(account_id="TEST_ACCOUNT", scope_ref="TEST_SCOPE", currency="USD",
                    source="TEST_CONFIG", source_version="v1", synthetic=False,
                    max_age_seconds=120, nav_basis="WHOLE_ACCOUNT_NET_LIQUIDATION",
                    asset_scope="STOCKS_AND_CASH_ONLY", positions_complete=True,
                    obligations_complete=True, settled_cash="100", reserved_cash="0",
                    other_cash_obligations="0", valuation_tolerance="0",
                    cash_rule="SETTLED_MINUS_DISJOINT_RESERVATIONS_AND_OBLIGATIONS_NO_SELL_BRIDGE",
                    cash_rule_source="TEST_CASH", protected_capital_basis="100", capital_state_source="TEST_STATE")
    return report, dict(account_contract=contract, report_binding=binding, source_ref="TEST_PRIVATE_ARCHIVE")


def run(report, config, **times):
    return inspect_report(json.dumps(report).encode(), json.dumps(config).encode(),
                          received_at="2000-01-04T12:00:30Z",
                          cutoff_utc=times.get("cutoff_utc", "2000-01-04T12:01:00Z"))


def test_frozen_report_flows_through_separate_real_readonly_assessment():
    report, config = case()
    result = run(report, config)
    assert result["assessment"]["status"] == "READY_READONLY_INPUT"
    assert result["assessment"]["spendable_cash"] == 100
    assert result["assessment"]["settings_applied"] is False
    assert result["source"]["received_at"] == "2000-01-04T12:00:30Z"
    assert result["source"]["published_at"] is None
    assert "cutoff_utc" not in config["account_contract"]


@pytest.mark.parametrize("field", ["platform", "strategy_profile", "account_scope", "runtime_revision"])
def test_wrong_report_identity_is_not_usable_even_if_cash_numbers_match(field):
    report, config = case()
    if field == "runtime_revision":
        report["diagnostics"][field] = "OTHER"
    else:
        report[field] = "OTHER"
    packet = run(report, config)
    result = packet["assessment"]
    assert "REPORT_SOURCE_BINDING_MISMATCH" in result["gaps"]
    assert packet["report_binding_equalities"][field] is False
    assert result["spendable_cash"] is None and result["current_weights"] is None


def test_missing_source_fields_and_account_contract_stay_gaps():
    report, config = case()
    config.pop("report_binding")
    assert "REPORT_SOURCE_BINDING_MISMATCH" in run(report, config)["assessment"]["gaps"]
    config.pop("account_contract")
    assert "ACCOUNT_WIRE_INVALID" in run(report, config)["assessment"]["gaps"]


def test_stale_report_is_not_refreshed_by_new_read_receipt():
    report, config = case()
    result = run(report, config, cutoff_utc="2000-01-05T12:01:00Z")["assessment"]
    assert "SNAPSHOT_TIME_INVALID" in result["gaps"]
    assert result["spendable_cash"] is None


@pytest.mark.parametrize("field", ["runtime_revision", "runtime_release_receipt"])
def test_contradictory_revision_is_not_silently_replaced(field):
    report, config = case()
    report[field] = "OTHER" if field == "runtime_revision" else dict(runtime_revision="OTHER")
    assert "REPORT_SOURCE_BINDING_MISMATCH" in run(report, config)["assessment"]["gaps"]


def test_rejects_a_config_that_would_replace_an_old_decision_cutoff():
    report, config = case()
    config["account_contract"]["cutoff_utc"] = "2000-01-03T12:00:00Z"
    with pytest.raises(ValueError, match="FROZEN_CUTOFF_NOT_REUSABLE"):
        run(report, config)


def test_invalid_json_or_top_level_shape_has_fixed_error():
    with pytest.raises(ValueError, match="READONLY_PACKET_INVALID"):
        inspect_report(b"private-invalid", b"{}", received_at="x", cutoff_utc="x")
    with pytest.raises(ValueError, match="READONLY_PACKET_INVALID"):
        inspect_report(b"[]", b"{}", received_at="x", cutoff_utc="x")


def files(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    report, config = case()
    for name, value in (("report.json", report), ("config.json", config)):
        path = root / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    return root, ["--report", str(root / "report.json"), "--config", str(root / "config.json"),
                  "--output", str(root / "output.json")]


def test_cli_keeps_receipt_private_and_refuses_to_replace_previous_result(monkeypatch, tmp_path, capsys):
    root, args = files(tmp_path)
    monkeypatch.setattr(inspector.sys, "platform", "linux")
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("NETWORK_FORBIDDEN"))
    assert inspector.main(args) == 2  # This invented 2000 snapshot is stale today.
    output = root / "output.json"
    raw = output.read_bytes()
    saved = json.loads(raw)
    assert "SNAPSHOT_TIME_INVALID" in saved["assessment"]["gaps"]
    assert saved["source_observed_at"] == "2000-01-04T12:00:00Z"
    assert saved["source"]["published_at"] is None
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert inspector.main(args) == 2
    assert output.read_bytes() == raw
    assert "TEST_ACCOUNT" not in capsys.readouterr().out


def test_cli_valid_invented_packet_still_has_no_execution_or_settings_permission(monkeypatch, tmp_path):
    root, args = files(tmp_path)
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2000, 1, 4, 12, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(inspector, "datetime", Clock)
    monkeypatch.setattr(inspector.sys, "platform", "linux")
    assert inspector.main(args) == 0
    result = json.loads((root / "output.json").read_text())
    assert result["assessment"]["status"] == "READY_READONLY_INPUT"
    assert result["execution_authorized"] is False and result["settings_applied"] is False


@pytest.mark.parametrize("platform,public_input", [("darwin", False), ("linux", True)])
def test_cli_rejects_mac_or_publicly_readable_input_before_output(monkeypatch, tmp_path, platform, public_input):
    root, args = files(tmp_path)
    monkeypatch.setattr(inspector.sys, "platform", platform)
    if public_input:
        (root / "report.json").chmod(0o644)
    assert inspector.main(args) == 2
    assert not (root / "output.json").exists()
