"""Preregistered retrospective trend/range-RSI2 screen; no live dependencies."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from dataclasses import dataclass, field
import sys
import argparse
from datetime import date, datetime, timezone
from quant_platform_kit.strategy_lifecycle.contracts import ResearchTrialRecord, ResearchTrialStatus
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

import numpy as np
import pandas as pd

FROZEN_PREREGISTRATION_SHA256 = "d2f4346e8ea9e0cd29af23e5175c1f98a63d88601512d636b7f4b191fd1fed8b"
FROZEN_CONFIG_CANONICAL_SHA256 = "ea1b876d4744e0ac363a91ae031df919e21404789d73f3828220fdb1749a8a12"
FROZEN_START = "2011-01-03"
FROZEN_END = "2026-10-30"


@dataclass
class PricePair:
    symbol: str
    signal_symbol: str
    dates: pd.DatetimeIndex
    opens: np.ndarray
    closes: np.ndarray
    base_weights: np.ndarray
    indicators: dict[str, np.ndarray]


@dataclass
class OverlayLifecycle:
    """Execution episodes prevent a delayed hold from becoming a new entry."""
    active: bool = False
    active_generation: int = 0
    pending_entry_generation: int | None = None
    next_generation: int = 0
    held_sessions: int = 0
    closed_generations: set[int] = field(default_factory=set)

    @property
    def effective_held(self):
        return self.active or self.pending_entry_generation is not None

    def make_intent(self, flag, *, static_range=False):
        if static_range:
            return "range_target", 0
        if flag:
            if self.active:
                return "continue", self.active_generation
            if self.pending_entry_generation is not None:
                return "continue", self.pending_entry_generation
            self.next_generation += 1
            self.pending_entry_generation = self.next_generation
            return "entry", self.next_generation
        if self.active:
            return "exit", self.active_generation
        if self.pending_entry_generation is not None:
            return "exit", self.pending_entry_generation
        return "noop", 0

    def consume(self, flag, kind, generation):
        before = self.active
        ignored = False
        if kind == "range_target":
            self.active = bool(flag)
        elif kind == "entry":
            if generation in self.closed_generations:
                ignored = True
            elif not self.active:
                self.active = True
                self.active_generation = generation
                if self.pending_entry_generation == generation:
                    self.pending_entry_generation = None
            elif self.active_generation != generation:
                ignored = True
        elif kind == "continue":
            ignored = not (self.active and self.active_generation == generation)
        elif kind == "exit":
            if self.active and self.active_generation == generation:
                self.active = False
                self.closed_generations.add(generation)
            elif not self.active and self.pending_entry_generation == generation:
                self.closed_generations.add(generation)
            else:
                ignored = True
            if generation in self.closed_generations and self.pending_entry_generation == generation:
                self.pending_entry_generation = None
        elif kind != "noop":
            raise ValueError("INVALID_OVERLAY_INTENT")
        entered = self.active and not before
        if not self.active or entered:
            self.held_sessions = 0
        return self.active, entered, ignored


def indicators(close: np.ndarray) -> dict[str, np.ndarray]:
    series = pd.Series(close)
    sma = series.rolling(200, min_periods=200).mean().to_numpy()
    changes = np.diff(close)
    er_den = series.diff().abs().rolling(20, min_periods=20).sum().to_numpy()
    er_num = (series - series.shift(20)).abs().to_numpy()
    er = np.divide(er_num, er_den, out=np.full(len(close), np.nan), where=er_den > 0)
    er[(er_den == 0) & (er_num == 0)] = 0.0
    rsi = np.full(len(close), np.nan)
    if len(changes) >= 2:
        gain = float(np.maximum(changes[:2], 0).mean())
        loss = float(np.maximum(-changes[:2], 0).mean())
        for i in range(2, len(close)):
            if i > 2:
                gain = (gain + max(float(changes[i-1]), 0.0)) / 2
                loss = (loss + max(float(-changes[i-1]), 0.0)) / 2
            rsi[i] = 50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100/(1+gain/loss)
    return {"sma200": sma, "rsi2": rsi, "er20": er,
            "trend": close > sma,
            "range": (np.abs(close/sma-1) <= .05) & (er <= .30)}


def make_pair(symbol, signal_symbol, dates, opens, closes, base_weights):
    return PricePair(symbol, signal_symbol, pd.DatetimeIndex(dates), np.asarray(opens, float),
                     np.asarray(closes, float), np.asarray(base_weights, float),
                     indicators(np.asarray(closes, float)[:,1]))


def load_pair(symbol: str, config: dict, input_paths: dict[str, Path]) -> PricePair:
    info = config["input_files"][symbol]
    path = input_paths[symbol]
    if hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
        raise ValueError("INPUT_HASH_MISMATCH")
    frame = pd.read_csv(path)
    fields = ["open","high","low","close","volume"]
    frame = frame.loc[frame.symbol.isin(info["trade_symbols"])].copy()
    if frame.duplicated(["symbol","as_of"]).any():
        raise ValueError("DUPLICATE_INPUT")
    for price_field in fields:
        frame[price_field] = pd.to_numeric(frame[price_field], errors="raise")
    if not np.isfinite(frame[fields]).all().all():
        raise ValueError("NONFINITE_INPUT")
    if (frame[["open","high","low","close"]] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError("INVALID_PRICE_OR_VOLUME")
    # Allow numerical serialization noise in identical adjusted high/close values.
    tol = 1e-8 * frame.high
    if ((frame.low > frame[["open","close"]].min(axis=1)+tol) |
        (frame.high < frame[["open","close"]].max(axis=1)-tol)).any():
        raise ValueError("OHLC_INCONSISTENT")
    frame["date"] = pd.to_datetime(frame.as_of, format="%Y-%m-%d")
    if (frame.date.dt.dayofweek > 4).any():
        raise ValueError("WEEKEND_EQUITY_BAR")
    earliest_levered = frame.loc[frame.symbol == symbol,"date"].min()
    frame = frame.loc[(frame.date >= earliest_levered) & (frame.date <= pd.Timestamp(config["evaluation_end"]))]
    dates_a = set(frame.loc[frame.symbol == symbol,"date"])
    dates_b = set(frame.loc[frame.symbol == info["signal_symbol"],"date"])
    if dates_a != dates_b:
        raise ValueError("PAIR_CALENDARS_DIFFER")
    opens = frame.pivot(index="date",columns="symbol",values="open").sort_index()[info["trade_symbols"]]
    closes = frame.pivot(index="date",columns="symbol",values="close").sort_index()[info["trade_symbols"]]
    if opens.isna().any().any() or closes.isna().any().any():
        raise ValueError("MISSING_PAIR_BAR")
    return make_pair(symbol,info["signal_symbol"],opens.index,opens.to_numpy(),closes.to_numpy(),info["base_weights"])


def trade_to_target(shares, cash, opening, weights, commission_bps, slippage_bps):
    """Sell first; proportional buy scaling makes the account self financing."""
    shares = np.asarray(shares,float).copy()
    opening = np.asarray(opening,float)
    weights = np.asarray(weights,float)
    if (not np.isfinite(opening).all() or np.any(opening <= 0) or
        not np.isfinite(weights).all() or np.any(weights < 0) or weights.sum() > 1+1e-12):
        raise ValueError("INVALID_EXECUTION_INPUT")
    nav_before = float(cash + shares@opening)
    desired = nav_before * weights / opening
    delta = desired-shares
    c, s = commission_bps/10000, slippage_bps/10000
    sells = np.maximum(-delta,0)
    sell_fill = opening*(1-s)
    sell_commission = float((sells*sell_fill).sum()*c)
    cash += float(sells@sell_fill)-sell_commission
    shares -= sells
    buys = np.maximum(delta,0)
    buy_fill = opening*(1+s)
    requested_cash = float((buys*buy_fill).sum()*(1+c))
    scale = min(1.0,max(0.0,cash)/requested_cash) if requested_cash>0 else 0.0
    buys *= scale
    buy_commission = float((buys*buy_fill).sum()*c)
    cash -= float(buys@buy_fill)+buy_commission
    shares += buys
    if -1e-8 < cash < 0:
        cash=0.0
    fees=sell_commission+buy_commission
    slip=float((sells*opening*s).sum()+(buys*opening*s).sum())
    nav_after=float(cash+shares@opening)
    error=nav_before-nav_after-fees-slip
    if cash < -1e-8 or np.any(shares < -1e-10) or abs(error)>max(1e-7,nav_before*1e-12):
        raise AssertionError("SELF_FINANCING_ACCOUNTING_FAILED")
    notional=float((sells*opening).sum()+(buys*opening).sum())
    order_count=int(np.sum((sells+buys)>1e-10))
    return shares,cash,fees,slip,notional,order_count,error


def desired_target(pair: PricePair, i: int, strategy: str, overlay_active: bool, held_sessions: int):
    if not math.isfinite(float(pair.indicators["sma200"][i])):
        raise ValueError("INSUFFICIENT_WARMUP")
    base_on = strategy.startswith("trend") and bool(pair.indicators["trend"][i])
    weights=pair.base_weights.copy() if base_on else np.zeros(2)
    active=False
    if strategy == "trend_range_hold":
        active=bool(pair.indicators["range"][i])
    elif strategy.startswith("mr_") or strategy.startswith("trend_mr_"):
        threshold=int(strategy.rsplit("_",1)[1])
        range_ok=bool(pair.indicators["range"][i])
        rsi=float(pair.indicators["rsi2"][i])
        if not math.isfinite(rsi):
            raise ValueError("MISSING_RSI")
        active=(range_ok and rsi < 70 and held_sessions < 5) if overlay_active else (range_ok and rsi <= threshold)
    if active:
        weights[0]+=.10
    if weights.sum()>1+1e-12:
        raise AssertionError("TARGET_BUDGET_EXCEEDED")
    return weights,bool(active)


def simulate(pair: PricePair, strategy: str, commission_bps: int, slippage_bps: int,
             lag: int, *, start=None, end=None):
    start=pd.Timestamp(start or FROZEN_START)
    end=pd.Timestamp(end or FROZEN_END)
    first=int(pair.dates.searchsorted(start))
    last=int(pair.dates.searchsorted(end,side="right"))-1
    if first-lag<199 or last<first:
        raise ValueError("BAD_EVALUATION_WINDOW")
    cash=100000.0
    shares=np.zeros(2)
    previous_nav=100000.0
    overlay=OverlayLifecycle()
    pending={}
    for i in range(first-lag,first):
        target,flag=desired_target(pair,i,strategy,overlay.effective_held,overlay.held_sessions)
        kind,generation=overlay.make_intent(flag,static_range=strategy=="trend_range_hold")
        pending[i+lag]=(i,target,flag,kind,generation)
    rows=[]
    for i in range(first,last+1):
        fees=slip=turnover=0.0
        orders=0
        error=0.0
        mr_entry=False
        ignored=False
        source_i=-1
        target=np.zeros(2)
        opening_nav=float(cash+shares@pair.opens[i])
        if i in pending:
            source_i,target,flag,kind,generation=pending.pop(i)
            if source_i != i-lag:
                raise AssertionError("NONCAUSAL_FILL")
            active,mr_entry,ignored=overlay.consume(flag,kind,generation)
            # A stale continuation/no-op cannot create or destroy a different
            # overlay episode. Preserve the base target from the same old close.
            target=target.copy()
            target[0]-=.10 if flag else 0.0
            target[0]+=.10 if active else 0.0
            shares,cash,fees,slip,notional,orders,error=trade_to_target(
                shares,cash,pair.opens[i],target,commission_bps,slippage_bps)
            turnover=notional/opening_nav
        nav=float(cash+shares@pair.closes[i])
        if nav<=0 or not math.isfinite(nav):
            raise AssertionError("INVALID_NAV")
        if overlay.active:
            overlay.held_sessions+=1
        next_target,next_flag=desired_target(pair,i,strategy,overlay.effective_held,overlay.held_sessions)
        next_kind,next_generation=overlay.make_intent(next_flag,static_range=strategy=="trend_range_hold")
        pending[i+lag]=(i,next_target,next_flag,next_kind,next_generation)
        rows.append({"date":pair.dates[i].strftime("%Y-%m-%d"),"nav":nav,
                     "return":nav/previous_nav-1,"cash":cash,
                     "levered_quantity":shares[0],"underlying_quantity":shares[1],
                     "gross_exposure":float(shares@pair.closes[i])/nav,
                     "levered_exposure":shares[0]*pair.closes[i,0]/nav,
                     "overlay_active":int(overlay.active),"overlay_entry":int(mr_entry),
                     "overlay_held_sessions":overlay.held_sessions,
                     "overlay_generation":overlay.active_generation,
                     "stale_overlay_intent_ignored":int(ignored),
                     "commission":fees,"slippage":slip,"turnover":turnover,
                     "order_count":orders,"accounting_error":error,
                     "signal_date":pair.dates[source_i].strftime("%Y-%m-%d") if source_i>=0 else "",
                     "target_levered":target[0],"target_underlying":target[1],
                     "next_target_levered":next_target[0],"next_target_underlying":next_target[1],
                     "range_signal":int(pair.indicators["range"][i]),"rsi2":pair.indicators["rsi2"][i]})
        previous_nav=nav
    return pd.DataFrame(rows).set_index("date")


def metrics(frame):
    r=frame["return"].to_numpy()
    wealth=np.cumprod(1+r)
    peak=np.maximum.accumulate(np.r_[1.0,wealth])[1:]
    std=float(np.std(r,ddof=1)) if len(r)>1 else 0.0
    years=len(r)/252
    return {"sessions":len(r),"total_return":float(wealth[-1]-1),
            "cagr":float(wealth[-1]**(1/years)-1),
            "max_drawdown":float(np.min(wealth/peak-1)),
            "sharpe_rf0":float(np.mean(r)/std*np.sqrt(252)) if std>0 else None,
            "annualized_volatility":std*np.sqrt(252),
            "turnover_sum":float(frame.turnover.sum()),
            "turnover_annualized":float(frame.turnover.sum()/years),
            "average_gross_exposure":float(frame.gross_exposure.mean()),
            "average_levered_exposure":float(frame.levered_exposure.mean()),
            "overlay_active_fraction":float(frame.overlay_active.mean()),
            "overlay_nominal_exposure":float(frame.overlay_active.mean()*.10),
            "overlay_entries":int(frame.overlay_entry.sum()),
            "stale_overlay_intents_ignored":int(frame.stale_overlay_intent_ignored.sum()),
            "order_count":int(frame.order_count.sum()),
            "commission_paid":float(frame.commission.sum()),
            "slippage_impact":float(frame.slippage.sum()),
            "max_abs_accounting_error":float(frame.accounting_error.abs().max())}


def block_bootstrap(differences, *, seed=20261031, replications=2000, block=20):
    x=np.asarray(differences,float)
    n=len(x)
    rng=np.random.default_rng(seed)
    out=[]
    nblocks=math.ceil(n/block)
    offsets=np.arange(block)
    for _ in range(replications):
        starts=rng.integers(0,n,size=nblocks)
        sample=x[(starts[:,None]+offsets[None,:])%n].reshape(-1)[:n]
        out.append(float(sample.mean()*252))
    return {"annualized_arithmetic_incremental_mean":float(x.mean()*252),
            "percentile_95_low":float(np.quantile(out,.025)),
            "percentile_95_high":float(np.quantile(out,.975)),
            "block_sessions":block,"replications":replications,"seed":seed,
            "not_multiple_testing_adjusted":True}


def synthetic_pair():
    n=1000
    i=np.arange(n)
    underlying=100+.02*i+3*np.sin(.37*i)
    lev=30+.01*i+2*np.sin(.37*i)
    closes=np.column_stack([lev,underlying])
    opens=closes*(1+.002*np.cos(i*.47))[:,None]
    return make_pair("SYNTHETIC","SYNTHETIC_UNDERLYING",pd.bdate_range("2010-01-01",periods=n),opens,closes,[.45,.45])


def actual_data_causality_checks(pairs, config):
    details={}
    for name,pair in pairs.items():
        cut=int(pair.dates.searchsorted(pd.Timestamp("2022-12-30")))
        mutated=make_pair(pair.symbol,pair.signal_symbol,pair.dates,
            np.vstack([pair.opens[:cut+1],pair.opens[cut+1:]*2.7]),
            np.vstack([pair.closes[:cut+1],pair.closes[cut+1:]*2.7]),pair.base_weights)
        truncated=make_pair(pair.symbol,pair.signal_symbol,pair.dates[:cut+1],pair.opens[:cut+1],pair.closes[:cut+1],pair.base_weights)
        results={}
        for strategy in config["strategies"]:
            for lag in config["execution"]["lag_sessions"]:
                baseline=simulate(pair,strategy,2,5,lag)
                changed=simulate(mutated,strategy,2,5,lag)
                short=simulate(truncated,strategy,2,5,lag,end=truncated.dates[-1])
                pd.testing.assert_frame_equal(baseline.iloc[:len(short)],changed.iloc[:len(short)])
                pd.testing.assert_frame_equal(baseline.iloc[:len(short)],short)
                results[f"{strategy}_lag{lag}"]={"compared_sessions":len(short),"orders":int(short.order_count.sum()),
                                   "overlay_entries":int(short.overlay_entry.sum()),"status":"PASS"}
        details[name]=results
    return details



def _freeze_trials(config, input_paths, output_dir, tracker):
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest() != FROZEN_CONFIG_CANONICAL_SHA256:
        raise ValueError("PREREGISTRATION_CHANGED")
    for symbol, info in config["input_files"].items():
        if hashlib.sha256(Path(input_paths[symbol]).read_bytes()).hexdigest() != info["sha256"]:
            raise ValueError("INPUT_HASH_MISMATCH")
    source = "sha256-" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    frozen = []
    for symbol, info in config["input_files"].items():
        for cost in config["costs"]:
            for lag in config["execution"]["lag_sessions"]:
                for strategy in config["strategies"]:
                    spec = {"symbol": symbol, "strategy": strategy, "cost": cost["id"], "lag": lag,
                            "input_sha256": info["sha256"], "source": source,
                            "preregistration_sha256": FROZEN_PREREGISTRATION_SHA256}
                    ident = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    frozen.append({**spec, "trial_id": ident})
    if len(frozen) != 64 or len({item["trial_id"] for item in frozen}) != 64:
        raise ValueError("TRIAL_REGISTRY_INCOMPLETE")
    registry = {"schema_version": "qsl.range_rsi2_trial_registry.v1", "trials": frozen,
                "preregistration_sha256": FROZEN_PREREGISTRATION_SHA256,
                "classification": "retrospective_development_only", "promotion_eligible": False,
                "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
    with (output_dir / "trial_registry.json").open("x", encoding="utf-8") as stream:
        tracker["registry_owned"] = True
        json.dump(registry, stream, sort_keys=True, indent=2)
        stream.write("\n")
    store = PerformanceStore(local_root=output_dir / "performance_store")
    for item in frozen:
        cost = next(c for c in config["costs"] if c["id"] == item["cost"])
        trial = ResearchTrialRecord(trial_id=item["trial_id"], domain="us_equity",
            strategy_profile="range_rsi2_" + item["symbol"].lower(), status=ResearchTrialStatus.STARTED,
            candidate_config_id=item["strategy"], actual_params=None, param_set_id=item["strategy"],
            source_revision=source, input_id=item["input_sha256"], window_start=date.fromisoformat(FROZEN_START),
            window_end=date.fromisoformat(FROZEN_END), calendar_id="paired_adjusted_price_sessions",
            periods_per_year=252.0, cost_source=item["cost"], cost_inputs={"commission_bps": float(cost["commission_bps"]),
                "slippage_bps": float(cost["slippage_bps"]), "market_impact_bps": 0.0},
            reason_code="", synthetic=False, run_id=None, param_version=None)
        store.save_research_trial(trial)
        if store.load_research_trial(trial.domain, trial.strategy_profile, trial.trial_id) != trial:
            raise ValueError("TRIAL_REGISTRY_READBACK_FAILED")
    return registry


def _run_study_impl(config, input_paths, output_dir, tracker):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry = _freeze_trials(config, input_paths, output_dir, tracker)
    pairs={symbol:load_pair(symbol, config, input_paths) for symbol in config["input_files"]}
    checks={"actual_input_future_mutation_and_truncation":actual_data_causality_checks(pairs, config)}
    (output_dir/"validation.json").write_text(json.dumps(checks,indent=2)+"\n")
    rows=[]
    period_rows=[]
    annual_rows=[]
    bootstrap_rows=[]
    paths={}
    (output_dir/"daily_paths").mkdir(exist_ok=True)
    for symbol,pair in pairs.items():
        for cost in config["costs"]:
            for lag in config["execution"]["lag_sessions"]:
                for strategy in config["strategies"]:
                    tracker["active"] = (symbol, strategy, cost["id"], lag)
                    frame=simulate(pair,strategy,cost["commission_bps"],cost["slippage_bps"],lag)
                    identity={"symbol":symbol,"strategy":strategy,"cost":cost["id"],"lag":lag}
                    rows.append({**identity,**metrics(frame)})
                    paths[(symbol,strategy,cost["id"],lag)]=frame
                    frame.to_csv(output_dir/"daily_paths"/f"{symbol}_{strategy}_{cost['id']}_lag{lag}.csv",float_format="%.17g")
                    for pstart,pend in config["periods"]:
                        sub=frame.loc[(frame.index>=pstart)&(frame.index<=pend)]
                        period_rows.append({**identity,"period":pstart[:4]+"-"+pend[:4],**metrics(sub)})
                    for year in sorted(set(i[:4] for i in frame.index)):
                        sub=frame.loc[[i.startswith(year) for i in frame.index]]
                        annual_rows.append({**identity,"year":year,**metrics(sub)})
                    print(json.dumps({**identity,"cagr":rows[-1]["cagr"],"maxdd":rows[-1]["max_drawdown"],"entries":rows[-1]["overlay_entries"]}),flush=True)
                    tracker["active"] = None
                for strategy in ["trend_mr_5","trend_mr_10","trend_mr_15"]:
                    for reference in ["trend","trend_range_hold"]:
                        candidate=paths[(symbol,strategy,cost["id"],lag)]["return"]
                        baseline=paths[(symbol,reference,cost["id"],lag)]["return"]
                        bootstrap_rows.append({"symbol":symbol,"strategy":strategy,"reference":reference,
                            "cost":cost["id"],"lag":lag,**block_bootstrap((candidate-baseline).to_numpy())})
    pd.DataFrame(rows).to_csv(output_dir/"summary.csv",index=False,float_format="%.12g")
    pd.DataFrame(period_rows).to_csv(output_dir/"periods.csv",index=False,float_format="%.12g")
    pd.DataFrame(annual_rows).to_csv(output_dir/"annual_returns.csv",index=False,float_format="%.12g")
    pd.DataFrame(bootstrap_rows).to_csv(output_dir/"bootstrap.csv",index=False,float_format="%.12g")
    result={"study_id":config["study_id"],"preregistration_sha256":FROZEN_PREREGISTRATION_SHA256,
            "trial_registry_sha256": hashlib.sha256((output_dir / "trial_registry.json").read_bytes()).hexdigest(),
            "qpk_trial_lifecycle": "started_pending_ledger_linkage",
            "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "input_sha256":{s:i["sha256"] for s,i in config["input_files"].items()},
            "classification":config["classification"],"run_count":len(rows),
            "data":{s:{"first":str(p.dates[0].date()),"last":str(p.dates[-1].date()),"rows":len(p.dates)} for s,p in pairs.items()},
            "environment":{"python":sys.version,"numpy":np.__version__,"pandas":pd.__version__},
            "results":rows,"period_results":period_rows,"annual_results":annual_rows,
            "bootstrap":bootstrap_rows,"validation":checks,
            "no_production_adoption_authorized":True}
    (output_dir/"results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"completed_runs":len(rows),"output":str(output_dir/"results.json")}),flush=True)


def run_study(config, input_paths, output_dir):
    """Run the frozen grid; terminate unlinked trials if any stage fails."""
    tracker = {"active": None, "registry_owned": False}
    try:
        return _run_study_impl(config, input_paths, output_dir, tracker)
    except BaseException as exc:
        if tracker["registry_owned"]:
            from .range_rsi2_failures import record_incomplete_trials
            try:
                record_incomplete_trials(output_dir, tracker["active"], interrupted=not isinstance(exc, Exception))
            except Exception as bookkeeping_error:
                exc.add_note(f"trial bookkeeping failed: {type(bookkeeping_error).__name__}")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the frozen retrospective RSI2 research study.")
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--preregistration-sha256", required=True)
    parser.add_argument("--soxl-input", type=Path, required=True)
    parser.add_argument("--soxl-sha256", required=True)
    parser.add_argument("--tqqq-input", type=Path, required=True)
    parser.add_argument("--tqqq-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    preregistration = args.preregistration.read_bytes()
    if (args.preregistration_sha256 != FROZEN_PREREGISTRATION_SHA256
            or hashlib.sha256(preregistration).hexdigest() != args.preregistration_sha256):
        raise ValueError("PREREGISTRATION_CHANGED")
    config = json.loads(preregistration)
    if (args.soxl_sha256 != config["input_files"]["SOXL"]["sha256"]
            or args.tqqq_sha256 != config["input_files"]["TQQQ"]["sha256"]):
        raise ValueError("INPUT_HASH_MISMATCH")
    run_study(config, {"SOXL": args.soxl_input, "TQQQ": args.tqqq_input}, args.output_dir)


if __name__ == "__main__":
    main()
