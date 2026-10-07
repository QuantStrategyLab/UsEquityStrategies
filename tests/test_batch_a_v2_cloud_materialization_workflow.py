from __future__ import annotations

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "batch-a-v2-cloud-materialization.yml"
)


def test_batch_a_v2_cloud_materialization_workflow_is_manual_default_branch_readonly() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "gcs_input_prefix:" in workflow
    assert "schedule:" not in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "workflow_run:" not in workflow
    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in workflow
    )
    assert "github.ref == 'refs/heads/main'" not in workflow

    assert "permissions:" in workflow
    assert "contents: read" in workflow
    assert "id-token: write" in workflow
    assert "issues: write" not in workflow
    assert "pull-requests:" not in workflow
    assert "contents: write" not in workflow
    assert "actions: write" not in workflow
    assert "packages: write" not in workflow

    assert (
        "^gs://qsl-research-evidence-831478360303/research/v2/input/"
        "[A-Za-z0-9][A-Za-z0-9._-]{0,62}$"
    ) in workflow
    assert "GCS_INPUT_PREFIX_REJECTED" in workflow

    assert "google-github-actions/auth@" in workflow
    assert "google-github-actions/setup-gcloud@" in workflow
    assert (
        "workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}"
        in workflow
    )
    assert (
        "service_account: ${{ vars.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}" in workflow
    )
    assert "project_id: ${{ vars.GCP_PROJECT_ID }}" in workflow

    assert "scripts/run_batch_a_from_gcs.py" in workflow
    assert "scripts/run_c3_batch_a_existing_member_baseline.py" in workflow
    assert '--gcs-input-prefix "${GCS_INPUT_PREFIX}"' in workflow
    assert "--pack-output" in workflow
    assert "--frozen-member-pack" in workflow
    assert "--staging-root" in workflow
    assert 'work_root="${RUNNER_TEMP}/batch-a-v2-cloud-materialization"' in workflow
    assert 'pack_path="${work_root}/member-pack.v2.json"' in workflow
    assert "${work_root}/staging" in workflow
    assert (
        'result_json="${RUNNER_TEMP}/batch-a-v2-cloud-materialization-result.json"'
        in workflow
    )
    assert "RUNNER_TEMP" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert '| tee "${result_json}"' in workflow
    assert '| tee -a "${GITHUB_STEP_SUMMARY}"' in workflow
    assert '>> "$GITHUB_STEP_SUMMARY"' not in workflow
    assert '>> "${GITHUB_STEP_SUMMARY}"' not in workflow

    assert "upload-artifact" not in workflow
    assert "actions/upload-artifact" not in workflow
    assert "storage rm" not in workflow
    assert "storage mv" not in workflow
    assert "storage ls" not in workflow
    assert "storage cp" not in workflow
    assert "objects.delete" not in workflow
    assert "objects.list" not in workflow
    assert "gsutil rm" not in workflow
    assert "gsutil mb" not in workflow
    assert "gh api" not in workflow
    assert "ALPACA_" not in workflow
    assert "placeorder" not in workflow.lower()
    assert "broker" not in workflow.lower()
    assert "AI_GATEWAY" not in workflow
    assert "CODEX_AUDIT" not in workflow
    assert "--execute" not in workflow
    assert "acquire_batch_a" not in workflow


def test_cloud_workflow_consumes_historical_report_and_verifies_runtime_before_auth():
    # CLI behavior is exercised separately using generated snapshots; this check
    # binds the actual cloud caller to that tested opt-in and prior staging.
    text = WORKFLOW.read_text()
    assert "--historical-account-report" in text
    assert '--snapshot-input-root "${work_root}/staging"' in text
    assert "--annual-risk-free-rate 0.0" in text
    assert "--annual-minimum-acceptable-return 0.0" in text
    assert "report_historical_batch_a_baselines" in text
    assert "compute_historical_research_metrics" in text
    assert "f6f2079f6b53d3cbb3b72bdcc5c2476ed8f170f1" in text
    assert text.index("Verify fixed historical report runtime") < text.index("Authenticate to Google Cloud")
    assert "--apply-risk-scaling" not in text  # Separate original approximation remains opt-in.
    assert "--synthetic" not in text
    assert "upload-artifact" not in text
