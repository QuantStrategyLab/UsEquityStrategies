"""Self-financing research harness; no market-data or broker client.

The baseline is supplied as a rule callback, evaluated against each book's own
prior-close equity/holdings. A frozen historical target tape is insufficient
for path-dependent live income rules. Real inputs require a qualified cloud
adapter; this module does not invent one or silently use no_plugin_core.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
import math
import random
from statistics import mean, stdev
from typing import Callable, Mapping, Sequence

from .rs04_b3 import (Bar, Features, State, advance, build_features, cap_targets,
                      combined_targets, ewma_forecast, record_fill)
from .rs04_b3_config import CONFIG, config_identity


@dataclass
class Ledger:
    cash: float
    quantity: dict[str, int]
    receivables: dict[date, float]
    last_fills: list[dict] = field(default_factory=list)

    def nav(self, prices: Mapping[str, float]) -> float:
        return self.cash+math.fsum(q*prices[s] for s, q in self.quantity.items())+math.fsum(self.receivables.values())

    def split(self, symbol: str, ratio: float) -> None:
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("SPLIT_INVALID")
        q = self.quantity.get(symbol, 0)*ratio
        if not math.isclose(q, round(q), abs_tol=1e-9):
            raise ValueError("FRACTIONAL_SPLIT_REQUIRES_CASH_IN_LIEU_ADAPTER")
        self.quantity[symbol] = round(q)

    def accrue(self, symbol: str, amount: float, pay_session: date) -> None:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("DIVIDEND_INVALID")
        self.receivables[pay_session] = self.receivables.get(pay_session, 0.)+self.quantity.get(symbol, 0)*amount

    def pay(self, session: date) -> None:
        for day in list(self.receivables):
            if day <= session:
                self.cash += self.receivables.pop(day)

    def rebalance(self, targets: Mapping[str, float], prices: Mapping[str, float], *,
                  profile: str, cost_multiplier: float, max_quantities: Mapping[str, int] | None = None,
                  loss_per_share: Mapping[str, float] | None = None) -> dict[str, float]:
        """One net trade per security; both BOXX legs and the terminal fill cost.

        Conservative fixed point budgets quantities against post-cost NAV.
        Dollars, dollar minimum ticket, whole shares, actual cash, no financing.
        """
        if cost_multiplier not in CONFIG.cost_multipliers:
            raise ValueError("COST_SCENARIO_NOT_REGISTERED")
        target = cap_targets(targets, profile)
        symbols = sorted(set(target)|set(self.quantity))
        if any(s not in prices or not math.isfinite(prices[s]) or prices[s] <= 0 for s in symbols):
            raise ValueError("PRICE_INVALID")
        initial = self.nav(prices)
        if initial <= 0 or self.cash < 0:
            raise ValueError("LEDGER_INVALID")
        total_cap = CONFIG.soxl_total if profile == "SOXL" else CONFIG.tqqq_total

        def costs(qs):
            details = []
            for symbol in symbols:
                change = qs[symbol]-self.quantity.get(symbol, 0)
                if not change:
                    continue
                notional = abs(change)*prices[symbol]
                commission = cost_multiplier*max(CONFIG.minimum_commission_usd,
                    CONFIG.commission_per_share_usd*abs(change), CONFIG.commission_bps/10000*notional)
                slippage = cost_multiplier*CONFIG.slippage_including_spread_bps/10000*notional
                details.append(dict(symbol=symbol, change=change, notional=notional,
                                    commission=commission, slippage=slippage))
            return details

        budget = initial
        for _ in range(64):
            qs = {s: math.floor(target.get(s, 0.)*budget/prices[s]) for s in symbols}
            for symbol, maximum in (max_quantities or {}).items():
                if symbol in qs:
                    qs[symbol] = min(qs[symbol], maximum)
            for symbol, loss in (loss_per_share or {}).items():
                if not math.isfinite(loss) or loss <= 0:
                    raise ValueError("LOSS_PER_SHARE_INVALID")
                if symbol in qs:
                    qs[symbol] = min(qs[symbol], math.floor(CONFIG.mr_loss_budget*budget/loss))
            details = costs(qs)
            after = initial-math.fsum(d["commission"]+d["slippage"] for d in details)
            if after >= budget-1e-10:
                break
            budget = after
        else:
            raise ValueError("COST_FIXED_POINT_FAILED")
        # Receivables count in NAV but cannot finance purchases or reserved cash.
        cash_after = after-math.fsum(qs[s]*prices[s] for s in symbols)-math.fsum(self.receivables.values())
        while cash_after < (1-total_cap)*after-1e-9:
            candidates = [s for s in symbols if qs[s] > 0]
            if not candidates:
                raise ValueError("SETTLED_CASH_RESERVE_UNAVAILABLE")
            symbol = max(candidates, key=lambda s: qs[s]*prices[s])
            qs[symbol] -= 1
            details = costs(qs)
            after = initial-math.fsum(d["commission"]+d["slippage"] for d in details)
            cash_after = after-math.fsum(qs[s]*prices[s] for s in symbols)-math.fsum(self.receivables.values())
        actual = {s: qs[s]*prices[s]/after for s in symbols}
        capped = cap_targets(actual, profile)
        if any(actual.get(s, 0.) > capped.get(s, 0.)+1e-10 for s in actual):
            raise ValueError("POST_COST_CAP_BREACH")
        self.cash, self.quantity, self.last_fills = cash_after, qs, details
        if abs(self.nav(prices)-after) > 1e-7 or self.cash < -1e-9:
            raise ValueError("CASH_RECONCILIATION_FAILED")
        return {k: math.fsum(d[k] for d in details) for k in ("commission", "slippage")}


@dataclass(frozen=True)
class Session:
    session: date
    prices: Mapping[str, float]
    features: Features | None
    splits: Mapping[str, float] = field(default_factory=dict)
    dividends: tuple[tuple[str, float, date], ...] = ()
    hard_risk: bool = False


@dataclass(frozen=True)
class Replay:
    points: tuple[dict, ...]  # cloud-private only for real inputs
    metrics: dict
    episodes: tuple[dict, ...]


def path_metrics(returns: Sequence[float], *, turnover: float) -> dict:
    """Daily net return metrics, ddof=1, initial NAV included, rf/target=0."""
    if not returns or any(not math.isfinite(r) or r <= -1 for r in returns):
        raise ValueError("RETURN_PATH_INVALID")
    n, nav, peak, dd = len(returns), 1., 1., 0.
    for r in returns:
        nav *= 1+r
        peak = max(peak, nav)
        dd = max(dd, 1-nav/peak)
    cagr = math.expm1(sum(math.log1p(r) for r in returns)*CONFIG.annual_sessions/n)
    sigma = stdev(returns) if n > 1 else 0.
    downside = math.sqrt(sum(min(0., r)**2 for r in returns)/n)
    return {"cagr": cagr, "max_drawdown": dd,
            "sharpe": mean(returns)/sigma*math.sqrt(CONFIG.annual_sessions) if sigma else None,
            "sortino": mean(returns)/downside*math.sqrt(CONFIG.annual_sessions) if downside else None,
            "calmar": cagr/dd if dd else None, "turnover_one_side": turnover,
            "observations": n, "total_return": nav-1, "rf_and_downside_target": 0.,
            "deflated_sharpe": None, "deflated_sharpe_status": "UNCOMPUTABLE",
            "deflated_sharpe_reason": "CUMULATIVE_TRIAL_UNIVERSE_NOT_ATTESTED"}


def paired_giveback(baseline: Sequence[float], candidate: Sequence[float]) -> dict:
    if len(baseline) != len(candidate):
        raise ValueError("PAIRED_SESSIONS_NOT_ALIGNED")
    rows = []
    for start in range(0, len(baseline)-CONFIG.giveback_block+1, CONFIG.giveback_block):
        stats = []
        for path in (baseline, candidate):
            nav, peak = 1., 1.
            for r in path[start:start+CONFIG.giveback_block]:
                nav *= 1+r
                peak = max(peak, nav)
            stats.append((peak-1, nav-1))
        (bp, be), (cp, ce) = stats
        if bp >= CONFIG.giveback_min_peak:
            rows.append({"baseline_g": (bp-be)/bp, "candidate_g": (cp-ce)/cp if cp > 0 else None,
                         "paired": ((bp-be)-(cp-ce))/bp})
    return {"eligible_blocks": len(rows),
            "baseline_giveback": mean(r["baseline_g"] for r in rows) if rows else None,
            "candidate_giveback": mean(r["candidate_g"] for r in rows if r["candidate_g"] is not None)
                if any(r["candidate_g"] is not None for r in rows) else None,
            "candidate_no_positive_peak_blocks": sum(r["candidate_g"] is None for r in rows),
            "paired_reduction_normalized_by_baseline_peak": mean(r["paired"] for r in rows) if rows else None,
            "inference": "DESCRIPTIVE_ONLY_UNTIL_QUALIFIED_OOS_AND_BOOTSTRAP"}


def replay(rows: Sequence[Session], baseline: Callable, *, profile: str, mode: str,
           kelly_full: float | Sequence[float], sizing_returns: Sequence[float] | None = None,
           cost_multiplier: float = 1., synthetic: bool = True) -> Replay:
    """Synthetic harness; cloud real adapter must establish live parity first.

    No real-data qualification is inferred from a caller setting a boolean.
    The callback receives only the past and a copy of prior-close book state.
    """
    if synthetic is not True:
        raise ValueError("QUALIFIED_LIVE_BASELINE_CLOUD_ADAPTER_REQUIRED")
    if mode not in ("baseline", "sizing_control", "b3") or len(rows) < 2:
        raise ValueError("REPLAY_INPUT_INVALID")
    if any(b.session <= a.session for a, b in zip(rows, rows[1:])):
        raise ValueError("SESSIONS_NOT_ORDERED")
    if sizing_returns is not None and len(sizing_returns) != len(rows):
        raise ValueError("SIZING_RETURNS_NOT_ALIGNED")
    book = Ledger(CONFIG.initial_usd, {}, {})
    state, peak, prior_nav = State(), CONFIG.initial_usd, CONFIG.initial_usd
    points, episodes, past_sizing_returns = [], [], []
    regime_returns = defaultdict(list)
    regime_counts, switches, last_regime = Counter(), 0, None
    turnover, total_cost = 0., {"commission": 0., "slippage": 0.}
    episode_balance, episode_peak = 0., 0.
    for i, row in enumerate(rows):
        old_q = book.quantity.get(profile, 0)
        targets, reasons, factor, sleeve = {}, (), 1., "TREND"
        upstream_targets, b1_budget, b2_factor = {}, None, None
        if i:
            previous = rows[i-1]
            snapshot = {"cash": book.cash, "quantity": dict(book.quantity), "nav": prior_nav,
                        "receivables": math.fsum(book.receivables.values())}
            raw = dict(baseline(tuple(rows[:i]), snapshot))
            capped = cap_targets(raw, profile)
            if any(raw.get(s, 0.) > capped.get(s, 0.)+1e-12 for s in raw):
                raise ValueError("BASELINE_CAP_BREACH")
            held = {s: q*previous.prices[s]/prior_nav for s, q in book.quantity.items()}
            if mode == "b3":
                decision = advance(state, previous.features, decision=row.session, index=i,
                                   profile=profile, baseline=raw, held=held, hard_risk=previous.hard_risk)
                state, targets, reasons, sleeve = decision.state, decision.targets, decision.reasons, decision.sleeve
            else:
                targets = raw
            upstream_targets = dict(targets)
            if mode != "baseline":
                full = kelly_full[i] if isinstance(kelly_full, Sequence) else kelly_full
                sized = combined_targets(targets, profile=profile, kelly_full=full,
                    forecast_vol=ewma_forecast(past_sizing_returns), drawdown=max(0., 1-prior_nav/peak))
                targets, factor = sized.targets, sized.sizing_factor
                b1_budget, b2_factor = sized.b1_nominal_budget, sized.b2_drawdown_factor
        # Corporate actions apply only after the t-1 decision, before t-close fill.
        for symbol, ratio in row.splits.items():
            book.split(symbol, ratio)
            if symbol == profile and state.entry:
                state = replace(state, entry=state.entry/ratio, initial_r=state.initial_r/ratio,
                                peak=state.peak/ratio, stop=state.stop/ratio)
                old_q = book.quantity.get(profile, 0)
        for symbol, amount, pay_day in row.dividends:
            entitlement = book.quantity.get(symbol, 0)*amount
            if pay_day < row.session:
                raise ValueError("DIVIDEND_PAY_BEFORE_EX_DATE")
            book.accrue(symbol, amount, pay_day)
            if symbol == profile and old_q:
                episode_balance += entitlement
        book.pay(row.session)
        prefill_nav = book.nav(row.prices)
        max_quantities, loss_per_share = {}, {}
        if mode == "b3" and sleeve == "MR":
            if old_q:
                max_quantities[profile] = old_q
            elif i and rows[i-1].features:
                loss_per_share[profile] = (CONFIG.entry_atr_r*rows[i-1].features.atr/row.splits.get(profile, 1.))
        if old_q:
            episode_peak = max(episode_peak, episode_balance+old_q*row.prices[profile])
        costs = book.rebalance(targets, row.prices, profile=profile, cost_multiplier=cost_multiplier,
                               max_quantities=max_quantities, loss_per_share=loss_per_share)
        final_nav = book.nav(row.prices)
        daily = final_nav/prior_nav-1
        weights = {s: q*row.prices[s]/final_nav for s, q in book.quantity.items()}
        for name, value in costs.items():
            total_cost[name] += value
        turnover += math.fsum(f["notional"] for f in book.last_fills)/prefill_nav
        new_q = book.quantity.get(profile, 0)
        if not old_q and new_q:
            episode_balance, episode_peak = 0., 0.
        for fill in book.last_fills:
            if fill["symbol"] == profile:
                episode_balance -= fill["change"]*row.prices[profile]+fill["commission"]+fill["slippage"]
        if old_q or new_q:
            economic_pnl = episode_balance+new_q*row.prices[profile]
            episode_peak = max(episode_peak, economic_pnl)
            if new_q == 0:
                episodes.append({"closed": True, "net_pnl": economic_pnl, "mfe": episode_peak,
                                 "giveback_amount": episode_peak-economic_pnl,
                                 "giveback_ratio": (episode_peak-economic_pnl)/episode_peak if episode_peak > 0 else None,
                                 "sleeve": state.sleeve})
        if mode == "b3" and (old_q or new_q):
            f = rows[i-1].features if i else None
            if f is None:
                # A missing observation can close existing risk, but cannot open it.
                atr = state.initial_r/CONFIG.entry_atr_r
                if new_q > old_q or atr <= 0:
                    raise ValueError("FILL_WITHOUT_OBSERVABLE_ATR")
            else:
                atr = f.atr/row.splits.get(profile, 1.)
            fill = next((f for f in book.last_fills if f["symbol"] == profile), None)
            buy_fees = ((fill["commission"]+fill["slippage"])/(new_q-old_q)
                        if fill and new_q > old_q else 0.)
            state = record_fill(state, old_quantity=old_q, quantity=new_q, price=row.prices[profile],
                                fee_per_share=buy_fees, atr=atr, index=i, sleeve=sleeve)
        regime = state.regime if mode == "b3" else "BASELINE_AS_IS"
        if last_regime is not None and regime != last_regime:
            switches += 1
        last_regime = regime
        regime_counts[regime] += 1
        regime_returns[regime].append(daily)
        points.append(dict(session=row.session, nav=final_nav, net_return=daily, cash=book.cash,
                           market_value=sum(q*row.prices[s] for s, q in book.quantity.items()),
                           receivables=sum(book.receivables.values()), weights=weights, targets=dict(targets),
                           nominal=sum(weights.values()), effective=sum(weights.values())+2*weights.get(profile, 0.),
                           sizing_factor=factor, upstream_targets=upstream_targets,
                           b1_nominal_budget=b1_budget, b2_drawdown_factor=b2_factor,
                           regime=regime, reasons=reasons))
        if sizing_returns is None:
            # Invented fixture driver only, never an asserted RS-04 net return path.
            past_sizing_returns.append(row.prices[profile]/rows[i-1].prices[profile]-1 if i else 0.)
        else:
            past_sizing_returns.append(sizing_returns[i])
        peak, prior_nav = max(peak, final_nav), final_nav
    if book.quantity.get(profile, 0):
        pnl = episode_balance+book.quantity[profile]*rows[-1].prices[profile]
        episodes.append({"closed": False, "net_pnl": pnl, "mfe": episode_peak,
                         "giveback_amount": episode_peak-pnl, "sleeve": state.sleeve})
    metrics = path_metrics([p["net_return"] for p in points], turnover=turnover)
    metrics.update(synthetic=True, research_only=True, promotion_eligible=False, live_ready=False,
                   no_order=True, budget_effect="NONE", config_sha256=config_identity(), mode=mode,
                   fees=total_cost, fee_status=CONFIG.fee_status, regime_sessions=dict(regime_counts),
                   regime_switches=switches, regime_stats={r: path_metrics(rs, turnover=0.) for r, rs in regime_returns.items()},
                   max_combined_nominal=max(p["nominal"] for p in points),
                   max_combined_effective=max(p["effective"] for p in points),
                   combined_exposure_path="PRIVATE_POINTS_UPSTREAM_TARGET_AND_B1_B2_FACTOR_AND_POST_COST_WEIGHTS",
                   closed_mr_episodes=sum(e["closed"] and e["sleeve"] == "MR" for e in episodes),
                   acceptance="NOT_EVALUATED_SYNTHETIC_ONLY")
    return Replay(tuple(points), metrics, tuple(episodes))


def synthetic_sessions(count: int = 1100) -> list[Session]:
    """Seeded invented OHLC. Calendar labels are deliberately not XNYS evidence."""
    rng = random.Random(CONFIG.seed)
    bars, rows, price = [], [], 100.
    for i in range(count):
        price *= math.exp(.0003+.012*math.sin(i/11)+rng.gauss(0, .007))
        day = date(2000, 1, 1)+timedelta(days=i)
        bars.append(Bar(day, price*1.01, price*.99, price))
        rows.append(Session(day, {"SOXL": price, "SOXX": 100+i*.01, "TQQQ": price,
                                   "QQQM": 100+i*.01, "BOXX": 100+i*.005}, None))
    features = build_features(bars, bars)
    return [replace(r, features=f) for r, f in zip(rows, features)]
