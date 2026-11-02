"""The same offline contract applies to both baseline families."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from importlib import import_module
import builtins
import socket

import pytest


FAMILIES = ("nasdaq100_buy_and_hold", "sp500_buy_and_hold")
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def case(family):
    api = import_module("us_equity_strategies.buy_and_hold")
    candidate = "QQQ" if family == FAMILIES[0] else "SPY"
    instrument = api.InstrumentBinding(
        candidate=candidate, symbol=candidate, identity="synthetic-contract",
        mic="XNAS", currency="USD", quantity_step=D("1"),
        minimum_notional=D("1"), verified=True,
    )
    binding = api.PlanBinding(
        profile=family, instrument=instrument, account_ref="synthetic-account",
        mandate_revision="synthetic-mandate-v1", release="synthetic-release",
        owner_ref="synthetic-owner", environment="offline_preview",
    )
    mandate = api.Mandate(
        binding=binding, starts_at=NOW-timedelta(days=1),
        expires_at=NOW+timedelta(days=1), reserve=D("100"),
        principal_ceiling=D("10000"), preference="CAPITAL_PRESERVATION",
        active=True, dividend_policy="cash",
    )
    allowance = api.Allowance(
        binding=binding, contribution_ref="synthetic-contribution", revision=1,
        starts_at=NOW-timedelta(minutes=1), expires_at=NOW+timedelta(hours=1),
        final_amount=D("500"), spent=D("0"), reserved=D("0"),
        base_amount=D("1000"), applied_scale=D("0.50"), policy_version="buy_and_hold_funding.v1",
    )
    facts = api.PortfolioFacts(
        binding=binding, observed_at=NOW, valid_until=NOW+timedelta(minutes=1),
        reconciled=True, exclusive_ownership=True, unrelated_exposure=False,
        holdings_adopted=True, held_quantity=D("10"),
        settled_cash_after_reservations=D("1000"), account_risk_capital=D("20000"),
        risk_headroom=D("1000"), risk_approved=True, principal_spent_and_reserved=D("0"),
        contribution_ref="synthetic-contribution", allowance_revision=1,
    )
    quote = api.Quote(
        instrument=instrument, observed_at=NOW, valid_until=NOW+timedelta(minutes=1),
        mark_price=D("100"), limit_price=D("100"), fee_buffer=D("1"),
    )
    caps = api.Capabilities(binding=binding, verified=True, fractional_verified=False)
    return api, dict(family=family, mandate=mandate, allowance=allowance,
                     facts=facts, quote=quote, capabilities=caps, now=NOW)


def plan(inputs):
    api = import_module("us_equity_strategies.buy_and_hold")
    return api.plan_buy_and_hold(**inputs)


def blocked(decision):
    assert decision.diagnostics["status"] == "BLOCKED"
    assert decision.positions == decision.budgets == ()
    assert {"no_execute", "no_order"} <= set(decision.risk_flags)


@pytest.mark.parametrize("family", FAMILIES)
def test_missing_facts_fail_closed(family):
    _, inputs = case(family)
    for field in ("mandate", "facts", "quote", "capabilities", "now"):
        blocked(plan({**inputs, field: None}))


@pytest.mark.parametrize("family", FAMILIES)
def test_diagnostics_do_not_echo_untrusted_identifiers(family, capsys):
    _, inputs = case(family)
    marker = "synthetic-private-value"
    inputs["family"] = marker
    decision = plan(inputs)
    blocked(decision)
    assert marker not in repr(decision)
    assert capsys.readouterr() == ("", "")
    inputs["family"] = family
    inputs["facts"] = replace(inputs["facts"], binding=replace(
        inputs["facts"].binding, account_ref=marker))
    decision = plan(inputs)
    blocked(decision)
    assert marker not in repr(decision)


@pytest.mark.parametrize("family", FAMILIES)
def test_stale_or_future_snapshot_and_revision_fail_closed(family):
    _, inputs = case(family)
    for patch in (
        {"observed_at": NOW-timedelta(minutes=6)},
        {"observed_at": NOW+timedelta(seconds=1)},
        {"valid_until": NOW}, {"allowance_revision": 2},
        {"contribution_ref": "synthetic-other-contribution"},
    ):
        blocked(plan({**inputs, "facts": replace(inputs["facts"], **patch)}))


@pytest.mark.parametrize("family", FAMILIES)
def test_unknown_integrity_and_wrong_binding_fail_closed(family):
    _, inputs = case(family)
    for field, value in (
        ("reconciled", None), ("reconciled", "true"), ("exclusive_ownership", False),
        ("unrelated_exposure", True), ("holdings_adopted", False),
        ("account_risk_capital", D("0")), ("risk_approved", None), ("risk_approved", "APPROVE"),
    ):
        blocked(plan({**inputs, "facts": replace(inputs["facts"], **{field: value})}))
    for field in ("account_ref", "mandate_revision", "release", "owner_ref", "profile"):
        wrong = replace(inputs["facts"].binding, **{field: "synthetic-other"})
        blocked(plan({**inputs, "facts": replace(inputs["facts"], binding=wrong)}))


@pytest.mark.parametrize("family", FAMILIES)
def test_no_allowance_and_price_moves_never_sell(family):
    _, inputs = case(family)
    inputs["facts"] = replace(inputs["facts"], principal_spent_and_reserved=D("500"))
    for price in (D("10"), D("100"), D("1000")):
        quote = replace(inputs["quote"], mark_price=price, limit_price=price)
        for allowance in (None, replace(inputs["allowance"], spent=D("500"))):
            result = plan({**inputs, "allowance": allowance, "quote": quote})
            assert result.diagnostics["status"] == "HOLD"
            assert result.positions == result.budgets == ()
        result = plan({**inputs, "quote": quote})
        assert all(p.target_value >= float(price * D("10")) for p in result.positions)
        assert all(p.symbol == inputs["mandate"].binding.instrument.symbol for p in result.positions)


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("preference,scale", [
    ("CAPITAL_PRESERVATION", D("0.5")),
    ("BALANCED_COMPOUNDING", D("0.75")),
    ("GROWTH_COMPOUNDING", D("1")),
])
def test_tier_exactly_once_and_direct_final_not_rescaled(family, preference, scale):
    _, inputs = case(family)
    inputs["mandate"] = replace(inputs["mandate"], preference=preference)
    inputs["facts"] = replace(inputs["facts"], settled_cash_after_reservations=D("2000"))
    inputs["quote"] = replace(inputs["quote"], fee_buffer=D("0"))
    inputs["allowance"] = replace(inputs["allowance"], final_amount=D("1000")*scale, applied_scale=scale)
    derived = plan(inputs)
    assert derived.diagnostics["quantity"] == str((D("10")*scale)//1)
    inputs["allowance"] = replace(inputs["allowance"], base_amount=None, applied_scale=None, final_amount=D("800"))
    direct = plan(inputs)
    assert D(direct.diagnostics["quantity"]) == D("8")
    assert direct.budgets == ()
    # A previously scaled residual cannot be treated as a fresh base.
    inputs["allowance"] = replace(inputs["allowance"], base_amount=D("800"), applied_scale=scale, final_amount=D("399"))
    blocked(plan(inputs))


@pytest.mark.parametrize("family", FAMILIES)
def test_cash_allowance_principal_and_headroom_minimum_no_double_debit(family):
    _, inputs = case(family)
    inputs["allowance"] = replace(inputs["allowance"], spent=D("100"), reserved=D("100"))
    # Cash is already net of reservations and settled fills; never subtract them again.
    inputs["facts"] = replace(inputs["facts"], settled_cash_after_reservations=D("400"),
                              principal_spent_and_reserved=D("200"))
    assert D(plan(inputs).diagnostics["quantity"]) == D("2")
    inputs["quote"] = replace(inputs["quote"], fee_buffer=D("0"))
    assert D(plan(inputs).diagnostics["quantity"]) == D("3")
    for patch in (
        {"risk_headroom": D("100")},
        {"settled_cash_after_reservations": D("200")},
        {"principal_spent_and_reserved": D("9900")},
    ):
        result = plan({**inputs, "facts": replace(inputs["facts"], **patch)})
        assert D(result.diagnostics["quantity"]) == D("1")


@pytest.mark.parametrize("family", FAMILIES)
def test_whole_unit_defer_fraction_requires_verified_bound_capability(family):
    _, inputs = case(family)
    inputs["facts"] = replace(inputs["facts"], risk_headroom=D("99"))
    result = plan(inputs)
    assert result.diagnostics["status"] == "DEFER"
    assert result.positions == ()
    instrument = replace(inputs["mandate"].binding.instrument, quantity_step=D("0.01"))
    binding = replace(inputs["mandate"].binding, instrument=instrument)
    for field in ("mandate", "facts", "allowance", "capabilities"):
        inputs[field] = replace(inputs[field], binding=binding)
    inputs["quote"] = replace(inputs["quote"], instrument=instrument)
    blocked(plan(inputs))
    inputs["capabilities"] = replace(inputs["capabilities"], fractional_verified=True)
    result = plan(inputs)
    assert D(result.diagnostics["quantity"]) == D("0.98")
    assert D(result.diagnostics["maximum_spend"]) == D("99")
    inputs["capabilities"] = replace(inputs["capabilities"], verified="true")
    blocked(plan(inputs))


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("bad", [None, -1, True, 1.5, D("-1"), D("NaN"), D("Infinity")])
def test_invalid_money_never_becomes_available(family, bad):
    _, inputs = case(family)
    for field in ("reserve", "principal_ceiling"):
        blocked(plan({**inputs, "mandate": replace(inputs["mandate"], **{field: bad})}))
    for field in ("final_amount", "spent", "reserved"):
        blocked(plan({**inputs, "allowance": replace(inputs["allowance"], **{field: bad})}))
    for field in ("held_quantity", "risk_headroom", "settled_cash_after_reservations", "principal_spent_and_reserved"):
        blocked(plan({**inputs, "facts": replace(inputs["facts"], **{field: bad})}))
    for field in ("mark_price", "limit_price", "fee_buffer"):
        blocked(plan({**inputs, "quote": replace(inputs["quote"], **{field: bad})}))


@pytest.mark.parametrize("family", FAMILIES)
def test_expired_authority_live_and_unknown_tier_block(family):
    _, inputs = case(family)
    for patch in (
        {"expires_at": NOW}, {"starts_at": NOW+timedelta(seconds=1)},
        {"active": False}, {"active": "true"}, {"preference": "UNKNOWN"},
        {"dividend_policy": "automatic_reinvestment"},
    ):
        blocked(plan({**inputs, "mandate": replace(inputs["mandate"], **patch)}))
    for field in ("mandate", "allowance", "facts", "capabilities"):
        inputs[field] = replace(inputs[field], binding=replace(inputs[field].binding, environment="live"))
    blocked(plan(inputs))


@pytest.mark.parametrize("family", FAMILIES)
def test_allowance_and_quote_integrity(family):
    _, inputs = case(family)
    for patch in (
        {"expires_at": NOW}, {"revision": True}, {"revision": 0},
        {"spent": D("501")}, {"reserved": D("501")},
        {"policy_version": "unknown"}, {"contribution_ref": ""},
        {"applied_scale": D("0.75")}, {"spent": D("1")},
    ):
        blocked(plan({**inputs, "allowance": replace(inputs["allowance"], **patch)}))
    for patch in (
        {"observed_at": NOW+timedelta(seconds=1)},
        {"observed_at": NOW-timedelta(minutes=6)}, {"valid_until": NOW},
        {"mark_price": D("0")}, {"limit_price": D("99")},
        {"instrument": replace(inputs["quote"].instrument, identity="synthetic-other-contract")},
    ):
        blocked(plan({**inputs, "quote": replace(inputs["quote"], **patch)}))


@pytest.mark.parametrize("family", FAMILIES)
def test_repeated_previews_do_not_consume_or_replay_stale_facts(family):
    _, inputs = case(family)
    before = repr(inputs)
    first = plan(inputs)
    assert plan(inputs) == first
    assert repr(inputs) == before
    refreshed = {**inputs, "allowance": replace(inputs["allowance"], reserved=D("500"), revision=2),
                 "facts": replace(inputs["facts"], allowance_revision=2, principal_spent_and_reserved=D("500"))}
    assert plan(refreshed).diagnostics["status"] == "HOLD"
    blocked(plan({**inputs, "facts": refreshed["facts"]}))
    assert plan({**inputs, "now": NOW+timedelta(minutes=2)}).positions == ()
    assert first.diagnostics["no_order"] is True


@pytest.mark.parametrize("family", FAMILIES)
def test_planner_and_entrypoint_have_no_io(family, monkeypatch):
    api, inputs = case(family)
    from quant_platform_kit.common.strategy_contracts import StrategyContext
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected I/O")
    # Import first; then all evaluation happens with I/O forbidden.
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("os.getenv", forbidden)
    class NoClock(datetime):
        @classmethod
        def now(cls, *args, **kwargs):
            forbidden()
        @classmethod
        def utcnow(cls):
            forbidden()
    monkeypatch.setattr(api, "datetime", NoClock)
    import time
    monkeypatch.setattr(time, "time", forbidden)
    entrypoint = getattr(api, family+"_entrypoint")
    ctx = StrategyContext(
        as_of=NOW, portfolio=inputs["facts"], market_data={"quote": inputs["quote"]},
        state={"mandate": inputs["mandate"], "allowance": inputs["allowance"]},
        capabilities={"buy_and_hold": inputs["capabilities"]},
    )
    result = entrypoint.evaluate(ctx)
    assert result == plan(inputs)
    assert result.diagnostics["status"] == "BUY_PREVIEW"
    blocked(entrypoint.evaluate(StrategyContext(as_of=NOW)))
    blocked(entrypoint.evaluate(replace(ctx, as_of=NOW+timedelta(minutes=2))))
    blocked(entrypoint.evaluate(replace(ctx, as_of=NOW.replace(tzinfo=None))))
    other = next(profile for profile in FAMILIES if profile != family)
    blocked(getattr(api, other+"_entrypoint").evaluate(ctx))


@pytest.mark.parametrize("family", FAMILIES)
def test_lot_minimum_notional_and_fee_bounds(family):
    _, inputs = case(family)
    for step, minimum, expected in ((D("2"), D("1"), "BUY_PREVIEW"),
                                    (D("10"), D("1"), "DEFER"),
                                    (D("1"), D("500"), "DEFER")):
        instrument = replace(inputs["quote"].instrument, quantity_step=step, minimum_notional=minimum)
        changed = {**inputs, "quote": replace(inputs["quote"], instrument=instrument), **{
            field: replace(inputs[field], binding=replace(inputs[field].binding, instrument=instrument))
            for field in ("mandate", "facts", "allowance", "capabilities")}}
        result = plan(changed)
        assert result.diagnostics["status"] == expected
        if expected == "BUY_PREVIEW":
            assert D(result.diagnostics["quantity"]) == D("4")
            assert D(result.diagnostics["maximum_spend"]) == D("401")
    assert plan({**inputs, "quote": replace(inputs["quote"], fee_buffer=D("501"))}).positions == ()


@pytest.mark.parametrize("family", FAMILIES)
def test_preview_catalog_has_no_platform_or_rollout_activation(family):
    api, inputs = case(family)
    from us_equity_strategies.catalog import (
        get_strategy_definition, get_strategy_entrypoint, get_strategy_metadata,
        get_runtime_enabled_profiles, get_qmt_rollout_allowlist,
    )
    from us_equity_strategies.manifests.buy_and_hold import MANIFESTS
    from us_equity_strategies.runtime_adapters import get_platform_runtime_adapter
    from us_equity_strategies.runtime_allowlist import get_runtime_selectable_profiles
    definition = get_strategy_definition(family)
    assert definition.supported_platforms == frozenset()
    assert definition.default_config["selected_instrument"] is None
    assert definition.default_config["budgets"] == ()
    assert get_strategy_metadata(family).status == "preview_only"
    assert get_strategy_metadata(family).benchmark == ("QQQ" if family == FAMILIES[0] else "SPY")
    assert MANIFESTS[family] == get_strategy_entrypoint(family).manifest
    assert family not in get_runtime_enabled_profiles()
    assert family not in get_runtime_selectable_profiles()
    assert family not in get_qmt_rollout_allowlist()
    for platform in ("ibkr", "schwab", "longbridge", "firstrade", "qmt", "paper_signal", "manual"):
        with pytest.raises(ValueError):
            get_platform_runtime_adapter(family, platform_id=platform)
    assert getattr(api, family+"_entrypoint").manifest.profile == family


@pytest.mark.parametrize("family,candidate,symbol", [
    (FAMILIES[0], "QQQ", "QQQ"), (FAMILIES[0], "QQQM", "QQQM"),
    (FAMILIES[0], "QNDX", "QNDX"), (FAMILIES[1], "SPY", "SPY"),
    (FAMILIES[1], "VOO", "VOO"), (FAMILIES[1], "SPLG", "SPYM"),
])
def test_exact_selected_candidate_only_and_alias_requires_evidence(family, candidate, symbol):
    _, inputs = case(family)
    instrument = replace(inputs["quote"].instrument, candidate=candidate, symbol=symbol,
                         alias_verified=(candidate == "SPLG"))
    def bound(selected):
        return {**inputs, "quote": replace(inputs["quote"], instrument=selected), **{
            field: replace(inputs[field], binding=replace(inputs[field].binding, instrument=selected))
            for field in ("mandate", "facts", "allowance", "capabilities")}}
    result = plan(bound(instrument))
    assert [p.symbol for p in result.positions] == [symbol]
    assert result.budgets == ()
    for patch in ({"symbol": "SYNTHETIC"}, {"verified": False}, {"identity": ""},
                  {"mic": ""}, {"currency": "SGD"}, {"leverage": D("3")},
                  {"product_type": "cfd"}, {"quantity_step": D("0")},
                  {"quantity_step": D("1.5")}, {"quantity_step": D("0.3")}):
        blocked(plan(bound(replace(instrument, **patch))))
    if candidate == "SPLG":
        blocked(plan(bound(replace(instrument, alias_verified=False))))
        blocked(plan(bound(replace(instrument, symbol="SPLG"))))
    other = "SPY" if family == FAMILIES[0] else "QQQ"
    blocked(plan(bound(replace(instrument, candidate=other, symbol=other))))
