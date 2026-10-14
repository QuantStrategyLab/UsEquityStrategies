#!/usr/bin/env python3
"""One-shot cloud-only cap sensitivity; aggregate output, no live authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

CAPS = (0.679, 0.55, 0.45, 0.35)
ARCHIVE_SHA = "b5a3dd40cb0d4260fb15492108bde7ab544ac0d191807943637b8216a6808482"
PREFIX = re.compile(r"gs://qsl-research-evidence-831478360303/research/v2/input/[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
START, END = date(2023, 3, 28), date(2026, 8, 25)
QPK_SHA = "b6b51d5c32594c47cc1b3ebc0c1ed226e9db887f"


class ResearchStop(ValueError):
    """A fixed reason code; no provider exception or data is exposed."""


def capped_target(target, cap):
    """Clip the final desired weight once; leave other assets and residual cash alone."""
    if cap not in CAPS:
        raise ResearchStop("CAP_NOT_REGISTERED")
    out = dict(target)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           or v < 0 for v in out.values()):
        raise ResearchStop("TARGET_INVALID")
    if math.fsum(out.values()) > 0.97 + 1e-9 or out.get("SOXX", 0) > 0.873 + 1e-9:
        raise ResearchStop("OTHER_RUNTIME_CAP_EXCEEDED")
    out["SOXL"] = min(out.get("SOXL", 0.0), cap)
    return out


def capped_replay(original_replay, cap):
    """Preserve the existing signal/fill clocks and drift engine, with one final clip."""
    def replay(strategy_key, days, asset_returns, assets, decide, trade_start, available_from=None):
        def decide_capped(i, drifted):
            desired = decide(i, drifted)
            return None if desired is None else capped_target(desired, cap)
        return original_replay(strategy_key, days, asset_returns, assets,
                               decide_capped, trade_start, available_from)
    return replay


def metrics(sessions, returns):
    """Initial NAV is part of drawdown; recovery is in observed trading sessions."""
    if len(sessions) != len(returns) or len(returns) < 20:
        raise ResearchStop("METRIC_WINDOW_TOO_SHORT")
    if any(not math.isfinite(r) or r <= -1 for r in returns):
        raise ResearchStop("NET_RETURN_INVALID")
    nav = [1.0]
    for r in returns:
        nav.append(nav[-1] * (1 + r))
    peak, peak_i, worst, worst_peak_i, trough_i = 1.0, 0, 0.0, 0, 0
    for i, value in enumerate(nav):
        if value >= peak:
            peak, peak_i = value, i
        loss = 1 - value / peak
        if loss > worst:
            worst, worst_peak_i, trough_i = loss, peak_i, i
    recovered = next((i for i in range(trough_i, len(nav))
                      if nav[i] >= nav[worst_peak_i]), None)
    worst20 = 0.0
    for end in range(20, len(nav)):
        local_peak = nav[end - 20]
        for value in nav[end - 19:end + 1]:
            local_peak = max(local_peak, value)
            worst20 = max(worst20, 1 - value / local_peak)
    maximum = max(nav)
    # Include the first observed return day in calendar-time annualization.
    years = ((sessions[-1] - sessions[0]).days + 1) / 365.25
    return {"observations": len(returns), "cagr": nav[-1] ** (1 / years) - 1,
            "max_drawdown": worst, "worst_20_session_drawdown": worst20,
            "giveback_from_peak_nav": 1 - nav[-1] / maximum,
            "profit_giveback_fraction": ((maximum - nav[-1]) / (maximum - 1)
                                         if maximum > 1 else None),
            "max_drawdown_peak_to_recovery_sessions": (
                recovered - worst_peak_i if recovered is not None else None),
            "max_drawdown_unrecovered_sessions": (
                len(returns) - worst_peak_i if recovered is None else 0)}


def validate_cloud(prefix):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ResearchStop("REAL_DATA_CLOUD_ONLY")
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ResearchStop("REPEAT_RUN_REJECTED")
    if not prefix:
        raise ResearchStop("RS04_CLOUD_ARCHIVE_LOCATION_REQUIRED")
    if not PREFIX.fullmatch(prefix):
        raise ResearchStop("ARCHIVE_PREFIX_REJECTED")


def stage_archive(prefix, root):
    """Only fixed manifest-referenced pages. No listing, source acquisition or cloud writes."""
    def fetch(relative, maximum_bytes):
        destination = root / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = subprocess.run(["gcloud", "storage", "cp", "--quiet",
                                 prefix + "/" + relative, str(destination)],
                                capture_output=True, timeout=60, check=False)
        if result.returncode or not destination.is_file():
            raise ResearchStop("ARCHIVE_OBJECT_READ_FAILED")
        destination.chmod(0o600)
        if destination.stat().st_size > maximum_bytes:
            raise ResearchStop("ARCHIVE_SIZE_LIMIT_EXCEEDED")
        return destination

    manifest_path = fetch("manifest.json", 1024 * 1024)
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != ARCHIVE_SHA:
        raise ResearchStop("ARCHIVE_MANIFEST_MISMATCH")
    manifest = json.loads(manifest_path.read_bytes())
    total_bytes = 0
    for symbol in ("SOXL", "SOXX", "BOXX"):
        for kind in ("bars", "actions"):
            items = [x for x in manifest.get("inputs", [])
                     if x.get("symbol") == symbol and x.get("kind") == kind]
            if len(items) != 1 or items[0].get("complete_pagination") is not True:
                raise ResearchStop("ARCHIVE_INPUT_INCOMPLETE")
            pages = items[0].get("pages", [])
            if not 1 <= len(pages) <= 32:
                raise ResearchStop("ARCHIVE_PAGE_LIMIT_EXCEEDED")
            for n, page in enumerate(pages, 1):
                size = page.get("bytes")
                if type(size) is not int or not 0 < size <= 8 * 1024 * 1024:
                    raise ResearchStop("ARCHIVE_SIZE_LIMIT_EXCEEDED")
                total_bytes += size
                if total_bytes > 32 * 1024 * 1024:
                    raise ResearchStop("ARCHIVE_SIZE_LIMIT_EXCEEDED")
                path = fetch(f"{kind}/{symbol}/page-{n:03d}.json", size)
                if path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != page["sha256"]:
                    raise ResearchStop("ARCHIVE_PAGE_MISMATCH")


def research(prefix):
    from importlib.metadata import distribution

    from quant_platform_kit.research_stats.execution_models import CostModel
    from us_equity_strategies.research import kelly_core_return_generator as core
    from us_equity_strategies.research.kelly_private_runner import _sessions, load_archive

    provenance = json.loads(distribution("quant-platform-kit").read_text("direct_url.json"))
    if provenance.get("vcs_info", {}).get("commit_id") != QPK_SHA:
        raise ResearchStop("RESEARCH_DEPENDENCY_MISMATCH")
    rows = []
    with tempfile.TemporaryDirectory(prefix="rs04-cap-", dir=os.environ["RUNNER_TEMP"]) as tmp:
        root = Path(tmp)
        root.chmod(0o700)
        stage_archive(prefix, root)
        bars, actions = load_archive(root, ("SOXL", "SOXX", "BOXX"), ARCHIVE_SHA)
        sessions = _sessions(bars, ("SOXL", "SOXX"), END)
        original_replay = core._replay
        for cap in CAPS:
            # Process-local research hook; runtime code and default generator remain unchanged.
            with patch.object(core, "_replay", capped_replay(original_replay, cap)):
                path = core.generate_soxl_core_path(
                    bars, actions, sessions, trade_start=START,
                    signal_price_basis="split_adjusted", indicator_source="qpk_live_builder",
                    indicator_history_bars=504).window(START, END)
            if len(path.sessions) != 856:
                raise ResearchStop("REGISTERED_SESSION_COUNT_MISMATCH")
            lo = next(i for i, d in enumerate(path.sessions) if d > END.replace(year=END.year - 1))
            for cost_bps in (5.0, 10.0, 15.0):
                net = path.measure_close_fill_costs(CostModel(commission_bps=cost_bps))
                rows.append({"cap": cap, "cost_bps_per_side": cost_bps,
                             "full_registered_history": metrics(path.sessions, net),
                             "archive_last_12_months": metrics(path.sessions[lo:], net[lo:])})
    return {"status": "COMPUTED_DEVELOPMENT_ONLY", "no_order": True, "live_ready": False,
            "promotion_eligible": False, "budget_effect": "NONE", "caps": CAPS,
            "archive_manifest_sha256": ARCHIVE_SHA, "source_revision": os.environ["GITHUB_SHA"],
            "qpk_revision": QPK_SHA, "window_start": START.isoformat(), "window_end": END.isoformat(),
            "recent_start_exclusive": END.replace(year=END.year - 1).isoformat(),
            "archive_age_days": (date.today() - END).days,
            "current_last_12_months_covered": False, "etf_inception_history_covered": False,
            "b1_b2": "NOT_LIVE_VALIDATED_NOT_ACTIVATED; final_SOXL=min(cap, desired_SOXL_after_B1_B2_once)",
            "limitations": ["no_plugin_core; not live parity; next_close differs from live intraday",
                            "at-close cost NAV overlay; no_weight_feedback; fractional holdings",
                            "SOXX/BOXX unchanged; removed SOXL stays zero-return cash",
                            "5/10/15bps are report scenarios, not attested live fees",
                            "full means registered archive, not ETF inception; recent ends at archive cutoff",
                            "live risk gate rejects above-cap targets; clipping needs separate implementation"],
            "results": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    try:
        prefix = os.environ.get("RS04_ARCHIVE_GCS_PREFIX", "").rstrip("/")
        validate_cloud(prefix)
        result = {"status": "PREFLIGHT_OK", "no_order": True} if args.preflight else research(prefix)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except ResearchStop as exc:
        reason = str(exc)
    except Exception:
        reason = "RESEARCH_FAILED_NO_RETRY"
    print(json.dumps({"status": "PARKED", "reason_code": reason, "no_order": True,
                      "metrics": None, "live_ready": False, "budget_effect": "NONE"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
