#!/usr/bin/env python3
"""PR gate: require a valid evidence package when catalog status is promoted."""

from __future__ import annotations

import ast
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

GATE_STAGES = frozenset(
    {
        "ai_monitored_candidate",
        "shadow_candidate",
        "live_candidate",
        "runtime_enabled",
    }
)
STATUS_ADDED_RE = re.compile(r'^\+.*status="([^"]+)"')
EVIDENCE_SUFFIXES = {".json", ".toml"}
STRATEGY_SPEC_FILENAMES = frozenset({"research-spec.json", "optimization-spec.json"})
STRATEGY_SPEC_ROOTS = frozenset({Path("docs/evidence"), Path("evidence")})
RESEARCH_ARTIFACT_FILENAMES = STRATEGY_SPEC_FILENAMES | {
    "benchmark-registry.json",
    "config-snapshot.json",
    "cost-model.json",
    "data-manifest.json",
    "trial-ledger.json",
}
SUPPORTING_RESEARCH_ARTIFACT_FILENAMES = RESEARCH_ARTIFACT_FILENAMES - STRATEGY_SPEC_FILENAMES
SUPPORTING_ARTIFACT_CONTRACTS: dict[str, dict[str, object]] = {
    "benchmark-registry.json": {
        "schema_version": str,
        "artifact_id": str,
        "strategy_profile": str,
        "state": ("enum", {"planned_not_evaluated", "validated"}),
        "benchmarks": [
            {
                "benchmark_id": str,
                "kind": ("enum", {"capital", "passive", "risk_matched", "simple_rule"}),
                "instrument": str,
                "comparison": str,
            }
        ],
        "acceptance_note": str,
    },
    "config-snapshot.json": {
        "schema_version": str,
        "artifact_id": str,
        "strategy_profile": str,
        "source": {"path": str, "object": str, "scope": str},
        "excluded_runtime_default_keys": [str],
        "signal_contract": {
            "required_input": str,
            "snapshot_contract_version": str,
            "snapshot_manifest_required": bool,
            "signal_effective_after_trading_days": int,
        },
        "runtime_feature_defaults": {
            "source": {"path": str, "object": str},
            "parameters": dict,
        },
        "parameters": dict,
        "replay_boundary": str,
    },
    "cost-model.json": {
        "schema_version": str,
        "artifact_id": str,
        "strategy_profile": str,
        "state": ("enum", {"provisional_not_cost_stress_validated", "validated"}),
        "implementation": {
            "source_code": str,
            "config_class": str,
            "cost_bps": (int, float),
            "application": str,
        },
        "required_before_promotion": [str],
        "known_inconsistency": str,
    },
    "data-manifest.json": {
        "schema_version": str,
        "artifact_id": str,
        "strategy_profile": str,
        "state": ("enum", {"blocked", "ready"}),
        "runtime_input_contract": {
            "required_input": str,
            "snapshot_contract_version": str,
            "snapshot_manifest_required": bool,
            "source_code": str,
        },
        "historical_research_requirements": {
            "immutable_price_revision": str,
            "point_in_time_universe": str,
            "point_in_time_feature_snapshots": str,
            "corporate_action_policy": str,
            "delisting_and_inception_policy": str,
        },
        "available_in_repository": {
            "immutable_historical_manifest": bool,
            "point_in_time_replay_evidence": bool,
            "real_returns_trades_positions": bool,
        },
        "blocker": str,
        "next_artifact": str,
    },
    "trial-ledger.json": {
        "schema_version": str,
        "artifact_id": str,
        "strategy_profile": str,
        "state": ("enum", {"empty_pending_real_data_research", "complete"}),
        "record_all_trials": bool,
        "entries": [dict],
        "rules": [str],
    },
}
SUPPORTING_ARTIFACT_SCHEMA_KINDS = {
    "benchmark-registry.json": "benchmark_registry",
    "config-snapshot.json": "config_snapshot",
    "cost-model.json": "cost_model",
    "data-manifest.json": "data_manifest",
    "trial-ledger.json": "trial_ledger",
}


def _git_diff(base_ref: str) -> str:
    result = subprocess.run(
        ["git", "diff", "--unified=20", f"origin/{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "--unified=20", f"{base_ref}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    return result.stdout


def _promotion_detected(diff: str) -> bool:
    return bool(_promoted_status_locations(diff))


def _source_diff_lines(diff: str) -> list[str]:
    lines: list[str] = []
    source_file = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            source_file = False
        elif line.startswith("+++ b/"):
            path = Path(line[6:])
            source_file = bool(path.parts and path.parts[0] == "src")
        if source_file:
            lines.append(line)
    return lines


def _module_string_constants(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            targets = node.targets
            value = node.value.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
            targets = [node.target]
            value = node.value.value
        else:
            continue
        if not isinstance(value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value
    return constants


def _promoted_profiles(diff: str) -> set[str]:
    return _resolve_promoted_profiles(diff)[0]


def _resolve_promoted_profiles(diff: str) -> tuple[set[str], list[str]]:
    profiles: set[str] = set()
    unresolved: list[str] = []
    parsed_files: dict[Path, ast.AST] = {}
    for path, line_number, status in _promoted_status_locations(diff):
        try:
            tree = parsed_files.get(path)
            if tree is None:
                tree = ast.parse(path.read_text(encoding="utf-8"))
                parsed_files[path] = tree
        except (OSError, SyntaxError) as exc:
            unresolved.append(f"{path}:{line_number}: cannot resolve promoted profile: {exc}")
            continue
        constants = _module_string_constants(tree)
        matches: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not (node.lineno <= line_number <= node.end_lineno):
                continue
            status_keyword = next((item for item in node.keywords if item.arg == "status"), None)
            if (
                status_keyword is None
                or status_keyword.value.lineno != line_number
                or not isinstance(status_keyword.value, ast.Constant)
                or status_keyword.value.value != status
            ):
                continue
            profile_keyword = next(
                (item for item in node.keywords if item.arg in {"canonical_profile", "profile"}), None
            )
            profile = (
                _resolve_profile_expression(profile_keyword.value, constants)
                if profile_keyword is not None
                else None
            )
            if profile is None and profile_keyword is not None:
                profile = _resolve_static_profile_expression(path, tree, profile_keyword.value)
            if profile is None:
                profile = _profile_from_metadata_entry(path, tree, node, constants)
            if profile is not None:
                matches.append(profile)
        if len(matches) != 1:
            unresolved.append(
                f"{path}:{line_number}: promoted status {status!r} must resolve to exactly one profile"
            )
            continue
        profiles.add(matches[0])
    return profiles, unresolved


def _resolve_profile_expression(value: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Name):
        return constants.get(value.id)
    return None


def _profile_from_metadata_entry(
    path: Path,
    tree: ast.AST,
    call: ast.Call,
    constants: dict[str, str],
) -> str | None:
    for node in getattr(tree, "body", []):
        target = None
        value = None
        if isinstance(node, ast.Assign):
            target = next((item for item in node.targets if isinstance(item, ast.Name)), None)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target
            value = node.value
        if (
            not isinstance(target, ast.Name)
            or target.id != "STRATEGY_METADATA"
            or not isinstance(value, ast.Dict)
        ):
            continue
        for index, (key, entry) in enumerate(zip(value.keys, value.values)):
            if entry is not call or key is None:
                continue
            profile = _resolve_profile_expression(key, constants)
            if profile is not None:
                return profile
            return _resolve_static_profile_expression(path, tree, key)
    return None


def _resolve_static_profile_expression(
    path: Path,
    tree: ast.AST,
    value: ast.expr,
    visited: frozenset[tuple[Path, str]] = frozenset(),
) -> str | None:
    constants = _module_string_constants(tree)
    direct = _resolve_profile_expression(value, constants)
    if direct is not None:
        return direct
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and not value.args:
        function = next(
            (
                node
                for node in getattr(tree, "body", [])
                if isinstance(node, ast.FunctionDef) and node.name == value.func.id
            ),
            None,
        )
        if function is not None and len(function.body) == 1 and isinstance(function.body[0], ast.Return):
            returned = function.body[0].value
            if returned is not None:
                return _resolve_static_profile_expression(path, tree, returned, visited)
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        local = _class_string_attribute(tree, value.value.id, value.attr)
        if local is not None:
            return local
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound_name = alias.asname or alias.name
                if isinstance(value, ast.Name) and bound_name == value.id:
                    module_path = _resolve_import_path(path, node.module, node.level)
                    return _resolve_static_export(module_path, alias.name, None, visited)
                if (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and bound_name == value.value.id
                ):
                    if node.module is None:
                        module_path = _resolve_import_path(path, alias.name, node.level)
                        return _resolve_static_export(module_path, value.attr, None, visited)
                    module_path = _resolve_import_path(path, node.module, node.level)
                    return _resolve_static_export(module_path, alias.name, value.attr, visited)
        elif isinstance(node, ast.Import) and isinstance(value, ast.Attribute):
            if not isinstance(value.value, ast.Name):
                continue
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[-1]) == value.value.id:
                    module_path = _resolve_import_path(path, alias.name, 0)
                    return _resolve_static_export(module_path, value.attr, None, visited)
    return None


def _resolve_static_export(
    module_path: Path | None,
    symbol: str,
    attribute: str | None,
    visited: frozenset[tuple[Path, str]],
) -> str | None:
    if module_path is None:
        return None
    marker = (module_path, f"{symbol}.{attribute or ''}")
    if marker in visited:
        return None
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    if attribute is not None:
        return _class_string_attribute(tree, symbol, attribute)
    expression = ast.Name(id=symbol)
    return _resolve_static_profile_expression(module_path, tree, expression, visited | {marker})


def _class_string_attribute(tree: ast.AST, class_name: str, attribute: str) -> str | None:
    class_node = next(
        (
            node
            for node in getattr(tree, "body", [])
            if isinstance(node, ast.ClassDef) and node.name == class_name
        ),
        None,
    )
    if class_node is None:
        return None
    for node in class_node.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if any(isinstance(target, ast.Name) and target.id == attribute for target in node.targets):
                return node.value.value if isinstance(node.value.value, str) else None
    return None


def _resolve_import_path(path: Path, module: str | None, level: int) -> Path | None:
    base = Path("src") if level == 0 else path.parent
    for _ in range(max(level - 1, 0)):
        base = base.parent
    parts = tuple(part for part in (module or "").split(".") if part)
    candidate = base.joinpath(*parts)
    file_candidate = candidate.with_suffix(".py")
    if file_candidate.is_file():
        return file_candidate
    package_candidate = candidate / "__init__.py"
    return package_candidate if package_candidate.is_file() else None


def _promoted_status_locations(diff: str) -> list[tuple[Path, int, str]]:
    locations: list[tuple[Path, int, str]] = []
    source_path: Path | None = None
    new_line: int | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            source_path = None
            new_line = None
            continue
        if line.startswith("+++ b/"):
            candidate = Path(line[6:])
            source_path = candidate if candidate.parts and candidate.parts[0] == "src" else None
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,\d+)?", line)
            new_line = int(match.group(1)) if source_path is not None and match else None
            continue
        if source_path is None or new_line is None:
            continue
        if line.startswith("-"):
            continue
        if line.startswith("+"):
            match = STATUS_ADDED_RE.match(line)
            if match and match.group(1) in GATE_STAGES:
                locations.append((source_path, new_line, match.group(1)))
        new_line += 1
    return locations


def _evidence_paths_from_diff(diff: str) -> list[Path]:
    paths: list[Path] = []
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        candidate = Path(line[6:])
        if _is_research_bundle_artifact(candidate):
            continue
        if candidate.suffix.lower() not in EVIDENCE_SUFFIXES:
            continue
        if "evidence" in candidate.parts or candidate.parent.name == "evidence":
            paths.append(candidate)
    return paths


def _discover_evidence_files(diff: str) -> list[Path]:
    discovered = _evidence_paths_from_diff(diff)
    for folder in (Path("docs/evidence"), Path("evidence")):
        if folder.is_dir():
            discovered.extend(
                path
                for path in folder.iterdir()
                if path.is_file()
                and path.suffix.lower() in EVIDENCE_SUFFIXES
                and not _is_research_bundle_artifact(path)
            )
            discovered.extend(
                path
                for path in folder.rglob("promotion-evidence.json")
                if path.is_file() and not _is_research_bundle_artifact(path)
            )
    explicit = os.environ.get("EVIDENCE_PACKAGE_PATH", "").strip()
    if explicit:
        discovered.append(Path(explicit))
    return sorted({path for path in discovered if path.exists()})


def _is_research_bundle_artifact(path: Path) -> bool:
    return (
        path.name in RESEARCH_ARTIFACT_FILENAMES
        and (path.parent / "research-spec.json").is_file()
        and (path.parent / "optimization-spec.json").is_file()
    )


def _discover_strategy_specs(diff: str) -> dict[Path, dict[str, Path]]:
    bundles: dict[Path, dict[str, Path]] = {}
    for line in diff.splitlines():
        if not line.startswith("+++ b/"):
            continue
        candidate = Path(line[6:])
        if (
            candidate.name not in STRATEGY_SPEC_FILENAMES
            or candidate.parent.parent not in STRATEGY_SPEC_ROOTS
        ):
            continue
        if candidate.exists():
            bundles.setdefault(candidate.parent, {})[candidate.name] = candidate
    return {directory: bundles[directory] for directory in sorted(bundles)}


def _validate_with_lifecycle(path: Path) -> tuple[bool, list[str]]:
    from quant_platform_kit.strategy_lifecycle.evidence_gate import validate_evidence_package_file

    result = validate_evidence_package_file(path)
    issues = list(result.issues)
    return result.valid, issues


def _validate_with_promotion_standard(path: Path) -> tuple[bool, list[str]]:
    script = Path("external/QuantPlatformKit/scripts/validate_strategy_evidence_package.py")
    if not script.exists():
        return True, []
    result = subprocess.run(
        [sys.executable, str(script), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, []
    issues = [line for line in result.stderr.splitlines() if line.strip()]
    issues.extend(line for line in result.stdout.splitlines() if line.strip())
    return False, issues or ["promotion evidence package validation failed"]


def _validate_strategy_specs(
    paths: list[Path],
    *,
    validator=None,
) -> tuple[bool, list[str]]:
    if validator is None:
        try:
            from quant_platform_kit.strategy_spec import validate_strategy_spec_file
        except ImportError:
            return False, ["QuantPlatformKit strategy-spec validator is unavailable; update the QPK pin"]
        validator = validate_strategy_spec_file

    issues: list[str] = []
    for path in paths:
        for issue in validator(path):
            issues.append(f"{path}: {issue}")
    return not issues, issues


def _spec_bundle_for_profile(
    profile: str,
    bundles: dict[Path, dict[str, Path]],
) -> tuple[list[Path], list[str]]:
    expected_directories = {root / profile for root in STRATEGY_SPEC_ROOTS}
    candidates = [
        (directory, bundle) for directory, bundle in bundles.items() if directory in expected_directories
    ]
    if len(candidates) != 1:
        return [], [f"{profile}: expected one changed strategy-spec directory named {profile!r}"]
    directory, bundle = candidates[0]
    missing = sorted(STRATEGY_SPEC_FILENAMES - set(bundle))
    if missing:
        return [], [f"{profile}: missing changed strategy spec files: {', '.join(missing)}"]

    paths = [bundle[name] for name in sorted(STRATEGY_SPEC_FILENAMES)]
    issues: list[str] = []
    payloads: dict[str, dict[str, object]] = {}
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            issues.append(f"{path}: cannot read strategy profile: {exc}")
            continue
        if not isinstance(payload, dict) or payload.get("strategy_profile") != profile:
            issues.append(f"{path}: strategy_profile must be {profile!r}")
        if isinstance(payload, dict):
            payloads[path.name] = payload
    research = payloads.get("research-spec.json")
    optimization = payloads.get("optimization-spec.json")
    if research is not None and optimization is not None:
        expected_research_spec_id = research.get("spec_id")
        if optimization.get("research_spec_id") != expected_research_spec_id:
            issues.append(
                f"{bundle['optimization-spec.json']}: research_spec_id must match "
                f"{bundle['research-spec.json']} spec_id {expected_research_spec_id!r}"
            )
        issues.extend(_validate_supporting_research_artifacts(directory, profile, research, optimization))
    return paths, issues


def _validate_supporting_research_artifacts(
    directory: Path,
    profile: str,
    research: dict[str, object],
    optimization: dict[str, object],
) -> list[str]:
    issues: list[str] = []
    artifacts: dict[str, dict[str, object]] = {}
    for name in sorted(SUPPORTING_RESEARCH_ARTIFACT_FILENAMES):
        path = directory / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            issues.append(f"{path}: required supporting artifact is unavailable: {exc}")
            continue
        if not isinstance(payload, dict):
            issues.append(f"{path}: supporting artifact must be a JSON object")
            continue
        artifacts[name] = payload
        issues.extend(_validate_artifact_contract(path, payload, SUPPORTING_ARTIFACT_CONTRACTS[name]))
        issues.extend(_validate_artifact_semantics(path, name, payload))
        if payload.get("strategy_profile") != profile:
            issues.append(f"{path}: strategy_profile must be {profile!r}")
        expected_schema_version = f"{profile}.{SUPPORTING_ARTIFACT_SCHEMA_KINDS[name]}.v1"
        if payload.get("schema_version") != expected_schema_version:
            issues.append(f"{path}: schema_version must be {expected_schema_version!r}")

    research_data = research.get("data")
    research_reproducibility = research.get("reproducibility")
    research_cost_model = research.get("cost_model")
    research_trial_ledger = research.get("trial_ledger")
    frozen_inputs = optimization.get("frozen_inputs")
    references = {
        "data-manifest.json": [
            research_data.get("manifest_id") if isinstance(research_data, dict) else None,
            frozen_inputs.get("data_manifest_id") if isinstance(frozen_inputs, dict) else None,
        ],
        "config-snapshot.json": [
            research_reproducibility.get("config_artifact_id")
            if isinstance(research_reproducibility, dict)
            else None,
            frozen_inputs.get("universe_id") if isinstance(frozen_inputs, dict) else None,
        ],
        "cost-model.json": [
            research_cost_model.get("model_id") if isinstance(research_cost_model, dict) else None,
            frozen_inputs.get("cost_model_id") if isinstance(frozen_inputs, dict) else None,
        ],
        "trial-ledger.json": [
            research_trial_ledger.get("artifact_id")
            if isinstance(research_trial_ledger, dict)
            else None
        ],
    }
    for name, expected_ids in references.items():
        payload = artifacts.get(name)
        if payload is None:
            continue
        artifact_id = payload.get("artifact_id")
        for expected_id in expected_ids:
            if artifact_id != expected_id:
                issues.append(
                    f"{directory / name}: artifact_id {artifact_id!r} does not match reference {expected_id!r}"
                )

    registry = artifacts.get("benchmark-registry.json")
    if registry is not None:
        registry_benchmarks = registry.get("benchmarks")
        registered_ids = (
            {item.get("benchmark_id") for item in registry_benchmarks if isinstance(item, dict)}
            if isinstance(registry_benchmarks, list)
            else set()
        )
        research_benchmarks = research.get("benchmarks")
        research_ids = (
            {item.get("benchmark_id") for item in research_benchmarks if isinstance(item, dict)}
            if isinstance(research_benchmarks, list)
            else set()
        )
        frozen_benchmark_ids = (
            frozen_inputs.get("benchmark_ids", []) if isinstance(frozen_inputs, dict) else []
        )
        optimization_ids = set(frozen_benchmark_ids) if isinstance(frozen_benchmark_ids, list) else set()
        if registered_ids != research_ids or registered_ids != optimization_ids:
            issues.append(
                f"{directory / 'benchmark-registry.json'}: benchmark_ids must match both strategy specs"
            )
    return issues


def _validate_artifact_contract(
    path: Path,
    value: object,
    contract: object,
    field: str = "$",
) -> list[str]:
    if isinstance(contract, dict):
        if not isinstance(value, dict):
            return [f"{path}: {field} must be an object"]
        issues: list[str] = []
        for name, child_contract in contract.items():
            child_field = f"{field}.{name}"
            if name not in value:
                issues.append(f"{path}: {child_field} is required")
                continue
            issues.extend(_validate_artifact_contract(path, value[name], child_contract, child_field))
        return issues
    if isinstance(contract, list):
        if not isinstance(value, list):
            return [f"{path}: {field} must be an array"]
        item_contract = contract[0]
        issues: list[str] = []
        for index, item in enumerate(value):
            issues.extend(_validate_artifact_contract(path, item, item_contract, f"{field}[{index}]"))
        return issues
    if isinstance(contract, tuple) and contract and contract[0] == "enum":
        allowed = contract[1]
        return [] if value in allowed else [f"{path}: {field} has unsupported value {value!r}"]
    expected_types = contract if isinstance(contract, tuple) else (contract,)
    if type(value) not in expected_types:
        expected = " or ".join(item.__name__ for item in expected_types)
        return [f"{path}: {field} must be {expected}"]
    if isinstance(value, str) and not value.strip():
        return [f"{path}: {field} must not be blank"]
    return []


def _validate_artifact_semantics(
    path: Path, name: str, payload: dict[str, object]
) -> list[str]:
    issues: list[str] = []
    if name == "benchmark-registry.json":
        benchmarks = payload.get("benchmarks")
        if isinstance(benchmarks, list):
            ids = [item.get("benchmark_id") for item in benchmarks if isinstance(item, dict)]
            kinds = {item.get("kind") for item in benchmarks if isinstance(item, dict)}
            if len(ids) != len(set(ids)):
                issues.append(f"{path}: $.benchmarks benchmark_id values must be unique")
            if kinds != {"capital", "passive", "risk_matched", "simple_rule"}:
                issues.append(f"{path}: $.benchmarks must contain all four benchmark kinds")
    elif name == "config-snapshot.json":
        signal_contract = payload.get("signal_contract")
        delay = (
            signal_contract.get("signal_effective_after_trading_days")
            if isinstance(signal_contract, dict)
            else None
        )
        if isinstance(delay, int) and not isinstance(delay, bool) and delay < 0:
            issues.append(f"{path}: signal_effective_after_trading_days must be non-negative")
    elif name == "cost-model.json":
        implementation = payload.get("implementation")
        cost_bps = implementation.get("cost_bps") if isinstance(implementation, dict) else None
        if (
            isinstance(cost_bps, (int, float))
            and not isinstance(cost_bps, bool)
            and ((isinstance(cost_bps, float) and not math.isfinite(cost_bps)) or cost_bps < 0)
        ):
            issues.append(f"{path}: $.implementation.cost_bps must be finite and non-negative")
    elif name == "data-manifest.json" and payload.get("state") == "ready":
        availability = payload.get("available_in_repository")
        if not isinstance(availability, dict) or not all(availability.values()):
            issues.append(f"{path}: ready data manifest requires all availability evidence")
    elif name == "trial-ledger.json" and payload.get("state") == "complete":
        entries = payload.get("entries")
        if payload.get("record_all_trials") is not True or not isinstance(entries, list) or not entries:
            issues.append(f"{path}: complete trial ledger requires recorded trial entries")
    return issues


def _run_promotion_dual_review(evidence_files: list[Path]) -> int:
    root = Path(os.environ.get("AIAUDIT_BRIDGE_ROOT", "external/AIAuditBridge"))
    script = root / "scripts" / "run_dual_review_pipeline.py"
    if not script.is_file():
        print("[evidence-gate] dual-review skipped: AIAuditBridge not found")
        return 0
    if str(os.environ.get("DUAL_REVIEW_GATE_SKIP", "")).strip().lower() in {"1", "true", "yes"}:
        print("[evidence-gate] dual-review skipped by DUAL_REVIEW_GATE_SKIP")
        return 0

    worst = 0
    for path in evidence_files:
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--from-evidence",
                str(path),
                "--dispatch",
            ],
            cwd=str(root),
            env={**os.environ, "PYTHONPATH": str(root)},
            check=False,
        )
        print(f"[evidence-gate] dual-review {path.name} exit={proc.returncode}")
        if proc.stdout:
            print(proc.stdout.strip())
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        worst = max(worst, proc.returncode)
    if worst >= 2:
        print("::error::Dual-review blocked promotion (disagreement or reject)", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    base_ref = os.environ.get("GITHUB_BASE_REF", "main").strip() or "main"
    diff = _git_diff(base_ref)

    if not _promotion_detected(diff):
        print("[evidence-gate] No lifecycle status promotion detected; skipping validation")
        return 0

    evidence_files = _discover_evidence_files(diff)
    promoted_profiles, unresolved_promotions = _resolve_promoted_profiles(diff)
    if unresolved_promotions:
        for issue in unresolved_promotions:
            print(f"::error::{issue}", file=sys.stderr)
        return 1
    if not promoted_profiles:
        print(
            "::error::Catalog status promotion detected but the promoted strategy profile could not be resolved",
            file=sys.stderr,
        )
        return 1

    strategy_spec_bundles = _discover_strategy_specs(diff)
    strategy_spec_paths: list[Path] = []
    bundle_issues: list[str] = []
    for profile in sorted(promoted_profiles):
        paths, issues = _spec_bundle_for_profile(profile, strategy_spec_bundles)
        strategy_spec_paths.extend(paths)
        bundle_issues.extend(issues)
    if bundle_issues:
        for issue in bundle_issues:
            print(f"::error::{issue}", file=sys.stderr)
        return 1

    specs_ok, spec_issues = _validate_strategy_specs(strategy_spec_paths)
    if not specs_ok:
        print("::error::Strategy specification validation failed", file=sys.stderr)
        for issue in spec_issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    if not evidence_files:
        print(
            "::error::Catalog status promotion requires a separate validated promotion evidence package; "
            "strategy specs and supporting research artifacts do not replace lifecycle review",
            file=sys.stderr,
        )
        return 1

    failed = False
    for path in evidence_files:
        lifecycle_ok, lifecycle_issues = _validate_with_lifecycle(path)
        standard_ok, standard_issues = _validate_with_promotion_standard(path)
        if lifecycle_ok and standard_ok:
            print(f"[evidence-gate] PASS {path}")
            continue
        failed = True
        print(f"[evidence-gate] FAIL {path}", file=sys.stderr)
        for issue in lifecycle_issues + standard_issues:
            print(f"  - {issue}", file=sys.stderr)

    if failed:
        return 1
    return _run_promotion_dual_review(evidence_files)


if __name__ == "__main__":
    raise SystemExit(main())
