import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_binding_has_one_v2_target_and_no_values_or_fallback():
    binding = json.loads((ROOT / "config/ai-hook-shadow.json").read_text())
    assert set(binding) == {"schema", "service_target", "mode", "purpose", "environment"}
    assert binding["service_target"] == "quant-ai-service-v2"
    assert binding["mode"] == "shadow" and binding["purpose"] == "research"
    assert all(value.startswith("AI_") for value in binding["environment"].values())


@pytest.mark.parametrize("distribution,accepted", [("quant_ai_service-2.0.13", True),
                                                   ("personal_ai_service-2.0.0", False),
                                                   ("quant_ai_service-1.0.0", False)])
def test_installer_only_accepts_approved_quant_v2_wheel(tmp_path, monkeypatch, distribution, accepted):
    spec = importlib.util.spec_from_file_location("ai5_install_client", ROOT / "scripts/install_ai_task_client.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    for key, name in (("AI_SERVICE_CLIENT", distribution), ("AI_SERVICE_QPK", "quant_platform_kit-1.0.0")):
        wheel = tmp_path / (name + "-py3-none-any.whl")
        wheel.write_bytes(b"approved-fixture")
        monkeypatch.setenv(key + "_WHEEL", str(wheel))
        monkeypatch.setenv(key + "_SHA256", hashlib.sha256(wheel.read_bytes()).hexdigest())
    with patch.object(installer.subprocess, "run") as install:
        if accepted:
            installer.main()
            install.assert_called_once()
        else:
            with pytest.raises(SystemExit, match="unavailable"):
                installer.main()
            install.assert_not_called()


def test_workflow_has_no_old_service_artifact_or_profile_default():
    for name in ("global-etf-review.yml", "soxl-research.yml", "soxl-manual-learning.yml",
                 "reusable-drift-check.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text()
        assert "personal_ai_service" not in workflow
        assert "AI_SERVICE_RESEARCH_PROFILE ||" not in workflow
