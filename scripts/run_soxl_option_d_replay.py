#!/usr/bin/env python3
"""Local files → optional research appendix; OFF copies baseline bytes exactly."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--option-d-research-replay-enabled", action="store_true", default=False)
    for name in ("input", "config", "policy", "overlay"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args(argv)
    raw = args.baseline_report.read_bytes()
    if not args.option_d_research_replay_enabled:
        sys.stdout.buffer.write(raw)
        return 0
    if any(getattr(args, name) is None for name in ("input", "config", "policy", "overlay")):
        parser.error("enabled replay requires --input, --config, --policy and --overlay")
    # Research and optional calendar code load only after explicit opt-in.
    from quant_platform_kit.common import StrategyRiskStateIdentity

    from us_equity_strategies.research.soxl_option_d import HaltEvidence, SessionFacts, freeze_xnys_calendar
    from us_equity_strategies.research.soxl_option_d_replay import binding_from_frozen_bytes, replay_option_d

    try:
        manifest = json.loads(args.input.read_bytes())
        required = {"identity", "calendar_start", "calendar_end", "observations"}
        allowed = required | {"through_session", "requested_new_risk_usd"}
        if not isinstance(manifest, dict) or not required <= set(manifest) <= allowed:
            raise ValueError("offline manifest has invalid fields")
        binding = binding_from_frozen_bytes(
            identity=StrategyRiskStateIdentity.from_dict(manifest["identity"]),
            calendar=freeze_xnys_calendar(manifest["calendar_start"], manifest["calendar_end"]),
            config_bytes=args.config.read_bytes(), policy_bytes=args.policy.read_bytes(), overlay_bytes=args.overlay.read_bytes(),
        )
        observations = []
        for row in manifest["observations"]:
            fields = dict(row)
            if fields.get("halt_evidence") is not None:
                fields["halt_evidence"] = HaltEvidence(**fields["halt_evidence"])
            observations.append(SessionFacts(**fields))
        report = replay_option_d(
            json.loads(raw), option_d_research_replay_enabled=True, binding=binding, observations=observations,
            through_session=manifest.get("through_session"), requested_new_risk_usd=manifest.get("requested_new_risk_usd"),
        )
        print(json.dumps(report, sort_keys=True, allow_nan=False, indent=2))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"Option D offline input rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
