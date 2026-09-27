"""Copy the UX1 research bundle into the built package."""

from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from us_equity_strategies.ux1_preview import stage_bundle  # noqa: E402


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        stage_bundle(
            ROOT / "docs/research/first_compounding_20260925",
            Path(self.build_lib) / "us_equity_strategies" / "ux1_bundle",
        )


setup(cmdclass={"build_py": build_py})
