"""Synthetic first-open proposal/whole-share mismatch classification."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module():
    directory = Path(__file__).parents[2] / "docs/research/first_compounding_20260925"
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location(
            "phase5_schwab_first_open_contract", directory / "phase5_schwab_first_open_contract.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))


def test_zero_research_fill_is_not_paper_proposal_activation() -> None:
    module = _module()
    proposals = (SimpleNamespace(symbol="QQQM", details={"side": "buy", "quantity": 4.25}),
                 SimpleNamespace(symbol="TQQQ", details={"side": "buy", "quantity": 0.4}),
                 SimpleNamespace(symbol="BOXX", details={"side": "buy", "quantity": 3.1}))
    result = module._proposal_summary(
        proposals, {"QQQM": 4, "TQQQ": 0, "BOXX": 3},
        {"QQQM": 425, "TQQQ": 40, "BOXX": 310})
    assert result["research_zero_share_but_positive_proposal_symbols"] == ["TQQQ"]
    assert result["fractional_quantity_symbols"] == ["BOXX", "QQQM", "TQQQ"]
    assert result["proposal_equals_research_whole_share_symbols"] == []


def test_zero_target_needs_no_proposal() -> None:
    module = _module()
    proposals = (SimpleNamespace(symbol="QQQM", details={"side": "buy", "quantity": 4.25}),
                 SimpleNamespace(symbol="BOXX", details={"side": "buy", "quantity": 3.1}))
    result = module._proposal_summary(
        proposals, {"QQQM": 4, "TQQQ": 0, "BOXX": 3},
        {"QQQM": 425, "TQQQ": 0, "BOXX": 310})
    assert result["no_target_no_proposal_symbols"] == ["TQQQ"]
    with pytest.raises(ValueError):
        module._proposal_summary(proposals[:1], {"QQQM": 4, "TQQQ": 0, "BOXX": 3},
                                 {"QQQM": 425, "TQQQ": 0, "BOXX": 310})
