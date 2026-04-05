from __future__ import annotations

import argparse
from pathlib import Path

from us_equity_strategies.snapshots.russell_1000_multi_factor_defensive import (
    build_feature_snapshot,
    read_table,
    write_table,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a Russell 1000 price-only feature snapshot.",
    )
    parser.add_argument("--prices", required=True, help="Input price history file (.csv/.json/.jsonl/.parquet)")
    parser.add_argument("--universe", required=True, help="Input universe file (.csv/.json/.jsonl/.parquet)")
    parser.add_argument("--output", required=True, help="Output feature snapshot path")
    parser.add_argument("--as-of", dest="as_of_date", help="Snapshot date (defaults to latest price date)")
    parser.add_argument("--benchmark-symbol", default="SPY")
    parser.add_argument("--min-price-usd", type=float, default=10.0)
    parser.add_argument("--min-adv20-usd", type=float, default=20_000_000.0)
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
        min_price_usd=args.min_price_usd,
        min_adv20_usd=args.min_adv20_usd,
        min_history_days=args.min_history_days,
    )
    write_table(snapshot, args.output)
    print(f"wrote {len(snapshot)} rows -> {Path(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
