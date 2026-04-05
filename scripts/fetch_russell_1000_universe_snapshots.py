from __future__ import annotations

import argparse
from pathlib import Path

from us_equity_strategies.data_prep.russell_1000_history import (
    download_ishares_historical_universe_snapshots,
    download_ishares_universe_snapshots,
)
from us_equity_strategies.snapshots.russell_1000_multi_factor_defensive import write_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Russell 1000 proxy universe snapshots from iShares IWB holdings history.",
    )
    parser.add_argument("--output-dir", required=True, help="Directory where dated snapshot files will be written")
    parser.add_argument(
        "--metadata-output",
        help="Optional metadata output path (.csv/.json/.jsonl/.parquet)",
    )
    parser.add_argument(
        "--source",
        choices=("official_monthly", "wayback"),
        default="official_monthly",
        help="official_monthly = iShares official dated JSON history; wayback = archived live CSV captures",
    )
    parser.add_argument("--start-date", help="Earliest date to request for official monthly history (defaults from --from-year)")
    parser.add_argument("--end-date", help="Latest date to request for official monthly history")
    parser.add_argument(
        "--max-lookback-days",
        type=int,
        default=7,
        help="When an exact requested date has no holdings, step back up to this many days to find the latest available trading date",
    )
    parser.add_argument("--from-year", type=int, default=2020, help="Earliest Wayback capture year to query")
    parser.add_argument("--to-year", type=int, help="Latest Wayback capture year to query")
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Skip the current live IWB holdings file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.source == "official_monthly":
        start_date = args.start_date or f"{args.from_year:04d}-01-01"
        end_date = args.end_date or (f"{args.to_year:04d}-12-31" if args.to_year else None)
        snapshot_tables, metadata = download_ishares_historical_universe_snapshots(
            start_date=start_date,
            end_date=end_date,
            max_lookback_days=args.max_lookback_days,
        )
    else:
        snapshot_tables, metadata = download_ishares_universe_snapshots(
            from_year=args.from_year,
            to_year=args.to_year,
            include_live=not args.no_live,
        )

    for as_of_date, snapshot in snapshot_tables:
        output_path = output_dir / f"r1000_{as_of_date:%Y-%m-%d}.csv"
        write_table(snapshot, output_path)

    if args.metadata_output:
        write_table(metadata, args.metadata_output)

    print(f"wrote {len(snapshot_tables)} snapshot files -> {output_dir}")
    if args.metadata_output:
        print(f"wrote snapshot metadata -> {args.metadata_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
