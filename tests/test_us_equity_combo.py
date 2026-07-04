from __future__ import annotations

from us_equity_strategies.strategies import us_equity_combo

from tests.test_mega_cap_leader_rotation import _mega_snapshot


def test_us_equity_combo_skips_ibit_leg_without_logger_error() -> None:
    weights, signal_desc, is_emergency, status_desc, diagnostics = us_equity_combo.compute_signals(
        _mega_snapshot(),
        current_holdings=set(),
        config={"dynamic": True},
    )

    assert weights
    assert "stock=" in signal_desc
    assert "etf=" in status_desc
    assert is_emergency is False
    assert diagnostics["dca_managed_symbols"] == ()
