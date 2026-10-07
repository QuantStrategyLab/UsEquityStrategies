"""UES-owned source projection; watcher output never authorizes research or trades."""
import argparse
import json
from pathlib import Path
from quant_platform_kit.strategy_lifecycle.watch.runner import load_payload, run_watcher
from quant_platform_kit.strategy_lifecycle.watch.strategy_watch import finding_to_research_task
from .watcher_task import bind_watcher_policy


def build_task(finding):
    task = finding_to_research_task(finding)
    return bind_watcher_policy(task) if task is not None else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_watcher(load_payload(args.input), dry_run=True, task_builder=build_task)
    except Exception:
        print(json.dumps({"status": "unavailable", "reason": "watcher_input_unavailable"}))
        return 3
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("status") == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
