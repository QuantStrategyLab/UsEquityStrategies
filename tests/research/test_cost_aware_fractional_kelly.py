"""R5: cost-aware fractional-Kelly target. Synthetic inputs only."""
import json
import math
import random
import socket

import pytest

from us_equity_strategies.research import cost_aware_fractional_kelly as r5
from us_equity_strategies.research import fractional_kelly_walk_forward as rs04


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _policy(**overrides):
    values = {"c": 0.5, "position_cap": 1.0, "cost_bps_per_side": 0.0,
              "holding_sessions": 1, "min_trade_weight": 0.0}
    values.update(overrides)
    return r5.CostAwareKellyPolicy(**values)


def _target(mu=0.00125, sigma2=0.0025, current=0.0, **policy):
    return r5.cost_aware_fractional_kelly(
        mu_excess_shrunk=mu, sigma2=sigma2, current_weight=current, policy=_policy(**policy))


def test_zero_cost_reproduces_fractional_kelly():
    result = _target(mu=0.001, sigma2=0.0004, current=0.1, c=0.25)
    assert result.status == "COMPUTED"
    assert result.reason_code is None
    assert result.raw_fractional_target == pytest.approx(0.625)
    assert result.no_trade_lower == pytest.approx(0.625)
    assert result.no_trade_upper == pytest.approx(0.625)
    assert result.target_weight == pytest.approx(0.625)
    assert result.trade_weight == pytest.approx(0.525)
    assert result.estimated_cost_fraction == 0.0
    assert result.cost_half_band == 0.0
    assert result.band_saturated is False


def test_cost_raises_lower_entry_target_and_reduces_turnover():
    zero = _target(current=0.0, cost_bps_per_side=0.0)
    low = _target(current=0.0, cost_bps_per_side=5.0)
    high = _target(current=0.0, cost_bps_per_side=10.0)
    blocked = _target(current=0.0, cost_bps_per_side=25.0)
    assert zero.target_weight == pytest.approx(0.25)
    assert low.target_weight == pytest.approx(0.15)
    assert high.target_weight == pytest.approx(0.05)
    assert blocked.status == "HOLD"
    assert blocked.target_weight == 0.0
    assert zero.trade_weight > low.trade_weight > high.trade_weight > blocked.trade_weight


def test_holding_sessions_amortises_cost():
    one = _target(current=0.0, cost_bps_per_side=5.0, holding_sessions=1)
    two = _target(current=0.0, cost_bps_per_side=5.0, holding_sessions=2)
    assert one.cost_half_band == pytest.approx(0.1)
    assert two.cost_half_band == pytest.approx(0.05)
    assert two.target_weight > one.target_weight


def test_current_inside_band_holds():
    result = _target(current=0.2, cost_bps_per_side=5.0)
    assert result.status == "HOLD"
    assert result.reason_code == "WITHIN_NO_TRADE_BAND"
    assert result.no_trade_lower == pytest.approx(0.15)
    assert result.no_trade_upper == pytest.approx(0.35)
    assert result.target_weight == pytest.approx(0.2)
    assert result.trade_weight == 0.0
    assert result.estimated_cost_fraction == 0.0


def test_current_above_band_reduces_to_upper_edge():
    result = _target(current=0.8, cost_bps_per_side=5.0)
    assert result.status == "COMPUTED"
    assert result.target_weight == pytest.approx(0.35)
    assert result.trade_weight == pytest.approx(-0.45)
    assert result.estimated_cost_fraction == pytest.approx(0.0005 * 0.45)


def test_position_cap_is_hard_ceiling():
    result = _target(mu=0.01, sigma2=0.01, current=0.8, position_cap=0.2, cost_bps_per_side=5.0)
    assert result.raw_fractional_target == pytest.approx(0.2)
    assert result.target_weight == pytest.approx(0.2)
    assert result.target_weight <= result.position_cap
    assert result.reason_code == "CURRENT_ABOVE_POSITION_CAP"


def test_zero_position_cap_forces_cash():
    result = _target(current=0.5, position_cap=0.0)
    assert result.target_weight == 0.0
    assert result.trade_weight == pytest.approx(-0.5)
    assert result.reason_code == "CURRENT_ABOVE_POSITION_CAP"


def test_min_trade_weight_does_not_override_hard_cap():
    result = _target(current=0.8, position_cap=0.2, min_trade_weight=0.9)
    assert result.target_weight == pytest.approx(0.2)
    assert result.status == "COMPUTED"
    assert result.reason_code == "CURRENT_ABOVE_POSITION_CAP"


def test_min_trade_weight_holds_small_changes():
    result = _target(current=0.24, cost_bps_per_side=0.0, min_trade_weight=0.02)
    assert result.status == "HOLD"
    assert result.reason_code == "BELOW_MIN_TRADE_WEIGHT"
    assert result.target_weight == pytest.approx(0.24)


def test_negative_edge_at_zero_stays_in_cash():
    result = _target(mu=-0.005, sigma2=0.01, current=0.0, cost_bps_per_side=0.0)
    assert result.status == "HOLD"
    assert result.reason_code == "NO_POSITIVE_EDGE"
    assert result.target_weight == 0.0
    assert result.trade_weight == 0.0


def test_zero_variance_is_parked_without_a_target():
    result = _target(sigma2=0.0, current=0.1)
    assert result.status == "PARKED"
    assert result.reason_code == "ZERO_VARIANCE"
    assert result.target_weight is None
    assert result.trade_weight is None
    assert result.no_order is True
    assert result.live_ready is False
    assert result.budget_effect == "NONE"


def test_net_growth_matches_hand_calculation():
    result = _target(mu=0.005, sigma2=0.01, current=0.0, cost_bps_per_side=5.0)
    target = result.target_weight
    k = 5.0 / 10_000.0
    gross_current = 0.005 * 0.0 - 0.5 * 0.01 * 0.0 ** 2
    gross_target = 0.005 * target - 0.5 * 0.01 * target ** 2
    net_target = gross_target - k * target / 1
    assert result.gross_log_growth_per_session_at_current == pytest.approx(gross_current)
    assert result.gross_log_growth_per_session_at_target == pytest.approx(gross_target)
    assert result.net_log_growth_per_session_at_target == pytest.approx(net_target)
    assert result.net_growth_improvement_vs_hold == pytest.approx(net_target - gross_current)
    assert result.net_growth_improvement_vs_hold > 0.0


def test_from_fit_uses_shrunk_mean_and_rejects_parked_fit():
    fit = {"status": "COMPUTED", "c": 0.5, "mu": 0.002, "mu_shrunk": 0.001,
           "sigma2": 0.01, "exposure": 0.05}
    result = r5.from_fractional_kelly_fit(fit, current_weight=0.0, policy=_policy(c=0.5))
    assert result.raw_fractional_target == pytest.approx(0.05)
    assert result.target_weight == pytest.approx(0.05)

    parked = rs04.fit_fractional_kelly([0.01] * 10, [0.0] * 10, 0.5)
    blocked = r5.from_fractional_kelly_fit(parked, current_weight=0.1, policy=_policy(c=0.5))
    assert blocked.status == "PARKED"
    assert blocked.reason_code == "INSUFFICIENT_TRAIN_OBSERVATIONS"
    assert blocked.target_weight is None


def test_from_fit_requires_matching_fraction():
    fit = {"status": "COMPUTED", "c": 0.25, "mu_shrunk": 0.001, "sigma2": 0.01}
    with pytest.raises(ValueError, match="FIT_POLICY_C_MISMATCH"):
        r5.from_fractional_kelly_fit(fit, current_weight=0.0, policy=_policy(c=0.5))


def test_rs04_fit_and_cost_aware_target_agree_at_zero_cost():
    rng = random.Random(7)
    sample = [rng.gauss(0.0008, 0.01) for _ in range(200)]
    fit = rs04.fit_fractional_kelly(sample, [0.0] * len(sample), 0.5)
    assert fit["status"] == "COMPUTED"
    result = r5.from_fractional_kelly_fit(fit, current_weight=0.0, policy=_policy(c=0.5, cost_bps_per_side=0.0))
    assert result.target_weight == pytest.approx(fit["exposure"])


def test_saturated_band_holds_instead_of_pretending_to_trade():
    result = _target(current=0.5, cost_bps_per_side=10_000.0)
    assert result.status == "HOLD"
    assert result.band_saturated is True
    assert result.no_trade_lower == 0.0
    assert result.no_trade_upper == 1.0
    assert result.target_weight == pytest.approx(0.5)
    assert result.estimated_cost_fraction == 0.0


def test_band_reduces_turnover_against_daily_full_rebalance():
    rng = random.Random(11)
    returns = [rng.gauss(0.001, 0.01) for _ in range(500)]
    policy = _policy(c=0.5, cost_bps_per_side=5.0)
    center = _target(current=0.0, cost_bps_per_side=0.0).target_weight

    def simulate(use_band):
        weight, turnover, cost = 0.0, 0.0, 0.0
        for r in returns:
            target = (r5.cost_aware_fractional_kelly(
                mu_excess_shrunk=0.00125, sigma2=0.0025, current_weight=weight, policy=policy
            ).target_weight if use_band else center)
            turnover += abs(target - weight)
            cost += policy.cost_bps_per_side / 10_000.0 * abs(target - weight)
            gross = target * r
            net = gross - policy.cost_bps_per_side / 10_000.0 * abs(target - weight)
            weight = 0.0 if 1.0 + net <= 0.0 else target * (1.0 + r) / (1.0 + net)
        return turnover, cost

    aware_turnover, aware_cost = simulate(True)
    fixed_turnover, fixed_cost = simulate(False)
    assert aware_turnover < fixed_turnover
    assert aware_cost < fixed_cost
    assert aware_turnover > 0.0


def test_invalid_inputs_fail_closed():
    for bad in (1.0, 1.5, 0.0, -0.25):
        with pytest.raises(ValueError):
            _policy(c=bad)
    for bad in (1.1, -0.1):
        with pytest.raises(ValueError):
            _policy(position_cap=bad)
    for bad in (-0.1, math.nan, math.inf):
        with pytest.raises(ValueError):
            _policy(cost_bps_per_side=bad)
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            _policy(holding_sessions=bad)
    for bad in (1.1, -0.1):
        with pytest.raises(ValueError):
            _policy(min_trade_weight=bad)
    for current in (-0.01, 1.01, math.nan):
        with pytest.raises(ValueError):
            _target(current=current)
    with pytest.raises(ValueError):
        _target(mu=math.nan)
    with pytest.raises(ValueError):
        _target(sigma2=-0.01)


def test_result_is_json_safe_and_authority_is_absent():
    result = _target(current=0.0, cost_bps_per_side=5.0)
    payload = result.to_dict()
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
    assert '"budget_effect": "NONE"' in encoded
    assert '"live_ready": false' in encoded
    assert '"no_order": true' in encoded
    assert payload["model_id"] == r5.MODEL_ID
    assert payload["status"] == "COMPUTED"
