"""Synthetic checks for the read-only locked-share floor audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = (Path(__file__).parents[2] /
            "docs/research/first_compounding_20260925/phase5_locked_inventory_audit.py")
    spec = importlib.util.spec_from_file_location("phase5_locked_inventory_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows():
    rows = [{"date": "2023-03-28", "shares": {"BOXX": 4}}]
    rows.extend({"date": "2023-03-29", "shares": {"BOXX": 5}} for _ in range(442))
    rows.append({"date": "2024-12-31", "shares": {"BOXX": 4}})
    return rows


def test_later_sale_can_leave_initial_locked_floor_intact() -> None:
    result = _module()._first_conflict(_rows(), "BOXX")
    assert result == {"locked_first_close_shares": 4,
                      "first_original_path_conflict": None,
                      "original_share_shortfall_at_first_conflict": None}


def test_first_breach_is_reported_without_claiming_alternative_path() -> None:
    rows = _rows()
    rows[2] = {"date": "2023-03-30", "shares": {"BOXX": 3}}
    result = _module()._first_conflict(rows, "BOXX")
    assert result == {"locked_first_close_shares": 4,
                      "first_original_path_conflict": "2023-03-30",
                      "original_share_shortfall_at_first_conflict": 1}
