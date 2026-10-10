"""Runtime entrypoints/strategies never import research code (RS-02)."""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "us_equity_strategies"
RUNTIME_PATHS = [
    PKG / "entrypoints", PKG / "strategies",
    PKG / "runtime_adapters.py", PKG / "runtime_allowlist.py", PKG / "catalog.py",
    PKG / "platform_registry_support.py", PKG / "combo_entrypoints.py",
]
FORBIDDEN = ("us_equity_strategies.research", "quant_platform_kit.research_stats")


def _files():
    for path in RUNTIME_PATHS:
        if path.is_dir():
            yield from path.rglob("*.py")
        elif path.exists():
            yield path


def _imports(path):
    rel = path.relative_to(PKG.parent).with_suffix("").parts
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(list(rel[: len(rel) - node.level]) + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            yield base
            yield from (f"{base}.{a.name}" for a in node.names)


def test_runtime_modules_do_not_import_research():
    offenders = [
        f"{p.relative_to(ROOT)} -> {name}"
        for p in _files() for name in _imports(p)
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN)
    ]
    assert offenders == []
    assert any(True for _ in _files())


PROBE = r"""
import importlib, sys
for name in ("us_equity_strategies", "us_equity_strategies.entrypoints",
             "us_equity_strategies.strategies", "us_equity_strategies.catalog",
             "us_equity_strategies.runtime_adapters"):
    importlib.import_module(name)
bad = sorted(m for m in sys.modules if m.startswith(("us_equity_strategies.research",
                                                     "quant_platform_kit.research_stats")))
print(bad)
sys.exit(1 if bad else 0)
"""


def test_importing_runtime_does_not_load_research():
    proc = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, capture_output=True, text=True,
                          env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""}, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
