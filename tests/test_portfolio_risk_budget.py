from __future__ import annotations

import math

import pytest

from us_equity_strategies.portfolio_risk_budget import (
    SCHEMA_VERSION,
    PortfolioAssetRiskSpec,
    PortfolioRiskBudgetPolicy,
    assess_portfolio_risk_budget,
    attribute_fixed_weight_return_risk,
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


@pytest.mark.parametrize(
    "result",
    [
        assess_portfolio_risk_budget(
            target_weights={"TQQQ": 0.30, "SOXL": 0.10, "BOXX": 0.60},
            asset_risk_specs=SPECS,
            policy=_policy(),
        ),
        assess_portfolio_risk_budget(
            target_weights={"TQQQ": 0.40, "SOXL": 0.20, "BOXX": 0.40},
            asset_risk_specs=SPECS,
            policy=_policy(max_effective_risk_exposure=1.2),
        ),
        assess_portfolio_risk_budget(
            target_weights={"UNKNOWN": 1.0},
            asset_risk_specs=SPECS,
            policy=_policy(),
        ),
    ],
)
def test_research_risk_assessment_is_a_no_order_status_only_contract(
    result: dict[str, object],
) -> None:
    assert result["status"] in {"APPROVE", "REDUCE", "PARKED"}
    assert result["execution_authorized"] is False
    assert not any(
        key in result
        for key in ("broker", "account_id", "order", "orders", "paper_authorized", "shadow_authorized", "live_authorized")
    )


def _assert_explicit_symbol_cap_parked(result: dict[str, object]) -> None:
    assert result["status"] == "PARKED"
    assert result["execution_authorized"] is False
    assert result["risk_scalar"] == 0.0
    assert result["recommended_target_weights"] == {}
    assert result["metrics"] == {}
    assert result["reason_codes"] == (
        "recommended allocation exceeds explicit symbol weight limit",
    )


def test_explicit_cash_cap_parks_when_cash_weight_already_exceeds_limit() -> None:
    result = assess_portfolio_risk_budget(
        target_weights={"QQQM": 0.1, "BOXX": 0.9},
        asset_risk_specs=SPECS,
        policy=_policy(
            max_effective_risk_exposure=1.0,
            max_symbol_weights={"BOXX": 0.5},
            max_underlying_effective_exposure={},
        ),
    )

    _assert_explicit_symbol_cap_parked(result)


def test_risk_reduction_that_breaches_explicit_cash_cap_parks() -> None:
    result = assess_portfolio_risk_budget(
        target_weights={"QQQM": 0.8, "BOXX": 0.2},
        asset_risk_specs=SPECS,
        policy=_policy(
            max_effective_risk_exposure=0.4,
            max_symbol_weights={"BOXX": 0.5},
            max_underlying_effective_exposure={},
        ),
    )

    _assert_explicit_symbol_cap_parked(result)


def test_explicit_cash_cap_allows_a_target_under_the_limit() -> None:
    target = {"QQQM": 0.6, "BOXX": 0.4}
    result = assess_portfolio_risk_budget(
        target_weights=target,
        asset_risk_specs=SPECS,
        policy=_policy(
            max_effective_risk_exposure=1.0,
            max_symbol_weights={"BOXX": 0.5},
            max_underlying_effective_exposure={},
        ),
    )

    assert result["status"] == "APPROVE"
    assert result["execution_authorized"] is False
    assert result["risk_scalar"] == 1.0
    assert result["reason_codes"] == ()
    assert result["recommended_target_weights"] == target


def test_explicit_cash_cap_allows_weight_equal_to_the_limit() -> None:
    target = {"QQQM": 0.5, "BOXX": 0.5}
    result = assess_portfolio_risk_budget(
        target_weights=target,
        asset_risk_specs=SPECS,
        policy=_policy(
            max_effective_risk_exposure=1.0,
            max_symbol_weights={"BOXX": 0.5},
            max_underlying_effective_exposure={},
        ),
    )

    assert result["status"] == "APPROVE"
    assert result["execution_authorized"] is False
    assert result["recommended_target_weights"] == target


def test_risk_reduction_that_lands_on_explicit_cash_cap_stays_reduced() -> None:
    result = assess_portfolio_risk_budget(
        target_weights={"QQQM": 0.8, "BOXX": 0.2},
        asset_risk_specs=SPECS,
        policy=_policy(
            max_effective_risk_exposure=0.4,
            max_symbol_weights={"BOXX": 0.6},
            max_underlying_effective_exposure={},
        ),
    )

    assert result["status"] == "REDUCE"
    assert result["execution_authorized"] is False
    assert result["reason_codes"] == ("PORTFOLIO_RISK_BUDGET_REDUCED",)
    assert result["risk_scalar"] == pytest.approx(0.5)
    assert result["recommended_target_weights"] == {
        "BOXX": pytest.approx(0.6),
        "QQQM": pytest.approx(0.4),
    }


def _dated_returns(
    series: dict[str, tuple[float, ...]], dates: tuple[str, ...]
) -> dict[str, list[dict[str, object]]]:
    return {
        symbol: [
            {"date": day, "simple_return": value}
            for day, value in zip(dates, values, strict=True)
        ]
        for symbol, values in series.items()
    }


def _attribute(
    weights: dict[str, float],
    series: dict[str, tuple[float, ...]],
    *,
    dates: tuple[str, ...] = ("2026-01-02", "2026-01-05"),
    as_of: str = "2026-01-05",
    minimum_observations: int = 2,
) -> dict[str, object]:
    return attribute_fixed_weight_return_risk(
        weights=weights,
        asset_returns=_dated_returns(series, dates),
        as_of=as_of,
        quote_currency="USD",
        minimum_observations=minimum_observations,
    )


def test_perfect_correlation_splits_sample_variance_by_fixed_weights() -> None:
    result = _attribute(
        {"BBB": 0.4, "AAA": 0.6},
        {"AAA": (0.02, -0.02), "BBB": (0.02, -0.02)},
    )

    assert result["status"] == "COMPUTED"
    assert result["research_qualified"] is False
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["weights"] == {"AAA": 0.6, "BBB": 0.4}
    assert result["portfolio_variance"] == pytest.approx(0.0008)
    assert result["daily_volatility"] == pytest.approx(0.02 * (2 ** 0.5))
    assert result["variance_contribution"] == {
        "AAA": pytest.approx(0.00048),
        "BBB": pytest.approx(0.00032),
    }
    assert math_is_close_sum(result)
    assert result["variance_contribution_share"] == {
        "AAA": pytest.approx(0.6),
        "BBB": pytest.approx(0.4),
    }
    assert result["correlation"]["AAA"]["BBB"] == pytest.approx(1.0)
    assert result["units"]["daily_volatility"] == "daily_simple_return"
    assert "NOT_ANNUALIZED" in result["limitations"]


def math_is_close_sum(result: dict[str, object]) -> bool:
    contribution = result["variance_contribution"]
    assert isinstance(contribution, dict)
    return math.fsum(contribution.values()) == pytest.approx(result["portfolio_variance"])


def test_offsetting_weights_keep_a_negative_variance_contribution() -> None:
    result = _attribute(
        {"AAA": 0.8, "BBB": 0.2},
        {"AAA": (0.02, -0.02, 0.0), "BBB": (-0.02, 0.02, 0.0)},
        dates=("2026-01-02", "2026-01-05", "2026-01-06"),
        as_of="2026-01-06",
    )

    assert result["variance_contribution"]["BBB"] == pytest.approx(-0.000048)
    assert result["variance_contribution"]["BBB"] < 0.0
    assert result["variance_contribution_share"]["BBB"] == pytest.approx(-1.0 / 3.0)
    assert result["portfolio_variance"] == pytest.approx(0.000144)
    assert math_is_close_sum(result)
    assert result["correlation"]["AAA"]["BBB"] == pytest.approx(-1.0)


def test_explicit_zero_cash_return_stays_distinct_from_a_nonzero_cash_series() -> None:
    risky = (0.01, -0.01)
    flat = _attribute({"AAA": 0.25, "CASH": 0.75}, {"AAA": risky, "CASH": (0.0, 0.0)})
    paid = _attribute({"AAA": 0.25, "CASH": 0.75}, {"AAA": risky, "CASH": (0.01, -0.02)})

    assert flat["status"] == "COMPUTED"
    assert flat["correlation"]["CASH"]["CASH"] is None
    assert flat["correlation"]["AAA"]["CASH"] is None
    assert flat["correlation"]["AAA"]["AAA"] == pytest.approx(1.0)
    assert flat["variance_contribution_share"]["AAA"] == pytest.approx(1.0)
    assert flat["variance_contribution"]["CASH"] == pytest.approx(0.0)
    assert paid["portfolio_variance"] != pytest.approx(flat["portfolio_variance"])
    assert paid["variance_contribution"]["CASH"] != pytest.approx(0.0)


def test_zero_portfolio_variance_leaves_contribution_shares_undefined() -> None:
    result = _attribute(
        {"AAA": 0.5, "BBB": 0.5},
        {"AAA": (0.02, -0.02), "BBB": (-0.02, 0.02)},
    )

    assert result["portfolio_variance"] == pytest.approx(0.0)
    assert result["daily_volatility"] == pytest.approx(0.0)
    assert result["variance_contribution_share"] == {"AAA": None, "BBB": None}
    assert result["correlation"]["AAA"]["BBB"] == pytest.approx(-1.0)
    assert math_is_close_sum(result)


def test_weight_order_does_not_change_the_sample_diagnostic() -> None:
    dates = ("2026-01-02", "2026-01-05", "2026-01-06")
    series = {"AAA": (0.02, -0.02, 0.0), "BBB": (-0.02, 0.02, 0.0)}
    forward = _attribute({"AAA": 0.8, "BBB": 0.2}, series, dates=dates, as_of="2026-01-06")
    reverse = _attribute({"BBB": 0.2, "AAA": 0.8}, series, dates=dates, as_of="2026-01-06")

    assert forward == reverse


@pytest.mark.parametrize(
    ("weights", "series", "kwargs", "reason"),
    [
        ({"AAA": True}, {"AAA": (0.01, -0.01)}, {}, "RETURN_RISK_WEIGHTS_INVALID"),
        ({"AAA": 0.4, "BBB": 0.4}, {"AAA": (0.01, -0.01), "BBB": (0.01, -0.01)}, {}, "RETURN_RISK_WEIGHTS_INVALID"),
        ({"AAA": float("nan")}, {"AAA": (0.01, -0.01)}, {}, "RETURN_RISK_WEIGHTS_INVALID"),
        ({"AAA": float("inf")}, {"AAA": (0.01, -0.01)}, {}, "RETURN_RISK_WEIGHTS_INVALID"),
        ({"AAA": 1.0}, {"BBB": (0.01, -0.01)}, {}, "RETURN_RISK_KEYS_MISMATCH"),
        ({"AAA": 0.5, "CASH": 0.5}, {"AAA": (0.01, -0.01)}, {}, "RETURN_RISK_KEYS_MISMATCH"),
        (
            {"AAA": 1.0},
            {"AAA": (0.01, float("nan"))},
            {},
            "RETURN_RISK_RETURN_INVALID",
        ),
        (
            {"AAA": 1.0},
            {"AAA": (0.01, float("inf"))},
            {},
            "RETURN_RISK_RETURN_INVALID",
        ),
        (
            {"AAA": 1.0},
            {"AAA": (True, 0.01)},
            {},
            "RETURN_RISK_RETURN_INVALID",
        ),
        (
            {"AAA": 1.0},
            {"AAA": (0.02, 0.01)},
            {"dates": ("2026-01-06", "2026-01-05")},
            "RETURN_RISK_DATES_NOT_STRICTLY_INCREASING",
        ),
        (
            {"AAA": 0.5, "BBB": 0.5},
            {"AAA": (0.01, -0.01), "BBB": (0.01, -0.01)},
            {"dates": ("2026-01-02", "2026-01-05"), "series_dates": {"BBB": ("2026-01-02", "2026-01-06")}},
            "RETURN_RISK_DATES_MISMATCH",
        ),
        (
            {"AAA": 1.0},
            {"AAA": (0.01, -0.01)},
            {"as_of": "2026-01-04"},
            "RETURN_RISK_DATE_AFTER_AS_OF",
        ),
        ({"AAA": 1.0}, {"AAA": (0.01, -0.01)}, {"minimum_observations": 1}, "RETURN_RISK_MINIMUM_OBSERVATIONS_INVALID"),
        ({"AAA": 1.0}, {"AAA": (0.01, -0.01)}, {"minimum_observations": 3}, "RETURN_RISK_SAMPLE_TOO_SMALL"),
    ],
)
def test_bad_return_risk_inputs_are_uncomputable_without_statistics(
    weights: dict[str, float],
    series: dict[str, tuple[float, ...]],
    kwargs: dict[str, object],
    reason: str,
) -> None:
    dates = kwargs.get("dates", ("2026-01-02", "2026-01-05"))
    assert isinstance(dates, tuple)
    panel = _dated_returns(series, dates)
    custom_dates = kwargs.get("series_dates")
    if isinstance(custom_dates, dict):
        for symbol, symbol_dates in custom_dates.items():
            panel[symbol] = [
                {"date": day, "simple_return": series[symbol][index]}
                for index, day in enumerate(symbol_dates)
            ]
    result = attribute_fixed_weight_return_risk(
        weights=weights,
        asset_returns=panel,
        as_of=str(kwargs.get("as_of", "2026-01-05")),
        quote_currency="USD",
        minimum_observations=kwargs.get("minimum_observations", 2),
    )

    assert result["status"] == "UNCOMPUTABLE"
    assert result["reason_codes"] == (reason,)
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert "portfolio_variance" not in result
    assert "variance_contribution" not in result
    assert "correlation" not in result


def test_extreme_unit_weight_correlations_do_not_overflow_or_underflow() -> None:
    large = _attribute({"AAA": 1.0}, {"AAA": (0.0, 1e100)})
    small = _attribute({"AAA": 1.0}, {"AAA": (0.0, 1e-100)})

    assert large["status"] == "COMPUTED"
    assert large["correlation"]["AAA"]["AAA"] == 1.0
    assert small["status"] == "COMPUTED"
    assert small["correlation"]["AAA"]["AAA"] == 1.0


def _assert_uncomputable_without_statistics(result: dict[str, object]) -> None:
    assert result["status"] == "UNCOMPUTABLE"
    assert result["execution_authorized"] is False
    assert "portfolio_variance" not in result
    assert "daily_volatility" not in result
    assert "variance_contribution" not in result
    assert "variance_contribution_share" not in result
    assert "correlation" not in result


def test_overflowing_finite_weights_or_returns_stay_uncomputable() -> None:
    weights = _attribute(
        {"AAA": 1e308, "BBB": 1e308},
        {"AAA": (0.0, 0.01), "BBB": (0.0, 0.01)},
    )
    returns = _attribute({"AAA": 1.0}, {"AAA": (1e308, 1e308)})
    huge_weight = attribute_fixed_weight_return_risk(
        weights={"AAA": 10**10000},
        asset_returns=_dated_returns({"AAA": (0.0, 0.01)}, ("2026-01-02", "2026-01-05")),
        as_of="2026-01-05",
        quote_currency="USD",
        minimum_observations=2,
    )
    huge_return = attribute_fixed_weight_return_risk(
        weights={"AAA": 1.0},
        asset_returns={
            "AAA": [
                {"date": "2026-01-02", "simple_return": 0},
                {"date": "2026-01-05", "simple_return": 10**10000},
            ]
        },
        as_of="2026-01-05",
        quote_currency="USD",
        minimum_observations=2,
    )

    for result in (weights, returns, huge_weight, huge_return):
        _assert_uncomputable_without_statistics(result)
