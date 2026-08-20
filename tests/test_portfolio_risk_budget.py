from __future__ import annotations

import pytest

from us_equity_strategies.portfolio_risk_budget import (
    SCHEMA_VERSION,
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
    assess_portfolio_risk_budget,
)


SPECS = {
    "TQQQ": PortfolioAssetRiskSpec("TQQQ", 3.0, "NASDAQ100"),
    "QQQM": PortfolioAssetRiskSpec("QQQM", 1.0, "NASDAQ100"),
    "SOXL": PortfolioAssetRiskSpec("SOXL", 3.0, "US_SEMICONDUCTOR"),
    "BOXX": PortfolioAssetRiskSpec("BOXX", 1.0, "USD_CASH", is_cash=True),
}


def _policy(**overrides: object) -> PortfolioRiskBudgetPolicy:
    values: dict[str, object] = {
        "cash_symbol": "BOXX",
        "max_effective_risk_exposure": 2.0,
        "max_symbol_weights": {"TQQQ": 0.40, "SOXL": 0.20},
        "max_underlying_effective_exposure": {"NASDAQ100": 1.5, "US_SEMICONDUCTOR": 0.8},
        "max_one_way_risk_turnover": None,
    }
    values.update(overrides)
    return PortfolioRiskBudgetPolicy(**values)


def test_within_budget_is_approved_without_changing_a_target() -> None:
    target = {"TQQQ": 0.30, "SOXL": 0.10, "BOXX": 0.60}

    result = assess_portfolio_risk_budget(
        target_weights=target,
        asset_risk_specs=SPECS,
        policy=_policy(),
    )

    assert result == {
        "schema_version": SCHEMA_VERSION,
        "status": "APPROVE",
        "execution_authorized": False,
        "risk_scalar": 1.0,
        "reason_codes": (),
        "recommended_target_weights": target,
        "metrics": {
            "nominal_weight": target,
            "effective_risk_exposure": pytest.approx(1.2),
            "underlying_effective_exposure": {
                "NASDAQ100": pytest.approx(0.9),
                "US_SEMICONDUCTOR": pytest.approx(0.3),
            },
            "one_way_risk_turnover": pytest.approx(0.4),
        },
    }


def test_leveraged_target_is_proportionally_reduced_and_redirected_to_cash() -> None:
    result = assess_portfolio_risk_budget(
        target_weights={"TQQQ": 0.40, "SOXL": 0.20, "BOXX": 0.40},
        asset_risk_specs=SPECS,
        policy=_policy(max_effective_risk_exposure=1.2),
    )

    assert result["status"] == "REDUCE"
    assert result["execution_authorized"] is False
    assert result["reason_codes"] == ("PORTFOLIO_RISK_BUDGET_REDUCED",)
    assert result["risk_scalar"] == pytest.approx(2.0 / 3.0)
    assert result["recommended_target_weights"] == {
        "BOXX": pytest.approx(0.60),
        "SOXL": pytest.approx(2.0 / 15.0),
        "TQQQ": pytest.approx(4.0 / 15.0),
    }
    assert result["metrics"]["effective_risk_exposure"] == pytest.approx(1.2)


def test_look_through_overlap_caps_tqqq_and_qqqm_together() -> None:
    result = assess_portfolio_risk_budget(
        target_weights={"TQQQ": 0.20, "QQQM": 0.40, "BOXX": 0.40},
        asset_risk_specs=SPECS,
        policy=_policy(
            max_effective_risk_exposure=2.0,
            max_symbol_weights={},
            max_underlying_effective_exposure={"NASDAQ100": 0.80},
        ),
    )

    assert result["status"] == "REDUCE"
    assert result["risk_scalar"] == pytest.approx(0.80 / 1.0)
    assert result["recommended_target_weights"] == {
        "BOXX": pytest.approx(0.52),
        "QQQM": pytest.approx(0.32),
        "TQQQ": pytest.approx(0.16),
    }
    assert result["metrics"]["underlying_effective_exposure"] == {
        "NASDAQ100": pytest.approx(0.80),
        "US_SEMICONDUCTOR": pytest.approx(0.0),
    }


def test_one_way_risk_turnover_is_a_first_class_cap() -> None:
    result = assess_portfolio_risk_budget(
        target_weights={"TQQQ": 0.40, "SOXL": 0.20, "BOXX": 0.40},
        current_weights={"BOXX": 1.0},
        asset_risk_specs=SPECS,
        policy=_policy(
            max_effective_risk_exposure=3.0,
            max_symbol_weights={},
            max_underlying_effective_exposure={},
            max_one_way_risk_turnover=0.30,
        ),
    )

    assert result["status"] == "REDUCE"
    assert result["risk_scalar"] == pytest.approx(0.50)
    assert result["recommended_target_weights"] == {
        "BOXX": pytest.approx(0.70),
        "SOXL": pytest.approx(0.10),
        "TQQQ": pytest.approx(0.20),
    }
    assert result["metrics"]["one_way_risk_turnover"] == pytest.approx(0.30)


@pytest.mark.parametrize(
    ("target", "policy", "reason"),
    [
        ({"TQQQ": 0.80, "SOXL": 0.40}, _policy(), "target allocation exceeds fully-funded portfolio"),
        ({"QQQ": 0.10, "BOXX": 0.90}, _policy(), "unknown target allocation symbol"),
        ({"TQQQ": 0.20, "BOXX": 0.80}, _policy(cash_symbol="TQQQ"), "cash symbol is not a cash asset"),
    ],
)
def test_bad_input_fails_closed_as_parked(
    target: dict[str, float], policy: PortfolioRiskBudgetPolicy, reason: str
) -> None:
    result = assess_portfolio_risk_budget(
        target_weights=target,
        asset_risk_specs=SPECS,
        policy=policy,
    )

    assert result["status"] == "PARKED"
    assert result["execution_authorized"] is False
    assert result["risk_scalar"] == 0.0
    assert result["recommended_target_weights"] == {}
    assert result["metrics"] == {}
    assert result["reason_codes"] == (reason,)


def test_inputs_are_not_mutated_and_the_result_is_deterministic() -> None:
    target = {"TQQQ": 0.40, "SOXL": 0.20, "BOXX": 0.40}
    policy = _policy(max_effective_risk_exposure=1.2)

    first = assess_portfolio_risk_budget(
        target_weights=target,
        asset_risk_specs=SPECS,
        policy=policy,
    )
    second = assess_portfolio_risk_budget(
        target_weights=target,
        asset_risk_specs=SPECS,
        policy=policy,
    )

    assert target == {"TQQQ": 0.40, "SOXL": 0.20, "BOXX": 0.40}
    assert first == second
