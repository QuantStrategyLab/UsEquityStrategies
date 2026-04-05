from __future__ import annotations

import os

from scripts.generate_russell_1000_feature_snapshot import main as generate_snapshot_main


def _require_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise EnvironmentError(f"{name} is required")
    return value


def main() -> int:
    argv = [
        "--prices",
        _require_env("R1000_PRICE_HISTORY_PATH"),
        "--universe",
        _require_env("R1000_UNIVERSE_PATH"),
        "--output",
        _require_env("R1000_FEATURE_SNAPSHOT_PATH"),
    ]

    optional_args = {
        "--as-of": os.getenv("R1000_AS_OF_DATE"),
        "--benchmark-symbol": os.getenv("R1000_BENCHMARK_SYMBOL"),
        "--min-price-usd": os.getenv("R1000_MIN_PRICE_USD"),
        "--min-adv20-usd": os.getenv("R1000_MIN_ADV20_USD"),
        "--min-history-days": os.getenv("R1000_MIN_HISTORY_DAYS"),
    }
    for flag, raw_value in optional_args.items():
        value = str(raw_value or "").strip()
        if value:
            argv.extend([flag, value])

    return generate_snapshot_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
