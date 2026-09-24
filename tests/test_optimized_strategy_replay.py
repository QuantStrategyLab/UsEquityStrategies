"""Synthetic checks for the research-only pre-risk-gate replay."""

from __future__ import annotations

import hashlib
import json
import tempfile
from copy import deepcopy
from dataclasses import replace
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
from us_equity_strategies.research.local_member_replay_input import (
    LocalMemberInputError,
    load_local_member_fixture,
    produce_local_member_fixture,
)
from us_equity_strategies.research.optimized_member_identity import (
    build_optimized_member_identity,
    calculate_optimized_member_identity_sha256,
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


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _local_fixture_file(path: Path) -> dict[str, object]:
    request = _soxl_request()
    config = {key: value for key, value in request.runtime_config.items() if key not in {"translator", "signal_text_fn"}}
    config = json.loads(json.dumps(config))
    source = {
        "runtime_config": config,
        "calendar": [day.isoformat() for day in request.calendar],
        "initial_cash": request.initial_cash,
        "initial_quantities": dict(request.initial_quantities),
        "prices": [
            {"session": bar.session.isoformat(), "symbol": bar.symbol, "open": bar.open,
             "high": bar.high, "low": bar.low, "close": bar.close}
            for bar in request.prices
        ],
        "computed_at": request.computed_at,
        "derived_indicators": {day.isoformat(): values for day, values in request.derived_indicators.items()},
        "benchmark_bars": [],
    }
    identity = build_optimized_member_identity(
        strategy_profile="soxl_soxx_trend_income", ues_revision="a" * 40,
        qpk_revision="b" * 40, ues_workspace_patch_sha256=None,
        param_set_id="synthetic-local", actual_params=config,
        config_sha256=hashlib.sha256(_canonical(config)).hexdigest(),
        input_sha256=hashlib.sha256(_canonical(source)).hexdigest(),
        window_start=source["calendar"][0], window_end=source["calendar"][-1],
        calendar_id="synthetic-calendar", periods_per_year=252,
        cost_source="SYNTHETIC_10BPS", cost_inputs={"commission_bps": 10, "slippage_bps": 0, "market_impact_bps": 0},
        fill_price_field="close", adjustment_contract="synthetic unadjusted prices",
        cash_contract="synthetic zero interest", corporate_action_contract="synthetic no events",
        external_cashflow_contract="synthetic no external flows", share_quantity_contract="synthetic fractional units",
    )
    payload = {"identity": identity, "input": source}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_local_file_produces_only_synthetic_ledger(tmp_path: Path) -> None:
    path = tmp_path / "member.json"
    payload = _local_fixture_file(path)
    identity, request = load_local_member_fixture(path)
    assert identity == payload["identity"]
    assert request.promotion_eligible is False
    store = PerformanceStore(local_root=tmp_path / "store", cloud_bucket="")
    _, record = produce_local_member_fixture(path, store, trial_id="local-synthetic")
    assert record.synthetic is True
    ledger = store.load_research_ledger("us_equity", "soxl_soxx_trend_income", "local-synthetic", record.run_id, record.param_version)
    assert ledger is not None and ledger.synthetic is True
    previous_cash, previous_nav = ledger.initial_cash, ledger.initial_nav
    for day in ledger.days:
        assert day.nav == pytest.approx(day.cash + sum(position.valuation for position in day.positions))
        assert day.cash == pytest.approx(previous_cash + day.trade_net_cashflow - day.fees)
        assert day.daily_return == pytest.approx(day.nav / previous_nav - 1.0)
        previous_cash, previous_nav = day.cash, day.nav


def test_local_file_rejects_changed_input_and_unsupported_control(tmp_path: Path) -> None:
    path = tmp_path / "member.json"
    payload = _local_fixture_file(path)
    payload["input"]["initial_cash"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LocalMemberInputError, match="LOCAL_MEMBER_INPUT_INVALID"):
        load_local_member_fixture(path)
    payload = _local_fixture_file(path)
    payload["input"]["runtime_config"]["option_overlay_enabled"] = True
    config = payload["input"]["runtime_config"]
    payload["identity"]["actual_params"] = config
    payload["identity"]["config_sha256"] = hashlib.sha256(_canonical(config)).hexdigest()
    payload["identity"]["input_sha256"] = hashlib.sha256(_canonical(payload["input"])).hexdigest()
    payload["identity"]["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(payload["identity"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="UNSIMULATED_OPTION_OVERLAY"):
        replay_optimized_strategy(request)


def _rebind_input_digest(payload: dict[str, object]) -> None:
    identity = payload["identity"]
    encoded = json.dumps(
        payload["input"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    identity["input_sha256"] = hashlib.sha256(encoded).hexdigest()
    identity["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(identity)


def test_local_file_rejects_python_equal_config_types(tmp_path: Path) -> None:
    path = tmp_path / "member.json"
    payload = _local_fixture_file(path)
    config = payload["input"]["runtime_config"]
    config["option_overlay_enabled"] = 0
    assert config["option_overlay_enabled"] == payload["identity"]["actual_params"]["option_overlay_enabled"]
    payload["identity"]["config_sha256"] = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LocalMemberInputError, match="LOCAL_MEMBER_INPUT_INVALID"):
        load_local_member_fixture(path)


def test_local_file_rejects_non_object_derived_indicators(tmp_path: Path) -> None:
    path = tmp_path / "member.json"
    payload = _local_fixture_file(path)
    payload["input"]["derived_indicators"] = []
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LocalMemberInputError, match="LOCAL_MEMBER_INPUT_INVALID"):
        load_local_member_fixture(path)


def test_local_tqqq_file_requires_benchmark_warmup(tmp_path: Path) -> None:
    path = tmp_path / "tqqq.json"
    payload = _local_fixture_file(path)
    source = payload["input"]
    config = _config(tqqq_growth_income_manifest)
    source["runtime_config"] = json.loads(json.dumps({key: value for key, value in config.items() if key not in {"translator", "signal_text_fn"}}))
    symbols = tuple(source["runtime_config"]["managed_symbols"])
    source["initial_quantities"] = {symbol: 0.0 for symbol in symbols}
    source["prices"] = [
        {"session": day.isoformat(), "symbol": symbol, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
        for day in (SIGNAL, EXECUTE) for symbol in symbols
    ]
    source["derived_indicators"] = None
    source["benchmark_bars"] = [
        {"session": day.isoformat(), "symbol": "QQQ", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0}
        for day in _business_days_ending(SIGNAL, 200)
    ]
    identity = payload["identity"]
    identity["strategy_profile"] = "tqqq_growth_income"
    identity["actual_params"] = source["runtime_config"]
    identity["config_sha256"] = hashlib.sha256(_canonical(source["runtime_config"])).hexdigest()
    identity["input_sha256"] = hashlib.sha256(_canonical(source)).hexdigest()
    identity["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(identity)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    assert len(request.benchmark_bars) == 200
    assert replay_optimized_strategy(request).live_executable is False
    source["benchmark_bars"].pop(0)
    identity["input_sha256"] = hashlib.sha256(_canonical(source)).hexdigest()
    identity["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(identity)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, short_request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="INSUFFICIENT_BENCHMARK"):
        replay_optimized_strategy(short_request)


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
    assert {"NO_SHARE_LOT_ROUNDING", "NO_CORPORATE_ACTIONS", "SYNTHETIC_BPS_NOT_LIVE_FEES"} <= set(REPLAY_GAPS)


def test_soxl_local_vol_trigger_and_recovery_follows_next_trading_day(monkeypatch) -> None:
    monkeypatch.setattr(
        "us_equity_strategies.entrypoints.apply_risk_gate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live risk gate called")),
    )
    sessions = (
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    )
    config = _config(soxl_soxx_trend_income_manifest)
    config["blend_gate_volatility_delever_retention_mode"] = "fixed"
    assert config["blend_gate_volatility_delever_enabled"] is True
    assert config["blend_gate_volatility_delever_symbol"] == "SOXX"
    assert config["blend_gate_volatility_delever_window"] == 10
    assert config["blend_gate_volatility_delever_threshold_mode"] == "rolling_percentile"
    assert config["blend_gate_volatility_delever_retention_ratio"] == 0.0
    assert config["blend_gate_volatility_delever_redirect_symbol"] == "SOXX"
    for key in (
        "option_overlay_enabled",
        "option_growth_overlay_enabled",
        "option_income_overlay_enabled",
        "market_regime_control_enabled",
        "market_regime_control_apply_risk_off",
        "market_regime_control_apply_risk_reduced",
    ):
        assert config[key] is False

    base_indicators = deepcopy(_soxl_request().derived_indicators[SIGNAL])
    volatilities = (0.20, 0.60, 0.20)
    indicators = {}
    for session, volatility in zip(sessions[:-1], volatilities, strict=True):
        payload = deepcopy(base_indicators)
        assert payload["soxx"]["realized_volatility_10_dynamic_threshold"] == 0.50
        payload["soxx"]["realized_volatility_10"] = volatility
        indicators[session] = payload
    prices = {symbol: 100.0 for symbol in SOXL_SYMBOLS}
    prices["SOXL"] = 50.0
    request = _soxl_request(
        runtime_config=config,
        calendar=sessions,
        prices=_bars(sessions, SOXL_SYMBOLS, prices),
        derived_indicators=indicators,
        cost_model=PromotionCostModel("ZERO", 0.0, 0.0, 0.0),
        portfolio_metadata=None,
        evidence_use="fixture",
        promotion_eligible=False,
    )
    assert request.portfolio_metadata is None
    assert request.evidence_use == "fixture"
    assert request.promotion_eligible is False

    result = replay_optimized_strategy(request)

    equity = 100_000.0
    reserve = equity * 0.03
    deployable = equity - reserve
    fills = {"SOXL": 50.0, "SOXX": 100.0, "BOXX": 100.0}
    baseline = {"SOXL": deployable * 0.70, "SOXX": deployable * 0.20, "BOXX": deployable * 0.10}
    delevered = {"SOXL": 0.0, "SOXX": deployable * 0.90, "BOXX": deployable * 0.10}
    expected_targets = (baseline, delevered, baseline)
    points = result.points
    assert [point.session for point in points] == list(sessions)
    assert points[0].fees == 0.0
    assert points[0].cash == pytest.approx(equity)
    assert points[0].nav == pytest.approx(equity)
    assert points[0].daily_return is None
    assert all(quantity == pytest.approx(0.0) for _, quantity in points[0].holdings)
    for index, targets in enumerate(expected_targets, start=1):
        point = points[index]
        previous = points[index - 1]
        assert point.session == sessions[index]
        assert point.session == sessions[index - 1] + timedelta(days=1)
        assert point.fees == 0.0
        assert point.cash == pytest.approx(reserve)
        assert point.nav == pytest.approx(point.cash + sum(value for _, value in point.market_values))
        assert point.nav == pytest.approx(previous.nav)
        assert point.daily_return == pytest.approx(0.0)
        holdings = dict(point.holdings)
        for symbol, target in targets.items():
            assert holdings[symbol] == pytest.approx(target / fills[symbol])
        for symbol in ("SCHD", "DGRO", "SGOV", "SPYI", "QQQI"):
            assert holdings[symbol] == pytest.approx(0.0)
    triggered = dict(points[2].holdings)
    baseline_holdings = dict(points[1].holdings)
    restored = dict(points[3].holdings)
    assert triggered["SOXL"] < baseline_holdings["SOXL"]
    assert triggered["SOXX"] > baseline_holdings["SOXX"]
    assert restored["SOXL"] == pytest.approx(baseline_holdings["SOXL"])
    assert restored["SOXX"] == pytest.approx(baseline_holdings["SOXX"])
    assert result.backtest.params["evidence_use"] == "fixture"
    assert result.backtest.params["promotion_eligible"] is False
    assert result.backtest.params["execution_timing_contract"] == "next_trading_day"
    effective = result.backtest.params["effective_runtime_config"]
    assert effective["blend_gate_volatility_delever_retention_mode"] == "fixed"
    assert effective["market_regime_control_enabled"] is False
    assert effective["option_overlay_enabled"] is False


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


def _research_path(root: Path, profile: str, trial_id: str, name: str) -> Path:
    material = f"us_equity\0{profile}\0{trial_id}".encode()
    digest = hashlib.sha256(material).hexdigest()
    return root / "research_trial" / digest / f"{name}.json"


def test_rejected_trial_readback_has_no_metrics() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        persist_optimized_strategy_trial,
    )

    config = _config(soxl_soxx_trend_income_manifest)
    config["option_income_overlay_enabled"] = True
    request = _soxl_request(runtime_config=config)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = PerformanceStore(local_root=root, cloud_bucket="")
        returned = persist_optimized_strategy_trial(request, store, trial_id="fixture-soxl-reject")
        loaded = store.load_research_trial("us_equity", "soxl_soxx_trend_income", "fixture-soxl-reject")
        started = json.loads(_research_path(root, "soxl_soxx_trend_income", "fixture-soxl-reject", "started").read_text())
        terminal = json.loads(_research_path(root, "soxl_soxx_trend_income", "fixture-soxl-reject", "terminal").read_text())
        assert store.load_research_ledger(
            "us_equity", "soxl_soxx_trend_income", "fixture-soxl-reject", "unused-run", 1
        ) is None
        assert list(root.rglob("backtest/**/*.json")) == []
    assert returned.status.value == "rejected"
    assert loaded is not None
    assert loaded.status.value == "rejected"
    assert loaded.reason_code == "unsimulated_option_overlay"
    assert loaded.run_id is None
    assert loaded.param_version is None
    assert loaded.actual_params is None
    assert loaded.synthetic is True
    assert started["status"] == "started"
    assert terminal["status"] == "rejected"
    assert terminal["reason_code"] == "unsimulated_option_overlay"
    assert ":" not in terminal["reason_code"]


def test_builder_exception_is_failed_without_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    from us_equity_strategies.research import optimized_strategy_replay as replay_module
    from us_equity_strategies.research.optimized_strategy_replay import (
        persist_optimized_strategy_trial,
    )

    def _boom(_ctx: object) -> None:
        raise RuntimeError("builder-boom-detail")

    manifest, _builder = replay_module._PROFILES["soxl_soxx_trend_income"]
    monkeypatch.setitem(replay_module._PROFILES, "soxl_soxx_trend_income", (manifest, _boom))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = PerformanceStore(local_root=root, cloud_bucket="")
        returned = persist_optimized_strategy_trial(_soxl_request(), store, trial_id="fixture-soxl-builder")
        loaded = store.load_research_trial("us_equity", "soxl_soxx_trend_income", "fixture-soxl-builder")
        terminal_text = _research_path(root, "soxl_soxx_trend_income", "fixture-soxl-builder", "terminal").read_text()
        assert store.load_research_ledger(
            "us_equity", "soxl_soxx_trend_income", "fixture-soxl-builder", "unused-run", 1
        ) is None
        assert list(root.rglob("backtest/**/*.json")) == []
    assert returned.status.value == "failed"
    assert loaded is not None
    assert loaded.status.value == "failed"
    assert loaded.reason_code == "decision_invalid"
    assert loaded.run_id is None
    assert "builder-boom-detail" not in terminal_text


def test_succeeded_trial_readback_matches_executed_ledger() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        persist_optimized_strategy_trial,
    )

    request = _soxl_request()
    replay = replay_optimized_strategy(request)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = PerformanceStore(local_root=root, cloud_bucket="")
        persist_optimized_strategy_trial(request, store, trial_id="fixture-soxl-success")
        loaded = store.load_research_trial(
            "us_equity",
            "soxl_soxx_trend_income",
            "fixture-soxl-success",
            run_id=replay.backtest.run_id,
            param_version=replay.backtest.param_version,
        )
        result = store.load_backtest_by_run_id(
            "us_equity",
            "soxl_soxx_trend_income",
            replay.backtest.run_id,
            param_version=replay.backtest.param_version,
        )
        assert loaded is not None and result is not None
        ledger = store.load_research_ledger(
            "us_equity",
            "soxl_soxx_trend_income",
            "fixture-soxl-success",
            loaded.run_id,
            loaded.param_version,
        )
        wrong_version = store.load_research_trial(
            "us_equity",
            "soxl_soxx_trend_income",
            "fixture-soxl-success",
            run_id=replay.backtest.run_id,
            param_version=replay.backtest.param_version + 1,
        )
        started = json.loads(_research_path(root, "soxl_soxx_trend_income", "fixture-soxl-success", "started").read_text())
        assert started["status"] == "started"
        assert started["run_id"] is None
    assert ledger is not None
    assert loaded.status.value == "succeeded"
    assert loaded.reason_code == ""
    assert dict(loaded.actual_params) == dict(result.params) == dict(replay.backtest.params)
    assert result.run_id == replay.backtest.run_id
    assert result.observation_count == ledger.observation_count == 1
    assert result.total_return == ledger.total_return == replay.backtest.total_return
    assert ledger.initial_session_date == replay.points[0].session
    assert ledger.initial_nav == replay.points[0].nav
    assert ledger.initial_cash == replay.points[0].cash
    assert ledger.days[0].session_date == replay.points[1].session
    assert ledger.days[0].cash == replay.points[1].cash
    assert ledger.days[0].fees == replay.points[1].fees
    assert ledger.days[0].nav == replay.points[1].nav
    assert ledger.days[0].daily_return == replay.points[1].daily_return
    assert {mark.symbol: mark.quantity for mark in ledger.days[0].positions} == {
        symbol: quantity for symbol, quantity in replay.points[1].holdings if quantity != 0.0
    }
    assert ledger.calendar_id == result.calendar_id == replay.backtest.calendar_id
    assert ledger.periods_per_year == result.periods_per_year == 252.0
    assert dict(ledger.cost_inputs) == dict(result.cost_inputs)
    assert ledger.cost_source == result.cost_model
    assert wrong_version is None


def test_store_failure_does_not_report_success() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        persist_optimized_strategy_trial,
    )

    class _UnavailableStore(PerformanceStore):
        def save_research_trial(self, trial: object) -> None:
            raise OSError("unavailable")

    with tempfile.TemporaryDirectory() as tmp:
        store = _UnavailableStore(local_root=Path(tmp), cloud_bucket="")
        with pytest.raises(OSError, match="unavailable"):
            persist_optimized_strategy_trial(_soxl_request(), store, trial_id="fixture-soxl-store")
        assert store.load_research_trial("us_equity", "soxl_soxx_trend_income", "fixture-soxl-store") is None


def test_input_id_changes_with_prices_or_indicators() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        _input_id,
        persist_optimized_strategy_trial,
    )

    baseline = _soxl_request()
    bar = baseline.prices[0]
    priced = _soxl_request(prices=tuple(
        DatedBar(bar.session, bar.symbol, bar.open, bar.high + 1.0, bar.low, bar.close + 1.0)
        if index == 0 else item
        for index, item in enumerate(baseline.prices)
    ))
    indicators = deepcopy(baseline.derived_indicators)
    indicators[SIGNAL]["soxx"]["rsi14"] = 51.0
    indicated = _soxl_request(derived_indicators=indicators)
    richer_cash = _soxl_request(initial_cash=100_001.0)
    assert baseline.calendar == priced.calendar == indicated.calendar
    assert _input_id(baseline) != _input_id(priced)
    assert _input_id(baseline) != _input_id(indicated)
    assert _input_id(baseline) != _input_id(richer_cash)
    assert _input_id(baseline) == _input_id(_soxl_request())
    broken = list(baseline.prices)
    broken[0] = DatedBar(bar.session, bar.symbol, float("nan"), bar.high, bar.low, bar.close)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = PerformanceStore(local_root=root, cloud_bucket="")
        with pytest.raises(OptimizedStrategyReplayError, match="NONFINITE_INPUT"):
            persist_optimized_strategy_trial(
                _soxl_request(prices=tuple(broken)),
                store,
                trial_id="fixture-soxl-nonfinite",
            )
        assert list(root.rglob("*.json")) == []


def test_tampered_cash_keeps_trade_flow_and_is_rejected() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        _research_ledger,
        _started_trial,
    )

    request = _soxl_request()
    replay = replay_optimized_strategy(request)
    opening, settled = replay.points
    assert opening.trade_net_cashflow == 0.0
    assert settled.trade_net_cashflow == settled.cash - opening.cash + settled.fees
    tampered_nav = settled.nav + 50.0
    tampered = replace(
        settled,
        cash=settled.cash + 50.0,
        nav=tampered_nav,
        daily_return=tampered_nav / opening.nav - 1.0,
    )
    assert tampered.trade_net_cashflow == settled.trade_net_cashflow
    with pytest.raises(ValueError, match="ledger_cash"):
        _research_ledger(replace(replay, points=(opening, tampered)), _started_trial(request, "fixture-soxl-tamper"))


def test_large_notional_cash_matches_trade_flow_in_qpk_ledger() -> None:
    from quant_platform_kit.strategy_lifecycle.contracts import (
        ResearchDailyLedger,
        ResearchLedgerDay,
        ResearchPositionMark,
    )

    from us_equity_strategies.research.optimized_strategy_replay import _rebalance

    opening = 10_000_000.0
    fills = {"A": 531.2377222491674, "B": 610.5374082422157}
    targets = {"A": 2577708.681153131, "B": 7422291.318846868}
    cash, quantities, fee, trade_net = _rebalance(opening, {"A": 0.0, "B": 0.0}, targets, fills, 0.0)
    assert fee == 0.0
    assert cash == opening + trade_net - fee
    assert cash != 0.0
    marks = tuple(
        ResearchPositionMark(symbol, quantities[symbol], quantities[symbol] * fills[symbol])
        for symbol in ("A", "B")
    )
    nav = cash + sum(mark.valuation for mark in marks)
    day = ResearchLedgerDay(
        EXECUTE,
        cash,
        marks,
        trade_net,
        fee,
        nav,
        nav / opening - 1.0,
    )
    ledger_kwargs = {
        "trial_id": "fixture-large-notional",
        "domain": "us_equity",
        "strategy_profile": "soxl_soxx_trend_income",
        "run_id": "fixture-large-notional-run",
        "param_version": 1,
        "input_id": "fixture-large-notional-input",
        "calendar_id": "fixture-calendar-v1",
        "periods_per_year": 252.0,
        "cost_source": "EXPLICIT_ZERO",
        "cost_inputs": {"commission_bps": 0.0},
        "initial_session_date": SIGNAL,
        "initial_nav": opening,
        "initial_cash": opening,
        "initial_positions": (),
        "synthetic": True,
    }
    accepted = ResearchDailyLedger(days=(day,), **ledger_kwargs)
    assert accepted.observation_count == 1
    tampered_nav = nav + 50.0
    tampered = replace(day, cash=cash + 50.0, nav=tampered_nav, daily_return=tampered_nav / opening - 1.0)
    assert tampered.trade_net_cashflow == trade_net
    with pytest.raises(ValueError, match="ledger_cash"):
        ResearchDailyLedger(days=(tampered,), **ledger_kwargs)


def test_hundred_million_full_deployment_ulp_is_accepted_and_real_shortfall_is_not() -> None:
    from quant_platform_kit.strategy_lifecycle.contracts import (
        ResearchDailyLedger,
        ResearchLedgerDay,
        ResearchPositionMark,
    )

    from us_equity_strategies.research.optimized_strategy_replay import _rebalance

    opening = 100_000_000.0
    fills = {"A": 551.9657193551195, "B": 205.2260336967429}
    targets = {"A": 73739074.86326925, "B": 26260925.13673075}
    cash, quantities, fee, trade_net = _rebalance(opening, {"A": 0.0, "B": 0.0}, targets, fills, 0.0)
    assert fee == 0.0
    assert cash == opening + trade_net - fee
    assert cash < 0.0
    marks = tuple(
        ResearchPositionMark(symbol, quantities[symbol], quantities[symbol] * fills[symbol])
        for symbol in ("A", "B")
    )
    nav = cash + sum(mark.valuation for mark in marks)
    day = ResearchLedgerDay(EXECUTE, cash, marks, trade_net, fee, nav, nav / opening - 1.0)
    accepted = ResearchDailyLedger(
        trial_id="fixture-hundred-million",
        domain="us_equity",
        strategy_profile="soxl_soxx_trend_income",
        run_id="fixture-hundred-million-run",
        param_version=1,
        input_id="fixture-hundred-million-input",
        calendar_id="fixture-calendar-v1",
        periods_per_year=252.0,
        cost_source="EXPLICIT_ZERO",
        cost_inputs={"commission_bps": 0.0},
        initial_session_date=SIGNAL,
        initial_nav=opening,
        initial_cash=opening,
        initial_positions=(),
        days=(day,),
        synthetic=True,
    )
    assert accepted.observation_count == 1
    overspent = dict(targets)
    overspent["A"] += 1.0
    with pytest.raises(OptimizedStrategyReplayError, match="CASH_INVALID"):
        _rebalance(opening, {"A": 0.0, "B": 0.0}, overspent, fills, 0.0)


def _ledger_day(cash: float, marks: tuple, trade_net: float, fee: float, nav: float, opening_nav: float):
    from quant_platform_kit.strategy_lifecycle.contracts import ResearchLedgerDay

    return ResearchLedgerDay(EXECUTE, cash, marks, trade_net, fee, nav, nav / opening_nav - 1.0)


def test_buy_and_sell_split_commission_cash_from_adverse_fill() -> None:
    from quant_platform_kit.strategy_lifecycle.contracts import (
        ResearchDailyLedger,
        ResearchPositionMark,
    )

    from us_equity_strategies.research.optimized_strategy_replay import _rebalance

    buy_cash, buy_qty, buy_fee, buy_flow = _rebalance(
        2_000.0, {"A": 0.0}, {"A": 800.0}, {"A": 80.0}, 0.25, 0.25
    )
    assert buy_qty["A"] == 10.0
    assert buy_fee == 200.0
    assert buy_flow == -1_000.0
    assert buy_cash == 800.0
    buy_nav = buy_cash + 800.0
    buy_mark = ResearchPositionMark("A", 10.0, 800.0)
    buy_ledger = ResearchDailyLedger(
        trial_id="fixture-cost-buy",
        domain="us_equity",
        strategy_profile="soxl_soxx_trend_income",
        run_id="fixture-cost-buy-run",
        param_version=1,
        input_id="fixture-cost-buy-input",
        calendar_id="fixture-calendar-v1",
        periods_per_year=252.0,
        cost_source="SYNTHETIC_BPS",
        cost_inputs={"commission_bps": 2_500.0, "slippage_bps": 2_500.0, "market_impact_bps": 0.0},
        initial_session_date=SIGNAL,
        initial_nav=2_000.0,
        initial_cash=2_000.0,
        initial_positions=(),
        days=(_ledger_day(buy_cash, (buy_mark,), buy_flow, buy_fee, buy_nav, 2_000.0),),
        synthetic=True,
    )
    assert buy_ledger.days[0].cash == 2_000.0 + buy_flow - buy_fee
    assert buy_ledger.days[0].fees == 200.0
    assert buy_ledger.total_fees == 200.0

    sell_cash, sell_qty, sell_fee, sell_flow = _rebalance(
        100.0, {"A": 10.0}, {"A": 0.0}, {"A": 80.0}, 0.25, 0.25
    )
    assert sell_qty["A"] == 0.0
    assert sell_fee == 200.0
    assert sell_flow == 600.0
    assert sell_cash == 500.0
    opening_nav = 900.0
    sell_nav = sell_cash
    sell_ledger = ResearchDailyLedger(
        trial_id="fixture-cost-sell",
        domain="us_equity",
        strategy_profile="soxl_soxx_trend_income",
        run_id="fixture-cost-sell-run",
        param_version=1,
        input_id="fixture-cost-sell-input",
        calendar_id="fixture-calendar-v1",
        periods_per_year=252.0,
        cost_source="SYNTHETIC_BPS",
        cost_inputs={"commission_bps": 2_500.0, "slippage_bps": 1_250.0, "market_impact_bps": 1_250.0},
        initial_session_date=SIGNAL,
        initial_nav=opening_nav,
        initial_cash=100.0,
        initial_positions=(ResearchPositionMark("A", 10.0, 800.0),),
        days=(_ledger_day(sell_cash, (), sell_flow, sell_fee, sell_nav, opening_nav),),
        synthetic=True,
    )
    assert sell_ledger.days[0].cash == 100.0 + sell_flow - sell_fee
    assert sell_ledger.days[0].positions == ()

    flat_cash, flat_qty, flat_fee, flat_flow = _rebalance(
        1_000.0, {"A": 0.0}, {"A": 500.0}, {"A": 50.0}, 0.0
    )
    assert flat_qty["A"] == 10.0
    assert flat_fee == 0.0
    assert flat_flow == -500.0
    assert flat_cash == 500.0


def test_bad_cost_inputs_and_unfunded_buys_are_rejected() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import _rebalance

    rejected = (
        (PromotionCostModel("NEG_COMMISSION", -1.0, 0.0, 0.0), "NONFINITE_INPUT"),
        (PromotionCostModel("NAN_SLIPPAGE", 0.0, float("nan"), 0.0), "NONFINITE_INPUT"),
        (PromotionCostModel("INF_IMPACT", 0.0, 0.0, float("inf")), "NONFINITE_INPUT"),
        (PromotionCostModel("FLAT_SELL", 0.0, 6_000.0, 4_000.0), "INVALID_FILL"),
    )
    for model, code in rejected:
        with pytest.raises(OptimizedStrategyReplayError, match=code):
            replay_optimized_strategy(_soxl_request(cost_model=model))
    with pytest.raises(OptimizedStrategyReplayError, match="INVALID_FILL"):
        _rebalance(1_000.0, {"A": 10.0}, {"A": 0.0}, {"A": 80.0}, 0.0, 1.0)
    with pytest.raises(OptimizedStrategyReplayError, match="CASH_INVALID"):
        _rebalance(900.0, {"A": 0.0}, {"A": 800.0}, {"A": 80.0}, 0.25, 0.25)
    with pytest.raises(OptimizedStrategyReplayError, match="CASH_INVALID"):
        replay_optimized_strategy(_soxl_request(cost_model=PromotionCostModel("SLIP500", 0.0, 500.0, 0.0)))
    with pytest.raises(OptimizedStrategyReplayError, match="CASH_INVALID"):
        replay_optimized_strategy(_soxl_request(cost_model=PromotionCostModel("COMM400", 400.0, 0.0, 0.0)))


def test_replay_buy_reads_commission_and_adverse_fill_back_from_ledger() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        persist_optimized_strategy_trial,
    )

    traded = 100_000.0 * 0.97
    commission = traded * 0.001
    adverse = traded * (5.0 + 5.0) / 10_000.0
    request = _soxl_request(cost_model=PromotionCostModel("HAND_SPLIT", 10.0, 5.0, 5.0))
    replay = replay_optimized_strategy(request)
    point = replay.points[1]
    assert point.fees == pytest.approx(commission)
    assert point.trade_net_cashflow == pytest.approx(-(traded + adverse))
    assert point.cash == pytest.approx(100_000.0 + point.trade_net_cashflow - point.fees)
    assert point.nav == pytest.approx(point.cash + traded)
    assert point.nav == pytest.approx(100_000.0 - commission - adverse)
    holdings = dict(point.holdings)
    assert holdings["SOXL"] == pytest.approx((traded * 0.70) / 50.0)
    assert holdings["SOXX"] == pytest.approx((traded * 0.20) / 100.0)
    assert holdings["BOXX"] == pytest.approx((traded * 0.10) / 100.0)
    assert replay.backtest.cost_model == "HAND_SPLIT"
    assert dict(replay.backtest.cost_inputs) == {
        "commission_bps": 10.0,
        "slippage_bps": 5.0,
        "market_impact_bps": 5.0,
    }
    assert "SYNTHETIC_BPS_NOT_LIVE_FEES" in replay.gaps
    with tempfile.TemporaryDirectory() as tmp:
        store = PerformanceStore(local_root=Path(tmp), cloud_bucket="")
        persist_optimized_strategy_trial(request, store, trial_id="fixture-soxl-cost-split")
        loaded = store.load_research_ledger(
            "us_equity",
            "soxl_soxx_trend_income",
            "fixture-soxl-cost-split",
            replay.backtest.run_id,
            replay.backtest.param_version,
        )
    assert loaded is not None
    assert loaded.days[0].cash == point.cash
    assert loaded.days[0].fees == point.fees
    assert loaded.days[0].trade_net_cashflow == point.trade_net_cashflow
    assert loaded.days[0].nav == point.nav
    assert loaded.days[0].daily_return == point.daily_return
    assert dict(loaded.cost_inputs) == dict(replay.backtest.cost_inputs)
    assert loaded.days[0].cash == pytest.approx(loaded.initial_cash + loaded.days[0].trade_net_cashflow - loaded.days[0].fees)
