"""Guard tests for additive APIs while published QPK pins remain unchanged."""
import importlib
import os
import pytest


def require_candidate_dependencies(requirements):
    try:
        for module_name, names in requirements.items():
            module = importlib.import_module(module_name)
            for name in names:
                if not hasattr(module, name):
                    raise ImportError(f"{module_name} lacks {name}")
    except ImportError as exc:
        if os.environ.get("REQUIRE_QSL_STRATEGY_INTEGRATION") == "1":
            raise RuntimeError("required candidate integration dependencies are unavailable") from exc
        pytest.skip("candidate integration APIs are unavailable with published pins", allow_module_level=True)
