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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import date, datetime, timezone
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


def generator_kwargs(p: prereg_mod.KellyPreregistration) -> dict[str, Any]:
    """Generator options frozen in the pre-registration (core_v1 defaults reproduce RS-04e)."""
    if p.generator_version == "core_v1":
        return {}
    if p.generator_version != "core_v2_parity":
        raise PrivateRunError("GENERATOR_VERSION_UNKNOWN")
    out: dict[str, Any] = {"signal_price_basis": p.signal_price_basis}
    if p.strategy_key == "SOXL":
        out["indicator_source"] = p.indicator_source
        out["indicator_history_bars"] = p.indicator_history_bars
    return out


def generator_disclosure(p: prereg_mod.KellyPreregistration) -> dict[str, Any]:
    return {"generator_version": p.generator_version, "signal_price_basis": p.signal_price_basis,
            "indicator_source": p.indicator_source, "indicator_history_bars": p.indicator_history_bars,
            "trial_role": p.trial_role, "supersedes": p.supersedes}


def _evaluate_private(raw_root: Path, out_dir: Path, preregistration_id: str, *, source_revision: str,
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
        path = generator(bars, actions, sessions, trade_start=p.window_start, **generator_kwargs(p)).window(
            p.window_start, p.window_end)
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
                        f"generator {p.generator_version}: signals on {p.signal_price_basis} prices; "
                        f"SOXL indicators from {p.indicator_source}",
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
    result["generator"] = generator_disclosure(p)
    _write_private(out_dir / f"{preregistration_id}.result.json", result)
    _write_private(out_dir / f"{preregistration_id}.core_path.json", {
        "sessions": [d.isoformat() for d in window_sessions], "gross_returns": path.gross_returns,
        "turnover": path.turnover, "first_signal": str(path.first_signal)})
    return result



def _call_bytes(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       allow_nan=False, default=str) + "\n").encode()


def _write_call_receipt(path: Path, payload: dict) -> None:
    """Create once. A partial write is uncertain evidence, never safe to overwrite."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(_call_bytes(payload))
        handle.flush()
        os.fsync(handle.fileno())


def _read_call_receipt(path: Path) -> dict | None:
    if path.is_symlink():
        raise PrivateRunError("CALL_RECEIPT_INVALID")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _run_private_call(out_dir: Path, identity: dict, operation: Callable[[], dict],
                      output_names: tuple[str, ...], *, attempt_id: str | None) -> dict:
    """Local single-caller invocation evidence, not a parameter/fold trial journal.

    Existing attempts are never resumed. A new attempt needs a new identity and
    unused output files. A storage failure may leave STARTED or partial bytes;
    neither permits a retry under the same identity or an inferred terminal.
    """
    if attempt_id is not None and (type(attempt_id) is not str or not attempt_id
            or len(attempt_id) > 300 or any(c.isspace() or ord(c) < 32 for c in attempt_id)):
        raise PrivateRunError("CALL_ATTEMPT_ID_INVALID")
    identity = json.loads(_call_bytes({**identity, "attempt_id": attempt_id}))
    # Default invocation identity stays stable when code/config changes: those
    # changes must conflict with the old attempt rather than silently retry it.
    attempt = {k: identity[k] for k in ("caller", "preregistration_id", "created_at_declared",
                                      "input_location_sha256")} if attempt_id is None else attempt_id
    key = hashlib.sha256(_call_bytes(attempt)).hexdigest()
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        raise PrivateRunError("CALL_OUTPUT_ROOT_INVALID")
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    start_path = out_dir / f".call-{key}.started.json"
    terminal_path = out_dir / f".call-{key}.terminal.json"
    if start_path.exists() or start_path.is_symlink() or terminal_path.exists() or terminal_path.is_symlink():
        previous = _read_call_receipt(start_path)
        if previous is None:
            raise PrivateRunError("CALL_RECEIPT_UNVERIFIED")
        if previous.get("identity") != identity:
            raise PrivateRunError("CALL_ATTEMPT_CONFLICT")
        raise PrivateRunError("CALL_ATTEMPT_ALREADY_RECORDED")
    started = {"schema": "qsl.research.private_call.v1", "scope": "private_research_invocation_only",
               "parameter_fold_trial_count": None, "data_qualification": "not_established_by_receipt",
               "identity": identity, "status": "started",
               "started_at": datetime.now(timezone.utc).isoformat()}
    # A write that raises might have committed; no compensating terminal is safe.
    _write_call_receipt(start_path, started)
    if _read_call_receipt(start_path) != started:
        raise PrivateRunError("CALL_START_READBACK_UNVERIFIED")

    def finish(status: str, reason: str, outputs: dict, domain_status: str | None = None) -> None:
        terminal = {**started, "status": status, "reason_code": reason,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "output_sha256": outputs, "domain_result_status": domain_status}
        _write_call_receipt(terminal_path, terminal)
        if _read_call_receipt(terminal_path) != terminal:
            raise PrivateRunError("CALL_TERMINAL_READBACK_UNVERIFIED")

    try:
        if any((out_dir / name).exists() or (out_dir / name).is_symlink() for name in output_names):
            raise PrivateRunError("CALL_OUTPUT_ALREADY_EXISTS")
        result = operation()
        outputs = {}
        for name in output_names:
            path = out_dir / name
            if path.is_symlink() or not path.is_file():
                raise OSError("CALL_OUTPUT_UNVERIFIED")
            outputs[name] = _sha(path)
        domain_status = result.get("status")
        if domain_status not in {"COMPUTED", "UNCOMPUTABLE"}:
            raise RuntimeError("CALL_RESULT_STATUS_UNRECOGNIZED")
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        status = ("aborted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else
                  "rejected" if isinstance(exc, ValueError) else "failed")
        try:
            finish(status, "call_" + status, {})
        except BaseException:
            try:
                exc.add_note("call_terminal_write_unverified")
            except BaseException:
                pass
        raise
    # Do not turn an uncertain successful-terminal write into a failure terminal.
    finish("succeeded" if domain_status == "COMPUTED" else "rejected",
           "" if domain_status == "COMPUTED" else "domain_result_uncomputable", outputs, domain_status)
    return result


def run_private(raw_root: Path, out_dir: Path, preregistration_id: str, *, source_revision: str,
                created_at: str, bootstrap_seed: int = 20261011,
                attempt_id: str | None = None) -> dict[str, Any]:
    """Record only this future invocation; economic outputs retain their legacy shape."""
    p = prereg_mod.PREREGISTRATIONS.get(preregistration_id)
    identity = {"caller": "kelly_private_runner", "preregistration_id": preregistration_id,
                "source_revision_declared": source_revision, "created_at_declared": created_at,
                "bootstrap_seed": bootstrap_seed, "preregistration_declared": asdict(p) if p else None,
                "input_location_sha256": hashlib.sha256(str(raw_root.absolute()).encode()).hexdigest(),
                "caller_file_sha256": _sha(Path(__file__))}
    names = (f"{preregistration_id}.result.json", f"{preregistration_id}.core_path.json") if p else ()
    return _run_private_call(out_dir, identity, lambda: _evaluate_private(
        raw_root, out_dir, preregistration_id, source_revision=source_revision,
        created_at=created_at, bootstrap_seed=bootstrap_seed), names, attempt_id=attempt_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--preregistration-id", required=True, choices=sorted(prereg_mod.PREREGISTRATIONS))
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--attempt-id")
    args = parser.parse_args(argv)
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise SystemExit("PRIVATE_RUN_FORBIDDEN_IN_CI")
    result = run_private(args.raw_root, args.out_dir, args.preregistration_id,
                         source_revision=args.source_revision, created_at=args.created_at, attempt_id=args.attempt_id)
    print(json.dumps({"status": result["status"], "reason_code": result.get("reason_code"),
                      "test_window": result.get("test_window"), "out_dir": str(args.out_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
