"""Private-environment runner for RS-06 portfolio fractional Kelly (research-only).

Run only on the machine holding the approved private archive; never in public CI.
Builds the SOXL and TQQQ ``no_plugin_core`` paths exactly as the RS-04 runner
does, aligns them with BOXX cash and the SOXX / QQQ benchmarks, and calls the
portfolio evaluator with a ``PortfolioRealDataAuthorization``. Outputs go only
to a private directory (0700/0600); stdout gets status only.
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from us_equity_strategies.research import fractional_kelly_preregistration as single
from us_equity_strategies.research import kelly_private_runner as rs04_runner
from us_equity_strategies.research import portfolio_kelly_preregistration as prereg_mod
from us_equity_strategies.research import portfolio_kelly_walk_forward as evaluator
from us_equity_strategies.research.kelly_core_return_generator import (
    CoreGeneratorError,
    generate_soxl_core_path,
    generate_tqqq_core_path,
    total_return_series,
)

PrivateRunError = rs04_runner.PrivateRunError
GENERATORS = {"SOXL": generate_soxl_core_path, "TQQQ": generate_tqqq_core_path}


def run_private(raw_root: Path, out_dir: Path, preregistration_id: str, *, source_revision: str,
                created_at: str, bootstrap_seed: int = 20261011) -> dict[str, Any]:
    p = prereg_mod.PREREGISTRATIONS.get(preregistration_id)
    if p is None:
        raise PrivateRunError("PREREGISTRATION_ID_INVALID")
    assets = tuple(k for k, _ in p.components)
    specs = {k: rs04_runner.SYMBOLS[k] for k in assets}
    symbols = sorted({s for spec in specs.values() for s in (*spec["signal"], *spec["assets"], spec["benchmark"])})
    bars, actions = rs04_runner.load_archive(raw_root, symbols, p.data_manifest_sha256)
    required = sorted({s for spec in specs.values() for s in (*spec["signal"], spec["assets"][0], spec["benchmark"])})
    sessions = rs04_runner._sessions(bars, required, p.window_end)
    if p.window_start not in sessions or p.window_end not in sessions:
        raise PrivateRunError("WINDOW_NOT_COVERED_BY_ARCHIVE")
    paths = {}
    components = {k: single.PREREGISTRATIONS[cid] for k, cid in p.components}
    for key in assets:
        try:
            paths[key] = GENERATORS[key](bars, actions, sessions, trade_start=p.window_start,
                                         **rs04_runner.generator_kwargs(components[key])).window(
                p.window_start, p.window_end)
        except CoreGeneratorError as exc:
            raise PrivateRunError(f"CORE_PATH_FAILED:{key}:{exc}") from exc
    window = list(paths[assets[0]].sessions)
    if any(list(paths[k].sessions) != window for k in assets):
        raise PrivateRunError("CORE_PATH_SESSIONS_NOT_ALIGNED")
    lo = sessions.index(p.window_start)
    benches = []
    for key in assets:
        sym = specs[key]["benchmark"]
        series = total_return_series(bars[sym], actions[sym], sessions)[lo: lo + len(window)]
        benches.append((sym, list(zip(window, series, strict=True))))
    boxx_sessions = [d for d in sessions if d >= min(b.session for b in bars["BOXX"])]
    if boxx_sessions.index(p.window_start) < 1:
        raise PrivateRunError("BOXX_HISTORY_TOO_SHORT_FOR_CASH_LEG")
    boxx = total_return_series(bars["BOXX"], actions["BOXX"], boxx_sessions)
    bl = boxx_sessions.index(p.window_start)
    cash = boxx[bl: bl + len(window)]
    manifest = single.DataManifest(p.data_manifest_sha256, p.data_source, p.data_license, p.calendar,
                                   p.window_start, p.window_end, len(window))
    context = {
        "report_id": f"{preregistration_id}-{created_at[:10]}",
        "candidate_id": "portfolio_kelly__" + "+".join(f"{k}_no_plugin_core" for k in assets),
        "source_revision": source_revision, "created_at": created_at, "manifest_sha256": manifest.sha256,
        "source": manifest.source, "license": manifest.license, "signal_cutoff": p.signal_cutoff,
        "execution": p.execution, "benchmark_name": "+".join(s for s, _ in benches) + " (each; plus 50/50)",
        "benchmark_rationale": "underlying 1x index ETFs of the component strategies (pre-registered)",
        "cost_model_id": "single_side_bps_on_traded_notional", "settlement_rule": "not_modeled_fractional_weights",
        "limitations": ("no_plugin_core components: all plugins/overlays off; differs from live",
                        "component generators: " + ", ".join(
                            f"{k} {c.generator_version}/{c.signal_price_basis}/{c.indicator_source}"
                            for k, c in components.items()),
                        "retrospective vendor data, not strict point-in-time; development only",
                        "fractional weights, no whole shares, no settlement"),
    }
    result = evaluator.evaluate_portfolio_kelly_walk_forward(
        asset_returns=[(k, list(zip(window, paths[k].gross_returns, strict=True))) for k in assets],
        asset_turnover={k: list(paths[k].turnover) for k in assets},
        cash_returns=list(zip(window, cash, strict=True)), benchmark_returns=benches,
        plan=p.plan, cost_bps_per_side=p.base_cost_bps_per_side, fractions=p.fractions, methods=p.methods,
        multipliers=p.multipliers, synthetic=False,
        real_data=prereg_mod.PortfolioRealDataAuthorization(preregistration_id, manifest),
        report_context=context, bootstrap_seed=bootstrap_seed)
    result["generator"] = {k: rs04_runner.generator_disclosure(components[k]) for k in assets}
    rs04_runner._write_private(out_dir / f"{preregistration_id}.result.json", result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--preregistration-id", required=True, choices=sorted(prereg_mod.PREREGISTRATIONS))
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--created-at", required=True)
    args = parser.parse_args(argv)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise SystemExit("PRIVATE_RUN_FORBIDDEN_IN_CI")
    result = run_private(args.raw_root, args.out_dir, args.preregistration_id,
                         source_revision=args.source_revision, created_at=args.created_at)
    print(json.dumps({"status": result["status"], "reason_code": result.get("reason_code"),
                      "test_window": result.get("test_window"), "out_dir": str(args.out_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
