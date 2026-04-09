from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from us_equity_strategies.snapshots.qqq_tech_enhancement import (
    build_feature_snapshot,
    read_table,
    write_table,
)
from us_equity_strategies.strategies.qqq_tech_enhancement import (
    PROFILE_NAME,
    SNAPSHOT_CONTRACT_VERSION,
)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _default_config_path() -> Path | None:
    sibling = (
        Path(__file__).resolve().parents[2]
        / "InteractiveBrokersPlatform"
        / "research"
        / "configs"
        / "growth_pullback_qqq_tech_enhancement.json"
    )
    return sibling if sibling.exists() else None


def write_snapshot_manifest(
    *,
    snapshot_path: Path,
    snapshot,
    config_path: Path | None,
    manifest_path: Path | None = None,
) -> Path:
    resolved_manifest = manifest_path or Path(f"{snapshot_path}.manifest.json")
    if config_path is None or not config_path.exists():
        raise FileNotFoundError(
            f"qqq_tech_enhancement snapshot manifest requires a valid config_path, got: {config_path}"
        )
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config_sha256 = _sha256_file(config_path)
    payload = {
        "manifest_type": "feature_snapshot",
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "strategy_profile": PROFILE_NAME,
        "config_name": str(config_payload.get("name") or PROFILE_NAME),
        "config_path": str(config_path) if config_path is not None else None,
        "config_sha256": config_sha256,
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": _sha256_file(snapshot_path),
        "snapshot_as_of": str(snapshot["as_of"].max()),
        "row_count": int(len(snapshot)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    resolved_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return resolved_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a qqq_tech_enhancement feature snapshot.",
    )
    parser.add_argument("--prices", required=True, help="Input price history file (.csv/.json/.jsonl/.parquet)")
    parser.add_argument("--universe", required=True, help="Input universe file (.csv/.json/.jsonl/.parquet)")
    parser.add_argument("--output", required=True, help="Output feature snapshot path")
    parser.add_argument("--manifest-output", default=None, help="Optional output path for sidecar manifest JSON")
    parser.add_argument(
        "--config-path",
        default=str(_default_config_path()) if _default_config_path() is not None else None,
        help="Canonical strategy config path used to populate manifest metadata",
    )
    parser.add_argument("--as-of", dest="as_of_date", required=True, help="Snapshot date")
    parser.add_argument("--benchmark-symbol", default="QQQ")
    parser.add_argument("--safe-haven", default="BOXX")
    parser.add_argument("--min-price-usd", type=float, default=10.0)
    parser.add_argument("--min-adv20-usd", type=float, default=50_000_000.0)
    parser.add_argument("--min-history-days", type=int, default=252)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    price_history = read_table(args.prices)
    universe_snapshot = read_table(args.universe)
    snapshot = build_feature_snapshot(
        price_history,
        universe_snapshot,
        as_of_date=args.as_of_date,
        benchmark_symbol=args.benchmark_symbol,
        safe_haven=args.safe_haven,
        min_price_usd=args.min_price_usd,
        min_adv20_usd=args.min_adv20_usd,
        min_history_days=args.min_history_days,
    )
    write_table(snapshot, args.output)
    manifest_path = write_snapshot_manifest(
        snapshot_path=Path(args.output),
        snapshot=snapshot,
        config_path=Path(args.config_path) if args.config_path else None,
        manifest_path=Path(args.manifest_output) if args.manifest_output else None,
    )
    print(f"wrote {len(snapshot)} rows -> {Path(args.output)}")
    print(f"wrote manifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
