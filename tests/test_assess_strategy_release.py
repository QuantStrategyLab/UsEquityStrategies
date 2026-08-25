from __future__ import annotations

import json
from pathlib import Path

from scripts.assess_strategy_release import main


def test_missing_evidence_is_a_failed_diagnostic_not_a_publishable_release(
    tmp_path: Path,
    capsys,
) -> None:
    config = tmp_path / "config.json"
    risk_policy = tmp_path / "risk.py"
    plugin = tmp_path / "plugin.py"
    for path in (config, risk_policy, plugin):
        path.write_text(path.name, encoding="utf-8")

    result = main(
        [
            "--release-id",
            "soxl-paper-assessment-20260824",
            "--strategy-profile",
            "soxl_soxx_trend_income",
            "--strategy-revision",
            "a" * 40,
            "--effective-session",
            "2026-08-25",
            "--target-set-id",
            "longbridge-sg-paper",
            "--target",
            "longbridge:SG",
            "--config-path",
            str(config),
            "--risk-policy-path",
            str(risk_policy),
            "--evidence-path",
            str(tmp_path / "missing-evidence.json"),
            "--plugin-bundle-path",
            str(plugin),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["ready"] is False
    assert payload["findings"] == ["evidence_package_missing"]
