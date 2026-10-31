"""Frozen, non-executable 144-path prelisting reference study."""
from __future__ import annotations

from bisect import bisect_left
from datetime import date
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path

from .provider_history import read_provider_history
from .range_rsi2_study import make_pair, metrics, simulate


def run_study(input_dir: Path, provider_manifest: Path, rates_path: Path, rates_sha256: str, output_dir: Path):
    """Create a new namespace, freeze the grid, then record every trial."""
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest_bytes = provider_manifest.read_bytes()
    rates_bytes = rates_path.read_bytes()
    if hashlib.sha256(rates_bytes).hexdigest() != rates_sha256:
        raise ValueError("RATE_BYTES_MISMATCH")
    sources = {r["symbol"]: r for r in json.loads(manifest_bytes)["records"]}
    models = ("trend", "trend_mr_10", "trend_range_hold")
    grid = list(itertools.product(("QQQ", "SOXX"), (0., .005, .01), (0., .005),
                                  ((2, 5), (5, 10)), (1, 2), models))
    registration = {"scope": "synthetic_reference_not_etf", "executable": False, "promotion_eligible": False,
                    "provider_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "rates_sha256": rates_sha256, "grid": grid,
                    "synthetic_price_rule": "3x overnight open; 3x daily close less calendar funding drag",
                    "listing": {"QQQ": "2010-02-09", "SOXX": "2010-03-11"}, "count": len(grid)}
    (output_dir / "registration.json").write_text(json.dumps(registration, indent=2) + "\n")
    rates = [(date.fromisoformat(r["observation_date"]), float(r["DFF"]) / 100)
             for r in csv.DictReader(rates_bytes.decode().splitlines()) if r["DFF"] not in ("", ".")]
    rate_dates = [d for d, _ in rates]
    if rate_dates != sorted(set(rate_dates)):
        raise ValueError("RATE_DATES_NOT_ORDERED")
    history = {}
    for sym in ("QQQ", "SOXX"):
        r = sources[sym]
        history[sym] = read_provider_history(input_dir / Path(r["path"]).name,
                                           expected_sha256=r["sha256"], expected_symbol=sym)["rows"]
    outcomes = []
    pair_cache = {}
    (output_dir / "daily_paths").mkdir()
    for sym, spread, tracking, cost, lag, model in grid:
        trial = {"symbol": sym, "spread": spread, "tracking_drag": tracking,
                 "commission_bps": cost[0], "slippage_bps": cost[1], "lag": lag, "model": model}
        trial_id = hashlib.sha256(json.dumps(trial, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        try:
            key = sym, spread, tracking
            if key not in pair_cache:
                listing = date.fromisoformat(registration["listing"][sym])
                rows = [row for row in history[sym] if row["session"] < listing]
                opens, closes, days = [], [], []
                nav = 1.0
                for i, row in enumerate(rows):
                    if i == 0:
                        opening = closing = nav
                    else:
                        previous = rows[i - 1]
                        idx = bisect_left(rate_dates, row["session"]) - 1
                        if idx < 0 or (row["session"] - rate_dates[idx]).days > 7:
                            raise ValueError("LAGGED_RATE_UNAVAILABLE")
                        opening = nav * (1 + 3 * (row["adjusted_open"] / previous["adjusted_close"] - 1))
                        drag = (.01 + 2 * (rates[idx][1] + spread) + tracking) * (row["session"] - previous["session"]).days / 365
                        closing = nav * (1 + 3 * (row["adjusted_close"] / previous["adjusted_close"] - 1) - drag)
                    if not math.isfinite(opening) or not math.isfinite(closing) or min(opening, closing) <= 0:
                        raise ValueError("NONPOSITIVE_SYNTHETIC_MARK")
                    opens.append([opening, row["adjusted_open"]]); closes.append([closing, row["adjusted_close"]])
                    days.append(row["session"]); nav = closing
                pair_cache[key] = make_pair("synthetic_3x", sym, days, opens, closes,
                                           (.45, .45) if sym == "QQQ" else (.7, .2))
            pair = pair_cache[key]
            frame = simulate(pair, model, *cost, lag, start=pair.dates[201], end=pair.dates[-1])
            path = output_dir / "daily_paths" / f"{trial_id}.csv"
            frame.to_csv(path, float_format="%.17g")
            outcomes.append({**trial, "trial_id": trial_id, "status": "SUCCEEDED",
                             "path": f"daily_paths/{trial_id}.csv", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "metrics": metrics(frame)})
        except Exception as exc:
            outcomes.append({**trial, "trial_id": trial_id, "status": "FAILED", "reason": str(exc)})
    (output_dir / "results.json").write_text(json.dumps({"registration": registration, "trials": outcomes},
                                                        indent=2, allow_nan=False) + "\n")
    return outcomes
