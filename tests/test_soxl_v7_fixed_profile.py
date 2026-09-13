from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pandas as pd
import pytest

from quant_platform_kit.common.capital_base import (
    CapitalScope,
    CapitalValuationBasis,
    CapitalBaseBinding,
    build_capital_base_snapshot,
)
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import StrategyContext
from quant_platform_kit.risk.contracts import CandidateRiskIdentity
from us_equity_strategies import get_runtime_enabled_profiles, get_strategy_entrypoint
from us_equity_strategies.entrypoints import (
    build_soxl_soxx_core_only_p2_v7_execution_decision,
)
from us_equity_strategies.v7_soxl_profile import (
    SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
    V7_CONFIG_SHA256,
    V7_FROZEN_RUNTIME_CONFIG,
    V7_UES_REVISION,
)


FIXED_RISK_NOW = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)


def _context(
    *,
    runtime_config=None,
    high_volatility: bool = False,
    trend_price: float = 80.0,
) -> StrategyContext:
    volatility = 0.60 if high_volatility else 0.20
    snapshot = PortfolioSnapshot(
        as_of=FIXED_RISK_NOW,
        total_equity=100_000.0,
        buying_power=100_000.0,
        positions=(),
        metadata={
            "market_currency_cash": 100_000.0,
            "observed_effective_exposure": 0.0,
        },
    )
    return StrategyContext(
        as_of="2026-09-11",
        market_data={
            "derived_indicators": {
                "soxl": {"price": 80.0, "ma_trend": 70.0},
                "soxx": {
                    "price": trend_price,
                    "ma_trend": 70.0,
                    "realized_volatility_10": volatility,
                    "realized_volatility_10_dynamic_threshold": 0.50,
                    "realized_volatility_10_dynamic_sample_count": 252.0,
                },
            }
        },
        portfolio=snapshot,
        runtime_config=dict(V7_FROZEN_RUNTIME_CONFIG if runtime_config is None else runtime_config),
    )


def _target_values(decision):
    return {position.symbol: position.target_value for position in decision.positions}


def _freeze_risk_clock(monkeypatch) -> None:
    from quant_platform_kit.risk import gate

    monkeypatch.setattr(gate, "_utc_now", lambda: FIXED_RISK_NOW)


def _execution_materials(snapshot: PortfolioSnapshot) -> dict[str, object]:
    runtime_revision = "d1ca798d880cd83965f3da5081850ca48a616d19"
    candidate = CandidateRiskIdentity(
        strategy_profile=SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
        account_mode="longbridge_paper_v1",
        strategy_revision=runtime_revision,
        runner_revision="b" * 40,
        config_sha256=V7_CONFIG_SHA256,
        input_manifest_sha256="c" * 64,
        authority_receipt_sha256="a" * 64,
    )
    mandate = {
        "mandate_id": "soxl_v7_synthetic_paper_v1",
        "mandate_version": "synthetic-2026-09-11.1",
        "authority_receipt_sha256": candidate.authority_receipt_sha256,
        "authority_scope": "PAPER",
        "strategy_profile": candidate.strategy_profile,
        "account_mode": candidate.account_mode,
        "strategy_revision": candidate.strategy_revision,
        "runner_revision": candidate.runner_revision,
        "config_sha256": candidate.config_sha256,
        "input_manifest_sha256": candidate.input_manifest_sha256,
        "candidate_identity_sha256": candidate.candidate_sha256,
        "effective_at": "2026-09-01T00:00:00Z",
        "expires_at": "2026-09-30T00:00:00Z",
        "max_snapshot_age_seconds": 300,
        "effective_exposure_cap": 1.0,
        "loss_budget": 0.01,
        "product_caps": {"SOXL": 0.50, "SOXX": 0.50, "BOXX": 1.0},
        "nominal_caps": {"SOXL": 0.50, "SOXX": 0.50, "BOXX": 1.0},
        "product_leverage_factors": {"SOXL": 3, "SOXX": 1, "BOXX": 1},
        "allowed_nonzero_assets": ["BOXX", "SOXL", "SOXX"],
        "source_revision": V7_UES_REVISION,
    }
    capital_binding = CapitalBaseBinding(
        account_scope="PAPER",
        runtime_scope="longbridge-quant-paper-service",
        strategy_scope=SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
        target_currency="USD",
        capital_scope=CapitalScope.ACCOUNT,
        valuation_basis=CapitalValuationBasis.BROKER_ACCOUNT_NET_LIQUIDATION,
    )
    capital_base = build_capital_base_snapshot(
        snapshot,
        account_scope=capital_binding.account_scope,
        runtime_scope=capital_binding.runtime_scope,
        strategy_scope=capital_binding.strategy_scope,
        reported_currency="USD",
        target_currency="USD",
        fx_rate_to_target=1.0,
        source_digest_sha256="d" * 64,
        capital_scope=capital_binding.capital_scope,
        valuation_basis=capital_binding.valuation_basis,
    )
    return {
        "candidate_risk_identity": candidate,
        "mandate_provenance": mandate,
        "capital_base": capital_base,
        "capital_base_binding": capital_binding,
        "strategy_release": {
            "release_id": "v7-synthetic-paper-20260913",
            "manifest_sha256": "e" * 64,
            "strategy_revision": runtime_revision,
            "config_sha256": V7_CONFIG_SHA256,
            "risk_policy_sha256": "f" * 64,
            "evidence_sha256": "1" * 64,
            "plugin_bundle_sha256": "2" * 64,
            "effective_session": "2026-09-14",
        },
    }


def test_v7_profile_is_findable_but_not_runtime_enabled() -> None:
    entrypoint = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE)

    assert entrypoint.manifest.profile == SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE
    assert entrypoint.manifest.default_config == V7_FROZEN_RUNTIME_CONFIG
    assert SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE not in get_runtime_enabled_profiles()


def test_v7_entrypoint_applies_frozen_full_weights_after_cash_reserve() -> None:
    decision = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(_context())

    assert _target_values(decision) == {
        "BOXX": 38_800.0,
        "SOXL": 33_950.0,
        "SOXX": 24_250.0,
    }
    assert decision.diagnostics["frozen_research_source"]["ues_revision"].startswith("07b164")
    assert decision.diagnostics["signal_effective_after_trading_days"] == 1
    assert decision.diagnostics["execution_annotations"]["signal_effective_after_trading_days"] == 1


def test_v7_research_entrypoint_remains_no_order() -> None:
    decision = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(_context())

    assert decision.diagnostics["no_order"] is True
    assert decision.diagnostics["execution_authorized"] is False


def test_v7_execution_builder_consumes_synthetic_risk_materials(monkeypatch) -> None:
    _freeze_risk_clock(monkeypatch)
    ctx = _context(high_volatility=True)
    materials = _execution_materials(ctx.portfolio)
    ctx = StrategyContext(
        as_of=ctx.as_of,
        market_data=ctx.market_data,
        portfolio=ctx.portfolio,
        runtime_config=ctx.runtime_config,
        state=materials,
        capabilities={
            "capital_base": materials["capital_base"],
            "capital_base_binding": materials["capital_base_binding"],
        },
    )

    decision = build_soxl_soxx_core_only_p2_v7_execution_decision(ctx)

    assert decision.positions
    assert decision.diagnostics["risk_gate"] == "APPROVE"
    assert decision.diagnostics["risk_assessment_outcome"] == "APPROVE"
    assert "no_order" not in decision.diagnostics
    assert "execution_authorized" not in decision.diagnostics


def test_v7_execution_builder_rejects_missing_materials() -> None:
    decision = build_soxl_soxx_core_only_p2_v7_execution_decision(_context())

    assert decision.positions == ()
    assert decision.diagnostics["risk_gate"] == "REJECT"
    assert decision.diagnostics["no_order"] is True


def test_v7_execution_builder_rejects_missing_fresh_capital(monkeypatch) -> None:
    _freeze_risk_clock(monkeypatch)
    ctx = _context(high_volatility=True)
    materials = _execution_materials(ctx.portfolio)
    materials.pop("capital_base")
    materials.pop("capital_base_binding")
    decision = build_soxl_soxx_core_only_p2_v7_execution_decision(
        StrategyContext(
            as_of=ctx.as_of,
            market_data=ctx.market_data,
            portfolio=ctx.portfolio,
            runtime_config=ctx.runtime_config,
            state=materials,
        )
    )

    assert decision.positions == ()
    assert decision.diagnostics["risk_gate"] == "REJECT"
    assert decision.diagnostics["no_order"] is True


def test_v7_execution_builder_rejects_research_only_mandate(monkeypatch) -> None:
    _freeze_risk_clock(monkeypatch)
    ctx = _context(high_volatility=True)
    materials = _execution_materials(ctx.portfolio)
    materials["mandate_provenance"] = {
        **materials["mandate_provenance"],
        "authority_scope": "RESEARCH_ONLY",
    }
    decision = build_soxl_soxx_core_only_p2_v7_execution_decision(
        StrategyContext(
            as_of=ctx.as_of,
            market_data=ctx.market_data,
            portfolio=ctx.portfolio,
            runtime_config=ctx.runtime_config,
            state=materials,
            capabilities={
                "capital_base": materials["capital_base"],
                "capital_base_binding": materials["capital_base_binding"],
            },
        )
    )

    assert decision.positions == ()
    assert decision.diagnostics["risk_gate"] == "REJECT"


def test_v7_entrypoint_redirects_soxl_to_boxx_in_high_volatility() -> None:
    decision = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
        _context(high_volatility=True)
    )

    assert _target_values(decision) == {
        "BOXX": 72_750.0,
        "SOXL": 0.0,
        "SOXX": 24_250.0,
    }


def test_v7_entrypoint_applies_frozen_mid_weights_after_cash_reserve() -> None:
    decision = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
        _context(trend_price=75.0)
    )

    assert _target_values(decision) == {
        "BOXX": 48_500.0,
        "SOXL": 24_250.0,
        "SOXX": 24_250.0,
    }


def test_v7_entrypoint_rejects_material_runtime_overrides() -> None:
    overridden = dict(V7_FROZEN_RUNTIME_CONFIG)
    overridden["blend_gate_soxl_weight"] = 0.70

    with pytest.raises(ValueError, match="frozen V7 runtime config"):
        get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
            _context(runtime_config=overridden)
        )

    bad_timing = dict(V7_FROZEN_RUNTIME_CONFIG)
    bad_timing["signal_effective_after_trading_days"] = 0
    with pytest.raises(ValueError, match="signal timing"):
        get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE).evaluate(
            _context(runtime_config=bad_timing)
        )
