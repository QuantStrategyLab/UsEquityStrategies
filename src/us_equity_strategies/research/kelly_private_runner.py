"""Private-environment runner for the RS-04 no_plugin_core evaluation (research-only).

Run only where the approved private archive lives; never in public CI. It reads
the private Alpaca SIP raw-bar + corporate-action archive, verifies the archive
manifest SHA-256 against the pre-registration, builds the no-plugin core path,
and calls the evaluator with a ``RealDataAuthorization``. Raw data and daily
outputs are written only to a private output directory (mode 0700/0600) and
are never printed; stdout gets aggregate metrics only.

Archive layout (as used by the R7 study): ``manifest.json`` with ``inputs`` items
``{symbol, kind: bars|actions, pages: [{sha256, bytes}], complete_pagination, count}``
and files ``<kind>/<SYMBOL>/page-NNN.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from us_equity_strategies.research import fractional_kelly_preregistration as prereg_mod
from us_equity_strategies.research import fractional_kelly_walk_forward as evaluator
from us_equity_strategies.research.kelly_core_return_generator import (
    CoreGeneratorError,
    CorporateActions,
    RawBar,
    generate_soxl_core_path,
    generate_tqqq_core_path,
    total_return_series,
)

SYMBOLS = {
    "SOXL": {"signal": ("SOXL", "SOXX"), "assets": ("SOXL", "SOXX", "BOXX"), "benchmark": "SOXX"},
    "TQQQ": {"signal": ("QQQ",), "assets": ("TQQQ", "QQQM", "BOXX"), "benchmark": "QQQ"},
}
HANDLED_ACTIONS = ("forward_splits", "cash_dividends")


class PrivateRunError(ValueError):
    """Fail-closed private run error; never includes data values."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pages(root: Path, manifest: Mapping[str, Any], symbol: str, kind: str) -> list[dict]:
    items = [x for x in manifest.get("inputs", []) if x.get("symbol") == symbol and x.get("kind") == kind]
    if len(items) != 1:
        raise PrivateRunError(f"ARCHIVE_INPUT_MISSING:{symbol}:{kind}")
    item = items[0]
    if item.get("complete_pagination") is not True or not item.get("pages"):
        raise PrivateRunError(f"ARCHIVE_PAGINATION_INCOMPLETE:{symbol}:{kind}")
    out = []
    for n, page in enumerate(item["pages"], start=1):
        path = root / kind / symbol / f"page-{n:03d}.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size != page["bytes"] or _sha(path) != page["sha256"]:
            raise PrivateRunError(f"ARCHIVE_PAGE_MISMATCH:{symbol}:{kind}:{n}")
        out.append(json.loads(path.read_bytes()))
    if out[-1].get("next_page_token") is not None:
        raise PrivateRunError(f"ARCHIVE_PAGINATION_INCOMPLETE:{symbol}:{kind}")
    return out


def load_archive(root: Path, symbols: Sequence[str], expected_manifest_sha256: str
                 ) -> tuple[dict[str, list[RawBar]], dict[str, CorporateActions]]:
    if root.is_symlink() or not (root / "manifest.json").is_file():
        raise PrivateRunError("ARCHIVE_NOT_FOUND")
    if _sha(root / "manifest.json") != expected_manifest_sha256:
        raise PrivateRunError("ARCHIVE_MANIFEST_SHA256_MISMATCH")
    manifest = json.loads((root / "manifest.json").read_bytes())
    bars: dict[str, list[RawBar]] = {}
    actions: dict[str, CorporateActions] = {}
    for symbol in symbols:
        rows = []
        for page in _pages(root, manifest, symbol, "bars"):
            if page.get("symbol") != symbol:
                raise PrivateRunError(f"ARCHIVE_SYMBOL_MISMATCH:{symbol}")
            rows.extend(page["bars"])
        parsed = [RawBar(date.fromisoformat(str(b["t"])[:10]), float(b["o"]), float(b["c"])) for b in rows]
        days = [b.session for b in parsed]
        if days != sorted(set(days)):
            raise PrivateRunError(f"ARCHIVE_DATES_INVALID:{symbol}")
        bars[symbol] = parsed
        splits, dividends = [], []
        for page in _pages(root, manifest, symbol, "actions"):
            ca = page.get("corporate_actions") or {}
            unhandled = sorted(k for k, v in ca.items() if v and k not in HANDLED_ACTIONS)
            if unhandled:
                raise PrivateRunError(f"UNHANDLED_CORPORATE_ACTION:{symbol}:{','.join(unhandled)}")
            for e in ca.get("forward_splits", []) or []:
                splits.append((date.fromisoformat(e["ex_date"]), float(e["new_rate"]) / float(e["old_rate"])))
            for e in ca.get("cash_dividends", []) or []:
                if e.get("symbol", symbol) != symbol:
                    raise PrivateRunError(f"DIVIDEND_SYMBOL_MISMATCH:{symbol}")
                dividends.append((date.fromisoformat(e["ex_date"]), float(e["rate"])))
        actions[symbol] = CorporateActions(tuple(splits), tuple(dividends))
    return bars, actions


def _sessions(bars: Mapping[str, Sequence[RawBar]], required: Sequence[str], end: date) -> list[date]:
    sets = [{b.session for b in bars[s]} for s in required]
    start = max(min(x) for x in sets)
    common = sorted(d for d in set.intersection(*sets) if start <= d <= end)
    union = sorted(d for d in set.union(*sets) if start <= d <= end)
    if common != union:
        raise PrivateRunError("SIGNAL_SESSIONS_NOT_ALIGNED")
    return common


def _write_private(path: Path, payload: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=1, allow_nan=False, default=str)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def run_private(raw_root: Path, out_dir: Path, preregistration_id: str, *, source_revision: str,
                created_at: str, bootstrap_seed: int = 20261011) -> dict[str, Any]:
    p = prereg_mod.PREREGISTRATIONS.get(preregistration_id)
    if p is None or not p.data_confirmed or prereg_mod.VARIANT_NO_PLUGIN not in p.data_confirmed_variants:
        raise PrivateRunError("PREREGISTRATION_NOT_CONFIRMED_FOR_NO_PLUGIN_CORE")
    spec = SYMBOLS[p.strategy_key]
    symbols = sorted({*spec["signal"], *spec["assets"], spec["benchmark"]})
    bars, actions = load_archive(raw_root, symbols, p.data_manifest_sha256)
    sessions = _sessions(bars, (*spec["signal"], spec["assets"][0], spec["benchmark"]), p.window_end)
    if p.window_start not in sessions or p.window_end not in sessions:
        raise PrivateRunError("WINDOW_NOT_COVERED_BY_ARCHIVE")
    generator = generate_soxl_core_path if p.strategy_key == "SOXL" else generate_tqqq_core_path
    try:
        path = generator(bars, actions, sessions, trade_start=p.window_start).window(p.window_start, p.window_end)
    except CoreGeneratorError as exc:
        raise PrivateRunError(f"CORE_PATH_FAILED:{exc}") from exc
    window_sessions = list(path.sessions)
    bench = total_return_series(bars[spec["benchmark"]], actions[spec["benchmark"]], sessions)
    lo = sessions.index(p.window_start)
    bench_window = bench[lo: lo + len(window_sessions)]
    boxx_sessions = [d for d in sessions if d >= min(b.session for b in bars["BOXX"])]
    if boxx_sessions.index(p.window_start) < 1:
        raise PrivateRunError("BOXX_HISTORY_TOO_SHORT_FOR_CASH_LEG")
    boxx = total_return_series(bars["BOXX"], actions["BOXX"], boxx_sessions)
    bl = boxx_sessions.index(p.window_start)
    cash_window = boxx[bl: bl + len(window_sessions)]
    manifest = prereg_mod.DataManifest(p.data_manifest_sha256, p.data_source, p.data_license, p.calendar,
                                       p.window_start, p.window_end, len(window_sessions))
    context = {
        "report_id": f"{preregistration_id}-no_plugin_core-{created_at[:10]}",
        "candidate_id": f"{p.live_profile}__no_plugin_core", "source_revision": source_revision,
        "created_at": created_at, "manifest_sha256": manifest.sha256, "source": manifest.source,
        "license": manifest.license, "signal_cutoff": p.signal_cutoff, "execution": p.execution,
        "benchmark_name": spec["benchmark"],
        "benchmark_rationale": "underlying 1x index ETF of the strategy (pre-registered)",
        "cost_model_id": "single_side_bps_on_traded_notional", "settlement_rule": "not_modeled_fractional_weights",
        "limitations": ("no_plugin_core: all plugins/overlays off; differs from live",
                        "signals on forward total-return index built from raw bars + corporate actions",
                        "fractional weights, no whole shares, no settlement; cash inside strategy earns 0",
                        "retrospective vendor data, not strict point-in-time; development only"),
    }
    result = evaluator.evaluate_fractional_kelly_walk_forward(
        strategy_returns=list(zip(window_sessions, path.gross_returns, strict=True)),
        benchmark_returns=list(zip(window_sessions, bench_window, strict=True)),
        cash_returns=list(zip(window_sessions, cash_window, strict=True)),
        strategy_turnover=list(path.turnover), plan=p.plan, cost_bps_per_side=p.base_cost_bps_per_side,
        fractions=p.fractions, multipliers=p.multipliers, synthetic=False,
        real_data=prereg_mod.RealDataAuthorization(p.strategy_key, preregistration_id, manifest,
                                                   prereg_mod.VARIANT_NO_PLUGIN),
        report_context=context, bootstrap_seed=bootstrap_seed)
    _write_private(out_dir / f"{preregistration_id}.result.json", result)
    _write_private(out_dir / f"{preregistration_id}.core_path.json", {
        "sessions": [d.isoformat() for d in window_sessions], "gross_returns": path.gross_returns,
        "turnover": path.turnover, "first_signal": str(path.first_signal)})
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
