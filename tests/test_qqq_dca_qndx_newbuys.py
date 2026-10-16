from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from importlib.resources import files
import json

import pytest

from quant_platform_kit.common.capital_base import (
    CapitalBaseBinding,
    CapitalScope,
    CapitalValuationBasis,
    build_capital_base_snapshot,
)
from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.common.strategy_contracts import StrategyContext
from quant_platform_kit.risk.contracts import RuntimeRiskLimits
from us_equity_strategies import get_strategy_definition, get_strategy_entrypoint
from us_equity_strategies.manifests import get_strategy_manifest as get_manifest
from us_equity_strategies.strategies.nasdaq_sp500_smart_dca import build_rebalance_plan


VARIANT = "qqq-dca-qndx-newbuys-202610"
PROFILE = "nasdaq_sp500_smart_dca"
NOW = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)
NASDAQ_SYMBOLS = ("QQQ", "QQQM", "QNDX")


def _portfolio(values=None):
    values = values if values is not None else {"QQQ": 100.0, "QQQM": 200.0, "QNDX": 100.0}
    return PortfolioSnapshot(
        as_of=NOW, total_equity=10000.0, buying_power=5000.0,
        positions=tuple(Position(symbol=s, quantity=v / 10.0, market_value=v) for s, v in values.items()),
    )


def _no_history(*_args):
    raise AssertionError("ordinary DCA must not fetch history")


def _context(monkeypatch, *, values=None, caps=None, total_cap=1.0):
    from quant_platform_kit.risk import gate

    monkeypatch.setattr(gate, "_utc_now", lambda: NOW)
    portfolio = _portfolio(values)
    binding = CapitalBaseBinding(
        account_scope="synthetic", runtime_scope="synthetic", strategy_scope=PROFILE,
        target_currency="USD", capital_scope=CapitalScope.ACCOUNT,
        valuation_basis=CapitalValuationBasis.BROKER_ACCOUNT_NET_LIQUIDATION,
    )
    capital = build_capital_base_snapshot(
        portfolio, account_scope=binding.account_scope, runtime_scope=binding.runtime_scope,
        strategy_scope=PROFILE, reported_currency="USD", target_currency="USD",
        fx_rate_to_target=1.0, source_digest_sha256="a" * 64,
        capital_scope=binding.capital_scope, valuation_basis=binding.valuation_basis,
    )
    symbols = (*NASDAQ_SYMBOLS, "SPLG")
    limits = RuntimeRiskLimits(
        allowed_symbols=symbols, product_leverage_factors={s: 1 for s in symbols},
        nominal_caps=caps or {s: 0.50 for s in symbols},
        total_nominal_exposure_cap=total_cap, total_effective_exposure_cap=total_cap,
        max_positions=20,
    )
    return StrategyContext(
        as_of=NOW, portfolio=portfolio,
        runtime_config={"new_buy_execution_symbol": "QNDX"},
        capabilities={"capital_base": capital, "capital_base_binding": binding, "runtime_risk_limits": limits},
    )


@pytest.mark.parametrize("source", ["QQQ", "QQQM"])
def test_new_buys_route_to_qndx_without_selling_existing_holdings(source):
    portfolio = _portfolio()
    plan = build_rebalance_plan(
        _no_history, portfolio, as_of=NOW,
        trade_allocations={source: 0.5, "SPLG": 0.5},
        managed_symbols=(source, "SPLG"), new_buy_execution_symbol="QNDX",
    )
    assert plan["trade_allocations"] == {"QNDX": 0.5, "SPLG": 0.5}
    assert set(plan["managed_symbols"]) == {*NASDAQ_SYMBOLS, "SPLG"}
    assert plan["target_values"] == {"QQQ": 100.0, "QQQM": 200.0, "QNDX": 600.0, "SPLG": 500.0}
    for position in portfolio.positions:
        assert plan["target_values"][position.symbol] >= position.market_value
    assert plan["signal_symbols"] == ("QQQ", "SPY")
    assert plan["nasdaq_combined_target_value"] == 900.0


def test_default_off_preserves_original_dca_targets():
    portfolio = _portfolio({"QQQM": 1000.0, "SPLG": 1200.0})
    plan = build_rebalance_plan(_no_history, portfolio, as_of=NOW)
    assert plan["target_values"] == {"QQQM": 1500.0, "SPLG": 1700.0}
    assert plan["managed_symbols"] == ("QQQM", "SPLG")
    decision = get_strategy_entrypoint(PROFILE).evaluate(StrategyContext(as_of=NOW, portfolio=portfolio))
    assert {p.symbol: p.target_value for p in decision.positions} == plan["target_values"]
    for config in (get_strategy_definition(PROFILE).default_config, get_manifest(PROFILE).default_config):
        assert config["new_buy_execution_symbol"] is None
        assert config["trade_allocations"] == {"QQQM": 0.5, "SPLG": 0.5}


def test_combined_exposure_approves_and_returns_only_unaggregated_execution_targets(monkeypatch):
    decision = get_strategy_entrypoint(PROFILE).evaluate(_context(monkeypatch))
    assert decision.diagnostics["risk_gate"] == "APPROVE"
    assert {p.symbol: p.target_value for p in decision.positions} == {
        "QQQ": 100.0, "QQQM": 200.0, "QNDX": 600.0, "SPLG": 500.0,
    }
    assert decision.diagnostics["nasdaq_combined_target_value"] == 900.0


@pytest.mark.parametrize("capped_symbol", NASDAQ_SYMBOLS)
def test_caps_use_combined_old_and_new_exposure(monkeypatch, capped_symbol):
    caps = {s: 0.5 for s in (*NASDAQ_SYMBOLS, "SPLG")}
    caps[capped_symbol] = 0.10
    ctx = _context(monkeypatch, values={"QQQ": 250.0, "QQQM": 300.0, "QNDX": 200.0}, caps=caps)
    # Each ticker remains below $1,000; together they reach $1,250 after the buy.
    decision = get_strategy_entrypoint(PROFILE).evaluate(ctx)
    assert decision.positions == ()
    assert decision.diagnostics["risk_gate"] == "REJECT"
    assert decision.diagnostics["reason"] == "nominal_cap_exceeded"
    assert decision.diagnostics["nasdaq_combined_target_value"] == 1250.0


def test_combined_exposure_keeps_existing_total_cap(monkeypatch):
    decision = get_strategy_entrypoint(PROFILE).evaluate(_context(monkeypatch, total_cap=0.10))
    assert decision.positions == ()
    assert decision.diagnostics["reason"] == "total_nominal_cap_exceeded"


def test_qndx_stays_off_without_valid_capital_evidence(monkeypatch):
    ctx = _context(monkeypatch)
    decision = get_strategy_entrypoint(PROFILE).evaluate(replace(ctx, capabilities={}))
    assert decision.positions == ()
    assert decision.diagnostics["risk_gate"] == "REJECT"


@pytest.mark.parametrize("as_of,buying_power", [("2026-10-15", 5000.0), (NOW, 0.0)])
def test_switch_never_liquidates_when_dca_is_skipped(as_of, buying_power):
    plan = build_rebalance_plan(
        _no_history, replace(_portfolio(), buying_power=buying_power),
        as_of=as_of, new_buy_execution_symbol="QNDX",
    )
    assert plan["actionable"] is False
    assert plan["target_values"] == {}


def test_named_config_and_alias_require_explicit_opt_in():
    config = json.loads(files("us_equity_strategies").joinpath(f"configs/{VARIANT}.json").read_text())
    assert config == {"new_buy_execution_symbol": "QNDX", "managed_symbols": ["QQQ", "QQQM", "QNDX", "SPLG"]}
    assert get_strategy_definition(VARIANT).profile == PROFILE
    assert get_manifest(VARIANT).profile == PROFILE
    assert get_strategy_definition(VARIANT).default_config["new_buy_execution_symbol"] is None
    assert "QNDX" in get_manifest(VARIANT).description


def test_ibkr_runtime_adapter_loads_named_preset_only_when_selected(monkeypatch):
    from us_equity_strategies.runtime_adapters import get_platform_runtime_adapter
    from us_equity_strategies.strategies.nasdaq_sp500_smart_dca import QNDX_NEW_BUYS_CONFIG_PATH

    adapter = get_platform_runtime_adapter(PROFILE, platform_id="ibkr")
    assert adapter.runtime_parameter_loader(config_path=None) == {}
    config = adapter.runtime_parameter_loader(config_path=QNDX_NEW_BUYS_CONFIG_PATH)
    ctx = _context(monkeypatch)
    decision = get_strategy_entrypoint(PROFILE).evaluate(replace(ctx, runtime_config=config))
    assert decision.diagnostics["risk_gate"] == "APPROVE"
    assert {p.symbol: p.target_value for p in decision.positions}["QNDX"] == 600.0
    with pytest.raises(ValueError, match="Unsupported DCA config_path"):
        adapter.runtime_parameter_loader(config_path="package://unapproved/config.json")


def test_mapping_combines_all_new_nasdaq_allocations_and_keeps_qqq_signals():
    indicators = {s: {"close": 100.0, "sma200": 100.0} for s in ("QQQ", "SPY")}
    plan = build_rebalance_plan(
        _no_history, _portfolio(), as_of=NOW, new_buy_execution_symbol="QNDX",
        trade_allocations={"QQQ": 0.2, "QQQM": 0.2, "QNDX": 0.1, "SPLG": 0.5},
        smart_multiplier_enabled=True, technical_indicator_snapshot=indicators,
        max_investment_usd=800.0, cash_reserve_usd=4500.0,
    )
    assert plan["signal_symbols"] == ("QQQ", "SPY")
    assert plan["trade_allocations"] == {"QNDX": 0.5, "SPLG": 0.5}
    assert plan["planned_investment_usd"] == 500.0
    assert plan["target_values"]["QNDX"] == 350.0


@pytest.mark.parametrize("profile", ["tqqq_growth_income", "us_equity_combo", "us_equity_combo_core", "us_equity_combo_leveraged"])
def test_tqqq_and_rebalancing_combos_are_excluded(profile):
    config = get_strategy_entrypoint(profile).manifest.default_config
    assert "new_buy_execution_symbol" not in config
    assert "QNDX" not in repr(config)
    if profile == "tqqq_growth_income":
        from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan as tqqq_plan
        with pytest.raises(TypeError, match="new_buy_execution_symbol"):
            tqqq_plan(None, None, new_buy_execution_symbol="QNDX")
    else:
        from tests.test_mega_cap_leader_rotation import _mega_snapshot
        from us_equity_strategies.strategies import us_equity_combo, us_equity_combo_core, us_equity_combo_leveraged
        module = {"us_equity_combo": us_equity_combo, "us_equity_combo_core": us_equity_combo_core,
                  "us_equity_combo_leveraged": us_equity_combo_leveraged}[profile]
        baseline = module.compute_signals(_mega_snapshot(), current_holdings=set(), config=dict(config))
        assert "QNDX" not in baseline[0]
        if profile == "us_equity_combo_leveraged":
            supplied = module.compute_signals(_mega_snapshot(), current_holdings=set(), config={**config, "new_buy_execution_symbol": "QNDX"})
            assert supplied[0] == baseline[0]
        else:
            with pytest.raises(TypeError, match="new_buy_execution_symbol"):
                module.compute_signals(_mega_snapshot(), current_holdings=set(), config={**config, "new_buy_execution_symbol": "QNDX"})


@pytest.mark.parametrize("symbol", ["TQQQ", "SPLG", True])
def test_mapping_rejects_unapproved_execution_symbols(symbol):
    with pytest.raises(ValueError, match="new_buy_execution_symbol"):
        build_rebalance_plan(_no_history, _portfolio(), as_of=NOW, new_buy_execution_symbol=symbol)
