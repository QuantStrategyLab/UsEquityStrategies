"""Synthetic entrypoint checks; frozen files are mutated only in tmp copies."""
from pathlib import Path
import shutil
import socket

import pytest

from us_equity_strategies.research import walk_forward_v2_entrypoint as entry
from us_equity_strategies.research.fractional_kelly_walk_forward_plan import WalkForwardPlan
from tests.research.test_r2_cv_leakage_v2 import _dates, _evaluate, stats


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SYNTHETIC_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _copy_frozen(tmp_path):
    check = entry.verify_m2_frozen_core(ROOT)
    for relative in check["verified_sha256"]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_original_protocol_and_core_match_without_full_m2_attestation():
    check = entry.verify_m2_frozen_core(ROOT)
    assert check["status"] == "MATCH"
    assert check["scope"] == "M2_PROTOCOL_AND_CORE_ONLY_SOURCE"
    assert check["m2_consumer_attested"] is False
    assert len(check["verified_sha256"]) == 11
    assert check["verified_sha256"][
        "src/us_equity_strategies/research/fractional_kelly_walk_forward.py"
    ] == "7e5892d59fd9c1243a5433b4c87b616d0805ed14130d24d65850bbfc8734ec74"


@pytest.mark.parametrize("target", [
    entry.PROTOCOL_JSON, entry.PROTOCOL_MD,
    "src/us_equity_strategies/research/partial_rebalance.py",
    "src/us_equity_strategies/research/fractional_kelly_walk_forward.py",
])
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_missing_or_changed_frozen_bytes_fail_closed(tmp_path, target, mutation):
    root = _copy_frozen(tmp_path)
    path = root / target
    if mutation == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"\n# artificial tamper\n")
    with pytest.raises(ValueError, match="M2_FROZEN_(FILE_UNAVAILABLE|HASH_MISMATCH)"):
        entry.verify_m2_frozen_core(root)


def test_hash_mismatch_prevents_evaluator_dispatch(tmp_path, monkeypatch):
    root = _copy_frozen(tmp_path)
    (root / "src/us_equity_strategies/income_layer.py").write_bytes(b"tampered")
    monkeypatch.setattr(entry, "_repository_root", lambda: root)
    from us_equity_strategies.research import fractional_kelly_walk_forward_v2 as single
    monkeypatch.setattr(single, "evaluate_fractional_kelly_walk_forward",
                        lambda **kwargs: pytest.fail("must reject before evaluation"))
    with pytest.raises(ValueError, match="M2_FROZEN_HASH_MISMATCH"):
        entry.evaluate_walk_forward_v2(evaluator_version="fractional_kelly_walk_forward_v2")


@pytest.mark.parametrize("version", ["v1", "v2", "unknown", "", None])
def test_explicit_known_version_required(version):
    with pytest.raises(ValueError, match="EVALUATOR_VERSION_UNSUPPORTED"):
        entry.evaluate_walk_forward_v2(evaluator_version=version)


@pytest.mark.parametrize("version", ["fractional_kelly_walk_forward_v2", "portfolio_kelly_walk_forward_v2"])
@pytest.mark.parametrize("forbidden", [{"synthetic": False}, {"real_data": object()}])
def test_real_data_permission_does_not_transfer(version, forbidden):
    with pytest.raises(ValueError, match="NEED_USER_V2_REAL_DATA_PROTOCOL_NOT_REGISTERED"):
        entry.evaluate_walk_forward_v2(evaluator_version=version, **forbidden)


@pytest.mark.parametrize("kind", ["single", "portfolio"])
@pytest.mark.usefixtures("stats")
def test_dispatch_preserves_02b_result_and_adds_scoped_hash_evidence(kind):
    days = _dates(55)
    plan = WalkForwardPlan(40, 5, 3, embargo_sessions=2)
    direct = _evaluate(kind, days, plan)
    assert direct["status"] == "COMPUTED", "required research_stats overlay missing"
    n = len(days)
    rows = list(zip(days, [.002 + .001 * (i % 3) for i in range(n)]))
    common = dict(plan=plan, cost_bps_per_side=5., synthetic=True,
                  cash_returns=list(zip(days, [0.0001] * n)))
    if kind == "single":
        common.update(strategy_returns=rows, benchmark_returns=rows, strategy_turnover=[.01] * n,
                      r8_returns=rows, r8_turnover=[.01] * n)
    else:
        common.update(asset_returns=[("A", rows), ("B", rows)],
                      benchmark_returns=[("IA", rows), ("IB", rows)],
                      asset_turnover={"A": [.01] * n, "B": [.01] * n})
    result = entry.evaluate_walk_forward_v2(evaluator_version=direct["evaluator_version"], **common)
    assert result.pop("entrypoint_version") == entry.ENTRYPOINT_VERSION
    check = result.pop("frozen_hash_check")
    assert check == entry.verify_m2_frozen_core(ROOT)
    assert result == direct
    assert result["no_order"] is True and result["live_ready"] is False
    assert result["promotion_eligible"] is False and result["budget_effect"] == "NONE"


def test_portfolio_actual_netting_stays_unsupported():
    with pytest.raises(ValueError, match="V2_NETTING_NOT_SUPPORTED"):
        entry.evaluate_walk_forward_v2(evaluator_version="portfolio_kelly_walk_forward_v2",
                                       asset_returns=[], cash_returns=[], benchmark_returns=[],
                                       plan=WalkForwardPlan(40, 5, 3), cost_bps_per_side=5.,
                                       netting_candidate={})
