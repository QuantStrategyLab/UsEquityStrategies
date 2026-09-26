"""Synthetic contract tests for the fixed UX1 R8 preview adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path

import pytest


def _adapter():
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "ux1_preview_adapter", root / "ux1_preview_adapter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(**overrides):
    values = {key: None for key in (
        "plugin_mode", "income_layer_mode", "income_layer_start_usd",
        "income_layer_max_ratio", "option_overlay_mode", "reserve_policy_mode",
        "min_reserved_cash_usd", "reserved_cash_ratio", "cash_only_execution_mode",
        "dca_mode", "dca_base_investment_usd")}
    values.update(overrides)
    return values


def _request(adapter, **overrides):
    body = {
        "schema": adapter.REQUEST_SCHEMA,
        "research_case_id": adapter.RESEARCH_CASE_ID,
        "candidate_id": adapter.CANDIDATE_ID,
        "objective": adapter.OBJECTIVE,
        "candidate_set_id": adapter.CANDIDATE_SET_ID,
        "capital_variant": adapter.CAPITAL_VARIANT,
        "cost_bps": adapter.COST_BPS,
        "source_class": adapter.SOURCE_CLASS,
        "advanced_settings": _settings(),
    }
    body.update(overrides)
    return body


def _grid(adapter, value):
    return {owner: {symbol: value for symbol in symbols}
            for owner, symbols in adapter.OWNER_SYMBOLS.items()}


def _startup(signal: str) -> dict:
    return {
        "signal_date": signal,
        "date": "2023-03-28" if signal == "2023-03-27" else "2023-03-29",
        "path": "B0",
        "action_selection": {"startup_fixed": True, "selected_action": "B0",
                             "scenario_count": 0, "previous_action_retained": True},
    }


def _dynamic(adapter, *, retained: bool, scores: dict, selected: str,
             shares: int, settled: float) -> dict:
    targets = _grid(adapter, 0.0)
    targets["outer"]["QQQM"] = 5000.0
    targets["outer"]["BOXX"] = 4900.0
    return {
        "signal_date": "2023-03-29",
        "date": "2023-03-30",
        "path": selected,
        "cost_bps": 10,
        "candidate_id": adapter.CANDIDATE_ID,
        "capital_variant": "C2",
        "action_selection": {
            "startup_fixed": False,
            "scenario_count": 60,
            "observed_through": "2023-03-29",
            "selected_action": selected,
            "previous_action": "B0",
            "previous_action_retained": retained,
            "estimated_scores": scores,
            "estimated_mean_log_growth": scores[selected],
        },
        "member_budget_usd": {"tqqq": 250.0, "soxl": 250.0},
        "target_usd": targets,
        "cash_target_usd": 100.0,
        "curve_ratio": 0.03,
        "wealth_reference_usd": 10000.0,
        "aggregate_member_cap_usd": 300.0,
        "trade_shares": _grid(adapter, shares),
        "trade_cost_usd": _grid(adapter, 0.0),
        "total_cost_usd": 0.0,
        "settled_cash_usd": settled,
        "pending_sale_usd": 3.0,
        "receivable_usd": 1.5,
        "shortages": [{"owner": "outer", "symbol": "QQQM",
                       "desired_shares": 2, "filled_shares": 1}],
    }


def _install_engines(monkeypatch, adapter, tmp_path: Path, ledger: list) -> dict:
    root = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(root))
    import post_r9_capital_study as capital
    import r7_joint_account_compare as r7
    import r8_joint_allocation_compare as r8

    calls = {}
    raw = tmp_path / "raw-root-token"
    r6 = tmp_path / "r6-root-token"
    materialized = tmp_path / "materialized-token.json"
    raw.mkdir()
    r6.mkdir()
    materialized.write_bytes(b"{}")
    monkeypatch.setenv("UX1_RAW_ROOT", str(raw))
    monkeypatch.setenv("UX1_R6_ROOT", str(r6))
    monkeypatch.setenv("UX1_MATERIALIZED", str(materialized))

    real_policy = r8._policy

    def policy():
        calls["policy"] = calls.get("policy", 0) + 1
        return real_policy()

    def load(*args, **kwargs):
        calls["load"] = (args, kwargs)
        return ([{"date": "synthetic"}], {"QQQM": {}}, {"2023-03-29": {}},
                {"tqqq_contract": {"synthetic": True}})

    def replay(*args, **kwargs):
        calls["replay"] = (args, kwargs)
        return {"cumulative_return": 9.9, "cagr_252_sessions": 9.9,
                "max_drawdown": -9.9}, ledger

    monkeypatch.setattr(r8, "_policy", policy)
    monkeypatch.setattr(r7, "_load_inputs", load)
    monkeypatch.setattr(r7, "_replay", replay)
    calls["modules"] = (r7, r8, capital)
    calls["roots"] = (raw, r6, materialized)
    return calls


def _clear_roots(monkeypatch) -> None:
    for name in ("UX1_RAW_ROOT", "UX1_R6_ROOT", "UX1_MATERIALIZED"):
        monkeypatch.delenv(name, raising=False)


def test_rejects_extra_authority_path_and_url_without_echoing_values() -> None:
    adapter = _adapter()
    extra = _request(adapter)
    extra["raw_root"] = "/Users/private/secret-bundle"
    extra["command"] = "curl https://secret.example/order"
    result = adapter.preview(extra)
    encoded = json.dumps(result)
    assert result["status"] == "rejected"
    assert result["reason_code"] == "UX1_FORBIDDEN_FIELD"
    assert "secret-bundle" not in encoded
    assert "secret.example" not in encoded
    assert "decision_preview" not in result


def test_original_full_v2_is_not_mapped_or_disabled(monkeypatch) -> None:
    adapter = _adapter()

    def boom(_request):
        raise AssertionError("full v2 must not enter the R8 calculator")

    monkeypatch.setattr(adapter, "_calculate", boom)
    result = adapter.preview(_request(
        adapter, candidate_id="independent-soxl-full-manifest-v2"))
    assert result["status"] == "unsupported_candidate"
    assert result["reasons"] == [
        "unsupported_candidate", "original_full_v2_not_mapped_to_simplified_r8"]
    assert result["original_full_v2_mapped"] is False
    assert result["original_full_v2_features_silently_disabled"] is False
    assert result["advanced_null_asserts_original_full_v2_toggles"] is False
    assert "decision_preview" not in result
    assert "optimality_scope" not in result


def test_explicit_advanced_values_stay_unsupported_and_retained(monkeypatch) -> None:
    adapter = _adapter()

    def boom(_request):
        raise AssertionError("unmodeled settings must not be dropped into R8")

    monkeypatch.setattr(adapter, "_calculate", boom)
    body = _request(adapter)
    body["advanced_settings"] = _settings(plugin_mode="custom_plugin",
                                          income_layer_start_usd=125,
                                          option_overlay_mode="spread")
    first = adapter.preview(body)
    second = adapter.preview(json.loads(json.dumps(body)))
    assert first["status"] == "unsupported_scope"
    assert first["advanced_settings"]["plugin_mode"] == "custom_plugin"
    assert first["advanced_settings"]["income_layer_start_usd"] == "125"
    assert first["advanced_settings"]["option_overlay_mode"] == "spread"
    assert first["advanced_settings"]["dca_mode"] is None
    assert "plugin_mode_not_modeled_by_r8" in first["reasons"]
    assert "income_layer_start_usd_not_modeled_by_r8" in first["reasons"]
    assert "option_overlay_mode_not_modeled_by_r8" in first["reasons"]
    assert "decision_preview" not in first
    assert first["request_fingerprint"] == second["request_fingerprint"]
    changed = _request(adapter)
    changed["advanced_settings"] = _settings(plugin_mode="other_plugin")
    assert adapter.preview(changed)["request_fingerprint"] != first["request_fingerprint"]


def test_invalid_advanced_range_is_rejected() -> None:
    adapter = _adapter()
    body = _request(adapter)
    body["advanced_settings"] = _settings(income_layer_max_ratio=1.5)
    result = adapter.preview(body)
    assert result["status"] == "rejected"
    assert result["reason_code"] == "UX1_ADVANCED_VALUE_INVALID"
    assert "1.5" not in json.dumps(result)


def test_small_decimal_fingerprint_uses_canonical_text() -> None:
    adapter = _adapter()
    text = _request(adapter, advanced_settings=_settings(
        reserve_policy_mode="ratio", reserved_cash_ratio="0.00001"))
    numeric = _request(adapter, advanced_settings=_settings(
        reserve_policy_mode="ratio", reserved_cash_ratio=1e-5))
    first = adapter.preview(text)
    second = adapter.preview(numeric)
    assert first["status"] == "unsupported_scope"
    assert first["advanced_settings"]["reserved_cash_ratio"] == "0.00001"
    assert first["request_fingerprint"] == second["request_fingerprint"]


def test_float_cost_and_missing_roots_do_not_calculate(monkeypatch) -> None:
    adapter = _adapter()
    _clear_roots(monkeypatch)
    mismatch = adapter.preview(_request(adapter, cost_bps=10.0))
    assert mismatch["status"] == "rejected"
    assert mismatch["reason_code"] == "UX1_FROZEN_CASE_MISMATCH"
    missing = adapter.preview(_request(adapter))
    encoded = json.dumps(missing)
    assert missing["status"] == "missing_input"
    assert missing["reason_code"] == "UX1_OPERATOR_INPUT_UNCONFIGURED"
    assert "UX1_RAW_ROOT" not in encoded
    monkeypatch.setenv("UX1_RAW_ROOT", "https://secret.example/raw")
    monkeypatch.setenv("UX1_R6_ROOT", "/tmp")
    monkeypatch.setenv("UX1_MATERIALIZED", "/tmp/materialized.json")
    hidden = adapter.preview(_request(adapter))
    assert hidden["status"] == "missing_input"
    assert "secret.example" not in json.dumps(hidden)


def test_loader_failures_are_structured_and_redacted(monkeypatch, tmp_path: Path) -> None:
    adapter = _adapter()
    ledger = [_startup("2023-03-27"), _startup("2023-03-28"),
              _dynamic(adapter, retained=True, selected="B1", shares=0, settled=1.0,
                       scores={"B0": 0.0, "B1": 0.01, "B2": 0.0, "B3": 0.0})]
    calls = _install_engines(monkeypatch, adapter, tmp_path, ledger)

    def missing_file(*_args, **_kwargs):
        raise FileNotFoundError(2, "missing", "/Users/private/secret-bundle/manifest.json")

    monkeypatch.setattr(calls["modules"][0], "_load_inputs", missing_file)
    missing = adapter.preview(_request(adapter))
    assert missing["status"] == "missing_input"
    assert missing["reason_code"] == "UX1_INPUT_UNAVAILABLE"
    assert "secret-bundle" not in json.dumps(missing)

    def explode(*_args, **_kwargs):
        raise OSError("denied /Users/private/secret-bundle/manifest.json")

    monkeypatch.setattr(calls["modules"][0], "_load_inputs", explode)
    failed = adapter.preview(_request(adapter))
    assert failed["status"] == "failed"
    assert failed["reason_code"] == "UX1_PREVIEW_FAILED"
    assert "secret-bundle" not in json.dumps(failed)

    def mismatch(*_args, **_kwargs):
        raise ValueError("R7_RAW_MANIFEST_MISMATCH:/Users/private/secret-bundle")

    monkeypatch.setattr(calls["modules"][0], "_load_inputs", mismatch)
    coded = adapter.preview(_request(adapter))
    assert coded["status"] == "failed"
    assert coded["reason_code"] == "R7_RAW_MANIFEST_MISMATCH"
    assert "secret-bundle" not in json.dumps(coded)


def test_preview_splits_decision_from_later_execution(monkeypatch, tmp_path: Path) -> None:
    adapter = _adapter()
    outside = {"B0": 0.0, "B1": 0.01, "B2": 0.0, "B3": -0.01}
    ledger = [
        _startup("2023-03-27"),
        _startup("2023-03-28"),
        _dynamic(adapter, retained=True, selected="B1", shares=0, settled=42.0, scores=outside),
    ]
    ledger[0]["settled_cash_usd"] = 1.0
    calls = _install_engines(monkeypatch, adapter, tmp_path, ledger)
    first = adapter.preview(_request(adapter))
    second = adapter.preview(_request(adapter))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["status"] == "ok"
    assert calls["policy"] >= 1
    assert calls["load"][0][0:3] == calls["roots"]
    replay = calls["replay"][1]
    assert replay["short_sessions"] == 3
    assert replay["cost_bps"] == 10
    assert replay["path_name"] == "B0"
    assert replay["candidate_id"] == adapter.CANDIDATE_ID
    assert replay["capital_hook"].variant == "C2"
    assert replay["capital_hook"].initial_nav == 10000
    assert replay["settlement_policy"]["policy_id"] == (
        "post_r9_us_equity_dtc_standard_settlement_v1")
    assert callable(replay["action_selector"])
    decision = first["decision_preview"]
    execution = first["historical_execution_check"]
    assert decision["decision_date"] == "2023-03-29"
    assert decision["scenario_count"] == 60
    assert decision["scenarios_observed_through"] <= "2023-03-29"
    assert decision["selected_action"] == "B1"
    assert decision["previous_action_retained"] is True
    assert decision["no_advantage"] is False
    assert decision["score_gain_over_previous_action"] == pytest.approx(0.01)
    assert decision["member_budgets_usd"] == {"tqqq": 250.0, "soxl": 250.0}
    assert decision["asset_targets_usd"]["outer"]["QQQM"] == 5000.0
    assert decision["curve_ratio"] == pytest.approx(0.03)
    assert decision["wealth_reference_usd"] == 10000.0
    assert decision["aggregate_member_cap_usd"] == 300.0
    assert execution["trade_date"] == "2023-03-30"
    assert execution["feeds_back_into_decision"] is False
    assert execution["settled_cash_usd"] == 42.0
    assert execution["pending_sale_usd"] == 3.0
    assert execution["receivable_usd"] == 1.5
    assert execution["no_action"] is True
    assert execution["trade_shares"]["outer"]["QQQM"] == 0
    assert execution["shortages"] == [{
        "owner": "outer", "symbol": "QQQM", "desired_shares": 2, "filled_shares": 1}]
    for key in ("trade_shares", "fees_usd", "shortages", "settled_cash_usd", "no_action"):
        assert key not in decision
    for key in ("scores", "no_advantage", "curve_ratio", "member_budgets_usd"):
        assert key not in execution
    encoded = json.dumps(first)
    assert "cumulative_return" not in encoded
    assert "cagr_252_sessions" not in encoded
    assert '"max_drawdown"' not in encoded
    assert "raw-root-token" not in encoded
    assert first["optimality_scope"] == "best_score_among_feasible_B0_B3_only"
    assert first["research_only"] is True
    assert first["no_order"] is True
    assert first["execution_authority_granted"] is False
    assert first["native_observed"] is False
    assert first["advanced_null_asserts_original_full_v2_toggles"] is False
    assert "null_advanced_settings_do_not_assert_original_full_v2_feature_toggles" in first["limitations"]
    assert "prior_window_cumulative_metrics_are_not_this_draft_performance" in first["limitations"]


def test_tie_and_nonzero_shares_are_independent_of_retained_flag(monkeypatch, tmp_path: Path) -> None:
    adapter = _adapter()
    within = {"B0": 0.0, "B1": 1e-12, "B2": 0.0, "B3": 0.0}
    shares = _grid(adapter, 0)
    shares["tqqq"]["TQQQ"] = 4
    row = _dynamic(adapter, retained=False, selected="B0", shares=0, settled=7.0, scores=within)
    row["trade_shares"] = shares
    ledger = [_startup("2023-03-27"), _startup("2023-03-28"), row]
    _install_engines(monkeypatch, adapter, tmp_path, ledger)
    result = adapter.preview(_request(adapter))
    decision = result["decision_preview"]
    execution = result["historical_execution_check"]
    assert decision["previous_action_retained"] is False
    assert decision["no_advantage"] is True
    assert decision["selected_action"] == "B0"
    assert execution["no_action"] is False
    assert execution["trade_shares"]["tqqq"]["TQQQ"] == 4
    assert execution["shortages"][0]["filled_shares"] == 1


def test_replay_integral_float_shares_are_whole_and_fractional_shares_fail() -> None:
    adapter = _adapter()
    assert adapter._whole(1.0) == 1
    with pytest.raises(ValueError, match="UX1_SHARE_NOT_WHOLE"):
        adapter._whole(1.5)


def test_future_scenario_does_not_produce_a_decision(monkeypatch, tmp_path: Path) -> None:
    adapter = _adapter()
    scores = {"B0": 0.0, "B1": 0.0, "B2": 0.0, "B3": 0.0}
    row = _dynamic(adapter, retained=True, selected="B0", shares=0, settled=1.0, scores=scores)
    row["action_selection"]["observed_through"] = "2023-03-30"
    ledger = [_startup("2023-03-27"), _startup("2023-03-28"), row]
    _install_engines(monkeypatch, adapter, tmp_path, ledger)
    result = adapter.preview(_request(adapter))
    assert result["status"] == "failed"
    assert result["reason_code"] == "UX1_FUTURE_SCENARIO"
    assert "decision_preview" not in result


def test_stdin_rejects_arguments_nan_and_returns_json(monkeypatch) -> None:
    adapter = _adapter()
    _clear_roots(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["ux1_preview_adapter.py", "--raw-root", "/tmp/secret"])
    stdout = BytesIO()
    monkeypatch.setattr(sys, "stdout", _Text(stdout))
    assert adapter.main() == 0
    denied = json.loads(stdout.getvalue())
    assert denied["reason_code"] == "UX1_ARGUMENTS_REJECTED"
    assert "secret" not in stdout.getvalue().decode()

    monkeypatch.setattr(sys, "argv", ["ux1_preview_adapter.py"])
    stdout = BytesIO()
    monkeypatch.setattr(sys, "stdout", _Text(stdout))
    assert adapter.main(BytesIO(b"NaN")) == 0
    assert json.loads(stdout.getvalue())["reason_code"] == "UX1_REQUEST_SCHEMA_INVALID"

    stdout = BytesIO()
    monkeypatch.setattr(sys, "stdout", _Text(stdout))
    payload = json.dumps(_request(adapter)).encode()
    assert adapter.main(BytesIO(payload)) == 0
    parsed = json.loads(stdout.getvalue())
    assert parsed["status"] == "missing_input"
    assert parsed["research_only"] is True


class _Text:
    def __init__(self, buffer: BytesIO) -> None:
        self.buffer = buffer

    def write(self, text: str) -> int:
        return self.buffer.write(text.encode())
