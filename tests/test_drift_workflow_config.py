from pathlib import Path


def test_drift_workflow_wires_snapshot_repo_and_lifecycle_env() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "drift-check.yml").read_text(encoding="utf-8")

    assert "preflight_backtests:" in workflow
    assert "needs: preflight_backtests" in workflow
    assert "scripts/run_walk_forward_backtest.py" in workflow
    assert '"--list-profiles"' in workflow
    assert '"data/lifecycle_store"' in workflow
    assert "uses: QuantStrategyLab/QuantPlatformKit/.github/workflows/reusable-drift-check.yml@335c7a22bc3f570bd5705427ccc40172eda6b289" in workflow
    assert "strategy_domain: us_equity" in workflow
    assert "caller_event_name: ${{ github.event_name }}" in workflow
    assert "caller_pr_head_repository: ${{ github.event.pull_request.head.repo.full_name || '' }}" in workflow
    assert "snapshot_repository: QuantStrategyLab/UsEquitySnapshotPipelines" in workflow
    assert "snapshot_checkout_path: external/UsEquitySnapshotPipelines" in workflow
    assert "ai_gateway_service_url: ${{ vars.AI_GATEWAY_SERVICE_URL }}" in workflow
    assert "codex_audit_service_url: ${{ secrets.CODEX_AUDIT_SERVICE_URL }}" in workflow
    assert "snapshot_repository_token: ${{ secrets.SNAPSHOT_REPOSITORY_TOKEN }}" in workflow
