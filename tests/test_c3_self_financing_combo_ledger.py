"""Synthetic SOXL/TQQQ/cash self-financing ledger checks.

Numbers below are hand-built acceptance inputs. They are not a historical
strategy result, a live fee schedule, or a cash-rate observation.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from quant_platform_kit.strategy_lifecycle.contracts import ResearchDailyLedger

from us_equity_strategies.research.c3_capital_path import (
    scale_member_budgets_to_cash,
    simulate_fixed_budget_capital_path,
)
from us_equity_strategies.research.c3_self_financing_combo_ledger import (
    CASH,
    COST_SOURCE,
    RAW_FIXED_BUDGET,
    RISK_SCALED,
    RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE,
    SOXL,
    TQQQ,
    build_soxl_tqqq_self_financing_ledgers,
)

INITIAL = date(2026, 1, 2)
SESSIONS = (date(2026, 1, 5), date(2026, 1, 6))
TARGET = {SOXL: 0.4, TQQQ: 0.4, CASH: 0.2}
_MEMBERS = (SOXL, TQQQ, CASH)


def _ledgers(**overrides: object):
    payload = {
        "soxl_net_returns": (0.10, 0.0),
        "tqqq_net_returns": (0.0, 0.10),
        "initial_session_date": INITIAL,
        "session_dates": SESSIONS,
        "initial_capital": 1_000.0,
        "target_weights": dict(TARGET),
        "risk_scalar": 1.0,
        "rebalance_indices": (),
        "combo_fee_bps": 0.0,
        "fee_bearing_members": (),
    }
    payload.update(overrides)
    return build_soxl_tqqq_self_financing_ledgers(**payload)


def _values(ledger, symbol: str) -> tuple[float, ...]:
    opening = {mark.symbol: mark.valuation for mark in ledger.initial_positions}
    series = [opening.get(symbol, 0.0)]
    for day in ledger.days:
        marks = {mark.symbol: mark.valuation for mark in day.positions}
        series.append(marks.get(symbol, 0.0))
    return tuple(series)


def test_no_rebalance_matches_hand_growth_and_keeps_member_return_intact() -> None:
    # 400/400/200. Day0 SOXL +10% → 440/400/200, NAV 1040.
    # Day1 TQQQ +10% → 440/440/200, NAV 1080. Cash return stays 0.
    ledgers = _ledgers()
    raw = ledgers[RAW_FIXED_BUDGET]
    assert raw.synthetic is True
    assert raw.cost_source == COST_SOURCE
    assert raw.initial_session_date == INITIAL
    assert raw.days[0].session_date == SESSIONS[0]
    assert raw.observation_count == 2
    assert _values(raw, SOXL) == pytest.approx((400.0, 440.0, 440.0))
    assert _values(raw, TQQQ) == pytest.approx((400.0, 400.0, 440.0))
    assert [mark.quantity for mark in raw.initial_positions] == pytest.approx((400.0, 400.0))
    assert [mark.quantity for mark in raw.days[0].positions] == pytest.approx((400.0, 400.0))
    assert [mark.quantity for mark in raw.days[1].positions] == pytest.approx((400.0, 400.0))
    assert raw.days[0].positions[0].valuation / raw.days[0].positions[0].quantity == pytest.approx(1.1)
    assert raw.initial_cash == pytest.approx(200.0)
    assert raw.days[0].cash == pytest.approx(200.0)
    assert raw.days[1].cash == pytest.approx(200.0)
    assert raw.days[0].fees == 0.0
    assert raw.days[1].fees == 0.0
    assert raw.days[0].trade_net_cashflow == 0.0
    assert raw.days[0].nav == pytest.approx(1_040.0)
    assert raw.days[1].nav == pytest.approx(1_080.0)
    assert raw.days[0].daily_return == raw.days[0].nav / raw.initial_nav - 1.0
    assert raw.days[1].daily_return == raw.days[1].nav / raw.days[0].nav - 1.0
    assert raw.total_fees == 0.0
    assert ledgers[RISK_SCALED].days[1].nav == pytest.approx(raw.days[1].nav)
    assert ledgers[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE].total_fees == 0.0


def test_rebalance_fee_is_self_financed_from_post_return_risk_leg_trades() -> None:
    # Capital 1500 at 0.5/0.3/0.2. SOXL doubles before the only rebalance:
    # grown 1500/450/300, pre-fee NAV 2250. In (1500, 3000) the book sells
    # SOXL and buys TQQQ, so |trades| = 1050 - 0.2 V.
    # V + 0.01 * (1050 - 0.2 V) = 2250 → V = 2239.5 / 0.998.
    solved = 2239.5 / 0.998
    fee = 2250.0 - solved
    ledgers = _ledgers(
        soxl_net_returns=(1.0,),
        tqqq_net_returns=(0.0,),
        session_dates=(SESSIONS[0],),
        initial_capital=1_500.0,
        target_weights={SOXL: 0.5, TQQQ: 0.3, CASH: 0.2},
        rebalance_indices=(0,),
        combo_fee_bps=100.0,
        fee_bearing_members=(SOXL, TQQQ),
    )
    charged = ledgers[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE]
    day = charged.days[0]
    soxl_trade = 0.5 * solved - 1_500.0
    tqqq_trade = 0.3 * solved - 450.0
    assert day.nav == pytest.approx(solved)
    assert day.fees == pytest.approx(fee)
    assert day.fees == pytest.approx(0.01 * (abs(soxl_trade) + abs(tqqq_trade)))
    assert day.cash == pytest.approx(charged.initial_cash + day.trade_net_cashflow - day.fees)
    assert day.nav == pytest.approx(day.cash + sum(mark.valuation for mark in day.positions))
    assert day.daily_return == day.nav / charged.initial_nav - 1.0
    assert _values(charged, SOXL)[1] == pytest.approx(0.5 * solved)
    assert _values(charged, TQQQ)[1] == pytest.approx(0.3 * solved)
    assert day.positions[0].quantity == pytest.approx(0.5 * solved / 2.0)
    assert day.positions[1].quantity == pytest.approx(0.3 * solved)
    assert day.cash == pytest.approx(0.2 * solved)
    assert ledgers[RAW_FIXED_BUDGET].days[0].fees == 0.0
    assert ledgers[RISK_SCALED].days[0].fees == 0.0
    assert ledgers[RISK_SCALED].days[0].nav == pytest.approx(2_250.0)

    approximated = simulate_fixed_budget_capital_path(
        member_ids=(SOXL, TQQQ, CASH),
        member_returns={SOXL: (1.0,), TQQQ: (0.0,), CASH: (0.0,)},
        target_weights={SOXL: 0.5, TQQQ: 0.3, CASH: 0.2},
        rebalance_fee_bps=100.0,
        rebalance_indices=(0,),
        fee_bearing_member_ids=(SOXL, TQQQ),
    )
    c3_ratio = float(approximated["terminal_nav"])
    booked_ratio = day.nav / charged.initial_nav
    assert c3_ratio == pytest.approx(1.5 * (1.0 - 0.01 * (1.0 / 6.0 + 0.1)))
    assert booked_ratio == pytest.approx(solved / 1_500.0)
    assert booked_ratio != pytest.approx(c3_ratio, rel=0.0, abs=1e-9)


def test_fee_uses_only_the_declared_risk_leg() -> None:
    # Same grown book as the two-leg case. Charging only TQQQ:
    # V + 0.01 * (0.3 V - 450) = 2250 → V = 2254.5 / 1.003.
    solved = 2254.5 / 1.003
    ledgers = _ledgers(
        soxl_net_returns=(1.0,),
        tqqq_net_returns=(0.0,),
        session_dates=(SESSIONS[0],),
        initial_capital=1_500.0,
        target_weights={SOXL: 0.5, TQQQ: 0.3, CASH: 0.2},
        rebalance_indices=(0,),
        combo_fee_bps=100.0,
        fee_bearing_members=(TQQQ,),
    )
    day = ledgers[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE].days[0]
    tqqq_trade = 0.3 * solved - 450.0
    soxl_trade = 0.5 * solved - 1_500.0
    assert day.nav == pytest.approx(solved)
    assert day.fees == pytest.approx(0.01 * abs(tqqq_trade))
    assert abs(soxl_trade) > abs(tqqq_trade)
    assert day.fees != pytest.approx(0.01 * (abs(soxl_trade) + abs(tqqq_trade)))


def test_flat_rebalance_charges_no_initial_establishment_fee() -> None:
    ledgers = _ledgers(
        soxl_net_returns=(0.0,),
        tqqq_net_returns=(0.0,),
        session_dates=(SESSIONS[0],),
        rebalance_indices=(0,),
        combo_fee_bps=100.0,
        fee_bearing_members=(SOXL, TQQQ),
    )
    day = ledgers[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE].days[0]
    assert day.fees == 0.0
    assert day.trade_net_cashflow == 0.0
    assert day.nav == pytest.approx(ledgers[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE].initial_nav)
    assert day.daily_return == 0.0


def test_risk_scalar_moves_the_residual_into_the_cash_leg() -> None:
    ledgers = _ledgers(risk_scalar=0.5)
    scaled_weights = scale_member_budgets_to_cash(
        budgets=TARGET, risk_scalar=0.5, cash_member_id=CASH
    )
    scaled = ledgers[RISK_SCALED]
    assert scaled_weights[CASH] == pytest.approx(0.6)
    assert scaled.initial_cash == pytest.approx(600.0)
    assert _values(scaled, SOXL) == pytest.approx((200.0, 220.0, 220.0))
    assert _values(scaled, TQQQ) == pytest.approx((200.0, 200.0, 220.0))
    assert scaled.days[0].cash == pytest.approx(600.0)
    assert scaled.days[0].nav == pytest.approx(1_020.0)
    assert scaled.days[0].fees == 0.0
    assert ledgers[RAW_FIXED_BUDGET].initial_cash == pytest.approx(200.0)
    assert ledgers[RAW_FIXED_BUDGET].days[0].nav == pytest.approx(1_040.0)

    parked = _ledgers(risk_scalar=0.0, soxl_net_returns=(0.5,), tqqq_net_returns=(0.5,), session_dates=(SESSIONS[0],))
    cash_book = parked[RISK_SCALED]
    assert cash_book.initial_positions == ()
    assert cash_book.days[0].positions == ()
    assert cash_book.days[0].nav == pytest.approx(cash_book.initial_nav)
    assert cash_book.days[0].daily_return == 0.0
    assert cash_book.initial_cash == pytest.approx(1_000.0)


def test_all_cash_and_single_risk_leg_boundaries() -> None:
    cash_only = _ledgers(
        soxl_net_returns=(0.25, -0.4),
        tqqq_net_returns=(0.1, 0.2),
        target_weights={SOXL: 0.0, TQQQ: 0.0, CASH: 1.0},
        rebalance_indices=(0, 1),
        combo_fee_bps=50.0,
        fee_bearing_members=(SOXL, TQQQ),
    )[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE]
    assert cash_only.initial_positions == ()
    assert cash_only.days[0].positions == ()
    assert cash_only.days[1].fees == 0.0
    assert cash_only.days[1].nav == pytest.approx(1_000.0)
    assert cash_only.days[0].daily_return == 0.0

    # One risk leg at 0.5, cash 0.5. SOXL +10% grows 500 → 550. TQQQ's +80%
    # has no units. Fee on SOXL only, below the 1100 breakpoint:
    # V + 0.01 * (550 - 0.5 V) = 1050 → V = 1044.5 / 0.995.
    solved = 1044.5 / 0.995
    single = _ledgers(
        soxl_net_returns=(0.10,),
        tqqq_net_returns=(0.80,),
        session_dates=(SESSIONS[0],),
        target_weights={SOXL: 0.5, TQQQ: 0.0, CASH: 0.5},
        rebalance_indices=(0,),
        combo_fee_bps=100.0,
        fee_bearing_members=(SOXL,),
    )[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE]
    day = single.days[0]
    assert TQQQ not in {mark.symbol for mark in day.positions}
    assert day.nav == pytest.approx(solved)
    assert day.fees == pytest.approx(0.01 * abs(0.5 * solved - 550.0))
    assert _values(single, SOXL)[1] == pytest.approx(0.5 * solved)
    assert day.cash == pytest.approx(0.5 * solved)


def test_fully_invested_book_does_not_borrow_to_pay_fee() -> None:
    charged = _ledgers(
        soxl_net_returns=(0.10,),
        tqqq_net_returns=(0.0,),
        session_dates=(SESSIONS[0],),
        target_weights={SOXL: 0.5, TQQQ: 0.5, CASH: 0.0},
        rebalance_indices=(0,),
        combo_fee_bps=100.0,
        fee_bearing_members=(SOXL, TQQQ),
    )[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE]
    day = charged.days[0]
    assert day.fees > 0.0
    assert day.cash >= 0.0
    assert day.cash == pytest.approx(0.0, abs=1e-10)
    assert day.nav == pytest.approx(sum(mark.valuation for mark in day.positions))


def test_later_return_does_not_change_the_earlier_book() -> None:
    early = _ledgers()
    later = _ledgers(soxl_net_returns=(0.10, 0.50), tqqq_net_returns=(0.0, -0.25))
    for key in (RAW_FIXED_BUDGET, RISK_SCALED, RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE):
        assert early[key].days[0].nav == later[key].days[0].nav
        assert early[key].days[0].cash == later[key].days[0].cash
        assert early[key].days[1].nav != later[key].days[1].nav


def test_zero_fee_paths_match_c3_capital_path_including_rebalance() -> None:
    weights = {SOXL: 0.5, TQQQ: 0.3, CASH: 0.2}
    ledgers = _ledgers(
        soxl_net_returns=(0.10, 0.0),
        tqqq_net_returns=(0.0, -0.05),
        initial_capital=2_000.0,
        target_weights=weights,
        risk_scalar=0.5,
        rebalance_indices=(1,),
        combo_fee_bps=0.0,
        fee_bearing_members=(),
    )
    scaled = scale_member_budgets_to_cash(budgets=weights, risk_scalar=0.5, cash_member_id=CASH)
    for ledger, used in (
        (ledgers[RAW_FIXED_BUDGET], weights),
        (ledgers[RISK_SCALED], scaled),
        (ledgers[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE], scaled),
    ):
        path = simulate_fixed_budget_capital_path(
            member_ids=_MEMBERS,
            member_returns={
                SOXL: (0.10, 0.0),
                TQQQ: (0.0, -0.05),
                CASH: (0.0, 0.0),
            },
            target_weights=used,
            rebalance_fee_bps=0.0,
            rebalance_indices=(1,),
        )
        assert [day.daily_return for day in ledger.days] == pytest.approx(path["daily_returns"])
        assert ledger.days[-1].nav / ledger.initial_nav == pytest.approx(path["terminal_nav"])
        assert ledger.total_fees == 0.0
        assert path["rebalance_fee_basis"] == "ZERO_FEE_RATE_NO_COST"
    assert ledgers[RAW_FIXED_BUDGET].days[-1].nav != pytest.approx(
        ledgers[RISK_SCALED].days[-1].nav
    )


def test_non_rebalance_day_does_not_charge_a_second_member_fee() -> None:
    ledgers = _ledgers(
        rebalance_indices=(1,),
        combo_fee_bps=25.0,
        fee_bearing_members=(SOXL, TQQQ),
    )
    charged = ledgers[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE]
    assert charged.days[0].fees == 0.0
    assert _values(charged, SOXL)[1] == pytest.approx(400.0 * 1.10)
    assert charged.days[1].fees > 0.0
    assert charged.cost_inputs["combo_fee_bps"] == pytest.approx(25.0)
    assert ledgers[RAW_FIXED_BUDGET].cost_inputs["combo_fee_bps"] == 0.0
    assert charged.cost_inputs["cash_daily_return"] == 0.0
    assert charged.cost_source == COST_SOURCE
    assert charged.synthetic is True


def test_three_ledgers_round_trip_through_local_performance_store(tmp_path) -> None:
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    ledgers = _ledgers(
        rebalance_indices=(1,),
        combo_fee_bps=100.0,
        fee_bearing_members=(SOXL,),
        risk_scalar=0.5,
    )
    store = PerformanceStore(local_root=tmp_path, cloud_bucket="")
    assert store.cloud_bucket == ""
    loaded = []
    for ledger in ledgers.values():
        store.save_research_ledger(ledger)
        found = store.load_research_ledger(
            ledger.domain,
            ledger.strategy_profile,
            ledger.trial_id,
            ledger.run_id,
            ledger.param_version,
        )
        assert found is not None
        loaded.append(found)
        assert found.synthetic is True
        assert found.cost_source == COST_SOURCE
        assert found.initial_nav == pytest.approx(ledger.initial_nav)
        assert found.days[1].fees == pytest.approx(ledger.days[1].fees)
        assert found.days[1].cash == pytest.approx(
            found.days[0].cash + found.days[1].trade_net_cashflow - found.days[1].fees
        )
        assert found.days[1].nav == pytest.approx(
            found.days[1].cash + sum(mark.valuation for mark in found.days[1].positions)
        )
        assert found.days[1].daily_return == found.days[1].nav / found.days[0].nav - 1.0
        assert store.load_research_ledger(
            ledger.domain,
            ledger.strategy_profile,
            ledger.trial_id,
            "other-run",
            ledger.param_version,
        ) is None
    assert len({item.trial_id for item in loaded}) == 3
    assert loaded[0].total_fees == 0.0
    assert loaded[1].total_fees == 0.0
    assert loaded[2].total_fees > 0.0


def test_different_synthetic_inputs_have_distinct_store_identities(tmp_path) -> None:
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    store = PerformanceStore(local_root=tmp_path, cloud_bucket="")
    first = _ledgers()[RAW_FIXED_BUDGET]
    changed = _ledgers(soxl_net_returns=(0.11, 0.0))[RAW_FIXED_BUDGET]
    assert first.input_id != changed.input_id
    assert first.trial_id != changed.trial_id
    for ledger in (first, changed, first):
        store.save_research_ledger(ledger)
        assert store.load_research_ledger(
            ledger.domain,
            ledger.strategy_profile,
            ledger.trial_id,
            ledger.run_id,
            ledger.param_version,
        ) == ledger


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"target_weights": {SOXL: 0.5, TQQQ: 0.5, CASH: 0.2}}, "SELF_FINANCING_WEIGHT_INVALID"),
        ({"target_weights": {SOXL: -0.1, TQQQ: 0.9, CASH: 0.2}}, "SELF_FINANCING_WEIGHT_INVALID"),
        ({"target_weights": {SOXL: 0.8, TQQQ: 0.2}}, "SELF_FINANCING_WEIGHT_INVALID"),
        ({"initial_capital": 0.0}, "SELF_FINANCING_CAPITAL_INVALID"),
        ({"initial_capital": True}, "SELF_FINANCING_CAPITAL_INVALID"),
        ({"soxl_net_returns": (0.1, float("nan"))}, "SELF_FINANCING_RETURN_INVALID"),
        ({"tqqq_net_returns": (-1.0, 0.0)}, "SELF_FINANCING_RETURN_INVALID"),
        ({"tqqq_net_returns": (0.0,)}, "SELF_FINANCING_RETURN_LENGTH_MISMATCH"),
        ({"session_dates": (INITIAL, SESSIONS[1])}, "SELF_FINANCING_DATE_MISALIGNED"),
        ({"session_dates": (SESSIONS[1], SESSIONS[0])}, "SELF_FINANCING_DATE_MISALIGNED"),
        ({"session_dates": (SESSIONS[0],)}, "SELF_FINANCING_DATE_MISALIGNED"),
        ({"initial_session_date": datetime(2026, 1, 2, tzinfo=UTC)}, "SELF_FINANCING_DATE_MISALIGNED"),
        ({"combo_fee_bps": -1.0}, "SELF_FINANCING_FEE_BPS_INVALID"),
        ({"rebalance_indices": (2,), "combo_fee_bps": 0.0}, "SELF_FINANCING_REBALANCE_INDEX_INVALID"),
        ({"rebalance_indices": (True,), "combo_fee_bps": 0.0}, "SELF_FINANCING_REBALANCE_INDEX_INVALID"),
        ({"combo_fee_bps": 10.0, "rebalance_indices": ()}, "SELF_FINANCING_FEE_SCHEDULE_REQUIRED"),
        (
            {"combo_fee_bps": 10.0, "rebalance_indices": (0,), "fee_bearing_members": ()},
            "SELF_FINANCING_FEE_BEARING_INVALID",
        ),
        (
            {"combo_fee_bps": 10.0, "rebalance_indices": (0,), "fee_bearing_members": (CASH,)},
            "SELF_FINANCING_FEE_BEARING_INVALID",
        ),
        (
            {"combo_fee_bps": 10.0, "rebalance_indices": (0,), "fee_bearing_members": (SOXL, SOXL)},
            "SELF_FINANCING_FEE_BEARING_INVALID",
        ),
        (
            {"combo_fee_bps": 10.0, "rebalance_indices": (0,), "fee_bearing_members": "SOXL"},
            "SELF_FINANCING_FEE_BEARING_INVALID",
        ),
        ({"risk_scalar": 1.5}, "RISK_SCALAR_INVALID"),
        ({"risk_scalar": True}, "RISK_SCALAR_INVALID"),
        (
            {
                "combo_fee_bps": 1_000_000.0,
                "rebalance_indices": (0,),
                "fee_bearing_members": (SOXL, TQQQ),
            },
            "SELF_FINANCING_FEE_BPS_INVALID",
        ),
    ],
)
def test_bad_synthetic_inputs_are_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises((ValueError, TypeError), match=message):
        _ledgers(**overrides)


def test_caller_sequences_are_not_mutated() -> None:
    soxl = [0.1, 0.0]
    dates = [SESSIONS[0], SESSIONS[1]]
    weights = dict(TARGET)
    _ledgers(soxl_net_returns=soxl, session_dates=dates, target_weights=weights)
    assert soxl == [0.1, 0.0]
    assert dates == [SESSIONS[0], SESSIONS[1]]
    assert weights == TARGET


def test_zero_fee_rebalance_conserves_nav_and_restores_targets() -> None:
    ledgers = _ledgers(
        soxl_net_returns=(0.25,),
        tqqq_net_returns=(0.0,),
        session_dates=(SESSIONS[0],),
        rebalance_indices=(0,),
    )
    raw = ledgers[RAW_FIXED_BUDGET]
    day = raw.days[0]
    assert day.fees == 0.0
    assert day.nav == pytest.approx(1_100.0)
    assert _values(raw, SOXL)[1] == pytest.approx(440.0)
    assert _values(raw, TQQQ)[1] == pytest.approx(440.0)
    assert day.cash == pytest.approx(220.0)
    assert math.fsum((_values(raw, SOXL)[1], _values(raw, TQQQ)[1], day.cash)) == pytest.approx(day.nav)


def _assert_machine_residual_is_not_a_borrow(
    ledger,
    *,
    solved: float,
    soxl_weight: float,
    tqqq_weight: float,
) -> None:
    # 负现金是大额 NAV 相减留下的机器残差，不是真实借款。
    assert isinstance(ledger, ResearchDailyLedger)
    day0, day1 = ledger.days
    assert day0.cash < 0.0
    assert day0.cash == ledger.initial_cash + day0.trade_net_cashflow - day0.fees
    scale = max(
        abs(ledger.initial_nav),
        abs(day0.nav),
        abs(day0.trade_net_cashflow),
        abs(day0.fees),
        1.0,
    )
    assert day0.cash >= -4 * math.ulp(scale)
    assert day0.nav == pytest.approx(solved)
    pre_nav = ledger.initial_nav * (1.0 + soxl_weight * 0.10)
    assert day0.fees == pytest.approx(pre_nav - solved)
    assert math.isclose(
        day0.cash,
        ledger.initial_cash + day0.trade_net_cashflow - day0.fees,
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    marks = {mark.symbol: mark.valuation for mark in day0.positions}
    assert math.isclose(marks[SOXL] / day0.nav, soxl_weight, rel_tol=0.0, abs_tol=4 * math.ulp(soxl_weight))
    assert math.isclose(marks[TQQQ] / day0.nav, tqqq_weight, rel_tol=0.0, abs_tol=4 * math.ulp(tqqq_weight))
    assert day1.fees == 0.0
    assert day1.trade_net_cashflow == 0.0
    assert day1.cash == day0.cash
    assert day1.cash == day0.cash + day1.trade_net_cashflow - day1.fees
    assert math.isclose(
        day1.nav,
        day1.cash + sum(mark.valuation for mark in day1.positions),
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    assert day1.daily_return == day1.nav / day0.nav - 1.0
    assert _values(ledger, SOXL)[2] == pytest.approx(_values(ledger, SOXL)[1])
    assert _values(ledger, TQQQ)[2] == pytest.approx(_values(ledger, TQQQ)[1] * 1.02)


def _assert_material_overfill_rejected(capital: float, soxl_weight: float, tqqq_weight: float) -> None:
    # 5e-13 仍能通过权重闭合，但美元缺口远多于几个 ULP，必须继续拒绝。
    with pytest.raises(ValueError, match="SELF_FINANCING_CASH_NEGATIVE"):
        _ledgers(
            soxl_net_returns=(0.10, 0.0),
            tqqq_net_returns=(0.0, 0.02),
            initial_capital=capital,
            target_weights={SOXL: soxl_weight + 5e-13, TQQQ: tqqq_weight, CASH: 0.0},
            risk_scalar=1.0,
            rebalance_indices=(0,),
            combo_fee_bps=10.0,
            fee_bearing_members=(SOXL, TQQQ),
        )


def test_million_full_investment_rebalance_keeps_machine_cash_residual() -> None:
    # Capital 1_000_000 at 0.05/0.95/0. SOXL +10% before the only rebalance:
    # grown 55_000/950_000/0, pre-fee NAV 1_005_000. Between the 1_000_000 and
    # 1_100_000 breakpoints the book sells SOXL and buys TQQQ:
    # V + 0.001 * ((55_000 - 0.05 V) + (0.95 V - 950_000)) = 1_005_000
    # → 1.0009 V = 1_005_895.
    solved = 1_005_895 / 1.0009
    charged = _ledgers(
        soxl_net_returns=(0.10, 0.0),
        tqqq_net_returns=(0.0, 0.02),
        initial_capital=1_000_000.0,
        target_weights={SOXL: 0.05, TQQQ: 0.95, CASH: 0.0},
        risk_scalar=1.0,
        rebalance_indices=(0,),
        combo_fee_bps=10.0,
        fee_bearing_members=(SOXL, TQQQ),
    )[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE]
    _assert_machine_residual_is_not_a_borrow(charged, solved=solved, soxl_weight=0.05, tqqq_weight=0.95)
    _assert_material_overfill_rejected(1_000_000.0, 0.05, 0.95)


def test_hundred_million_full_investment_rebalance_keeps_machine_cash_residual() -> None:
    # Capital 1e8 at 0.3/0.7/0. SOXL +10% before the only rebalance:
    # grown 33_000_000/70_000_000/0, pre-fee NAV 103_000_000. Between the
    # 100_000_000 and 110_000_000 breakpoints the book sells SOXL and buys TQQQ:
    # V + 0.001 * ((33_000_000 - 0.3 V) + (0.7 V - 70_000_000)) = 103_000_000
    # → 1.0004 V = 103_037_000. Theoretical cash is zero.
    solved = 103_037_000 / 1.0004
    charged = _ledgers(
        soxl_net_returns=(0.10, 0.0),
        tqqq_net_returns=(0.0, 0.02),
        initial_capital=100_000_000.0,
        target_weights={SOXL: 0.3, TQQQ: 0.7, CASH: 0.0},
        risk_scalar=1.0,
        rebalance_indices=(0,),
        combo_fee_bps=10.0,
        fee_bearing_members=(SOXL, TQQQ),
    )[RISK_SCALED_WITH_SYNTHETIC_COMBO_FEE]
    _assert_machine_residual_is_not_a_borrow(charged, solved=solved, soxl_weight=0.3, tqqq_weight=0.7)
    _assert_material_overfill_rejected(100_000_000.0, 0.3, 0.7)


def test_initial_cash_machine_residual_is_kept() -> None:
    # 1e8 at 0.45/0.55/0. The weight products overshoot by a few ULPs of the
    # opening NAV. 负现金是机器残差，不是真实借款。
    raw = _ledgers(
        soxl_net_returns=(0.0,),
        tqqq_net_returns=(0.0,),
        session_dates=(SESSIONS[0],),
        initial_capital=100_000_000.0,
        target_weights={SOXL: 0.45, TQQQ: 0.55, CASH: 0.0},
        risk_scalar=1.0,
        rebalance_indices=(),
    )[RAW_FIXED_BUDGET]
    assert isinstance(raw, ResearchDailyLedger)
    assert raw.initial_cash < 0.0
    assert raw.initial_cash >= -4 * math.ulp(max(abs(raw.initial_nav), 1.0))
    assert raw.days[0].cash == raw.initial_cash
    assert raw.days[0].trade_net_cashflow == 0.0
    assert raw.days[0].fees == 0.0
    assert math.isclose(
        raw.days[0].cash,
        raw.initial_cash + raw.days[0].trade_net_cashflow - raw.days[0].fees,
        rel_tol=0.0,
        abs_tol=1e-9,
    )
