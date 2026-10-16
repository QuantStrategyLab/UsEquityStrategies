"""Opt-in offline replay acceptance and no-order boundary."""
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from quant_platform_kit.common import StrategyRiskStateStore

from us_equity_strategies.research.soxl_option_d_replay import (
    binding_from_frozen_bytes,
    replay_option_d,
)
from tests.test_soxl_option_d import binding, facts, loss_cluster


def test_disabled_is_exact_baseline_identity_and_does_not_touch_inputs():
    baseline={"existing":[1,2],"combined_scale":.75}
    class Explodes:
        def __iter__(self): raise AssertionError("disabled replay read input")
    assert replay_option_d(baseline,observations=Explodes()) is baseline


def test_enabled_replay_deterministic_only_new_risk_budget_changes():
    b=binding(); rows=loss_cluster()+[facts("2026-10-06",1700,1850),facts("2026-10-07",1850,1850),facts("2026-10-08",1850,1850)]
    baseline={"combined_scale":.75,"existing_holdings":{"SOXL":10}}
    kwargs=dict(option_d_research_replay_enabled=True,binding=b,observations=rows,
                requested_new_risk_usd={r.session:100 for r in rows})
    result=replay_option_d(baseline,**kwargs)
    assert result == replay_option_d(baseline,**kwargs)
    assert baseline == {"combined_scale":.75,"existing_holdings":{"SOXL":10}}
    assert result["combined_scale"] == .75 and result["existing_holdings"] == {"SOXL":10}
    sessions=result["option_d_replay"]["sessions"]
    assert [r["simulated_new_risk_budget_usd"] for r in sessions[2:]] == [0,0,50,100]
    assert result["no_order"] and not result["execution_authorized"]
    assert not result["live_ready"] and result["schwab_parked"]


def test_replay_expands_missing_calendar_sessions_and_halts():
    rows=loss_cluster()+[facts("2026-10-09",1700,3000)]
    result=replay_option_d({},option_d_research_replay_enabled=True,binding=binding(),observations=rows)
    sessions=result["option_d_replay"]["sessions"]
    assert [r["session"] for r in sessions] == ["2026-10-01","2026-10-02","2026-10-05","2026-10-06","2026-10-07","2026-10-08","2026-10-09"]
    assert [r["data_gap"] for r in sessions] == [False,False,False,True,True,True,False]
    assert sessions[-1]["transition"]["state"]["step"] == "HARD"
    assert all(r["simulated_new_risk_budget_usd"] == 0 for r in sessions[2:])


def test_restart_validates_entire_frozen_prefix_and_store(tmp_path):
    b=binding(); rows=loss_cluster()+[facts("2026-10-06",1700,1850)]
    full=replay_option_d({},option_d_research_replay_enabled=True,binding=b,observations=rows)
    from quant_platform_kit.common import StrategyRiskStateTransition
    store=StrategyRiskStateStore(local_dir=tmp_path)
    for row in full["option_d_replay"]["sessions"]:
        store.append(StrategyRiskStateTransition.from_dict(row["transition"]))
    loaded=store.load_chain(b.identity)
    assert replay_option_d({},option_d_research_replay_enabled=True,binding=b,observations=rows,existing_transitions=loaded) == full
    with pytest.raises(ValueError,match="frozen prefix"):
        replay_option_d({},option_d_research_replay_enabled=True,binding=b,
                        observations=[replace(rows[0],equity_end=1901),*rows[1:]],existing_transitions=loaded)


@pytest.mark.parametrize("mode", ["live","paper","broker","LIVE","shadow"])
def test_execution_modes_rejected(mode):
    with pytest.raises(ValueError,match="RESEARCH_ONLY"):
        replay_option_d({},option_d_research_replay_enabled=True,execution_mode=mode,binding=binding(),observations=[])


def test_broker_or_executor_ports_not_accepted():
    for name in ("broker","executor","order_client"):
        with pytest.raises(TypeError): replay_option_d({},**{name:object()})


@pytest.mark.parametrize("rows", [[facts("2026-10-01"),facts("2026-10-01")],
    [facts("2026-10-02"),facts("2026-10-01")],[facts("2026-10-03")]])
def test_bad_observation_order_duplicates_non_session_rejected(rows):
    with pytest.raises(ValueError):
        replay_option_d({},option_d_research_replay_enabled=True,binding=binding(),observations=rows)


def test_frozen_bytes_keep_three_identities_distinct():
    import dataclasses
    b=binding(); config=b'{"research_only":true}'
    identity=dataclasses.replace(b.identity,config_sha256=hashlib.sha256(config).hexdigest())
    made=binding_from_frozen_bytes(identity=identity,calendar=b.calendar,config_bytes=config,policy_bytes=b'policy',overlay_bytes=b'overlay')
    assert made.policy_sha256 == hashlib.sha256(b'policy').hexdigest()
    assert made.overlay_sha256 == hashlib.sha256(b'overlay').hexdigest()
    with pytest.raises(ValueError,match="config"):
        binding_from_frozen_bytes(identity=identity,calendar=b.calendar,config_bytes=b'changed',policy_bytes=b'policy',overlay_bytes=b'overlay')


def test_cli_disabled_byte_equivalence_and_optin(tmp_path):
    root=Path(__file__).resolve().parents[1]
    baseline=tmp_path/'baseline.json'; baseline.write_bytes(b'{ "existing": 1 }\n')
    cmd=[sys.executable,str(root/'scripts/run_soxl_option_d_replay.py'),'--baseline-report',str(baseline)]
    disabled=subprocess.run(cmd,cwd=root,capture_output=True)
    assert disabled.returncode == 0 and disabled.stdout == baseline.read_bytes()
    b=binding(); config=tmp_path/'config'; config.write_bytes(b'config')
    policy=tmp_path/'policy'; policy.write_bytes(b'policy')
    overlay=tmp_path/'overlay'; overlay.write_bytes(b'overlay')
    manifest=tmp_path/'input.json'
    manifest.write_text(json.dumps({"identity":{**b.identity.to_dict(),"config_sha256":hashlib.sha256(b'config').hexdigest()},
        "calendar_start":"2026-10-01","calendar_end":"2026-11-30",
        "observations":[__import__('dataclasses').asdict(r) for r in loss_cluster()]}))
    enabled=subprocess.run(cmd+['--option-d-research-replay-enabled','--input',str(manifest),
        '--config',str(config),'--policy',str(policy),'--overlay',str(overlay)],cwd=root,capture_output=True)
    assert enabled.returncode == 0, enabled.stderr
    report=json.loads(enabled.stdout)
    assert report['no_order'] and report['option_d_replay']['sessions'][-1]['transition']['state']['step'] == 'S0'


def test_explicit_through_session_retains_trailing_gaps():
    result=replay_option_d({},option_d_research_replay_enabled=True,binding=binding(),
        observations=loss_cluster(),through_session="2026-10-08")
    assert result["option_d_replay"]["sessions"][-1]["transition"]["state"]["step"] == "HARD"


def test_offline_replay_never_uses_network(monkeypatch):
    import socket
    b=binding()
    def denied(*args,**kwargs): raise AssertionError("offline replay attempted networking")
    monkeypatch.setattr(socket,"socket",denied)
    result=replay_option_d({},option_d_research_replay_enabled=True,binding=b,observations=loss_cluster())
    assert result["no_order"] and not result["execution_authorized"]


@pytest.mark.parametrize("value", [-1,float("nan"),float("inf"),True,"100"])
def test_invalid_new_risk_budget_rejected(value):
    with pytest.raises(ValueError):
        replay_option_d({},option_d_research_replay_enabled=True,binding=binding(),observations=loss_cluster(),
            requested_new_risk_usd={"2026-10-01":value})
