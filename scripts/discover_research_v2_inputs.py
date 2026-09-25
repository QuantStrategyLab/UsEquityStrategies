#!/usr/bin/env python3
"""Bounded, read-only inventory of the approved research v2 input prefix.

The Cloud SDK handles page tokens internally. A full 5,000-object response is
conservatively reported as incomplete; no bucket-root or other-prefix query is
made. Only names directly relevant to this research enter the public summary.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence

PREFIX = "gs://qsl-research-evidence-831478360303/research/v2/input/"
OBJECT_PREFIX = "research/v2/input/"
MAX_OBJECTS = 5000
PAGE_SIZE = 500
MATCHES = ("qqqm", "boxx", "regime", "taco", "crisis", "macro", "manifest", "schema", "catalog", "index")


def summarize(raw: object) -> dict[str, object]:
    if not isinstance(raw, list):
        raise ValueError("LIST_FORMAT_UNRECOGNIZED")
    if len(raw) > MAX_OBJECTS:
        raise ValueError("OBJECT_BUDGET_EXCEEDED")
    matches: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("LIST_FORMAT_UNRECOGNIZED")
        name = item.get("name", "")
        if name.startswith("gs://qsl-research-evidence-831478360303/"):
            name = name.removeprefix("gs://qsl-research-evidence-831478360303/")
        if not isinstance(name, str) or not name.startswith(OBJECT_PREFIX):
            raise ValueError("OUT_OF_SCOPE_OBJECT")
        if any(term in name.lower() for term in MATCHES):
            matches.append({
                "uri": f"gs://qsl-research-evidence-831478360303/{name}",
                "generation": item.get("generation"),
                "size": item.get("size"),
                "md5Hash": item.get("md5Hash"),
                "crc32c": item.get("crc32c"),
                "updated": item.get("updated"),
            })
    return {
        "status": "RANGE_INCOMPLETE" if len(raw) == MAX_OBJECTS else "LIST_COMPLETE",
        "scope": PREFIX,
        "objects_seen": len(raw),
        "object_budget": MAX_OBJECTS,
        "page_size": PAGE_SIZE,
        "matched_objects": matches,
        "raw_content_read_bytes": 0,
        "execution_authorized": False,
        "no_order": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        raise ValueError("NO_INPUTS_ALLOWED")
    command = [
        "gcloud", "storage", "objects", "list", PREFIX + "**",
        "--raw", "--exhaustive", "--format=json", f"--limit={MAX_OBJECTS}",
        f"--page-size={PAGE_SIZE}", "--quiet",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=1200)
        if result.returncode != 0:
            stderr = result.stderr.lower()
            reason = "LIST_PERMISSION_DENIED" if "permission" in stderr or "403" in stderr else "LIST_FAILED"
            payload = {"status": "PARKED", "reason_code": reason, "scope": PREFIX,
                       "execution_authorized": False, "no_order": True}
            code = 2
        else:
            payload = summarize(json.loads(result.stdout))
            code = 0
    except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
        payload = {"status": "PARKED", "reason_code": "LIST_OR_FORMAT_FAILED",
                   "scope": PREFIX, "execution_authorized": False, "no_order": True}
        code = 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
