from __future__ import annotations

import json

import pandas as pd


def _mega_snapshot(qqq_sma200_gap: float = 0.08) -> pd.DataFrame:
    as_of = pd.Timestamp("2026-03-31")
    rows = [
        {
            "as_of": as_of,
            "symbol": "QQQ",
            "sector": "benchmark",
            "close": 500.0,
            "adv20_usd": 1_000_000_000.0,
            "history_days": 400,
            "mom_3m": 0.12,
            "mom_6m": 0.20,
            "mom_12_1": 0.30,
            "rel_mom_6m_vs_benchmark": 0.0,
            "rel_mom_6m_vs_broad_benchmark": 0.05,
            "high_252_gap": -0.01,
            "sma200_gap": qqq_sma200_gap,
            "vol_63": 0.20,
            "maxdd_126": -0.10,
            "eligible": False,
        },
        {
            "as_of": as_of,
            "symbol": "SPY",
            "sector": "benchmark",
            "close": 450.0,
            "adv20_usd": 1_000_000_000.0,
            "history_days": 400,
            "mom_3m": 0.08,
            "mom_6m": 0.15,
            "mom_12_1": 0.22,
            "rel_mom_6m_vs_benchmark": -0.05,
            "rel_mom_6m_vs_broad_benchmark": 0.0,
            "high_252_gap": -0.02,
            "sma200_gap": 0.05,
            "vol_63": 0.16,
            "maxdd_126": -0.08,
            "eligible": False,
        },
        {
            "as_of": as_of,
            "symbol": "BOXX",
            "sector": "cash",
            "close": 101.0,
            "adv20_usd": 30_000_000.0,
            "history_days": 400,
            "mom_3m": 0.01,
            "mom_6m": 0.02,
            "mom_12_1": 0.04,
            "rel_mom_6m_vs_benchmark": -0.18,
            "rel_mom_6m_vs_broad_benchmark": -0.13,
            "high_252_gap": 0.0,
            "sma200_gap": 0.01,
            "vol_63": 0.03,
            "maxdd_126": -0.01,
            "eligible": False,
        },
    ]
    leaders = [
        ("NVDA", "Information Technology", 0.30, 0.55, 0.45, 0.10, 0.15, -0.01, 0.35, -0.08),
        ("META", "Communication Services", 0.22, 0.38, 0.30, 0.02, 0.09, -0.03, 0.24, -0.09),
        ("MSFT", "Information Technology", 0.18, 0.32, 0.25, -0.01, 0.08, -0.02, 0.20, -0.07),
        ("AAPL", "Information Technology", 0.15, 0.28, 0.21, -0.03, 0.06, -0.04, 0.18, -0.10),
        ("AMZN", "Consumer Discretionary", 0.14, 0.26, 0.19, -0.04, 0.05, -0.06, 0.22, -0.11),
        ("TSLA", "Consumer Discretionary", 0.02, 0.05, -0.05, -0.15, -0.02, -0.20, 0.45, -0.25),
    ]
    for symbol, sector, mom3, mom6, mom12, rel_qqq, rel_spy, high_gap, vol, maxdd in leaders:
        rows.append(
            {
                "as_of": as_of,
                "symbol": symbol,
                "sector": sector,
                "close": 100.0,
                "adv20_usd": 100_000_000.0,
                "history_days": 400,
                "mom_3m": mom3,
                "mom_6m": mom6,
                "mom_12_1": mom12,
                "rel_mom_6m_vs_benchmark": rel_qqq,
                "rel_mom_6m_vs_broad_benchmark": rel_spy,
                "high_252_gap": high_gap,
                "sma200_gap": 0.08 if symbol != "TSLA" else -0.03,
                "vol_63": vol,
                "maxdd_126": maxdd,
                "eligible": True,
            }
        )
    return pd.DataFrame(rows)


def test_build_target_weights_selects_four_leaders() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import build_target_weights

    weights, ranked, metadata = build_target_weights(_mega_snapshot(), current_holdings={"AAPL"})

    assert metadata["regime"] == "risk_on"
    assert metadata["selected_count"] == 4
    assert ranked.iloc[0]["symbol"] == "NVDA"
    assert "SPY" not in weights
    assert "QQQ" not in weights
    assert abs(sum(weights.values()) - 1.0) < 1e-8
    assert "BOXX" not in weights


def test_build_target_weights_uses_half_exposure_when_qqq_is_below_sma() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import build_target_weights

    weights, _ranked, metadata = build_target_weights(_mega_snapshot(qqq_sma200_gap=-0.02), current_holdings=set())

    assert metadata["regime"] == "soft_defense"
    assert abs(metadata["target_stock_weight"] - 0.5) < 1e-8
    assert abs(weights["BOXX"] - 0.5) < 1e-8


def test_build_target_weights_reduces_holdings_for_small_accounts() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import build_target_weights

    _weights, _ranked, metadata = build_target_weights(
        _mega_snapshot(),
        current_holdings=set(),
        portfolio_total_equity=8_000.0,
        min_position_value_usd=3_000.0,
        single_name_cap=0.50,
    )

    assert metadata["requested_holdings_count"] == 4
    assert metadata["effective_holdings_count"] == 2
    assert metadata["selected_count"] == 2


def test_build_blended_target_weights_combines_top2_and_top4_sleeves() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import build_blended_target_weights

    weights, _ranked, metadata = build_blended_target_weights(
        _mega_snapshot(),
        current_holdings=set(),
        blend_sleeves=(
            {"name": "top2_cap50", "weight": 0.50, "holdings_count": 2, "single_name_cap": 0.50},
            {"name": "top4_cap25", "weight": 0.50, "holdings_count": 4, "single_name_cap": 0.25},
        ),
        dynamic_universe_size=50,
        soft_defense_exposure=1.0,
        hard_defense_exposure=1.0,
    )

    assert metadata["blend_mode"] == "fixed_weighted_sleeves"
    assert metadata["selected_count"] == 4
    assert abs(sum(weights.values()) - 1.0) < 1e-8
    assert "BOXX" not in weights
    assert weights["NVDA"] > weights["MSFT"]
    assert weights["META"] > weights["AAPL"]
    assert abs(weights["NVDA"] - 0.375) < 1e-8
    assert abs(weights["META"] - 0.375) < 1e-8
    assert abs(weights["MSFT"] - 0.125) < 1e-8
    assert abs(weights["AAPL"] - 0.125) < 1e-8


def test_compute_signals_supports_conservative_profile_variant() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import compute_signals

    weights, _signal, _is_emergency, _status_desc, metadata = compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        leader_rotation_profile_variant="blend_top2_25_top4_75",
        soft_defense_exposure=1.0,
        hard_defense_exposure=1.0,
    )

    assert metadata["leader_rotation_profile_variant"] == "blend_top2_25_top4_75"
    assert metadata["blend_mode"] == "fixed_weighted_sleeves"
    assert abs(sum(weights.values()) - 1.0) < 1e-8
    assert abs(weights["NVDA"] - 0.3125) < 1e-8
    assert abs(weights["META"] - 0.3125) < 1e-8
    assert abs(weights["MSFT"] - 0.1875) < 1e-8
    assert abs(weights["AAPL"] - 0.1875) < 1e-8


def test_compute_signals_supports_top4_baseline_profile_variant() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import compute_signals

    weights, _signal, _is_emergency, _status_desc, metadata = compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        leader_rotation_profile_variant="top4_baseline",
        soft_defense_exposure=1.0,
        hard_defense_exposure=1.0,
    )

    assert metadata["leader_rotation_profile_variant"] == "top4_baseline"
    assert "blend_mode" not in metadata
    assert abs(sum(weights.values()) - 1.0) < 1e-8
    assert abs(weights["NVDA"] - 0.25) < 1e-8
    assert abs(weights["META"] - 0.25) < 1e-8
    assert abs(weights["MSFT"] - 0.25) < 1e-8
    assert abs(weights["AAPL"] - 0.25) < 1e-8


def test_compute_signals_can_report_shadow_profile_variants_without_changing_weights() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import compute_signals

    weights, _signal, _is_emergency, _status_desc, metadata = compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        leader_rotation_profile_variant="blend_top2_50_top4_50",
        leader_rotation_shadow_variants="all",
        soft_defense_exposure=1.0,
        hard_defense_exposure=1.0,
    )

    assert metadata["leader_rotation_profile_variant"] == "blend_top2_50_top4_50"
    assert abs(weights["NVDA"] - 0.375) < 1e-8
    assert abs(weights["META"] - 0.375) < 1e-8
    assert abs(weights["MSFT"] - 0.125) < 1e-8
    assert abs(weights["AAPL"] - 0.125) < 1e-8

    shadow = metadata["leader_rotation_shadow_variants"]
    assert tuple(shadow) == (
        "top4_baseline",
        "blend_top2_25_top4_75",
        "blend_top2_50_top4_50",
    )
    assert abs(shadow["top4_baseline"]["weights"]["NVDA"] - 0.25) < 1e-8
    assert abs(shadow["blend_top2_25_top4_75"]["weights"]["NVDA"] - 0.3125) < 1e-8
    assert abs(shadow["blend_top2_50_top4_50"]["weights"]["NVDA"] - 0.375) < 1e-8
    assert abs(shadow["top4_baseline"]["weight_delta_vs_active"]["NVDA"] + 0.125) < 1e-8
    assert abs(shadow["top4_baseline"]["weight_delta_vs_active"]["MSFT"] - 0.125) < 1e-8
    assert abs(shadow["top4_baseline"]["turnover_delta_vs_active"] - 0.25) < 1e-8
    assert shadow["top4_baseline"]["largest_weight_increase_vs_active"] == {"symbol": "AAPL", "delta": 0.125}
    assert shadow["top4_baseline"]["largest_weight_decrease_vs_active"] == {"symbol": "META", "delta": -0.125}
    assert shadow["blend_top2_50_top4_50"]["weight_delta_vs_active"] == {}
    assert shadow["blend_top2_50_top4_50"]["turnover_delta_vs_active"] == 0.0
    assert shadow["blend_top2_50_top4_50"]["largest_weight_increase_vs_active"] is None
    assert shadow["blend_top2_50_top4_50"]["largest_weight_decrease_vs_active"] is None


def test_compute_signals_builds_shadow_operator_review_rows() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import (
        SHADOW_REVIEW_ROW_FIELDS,
        compute_signals,
    )

    _weights, _signal, _is_emergency, _status_desc, metadata = compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        leader_rotation_profile_variant="blend_top2_50_top4_50",
        leader_rotation_shadow_variants="all",
        soft_defense_exposure=1.0,
        hard_defense_exposure=1.0,
    )

    rows = metadata["leader_rotation_shadow_review_rows"]
    assert metadata["leader_rotation_shadow_review_schema_version"] == "russell_top50_shadow_review.v1"
    assert tuple(metadata["leader_rotation_shadow_review_row_fields"]) == SHADOW_REVIEW_ROW_FIELDS
    assert tuple(rows[0]) == SHADOW_REVIEW_ROW_FIELDS
    assert json.loads(json.dumps(rows))[0]["shadow_variant"] == "top4_baseline"
    assert [row["shadow_variant"] for row in rows] == [
        "top4_baseline",
        "blend_top2_25_top4_75",
        "blend_top2_50_top4_50",
    ]
    assert all(row["active_variant"] == "blend_top2_50_top4_50" for row in rows)
    assert all(row["schema_version"] == "russell_top50_shadow_review.v1" for row in rows)
    assert rows[0]["largest_increase_symbol"] == "AAPL"
    assert rows[0]["largest_increase_delta"] == 0.125
    assert rows[0]["largest_decrease_symbol"] == "META"
    assert rows[0]["largest_decrease_delta"] == -0.125
    assert rows[0]["turnover_delta_vs_active"] == 0.25
    assert rows[0]["selected_count"] == 4
    assert rows[0]["safe_haven_weight"] == 0.0
    assert rows[0]["review_note"] == (
        "active=blend_top2_50_top4_50 shadow=top4_baseline "
        "turnover_delta=25.00% max_increase=AAPL:+12.50% max_decrease=META:-12.50%"
    )
    assert rows[2]["largest_increase_symbol"] == ""
    assert rows[2]["largest_decrease_symbol"] == ""
    assert "account" not in rows[0]["review_note"].lower()


def test_compute_signals_noops_outside_monthly_window() -> None:
    from us_equity_strategies.strategies.mega_cap_leader_rotation import compute_signals

    weights, _signal, _is_emergency, status_desc, metadata = compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        run_as_of="2026-04-10",
    )

    assert weights is None
    assert "no-op" in status_desc
    assert "outside_monthly_execution_window" in metadata["no_op_reason"]
    assert metadata["notification_context"]["signal"]["code"] == "signal_monthly_snapshot_waiting"
    assert metadata["notification_context"]["status"]["code"] == "status_monthly_snapshot_waiting_window"
