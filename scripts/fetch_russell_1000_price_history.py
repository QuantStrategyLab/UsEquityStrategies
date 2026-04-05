from __future__ import annotations

import argparse

from us_equity_strategies.data_prep.russell_1000_history import (
    build_symbol_alias_candidates_from_directory,
    build_symbol_alias_table,
    collect_symbol_universe,
)
from us_equity_strategies.data_prep.yfinance_prices import download_price_history
from us_equity_strategies.snapshots.russell_1000_multi_factor_defensive import read_table, write_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Russell 1000 price history from yfinance using universe-history symbols.",
    )
    parser.add_argument("--universe-history", required=True, help="Universe history file")
    parser.add_argument("--output", required=True, help="Output price history file")
    parser.add_argument("--start", required=True, help="Price download start date")
    parser.add_argument("--end", help="Price download end date")
    parser.add_argument("--benchmark-symbol", default="SPY")
    parser.add_argument("--safe-haven", default="BOXX")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument(
        "--snapshot-dir",
        help="Optional directory of dated universe snapshots; when provided, build ticker alias candidates from snapshot identifiers",
    )
    parser.add_argument(
        "--alias-output",
        help="Optional output path for derived ticker alias candidates (.csv/.json/.jsonl/.parquet)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    universe_history = read_table(args.universe_history)
    symbols = collect_symbol_universe(
        universe_history,
        benchmark_symbol=args.benchmark_symbol,
        safe_haven=args.safe_haven,
    )
    symbol_aliases = (
        build_symbol_alias_candidates_from_directory(args.snapshot_dir)
        if str(args.snapshot_dir or "").strip()
        else {}
    )
    alias_table = build_symbol_alias_table(symbol_aliases)
    prices = download_price_history(
        symbols,
        start=args.start,
        end=args.end,
        chunk_size=args.chunk_size,
        symbol_aliases=symbol_aliases,
    )
    write_table(prices, args.output)
    if args.alias_output:
        write_table(alias_table, args.alias_output)
    print(f"downloaded {len(symbols)} symbols, wrote {len(prices)} rows -> {args.output}")
    if args.alias_output:
        print(f"wrote {len(alias_table)} ticker alias rows across {len(symbol_aliases)} symbols -> {args.alias_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
