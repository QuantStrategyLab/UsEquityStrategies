import unittest
from types import SimpleNamespace

from us_equity_strategies.cash_only_equity import (
    apply_cash_only_account_state,
    compute_strategy_total_equity,
    resolve_raw_cash_from_snapshot,
    resolve_strategy_equity_for_targets,
)


class CashOnlyEquityTests(unittest.TestCase):
    def test_resolve_raw_cash_prefers_market_currency_cash_metadata(self) -> None:
        snapshot = SimpleNamespace(
            buying_power=1588.89,
            cash_balance=1588.89,
            metadata={"market_currency_cash": -284.0, "available_funds": 1588.89},
        )
        self.assertEqual(resolve_raw_cash_from_snapshot(snapshot), -284.0)

    def test_compute_strategy_total_equity_uses_raw_cash_not_margin_capacity(self) -> None:
        total = compute_strategy_total_equity({"SOXX": 2443.67}, -284.0)
        self.assertAlmostEqual(total, 2159.67, places=2)

    def test_apply_cash_only_account_state_overwrites_inflated_total(self) -> None:
        account_state = {
            "available_cash": 1588.89,
            "market_values": {"SOXX": 2443.67},
            "total_strategy_equity": 4032.56,
        }
        normalized = apply_cash_only_account_state(account_state, raw_cash=-284.0)
        self.assertEqual(normalized["available_cash"], -284.0)
        self.assertAlmostEqual(normalized["total_strategy_equity"], 2159.67, places=2)


    def test_resolve_strategy_equity_for_targets_uses_gross_positions_when_net_negative(self) -> None:
        equity, deleverage = resolve_strategy_equity_for_targets(
            market_values={"SOXX": 2443.67},
            raw_cash=-284.0,
            cash_only_execution=True,
        )
        self.assertAlmostEqual(equity, 2159.67, places=2)
        self.assertFalse(deleverage)

        equity, deleverage = resolve_strategy_equity_for_targets(
            market_values={"SOXX": 2443.67},
            raw_cash=-3000.0,
            cash_only_execution=True,
        )
        self.assertAlmostEqual(equity, 2443.67, places=2)
        self.assertTrue(deleverage)


if __name__ == "__main__":
    unittest.main()
