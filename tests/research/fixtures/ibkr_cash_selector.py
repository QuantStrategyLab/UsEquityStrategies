"""Frozen generic producer functions for artificial contract tests only.

This is a source-preserving projection, not an import of a running broker
service or proof that a deployed producer still uses this version.
"""
from __future__ import annotations

MARKET_CURRENCY_CASH_TAG_PRIORITY = (
    "$LEDGER-CashBalance", "$LEDGER-TotalCashBalance",
    "CashBalance", "TotalCashBalance", "SettledCash",
)


def _normalize_account_ids(account_ids=None) -> tuple[str, ...]:
    if account_ids is None:
        return ()
    if isinstance(account_ids, str):
        candidates = [account_ids]
    else:
        candidates = list(account_ids)
    normalized = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            normalized.append(text)
    return tuple(dict.fromkeys(normalized))

def _matches_account(account_id: str | None, selected_account_ids: tuple[str, ...]) -> bool:
    if not selected_account_ids:
        return True
    return str(account_id or "").strip() in selected_account_ids

def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _cash_value_for_currency(account_values, *, currency: str, account_ids=None) -> float | None:
    selected_account_ids = _normalize_account_ids(account_ids)
    market_currency = str(currency or "USD").strip().upper()
    values_by_account_currency: dict[tuple[str | None, str], dict[str, float]] = {}
    for account_value in account_values or ():
        account_id = str(getattr(account_value, "account", "") or "").strip() or None
        if not _matches_account(account_id, selected_account_ids):
            continue
        value_currency = str(getattr(account_value, "currency", "") or "").strip().upper()
        if not value_currency:
            continue
        value = _coerce_float(getattr(account_value, "value", None))
        if value is None:
            continue
        tag = str(getattr(account_value, "tag", "") or "").strip()
        values_by_account_currency.setdefault((account_id, value_currency), {})[tag] = value

    total = 0.0
    matched = False
    for (_account_id, value_currency), tag_values in values_by_account_currency.items():
        if value_currency != market_currency:
            continue
        for tag in MARKET_CURRENCY_CASH_TAG_PRIORITY:
            if tag in tag_values:
                total += float(tag_values[tag])
                matched = True
                break
    return total if matched else None
