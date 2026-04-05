from __future__ import annotations

import os
from pathlib import Path

from scripts.build_russell_1000_universe_history import main as build_universe_main
from scripts.fetch_russell_1000_price_history import main as fetch_prices_main


def _require_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise EnvironmentError(f"{name} is required")
    return value


def main() -> int:
    input_dir = _require_env("R1000_SNAPSHOT_INPUT_DIR")
    output_dir = Path(_require_env("R1000_DATA_OUTPUT_DIR"))
    output_dir.mkdir(parents=True, exist_ok=True)

    universe_history_path = output_dir / "r1000_universe_history.csv"
    price_history_path = output_dir / "r1000_price_history.csv"
    symbol_alias_path = output_dir / "r1000_symbol_aliases.csv"

    build_universe_main(
        [
            "--input-dir",
            input_dir,
            "--output",
            str(universe_history_path),
        ]
        + (
            [
                "--backfill-start-date",
                str(os.getenv("R1000_UNIVERSE_BACKFILL_START", "")).strip(),
            ]
            if str(os.getenv("R1000_UNIVERSE_BACKFILL_START", "")).strip()
            else []
        )
    )
    fetch_args = [
        "--universe-history",
        str(universe_history_path),
        "--output",
        str(price_history_path),
        "--snapshot-dir",
        input_dir,
        "--alias-output",
        str(symbol_alias_path),
        "--start",
        _require_env("R1000_PRICE_DOWNLOAD_START"),
    ]
    price_download_end = str(os.getenv("R1000_PRICE_DOWNLOAD_END", "")).strip()
    if price_download_end:
        fetch_args.extend(["--end", price_download_end])
    benchmark_symbol = str(os.getenv("R1000_BENCHMARK_SYMBOL", "")).strip()
    if benchmark_symbol:
        fetch_args.extend(["--benchmark-symbol", benchmark_symbol])
    safe_haven = str(os.getenv("R1000_SAFE_HAVEN", "")).strip()
    if safe_haven:
        fetch_args.extend(["--safe-haven", safe_haven])
    chunk_size = str(os.getenv("R1000_PRICE_CHUNK_SIZE", "")).strip()
    if chunk_size:
        fetch_args.extend(["--chunk-size", chunk_size])

    fetch_prices_main(fetch_args)
    print(f"prepared data under {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
