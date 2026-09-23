"""Synthetic checks for the research-only pre-risk-gate replay."""

from __future__ import annotations

import hashlib
import json
import tempfile
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest
from quant_platform_kit.strategy_lifecycle.contracts import PromotionCostModel
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

from us_equity_strategies.entrypoints._common import (
    default_signal_text_fn,
    default_translator,
)
from us_equity_strategies.manifests import (
    soxl_soxx_trend_income_manifest,
    tqqq_growth_income_manifest,
)
from us_equity_strategies.research.optimized_strategy_replay import (
    REPLAY_GAPS,
    DatedBar,
    ExecutionAssumptions,
    OptimizedStrategyReplayError,
    ReplayIdentity,
    ReplayRequest,
    replay_optimized_strategy,
)

SIGNAL = date(2024, 1, 2)
EXECUTE = date(2024, 1, 3)
SOXL_SYMBOLS = ("BOXX", "DGRO", "QQQI", "SCHD", "SGOV", "SOXL", "SOXX", "SPYI")


def _config(manifest) -> dict[str, object]:
    config = dict(manifest.default_config)
    config["signal_effective_after_trading_days"] = 1
    config["translator"] = default_translator
    config["signal_text_fn"] = default_signal_text_fn
    config["option_overlay_enabled"] = False
    config["option_growth_overlay_enabled"] = False
    config["option_income_overlay_enabled"] = False
    config["market_regime_control_enabled"] = False
    if manifest.profile == "tqqq_growth_income":
        config["benchmark_symbol"] = "QQQ"
        config["dual_drive_macro_risk_governor_enabled"] = False
        config["dual_drive_crisis_defense_enabled"] = False
        config["dual_drive_volatility_delever_taco_veto_enabled"] = False
        config["dual_drive_volatility_delever_retention_mode"] = "none"
    else:
        config["blend_gate_volatility_delever_retention_mode"] = "none"
        config["market_regime_control_apply_risk_off"] = False
        config["market_regime_control_apply_risk_reduced"] = False
    return config


def _identity(profile: str) -> ReplayIdentity:
    return ReplayIdentity(
        strategy_profile=profile,
        domain="us_equity",
        param_set_id="synthetic-hand-v1",
        source_revision="synthetic-source-revision",
    )


def _bars(sessions: tuple[date, ...], symbols: tuple[str, ...], prices: dict[str, float]) -> tuple[DatedBar, ...]:
    bars: list[DatedBar] = []
    for session in sessions:
        for symbol in symbols:
            price = prices[symbol]
            bars.append(DatedBar(session, symbol, price, price, price, price))
    return tuple(bars)


def _soxl_request(**overrides: object) -> ReplayRequest:
    sessions = (SIGNAL, EXECUTE)
    prices = {symbol: 100.0 for symbol in SOXL_SYMBOLS}
    prices["SOXL"] = 50.0
    request = ReplayRequest(
        identity=_identity("soxl_soxx_trend_income"),
        runtime_config=_config(soxl_soxx_trend_income_manifest),
        calendar=sessions,
        initial_cash=100_000.0,
        initial_quantities={symbol: 0.0 for symbol in SOXL_SYMBOLS},
        prices=_bars(sessions, SOXL_SYMBOLS, prices),
        execution=ExecutionAssumptions(
            signal_effective_after_trading_days=1,
            execution_timing_contract="next_trading_day",
            fill_price_field="close",
            nav_mark_field="close",
        ),
        cost_model=PromotionCostModel("HAND_10BPS", 10.0, 0.0, 0.0),
        computed_at="2024-01-03T21:00:00Z",
        evidence_use="fixture",
        promotion_eligible=False,
        calendar_id="fixture-calendar-v1",
        periods_per_year=252.0,
        derived_indicators={
            SIGNAL: {
                "soxl": {"price": 50.0, "ma_trend": 40.0},
                "soxx": {
                    "price": 120.0,
                    "ma_trend": 100.0,
                    "ma20_slope": -0.25,
                    "rsi14": 50.0,
                    "rsi14_dynamic_threshold": 65.0,
                    "bb_mid": 100.0,
                    "bb_upper": 130.0,
                    "bb_lower": 80.0,
                    "realized_volatility_10": 0.20,
                    "realized_volatility_10_dynamic_threshold": 0.50,
                    "realized_volatility_10_dynamic_sample_count": 252.0,
                },
            }
        },
    )
    for key, value in overrides.items():
        object.__setattr__(request, key, value)
    return request


def test_missing_runtime_field_is_rejected() -> None:
    config = _config(soxl_soxx_trend_income_manifest)
    del config["trend_ma_window"]
    with pytest.raises(OptimizedStrategyReplayError, match="MISSING_FIELD:trend_ma_window"):
        replay_optimized_strategy(_soxl_request(runtime_config=config))


def test_nonfinite_price_is_rejected() -> None:
    bars = list(_soxl_request().prices)
    bars[0] = DatedBar(SIGNAL, "BOXX", 100.0, 100.0, 100.0, float("nan"))
    with pytest.raises(OptimizedStrategyReplayError, match="NONFINITE_INPUT"):
        replay_optimized_strategy(_soxl_request(prices=tuple(bars)))


def test_calendar_that_disagrees_with_builder_timing_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "us_equity_strategies.entrypoints.apply_risk_gate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live risk gate called")),
    )
    sessions = (date(2024, 1, 3), date(2024, 1, 5))
    prices = {symbol: 100.0 for symbol in SOXL_SYMBOLS}
    prices["SOXL"] = 50.0
    request = _soxl_request(
        calendar=sessions,
        prices=_bars(sessions, SOXL_SYMBOLS, prices),
        derived_indicators={
            sessions[0]: _soxl_request().derived_indicators[SIGNAL],
        },
    )
    with pytest.raises(OptimizedStrategyReplayError, match="TIMING_MISMATCH"):
        replay_optimized_strategy(request)


def test_soxl_pre_gate_targets_match_hand_calculated_ledger(monkeypatch) -> None:
    monkeypatch.setattr(
        "us_equity_strategies.entrypoints.apply_risk_gate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live risk gate called")),
    )
    equity = 100_000.0
    reserve = equity * 0.03
    deployable = equity - reserve
    targets = {"SOXL": deployable * 0.70, "SOXX": deployable * 0.20, "BOXX": deployable * 0.10}
    fills = {"SOXL": 50.0, "SOXX": 100.0, "BOXX": 100.0}
    fee = sum(targets.values()) * 0.001
    result = replay_optimized_strategy(_soxl_request())

    assert result.live_executable is False
    assert result.risk_gate_applied is False
    assert result.gaps == REPLAY_GAPS
    assert [point.session for point in result.points] == [SIGNAL, EXECUTE]
    assert result.points[0].fees == 0.0
    assert result.points[0].nav == pytest.approx(equity)
    assert result.points[0].daily_return is None
    assert result.points[1].fees == pytest.approx(fee)
    assert result.points[1].cash == pytest.approx(reserve - fee)
    assert result.points[1].nav == pytest.approx(equity - fee)
    assert result.points[1].daily_return == pytest.approx((equity - fee) / equity - 1.0)
    holdings = dict(result.points[1].holdings)
    for symbol, target in targets.items():
        assert holdings[symbol] == pytest.approx(target / fills[symbol])
    for symbol in ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI"):
        assert holdings[symbol] == pytest.approx(0.0)
    backtest = result.backtest
    assert backtest.strategy_profile == "soxl_soxx_trend_income"
    assert backtest.domain == "us_equity"
    assert backtest.param_set_id == "synthetic-hand-v1"
    assert backtest.source_revision == "synthetic-source-revision"
    total_return = (equity - fee) / equity - 1.0
    assert backtest.start_date == SIGNAL
    assert backtest.end_date == EXECUTE
    assert backtest.observation_count == 1
    assert backtest.sharpe_ratio is None
    assert backtest.win_rate is None
    assert backtest.total_return == pytest.approx(total_return)
    assert backtest.cagr == pytest.approx((1.0 + total_return) ** 252.0 - 1.0)
    assert backtest.cost_model == "HAND_10BPS"
    assert backtest.cost_inputs["commission_bps"] == 10.0
    assert backtest.params["research_only"] is True
    assert backtest.params["live_executable"] is False
    assert backtest.params["risk_gate_applied"] is False
    assert backtest.params["decision_builder"] == "_build_soxl_soxx_trend_income_decision"
    assert backtest.params["fixture_only"] is True
    assert backtest.params["promotion_eligible"] is False
    assert backtest.calendar_id == "fixture-calendar-v1"
    assert backtest.periods_per_year == 252.0
    assert backtest.params["calendar_id"] == "fixture-calendar-v1"
    assert backtest.params["periods_per_year"] == 252.0
    assert backtest.params["effective_runtime_config"]["trend_ma_window"] == 140
    assert backtest.params["effective_runtime_config"]["translator"] == (
        "us_equity_strategies.entrypoints._common.default_translator"
    )
    assert backtest.params["effective_runtime_config"]["signal_text_fn"] == (
        "us_equity_strategies.entrypoints._common.default_signal_text_fn"
    )
    json.dumps(backtest.params)
    assert backtest.benchmark_cagr is None
    assert {"NO_SHARE_LOT_ROUNDING", "NO_CORPORATE_ACTIONS", "CASH_BPS_FEE_NOT_ADVERSE_FILL"} <= set(REPLAY_GAPS)


def test_non_qqq_benchmark_and_unsimulated_controls_are_rejected() -> None:
    tqqq_config = _config(tqqq_growth_income_manifest)
    tqqq_config["benchmark_symbol"] = "SPY"
    with pytest.raises(OptimizedStrategyReplayError, match="UNSUPPORTED_BENCHMARK"):
        replay_optimized_strategy(_soxl_request(
            identity=_identity("tqqq_growth_income"),
            runtime_config=tqqq_config,
        ))
    overlay_config = _config(soxl_soxx_trend_income_manifest)
    overlay_config["option_income_overlay_enabled"] = True
    with pytest.raises(OptimizedStrategyReplayError, match="UNSIMULATED_OPTION_OVERLAY"):
        replay_optimized_strategy(_soxl_request(runtime_config=overlay_config))
    plugin_config = _config(soxl_soxx_trend_income_manifest)
    plugin_config["market_regime_control_enabled"] = True
    with pytest.raises(OptimizedStrategyReplayError, match="UNSIMULATED_TARGET_PLUGIN"):
        replay_optimized_strategy(_soxl_request(runtime_config=plugin_config))
    implicit_macro = _config(tqqq_growth_income_manifest)
    del implicit_macro["dual_drive_macro_risk_governor_enabled"]
    with pytest.raises(OptimizedStrategyReplayError, match="UNSIMULATED_TARGET_PLUGIN"):
        replay_optimized_strategy(_soxl_request(
            identity=_identity("tqqq_growth_income"),
            runtime_config=implicit_macro,
        ))
    environment_retention = _config(tqqq_growth_income_manifest)
    environment_retention["dual_drive_volatility_delever_retention_mode"] = "environment"
    with pytest.raises(OptimizedStrategyReplayError, match="UNSIMULATED_TARGET_PLUGIN"):
        replay_optimized_strategy(_soxl_request(
            identity=_identity("tqqq_growth_income"),
            runtime_config=environment_retention,
        ))


def _business_days_ending(end: date, count: int) -> tuple[date, ...]:
    found: list[date] = []
    cursor = end
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor)
        cursor -= timedelta(days=1)
    return tuple(reversed(found))


def test_tqqq_flat_benchmark_parks_reserve_in_boxx(monkeypatch) -> None:
    monkeypatch.setattr(
        "us_equity_strategies.entrypoints.record_strategy_decision",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live recorder called")),
    )
    symbols = ("BOXX", "DGRO", "QQQI", "QQQM", "SCHD", "SGOV", "SPYI", "TQQQ")
    lookback = _business_days_ending(SIGNAL, 200)
    benchmark = tuple(
        DatedBar(session, "QQQ", 100.0, 100.0, 100.0, 100.0) for session in lookback
    )
    prices = _bars((SIGNAL, EXECUTE), symbols, {symbol: 100.0 for symbol in symbols})
    result = replay_optimized_strategy(
        ReplayRequest(
            identity=_identity("tqqq_growth_income"),
            runtime_config=_config(tqqq_growth_income_manifest),
            calendar=(SIGNAL, EXECUTE),
            initial_cash=100_000.0,
            initial_quantities={symbol: 0.0 for symbol in symbols},
            prices=prices,
            execution=ExecutionAssumptions(1, "next_trading_day", "close", "close"),
            cost_model=PromotionCostModel("ZERO", 0.0, 0.0, 0.0),
            computed_at="2024-01-03T21:00:00Z",
            evidence_use="fixture",
            promotion_eligible=False,
            calendar_id="fixture-calendar-v1",
            periods_per_year=252.0,
            benchmark_bars=benchmark,
        )
    )
    equity = 100_000.0
    reserve = equity * 0.02
    boxx_target = equity - reserve
    assert result.risk_gate_applied is False
    assert result.points[1].fees == pytest.approx(0.0)
    assert result.points[1].cash == pytest.approx(reserve)
    assert result.points[1].nav == pytest.approx(equity)
    assert result.points[1].daily_return == pytest.approx(0.0)
    holdings = dict(result.points[1].holdings)
    assert holdings["BOXX"] == pytest.approx(boxx_target / 100.0)
    assert holdings["TQQQ"] == pytest.approx(0.0)
    assert holdings["QQQM"] == pytest.approx(0.0)
    assert result.backtest.observation_count == 1
    assert result.backtest.win_rate is None
    assert result.backtest.params["decision_builder"] == "_build_tqqq_growth_income_decision"
    assert result.backtest.params["promotion_eligible"] is False
    assert result.backtest.total_return == pytest.approx(0.0)
    assert result.backtest.cagr == pytest.approx(0.0)
    assert result.backtest.sharpe_ratio is None


def test_tqqq_rejects_benchmark_shorter_than_enabled_volatility_window() -> None:
    symbols = ("BOXX", "DGRO", "QQQI", "QQQM", "SCHD", "SGOV", "SPYI", "TQQQ")

    def run(bar_count: int, **updates: object) -> None:
        config = _config(tqqq_growth_income_manifest)
        config.update(updates)
        lookback = _business_days_ending(SIGNAL, bar_count)
        replay_optimized_strategy(
            ReplayRequest(
                identity=_identity("tqqq_growth_income"),
                runtime_config=config,
                calendar=(SIGNAL, EXECUTE),
                initial_cash=100_000.0,
                initial_quantities={symbol: 0.0 for symbol in symbols},
                prices=_bars((SIGNAL, EXECUTE), symbols, {symbol: 100.0 for symbol in symbols}),
                execution=ExecutionAssumptions(1, "next_trading_day", "close", "close"),
                cost_model=PromotionCostModel("ZERO", 0.0, 0.0, 0.0),
                computed_at="2024-01-03T21:00:00Z",
                evidence_use="fixture",
                promotion_eligible=False,
                calendar_id="fixture-calendar-v1",
                periods_per_year=252.0,
                benchmark_bars=tuple(
                    DatedBar(session, "QQQ", 100.0, 100.0, 100.0, 100.0) for session in lookback
                ),
            )
        )

    with pytest.raises(OptimizedStrategyReplayError, match="INSUFFICIENT_BENCHMARK"):
        run(
            200,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=250,
        )
    with pytest.raises(OptimizedStrategyReplayError, match="INSUFFICIENT_BENCHMARK"):
        run(
            250,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=250,
            dual_drive_volatility_delever_threshold_mode="fixed",
        )
    run(
        251,
        dual_drive_volatility_delever_enabled=True,
        dual_drive_volatility_delever_window=250,
        dual_drive_volatility_delever_threshold_mode="fixed",
    )
    with pytest.raises(OptimizedStrategyReplayError, match="INSUFFICIENT_BENCHMARK"):
        run(
            200,
            dual_drive_volatility_delever_enabled=True,
            dual_drive_volatility_delever_window=5,
            dual_drive_volatility_delever_threshold_mode="rolling_percentile",
            dual_drive_volatility_delever_dynamic_min_periods=205,
        )
    run(
        210,
        dual_drive_volatility_delever_enabled=True,
        dual_drive_volatility_delever_window=5,
        dual_drive_volatility_delever_threshold_mode="rolling_percentile",
        dual_drive_volatility_delever_dynamic_min_periods=205,
    )
    run(200, dual_drive_volatility_delever_enabled=False, dual_drive_volatility_delever_window=250)


def test_enabled_rsi_control_rejects_a_missing_indicator() -> None:
    indicators = deepcopy(_soxl_request().derived_indicators)
    del indicators[SIGNAL]["soxx"]["rsi14"]
    with pytest.raises(OptimizedStrategyReplayError, match="MISSING_FIELD:derived_indicators.soxx.rsi14"):
        replay_optimized_strategy(_soxl_request(derived_indicators=indicators))


def test_disabled_overlay_controls_do_not_require_those_indicators() -> None:
    config = _config(soxl_soxx_trend_income_manifest)
    config["blend_gate_rsi_cap_enabled"] = False
    config["blend_gate_dynamic_rsi_threshold_enabled"] = False
    config["blend_gate_bollinger_cap_enabled"] = False
    indicators = deepcopy(_soxl_request().derived_indicators)
    for key in ("rsi14", "rsi14_dynamic_threshold", "bb_mid", "bb_upper", "bb_lower"):
        del indicators[SIGNAL]["soxx"][key]
    result = replay_optimized_strategy(_soxl_request(runtime_config=config, derived_indicators=indicators))
    assert result.backtest.observation_count == 1
    assert dict(result.points[1].holdings)["SOXL"] == pytest.approx((100_000.0 * 0.97 * 0.70) / 50.0)


def test_negative_slope_is_a_legal_signed_indicator() -> None:
    indicators = deepcopy(_soxl_request().derived_indicators)
    indicators[SIGNAL]["soxx"]["ma20_slope"] = -4.5
    result = replay_optimized_strategy(_soxl_request(derived_indicators=indicators))
    assert result.points[1].nav == pytest.approx(100_000.0 - (100_000.0 * 0.97 * 0.001))


def test_static_plugin_metadata_and_real_entry_are_rejected() -> None:
    with pytest.raises(OptimizedStrategyReplayError, match="NONEMPTY_PORTFOLIO_METADATA"):
        replay_optimized_strategy(
            _soxl_request(portfolio_metadata={"market_regime_control": {"plugin": "market_regime_control"}})
        )
    with pytest.raises(OptimizedStrategyReplayError, match="NONEMPTY_PORTFOLIO_METADATA"):
        replay_optimized_strategy(_soxl_request(portfolio_metadata={"note": "static"}))
    custom = _config(soxl_soxx_trend_income_manifest)
    custom["translator"] = lambda key, **kwargs: key
    with pytest.raises(OptimizedStrategyReplayError, match="INVALID_FIELD:translator"):
        replay_optimized_strategy(_soxl_request(runtime_config=custom))
    with pytest.raises(OptimizedStrategyReplayError, match="REAL_ENTRY_PIT_REQUIRED"):
        replay_optimized_strategy(_soxl_request(evidence_use="production"))
    with pytest.raises(OptimizedStrategyReplayError, match="INVALID_FIELD:promotion_eligible"):
        replay_optimized_strategy(_soxl_request(promotion_eligible=True))


def test_run_id_changes_with_cost_numbers_config_and_opening_ledger() -> None:
    baseline = replay_optimized_strategy(_soxl_request())
    different_cost = replay_optimized_strategy(
        _soxl_request(cost_model=PromotionCostModel("HAND_10BPS", 11.0, 0.0, 0.0))
    )
    config = _config(soxl_soxx_trend_income_manifest)
    config["trend_ma_window"] = 141
    different_config = replay_optimized_strategy(_soxl_request(runtime_config=config))
    quantities = {symbol: 0.0 for symbol in SOXL_SYMBOLS}
    quantities["SOXL"] = 1.0
    different_ledger = replay_optimized_strategy(
        _soxl_request(initial_cash=99_950.0, initial_quantities=quantities)
    )
    identifiers = {
        baseline.backtest.run_id,
        different_cost.backtest.run_id,
        different_config.backtest.run_id,
        different_ledger.backtest.run_id,
    }
    assert len(identifiers) == 4
    assert baseline.backtest.cost_model == different_cost.backtest.cost_model
    assert different_config.backtest.params["effective_runtime_config"]["trend_ma_window"] == 141
    assert (
        baseline.backtest.params["effective_runtime_config_sha256"]
        != different_config.backtest.params["effective_runtime_config_sha256"]
    )


def test_periods_per_year_is_explicit_in_cagr() -> None:
    annual = replay_optimized_strategy(_soxl_request(periods_per_year=365.0))
    total_return = annual.backtest.total_return
    assert annual.backtest.periods_per_year == 365.0
    assert annual.backtest.calendar_id == "fixture-calendar-v1"
    assert annual.backtest.params["periods_per_year"] == 365.0
    assert annual.backtest.cagr == pytest.approx((1.0 + total_return) ** 365.0 - 1.0)
    assert annual.backtest.observation_count == 1


def _run_backtest_path(root: Path, run_id: str, param_version: int) -> Path:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return (
        root
        / "backtest"
        / "us_equity"
        / "soxl_soxx_trend_income"
        / "runs"
        / digest
        / f"backtest_v{param_version}.json"
    )


def test_same_computed_at_different_costs_round_trip_from_local_store() -> None:
    cheap = replay_optimized_strategy(_soxl_request())
    costly = replay_optimized_strategy(
        _soxl_request(cost_model=PromotionCostModel("HAND_10BPS", 11.0, 0.0, 0.0))
    )
    assert cheap.backtest.computed_at == costly.backtest.computed_at == "2024-01-03T21:00:00Z"
    assert cheap.backtest.run_id != costly.backtest.run_id
    assert cheap.backtest.cost_inputs["commission_bps"] == 10.0
    assert costly.backtest.cost_inputs["commission_bps"] == 11.0

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = PerformanceStore(local_root=root, cloud_bucket="")
        store.save_backtest_result(cheap.backtest)
        store.save_backtest_result(costly.backtest)
        cheap_path = _run_backtest_path(root, cheap.backtest.run_id, cheap.backtest.param_version)
        costly_path = _run_backtest_path(root, costly.backtest.run_id, costly.backtest.param_version)
        saved_files = sorted(root.rglob("*.json"))
        cheap_payload = json.loads(cheap_path.read_text(encoding="utf-8"))
        costly_payload = json.loads(costly_path.read_text(encoding="utf-8"))
        loaded_cheap = store.load_backtest_by_run_id(
            "us_equity",
            "soxl_soxx_trend_income",
            cheap.backtest.run_id,
        )
        loaded_costly = store.load_backtest_by_run_id(
            "us_equity",
            "soxl_soxx_trend_income",
            costly.backtest.run_id,
        )
        missing_run = store.load_backtest_by_run_id(
            "us_equity",
            "soxl_soxx_trend_income",
            "missing-run",
        )
        wrong_profile = store.load_backtest_by_run_id(
            "us_equity",
            "tqqq_growth_income",
            cheap.backtest.run_id,
        )
        assert store.cloud_bucket == ""
        assert saved_files == sorted((cheap_path, costly_path))
        assert cheap_path.is_file() and costly_path.is_file()
        assert cheap_path.parent != costly_path.parent
        assert not list((root / "backtest" / "us_equity" / "soxl_soxx_trend_income").glob("backtest_v*.json"))

    assert cheap_payload["run_id"] == cheap.backtest.run_id
    assert costly_payload["run_id"] == costly.backtest.run_id
    assert cheap_payload["cost_inputs"] == dict(cheap.backtest.cost_inputs)
    assert costly_payload["cost_inputs"] == dict(costly.backtest.cost_inputs)
    for original, loaded in ((cheap.backtest, loaded_cheap), (costly.backtest, loaded_costly)):
        assert loaded is not None
        assert loaded.run_id == original.run_id
        assert loaded.strategy_profile == original.strategy_profile == "soxl_soxx_trend_income"
        assert loaded.domain == original.domain == "us_equity"
        assert loaded.param_set_id == original.param_set_id == "synthetic-hand-v1"
        assert loaded.source_revision == original.source_revision == "synthetic-source-revision"
        assert loaded.calendar_id == original.calendar_id == "fixture-calendar-v1"
        assert loaded.periods_per_year == original.periods_per_year == 252.0
        assert loaded.computed_at == original.computed_at
        assert loaded.cost_model == original.cost_model == "HAND_10BPS"
        assert dict(loaded.cost_inputs) == dict(original.cost_inputs)
        assert loaded.observation_count == original.observation_count == 1
        assert loaded.total_return == pytest.approx(original.total_return)
        assert loaded.cagr == pytest.approx(original.cagr)
        assert loaded.max_drawdown == pytest.approx(original.max_drawdown)
        assert loaded.sharpe_ratio is None
        assert loaded.volatility is None
        assert loaded.win_rate is None
        assert loaded.start_date == original.start_date == SIGNAL
        assert loaded.end_date == original.end_date == EXECUTE
    assert loaded_cheap.total_return != pytest.approx(loaded_costly.total_return)
    assert missing_run is None
    assert wrong_profile is None
