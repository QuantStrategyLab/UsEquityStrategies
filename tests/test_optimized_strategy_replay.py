"""Synthetic checks for the research-only pre-risk-gate replay."""

from __future__ import annotations

import hashlib
import json
import math
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


def test_whole_share_execution_sells_first_and_never_spends_unavailable_cash() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import _rebalance

    cash, quantities, fees, trade_cashflow = _rebalance(
        0.2, {"AAA": 1.0, "BBB": 0.0},
        {"AAA": 0.0, "BBB": 100.0}, {"AAA": 100.0, "BBB": 100.0},
        0.001, whole_shares=True,
    )
    assert quantities == {"AAA": 0.0, "BBB": 1.0}
    assert cash == pytest.approx(0.0)
    assert fees == pytest.approx(0.2)
    assert trade_cashflow == pytest.approx(0.0)


def test_whole_share_execution_rejects_unfunded_plan_without_symbol_priority() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import _rebalance

    for first, second in (("AAA", "ZZZ"), ("ZZZ", "AAA")):
        with pytest.raises(OptimizedStrategyReplayError, match="PLAN_UNFUNDED"):
            _rebalance(
                100.0, {first: 0.0, second: 0.0},
                {first: 50.0, second: 50.0}, {first: 50.0, second: 50.0},
                0.01, whole_shares=True,
            )


@pytest.mark.parametrize("profile", ("SOXL", "TQQQ"))
def test_unheld_inactive_income_assets_do_not_require_daily_prices(profile: str) -> None:
    request = (_soxl_market_regime_request(("no_action", "no_action"))
               if profile == "SOXL" else _tqqq_market_regime_request(("no_action", "no_action")))
    inactive = {"SCHD", "DGRO", "SGOV", "SPYI", "QQQI"}
    baseline = replay_optimized_strategy(request)
    sparse = replace(request, prices=tuple(bar for bar in request.prices if bar.symbol not in inactive))
    actual = replay_optimized_strategy(sparse)
    assert [point.nav for point in actual.points] == pytest.approx([point.nav for point in baseline.points])
    assert actual.decision_targets == baseline.decision_targets
    assert all(dict(point.holdings)[symbol] == 0 for point in actual.points for symbol in inactive)


def test_missing_price_for_held_income_asset_still_fails() -> None:
    request = _soxl_market_regime_request(("no_action", "no_action"))
    held = dict(request.initial_quantities)
    held["SCHD"] = 1.0
    request = replace(request, initial_quantities=held,
                      prices=tuple(bar for bar in request.prices if bar.symbol != "SCHD"))
    with pytest.raises(OptimizedStrategyReplayError, match="INPUT_GAP"):
        replay_optimized_strategy(request)


def test_missing_price_for_newly_targeted_asset_still_fails() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import _rebalance

    with pytest.raises(OptimizedStrategyReplayError, match="INPUT_GAP"):
        _rebalance(100.0, {"SCHD": 0.0}, {"SCHD": 50.0}, {}, 0.0, whole_shares=True)


def _v2_identity_from_v1(
    identity: dict[str, object], *, ues_patch: str = "c" * 64, qpk_patch: str = "d" * 64
) -> dict[str, object]:
    from us_equity_strategies.research.optimized_member_identity import build_optimized_member_identity_v2

    contracts = identity["declared_contracts"]
    execution = identity["execution"]
    return build_optimized_member_identity_v2(
        strategy_profile=identity["strategy_profile"],
        ues_revision=identity["ues_revision"],
        qpk_revision=identity["qpk_revision"],
        ues_workspace_patch_sha256=ues_patch,
        qpk_workspace_patch_sha256=qpk_patch,
        param_set_id=identity["param_set_id"],
        actual_params=identity["actual_params"],
        config_sha256=identity["config_sha256"],
        input_sha256=identity["input_sha256"],
        window_start=identity["window_start"],
        window_end=identity["window_end"],
        calendar_id=identity["calendar_id"],
        periods_per_year=identity["periods_per_year"],
        cost_source=identity["cost_source"],
        cost_inputs=identity["cost_inputs"],
        fill_price_field=execution["fill_price_field"],
        adjustment_contract=contracts["adjustment"],
        cash_contract=contracts["cash"],
        corporate_action_contract=contracts["corporate_action"],
        external_cashflow_contract=contracts["external_cashflow"],
        share_quantity_contract=contracts["share_quantity"],
    )


def _tqqq_option_quote(
    contract_id: str, *, strike: float, bid: float, ask: float, delta: float = 0.75
) -> dict[str, object]:
    return {
        "contract_id": contract_id,
        "underlying": "TQQQ",
        "right": "call",
        "strike": strike,
        "expiration": "2026-01-02",
        "multiplier": 100.0,
        "bid": bid,
        "ask": ask,
        "delta": delta,
        "source_id": f"synthetic-option-quote:{contract_id}",
        "available_at": "2024-01-02T20:59:00Z",
    }


def _tqqq_option_position(
    contract_id: str,
    campaign_id: str,
    lot_index: int,
    *,
    cost_basis: float = 3000.0,
    campaign_basis: float = 9000.0,
    recovered_proceeds: float = 0.0,
) -> dict[str, object]:
    return {
        "ledger_position_id": f"{campaign_id}-U{lot_index:04d}",
        "contract_id": contract_id,
        "campaign_id": campaign_id,
        "lot_index": lot_index,
        "cost_basis_per_contract": cost_basis,
        "campaign_basis": campaign_basis,
        "recovered_proceeds": recovered_proceeds,
    }


def _tqqq_option_fixture_file(
    path: Path,
    *,
    initial_cash: float = 300_000.0,
    positions_by_day: tuple[tuple[dict[str, object], ...], ...] | None = None,
    quotes_by_day: tuple[tuple[dict[str, object], ...], ...] | None = None,
) -> dict[str, object]:
    base = _tqqq_market_regime_request(("risk_on", "risk_on"))
    sessions = base.calendar
    config = {key: value for key, value in base.runtime_config.items()
              if key not in {"translator", "signal_text_fn"}}
    config.update(
        option_overlay_enabled=True,
        option_growth_overlay_enabled=True,
        option_growth_overlay_recipe="tqqq_leaps_growth_v1",
        option_growth_overlay_start_usd=250_000.0,
        option_growth_overlay_nav_budget_ratio=0.03,
        option_income_overlay_enabled=False,
    )
    if quotes_by_day is None:
        candidate = _tqqq_option_quote("TQQQ-2026-01-02-C-100", strike=100.0, bid=30.0, ask=30.0)
        quotes_by_day = tuple((candidate,) for _ in sessions)
    if positions_by_day is None:
        initial_quotes = quotes_by_day[0]
        ranked = []
        for quote in initial_quotes:
            dte = (date.fromisoformat(quote["expiration"]) - sessions[0]).days
            mid = (quote["bid"] + quote["ask"]) / 2.0
            spread = (quote["ask"] - quote["bid"]) / mid if quote["bid"] > 0.0 else 0.0
            if quote["right"] == "call" and 540 <= dte <= 930 and quote["delta"] > 0.0 and spread <= 0.12:
                ranked.append((abs(dte - int(24 * 30.4375)), abs(quote["delta"] - 0.75), quote["strike"], quote))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        if ranked:
            candidate = ranked[0][3]
            mid = (candidate["bid"] + candidate["ask"]) / 2.0
            unit_cost = round(min(candidate["ask"], mid * 1.03), 2) * candidate["multiplier"]
            count = math.floor(initial_cash * 0.03 / unit_cost)
            contract_id = candidate["contract_id"]
            open_session = sessions[1]
            campaign_id = f"tqqq-leaps-{open_session.isoformat()}"
            import hashlib
            positions_by_day = ((), (), tuple({
                "ledger_position_id": "OPT-" + hashlib.sha256(f"{contract_id}|{campaign_id}|{lot}".encode()).hexdigest()[:28],
                "contract_id": contract_id, "campaign_id": campaign_id, "lot_index": lot,
                "cost_basis_per_contract": unit_cost, "campaign_basis": unit_cost * count,
                "recovered_proceeds": 0.0,
            } for lot in range(1, count + 1)))
        else:
            positions_by_day = tuple(() for _ in sessions)
    source = {
        "runtime_config": config,
        "calendar": [session.isoformat() for session in sessions],
        "initial_cash": initial_cash,
        "initial_quantities": dict(base.initial_quantities),
        "prices": [
            {"session": bar.session.isoformat(), "symbol": bar.symbol, "open": bar.open,
             "high": bar.high, "low": bar.low, "close": bar.close,
             "source_id": f"synthetic-price:{bar.symbol}:{bar.session.isoformat()}",
             "available_at": f"{bar.session.isoformat()}T20:00:00Z"}
            for bar in base.prices
        ],
        "computed_at": base.computed_at,
        "derived_indicators": None,
        "indicator_sources": None,
        "benchmark_bars": [
            {"session": bar.session.isoformat(), "symbol": bar.symbol, "open": bar.open,
             "high": bar.high, "low": bar.low, "close": bar.close,
             "source_id": f"synthetic-benchmark:{bar.symbol}:{bar.session.isoformat()}",
             "available_at": f"{bar.session.isoformat()}T20:00:00Z"}
            for bar in base.benchmark_bars
        ],
        "state_inputs": list(base.state_inputs),
        "option_market_inputs": [],
    }
    for session, positions, quotes in zip(sessions, positions_by_day, quotes_by_day, strict=True):
        state_row = next((row for row in base.state_inputs if row["signal_date"] == session.isoformat()), None)
        decision_at = state_row["decision_at"] if state_row is not None else f"{session.isoformat()}T21:00:00Z"
        qqq_available_at = state_row["decision_at"].replace("21:00:00", "20:59:00") if state_row else f"{session.isoformat()}T20:59:00Z"
        row_quotes = []
        for quote in quotes:
            row_quote = dict(quote)
            row_quote["available_at"] = qqq_available_at
            row_quotes.append(row_quote)
        source["option_market_inputs"].append({
            "session": session.isoformat(),
            "decision_at": decision_at,
            "qqq_indicator": {
                "above_200dma": True,
                "momentum_63d": 0.05,
                "source_id": f"synthetic-qqq-indicator:{session.isoformat()}",
                "available_at": qqq_available_at,
            },
            "positions_source": {
                "source_id": f"synthetic-option-position-state:{session.isoformat()}",
                "available_at": qqq_available_at,
            },
            "positions": list(positions),
            "quotes": row_quotes,
        })
    identity = _build_tqqq_v2_identity(source)
    payload = {"identity": identity, "input": source}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _build_tqqq_v2_identity(
    source: dict[str, object],
    *,
    cost_source: str = "SYNTHETIC_ZERO",
    cost_inputs: dict[str, float] | None = None,
    param_set_id: str = "synthetic-tqqq-leaps-slice-v1",
    share_quantity_contract: str = "synthetic fractional equity units and integer option lots",
) -> dict[str, object]:
    from us_equity_strategies.research.optimized_member_identity import build_optimized_member_identity_v2

    config = source["runtime_config"]
    config_sha = hashlib.sha256(_canonical(config)).hexdigest()
    input_sha = hashlib.sha256(_canonical(source)).hexdigest()
    return build_optimized_member_identity_v2(
        strategy_profile="tqqq_growth_income",
        ues_revision="a" * 40,
        qpk_revision="b" * 40,
        ues_workspace_patch_sha256="c" * 64,
        qpk_workspace_patch_sha256="d" * 64,
        param_set_id=param_set_id,
        actual_params=config,
        config_sha256=config_sha,
        input_sha256=input_sha,
        window_start=source["calendar"][0],
        window_end=source["calendar"][-1],
        calendar_id="synthetic-calendar",
        periods_per_year=252,
        cost_source=cost_source,
        cost_inputs=cost_inputs or {"commission_bps": 0, "slippage_bps": 0, "market_impact_bps": 0},
        fill_price_field="close",
        adjustment_contract="synthetic unadjusted prices",
        cash_contract="synthetic zero interest",
        corporate_action_contract="synthetic no corporate actions",
        external_cashflow_contract="synthetic no external flows",
        share_quantity_contract=share_quantity_contract,
    )


def _build_soxl_option_v2_identity(
    source: dict[str, object], *, fill_price_field: str = "close",
    param_set_id: str = "synthetic-soxx-put-credit-slice-v1",
    share_quantity_contract: str = "synthetic fractional units and integer option contracts",
) -> dict[str, object]:
    from us_equity_strategies.research.optimized_member_identity import build_optimized_member_identity_v2

    config = source["runtime_config"]
    return build_optimized_member_identity_v2(
        strategy_profile="soxl_soxx_trend_income", ues_revision="a" * 40,
        qpk_revision="b" * 40, ues_workspace_patch_sha256="c" * 64,
        qpk_workspace_patch_sha256="d" * 64, param_set_id=param_set_id,
        actual_params=config, config_sha256=hashlib.sha256(_canonical(config)).hexdigest(),
        input_sha256=hashlib.sha256(_canonical(source)).hexdigest(),
        window_start=source["calendar"][0], window_end=source["calendar"][-1],
        calendar_id="synthetic-calendar", periods_per_year=252,
        cost_source="SYNTHETIC_10BPS",
        cost_inputs={"commission_bps": 10, "slippage_bps": 0, "market_impact_bps": 0},
        fill_price_field=fill_price_field, adjustment_contract="synthetic unadjusted prices",
        cash_contract="synthetic zero interest", corporate_action_contract="synthetic no events",
        external_cashflow_contract="synthetic no external flows",
        share_quantity_contract=share_quantity_contract,
    )


def _local_fixture_file(
    path: Path,
    *,
    state_inputs: list[dict[str, object]] | None = None,
    income_cashflow: dict[str, float] | None = None,
    external_cashflow: dict[str, float] | None = None,
    ledger_events: dict[str, dict[str, object]] | None = None,
    corporate_action_contract: str = "synthetic no events",
    external_cashflow_contract: str = "synthetic no external flows",
) -> dict[str, object]:
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
             "high": bar.high, "low": bar.low, "close": bar.close,
             "source_id": bar.source_id, "available_at": bar.available_at}
            for bar in request.prices
        ],
        "computed_at": request.computed_at,
        "derived_indicators": {day.isoformat(): values for day, values in request.derived_indicators.items()},
        "indicator_sources": {day.isoformat(): values for day, values in request.indicator_sources.items()},
        "benchmark_bars": [],
    }
    if state_inputs is not None:
        source["state_inputs"] = state_inputs
    if income_cashflow is not None:
        source["income_cashflow"] = income_cashflow
    if external_cashflow is not None:
        source["external_cashflow"] = external_cashflow
    if ledger_events is not None:
        source["ledger_events"] = ledger_events
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
        cash_contract="synthetic zero interest", corporate_action_contract=corporate_action_contract,
        external_cashflow_contract=external_cashflow_contract, share_quantity_contract="synthetic fractional units",
    )
    payload = {"identity": identity, "input": source}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _causal_state_inputs(
    sessions: tuple[date, ...] = (SIGNAL, EXECUTE),
    *,
    state: dict[str, object] | None = None,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "signal_date": session.isoformat(),
            "decision_at": f"{session.isoformat()}T21:00:00Z",
            "available_at": f"{session.isoformat()}T20:59:00Z",
            "state": {"fixture_state": "known", **(state or {})},
            "value_sources": {
                key: {"source_id": f"fixture-state:{key}", "available_at": f"{session.isoformat()}T20:59:00Z"}
                for key in {"fixture_state", *(state or {}).keys()}
            },
        }
        for session in sessions[:-1]
    )


def _market_regime_artifact(
    signal_date: date,
    *,
    route: str = "no_action",
    approved: bool = True,
    include_retention_context: bool = True,
) -> dict[str, object]:
    risk_off = route == "risk_off"
    position_control: dict[str, object] = {
        "final_route": route,
        "route_source": "synthetic_fixture",
        "suggested_action": "defend" if risk_off else "watch_only",
        "risk_budget_scalar": 0.0 if risk_off else 1.0,
        "leverage_scalar": 0.0 if risk_off else 1.0,
        "risk_asset_scalar": 0.0 if risk_off else 1.0,
        "crisis_defense_required": risk_off,
        "reason_codes": [f"fixture:{route}"],
    }
    if include_retention_context:
        position_control["volatility_delever_context"] = {
            "actionable_for_position_control": True,
            "hard_risk": False,
            "soft_risk": False,
            "constructive": True,
            "rebound_confirm": True,
        }
    return {
        "plugin": "market_regime_control",
        "schema_version": "market_regime_control.v1",
        "as_of": signal_date.isoformat(),
        "candidate_id": "soxl-fixture-market-regime-v1",
        "execution_controls": {
            "position_control_allowed": True,
            "consumption_evidence_status": (
                "research_backtest_approved" if approved else "notification_only"
            ),
        },
        "position_control": position_control,
    }


def _soxl_market_regime_request(
    routes: tuple[str, ...],
    *,
    include_retention_context: bool = True,
) -> ReplayRequest:
    sessions = (SIGNAL, EXECUTE, date(2024, 1, 4))
    base = _soxl_request()
    config = deepcopy(dict(base.runtime_config))
    config.update(
        market_regime_control_enabled=True,
        market_regime_control_apply_risk_off=True,
        blend_gate_volatility_delever_enabled=True,
        blend_gate_volatility_delever_threshold=0.50,
        blend_gate_volatility_delever_retention_mode="environment",
    )
    indicators = deepcopy(base.derived_indicators)
    assert indicators is not None
    for session in sessions[:-1]:
        indicators[session] = deepcopy(indicators[SIGNAL])
        indicators[session]["soxx"]["realized_volatility_10"] = 0.80
        indicators[session]["soxx"]["realized_volatility_10_dynamic_threshold"] = 0.50
    states = tuple(
        {
            "signal_date": session.isoformat(),
            "decision_at": f"{session.isoformat()}T21:00:00Z",
            "available_at": f"{session.isoformat()}T20:59:00Z",
            "state": {
                "market_regime_control": _market_regime_artifact(
                    session,
                    route=route,
                    include_retention_context=include_retention_context,
                )
            },
            "value_sources": {
                "market_regime_control": {
                    "source_id": f"synthetic-market-regime:{session.isoformat()}",
                    "available_at": f"{session.isoformat()}T20:59:00Z",
                }
            },
        }
        for session, route in zip(sessions[:-1], routes, strict=True)
    )
    prices = {symbol: 100.0 for symbol in SOXL_SYMBOLS}
    prices["SOXL"] = 50.0
    return replace(
        base,
        runtime_config=config,
        calendar=sessions,
        prices=_bars(sessions, SOXL_SYMBOLS, prices),
        derived_indicators=indicators,
        state_inputs=states,
    )


def _tqqq_market_regime_artifact(signal_date: date, *, route: str) -> dict[str, object]:
    risk_off = route == "risk_off"
    return {
        "plugin": "market_regime_control",
        "schema_version": "market_regime_control.v1",
        "as_of": signal_date.isoformat(),
        "candidate_id": "tqqq-fixture-market-regime-v1",
        "execution_controls": {
            "position_control_allowed": True,
            "consumption_evidence_status": "research_backtest_approved",
        },
        "position_control": {
            "final_route": route,
            "route_source": "synthetic_fixture",
            "suggested_action": "defend" if risk_off else "watch_only",
            "risk_budget_scalar": 0.0 if risk_off else 1.0,
            "leverage_scalar": 0.0 if risk_off else 1.0,
            "risk_asset_scalar": 0.0 if risk_off else 1.0,
            "taco_allowed": False,
            "local_delever_veto_allowed": False,
            "crisis_defense_required": risk_off,
            "reason_codes": [f"fixture:{route}"],
            "volatility_delever_context": {
                "actionable_for_position_control": True,
                "hard_risk": False,
                "soft_risk": False,
                "constructive": True,
                "rebound_confirm": True,
            },
        },
    }


def _tqqq_market_regime_request(routes: tuple[str, ...]) -> ReplayRequest:
    sessions = (SIGNAL, EXECUTE, date(2024, 1, 4))
    config = _config(tqqq_growth_income_manifest)
    config.update(
        market_regime_control_enabled=True,
        dual_drive_macro_risk_governor_enabled=True,
        dual_drive_crisis_defense_enabled=True,
        dual_drive_volatility_delever_taco_veto_enabled=True,
        dual_drive_volatility_delever_retention_mode="environment",
        dual_drive_volatility_delever_threshold_mode="fixed",
        dual_drive_volatility_delever_threshold=0.05,
        dual_drive_volatility_delever_exit_threshold=0.05,
    )
    history_sessions = _business_days_ending(SIGNAL, 202)
    tail_returns = (0.0, 0.03, -0.05, 0.04, -0.06, 0.05, -0.04, 0.06, -0.05, 0.07, -0.04, 0.08)
    close_values = [100.0 + index * 0.20 for index in range(len(history_sessions) - len(tail_returns))]
    anchor = close_values[-1]
    close_values.extend(anchor * (1.0 + change) for change in tail_returns)
    benchmark = tuple(
        DatedBar(session, "QQQ", close, close, close, close)
        for session, close in zip(history_sessions, close_values, strict=True)
    ) + tuple(
        DatedBar(session, "QQQ", close_values[-1], close_values[-1], close_values[-1], close_values[-1])
        for session in sessions[1:]
    )
    states = tuple(
        {
            "signal_date": session.isoformat(),
            "decision_at": f"{session.isoformat()}T21:00:00Z",
            "available_at": f"{session.isoformat()}T20:59:00Z",
            "state": {"market_regime_control": _tqqq_market_regime_artifact(session, route=route)},
            "value_sources": {
                "market_regime_control": {
                    "source_id": f"synthetic-market-regime:{session.isoformat()}",
                    "available_at": f"{session.isoformat()}T20:59:00Z",
                }
            },
        }
        for session, route in zip(sessions[:-1], routes, strict=True)
    )
    symbols = tuple(config["managed_symbols"])
    prices = _bars(sessions, symbols, {symbol: 100.0 for symbol in symbols})
    return ReplayRequest(
        identity=replace(_identity("tqqq_growth_income"), param_set_id="synthetic-tqqq-plugin-v1"),
        runtime_config=config,
        calendar=sessions,
        initial_cash=100_000.0,
        initial_quantities={symbol: 0.0 for symbol in symbols},
        prices=prices,
        execution=ExecutionAssumptions(1, "next_trading_day", "close", "close"),
        cost_model=PromotionCostModel("SYNTHETIC_ZERO", 0.0, 0.0, 0.0),
        computed_at="2024-01-03T21:00:00Z",
        evidence_use="fixture",
        promotion_eligible=False,
        calendar_id="fixture-calendar-v1",
        periods_per_year=252.0,
        benchmark_bars=benchmark,
        state_inputs=states,
    )


def test_local_file_produces_only_synthetic_ledger(tmp_path: Path) -> None:
    path = tmp_path / "member.json"
    payload = _local_fixture_file(path)
    identity, request = load_local_member_fixture(path)
    assert identity == payload["identity"]
    assert request.promotion_eligible is False
    store = PerformanceStore(local_root=tmp_path / "store", cloud_bucket="")
    _, record = produce_local_member_fixture(path, store, trial_id="local-synthetic")
    assert record.synthetic is True
    loaded = store.load_research_trial("us_equity", "soxl_soxx_trend_income", "local-synthetic")
    assert loaded is not None and loaded.research_identity == payload["identity"]
    assert record.research_identity == loaded.research_identity
    assert record.actual_params is not None and "research_identity" not in record.actual_params
    stored_result = store.load_backtest_by_run_id(
        "us_equity", "soxl_soxx_trend_income", record.run_id,
        param_version=record.param_version,
    )
    assert stored_result is not None
    assert stored_result.params["research_identity"] == payload["identity"]
    ledger = store.load_research_ledger("us_equity", "soxl_soxx_trend_income", "local-synthetic", record.run_id, record.param_version)
    assert ledger is not None and ledger.synthetic is True
    previous_cash, previous_nav = ledger.initial_cash, ledger.initial_nav
    for day in ledger.days:
        assert day.income_cashflow == 0.0
        assert day.nav == pytest.approx(day.cash + sum(position.valuation for position in day.positions))
        assert day.cash == pytest.approx(
            previous_cash + day.trade_net_cashflow - day.fees + day.income_cashflow
        )
        assert day.daily_return == pytest.approx(day.nav / previous_nav - 1.0)
        previous_cash, previous_nav = day.cash, day.nav


def test_full_member_identity_changes_run_id_and_conflicting_trial_is_rejected(tmp_path: Path) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        persist_optimized_strategy_trial,
    )

    path_a = tmp_path / "member-a.json"
    path_b = tmp_path / "member-b.json"
    path_contract = tmp_path / "member-contract.json"
    path_input = tmp_path / "member-input.json"
    payload_a = _local_fixture_file(path_a)
    payload_b = _local_fixture_file(path_b)
    payload_contract = _local_fixture_file(path_contract)
    payload_input = _local_fixture_file(path_input)
    payload_b["identity"]["qpk_revision"] = "c" * 40
    payload_b["identity"]["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(
        payload_b["identity"]
    )
    path_b.write_text(json.dumps(payload_b), encoding="utf-8")
    payload_contract["identity"]["declared_contracts"]["cash"] = "synthetic cash earns Unicode 零 interest"
    payload_contract["identity"]["economic_identity_sha256"] = calculate_optimized_member_identity_sha256(
        payload_contract["identity"]
    )
    path_contract.write_text(json.dumps(payload_contract), encoding="utf-8")
    payload_input["input"]["initial_cash"] += 1.0
    _rebind_input_digest(payload_input)
    path_input.write_text(json.dumps(payload_input), encoding="utf-8")

    _, request_a = load_local_member_fixture(path_a)
    _, request_b = load_local_member_fixture(path_b)
    _, request_contract = load_local_member_fixture(path_contract)
    _, request_input = load_local_member_fixture(path_input)
    replay_a = replay_optimized_strategy(request_a)
    replay_b = replay_optimized_strategy(request_b)
    replay_contract = replay_optimized_strategy(request_contract)
    replay_input = replay_optimized_strategy(request_input)
    identities = [
        replay.backtest.params["research_identity"]
        for replay in (replay_a, replay_b, replay_contract, replay_input)
    ]
    run_ids = [replay.backtest.run_id for replay in (replay_a, replay_b, replay_contract, replay_input)]
    assert len(set(run_ids)) == 4
    assert all(identity != identities[0] for identity in identities[1:])

    store = PerformanceStore(local_root=tmp_path / "store", cloud_bucket="")
    persist_optimized_strategy_trial(request_a, store, trial_id="identity-bound")
    with pytest.raises(ValueError, match="research_trial"):
        persist_optimized_strategy_trial(request_b, store, trial_id="identity-bound")


def test_local_identity_mismatch_is_rejected_before_trial_start(tmp_path: Path) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        persist_optimized_strategy_trial,
    )

    path = tmp_path / "member.json"
    _local_fixture_file(path)
    _, request = load_local_member_fixture(path)
    changed = replace(request, calendar_id="different-calendar")
    store = PerformanceStore(local_root=tmp_path / "store", cloud_bucket="")
    with pytest.raises(OptimizedStrategyReplayError, match="RESEARCH_IDENTITY_MISMATCH"):
        persist_optimized_strategy_trial(changed, store, trial_id="identity-mismatch")
    assert store.load_research_trial("us_equity", "soxl_soxx_trend_income", "identity-mismatch") is None


def test_causal_state_inputs_reject_future_missing_and_duplicate_rows() -> None:
    valid = _causal_state_inputs()[0]
    future = dict(valid)
    future["available_at"] = "2024-01-02T21:00:01Z"
    with pytest.raises(OptimizedStrategyReplayError, match="STATE_NOT_AVAILABLE_AT_DECISION"):
        replay_optimized_strategy(_soxl_request(state_inputs=(future,)))
    with pytest.raises(OptimizedStrategyReplayError, match="STATE_INPUT_GAP"):
        replay_optimized_strategy(_soxl_request(state_inputs=()))
    with pytest.raises(OptimizedStrategyReplayError, match="DUPLICATE_FIELD:state_inputs.signal_date"):
        replay_optimized_strategy(_soxl_request(state_inputs=(valid, valid)))


def test_state_value_provenance_must_be_available_by_decision() -> None:
    valid = dict(_causal_state_inputs()[0])
    valid["value_sources"] = {
        "fixture_state": {"source_id": "fixture-state:fixture_state", "available_at": "2024-01-02T21:00:01Z"}
    }
    with pytest.raises(OptimizedStrategyReplayError, match="INPUT_NOT_AVAILABLE_AT_DECISION:state"):
        replay_optimized_strategy(_soxl_request(state_inputs=(valid,)))


def test_member_bar_available_after_earlier_daily_decision_is_rejected() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        _validate_member_input_provenance,
    )

    state = dict(_causal_state_inputs()[0])
    state["decision_at"] = "2024-01-02T20:00:00Z"
    state["available_at"] = "2024-01-02T19:59:00Z"
    state["value_sources"] = {
        "fixture_state": {
            "source_id": "fixture-state:fixture_state",
            "available_at": "2024-01-02T19:59:00Z",
        }
    }
    request = _soxl_request(state_inputs=(state,), research_identity={})
    bars = list(request.prices)
    late_index = next(index for index, bar in enumerate(bars) if bar.session == SIGNAL)
    bars[late_index] = replace(bars[late_index], available_at="2024-01-02T20:30:00Z")
    request = replace(request, prices=tuple(bars))

    with pytest.raises(OptimizedStrategyReplayError, match="INPUT_NOT_AVAILABLE_AT_DECISION:bar"):
        _validate_member_input_provenance(request, request.calendar)


def test_member_benchmark_warmup_bar_must_be_known_by_first_decision() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        _validate_member_input_provenance,
    )

    request = _soxl_request(research_identity={})
    request = replace(
        request,
        benchmark_bars=(DatedBar(
            date(2023, 12, 29), "QQQ", 100.0, 100.0, 100.0, 100.0,
            "synthetic:QQQ:warmup", "2099-01-01T00:00:00Z",
        ),),
    )
    with pytest.raises(OptimizedStrategyReplayError, match="INPUT_NOT_AVAILABLE_AT_DECISION:bar"):
        _validate_member_input_provenance(request, request.calendar)


@pytest.mark.parametrize(
    ("metric", "value", "source_symbol", "expected_error"),
    (
        ("price", 1_000_000_000.0, "SOXL", "INDICATOR_RECOMPUTATION_MISMATCH"),
        ("price", 50.0, "SOXX", "INDICATOR_RECOMPUTATION_MISMATCH"),
        ("ma_trend", 1_000_000_000.0, "SOXL", "INDICATOR_RECOMPUTATION_UNSUPPORTED"),
    ),
)
def test_member_deterministic_indicator_must_have_supported_recomputation(
    metric: str, value: float, source_symbol: str, expected_error: str,
) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        _validate_member_input_provenance,
    )

    request = _soxl_request(research_identity={})
    indicators = deepcopy(request.derived_indicators)
    indicators[SIGNAL]["soxl"][metric] = value
    sources = deepcopy(request.indicator_sources)
    bar = next(bar for bar in request.prices if bar.session == SIGNAL and bar.symbol == source_symbol)
    sources[SIGNAL]["soxl"][metric] = {
        "source_id": f"deterministic:{metric}:{source_symbol}",
        "available_at": f"{SIGNAL.isoformat()}T20:59:00Z",
        "derivation": "deterministic_from_known_bars.v1",
        "source_bars": [{
            "session": SIGNAL.isoformat(),
            "symbol": source_symbol,
            "source_id": bar.source_id,
            "available_at": bar.available_at,
        }],
    }
    request = replace(request, derived_indicators=indicators, indicator_sources=sources)

    with pytest.raises(OptimizedStrategyReplayError, match=expected_error):
        _validate_member_input_provenance(request, request.calendar)


def test_state_inputs_bind_local_identity_and_change_run_id(tmp_path: Path) -> None:
    path_a = tmp_path / "state-a.json"
    path_b = tmp_path / "state-b.json"
    states_a = list(_causal_state_inputs(state={"route": "risk_on"}))
    states_b = list(_causal_state_inputs(state={"route": "risk_off"}))
    _local_fixture_file(path_a, state_inputs=states_a)
    _local_fixture_file(path_b, state_inputs=states_b)
    _, request_a = load_local_member_fixture(path_a)
    _, request_b = load_local_member_fixture(path_b)
    replay_a = replay_optimized_strategy(request_a)
    replay_b = replay_optimized_strategy(request_b)
    assert replay_a.backtest.run_id != replay_b.backtest.run_id

    tampered = replace(request_a, state_inputs=request_b.state_inputs)
    with pytest.raises(OptimizedStrategyReplayError, match="RESEARCH_IDENTITY_MISMATCH"):
        replay_optimized_strategy(tampered)


def test_internal_income_cashflow_is_counted_once_and_persisted(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from us_equity_strategies.research import optimized_strategy_replay as replay_module
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "income-member.json"
    payload = _local_fixture_file(path, income_cashflow={EXECUTE.isoformat(): 5.0})
    payload["input"]["initial_cash"] = 100.0
    payload["input"]["initial_quantities"]["SOXL"] = 1.0
    for bar in payload["input"]["prices"]:
        price = 100.0 if bar["session"] == SIGNAL.isoformat() else 110.0
        bar.update(open=price, high=price, low=price, close=price)
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)

    def hold_soxl(context):
        return SimpleNamespace(
            positions=tuple(
                SimpleNamespace(symbol=symbol, target_value=110.0 if symbol == "SOXL" else 0.0)
                for symbol in SOXL_SYMBOLS
            ),
            risk_flags=(),
            diagnostics={
                "signal_date": SIGNAL.isoformat(),
                "effective_date": EXECUTE.isoformat(),
                "execution_timing_contract": "next_trading_day",
                "signal_effective_after_trading_days": 1,
            },
        )

    manifest, _ = replay_module._PROFILES["soxl_soxx_trend_income"]
    monkeypatch.setitem(replay_module._PROFILES, "soxl_soxx_trend_income", (manifest, hold_soxl))
    replay = replay_optimized_strategy(request)
    assert replay.points[0].nav == pytest.approx(200.0)
    assert replay.points[1].cash == pytest.approx(105.0)
    assert replay.points[1].nav == pytest.approx(215.0)
    assert replay.points[1].daily_return == pytest.approx(0.075)
    assert replay.points[1].income_cashflow == pytest.approx(5.0)

    store = PerformanceStore(local_root=tmp_path / "income-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="income-once")
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", "income-once", record.run_id, record.param_version
    )
    assert ledger is not None and ledger.days[0].income_cashflow == pytest.approx(5.0)
    assert ledger.days[0].cash == pytest.approx(105.0)
    assert ledger.days[0].nav == pytest.approx(215.0)

    tampered = replace(request, income_cashflow={EXECUTE: 6.0})
    with pytest.raises(OptimizedStrategyReplayError, match="RESEARCH_IDENTITY_MISMATCH"):
        replay_optimized_strategy(tampered)
    changed_income = replace(request, research_identity=None, income_cashflow={EXECUTE: 6.0})
    changed_replay = replay_optimized_strategy(changed_income)
    assert changed_replay.backtest.run_id != replay.backtest.run_id
    assert replay_module._input_id(changed_income) != replay_module._input_id(
        replace(request, research_identity=None)
    )


@pytest.mark.parametrize(
    ("income_cashflow", "message"),
    [
        ({"2024-1-03": 1.0}, "INVALID_FIELD:income_cashflow.date"),
        ({EXECUTE: float("inf")}, "NONFINITE_INPUT"),
    ],
)
def test_invalid_internal_income_cashflow_is_rejected(income_cashflow, message) -> None:
    with pytest.raises(OptimizedStrategyReplayError, match=message):
        replay_optimized_strategy(_soxl_request(income_cashflow=income_cashflow))


def test_negative_internal_income_cannot_create_overdraft() -> None:
    with pytest.raises(OptimizedStrategyReplayError, match="CASH_INVALID"):
        replay_optimized_strategy(_soxl_request(income_cashflow={EXECUTE: -2904.0}))


def test_external_cashflow_is_pretrade_and_return_neutral(monkeypatch, tmp_path: Path) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import (
        _input_id,
        persist_optimized_strategy_trial,
    )

    sessions = (SIGNAL, EXECUTE)
    path = tmp_path / "external-flow.json"
    payload = _local_fixture_file(
        path,
        external_cashflow={EXECUTE.isoformat(): 100.0},
        external_cashflow_contract="synthetic declared external contribution before trading",
    )
    payload["input"]["initial_cash"] = 100.0
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_soxl_targets(monkeypatch, sessions, {SIGNAL: 0.0})
    replay = replay_optimized_strategy(request)
    point = replay.points[1]
    assert point.cash == pytest.approx(200.0)
    assert point.nav == pytest.approx(200.0)
    assert point.external_cashflow == pytest.approx(100.0)
    assert point.daily_return == pytest.approx(0.0)
    assert replay.backtest.total_return == pytest.approx(0.0)
    changed = replace(request, external_cashflow={EXECUTE: 101.0}, research_identity=None)
    assert _input_id(request) != _input_id(changed)
    assert replay.backtest.run_id != replay_optimized_strategy(changed).backtest.run_id

    store = PerformanceStore(local_root=tmp_path / "external-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="external-neutral")
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert ledger.days[0].external_cashflow == pytest.approx(100.0)
    assert ledger.days[0].daily_return == pytest.approx(0.0)


def test_v2_identity_binds_both_workspace_patches_through_qpk_trial(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research.optimized_member_identity import validate_optimized_member_identity
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "v2-member.json"
    payload = _local_fixture_file(path)
    identity = _v2_identity_from_v1(payload["identity"])
    payload["identity"] = identity
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_soxl_targets(monkeypatch, (SIGNAL, EXECUTE), {SIGNAL: 0.0})

    replay = replay_optimized_strategy(request)
    assert replay.backtest.params["research_identity"] == identity
    assert replay.backtest.run_id

    changed_qpk = _v2_identity_from_v1(payload["identity"], qpk_patch="e" * 64)
    changed_ues = _v2_identity_from_v1(payload["identity"], ues_patch="f" * 64)
    assert changed_qpk["economic_identity_sha256"] != identity["economic_identity_sha256"]
    assert changed_ues["economic_identity_sha256"] != identity["economic_identity_sha256"]
    assert replay_optimized_strategy(replace(request, research_identity=changed_qpk)).backtest.run_id != replay.backtest.run_id
    assert replay_optimized_strategy(replace(request, research_identity=changed_ues)).backtest.run_id != replay.backtest.run_id
    for field in ("ues_workspace_patch_sha256", "qpk_workspace_patch_sha256"):
        tampered = dict(identity)
        tampered[field] = "a" * 64
        with pytest.raises(ValueError, match="digest mismatch"):
            validate_optimized_member_identity(tampered)

    store = PerformanceStore(local_root=tmp_path / "v2-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="v2-patched-identity")
    stored = store.load_research_trial("us_equity", "soxl_soxx_trend_income", record.trial_id)
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version
    )
    assert stored is not None and stored.research_identity == identity
    assert ledger is not None and ledger.run_id == record.run_id


def test_dividend_accrual_payment_and_local_ledger_readback(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research import optimized_strategy_replay as replay_module
    from us_equity_strategies.research.optimized_strategy_replay import (
        _input_id,
        persist_optimized_strategy_trial,
    )

    path = tmp_path / "dividend-events.json"
    payload = _local_fixture_file(
        path, corporate_action_contract="synthetic declared dividend accrual and payment events"
    )
    source = payload["input"]
    source["initial_cash"] = 0.0
    source["initial_quantities"]["SOXL"] = 1.0
    for bar in source["prices"]:
        close = 100.0 if bar["session"] == SIGNAL.isoformat() else 95.0
        bar.update(open=close, high=close, low=close, close=close)
    accrual = {
        "event_id": "synthetic-dividend-accrual",
        "event_type": "dividend_accrual",
        "symbol": "SOXL",
        "per_share": 5.0,
    }
    payment = {
        "event_id": "synthetic-dividend-payment",
        "event_type": "dividend_payment",
        "symbol": "SOXL",
        "amount": 5.0,
        "reference_event_id": accrual["event_id"],
    }
    source["ledger_events"] = {
        EXECUTE.isoformat(): {
            "declared_event_ids": [accrual["event_id"]], "events": [accrual],
        },
        "2024-01-04": {
            "declared_event_ids": [payment["event_id"]], "events": [payment],
        },
    }
    source["income_cashflow"] = {"2024-01-04": 5.0}
    source["calendar"] = [SIGNAL.isoformat(), EXECUTE.isoformat(), "2024-01-04"]
    third_day_prices = []
    for bar in source["prices"]:
        if bar["session"] != EXECUTE.isoformat():
            continue
        clone = dict(bar)
        clone["session"] = "2024-01-04"
        if clone["symbol"] == "SOXL":
            clone.update(open=95.0, high=95.0, low=95.0, close=95.0)
        third_day_prices.append(clone)
    source["prices"].extend(third_day_prices)
    source["derived_indicators"]["2024-01-03"] = deepcopy(source["derived_indicators"][SIGNAL.isoformat()])
    source["indicator_sources"]["2024-01-03"] = deepcopy(source["indicator_sources"][SIGNAL.isoformat()])
    for by_symbol in source["indicator_sources"]["2024-01-03"].values():
        for evidence in by_symbol.values():
            evidence["source_id"] = evidence["source_id"].replace(SIGNAL.isoformat(), "2024-01-03")
            evidence["available_at"] = "2024-01-03T20:59:00Z"
    payload["identity"]["window_end"] = "2024-01-04"
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)

    sessions = (SIGNAL, EXECUTE, date(2024, 1, 4))
    _install_fixed_soxl_targets(monkeypatch, sessions, {SIGNAL: 95.0, EXECUTE: 95.0})
    replay = replay_optimized_strategy(request)
    accrual_day, payment_day = replay.points[1:]
    assert accrual_day.cash == pytest.approx(0.0)
    assert accrual_day.dividend_receivable == pytest.approx(5.0)
    assert accrual_day.nav == pytest.approx(100.0)
    assert accrual_day.daily_return == pytest.approx(0.0)
    assert payment_day.cash == pytest.approx(5.0)
    assert payment_day.dividend_receivable == pytest.approx(0.0)
    assert payment_day.nav == pytest.approx(100.0)
    assert payment_day.income_cashflow == pytest.approx(5.0)
    assert payment_day.daily_return == pytest.approx(0.0)
    assert replay.backtest.total_return == pytest.approx(0.0)

    tampered_events = dict(request.ledger_events)
    changed_accrual = dict(tampered_events[EXECUTE]["events"][0])
    changed_accrual["per_share"] = 4.0
    tampered_events[EXECUTE] = {
        "declared_event_ids": (changed_accrual["event_id"],), "events": (changed_accrual,),
    }
    with pytest.raises(OptimizedStrategyReplayError, match="RESEARCH_IDENTITY_MISMATCH"):
        replay_optimized_strategy(replace(request, ledger_events=tampered_events))
    assert replay_module._input_id(request) != replay_module._input_id(
        replace(request, ledger_events=tampered_events, research_identity=None)
    )

    store = PerformanceStore(local_root=tmp_path / "dividend-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="dividend-events")
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert ledger.days[0].dividend_receivable == pytest.approx(5.0)
    assert ledger.days[0].events[0].event_type == "dividend_accrual"
    assert ledger.days[1].income_cashflow == pytest.approx(5.0)
    assert ledger.days[1].events[0].event_type == "dividend_payment"

    from us_equity_strategies.research.optimized_strategy_replay import _research_ledger, _started_trial

    bad_receivable_day = replace(accrual_day, dividend_receivable=4.0)
    with pytest.raises(ValueError, match="ledger_nav"):
        _research_ledger(
            replace(replay, points=(replay.points[0], bad_receivable_day, payment_day)),
            _started_trial(request, "dividend-receivable-mismatch"),
        )


def test_split_event_preserves_nav_and_updates_shares(monkeypatch) -> None:
    sessions = (SIGNAL, EXECUTE)
    split = {
        "event_id": "synthetic-split-2-for-1",
        "event_type": "split",
        "symbol": "SOXL",
        "ratio": 2.0,
    }
    request = _accounting_fixture_request(
        sessions, initial_cash=0.0, initial_soxl_quantity=1.0,
        soxl_closes={SIGNAL: 100.0, EXECUTE: 50.0},
        ledger_events={EXECUTE: {"declared_event_ids": [split["event_id"]], "events": [split]}},
    )
    _install_fixed_soxl_targets(monkeypatch, sessions, {SIGNAL: 100.0})
    replay = replay_optimized_strategy(request)
    before = replay.points[0]
    day = replay.points[1]
    assert dict(before.holdings)["SOXL"] == pytest.approx(1.0)
    assert before.nav == pytest.approx(100.0)
    assert dict(day.holdings)["SOXL"] == pytest.approx(2.0)
    assert day.nav == pytest.approx(100.0)
    assert day.daily_return == pytest.approx(0.0)
    assert day.trade_net_cashflow == pytest.approx(0.0)


@pytest.mark.parametrize("invalid_kind", ("missing_day", "missing_event", "duplicate_id", "cash_mismatch"))
def test_invalid_ledger_event_sets_and_dividend_cash_are_rejected(invalid_kind: str) -> None:
    third_day = date(2024, 1, 4)
    accrual = {
        "event_id": "dividend-accrual", "event_type": "dividend_accrual",
        "symbol": "SOXL", "per_share": 5.0,
    }
    payment = {
        "event_id": "dividend-payment", "event_type": "dividend_payment",
        "symbol": "SOXL", "amount": 5.0, "reference_event_id": "dividend-accrual",
    }
    events = {
        EXECUTE: {"declared_event_ids": [accrual["event_id"]], "events": [accrual]},
        third_day: {"declared_event_ids": [payment["event_id"]], "events": [payment]},
    }
    expected = {
        "missing_day": "LEDGER_EVENT_DAY_GAP",
        "missing_event": "LEDGER_EVENT_SET",
        "duplicate_id": "LEDGER_EVENT_DUPLICATE",
        "cash_mismatch": "LEDGER_DIVIDEND_CASHFLOW",
    }[invalid_kind]
    income = {third_day: 5.0}
    if invalid_kind == "missing_day":
        events.pop(third_day)
    elif invalid_kind == "missing_event":
        events[EXECUTE] = {"declared_event_ids": [], "events": [accrual]}
    elif invalid_kind == "duplicate_id":
        payment["event_id"] = accrual["event_id"]
        events[third_day] = {"declared_event_ids": [accrual["event_id"]], "events": [payment]}
    else:
        income = {third_day: 4.0}
    request = _accounting_fixture_request(
        (SIGNAL, EXECUTE, third_day), initial_cash=0.0, initial_soxl_quantity=1.0,
        soxl_closes={SIGNAL: 100.0, EXECUTE: 95.0, third_day: 95.0},
        ledger_events=events, income_cashflow=income,
    )
    with pytest.raises(OptimizedStrategyReplayError, match=expected):
        replay_optimized_strategy(request)


def test_soxl_research_market_regime_runs_real_builder_and_recurses_state(monkeypatch) -> None:
    from us_equity_strategies.research import optimized_strategy_replay as replay_module

    manifest, original_builder = replay_module._PROFILES["soxl_soxx_trend_income"]
    observed = []

    def observe(context):
        decision = original_builder(context)
        observed.append((context, decision.diagnostics))
        return decision

    monkeypatch.setitem(
        replay_module._PROFILES, "soxl_soxx_trend_income", (manifest, observe)
    )
    research_request = _soxl_market_regime_request(("no_action", "risk_off"))
    assert research_request.evidence_use == "fixture"
    assert research_request.promotion_eligible is False
    assert research_request.identity.param_set_id == "synthetic-hand-v1"
    research_result = replay_optimized_strategy(research_request)
    first_decision_context, first_diagnostics = observed[0]
    second_decision_context, _ = observed[1]
    assert first_diagnostics["blend_gate_volatility_delever_triggered"] is True
    assert first_diagnostics["blend_gate_volatility_delever_effective_retention_ratio"] == pytest.approx(0.50)
    second_snapshot_quantities = {
        position.symbol: position.quantity
        for position in second_decision_context.portfolio.positions
    }
    assert second_snapshot_quantities == pytest.approx(
        {
            symbol: quantity
            for symbol, quantity in research_result.points[1].holdings
            if quantity > 0.0
        }
    )

    risk_off_result = replay_optimized_strategy(
        _soxl_market_regime_request(("risk_off", "risk_off"))
    )
    risk_off_decision = observed[2][1]
    assert risk_off_decision["market_regime_control_applied"] is True
    assert dict(research_result.points[1].holdings) != dict(risk_off_result.points[1].holdings)
    assert research_result.backtest.run_id != risk_off_result.backtest.run_id
    assert research_result.points[1].holdings
    assert first_decision_context.portfolio.metadata["market_regime_control"]["execution_controls"][
        "consumption_evidence_status"
    ] == "automation_approved"
    assert replay_module._input_id(research_request) != replay_module._input_id(
        _soxl_market_regime_request(("risk_off", "risk_off"))
    )


@pytest.mark.parametrize("failure", ("missing", "future", "unapproved", "missing_context"))
def test_soxl_research_market_regime_requires_causal_approved_artifacts(failure: str) -> None:
    request = _soxl_market_regime_request(("no_action", "no_action"))
    rows = list(request.state_inputs or ())
    row = dict(rows[0])
    state = dict(row["state"])
    artifact = dict(state.get("market_regime_control", {}))
    if failure == "missing":
        state.pop("market_regime_control", None)
        row["value_sources"].pop("market_regime_control", None)
    elif failure == "future":
        row["available_at"] = "2024-01-02T21:00:01Z"
    elif failure == "unapproved":
        controls = dict(artifact["execution_controls"])
        controls["consumption_evidence_status"] = "notification_only"
        artifact["execution_controls"] = controls
        state["market_regime_control"] = artifact
    else:
        position = dict(artifact["position_control"])
        position.pop("volatility_delever_context")
        artifact["position_control"] = position
        state["market_regime_control"] = artifact
    row["state"] = state
    rows[0] = row
    invalid = replace(request, state_inputs=tuple(rows))
    error = {
        "missing": "RESEARCH_MARKET_REGIME_ARTIFACT_REQUIRED",
        "future": "STATE_NOT_AVAILABLE_AT_DECISION",
        "unapproved": "RESEARCH_MARKET_REGIME_ARTIFACT_UNAPPROVED",
        "missing_context": "RESEARCH_MARKET_REGIME_CONTEXT_REQUIRED",
    }[failure]
    with pytest.raises(OptimizedStrategyReplayError, match=error):
        replay_optimized_strategy(invalid)


def test_unvalidated_nested_market_regime_artifact_cannot_override_root_state() -> None:
    request = _soxl_market_regime_request(("no_action", "no_action"))
    rows = []
    for row in request.state_inputs or ():
        copied = deepcopy(dict(row))
        root_artifact = copied["state"]["market_regime_control"]
        nested_artifact = _market_regime_artifact(
            date.fromisoformat(copied["signal_date"]), route="risk_off"
        )
        nested_artifact["execution_controls"]["consumption_evidence_status"] = (
            "automation_approved"
        )
        copied["state"] = {
            "aaa_unvalidated": {"nested": nested_artifact},
            "market_regime_control": root_artifact,
        }
        copied["value_sources"]["aaa_unvalidated"] = {
            "source_id": "synthetic-unvalidated-nested",
            "available_at": f"{copied['signal_date']}T20:59:00Z",
        }
        rows.append(copied)
    with_extra_artifact = replace(request, state_inputs=tuple(rows))

    guarded = replay_optimized_strategy(with_extra_artifact)
    expected = replay_optimized_strategy(request)
    assert dict(guarded.points[1].holdings) == dict(expected.points[1].holdings)


def test_tqqq_research_plugins_run_real_builder_with_causal_retention_and_crisis(monkeypatch) -> None:
    from us_equity_strategies.research import optimized_strategy_replay as replay_module

    manifest, original_builder = replay_module._PROFILES["tqqq_growth_income"]
    observed = []

    def observe(context):
        decision = original_builder(context)
        observed.append((context, decision))
        return decision

    monkeypatch.setitem(replay_module._PROFILES, "tqqq_growth_income", (manifest, observe))
    request = _tqqq_market_regime_request(("no_action", "risk_off"))
    assert request.identity.param_set_id == "synthetic-tqqq-plugin-v1"
    assert request.evidence_use == "fixture" and request.promotion_eligible is False
    result = replay_optimized_strategy(request)
    first_context, first_decision = observed[0]
    second_context, _ = observed[1]
    annotations = first_decision.diagnostics["execution_annotations"]
    assert annotations["dual_drive_volatility_delever_triggered"] is True
    assert annotations["dual_drive_volatility_delever_retention_context_found"] is True
    assert annotations["dual_drive_volatility_delever_retention_ratio"] == pytest.approx(0.50)
    assert annotations["dual_drive_volatility_delever_taco_veto_enabled"] is True
    assert annotations["dual_drive_volatility_delever_taco_rebound_context_active"] is False
    assert annotations["dual_drive_volatility_delever_vetoed"] is False
    assert {
        position.symbol: position.quantity
        for position in second_context.portfolio.positions
    } == pytest.approx(
        {
            symbol: quantity
            for symbol, quantity in result.points[1].holdings
            if quantity > 0.0
        }
    )
    assert first_context.portfolio.metadata["market_regime_control"]["execution_controls"][
        "consumption_evidence_status"
    ] == "automation_approved"

    risk_off_result = replay_optimized_strategy(
        _tqqq_market_regime_request(("risk_off", "risk_off"))
    )
    risk_off_decision = observed[2][1]
    risk_off_annotations = risk_off_decision.diagnostics["execution_annotations"]
    macro_context = risk_off_decision.diagnostics["notification_context"]["risk_controls"][
        "dual_drive_macro_risk_governor"
    ]
    assert macro_context["applied"] is True
    assert macro_context["leverage_scalar"] == 0.0
    assert risk_off_annotations["dual_drive_crisis_defense_applied"] is True
    no_action_holdings = dict(result.points[1].holdings)
    risk_off_holdings = dict(risk_off_result.points[1].holdings)
    assert no_action_holdings["TQQQ"] > risk_off_holdings["TQQQ"]
    assert no_action_holdings["QQQM"] > risk_off_holdings["QQQM"]
    assert no_action_holdings["BOXX"] < risk_off_holdings["BOXX"]
    assert result.backtest.run_id != risk_off_result.backtest.run_id
    assert replay_module._input_id(request) != replay_module._input_id(
        _tqqq_market_regime_request(("risk_off", "risk_off"))
    )


def test_tqqq_first_decision_is_stable_under_future_perturbation_and_prefix_replay(monkeypatch) -> None:
    from us_equity_strategies.research import optimized_strategy_replay as replay_module

    manifest, original_builder = replay_module._PROFILES["tqqq_growth_income"]
    observed = []

    def observe(context):
        decision = original_builder(context)
        symbols = tuple(context.runtime_config["managed_symbols"])
        observed.append(replay_module._targets(decision, symbols))
        return decision

    monkeypatch.setitem(replay_module._PROFILES, "tqqq_growth_income", (manifest, observe))
    request = _tqqq_market_regime_request(("no_action", "risk_off"))
    replay_optimized_strategy(request)
    baseline_first = observed[0]

    observed.clear()
    future_bars = list(request.benchmark_bars)
    future = future_bars[-1]
    future_bars[-1] = DatedBar(
        future.session, future.symbol, future.open * 3, future.high * 3,
        future.low * 3, future.close * 3, future.source_id, future.available_at,
    )
    replay_optimized_strategy(replace(request, benchmark_bars=tuple(future_bars)))
    assert observed[0] == baseline_first

    observed.clear()
    prefix_sessions = request.calendar[:2]
    prefix = replace(
        request,
        calendar=prefix_sessions,
        prices=tuple(bar for bar in request.prices if bar.session in prefix_sessions),
        state_inputs=request.state_inputs[:1],
        benchmark_bars=tuple(bar for bar in request.benchmark_bars if bar.session <= prefix_sessions[0]),
    )
    replay_optimized_strategy(prefix)
    assert observed[0] == baseline_first


@pytest.mark.parametrize("failure", ("missing", "future", "unapproved", "nested"))
def test_tqqq_research_plugins_reject_or_ignore_untrusted_state(failure: str) -> None:
    request = _tqqq_market_regime_request(("no_action", "no_action"))
    rows = list(request.state_inputs or ())
    row = deepcopy(dict(rows[0]))
    state = row["state"]
    if failure == "missing":
        state.pop("market_regime_control")
        row["value_sources"].pop("market_regime_control")
        row["state"] = state
        rows[0] = row
        with pytest.raises(OptimizedStrategyReplayError, match="RESEARCH_MARKET_REGIME_ARTIFACT_REQUIRED"):
            replay_optimized_strategy(replace(request, state_inputs=tuple(rows)))
        return
    if failure == "future":
        row["available_at"] = "2024-01-02T21:00:01Z"
    elif failure == "unapproved":
        state["market_regime_control"]["execution_controls"]["consumption_evidence_status"] = (
            "notification_only"
        )
        row["state"] = state
    else:
        valid = state["market_regime_control"]
        nested = _tqqq_market_regime_artifact(
            date.fromisoformat(row["signal_date"]), route="risk_off"
        )
        nested["execution_controls"]["consumption_evidence_status"] = "automation_approved"
        row["state"] = {"aaa_unvalidated": {"nested": nested}, "market_regime_control": valid}
        row["value_sources"]["aaa_unvalidated"] = {
            "source_id": "synthetic-unvalidated-nested",
            "available_at": f"{row['signal_date']}T20:59:00Z",
        }
        rows[0] = row
        with_extra = replace(request, state_inputs=tuple(rows))
        with_extra_result = replay_optimized_strategy(with_extra)
        baseline = replay_optimized_strategy(request)
        assert dict(with_extra_result.points[1].holdings) == dict(baseline.points[1].holdings)
        return
    rows[0] = row
    error = "STATE_NOT_AVAILABLE_AT_DECISION" if failure == "future" else "RESEARCH_MARKET_REGIME_ARTIFACT_UNAPPROVED"
    with pytest.raises(OptimizedStrategyReplayError, match=error):
        replay_optimized_strategy(replace(request, state_inputs=tuple(rows)))


def test_tqqq_research_plugins_require_all_explicit_flags() -> None:
    request = _tqqq_market_regime_request(("no_action", "no_action"))
    config = dict(request.runtime_config)
    config["dual_drive_crisis_defense_enabled"] = False
    with pytest.raises(OptimizedStrategyReplayError, match="UNSIMULATED_TARGET_PLUGIN"):
        replay_optimized_strategy(replace(request, runtime_config=config))


def test_local_member_identity_accepts_non_ascii_runtime_config(tmp_path: Path) -> None:
    path = tmp_path / "unicode-member.json"
    payload = _local_fixture_file(path)
    config = payload["input"]["runtime_config"]
    config["blend_gate_volatility_delever_retention_policy"] = "synthetic café 策略"
    payload["identity"]["actual_params"] = config
    payload["identity"]["config_sha256"] = hashlib.sha256(_canonical(config)).hexdigest()
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    assert replay.backtest.params["research_identity"] == payload["identity"]


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
        {"session": day.isoformat(), "symbol": symbol, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
         "source_id": f"synthetic:{symbol}:{day.isoformat()}", "available_at": f"{day.isoformat()}T20:00:00Z"}
        for day in (SIGNAL, EXECUTE) for symbol in symbols
    ]
    source["derived_indicators"] = None
    source["benchmark_bars"] = [
        {"session": day.isoformat(), "symbol": "QQQ", "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
         "source_id": f"synthetic:QQQ:{day.isoformat()}", "available_at": f"{day.isoformat()}T20:00:00Z"}
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
            bars.append(DatedBar(
                session, symbol, price, price, price, price,
                f"synthetic:{symbol}:{session.isoformat()}", f"{session.isoformat()}T20:00:00Z",
            ))
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
        indicator_sources={
            SIGNAL: {
                symbol: {
                    metric: {
                        "source_id": f"synthetic-indicator:{symbol.upper()}:{SIGNAL.isoformat()}",
                        "available_at": f"{SIGNAL.isoformat()}T20:59:00Z",
                        "derivation": "synthetic_fixture_literal.v1",
                        "source_bars": [],
                    }
                    for metric in metrics
                }
                for symbol, metrics in {
                    "soxl": {"price": 50.0, "ma_trend": 40.0},
                    "soxx": {"price": 120.0, "ma_trend": 100.0, "ma20_slope": -0.25,
                             "rsi14": 50.0, "rsi14_dynamic_threshold": 65.0, "bb_mid": 100.0,
                             "bb_upper": 130.0, "bb_lower": 80.0, "realized_volatility_10": 0.20,
                             "realized_volatility_10_dynamic_threshold": 0.50,
                             "realized_volatility_10_dynamic_sample_count": 252.0},
                }.items()
            }
        },
    )
    for key, value in overrides.items():
        object.__setattr__(request, key, value)
    return request


def _accounting_fixture_request(
    sessions: tuple[date, ...],
    *,
    initial_cash: float,
    initial_soxl_quantity: float,
    soxl_closes: dict[date, float],
    ledger_events: dict[date, dict[str, object]] | None = None,
    income_cashflow: dict[date, float] | None = None,
    external_cashflow: dict[date, float] | None = None,
) -> ReplayRequest:
    base = _soxl_request()
    symbols = SOXL_SYMBOLS
    prices = tuple(
        DatedBar(
            session, symbol,
            soxl_closes[session] if symbol == "SOXL" else 100.0,
            soxl_closes[session] if symbol == "SOXL" else 100.0,
            soxl_closes[session] if symbol == "SOXL" else 100.0,
            soxl_closes[session] if symbol == "SOXL" else 100.0,
        )
        for session in sessions for symbol in symbols
    )
    indicators = {
        session: deepcopy(base.derived_indicators[SIGNAL])
        for session in sessions[:-1]
    }
    quantities = {symbol: 0.0 for symbol in symbols}
    quantities["SOXL"] = initial_soxl_quantity
    return replace(
        base,
        calendar=sessions,
        initial_cash=initial_cash,
        initial_quantities=quantities,
        prices=prices,
        cost_model=PromotionCostModel("SYNTHETIC_ZERO", 0.0, 0.0, 0.0),
        derived_indicators=indicators,
        ledger_events=ledger_events,
        income_cashflow=income_cashflow,
        external_cashflow=external_cashflow,
    )


def _install_fixed_soxl_targets(monkeypatch, sessions: tuple[date, ...], target_values) -> None:
    from types import SimpleNamespace

    from us_equity_strategies.research import optimized_strategy_replay as replay_module

    effective_by_signal = dict(zip(sessions[:-1], sessions[1:], strict=True))
    manifest, _ = replay_module._PROFILES["soxl_soxx_trend_income"]

    def fixed_target_builder(context):
        signal = date.fromisoformat(context.as_of)
        soxl_value = target_values[signal]
        return SimpleNamespace(
            positions=tuple(
                SimpleNamespace(symbol=symbol, target_value=soxl_value if symbol == "SOXL" else 0.0)
                for symbol in SOXL_SYMBOLS
            ),
            risk_flags=(),
            diagnostics={
                "signal_date": signal.isoformat(),
                "effective_date": effective_by_signal[signal].isoformat(),
                "execution_timing_contract": "next_trading_day",
                "signal_effective_after_trading_days": 1,
            },
        )

    monkeypatch.setitem(
        replay_module._PROFILES, "soxl_soxx_trend_income", (manifest, fixed_target_builder)
    )


def _install_fixed_tqqq_targets(
    monkeypatch,
    sessions: tuple[date, ...],
    *,
    target_values: dict[date, dict[str, float]] | None = None,
) -> None:
    from types import SimpleNamespace

    from us_equity_strategies.research import optimized_strategy_replay as replay_module

    effective_by_signal = dict(zip(sessions[:-1], sessions[1:], strict=True))
    manifest, _ = replay_module._PROFILES["tqqq_growth_income"]

    def fixed_target_builder(context):
        signal = date.fromisoformat(context.as_of)
        return SimpleNamespace(
            positions=tuple(
                SimpleNamespace(symbol=symbol, target_value=(target_values or {}).get(signal, {}).get(symbol, 0.0))
                for symbol in _tqqq_market_regime_request(("risk_on", "risk_on")).runtime_config["managed_symbols"]
            ),
            risk_flags=(),
            diagnostics={
                "signal_date": signal.isoformat(),
                "effective_date": effective_by_signal[signal].isoformat(),
                "execution_timing_contract": "next_trading_day",
                "signal_effective_after_trading_days": 1,
            },
        )

    monkeypatch.setitem(replay_module._PROFILES, "tqqq_growth_income", (manifest, fixed_target_builder))


def _tqqq_expiry_fixture(path: Path, *, spot: float, initial_cash: float = 100_000.0) -> dict[str, object]:
    sessions = (SIGNAL, EXECUTE, date(2024, 1, 4))
    expiration = sessions[-1]
    contract_id = "TQQQ-2024-01-04-C-100"
    campaign_id = "expiry-campaign"
    lot = _tqqq_option_position(
        contract_id, campaign_id, 1, cost_basis=1_000.0, campaign_basis=1_000.0
    )
    quote = _tqqq_option_quote(contract_id, strike=100.0, bid=20.0, ask=22.0)
    quote["expiration"] = expiration.isoformat()
    lots_by_day = tuple((lot,) for _ in sessions)
    quotes_by_day = tuple((quote,) for _ in sessions)
    payload = _tqqq_option_fixture_file(
        path, initial_cash=initial_cash,
        positions_by_day=lots_by_day, quotes_by_day=quotes_by_day,
    )
    for bar in payload["input"]["prices"]:
        if bar["session"] == expiration.isoformat() and bar["symbol"] == "TQQQ":
            bar.update(open=spot, high=spot, low=spot, close=spot)
    payload["identity"] = _build_tqqq_v2_identity(
        payload["input"],
        cost_source="SYNTHETIC_10BPS",
        cost_inputs={"commission_bps": 10, "slippage_bps": 0, "market_impact_bps": 0},
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _tqqq_roll_fixture(
    path: Path, *, gate_route: str = "risk_on", include_new_quote: bool = True,
    old_execution_bid: float = 90.0, new_execution_ask: float = 30.0,
) -> dict[str, object]:
    sessions = (SIGNAL, EXECUTE, date(2024, 1, 4))
    old_contract = "TQQQ-2024-12-31-C-80"
    new_contract = "TQQQ-2026-01-02-C-100"
    old_campaign = "old-leaps-campaign"
    old_lots = tuple(
        _tqqq_option_position(old_contract, old_campaign, lot, cost_basis=3_000.0, campaign_basis=9_000.0)
        for lot in range(1, 4)
    )
    new_campaign = f"tqqq-leaps-roll-{EXECUTE.isoformat()}"
    new_unit_cost = round(min(new_execution_ask, ((29.0 + new_execution_ask) / 2.0) * 1.03), 2) * 100.0
    new_lot_count = math.floor(9_450.0 / new_unit_cost)
    new_campaign_basis = new_unit_cost * new_lot_count
    new_lots = tuple(
        {
            "ledger_position_id": "OPT-" + hashlib.sha256(
                f"{new_contract}|{new_campaign}|{lot}".encode()
            ).hexdigest()[:28],
            "contract_id": new_contract,
            "campaign_id": new_campaign,
            "lot_index": lot,
            "cost_basis_per_contract": new_unit_cost,
            "campaign_basis": new_campaign_basis,
            "recovered_proceeds": 0.0,
        }
        for lot in range(1, new_lot_count + 1)
    )
    old_signal = _tqqq_option_quote(old_contract, strike=80.0, bid=50.0, ask=52.0, delta=0.95)
    old_execution = _tqqq_option_quote(old_contract, strike=80.0, bid=old_execution_bid, ask=92.0, delta=0.95)
    new_signal = _tqqq_option_quote(new_contract, strike=100.0, bid=29.0, ask=31.0, delta=0.75)
    new_execution = _tqqq_option_quote(new_contract, strike=100.0, bid=29.0, ask=new_execution_ask, delta=0.75)
    for quote in (old_signal, old_execution):
        quote["expiration"] = "2024-12-31"
    rows = (
        (old_signal, new_signal) if include_new_quote else (old_signal,),
        (old_execution, new_execution) if include_new_quote else (old_execution,),
        (old_execution, new_execution) if include_new_quote else (old_execution,),
    )
    execution_mid = (29.0 + new_execution_ask) / 2.0
    execution_candidate_tradable = (
        new_execution_ask > 0.0
        and (new_execution_ask - 29.0) / execution_mid <= 0.12
    )
    positions = (
        old_lots, old_lots,
        new_lots if include_new_quote and gate_route == "risk_on" and execution_candidate_tradable else old_lots,
    )
    payload = _tqqq_option_fixture_file(
        path, initial_cash=300_000.0, positions_by_day=positions, quotes_by_day=rows
    )
    if gate_route != "risk_on":
        for row in payload["input"]["state_inputs"]:
            artifact = row["state"]["market_regime_control"]
            artifact["position_control"]["final_route"] = gate_route
    payload["identity"] = _build_tqqq_v2_identity(payload["input"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_tqqq_leaps_synthetic_open_and_daily_mark_round_trip(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "tqqq-leaps-open.json"
    payload = _tqqq_option_fixture_file(path)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)

    replay = replay_optimized_strategy(request)
    first_trade = replay.points[1]
    assert first_trade.cash == pytest.approx(291_000.0)
    assert sum(mark.valuation for mark in first_trade.option_positions) == pytest.approx(9_000.0)
    assert first_trade.nav == pytest.approx(300_000.0)
    assert len(first_trade.option_positions) == 3
    assert all(mark.option_premium_cashflow == pytest.approx(-3_000.0) for mark in first_trade.option_positions)
    assert len([event for event in first_trade.events if event.event_type == "option_trade"]) == 3

    store = PerformanceStore(local_root=tmp_path / "tqqq-leaps-open-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="tqqq-leaps-open")
    ledger = store.load_research_ledger(
        "us_equity", "tqqq_growth_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert sum(mark.valuation for mark in ledger.days[0].positions if mark.option_underlying == "TQQQ") == pytest.approx(9_000.0)
    assert payload["identity"]["schema_version"].endswith(".v2")


@pytest.mark.parametrize("opening_cash,expected_open", [(10_000.0, False), (300_000.0, True)])
def test_tqqq_new_leaps_obeys_original_budget_threshold_without_future_positions(
    tmp_path: Path, monkeypatch, opening_cash: float, expected_open: bool
) -> None:
    path = tmp_path / "tqqq-start-threshold.json"
    fixture = _tqqq_option_fixture_file(path, initial_cash=opening_cash)
    source = fixture["input"]
    for row in source["option_market_inputs"][1:]:
        row.pop("positions")
        row.pop("positions_source")
    fixture["identity"] = _build_tqqq_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    replay = replay_optimized_strategy(request)
    assert bool(replay.points[1].option_positions) is expected_open


def test_tqqq_leaps_fixture_identity_is_not_the_full_v2_candidate(tmp_path: Path) -> None:
    path = tmp_path / "tqqq-leaps-identity-separation.json"
    fixture = _tqqq_option_fixture_file(path)
    candidate = json.loads(
        Path("docs/research/independent_tqqq_full_manifest_v2_20260925.json").read_text()
    )
    assert fixture["identity"]["param_set_id"] == "synthetic-tqqq-leaps-slice-v1"
    assert fixture["identity"]["param_set_id"] != candidate["candidate_id"]
    assert fixture["identity"]["config_sha256"] != candidate["config_sha256"]


def _soxx_credit_fixture(path: Path) -> dict[str, object]:
    payload = _local_fixture_file(path)
    source = payload["input"]
    third = date(2024, 1, 4)
    source["calendar"].append(third.isoformat())
    last_session_prices = [dict(item) for item in source["prices"] if item["session"] == EXECUTE.isoformat()]
    for item in last_session_prices:
        item["session"] = third.isoformat()
        item["source_id"] = f"synthetic:{item['symbol']}:{third.isoformat()}"
        item["available_at"] = f"{third.isoformat()}T20:00:00Z"
    source["prices"].extend(last_session_prices)
    source["derived_indicators"][EXECUTE.isoformat()] = deepcopy(source["derived_indicators"][SIGNAL.isoformat()])
    source["indicator_sources"][EXECUTE.isoformat()] = deepcopy(source["indicator_sources"][SIGNAL.isoformat()])
    config = source["runtime_config"]
    config.update({
        "option_overlay_enabled": True, "option_growth_overlay_enabled": False,
        "option_income_overlay_enabled": True,
        "option_income_overlay_recipe": "soxx_put_credit_spread_income_v1",
        "option_income_overlay_start_usd": 50_000.0,
    })
    expiration = "2024-02-16"
    quotes = (
        {"contract_id": "SOXX-P-92", "underlying": "SOXX", "right": "put", "strike": 92.0,
         "expiration": expiration, "multiplier": 100.0, "bid": 3.0, "ask": 3.0, "delta": -0.2,
         "source_id": "synthetic-chain-short", "available_at": "2024-01-02T20:59:00Z"},
        {"contract_id": "SOXX-P-82", "underlying": "SOXX", "right": "put", "strike": 82.0,
         "expiration": expiration, "multiplier": 100.0, "bid": 1.0, "ask": 1.0, "delta": -0.1,
         "source_id": "synthetic-chain-long", "available_at": "2024-01-02T20:59:00Z"},
    )
    source["option_market_inputs"] = []
    for session in (SIGNAL, EXECUTE, third):
        at = f"{session.isoformat()}T20:59:00Z"
        existing_positions = []
        if session == third:
            campaign = f"soxx-put-credit-{EXECUTE.isoformat()}"
            existing_positions = [
                {"ledger_position_id": f"{campaign}-short", "contract_id": "SOXX-P-92",
                 "campaign_id": campaign, "leg": "short", "quantity": -1.0,
                 "cost_basis_per_contract": 300.0, "campaign_basis": 800.0, "recovered_proceeds": 0.0},
                {"ledger_position_id": f"{campaign}-long", "contract_id": "SOXX-P-82",
                 "campaign_id": campaign, "leg": "long", "quantity": 1.0,
                 "cost_basis_per_contract": 100.0, "campaign_basis": 800.0, "recovered_proceeds": 0.0},
            ]
        source["option_market_inputs"].append({
            "session": session.isoformat(), "decision_at": f"{session.isoformat()}T21:00:00Z",
            "soxx_trend": {"positive": True, "source_id": f"trend:{session}", "available_at": at},
            "iv_rank": {"value": 0.5, "source_id": f"iv:{session}", "available_at": at},
            "positions_source": {"source_id": f"positions:{session}", "available_at": at},
            "positions": existing_positions,
            "quotes": [dict(quote, available_at=at) for quote in quotes],
        })
    payload["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_soxx_put_credit_open_is_fully_collateralized_and_readable(tmp_path: Path) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "soxx-put-credit.json"
    fixture = _soxx_credit_fixture(path)
    _, request = load_local_member_fixture(path)
    baseline_config = dict(request.runtime_config)
    baseline_config.update(option_overlay_enabled=False, option_income_overlay_enabled=False)
    baseline = replay_optimized_strategy(replace(
        request, runtime_config=baseline_config, option_market_inputs=None, research_identity=None,
    ))
    replay = replay_optimized_strategy(request)
    opening = replay.points[1]
    assert opening.cash == pytest.approx(baseline.points[1].cash + 200.0)
    assert opening.nav == pytest.approx(baseline.points[1].nav)
    assert opening.restricted_cash == pytest.approx(800.0)
    assert opening.cash >= opening.restricted_cash
    assert sum(mark.valuation for mark in opening.option_positions) == pytest.approx(-200.0)
    option_trades = [event for event in opening.events if event.event_type == "option_trade"]
    assert sorted(event.amount for event in option_trades) == pytest.approx([-100.0, 300.0])
    assert sorted(mark.valuation for mark in opening.option_positions) == pytest.approx([-300.0, 100.0])
    assert any(event.event_type == "collateral_change" and event.amount == pytest.approx(800.0) for event in opening.events)
    store = PerformanceStore(local_root=tmp_path / "soxx-put-credit-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="soxx-put-credit")
    ledger = store.load_research_ledger("us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version)
    assert ledger is not None
    assert ledger.days[0].cash == pytest.approx(baseline.points[1].cash + 200.0)
    assert ledger.days[0].nav == pytest.approx(baseline.points[1].nav)
    assert ledger.days[0].restricted_cash == pytest.approx(800.0)
    assert not any(event.event_type == "option_trade" for event in ledger.days[1].events)
    assert ledger.days[1].restricted_cash == pytest.approx(800.0)
    assert 2 * ledger.days[0].restricted_cash > ledger.days[0].nav * 0.01
    assert fixture["identity"]["param_set_id"] == "synthetic-soxx-put-credit-slice-v1"


@pytest.mark.parametrize("opening_cash,expected_open", [(100_000.0, False), (200_000.0, True)])
def test_soxx_new_spread_obeys_original_budget_threshold(
    tmp_path: Path, opening_cash: float, expected_open: bool
) -> None:
    path = tmp_path / "soxx-start-threshold.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    source["initial_cash"] = opening_cash
    source["runtime_config"]["option_income_overlay_start_usd"] = 150_000.0
    for row in source["option_market_inputs"][1:]:
        row.pop("positions")
        row.pop("positions_source")
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    assert bool(replay.points[1].option_positions) is expected_open


def test_soxx_existing_spread_below_new_entry_threshold_is_still_managed(tmp_path: Path) -> None:
    path = tmp_path / "soxx-held-below-start.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    _soxx_restore_open_spread(source, "2024-02-16")
    source["runtime_config"]["option_income_overlay_start_usd"] = 150_000.0
    source["initial_cash"] = 70_200.0
    for row in source["option_market_inputs"][1:]:
        row.pop("positions")
        row.pop("positions_source")
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    assert all(len(point.option_positions) == 2 for point in replay.points)
    assert all(point.restricted_cash == pytest.approx(800.0) for point in replay.points)


def test_soxx_rechecks_new_entry_threshold_after_account_funding(tmp_path: Path) -> None:
    path = tmp_path / "soxx-cross-start.json"
    fixture = _soxx_credit_fixture(path)
    _soxx_add_session(fixture, date(2024, 1, 5))
    source = fixture["input"]
    source["initial_cash"] = 100_000.0
    source["external_cashflow"] = {EXECUTE.isoformat(): 100_000.0}
    source["runtime_config"]["option_income_overlay_start_usd"] = 150_000.0
    for row in source["option_market_inputs"][1:]:
        row.pop("positions")
        row.pop("positions_source")
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    assert replay.points[1].option_positions == ()
    assert len(replay.points[2].option_positions) == 2
    assert len(replay.points[3].option_positions) == 2


def test_soxx_above_entry_threshold_cannot_use_empty_quote_chain(tmp_path: Path) -> None:
    path = tmp_path / "soxx-missing-chain.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    for row in source["option_market_inputs"]:
        row["positions"] = []
        row["quotes"] = []
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_QUOTE_MISSING"):
        replay_optimized_strategy(request)


def test_soxx_put_credit_rejects_future_iv_rank(tmp_path: Path) -> None:
    path = tmp_path / "soxx-put-credit-future-iv.json"
    fixture = _soxx_credit_fixture(path)
    fixture["input"]["option_market_inputs"][0]["iv_rank"]["available_at"] = "2099-01-01T00:00:00Z"
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_INPUT_NOT_AVAILABLE"):
        replay_optimized_strategy(request)


def test_soxx_put_credit_holds_existing_spread_without_iv_and_rejects_missing_leg_quote(tmp_path: Path) -> None:
    path = tmp_path / "soxx-put-credit-no-iv-held.json"
    fixture = _soxx_credit_fixture(path)
    fixture["input"]["option_market_inputs"][-1].pop("iv_rank")
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    held_day = replay.points[-1]
    assert held_day.restricted_cash == pytest.approx(800.0)
    assert len(held_day.option_positions) == 2
    assert not any(event.event_type == "option_trade" for event in held_day.events)

    fixture["input"]["option_market_inputs"][-1]["quotes"] = fixture["input"]["option_market_inputs"][-1]["quotes"][:1]
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, missing_quote_request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_QUOTE_MISSING"):
        replay_optimized_strategy(missing_quote_request)


def test_soxx_put_credit_requires_iv_rank_for_new_entries(tmp_path: Path) -> None:
    path = tmp_path / "soxx-put-credit-no-iv-entry.json"
    fixture = _soxx_credit_fixture(path)
    fixture["input"]["option_market_inputs"][0].pop("iv_rank")
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_IV_RANK_REQUIRED"):
        replay_optimized_strategy(request)


@pytest.mark.parametrize("execution_day", [False, True], ids=["signal-credit", "execution-credit"])
def test_soxx_put_credit_requires_recipe_minimum_credit_on_signal_and_execution(
    tmp_path: Path, execution_day: bool
) -> None:
    path = tmp_path / f"soxx-put-credit-nonviable-{execution_day}.json"
    fixture = _soxx_credit_fixture(path)
    rows = fixture["input"]["option_market_inputs"]
    for row in rows:
        row["positions"] = []
    if execution_day:
        for row in rows[1:]:
            row["quotes"][0]["bid"] = 1.01
            row["quotes"][0]["ask"] = 1.1
    else:
        for row in rows[:2]:
            row["quotes"][0]["bid"] = 0.5
            row["quotes"][0]["ask"] = 0.6
        for daily_row in rows:
            daily_row["quotes"][1]["strike"] = 84.0
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    assert not any(mark.option_underlying == "SOXX" for point in replay.points for mark in point.option_positions)
    assert not any(event.event_type == "option_trade" for point in replay.points for event in point.events)


def test_soxx_put_credit_selects_closest_short_then_lower_long_strike(tmp_path: Path) -> None:
    path = tmp_path / "soxx-put-credit-recipe-selection.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    source["calendar"] = source["calendar"][:2]
    source["prices"] = [bar for bar in source["prices"] if bar["session"] in source["calendar"]]
    source["derived_indicators"] = {SIGNAL.isoformat(): source["derived_indicators"][SIGNAL.isoformat()]}
    source["indicator_sources"] = {SIGNAL.isoformat(): source["indicator_sources"][SIGNAL.isoformat()]}
    source["option_market_inputs"] = source["option_market_inputs"][:2]
    expiry = "2024-02-16"
    chain = [
        {"contract_id": "SOXX-P-93", "underlying": "SOXX", "right": "put", "strike": 93.0,
         "expiration": expiry, "multiplier": 100.0, "bid": 3.0, "ask": 3.0, "delta": -0.2,
         "source_id": "chain-short-93", "available_at": "2024-01-02T20:59:00Z"},
        {"contract_id": "SOXX-P-83", "underlying": "SOXX", "right": "put", "strike": 83.0,
         "expiration": expiry, "multiplier": 100.0, "bid": 1.0, "ask": 1.0, "delta": -0.1,
         "source_id": "chain-long-83", "available_at": "2024-01-02T20:59:00Z"},
    ]
    for row in source["option_market_inputs"]:
        row["quotes"] = [dict(quote, available_at=f"{row['session']}T20:59:00Z") for quote in chain]
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    opened = replay.points[-1]
    assert {mark.option_strike for mark in opened.option_positions} == {93.0, 83.0}


def test_soxx_put_credit_restores_initial_collateral_from_opening_basis(tmp_path: Path) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "soxx-put-credit-initial-position.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    source["initial_cash"] = 100_200.0
    campaign = "restored-soxx-credit"
    restored = [
        {"ledger_position_id": f"{campaign}-short", "contract_id": "SOXX-P-92",
         "campaign_id": campaign, "leg": "short", "quantity": -1.0,
         "cost_basis_per_contract": 300.0, "campaign_basis": 800.0, "recovered_proceeds": 0.0},
        {"ledger_position_id": f"{campaign}-long", "contract_id": "SOXX-P-82",
         "campaign_id": campaign, "leg": "long", "quantity": 1.0,
         "cost_basis_per_contract": 100.0, "campaign_basis": 800.0, "recovered_proceeds": 0.0},
    ]
    for row in source["option_market_inputs"]:
        row["positions"] = deepcopy(restored)
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    replay = replay_optimized_strategy(request)
    assert replay.points[0].restricted_cash == pytest.approx(800.0)
    assert replay.points[0].nav == pytest.approx(100_000.0)
    store = PerformanceStore(local_root=tmp_path / "soxx-put-credit-initial-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="soxx-put-credit-initial")
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert ledger.initial_restricted_cash == pytest.approx(800.0)
    assert sum(mark.valuation for mark in ledger.initial_positions if mark.option_underlying == "SOXX") == pytest.approx(-200.0)


@pytest.mark.parametrize("management", ["mark", "close", "expiry"])
def test_soxx_existing_spread_below_new_entry_budget_remains_manageable(
    tmp_path: Path, monkeypatch, management: str
) -> None:
    path = tmp_path / f"soxx-existing-under-entry-budget-{management}.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    expiration = "2024-02-16" if management != "expiry" else source["calendar"][-1]
    _soxx_restore_open_spread(source, expiration)
    source["initial_cash"] = 70_200.0
    if management == "close":
        source["option_market_inputs"][0]["management"] = {
            "action": "close_spread", "campaign_id": "restored-soxx-credit",
            "source_id": "synthetic-close-existing-spread",
            "available_at": "2024-01-02T20:59:00Z",
        }
        source["option_market_inputs"][1]["quotes"][0]["ask"] = 4.0
        source["option_market_inputs"][1]["quotes"][1]["bid"] = 0.5
        source["option_market_inputs"][1]["soxx_trend"]["positive"] = False
        source["option_market_inputs"][2]["positions"] = []
        source["option_market_inputs"][2]["positions"] = []
    if management == "expiry":
        for row in source["prices"]:
            if row["session"] == expiration and row["symbol"] == "SOXX":
                row.update(open=92.0, high=92.0, low=92.0, close=92.0)
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_soxl_targets(monkeypatch, request.calendar, {day: 0.0 for day in request.calendar[:-1]})

    replay = replay_optimized_strategy(request)
    assert replay.points[0].cash == pytest.approx(70_200.0)
    assert replay.points[0].nav == pytest.approx(70_000.0)
    assert replay.points[0].restricted_cash == pytest.approx(800.0)
    if management == "mark":
        assert len(replay.points[-1].option_positions) == 2
    elif management == "close":
        assert replay.points[1].option_positions == ()
        assert replay.points[1].restricted_cash == pytest.approx(0.0)
    else:
        assert replay.points[-1].option_positions == ()
        assert replay.points[-1].restricted_cash == pytest.approx(0.0)
        assert replay.points[-1].option_settlement_cashflow == pytest.approx(0.0)


def test_soxx_put_credit_rejects_incomplete_initial_basis(tmp_path: Path) -> None:
    path = tmp_path / "soxx-put-credit-incomplete-initial.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    campaign = "incomplete-soxx-credit"
    for row in source["option_market_inputs"]:
        row["positions"] = [
            {"ledger_position_id": f"{campaign}-short", "contract_id": "SOXX-P-92",
             "campaign_id": campaign, "leg": "short", "quantity": -1.0,
             "cost_basis_per_contract": 300.0, "campaign_basis": 700.0, "recovered_proceeds": 0.0},
            {"ledger_position_id": f"{campaign}-long", "contract_id": "SOXX-P-82",
             "campaign_id": campaign, "leg": "long", "quantity": 1.0,
             "cost_basis_per_contract": 100.0, "campaign_basis": 700.0, "recovered_proceeds": 0.0},
        ]
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_INITIAL_POSITION_INCOMPLETE"):
        replay_optimized_strategy(request)


def _soxx_add_session(payload: dict[str, object], session: date) -> None:
    source = payload["input"]
    previous = date.fromisoformat(source["calendar"][-1])
    source["calendar"].append(session.isoformat())
    for row in tuple(source["prices"]):
        if row["session"] == previous.isoformat():
            copied = dict(row)
            copied["session"] = session.isoformat()
            copied["source_id"] = f"synthetic:{copied['symbol']}:{session.isoformat()}"
            copied["available_at"] = f"{session.isoformat()}T20:00:00Z"
            source["prices"].append(copied)
    previous_indicator_day = max(source["derived_indicators"])
    if previous.isoformat() not in source["derived_indicators"]:
        source["derived_indicators"][previous.isoformat()] = deepcopy(
            source["derived_indicators"][previous_indicator_day]
        )
        source["indicator_sources"][previous.isoformat()] = deepcopy(
            source["indicator_sources"][previous_indicator_day]
        )
    copied_option_row = deepcopy(source["option_market_inputs"][-1])
    copied_option_row["session"] = session.isoformat()
    copied_option_row["decision_at"] = f"{session.isoformat()}T21:00:00Z"
    copied_option_row["soxx_trend"]["available_at"] = f"{session.isoformat()}T20:59:00Z"
    copied_option_row["soxx_trend"]["source_id"] = f"trend:{session}"
    copied_option_row["positions_source"]["available_at"] = f"{session.isoformat()}T20:59:00Z"
    copied_option_row["positions_source"]["source_id"] = f"positions:{session}"
    copied_option_row["quotes"] = [
        {**quote, "available_at": f"{session.isoformat()}T20:59:00Z"}
        for quote in copied_option_row["quotes"]
    ]
    source["option_market_inputs"].append(copied_option_row)
    payload["identity"] = _build_soxl_option_v2_identity(source)


def _soxx_restore_open_spread(source: dict[str, object], expiration: str) -> None:
    campaign = "restored-soxx-credit"
    positions = [
        {"ledger_position_id": f"{campaign}-short", "contract_id": "SOXX-P-92",
         "campaign_id": campaign, "leg": "short", "quantity": -1.0,
         "cost_basis_per_contract": 300.0, "campaign_basis": 800.0, "recovered_proceeds": 0.0},
        {"ledger_position_id": f"{campaign}-long", "contract_id": "SOXX-P-82",
         "campaign_id": campaign, "leg": "long", "quantity": 1.0,
         "cost_basis_per_contract": 100.0, "campaign_basis": 800.0, "recovered_proceeds": 0.0},
    ]
    for row in source["option_market_inputs"]:
        row["positions"] = deepcopy(positions)
        for quote in row["quotes"]:
            quote["expiration"] = expiration
    source["initial_cash"] = 100_200.0


def test_soxx_put_credit_explicit_close_next_session_releases_collateral_and_reads_back(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "soxx-put-credit-explicit-close.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    close_signal = date(2024, 1, 4)
    close_execution = date(2024, 1, 5)
    _soxx_add_session(fixture, close_execution)
    after_close = date(2024, 1, 8)
    _soxx_add_session(fixture, after_close)
    signal_row = source["option_market_inputs"][2]
    campaign = f"soxx-put-credit-{EXECUTE.isoformat()}"
    signal_row["management"] = {
        "action": "close_spread", "campaign_id": campaign,
        "source_id": "synthetic-management-close", "available_at": f"{close_signal.isoformat()}T20:59:00Z",
    }
    execution_row = source["option_market_inputs"][-2]
    execution_row["positions"] = deepcopy(signal_row["positions"])
    execution_row["quotes"][0]["ask"] = 4.0
    execution_row["quotes"][1]["bid"] = 0.5
    execution_row["soxx_trend"]["positive"] = False
    fixture["input"]["option_market_inputs"][-1]["positions"] = []
    fixture["input"]["option_market_inputs"][-1]["soxx_trend"]["positive"] = False
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_soxl_targets(monkeypatch, request.calendar, {day: 0.0 for day in request.calendar[:-1]})

    replay = replay_optimized_strategy(request)
    closed = replay.points[-2]
    assert closed.option_positions == ()
    assert closed.restricted_cash == pytest.approx(0.0)
    assert closed.cash == pytest.approx(99_850.0)
    assert closed.nav == pytest.approx(99_850.0)
    assert sorted(event.amount for event in closed.events if event.event_type == "option_trade") == [-400.0, 50.0]
    assert [event.amount for event in closed.events if event.event_type == "collateral_change"] == [-800.0]
    assert replay.points[-1].option_positions == ()
    assert not any(event.event_type in {"option_trade", "option_settlement"} for event in replay.points[-1].events)

    store = PerformanceStore(local_root=tmp_path / "soxx-put-credit-close-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="soxx-put-credit-close")
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert ledger.days[-2].restricted_cash == pytest.approx(0.0)
    assert ledger.days[-2].cash == pytest.approx(99_850.0)
    assert not any(mark.option_underlying == "SOXX" for mark in ledger.days[-2].positions)
    assert not any(event.event_type in {"option_trade", "option_settlement"} for event in ledger.days[-1].events)


@pytest.mark.parametrize(
    ("spot", "expected_cash", "expected_shares", "settlement_cashflow"),
    [(92.0, 100_200.0, 0.0, 0.0), (87.0, 91_000.0, 100.0, -9_200.0),
     (75.0, 99_200.0, 0.0, -1_000.0)],
)
def test_soxx_put_credit_expiry_settles_spread_and_reads_back(
    tmp_path: Path, spot: float, expected_cash: float, expected_shares: float,
    settlement_cashflow: float, monkeypatch,
) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / f"soxx-put-credit-expiry-{spot}.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    expiration = source["calendar"][-1]
    _soxx_restore_open_spread(source, expiration)
    for row in source["prices"]:
        if row["session"] == expiration and row["symbol"] == "SOXX":
            row.update(open=spot, high=spot, low=spot, close=spot)
    _soxx_add_session(fixture, date.fromisoformat(expiration) + timedelta(days=1))
    source["option_market_inputs"][-1]["positions"] = []
    source["option_market_inputs"][-1]["quotes"] = []
    source["option_market_inputs"][-1]["soxx_trend"]["positive"] = False
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_soxl_targets(monkeypatch, request.calendar, {day: 0.0 for day in request.calendar[:-1]})

    replay = replay_optimized_strategy(request)
    settled = replay.points[-2]
    assert settled.option_positions == ()
    assert settled.cash == pytest.approx(expected_cash)
    assert dict(settled.holdings)["SOXX"] == pytest.approx(expected_shares)
    assert settled.option_settlement_cashflow == pytest.approx(settlement_cashflow)
    assert settled.restricted_cash == pytest.approx(0.0)
    assert [event.event_type for event in settled.events].count("option_settlement") == 1
    assert [event.amount for event in settled.events if event.event_type == "collateral_change"] == [-800.0]
    assert not any(event.event_type == "option_settlement" for event in replay.points[-1].events)
    assert replay.points[-1].option_positions == ()

    store = PerformanceStore(local_root=tmp_path / f"soxx-expiry-store-{spot}", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id=f"soxx-expiry-{spot}")
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert ledger.days[-2].option_settlement_cashflow == pytest.approx(settlement_cashflow)
    assert ledger.days[-2].restricted_cash == pytest.approx(0.0)
    assert sum(mark.quantity for mark in ledger.days[-2].positions if mark.symbol == "SOXX") == pytest.approx(expected_shares)
    assert not any(event.event_type == "option_settlement" for event in ledger.days[-1].events)


def test_soxx_put_credit_close_requires_causal_management_and_old_leg_quotes(tmp_path: Path) -> None:
    path = tmp_path / "soxx-close-causality.json"
    fixture = _soxx_credit_fixture(path)
    _soxx_add_session(fixture, date(2024, 1, 5))
    signal_row = fixture["input"]["option_market_inputs"][2]
    signal_row["management"] = {
        "action": "close_spread", "campaign_id": f"soxx-put-credit-{EXECUTE.isoformat()}",
        "source_id": "synthetic-close", "available_at": "2024-01-04T21:01:00Z",
    }
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_INPUT_NOT_AVAILABLE"):
        replay_optimized_strategy(request)

    fixture = _soxx_credit_fixture(path)
    _soxx_add_session(fixture, date(2024, 1, 5))
    signal_row = fixture["input"]["option_market_inputs"][2]
    signal_row["management"] = {
        "action": "close_spread", "campaign_id": f"soxx-put-credit-{EXECUTE.isoformat()}",
        "source_id": "synthetic-close", "available_at": "2024-01-04T20:59:00Z",
    }
    fixture["input"]["option_market_inputs"][-1]["quotes"] = [
        quote for quote in fixture["input"]["option_market_inputs"][-1]["quotes"]
        if quote["contract_id"] != "SOXX-P-82"
    ]
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_QUOTE_MISSING"):
        replay_optimized_strategy(request)

    fixture = _soxx_credit_fixture(path)
    _soxx_add_session(fixture, date(2024, 1, 5))
    fixture["input"]["option_market_inputs"][2]["management"] = {
        "action": "close_spread", "campaign_id": f"soxx-put-credit-{EXECUTE.isoformat()}",
        "available_at": "2024-01-04T20:59:00Z",
    }
    fixture["identity"] = _build_soxl_option_v2_identity(fixture["input"])
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_MANAGEMENT_INVALID"):
        replay_optimized_strategy(request)


def test_soxx_put_credit_expiry_rejects_missing_spot_and_duplicate_or_early_assignment(tmp_path: Path) -> None:
    path = tmp_path / "soxx-expiry-rejections.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    expiration = source["calendar"][-1]
    _soxx_restore_open_spread(source, expiration)
    source["prices"] = [
        row for row in source["prices"]
        if not (row["session"] == expiration and row["symbol"] == "SOXX")
    ]
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="INPUT_GAP"):
        replay_optimized_strategy(request)

    for event_session in ("2024-01-03", expiration):
        fixture = _soxx_credit_fixture(path)
        source = fixture["input"]
        expiration = source["calendar"][-1]
        _soxx_restore_open_spread(source, expiration)
        source["ledger_events"] = {
            session: {"declared_event_ids": [], "events": []}
            for session in source["calendar"][1:]
        }
        source["ledger_events"][event_session] = {
            "declared_event_ids": ["external-settlement"],
            "events": [{
                "event_id": "external-settlement", "event_type": "option_settlement",
                "symbol": "SOXX", "settlement_price": 87.0,
            }],
        }
        fixture["identity"] = _build_soxl_option_v2_identity(source)
        path.write_text(json.dumps(fixture), encoding="utf-8")
        _, request = load_local_member_fixture(path)
        with pytest.raises(OptimizedStrategyReplayError, match="OPTION_SETTLEMENT_DUPLICATE"):
            replay_optimized_strategy(request)


@pytest.mark.parametrize(("cash", "spot"), [(8_000.0, 87.0), (9_000.0, 75.0)])
def test_soxx_put_credit_expiry_rejects_unfunded_short_assignment(
    tmp_path: Path, monkeypatch, cash: float, spot: float
) -> None:
    from types import SimpleNamespace

    from us_equity_strategies.research import optimized_strategy_replay as replay_module

    path = tmp_path / f"soxx-expiry-insufficient-cash-{cash}-{spot}.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    expiration = source["calendar"][-1]
    _soxx_restore_open_spread(source, expiration)
    source["initial_cash"] = cash
    source["initial_quantities"]["SOXX"] = 1_000.0
    for row in source["prices"]:
        if row["session"] == expiration and row["symbol"] == "SOXX":
            row.update(open=100.0, high=max(100.0, spot), low=spot, close=spot)
    fixture["identity"] = _build_soxl_option_v2_identity(source, fill_price_field="open")
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    manifest, _ = replay_module._PROFILES["soxl_soxx_trend_income"]

    def hold_shares(context):
        return SimpleNamespace(
            positions=tuple(SimpleNamespace(symbol=symbol, target_value=100_000.0 if symbol == "SOXX" else 0.0)
                            for symbol in SOXL_SYMBOLS),
            risk_flags=(), diagnostics={
                "signal_date": context.as_of,
                "effective_date": (date.fromisoformat(context.as_of) + timedelta(days=1)).isoformat(),
                "execution_timing_contract": "next_trading_day",
                "signal_effective_after_trading_days": 1,
            },
        )

    monkeypatch.setitem(replay_module._PROFILES, "soxl_soxx_trend_income", (manifest, hold_shares))
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_EXERCISE_CASH_INSUFFICIENT"):
        replay_optimized_strategy(request)


def test_soxx_put_credit_expiry_marks_core_trade_before_settlement_in_qpk(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "soxx-expiry-core-trade.json"
    fixture = _soxx_credit_fixture(path)
    source = fixture["input"]
    expiration = source["calendar"][-1]
    _soxx_restore_open_spread(source, expiration)
    for row in source["prices"]:
        if row["session"] == expiration and row["symbol"] == "SOXX":
            row.update(open=87.0, high=87.0, low=87.0, close=87.0)
    _soxx_add_session(fixture, date.fromisoformat(expiration) + timedelta(days=1))
    source["option_market_inputs"][-1]["positions"] = []
    source["option_market_inputs"][-1]["quotes"] = []
    source["option_market_inputs"][-1]["soxx_trend"]["positive"] = False
    fixture["identity"] = _build_soxl_option_v2_identity(source)
    path.write_text(json.dumps(fixture), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_soxl_targets(
        monkeypatch, request.calendar, {SIGNAL: 0.0, EXECUTE: 1_000.0, date(2024, 1, 4): 0.0}
    )
    from us_equity_strategies.research.optimized_strategy_replay import replay_optimized_strategy
    replay = replay_optimized_strategy(request)
    expiry_day = replay.points[-2]
    assert expiry_day.equity_trade_phase == "before_settlement"
    assert expiry_day.equity_trade_quantities == {"SOXL": pytest.approx(20.0)}
    assert expiry_day.option_settlement_cashflow == pytest.approx(-9_200.0)

    store = PerformanceStore(local_root=tmp_path / "soxx-expiry-core-trade-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="soxx-expiry-core-trade")
    ledger = store.load_research_ledger(
        "us_equity", "soxl_soxx_trend_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    stored = ledger.days[-2]
    assert stored.equity_trade_phase == "before_settlement"
    assert stored.equity_trade_quantities == {"SOXL": pytest.approx(20.0)}
    assert stored.option_settlement_cashflow == pytest.approx(-9_200.0)


def test_tqqq_leaps_partial_principal_recovery_uses_held_contract_bid(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-partial-close.json"
    old_contract = "TQQQ-2026-01-02-C-80"
    new_contract = "TQQQ-2026-01-02-C-100"
    campaign = "legacy-campaign"
    old_lots = tuple(_tqqq_option_position(old_contract, campaign, i) for i in (1, 2, 3))
    remaining = tuple(
        _tqqq_option_position(old_contract, campaign, i, recovered_proceeds=9_000.0)
        for i in (2, 3)
    )
    quotes = tuple(
        (
            _tqqq_option_quote(old_contract, strike=80.0, bid=90.0, ask=92.0, delta=0.95),
            _tqqq_option_quote(new_contract, strike=100.0, bid=30.0, ask=30.0, delta=0.75),
        )
        for _ in range(3)
    )
    _tqqq_option_fixture_file(
        path,
        initial_cash=273_000.0,
        positions_by_day=(old_lots, old_lots, remaining),
        quotes_by_day=quotes,
    )
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)

    replay = replay_optimized_strategy(request)
    closed_day = replay.points[1]
    sale_events = [event for event in closed_day.events if event.event_type == "option_trade" and event.amount > 0]
    assert len(sale_events) == 1
    assert sale_events[0].symbol.startswith(f"{campaign}-U")
    assert sale_events[0].amount == pytest.approx(9_000.0)
    assert closed_day.cash == pytest.approx(282_000.0)
    assert len(closed_day.option_positions) == 2
    assert closed_day.nav == pytest.approx(300_000.0)
    assert all(mark.option_underlying == "TQQQ" for mark in closed_day.option_positions)


def test_tqqq_leaps_rejects_future_option_quote_and_unbound_change(tmp_path: Path) -> None:
    path = tmp_path / "tqqq-leaps-future-quote.json"
    payload = _tqqq_option_fixture_file(path)
    payload["input"]["option_market_inputs"][0]["quotes"][0]["available_at"] = "2099-01-01T00:00:00Z"
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_INPUT_NOT_AVAILABLE"):
        replay_optimized_strategy(request)

    payload = _tqqq_option_fixture_file(path)
    payload["input"]["option_market_inputs"][0]["quotes"][0]["bid"] = 31.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LocalMemberInputError, match="LOCAL_MEMBER_INPUT_INVALID"):
        load_local_member_fixture(path)


def test_tqqq_leaps_terminal_cutoff_binds_session_and_quote_availability(tmp_path: Path) -> None:
    path = tmp_path / "tqqq-leaps-terminal-cutoff.json"
    payload = _tqqq_option_fixture_file(path)
    terminal = payload["input"]["option_market_inputs"][-1]
    terminal["decision_at"] = "2099-01-01T00:00:00Z"
    for quote in terminal["quotes"]:
        quote["available_at"] = "2099-01-01T00:00:00Z"
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_INPUT_SESSION_MISMATCH"):
        replay_optimized_strategy(request)


def test_tqqq_leaps_negative_momentum_blocks_new_entry(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-negative-momentum.json"
    payload = _tqqq_option_fixture_file(path, positions_by_day=((), (), ()))
    payload["input"]["option_market_inputs"][0]["qqq_indicator"]["momentum_63d"] = -0.01
    _rebind_input_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    replay = replay_optimized_strategy(request)
    assert replay.points[1].option_positions == ()


def test_tqqq_leaps_zero_bid_marks_existing_lots_without_selling(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-zero-bid.json"
    old_contract = "TQQQ-2026-01-02-C-80"
    campaign = "zero-bid-campaign"
    lots = tuple(_tqqq_option_position(old_contract, campaign, index) for index in (1, 2, 3))
    quotes = tuple((
        _tqqq_option_quote(old_contract, strike=80.0, bid=0.0, ask=30.0, delta=0.75),
    ) for _ in range(3))
    _tqqq_option_fixture_file(path, positions_by_day=(lots, lots, lots), quotes_by_day=quotes)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    replay = replay_optimized_strategy(request)
    assert len(replay.points[1].option_positions) == 3
    assert all(mark.valuation == 0.0 for mark in replay.points[1].option_positions)
    assert not any(event.event_type == "option_trade" for event in replay.points[1].events)


def test_tqqq_leaps_new_candidate_rejects_zero_bid_and_keeps_valid_spread() -> None:
    from us_equity_strategies.research.optimized_strategy_replay import _tqqq_leaps_candidate

    zero_bid = _tqqq_option_quote("zero-bid", strike=90.0, bid=0.0, ask=30.0)
    valid = _tqqq_option_quote("valid", strike=100.0, bid=29.0, ask=31.0)
    boundary = _tqqq_option_quote("boundary", strike=100.0, bid=90.0, ask=110.0)
    too_wide = _tqqq_option_quote("too-wide", strike=100.0, bid=89.0, ask=111.0)
    config = {"option_growth_overlay_max_bid_ask_spread_ratio": 0.12}

    assert _tqqq_leaps_candidate({"zero-bid": zero_bid}, SIGNAL, config) is None
    assert _tqqq_leaps_candidate({"zero-bid": zero_bid, "valid": valid}, SIGNAL, config) == valid
    config["option_growth_overlay_max_bid_ask_spread_ratio"] = 0.2
    assert _tqqq_leaps_candidate({"boundary": boundary}, SIGNAL, config) == boundary
    assert _tqqq_leaps_candidate({"too-wide": too_wide}, SIGNAL, config) is None


def test_tqqq_leaps_candidate_matches_recipe_mid_spread_and_dte_priority(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-candidate-priority.json"
    target = _tqqq_option_quote("TQQQ-2026-01-02-C-100", strike=100.0, bid=29.0, ask=31.0, delta=0.74)
    closer_delta = _tqqq_option_quote("closer-delta", strike=99.0, bid=29.0, ask=31.0, delta=0.75)
    closer_delta["expiration"] = "2025-07-26"
    closer_dte = _tqqq_option_quote("closer-dte", strike=98.0, bid=26.0, ask=34.0, delta=0.75)
    quotes = tuple((target, closer_delta, closer_dte) for _ in range(3))
    _tqqq_option_fixture_file(path, quotes_by_day=quotes)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    replay = replay_optimized_strategy(request)
    assert replay.points[1].option_positions
    assert replay.points[1].option_positions[0].option_strike == 100.0


@pytest.mark.parametrize("spot", [90.0, 100.0])
def test_tqqq_leaps_otm_and_atm_expire_without_delivery(tmp_path: Path, monkeypatch, spot: float) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / f"tqqq-expiry-lapse-{spot}.json"
    _tqqq_expiry_fixture(path, spot=spot)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    replay = replay_optimized_strategy(request)
    expiry = replay.points[-1]
    assert expiry.option_positions == ()
    assert dict(expiry.holdings)["TQQQ"] == 0.0
    assert expiry.option_settlement_cashflow == 0.0
    assert len([event for event in expiry.events if event.event_type == "option_settlement"]) == 1

    store = PerformanceStore(local_root=tmp_path / "expiry-lapse-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id=f"expiry-lapse-{spot}")
    ledger = store.load_research_ledger(
        "us_equity", "tqqq_growth_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert ledger.days[-1].option_settlement_cashflow == 0.0
    assert ledger.days[-1].events[-1].event_type == "option_settlement"


def test_tqqq_leaps_itm_expiry_and_same_day_core_trade_read_back(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "tqqq-expiry-exercise.json"
    _tqqq_expiry_fixture(path, spot=110.0)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(
        monkeypatch, request.calendar,
        target_values={EXECUTE: {"QQQM": 1_000.0}},
    )
    replay = replay_optimized_strategy(request)
    expiry = replay.points[-1]
    assert expiry.option_settlement_cashflow == pytest.approx(-10_000.0)
    assert dict(expiry.holdings)["TQQQ"] == pytest.approx(100.0)
    assert expiry.equity_trade_phase == "before_settlement"
    assert expiry.equity_trade_quantities == {"QQQM": pytest.approx(10.0)}
    assert expiry.equity_trade_cashflow == pytest.approx(-1_000.0)
    assert expiry.fees == pytest.approx(1.0)
    assert expiry.cash == pytest.approx(88_999.0)

    store = PerformanceStore(local_root=tmp_path / "expiry-exercise-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="expiry-exercise")
    ledger = store.load_research_ledger(
        "us_equity", "tqqq_growth_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    day = ledger.days[-1]
    assert day.option_settlement_cashflow == pytest.approx(-10_000.0)
    assert day.equity_trade_phase == "before_settlement"
    assert day.equity_trade_quantities == {"QQQM": pytest.approx(10.0)}
    assert day.cash == pytest.approx(expiry.cash)


def test_tqqq_leaps_itm_expiry_rejects_insufficient_cash(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-expiry-insufficient-cash.json"
    _tqqq_expiry_fixture(path, spot=110.0, initial_cash=5_000.0)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_EXERCISE_CASH_INSUFFICIENT"):
        replay_optimized_strategy(request)


def test_tqqq_leaps_rejects_expiry_date_missing_from_calendar(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-expiry-date-missing.json"
    payload = _tqqq_expiry_fixture(path, spot=110.0)
    omitted = payload["input"]["calendar"][-1]
    replacement = "2024-01-05"
    payload["input"]["calendar"][-1] = replacement
    for bar in payload["input"]["prices"]:
        if bar["session"] == omitted:
            bar["session"] = replacement
            bar["available_at"] = f"{replacement}T20:00:00Z"
    for bar in payload["input"]["benchmark_bars"]:
        if bar["session"] == omitted:
            bar["session"] = replacement
            bar["available_at"] = f"{replacement}T20:00:00Z"
    last = payload["input"]["option_market_inputs"][-1]
    last["session"] = replacement
    last["decision_at"] = f"{replacement}T21:00:00Z"
    last["qqq_indicator"]["available_at"] = f"{replacement}T20:59:00Z"
    last["qqq_indicator"]["source_id"] = f"synthetic-qqq-indicator:{replacement}"
    last["positions_source"]["available_at"] = f"{replacement}T20:59:00Z"
    last["positions_source"]["source_id"] = f"synthetic-option-position-state:{replacement}"
    last["quotes"] = []
    payload["identity"] = _build_tqqq_v2_identity(payload["input"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_EXPIRATION_SESSION_MISSING"):
        replay_optimized_strategy(request)


def test_tqqq_leaps_roll_uses_old_bid_new_ask_and_round_trips_qpk(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "tqqq-leaps-roll.json"
    payload = _tqqq_roll_fixture(path)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(
        monkeypatch, request.calendar,
        target_values={SIGNAL: {"QQQM": 1_000.0}},
    )

    replay = replay_optimized_strategy(request)
    signal_point, execution_point = replay.points[:2]
    old_ids = {lot["ledger_position_id"] for lot in payload["input"]["option_market_inputs"][0]["positions"]}
    new_ids = {lot["ledger_position_id"] for lot in payload["input"]["option_market_inputs"][2]["positions"]}
    assert {mark.symbol for mark in signal_point.option_positions} == old_ids
    assert {mark.symbol for mark in execution_point.option_positions} == new_ids
    assert old_ids.isdisjoint(new_ids)
    assert execution_point.cash == pytest.approx(317_000.0)
    assert dict(execution_point.holdings)["QQQM"] == pytest.approx(10.0)
    assert execution_point.equity_trade_cashflow == pytest.approx(-1_000.0)
    assert sum(mark.valuation for mark in execution_point.option_positions) == pytest.approx(8_700.0)
    assert execution_point.nav == pytest.approx(326_700.0)
    events = {event.symbol: event.amount for event in execution_point.events if event.event_type == "option_trade"}
    assert set(events) == old_ids | new_ids
    assert all(events[lot_id] == pytest.approx(9_000.0) for lot_id in old_ids)
    assert all(events[lot_id] == pytest.approx(-3_000.0) for lot_id in new_ids)
    assert replay.points == replay_optimized_strategy(request).points

    prefix_payload = deepcopy(payload)
    prefix_input = prefix_payload["input"]
    prefix_dates = {item.isoformat() for item in request.calendar[:2]}
    prefix_input["calendar"] = prefix_input["calendar"][:2]
    prefix_input["prices"] = [bar for bar in prefix_input["prices"] if bar["session"] in prefix_dates]
    prefix_end = date.fromisoformat(prefix_input["calendar"][-1])
    prefix_input["benchmark_bars"] = [
        bar for bar in prefix_input["benchmark_bars"]
        if date.fromisoformat(bar["session"]) <= prefix_end
    ]
    prefix_signals = set(prefix_input["calendar"][:-1])
    prefix_input["state_inputs"] = [row for row in prefix_input["state_inputs"] if row["signal_date"] in prefix_signals]
    prefix_input["option_market_inputs"] = prefix_input["option_market_inputs"][:2]
    prefix_payload["identity"] = _build_tqqq_v2_identity(prefix_input)
    prefix_path = tmp_path / "tqqq-leaps-roll-prefix.json"
    prefix_path.write_text(json.dumps(prefix_payload), encoding="utf-8")
    _, prefix_request = load_local_member_fixture(prefix_path)
    _install_fixed_tqqq_targets(
        monkeypatch, prefix_request.calendar,
        target_values={SIGNAL: {"QQQM": 1_000.0}},
    )
    assert replay_optimized_strategy(prefix_request).points == replay.points[:2]

    _install_fixed_tqqq_targets(
        monkeypatch, request.calendar,
        target_values={SIGNAL: {"QQQM": 1_000.0}},
    )
    store = PerformanceStore(local_root=tmp_path / "tqqq-leaps-roll-store", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="tqqq-leaps-roll")
    ledger = store.load_research_ledger(
        "us_equity", "tqqq_growth_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None
    assert {mark.symbol for mark in ledger.days[0].positions if mark.option_underlying} == new_ids
    ledger_option_trades = {
        event.symbol: event.amount for event in ledger.days[0].events if event.event_type == "option_trade"
    }
    assert ledger_option_trades == pytest.approx(events)
    assert ledger.days[0].cash == pytest.approx(execution_point.cash)
    assert ledger.days[0].nav == pytest.approx(execution_point.nav)
    assert ledger.days[0].equity_trade_cashflow == pytest.approx(-1_000.0)
    assert ledger.days[0].trade_net_cashflow == pytest.approx(17_000.0)


def test_tqqq_leaps_roll_missing_replacement_quote_holds_old_lots(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-roll-missing-replacement.json"
    payload = _tqqq_roll_fixture(path)
    option_rows = payload["input"]["option_market_inputs"]
    option_rows[1]["quotes"] = [quote for quote in option_rows[1]["quotes"] if quote["contract_id"].startswith("TQQQ-2024")]
    option_rows[2]["positions"] = deepcopy(option_rows[1]["positions"])
    payload["identity"] = _build_tqqq_v2_identity(payload["input"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)

    replay = replay_optimized_strategy(request)
    old_ids = {lot["ledger_position_id"] for lot in option_rows[1]["positions"]}
    assert {mark.symbol for mark in replay.points[1].option_positions} == old_ids
    assert not any(event.event_type == "option_trade" for event in replay.points[1].events)


def test_tqqq_leaps_roll_untradeable_replacement_holds_old_lots(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-roll-wide-spread.json"
    _tqqq_roll_fixture(path, new_execution_ask=40.0)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)

    replay = replay_optimized_strategy(request)
    old_ids = {lot["ledger_position_id"] for lot in request.option_market_inputs[0]["positions"]}
    assert {mark.symbol for mark in replay.points[1].option_positions} == old_ids
    assert not any(event.event_type == "option_trade" for event in replay.points[1].events)


def test_tqqq_leaps_roll_missing_old_quote_fails_closed(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-roll-missing-old.json"
    payload = _tqqq_roll_fixture(path)
    payload["input"]["option_market_inputs"][1]["quotes"] = [
        quote for quote in payload["input"]["option_market_inputs"][1]["quotes"]
        if not quote["contract_id"].startswith("TQQQ-2024")
    ]
    payload["identity"] = _build_tqqq_v2_identity(payload["input"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_QUOTE_MISSING"):
        replay_optimized_strategy(request)


def test_tqqq_leaps_roll_gate_failure_marks_old_campaign_without_forced_close(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-leaps-roll-gate-failure.json"
    _tqqq_roll_fixture(path, gate_route="risk_off", old_execution_bid=50.0)
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)

    replay = replay_optimized_strategy(request)
    old_ids = {lot["ledger_position_id"] for lot in request.option_market_inputs[0]["positions"]}
    for point in replay.points:
        assert {mark.symbol for mark in point.option_positions} == old_ids
        assert not any(event.event_type == "option_trade" for event in point.events)


def test_tqqq_leaps_roll_quote_change_changes_input_and_run_identity(tmp_path: Path, monkeypatch) -> None:
    paths = (tmp_path / "roll-a.json", tmp_path / "roll-b.json")
    requests = []
    for path, ask in zip(paths, (30.0, 31.0), strict=True):
        _tqqq_roll_fixture(path, new_execution_ask=ask)
        _, request = load_local_member_fixture(path)
        requests.append(request)
    _install_fixed_tqqq_targets(monkeypatch, requests[0].calendar)
    replay_a = replay_optimized_strategy(requests[0])
    _install_fixed_tqqq_targets(monkeypatch, requests[1].calendar)
    replay_b = replay_optimized_strategy(requests[1])
    assert requests[0].research_identity["input_sha256"] != requests[1].research_identity["input_sha256"]
    assert replay_a.backtest.run_id != replay_b.backtest.run_id


def test_tqqq_leaps_dte_one_skips_roll_and_settles_on_expiration_day(tmp_path: Path, monkeypatch) -> None:
    from us_equity_strategies.research.optimized_strategy_replay import persist_optimized_strategy_trial

    path = tmp_path / "tqqq-leaps-roll-expiration-next-session.json"
    payload = _tqqq_roll_fixture(path)
    expiration = EXECUTE.isoformat()
    old_contract = "TQQQ-2024-12-31-C-80"
    option_rows = payload["input"]["option_market_inputs"]
    for row in option_rows:
        for quote in row["quotes"]:
            if quote["contract_id"] == old_contract:
                quote["expiration"] = expiration
    option_rows[-1]["quotes"] = [
        quote for quote in option_rows[-1]["quotes"] if quote["contract_id"] != old_contract
    ]
    option_rows[-1]["positions"] = []
    for row in payload["input"]["state_inputs"]:
        if row["signal_date"] == expiration:
            row["state"]["market_regime_control"]["position_control"]["final_route"] = "risk_off"
    for bar in payload["input"]["prices"]:
        if bar["session"] == expiration and bar["symbol"] == "TQQQ":
            bar.update(open=85.0, high=85.0, low=85.0, close=85.0)
    payload["identity"] = _build_tqqq_v2_identity(payload["input"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)

    store = PerformanceStore(local_root=tmp_path / "tqqq-expiry-after-roll-signal", cloud_bucket="")
    record = persist_optimized_strategy_trial(request, store, trial_id="tqqq-expiry-after-roll-signal")
    ledger = store.load_research_ledger(
        "us_equity", "tqqq_growth_income", record.trial_id, record.run_id, record.param_version
    )
    assert ledger is not None, (record.status, record.reason_code)
    expiry_day = ledger.days[0]
    assert [event.event_type for event in expiry_day.events].count("option_settlement") == 1
    assert not any(event.event_type == "option_trade" for event in expiry_day.events)
    assert expiry_day.option_settlement_cashflow == pytest.approx(-24_000.0)
    assert next(mark for mark in expiry_day.positions if mark.symbol == "TQQQ").quantity == pytest.approx(300.0)


def test_tqqq_leaps_rejects_contract_terms_drift_before_expiry(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-expiry-contract-terms-drift.json"
    payload = _tqqq_expiry_fixture(path, spot=110.0)
    payload["input"]["option_market_inputs"][-1]["quotes"][0]["strike"] = 120.0
    payload["identity"] = _build_tqqq_v2_identity(
        payload["input"],
        cost_source="SYNTHETIC_10BPS",
        cost_inputs={"commission_bps": 10, "slippage_bps": 0, "market_impact_bps": 0},
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)

    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_CONTRACT_TERMS_MISMATCH"):
        replay_optimized_strategy(request)


def test_tqqq_leaps_rejects_duplicate_expiry_settlement(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tqqq-expiry-duplicate-settlement.json"
    payload = _tqqq_expiry_fixture(path, spot=110.0)
    expiry_day = payload["input"]["calendar"][-1]
    payload["input"]["ledger_events"] = {
        payload["input"]["calendar"][-2]: {
            "declared_event_ids": [],
            "events": [],
        },
        expiry_day: {
            "declared_event_ids": ["duplicate-expiry"],
            "events": [{
                "event_id": "duplicate-expiry",
                "event_type": "option_settlement",
                "symbol": "TQQQ",
                "settlement_price": float(110.0),
            }],
        }
    }
    payload["identity"] = _build_tqqq_v2_identity(payload["input"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    _, request = load_local_member_fixture(path)
    _install_fixed_tqqq_targets(monkeypatch, request.calendar)
    with pytest.raises(OptimizedStrategyReplayError, match="OPTION_SETTLEMENT_DUPLICATE"):
        replay_optimized_strategy(request)


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
