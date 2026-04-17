from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MIN_RECOMMENDED_EQUITY_USD: dict[str, float] = {
    "global_etf_rotation": 3_000.0,
    "tqqq_growth_income": 500.0,
    "soxl_soxx_trend_income": 1_000.0,
    "russell_1000_multi_factor_defensive": 30_000.0,
    "tech_communication_pullback_enhancement": 10_000.0,
    "qqq_tech_enhancement": 10_000.0,
    "mega_cap_leader_rotation_dynamic_top20": 10_000.0,
    "mega_cap_leader_rotation_aggressive": 10_000.0,
    "mega_cap_leader_rotation_top50_balanced": 10_000.0,
    "dynamic_mega_leveraged_pullback": 10_000.0,
}

SMALL_ACCOUNT_WARNING_REASON = "integer_shares_min_position_value_may_prevent_backtest_replication"


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_profile(profile: str | None) -> str:
    return str(profile or "").strip().lower()


def get_min_recommended_equity_usd(profile: str | None) -> float | None:
    return MIN_RECOMMENDED_EQUITY_USD.get(normalize_profile(profile))


def build_account_size_diagnostics(
    profile: str | None,
    portfolio_total_equity: float | None,
) -> dict[str, object]:
    min_recommended = get_min_recommended_equity_usd(profile)
    if min_recommended is None:
        return {}

    diagnostics: dict[str, object] = {
        "min_recommended_equity_usd": float(min_recommended),
    }
    total_equity = _coerce_float(portfolio_total_equity)
    if total_equity is None:
        diagnostics["small_account_warning"] = False
        return diagnostics

    diagnostics["portfolio_total_equity"] = total_equity
    warning = total_equity < float(min_recommended)
    diagnostics["small_account_warning"] = warning
    if warning:
        diagnostics["small_account_warning_reason"] = SMALL_ACCOUNT_WARNING_REASON
    return diagnostics


def build_account_size_diagnostics_from_context(
    profile: str | None,
    ctx: Any,
) -> dict[str, object]:
    portfolio = getattr(ctx, "portfolio", None)
    portfolio_total_equity = getattr(portfolio, "total_equity", None)
    if portfolio_total_equity is None:
        market_data = getattr(ctx, "market_data", {}) or {}
        if isinstance(market_data, Mapping):
            snapshot = market_data.get("portfolio_snapshot")
            portfolio_total_equity = getattr(snapshot, "total_equity", None)
    return build_account_size_diagnostics(profile, _coerce_float(portfolio_total_equity))


def append_account_size_warning(text: str, diagnostics: Mapping[str, object]) -> str:
    message = str(text or "").strip()
    if not diagnostics.get("small_account_warning"):
        return message

    portfolio_total_equity = _coerce_float(diagnostics.get("portfolio_total_equity"))
    min_recommended = _coerce_float(diagnostics.get("min_recommended_equity_usd"))
    if portfolio_total_equity is None or min_recommended is None:
        return message

    warning = (
        "small_account_warning=true "
        f"portfolio_equity=${portfolio_total_equity:,.0f} "
        f"min_recommended_equity=${min_recommended:,.0f} "
        f"reason={SMALL_ACCOUNT_WARNING_REASON}"
    )
    if not message:
        return warning
    return f"{message} | {warning}"
