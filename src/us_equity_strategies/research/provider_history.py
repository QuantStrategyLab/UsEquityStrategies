"""Validate retrospective provider chart bytes; no publication-time claims."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math


def read_provider_history(path: Path, *, expected_sha256: str, expected_symbol: str):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ValueError("PROVIDER_BYTES_MISMATCH")
    chart = json.loads(raw)["chart"]
    if chart.get("error") is not None or len(chart["result"]) != 1:
        raise ValueError("PROVIDER_RESULT_INVALID")
    result = chart["result"][0]
    if result["meta"]["symbol"] != expected_symbol:
        raise ValueError("PROVIDER_SYMBOL_MISMATCH")
    zone = ZoneInfo(result["meta"]["exchangeTimezoneName"])
    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"]["adjclose"][0]["adjclose"]
    fields = ("open", "high", "low", "close", "volume")
    if any(len(quote[k]) != len(stamps) for k in fields) or len(adjusted) != len(stamps):
        raise ValueError("PROVIDER_ARRAY_LENGTH_MISMATCH")
    rows = []
    for i, stamp in enumerate(stamps):
        session = datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(zone).date()
        if rows and session <= rows[-1]["session"]:
            raise ValueError("DUPLICATE_OR_UNORDERED_SESSION")
        prices = [quote[key][i] for key in fields[:4]]
        adj, volume = adjusted[i], quote["volume"][i]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0
               for x in (*prices, adj)):
            raise ValueError("INVALID_OHLC_OR_ADJUSTED_CLOSE")
        op, high, low, close = prices
        if low > min(op, close) or high < max(op, close) or low > high:
            raise ValueError("INVALID_OHLC_RELATIONSHIP")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0:
            raise ValueError("INVALID_VOLUME")
        ratio = adj / close
        rows.append({"session": session, "open": op, "high": high, "low": low, "close": close,
                     "adjusted_open": op * ratio, "adjusted_high": high * ratio,
                     "adjusted_low": low * ratio, "adjusted_close": adj, "volume": volume})
    events = result.get("events", {})
    for kind in ("dividends", "splits"):
        for key, event in events.get(kind, {}).items():
            if int(key) != event["date"]:
                raise ValueError("EVENT_TIMESTAMP_MISMATCH")
            numbers = (event["amount"],) if kind == "dividends" else (event["numerator"], event["denominator"])
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0
                   for x in numbers):
                raise ValueError("INVALID_CORPORATE_ACTION")
    return {"provider_sha256": digest, "symbol": expected_symbol, "rows": rows,
            "event_counts": {kind: len(events.get(kind, {})) for kind in ("dividends", "splits")},
            "price_convention": "provider_quote_times_adjusted_close_over_close",
            "availability": "RETROSPECTIVE_NOT_POINT_IN_TIME", "actions_reconciled": False}
