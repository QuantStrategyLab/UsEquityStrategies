"""Private Linux-only inspection of retained reports; no broker or cloud I/O."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

from us_equity_strategies.research.auto_portfolio_account import assess_ibkr_readonly_account_input, digest


def inspect_report(report_bytes, config_bytes, *, received_at, cutoff_utc):
    """Create a new assessment cutoff without changing source observation time.

    config is a static private account/source selection, not an old decision
    packet. Historical cutoffs must not be overwritten with the current clock.
    All account/financial declarations remain unverified caller input.
    """
    try:
        report, config = json.loads(report_bytes), json.loads(config_bytes)
        if not isinstance(report, dict) or not isinstance(config, dict):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("READONLY_PACKET_INVALID") from None
    supplied = config.get("account_contract")
    if isinstance(supplied, dict) and "cutoff_utc" in supplied:
        raise ValueError("FROZEN_CUTOFF_NOT_REUSABLE")
    contract = {**supplied, "cutoff_utc": cutoff_utc} if isinstance(supplied, dict) else None
    summary = report.get("summary")
    facts = summary.get("account_facts") if isinstance(summary, dict) else None
    diagnostics = report.get("diagnostics")
    revision = diagnostics.get("runtime_revision") if isinstance(diagnostics, dict) else None
    source = dict(source_ref=config.get("source_ref"), source_revision=revision,
                  source_object_sha256=hashlib.sha256(report_bytes).hexdigest(),
                  facts_sha256=digest(facts), contract_sha256=digest(contract),
                  received_at=received_at, published_at=report.get("published_at"))
    result = assess_ibkr_readonly_account_input(facts, contract, source=source)
    binding = config.get("report_binding")
    fields = ("platform", "strategy_profile", "account_scope", "runtime_revision")
    equalities = {}
    for key in fields:
        expected = binding.get(key) if isinstance(binding, dict) else None
        observed = revision if key == "runtime_revision" else report.get(key)
        equalities[key] = expected == observed if all(isinstance(v, str) and v.strip() for v in (expected, observed)) else None
    matches = all(value is True for value in equalities.values())
    receipt = report.get("runtime_release_receipt")
    if (report.get("runtime_revision") is not None and report["runtime_revision"] != revision
            or isinstance(receipt, dict) and receipt.get("runtime_revision") is not None
            and receipt["runtime_revision"] != revision):
        matches = False
    if not matches:
        result["gaps"] = sorted(set(result["gaps"] + ["REPORT_SOURCE_BINDING_MISMATCH"]))
        result.update(status="ACCOUNT_INPUT_GAPS", spendable_cash=None, current_weights=None)
    return dict(assessment=result, source=source, config_sha256=hashlib.sha256(config_bytes).hexdigest(),
                report_binding_matched=bool(matches), report_binding_equalities=equalities,
                source_observed_at=facts.get("observed_at") if isinstance(facts, dict) else None,
                assessment_cutoff_utc=cutoff_utc, historical_pit_attested=False,
                execution_authorized=False, settings_applied=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise ValueError("PRIVATE_LINUX_REQUIRED")
        if any(p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077
               for p in (args.report, args.config)):
            raise ValueError("REGULAR_PRIVATE_INPUT_REQUIRED")
        if (args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir()
                or any(p.is_symlink() for p in args.output.parents)
                or stat.S_IMODE(args.output.parent.stat().st_mode) != 0o700):
            raise ValueError("CREATE_ONLY_PRIVATE_OUTPUT_REQUIRED")
        if any(p.stat().st_size > 2 * 1024 * 1024 for p in (args.report, args.config)):
            raise ValueError("INPUT_SIZE_LIMIT")
        report_bytes, config_bytes = args.report.read_bytes(), args.config.read_bytes()
        received = datetime.now(timezone.utc).isoformat()
        result = inspect_report(report_bytes, config_bytes, received_at=received,
                                cutoff_utc=datetime.now(timezone.utc).isoformat())
        payload = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        assessed = result["assessment"]
        print(json.dumps(dict(status=assessed["status"], gaps=assessed["gaps"],
                              output_sha256=hashlib.sha256(payload).hexdigest(),
                              execution_authorized=False, settings_applied=False)))
        return 0 if assessed["status"] == "READY_READONLY_INPUT" else 2
    except (ValueError, TypeError, OSError, OverflowError):
        print(json.dumps(dict(status="READONLY_INSPECTION_FAILED", execution_authorized=False,
                              settings_applied=False)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
