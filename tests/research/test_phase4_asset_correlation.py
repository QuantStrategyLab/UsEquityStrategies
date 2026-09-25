"""Focused economic-return and matrix checks for the asset risk reader."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest


def _module():
    directory = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location("phase4_asset_correlation", directory / "phase4_asset_correlation.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))


def test_raw_ex_date_dividend_enters_economic_holding_return_once() -> None:
    return_one_day = _module()._holding_return
    assert return_one_day(100, 99, 1) == pytest.approx(0)
    assert return_one_day(100, 99, 0) == pytest.approx(-0.01)
    with pytest.raises(ValueError):
        return_one_day(100, 99, -1)


def test_correlation_matrix_is_symmetric_and_unit_diagonal() -> None:
    module = _module()
    qqqm = tuple(0.01 if index % 2 else -0.01 for index in range(444))
    tqqq = tuple(3 * value for value in qqqm)
    boxx = tuple(0.001 * math.sin(index) for index in range(444))
    matrix = module._matrix({"QQQM": qqqm, "TQQQ": tqqq, "BOXX": boxx})
    assert matrix["QQQM"]["TQQQ"] == pytest.approx(1)
    assert matrix["TQQQ"]["QQQM"] == pytest.approx(matrix["QQQM"]["TQQQ"])
    assert all(matrix[symbol][symbol] == 1 for symbol in module.SYMBOLS)
