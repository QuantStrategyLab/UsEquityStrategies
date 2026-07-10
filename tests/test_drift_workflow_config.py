from pathlib import Path


def test_drift_workflow_wires_real_snapshot_history_and_preflight_bundle() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "drift-check.yml").read_text(encoding="utf-8")

    assert "preflight_backtests:" in workflow
    assert "needs: preflight_backtests" in workflow
    assert "Download latest trusted market history" in workflow
    assert "gh api --paginate --slurp" in workflow
    assert "downloaded_price_history.csv" in workflow
    assert 'head_branch") == "main"' in workflow
    assert '"path": ".github/workflows/publish-snapshot-artifacts.yml"' in workflow
    assert '"conclusion": "success"' in workflow
    assert '"QuantStrategyLab/UsEquitySnapshotPipelines"' in workflow
    assert "repository: QuantStrategyLab/QuantPlatformKit" in workflow
    assert "ref: 7f0367c3bcb470306f6613d76e61365b3bf4ffcd" in workflow
    assert "python -m pip install --no-deps -e external/QuantPlatformKit" in workflow
    assert "scripts/run_walk_forward_backtest.py" in workflow
    assert '"--list-profiles"' in workflow
    assert "--market-history" in workflow
    assert "--returns-output" in workflow
    assert "LIFECYCLE_PREFLIGHT_BUNDLE_ROOT" in workflow
    assert "Upload lifecycle preflight artifact" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "uses: QuantStrategyLab/QuantPlatformKit/.github/workflows/reusable-drift-check.yml@7f0367c3bcb470306f6613d76e61365b3bf4ffcd" in workflow
    assert "strategy_domain: us_equity" in workflow
    assert "caller_event_name: ${{ github.event_name }}" in workflow
    assert "caller_pr_head_repository: ${{ github.event.pull_request.head.repo.full_name || '' }}" in workflow
    assert "snapshot_repository: QuantStrategyLab/UsEquitySnapshotPipelines" in workflow
    assert "snapshot_checkout_path: external/UsEquitySnapshotPipelines" in workflow
    assert "ai_gateway_service_url: ${{ vars.AI_GATEWAY_SERVICE_URL }}" in workflow
    assert "lifecycle_preflight_artifact: lifecycle-preflight-${{ github.run_id }}" in workflow
    assert "codex_audit_service_url: ${{ secrets.CODEX_AUDIT_SERVICE_URL }}" in workflow
    assert "snapshot_repository_token: ${{ secrets.SNAPSHOT_REPOSITORY_TOKEN }}" in workflow
