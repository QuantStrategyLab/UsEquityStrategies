from __future__ import annotations

import argparse

from us_equity_strategies.data_prep.russell_1000_history import (
    backfill_universe_history_start,
    build_interval_universe_history_from_directory,
    write_interval_universe_history,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build interval-form Russell 1000 universe history from dated snapshot files.",
    )
    parser.add_argument("--input-dir", required=True, help="Directory containing dated constituent snapshots")
    parser.add_argument("--output", required=True, help="Output universe history path")
    parser.add_argument(
        "--backfill-start-date",
        help="Optional date used to backfill the earliest snapshot start_date (useful when the first PIT snapshot starts after the backtest start)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    history = build_interval_universe_history_from_directory(args.input_dir)
    if args.backfill_start_date:
        history = backfill_universe_history_start(history, args.backfill_start_date)
    write_interval_universe_history(history, args.output)
    print(f"wrote {len(history)} rows -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
