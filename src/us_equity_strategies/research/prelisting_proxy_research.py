"""Research-only proxy paths; synthetic returns are not leveraged ETF history."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import hashlib
import json
from bisect import bisect_left


@dataclass(frozen=True)
class SyntheticAssumptions:
    annual_expense: float
    annual_financing: float
    annual_tracking_drag: float
    sessions_per_year: int = 252


def synthetic_three_times_path(bars, assumptions: SyntheticAssumptions):
    """Compound close-to-close proxy returns with explicitly supplied annual drags.

    Each bar is ``(session, adjusted_close)``; no execution prices or positions
    are produced. The first bar anchors the path and has no calculated return.
    The constant financing input is a modeling assumption, not a historical rate.
    """
    if type(assumptions) is not SyntheticAssumptions or assumptions.sessions_per_year != 252:
        raise ValueError("DECLARED_ASSUMPTIONS_REQUIRED")
    rates = (assumptions.annual_expense, assumptions.annual_financing, assumptions.annual_tracking_drag)
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x < 0 for x in rates):
        raise ValueError("FINITE_NONNEGATIVE_ASSUMPTIONS_REQUIRED")
    if len(bars) < 2:
        raise ValueError("TWO_SOURCE_BARS_REQUIRED")
    output = []
    previous_session = None
    previous_close = None
    nav = 1.0
    daily_drag = sum(rates) / assumptions.sessions_per_year
    if not math.isfinite(daily_drag):
        raise ValueError("FINITE_TOTAL_DRAG_REQUIRED")
    for session, close in bars:
        if type(session) is not date or (previous_session is not None and session <= previous_session):
            raise ValueError("STRICTLY_ORDERED_SESSIONS_REQUIRED")
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
            raise ValueError("POSITIVE_FINITE_ADJUSTED_CLOSE_REQUIRED")
        daily_return = None if previous_close is None else 3.0 * (close / previous_close - 1.0) - daily_drag
        if daily_return is not None:
            if not math.isfinite(daily_return) or daily_return <= -1.0:
                raise ValueError("SYNTHETIC_INSOLVENCY_UNMODELED")
            nav *= 1.0 + daily_return
            if not math.isfinite(nav):
                raise ValueError("SYNTHETIC_NAV_OVERFLOW")
        output.append({"session": session.isoformat(), "synthetic_return": daily_return, "synthetic_nav": nav})
        previous_session, previous_close = session, close
    canonical = json.dumps([(d.isoformat(), float(c)) for d, c in bars],
                           separators=(",", ":"), allow_nan=False).encode()
    return {"scope": "synthetic_proxy_not_etf", "research_only": True, "executable": False,
            "source_bars_sha256": hashlib.sha256(canonical).hexdigest(),
            "promotion_eligible": False, "assumptions": {"annual_expense": assumptions.annual_expense,
            "annual_financing": assumptions.annual_financing, "annual_tracking_drag": assumptions.annual_tracking_drag,
            "sessions_per_year": assumptions.sessions_per_year}, "rows": output}


def classify_session(session: date, first_tradable_session: date) -> str:
    """Classify against a caller-verified listing date, not a data-file start."""
    if type(session) is not date or type(first_tradable_session) is not date:
        raise ValueError("SESSION_AND_LISTING_SESSION_REQUIRED")
    return "PRELISTING_PROXY_ONLY" if session < first_tradable_session else "POSTLISTING_DATA_REQUIRED"


def proxy_signal_diagnostics(bars, *, first_tradable_session: date):
    """Evaluate the frozen signal on underlying bars, with no ETF return path."""
    import numpy as np
    from .range_rsi2_study import indicators

    if type(first_tradable_session) is not date or len(bars) < 2:
        raise ValueError("LISTING_AND_SOURCE_BARS_REQUIRED")
    previous = None
    for session, close in bars:
        if type(session) is not date or (previous is not None and session <= previous):
            raise ValueError("STRICTLY_ORDERED_SESSIONS_REQUIRED")
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
            raise ValueError("POSITIVE_FINITE_ADJUSTED_CLOSE_REQUIRED")
        previous = session
    values = indicators(np.asarray([close for _, close in bars], dtype=float))
    rows = []
    for i, (session, _) in enumerate(bars):
        def finite_or_none(value):
            return float(value) if math.isfinite(value) else None
        rows.append({"session": session.isoformat(), "tradability": classify_session(session, first_tradable_session),
                     "sma200": finite_or_none(values["sma200"][i]), "rsi2": finite_or_none(values["rsi2"][i]),
                     "er20": finite_or_none(values["er20"][i]),
                     "range": bool(values["range"][i]) if math.isfinite(values["sma200"][i])
                     and math.isfinite(values["er20"][i]) else None})
    canonical = json.dumps([(d.isoformat(), float(c)) for d, c in bars],
                           separators=(",", ":"), allow_nan=False).encode()
    return {"scope": "underlying_signal_only", "research_only": True, "executable": False,
            "promotion_eligible": False, "source_bars_sha256": hashlib.sha256(canonical).hexdigest(),
            "first_tradable_session": first_tradable_session.isoformat(), "rows": rows}


def pre2026_overlap_diagnostic(proxy_bars, etf_bars, assumptions: SyntheticAssumptions):
    """Compare fixed hypothetical one-day returns to observed ETF returns.

    Adjacent ETF and proxy observations must have the same two sessions. All
    model inputs are frozen before computing this diagnostic; no parameter fit
    is performed and 2026 observations are excluded.
    """
    proxy = {d: c for d, c in proxy_bars if d.year < 2026}
    etf = [(d, c) for d, c in etf_bars if d.year < 2026]
    if len(proxy) != sum(d.year < 2026 for d, _ in proxy_bars):
        raise ValueError("DUPLICATE_PROXY_SESSION")
    if any(etf[i][0] <= etf[i - 1][0] for i in range(1, len(etf))):
        raise ValueError("STRICTLY_ORDERED_ETF_SESSIONS_REQUIRED")
    positions = {session: i for i, session in enumerate(sorted(proxy))}
    differences = []
    for (d0, p0), (d1, p1) in zip(etf, etf[1:]):
        if d0 not in proxy or d1 not in proxy:
            raise ValueError("UNMATCHED_OVERLAP_SESSION")
        # Reject calendar gaps in the proxy sequence, not merely missing endpoints.
        if positions[d1] != positions[d0] + 1:
            raise ValueError("UNMATCHED_OVERLAP_ADJACENCY")
        synthetic = synthetic_three_times_path([(d0, proxy[d0]), (d1, proxy[d1])], assumptions)["rows"][1]["synthetic_return"]
        if p0 <= 0 or p1 <= 0 or not math.isfinite(p0) or not math.isfinite(p1):
            raise ValueError("INVALID_ETF_CLOSE")
        differences.append(synthetic - (p1 / p0 - 1.0))
    if not differences:
        raise ValueError("NO_OVERLAP")
    return {"scope": "pre2026_tracking_diagnostic_not_fit", "research_only": True,
            "executable": False, "promotion_eligible": False, "sessions": len(differences),
            "mean_daily_difference": math.fsum(differences) / len(differences),
            "rmse_daily_difference": math.sqrt(math.fsum(x * x for x in differences) / len(differences)),
            "last_session": etf[-1][0].isoformat()}


def floating_rate_three_times_path(bars, rates, *, spread: float, tracking_drag: float):
    """Apply declared calendar-accrued drag using a strictly earlier DFF row."""
    if spread not in (0.0, .005, .01) or tracking_drag not in (0.0, .005):
        raise ValueError("UNREGISTERED_SCENARIO")
    if len(bars) < 2 or len(rates) < 1:
        raise ValueError("SOURCE_HISTORY_REQUIRED")
    dates = []
    for day, rate in rates:
        if type(day) is not date or (dates and day <= dates[-1]):
            raise ValueError("RATE_SESSIONS_NOT_ORDERED")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
            raise ValueError("INVALID_RATE")
        dates.append(day)
    nav, output = 1.0, []
    previous = None
    for session, close in bars:
        if type(session) is not date or (previous is not None and session <= previous[0]):
            raise ValueError("STRICTLY_ORDERED_SESSIONS_REQUIRED")
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
            raise ValueError("POSITIVE_FINITE_ADJUSTED_CLOSE_REQUIRED")
        daily_return = None
        rate_day = None
        if previous is not None:
            idx = bisect_left(dates, session) - 1
            if idx < 0 or (session - dates[idx]).days > 7:
                raise ValueError("LAGGED_RATE_UNAVAILABLE")
            rate_day, rate = rates[idx]
            drag = (.01 + 2 * (rate + spread) + tracking_drag) * (session - previous[0]).days / 365.0
            daily_return = 3 * (close / previous[1] - 1) - drag
            if not math.isfinite(daily_return) or daily_return <= -1:
                raise ValueError("SYNTHETIC_INSOLVENCY_UNMODELED")
            nav *= 1 + daily_return
            if not math.isfinite(nav):
                raise ValueError("SYNTHETIC_NAV_OVERFLOW")
        output.append({"session": session.isoformat(), "synthetic_return": daily_return,
                       "synthetic_nav": nav, "assumed_rate_observation": rate_day.isoformat() if rate_day else None})
        previous = (session, close)
    return {"scope": "synthetic_proxy_floating_rate_not_etf", "research_only": True, "executable": False,
            "promotion_eligible": False, "availability": "ASSUMED_LAG_NOT_PIT_PROOF", "rows": output,
            "annual_management": .01, "spread": spread, "tracking_drag": tracking_drag}


def floating_rate_overlap_diagnostic(proxy_bars, etf_bars, rates, *, spread, tracking_drag):
    """Compare one-day hypothetical and observed returns through 2025 only."""
    proxy = {d: c for d, c in proxy_bars if d.year < 2026}
    if len(proxy) != sum(d.year < 2026 for d, _ in proxy_bars):
        raise ValueError("DUPLICATE_PROXY_SESSION")
    etf = [(d, c) for d, c in etf_bars if d.year < 2026]
    if len(etf) < 2 or any(etf[i][0] <= etf[i - 1][0] for i in range(1, len(etf))):
        raise ValueError("STRICTLY_ORDERED_ETF_SESSIONS_REQUIRED")
    positions = {session: i for i, session in enumerate(sorted(proxy))}
    if any(d not in proxy for d, _ in etf):
        raise ValueError("UNMATCHED_OVERLAP_SESSION")
    if any(positions[etf[i][0]] != positions[etf[i - 1][0]] + 1 for i in range(1, len(etf))):
        raise ValueError("UNMATCHED_OVERLAP_ADJACENCY")
    if any(isinstance(p, bool) or not isinstance(p, (float, int)) or not math.isfinite(p) or p <= 0 for _, p in etf):
        raise ValueError("INVALID_ETF_CLOSE")
    proxy_overlap = [(d, proxy[d]) for d, _ in etf]
    synthetic = floating_rate_three_times_path(proxy_overlap, rates, spread=spread, tracking_drag=tracking_drag)
    diff = [synthetic["rows"][i]["synthetic_return"] - (etf[i][1] / etf[i - 1][1] - 1)
            for i in range(1, len(etf))]
    return {"scope": "pre2026_tracking_diagnostic_not_fit", "research_only": True, "executable": False,
            "promotion_eligible": False, "sessions": len(diff), "last_session": etf[-1][0].isoformat(),
            "mean_daily_difference": math.fsum(diff) / len(diff),
            "rmse_daily_difference": math.sqrt(math.fsum(x * x for x in diff) / len(diff))}
