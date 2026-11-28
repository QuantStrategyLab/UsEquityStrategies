from pathlib import Path


def test_drift_workflow_wires_real_snapshot_history_and_preflight_bundle() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "drift-check.yml").read_text(encoding="utf-8")

    assert "preflight_backtests:" in workflow
    assert "needs: preflight_backtests" in workflow
    assert "snapshot_repository_ref: ${{ steps.snapshot-input.outputs.snapshot_repository_ref }}" in workflow
    assert "id: snapshot-input" in workflow
    assert 'print(run["head_sha"])' in workflow
    assert "snapshot_repository_ref: ${{ needs.preflight_backtests.outputs.snapshot_repository_ref }}" in workflow
    assert workflow.count("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)") == 2
    assert "Download latest trusted market history" in workflow
    assert "gh api --paginate --slurp" in workflow
    assert "trusted-snapshot-runs.json" in workflow
    assert "downloaded_price_history.csv" in workflow
    assert "us-equity-market-history-" in workflow
    assert '"head_branch": "main"' in workflow
    assert '"path": ".github/workflows/publish-snapshot-artifacts.yml"' in workflow
    assert '"conclusion": "success"' in workflow
    assert '"QuantStrategyLab/UsEquitySnapshotPipelines"' in workflow
    assert "repository: QuantStrategyLab/QuantPlatformKit" in workflow
    assert "ref: c7646a7168b3dafa763ef7751a182d23e8de7790" in workflow
    assert "python -m pip install --no-deps -e external/QuantPlatformKit" in workflow
    assert "scripts/run_walk_forward_backtest.py" in workflow
    assert '"--list-lifecycle-profiles"' in workflow
    assert "--market-history" in workflow
    assert "--returns-output" in workflow
    assert "LIFECYCLE_PREFLIGHT_BUNDLE_ROOT" in workflow
    assert "Upload lifecycle preflight artifact" in workflow
    assert "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9" in workflow
    assert "uses: ./.github/workflows/reusable-drift-check.yml" in workflow
    assert "strategy_domain: us_equity" in workflow
    assert "caller_event_name: ${{ github.event_name }}" in workflow
    assert "caller_pr_head_repository: ${{ github.event.pull_request.head.repo.full_name || '' }}" in workflow
    assert "snapshot_repository: QuantStrategyLab/UsEquitySnapshotPipelines" in workflow
    assert "snapshot_checkout_path: external/UsEquitySnapshotPipelines" in workflow
    assert "ai_service_url: ${{ vars.AI_SERVICE_URL }}" in workflow
    assert "lifecycle_performance_bucket: ${{ secrets.LIFECYCLE_PERFORMANCE_BUCKET }}" in workflow
    assert "lifecycle_preflight_artifact: lifecycle-preflight-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "quant_platform_kit_ref: c7646a7168b3dafa763ef7751a182d23e8de7790" in workflow
    assert "gcp_workload_identity_provider: ${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}" in workflow
    assert "gcp_service_account: ${{ secrets.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}" in workflow
    assert "gcp_project_id: ${{ secrets.GCP_PROJECT_ID }}" in workflow
    assert "strategy_profile:" in workflow
    assert "REQUESTED_STRATEGY_PROFILE: ${{ inputs.strategy_profile || '' }}" in workflow
    assert '"--list-profiles"' in workflow
    assert "unsupported targeted lifecycle profile=" in workflow
    assert "id: build-lifecycle" in workflow
    assert "lifecycle_profiles: ${{ steps.build-lifecycle.outputs.lifecycle_profiles }}" in workflow
    assert "lifecycle_profiles=${json.dumps(profiles, separators=(',', ':'))}" in workflow or (
        'handle.write(f"lifecycle_profiles={json.dumps(profiles, separators=(\',\', \':\'))}\\n")' in workflow
    )
    assert "matrix:" in workflow
    assert "profile: ${{ fromJSON(needs.preflight_backtests.outputs.lifecycle_profiles) }}" in workflow
    assert "strategy_profile: ${{ matrix.profile }}" in workflow
    assert "walk-forward research proxy" in workflow
    assert "not paper/live account" in workflow
    assert "tqqq_growth_income" not in workflow.split("LIFECYCLE_PREFLIGHT_BUNDLE_ROOT", 1)[1].split("Upload lifecycle", 1)[0]
    assert "codex_audit_service_url:" not in workflow
    assert "secrets.SNAPSHOT_REPOSITORY_TOKEN || secrets.QSL_REPO_SYNC_TOKEN || github.token" in workflow
    assert "disable_legacy_pat_fallback:" in workflow
    assert "secrets.QSL_DRIFT_APP_ID || secrets.QSL_GITHUB_APP_ID" in workflow
    assert "secrets.QSL_DRIFT_APP_PRIVATE_KEY || secrets.QSL_GITHUB_APP_PRIVATE_KEY" in workflow
    assert "actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349 # v2" in workflow
    assert "repositories: UsEquitySnapshotPipelines" in workflow
    assert "permission-contents: read" in workflow
    assert "permission-actions: read" in workflow
    assert "DISABLE_LEGACY_PAT_FALLBACK: ${{ github.event_name == 'workflow_dispatch' && inputs.disable_legacy_pat_fallback }}" in workflow
    assert "disable_legacy_pat_fallback: ${{ github.event_name == 'workflow_dispatch' && inputs.disable_legacy_pat_fallback }}" in workflow
    assert "qsl_drift_app_id: ${{ secrets.QSL_DRIFT_APP_ID || secrets.QSL_GITHUB_APP_ID }}" in workflow
    assert "qsl_drift_app_private_key: ${{ secrets.QSL_DRIFT_APP_PRIVATE_KEY || secrets.QSL_GITHUB_APP_PRIVATE_KEY }}" in workflow
    assert (
        "snapshot_repository_token: ${{ !(github.event_name == 'workflow_dispatch' && inputs.disable_legacy_pat_fallback) "
        "&& (secrets.SNAPSHOT_REPOSITORY_TOKEN || secrets.QSL_REPO_SYNC_TOKEN || github.token) || '' }}"
    ) in workflow
    assert "Verify App-only snapshot checkout" in workflow
    assert "Clean up App-only snapshot checkout proof" in workflow
    assert "if: ${{ github.event_name == 'workflow_dispatch' && inputs.disable_legacy_pat_fallback }}" in workflow
    assert "repository: QuantStrategyLab/UsEquitySnapshotPipelines" in workflow
    assert "ref: ${{ steps.snapshot-input.outputs.snapshot_repository_ref }}" in workflow
    assert "token: ${{ steps.app_token.outputs.token }}" in workflow
    assert "path: external/app-checkout-proof" in workflow
    assert "persist-credentials: false" in workflow
    assert "test -d external/app-checkout-proof/.git && rm -rf external/app-checkout-proof" in workflow


def test_reusable_drift_workflow_wires_readonly_app_token_and_no_fallback() -> None:
    reusable = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "reusable-drift-check.yml"
    ).read_text(encoding="utf-8")

    assert "disable_legacy_pat_fallback:" in reusable
    assert "qsl_drift_app_id:" in reusable
    assert "qsl_drift_app_private_key:" in reusable
    assert "id: drift_app_token" in reusable
    assert "HAS_QSL_DRIFT_APP_CREDENTIALS: ${{ secrets.qsl_drift_app_id != '' && secrets.qsl_drift_app_private_key != '' }}" in reusable
    assert "if: env.HAS_QSL_DRIFT_APP_CREDENTIALS == 'true'" in reusable
    assert "continue-on-error: ${{ !inputs.disable_legacy_pat_fallback }}" in reusable
    assert "actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349 # v2" in reusable
    assert "owner: QuantStrategyLab" in reusable
    assert "repositories: ${{ steps.validate_caller.outputs.snapshot_repo_name }}" in reusable
    assert "permission-contents: read" in reusable
    assert "permission-actions: read" in reusable
    assert "Verify drift GitHub App token when fallback is disabled" in reusable
    assert (
        "token: ${{ inputs.disable_legacy_pat_fallback && steps.drift_app_token.outputs.token "
        "|| (!inputs.disable_legacy_pat_fallback && (steps.drift_app_token.outputs.token "
        "|| secrets.snapshot_repository_token || github.token)) || '' }}"
    ) in reusable
