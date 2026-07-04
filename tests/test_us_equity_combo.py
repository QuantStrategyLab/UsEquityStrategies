from __future__ import annotations

import json
from pathlib import Path

from us_equity_strategies.combo_manifests import (
    us_equity_combo_core_manifest,
    us_equity_combo_manifest,
)
from us_equity_strategies.strategies import (
    us_equity_combo,
    us_equity_combo_core,
    us_equity_combo_leveraged,
)

from tests.test_mega_cap_leader_rotation import _mega_snapshot


def test_us_equity_combo_skips_ibit_leg_without_logger_error() -> None:
    weights, signal_desc, is_emergency, status_desc, diagnostics = us_equity_combo.compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        config={"dynamic": True},
    )

    assert weights
    assert "stock=" in signal_desc
    assert "etf=" in status_desc
    assert is_emergency is False
    assert diagnostics["dca_managed_symbols"] == ()


def test_us_equity_combo_accepts_manifest_weight_aliases() -> None:
    weights, _signal_desc, _is_emergency, _status_desc, diagnostics = us_equity_combo.compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        config=dict(us_equity_combo_manifest.default_config),
    )

    assert weights
    assert diagnostics["stock_weight"] == 0.50
    assert diagnostics["etf_weight"] == 0.50


def test_us_equity_combo_core_uses_core_shadow_weights_in_risk_on() -> None:
    weights, _signal_desc, is_emergency, _status_desc, diagnostics = (
        us_equity_combo_core.compute_signals(
            _mega_snapshot(),
            current_holdings=set(),
            config=dict(us_equity_combo_core_manifest.default_config),
        )
    )

    assert is_emergency is False
    assert diagnostics["profile_name"] == "us_equity_combo_core"
    assert diagnostics["regime_state"] == "risk_on"
    assert diagnostics["effective_russell_weight"] == 0.40
    assert diagnostics["effective_dca_weight"] == 0.40
    assert diagnostics["effective_safe_weight"] == 0.20
    assert weights["QQQM"] == 0.20
    assert weights["SPLG"] == 0.20
    assert weights["BOXX"] == 0.20
    assert abs(sum(weights.values()) - 1.0) < 1e-8


def test_us_equity_combo_core_switches_to_hard_defense_when_trend_breaks() -> None:
    weights, _signal_desc, is_emergency, _status_desc, diagnostics = (
        us_equity_combo_core.compute_signals(
            _mega_snapshot(qqq_sma200_gap=-0.02),
            current_holdings=set(),
            config=dict(us_equity_combo_core_manifest.default_config),
        )
    )

    assert is_emergency is True
    assert diagnostics["regime_state"] == "hard_defense"
    assert diagnostics["effective_russell_weight"] == 0.20
    assert diagnostics["effective_dca_weight"] == 0.05
    assert diagnostics["effective_safe_weight"] == 0.75
    assert weights["QQQM"] == 0.025
    assert weights["SPLG"] == 0.025
    assert weights["BOXX"] >= 0.75
    assert abs(sum(weights.values()) - 1.0) < 1e-8


def test_us_equity_combo_leveraged_risk_off_uses_configured_weights() -> None:
    weights, metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={"spy_above_ma200": False},
        config={"tqqq_weight": 0.35, "soxl_weight": 0.20, "boxx_weight": 0.45},
    )

    assert weights == {"TQQQ": 0.175, "SOXL": 0.10, "BOXX": 0.725}
    assert metadata["hard_defense_risk_exposure"] == 0.50


def test_us_equity_combo_leveraged_supports_zero_hard_defense_shadow() -> None:
    weights, metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={"spy_above_ma200": False},
        config={
            "tqqq_weight": 0.35,
            "soxl_weight": 0.20,
            "boxx_weight": 0.45,
            "hard_defense_risk_exposure": 0.0,
        },
    )

    assert weights == {"TQQQ": 0.0, "SOXL": 0.0, "BOXX": 1.0}
    assert metadata["hard_defense_risk_exposure"] == 0.0


def test_us_equity_combo_leveraged_uses_multi_asset_hard_defense() -> None:
    weights, metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={
            "spy_above_ma200": True,
            "qqq_above_ma200": False,
            "soxx_above_ma200": True,
        },
        config={
            "tqqq_weight": 0.35,
            "soxl_weight": 0.20,
            "boxx_weight": 0.45,
            "hard_defense_risk_exposure": 0.0,
        },
    )

    assert weights == {"TQQQ": 0.0, "SOXL": 0.0, "BOXX": 1.0}
    assert metadata["regime_state"] == "hard_defense"
    assert metadata["above_ma200"]["QQQ"] is False


def test_us_equity_combo_leveraged_marks_soft_defense_without_changing_shadow_weights() -> None:
    weights, metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={
            "spy_above_ma200": True,
            "qqq_above_ma200": True,
            "soxx_above_ma200": True,
            "spy_ma20_slope_positive": True,
            "qqq_ma20_slope_positive": False,
            "soxx_ma20_slope_positive": True,
        },
        config={
            "tqqq_weight": 0.35,
            "soxl_weight": 0.20,
            "boxx_weight": 0.45,
            "hard_defense_risk_exposure": 0.0,
        },
    )

    assert weights == {"TQQQ": 0.35, "SOXL": 0.20, "BOXX": 0.45}
    assert metadata["regime_state"] == "soft_defense"
    assert metadata["ma20_slope_positive"]["QQQ"] is False


def test_us_equity_combo_leveraged_shadow_352045_config_matches_strategy_behavior() -> None:
    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "us_equity_strategies"
        / "configs"
        / "us_equity_combo_leveraged_shadow_352045.json"
    )
    shadow_config = json.loads(config_path.read_text(encoding="utf-8"))
    runtime_config = shadow_config["runtime_config"]

    risk_on_weights, _risk_on_metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={"spy_above_ma200": True},
        config=runtime_config,
    )
    risk_off_weights, risk_off_metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={"spy_above_ma200": False},
        config=runtime_config,
    )

    assert shadow_config["status"] == "shadow_candidate"
    assert shadow_config["promotion_state"]["live_enable_candidate"] is False
    assert risk_on_weights == {"TQQQ": 0.35, "SOXL": 0.20, "BOXX": 0.45}
    assert risk_off_weights == {"TQQQ": 0.0, "SOXL": 0.0, "BOXX": 1.0}
    assert risk_off_metadata["hard_defense_risk_exposure"] == 0.0


def test_us_equity_combo_leveraged_loads_shadow_runtime_config() -> None:
    config_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "us_equity_strategies"
        / "configs"
        / "us_equity_combo_leveraged_shadow_352045.json"
    )

    runtime_config = us_equity_combo_leveraged.load_runtime_parameters(
        config_path=config_path,
        logger=lambda _message: None,
    )

    assert runtime_config["runtime_config_name"] == "us_equity_combo_leveraged_shadow_352045"
    assert runtime_config["runtime_config_path"] == str(config_path)
    assert runtime_config["runtime_config_source"] == "external_config"
    assert runtime_config["tqqq_weight"] == 0.35
    assert runtime_config["soxl_weight"] == 0.20
    assert runtime_config["boxx_weight"] == 0.45
    assert runtime_config["hard_defense_risk_exposure"] == 0.0


def test_us_equity_combo_leveraged_loads_package_runtime_config() -> None:
    runtime_config = us_equity_combo_leveraged.load_runtime_parameters(
        config_path=(
            "package://us_equity_strategies/"
            "configs/us_equity_combo_leveraged_shadow_352045.json"
        ),
        logger=lambda _message: None,
    )

    assert runtime_config["runtime_config_name"] == "us_equity_combo_leveraged_shadow_352045"
    assert runtime_config["runtime_config_path"].startswith("package://us_equity_strategies/")
    assert runtime_config["tqqq_weight"] == 0.35
    assert runtime_config["hard_defense_risk_exposure"] == 0.0


def test_us_equity_combo_leveraged_loads_402040_shadow_config() -> None:
    runtime_config = us_equity_combo_leveraged.load_runtime_parameters(
        config_path=(
            "package://us_equity_strategies/"
            "configs/us_equity_combo_leveraged_shadow_402040.json"
        ),
        logger=lambda _message: None,
    )
    risk_on_weights, _risk_on_metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={
            "spy_above_ma200": True,
            "qqq_above_ma200": True,
            "soxx_above_ma200": True,
        },
        config=runtime_config,
    )
    hard_weights, hard_metadata = us_equity_combo_leveraged.build_target_weights(
        market_data={
            "spy_above_ma200": True,
            "qqq_above_ma200": False,
            "soxx_above_ma200": True,
        },
        config=runtime_config,
    )

    assert runtime_config["runtime_config_name"] == "us_equity_combo_leveraged_shadow_402040"
    assert risk_on_weights == {"TQQQ": 0.4, "SOXL": 0.2, "BOXX": 0.4}
    assert hard_weights == {"TQQQ": 0.0, "SOXL": 0.0, "BOXX": 1.0}
    assert hard_metadata["regime_state"] == "hard_defense"
