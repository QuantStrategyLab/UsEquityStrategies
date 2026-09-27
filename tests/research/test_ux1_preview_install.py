"""Packaging checks for the installed UX1 preview entry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from us_equity_strategies.ux1_preview import BUNDLE_FILES, resolve_bundle, stage_bundle
import us_equity_strategies.ux1_preview as entry


_RESEARCH = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
_EXCLUDED = {
    "phase5_capital_scale_compare.py",
    "small_account_demo.py",
    "RESEARCH_SCOPE.md",
    "r8_joint_allocation_status.md",
    "soxl_soxx_trend_income.py",
}


def test_bundle_contains_only_the_imported_research_files() -> None:
    present = {path.name for path in _RESEARCH.iterdir() if path.is_file()}
    bundled = set(BUNDLE_FILES)
    assert bundled < present
    assert _EXCLUDED.isdisjoint(bundled)
    assert "ux1_preview_adapter.py" in bundled
    assert not any(name.startswith("us_equity_strategies") for name in bundled)


def test_stage_bundle_copies_the_fixed_set(tmp_path: Path) -> None:
    destination = tmp_path / "ux1_bundle"
    stage_bundle(_RESEARCH, destination)
    assert sorted(path.name for path in destination.iterdir()) == sorted(BUNDLE_FILES)
    assert (destination / "ux1_preview_adapter.py").is_file()
    assert not (destination / "ux1_preview_adapter.py").is_symlink()


def test_entry_rejects_arguments_without_echoing_them(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["ux1-preview", "--raw-root", "/tmp/secret"])
    assert entry.main() == 0
    captured = capsys.readouterr().out
    body = json.loads(captured)
    assert body["schema"] == "qsl.ux1.preview_result.v1"
    assert body["reason_code"] == "UX1_ARGUMENTS_REJECTED"
    assert "secret" not in captured
    assert "UX1_RAW_ROOT" not in captured


def test_bundle_policy_imports_match_frozen_case() -> None:
    bundle = resolve_bundle()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for name in ("UX1_RAW_ROOT", "UX1_R6_ROOT", "UX1_MATERIALIZED"):
        env.pop(name, None)
    completed = subprocess.run(
        [sys.executable, "-c", _POLICY_CHECK, str(bundle)],
        cwd=Path("/tmp"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "policy-ok"
    assert "UX1_" not in completed.stdout


_POLICY_CHECK = """
import sys
from pathlib import Path
bundle = Path(sys.argv[1])
sys.path.insert(0, str(bundle))
import post_r9_capital_study as capital
import r8_joint_allocation_compare as r8
policy = r8._policy()
hook = capital.capital_hook("C2", 10000)
assert policy["startup"]["dynamic_first_decision_date"] == "2023-03-29"
assert policy["startup"]["first_dynamic_trade_date"] == "2023-03-30"
assert hook.variant == "C2"
assert hook.initial_nav == 10000
assert hook.policy_id == "post_r9_active_capital_boundary_v1"
print("policy-ok")
"""
