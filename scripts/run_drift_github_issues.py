#!/usr/bin/env python3
"""Create GitHub issues for drift alerts after quant-lifecycle drift detection."""

from __future__ import annotations

import os
import sys


def main() -> int:
    domain = os.environ.get("STRATEGY_DOMAIN", "").strip()
    if not domain:
        print("::error::STRATEGY_DOMAIN is required", file=sys.stderr)
        return 1

    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in repository:
        owner, repo = repository.split("/", 1)
        os.environ.setdefault("CODEX_AUDIT_ORG", owner)
        os.environ.setdefault("CODEX_AUDIT_ORCHESTRATOR_REPO", repo)

    from quant_platform_kit.strategy_lifecycle.codex_integration import create_issues_for_domain
    from quant_platform_kit.strategy_lifecycle.drift_detector import run_drift_detection

    drifts = run_drift_detection(domain)
    critical = sum(1 for item in drifts if getattr(getattr(item, "status", None), "value", "") == "critical")
    review = sum(1 for item in drifts if getattr(getattr(item, "status", None), "value", "") == "review")
    print(f"[drift-check] domain={domain} checked={len(drifts)} review={review} critical={critical}")

    results = create_issues_for_domain(domain, dry_run=False)
    created = [item for item in results if item.get("issue_url")]
    errors = [item for item in results if item.get("error")]
    print(f"[drift-check] issues_created={len(created)} errors={len(errors)}")
    for item in created:
        print(f"  - {item.get('issue_url')}")
    for item in errors:
        print(f"::warning::{item.get('title')}: {item.get('error')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
