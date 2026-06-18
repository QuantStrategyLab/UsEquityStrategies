from __future__ import annotations

import argparse
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

from .smart_dca_research import (
    compare_execution_day_contribution_scenarios,
    compare_monthly_execution_day_scenarios,
    SUPPORTED_DCA_CADENCES,
    write_scenario_research_artifacts,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        signal_prices = _load_signal_frame(
            args.signal_csv,
            date_column=args.date_column,
            signal_columns=_parse_column_list(args.signal_columns),
        )
        trade_prices = _load_trade_series(
            args.trade_csv,
            date_column=args.date_column,
            trade_column=args.trade_column,
        )
        output_dir = Path(args.output_dir)
        execution_days = _parse_execution_days(args.execution_days)
        contribution_values = _parse_contribution_values(
            args.monthly_contribution_usd_values
        )
        start_dates = _parse_start_dates(args.start_dates)
        cadences = _parse_cadences(args.cadences)
        if contribution_values is None and start_dates is None and cadences == ("monthly",):
            contribution_values = (args.monthly_contribution_usd,)
            scenarios = compare_monthly_execution_day_scenarios(
                signal_prices=signal_prices,
                trade_prices=trade_prices,
                execution_days=execution_days,
                candidate_set=args.candidate_set,
                monthly_contribution_usd=args.monthly_contribution_usd,
                start_date=args.start_date,
                end_date=args.end_date,
                align_start_after_warmup=not args.no_align_start_after_warmup,
                min_investment_usd=args.min_investment_usd,
            )
        else:
            if contribution_values is None:
                contribution_values = (args.monthly_contribution_usd,)
            scenarios = compare_execution_day_contribution_scenarios(
                signal_prices=signal_prices,
                trade_prices=trade_prices,
                execution_days=execution_days,
                monthly_contribution_usd_values=contribution_values,
                start_dates=start_dates,
                cadences=cadences,
                candidate_set=args.candidate_set,
                start_date=args.start_date,
                end_date=args.end_date,
                align_start_after_warmup=not args.no_align_start_after_warmup,
                min_investment_usd=args.min_investment_usd,
            )
        metadata = _research_metadata(
            args=args,
            execution_days=execution_days,
            contribution_values=contribution_values,
            start_dates=start_dates,
            cadences=cadences,
        )
        artifact_paths = write_scenario_research_artifacts(
            output_dir,
            scenarios,
            metadata=metadata,
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = {
        "candidate_set": args.candidate_set,
        "monthly_contribution_usd_values": contribution_values,
        "start_dates": None if start_dates is None else [item.date().isoformat() for item in start_dates],
        "cadences": cadences,
        "metadata": metadata,
        "output_dir": str(output_dir),
        "scenario_index": str(artifact_paths["scenario_index"]),
        "scenario_manifest": str(artifact_paths["scenario_manifest"]),
        "artifacts": {
            name: str(path)
            for name, path in sorted(artifact_paths.items())
        },
    }
    print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run fixed smart-DCA research candidates from local CSV files and "
            "write metrics, decision logs, and manifest artifacts."
        )
    )
    parser.add_argument("--signal-csv", required=True, type=Path)
    parser.add_argument("--trade-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--candidate-set",
        default="nasdaq_sp500_price",
        help=(
            "Candidate set or preset name. Known sets include nasdaq_sp500_price, "
            "ibit_btc_ahr999_mayer_price, ibit_btc_ahr999_mayer_precomputed, and all."
        ),
    )
    parser.add_argument(
        "--signal-columns",
        help="Comma-separated signal CSV columns. Defaults to all non-date columns.",
    )
    parser.add_argument(
        "--trade-column",
        help="Trade price column. Defaults to close/adj_close if present, else first value column.",
    )
    parser.add_argument("--date-column", default="date")
    parser.add_argument("--monthly-contribution-usd", type=float, default=1000.0)
    parser.add_argument(
        "--monthly-contribution-usd-values",
        help=(
            "Comma-separated contribution amounts for robustness runs. "
            "When set, the CLI runs execution day x contribution amount scenarios."
        ),
    )
    parser.add_argument("--min-investment-usd", type=float, default=0.0)
    parser.add_argument(
        "--cadences",
        default="monthly",
        help="Comma-separated DCA cadences for robustness runs: weekly, monthly, quarterly.",
    )
    parser.add_argument("--execution-days", default="1,10,15,20,25")
    parser.add_argument("--start-date")
    parser.add_argument(
        "--start-dates",
        help=(
            "Comma-separated explicit rolling-start dates. When set, the CLI "
            "runs start date x execution day x contribution amount scenarios."
        ),
    )
    parser.add_argument("--end-date")
    parser.add_argument(
        "--no-align-start-after-warmup",
        action="store_true",
        help="Do not align the shared backtest start after candidate warm-up history.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def _parse_column_list(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    columns = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not columns:
        raise ValueError("signal-columns must include at least one column")
    return columns


def _research_metadata(
    *,
    args: argparse.Namespace,
    execution_days: tuple[int, ...],
    contribution_values: tuple[float, ...],
    start_dates: tuple[pd.Timestamp, ...] | None,
    cadences: tuple[str, ...],
) -> dict[str, object]:
    return {
        "research_config": {
            "candidate_set": args.candidate_set,
            "date_column": args.date_column,
            "signal_columns": _parse_column_list(args.signal_columns),
            "trade_column": args.trade_column,
            "monthly_contribution_usd": args.monthly_contribution_usd,
            "monthly_contribution_usd_values": contribution_values,
            "cadences": cadences,
            "execution_days": execution_days,
            "start_date": args.start_date,
            "start_dates": None
            if start_dates is None
            else tuple(item.date().isoformat() for item in start_dates),
            "end_date": args.end_date,
            "align_start_after_warmup": not args.no_align_start_after_warmup,
            "min_investment_usd": args.min_investment_usd,
        },
        "input_artifacts": {
            "signal_csv": _file_record(args.signal_csv),
            "trade_csv": _file_record(args.trade_csv),
        },
    }


def _file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_execution_days(raw: str) -> tuple[int, ...]:
    days: list[int] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            day = int(value)
        except ValueError as exc:
            raise ValueError(f"execution day must be an integer: {value!r}") from exc
        if day < 1 or day > 31:
            raise ValueError(f"execution day must be between 1 and 31: {day}")
        days.append(day)
    if not days:
        raise ValueError("execution-days must include at least one day")
    return tuple(days)


def _parse_contribution_values(raw: str | None) -> tuple[float, ...] | None:
    if raw is None:
        return None
    amounts: list[float] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            amount = float(value)
        except ValueError as exc:
            raise ValueError(f"monthly contribution must be numeric: {value!r}") from exc
        if amount <= 0.0:
            raise ValueError(f"monthly contribution must be positive: {amount}")
        amounts.append(amount)
    if not amounts:
        raise ValueError("monthly-contribution-usd-values must include at least one amount")
    return tuple(amounts)


def _parse_start_dates(raw: str | None) -> tuple[pd.Timestamp, ...] | None:
    if raw is None:
        return None
    dates: list[pd.Timestamp] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            raise ValueError(f"start date must be parseable: {value!r}")
        dates.append(timestamp.tz_localize(None).normalize())
    if not dates:
        raise ValueError("start-dates must include at least one date")
    return tuple(dates)


def _parse_cadences(raw: str) -> tuple[str, ...]:
    values = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("cadences must include at least one cadence")
    unsupported = [value for value in values if value not in SUPPORTED_DCA_CADENCES]
    if unsupported:
        raise ValueError(
            "unsupported cadences: "
            + ", ".join(unsupported)
            + "; supported: "
            + ", ".join(sorted(SUPPORTED_DCA_CADENCES))
        )
    return values


def _load_signal_frame(
    path: Path,
    *,
    date_column: str,
    signal_columns: tuple[str, ...] | None,
) -> pd.DataFrame:
    frame = _read_csv_with_date_index(path, date_column=date_column)
    columns = signal_columns or tuple(frame.columns)
    _require_columns(frame, columns, role="signal")
    result = frame.loc[:, list(columns)].apply(pd.to_numeric, errors="coerce")
    result = result.dropna(how="all")
    if result.empty:
        raise ValueError(f"signal CSV has no numeric signal values: {path}")
    return result


def _load_trade_series(
    path: Path,
    *,
    date_column: str,
    trade_column: str | None,
) -> pd.Series:
    frame = _read_csv_with_date_index(path, date_column=date_column)
    column = trade_column or _default_trade_column(frame)
    _require_columns(frame, (column,), role="trade")
    result = pd.to_numeric(frame[column], errors="coerce").dropna()
    result = result[result > 0.0]
    if result.empty:
        raise ValueError(f"trade CSV has no positive trade prices: {path}")
    return result


def _read_csv_with_date_index(path: Path, *, date_column: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if date_column not in frame.columns:
        raise ValueError(f"CSV missing date column {date_column!r}: {path}")
    index = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
    if index.isna().any():
        raise ValueError(f"CSV contains invalid dates in {date_column!r}: {path}")
    frame = frame.drop(columns=[date_column])
    frame.index = pd.DatetimeIndex(index).tz_convert(None).normalize()
    frame = frame.sort_index()
    return frame.groupby(level=0).last()


def _default_trade_column(frame: pd.DataFrame) -> str:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in ("close", "adj_close", "adj close"):
        if candidate in normalized:
            return normalized[candidate]
    if len(frame.columns) == 1:
        return str(frame.columns[0])
    raise ValueError(
        "trade CSV must include --trade-column when it has multiple non-date columns"
    )


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], *, role: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        available = ", ".join(str(column) for column in frame.columns)
        raise ValueError(f"missing {role} columns {missing}; available columns: {available}")


if __name__ == "__main__":
    raise SystemExit(main())
