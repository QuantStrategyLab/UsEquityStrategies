"""Opt-in synthetic v2 evaluation after checking unchanged M2 core bytes.

This source-checkout entrypoint does not attest the full M2 consumer or transfer
its protocol/real-data authority. Legacy callers never route through this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ENTRYPOINT_VERSION = "qsl.research.walk_forward_v2_entrypoint.v1"
PROTOCOL_JSON = "docs/research/protocols/R3_VS_R0_CHALLENGER_20261022.json"
PROTOCOL_MD = "docs/research/protocols/R3_VS_R0_CHALLENGER_20261022.md"
# Existing protocol bytes, not new or replacement M2 hashes.
_PROTOCOL_SHA256 = {
    PROTOCOL_JSON: "7d10f4c3af1aaa0e640d795f300be4915685a9bb78e6d570969d8afee24a31db",
    PROTOCOL_MD: "3672631b5b77a3846440449fd6fd26bffceacec6084b98351595263f73a30a62",
}
_VERSIONS = ("fractional_kelly_walk_forward_v2", "portfolio_kelly_walk_forward_v2")


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def verify_m2_frozen_core(repository_root: Path) -> dict[str, Any]:
    """Check the original protocol, binding file and its eight core sources.

    Historical verified_git_blobs also lists an older rs04 consumer. That full
    historical environment is intentionally not certified by this scoped check.
    Missing checkout metadata or any mismatching byte prevents evaluation.
    """
    verified: dict[str, str] = {}

    def read_verified(relative: str, expected: str) -> bytes:
        try:
            raw = (repository_root / relative).read_bytes()
        except OSError as exc:
            raise ValueError(f"M2_FROZEN_FILE_UNAVAILABLE:{relative}") from exc
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError(f"M2_FROZEN_HASH_MISMATCH:{relative}")
        verified[relative] = actual
        return raw

    protocol = json.loads(read_verified(PROTOCOL_JSON, _PROTOCOL_SHA256[PROTOCOL_JSON]))
    read_verified(PROTOCOL_MD, _PROTOCOL_SHA256[PROTOCOL_MD])
    binding = protocol["version_binding"]
    binding_path = "src/us_equity_strategies/research/partial_rebalance.py"
    binding_digest = next(row["sha256"] for row in binding["verified_git_blobs"]
                          if row["path"] == binding_path)
    read_verified(binding_path, binding_digest)
    core = binding["core_only_frozen_constants"]["CORE_ONLY_SOURCE_SHA256"]
    for relative, expected in sorted(core.items()):
        read_verified("src/us_equity_strategies/" + relative, expected)
    return {
        "status": "MATCH", "scope": "M2_PROTOCOL_AND_CORE_ONLY_SOURCE",
        "verified_sha256": verified, "m2_consumer_attested": False,
    }


def evaluate_walk_forward_v2(*, evaluator_version: str, **inputs: Any) -> dict[str, Any]:
    """Keep 02B inputs/outputs, adding entrypoint identity and scoped hash evidence.

    The evaluator version is mandatory. Inputs retain the existing v2 synthetic
    and no-netting restrictions; no trial store or production route is created.
    """
    if evaluator_version not in _VERSIONS:
        raise ValueError("EVALUATOR_VERSION_UNSUPPORTED")
    if inputs.get("synthetic", True) is not True or inputs.get("real_data") is not None:
        raise ValueError("NEED_USER_V2_REAL_DATA_PROTOCOL_NOT_REGISTERED")
    check = verify_m2_frozen_core(_repository_root())
    # Validate bytes before loading the opted-in evaluator and its helpers.
    if evaluator_version == "fractional_kelly_walk_forward_v2":
        from us_equity_strategies.research.fractional_kelly_walk_forward_v2 import (
            evaluate_fractional_kelly_walk_forward,
        )
        result = evaluate_fractional_kelly_walk_forward(**inputs)
    else:
        from us_equity_strategies.research.portfolio_kelly_walk_forward_v2 import (
            evaluate_portfolio_kelly_walk_forward,
        )
        result = evaluate_portfolio_kelly_walk_forward(**inputs)
    return {**result, "entrypoint_version": ENTRYPOINT_VERSION, "frozen_hash_check": check}
