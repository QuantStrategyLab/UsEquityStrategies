import hashlib
import json
import math
import pytest
from us_equity_strategies.research.tqqq_baseline_canonical_boundary import (
    BaselineBoundaryError, EquityPoint, ReturnPoint, BaselineResult,
)

def result():
 e=(EquityPoint("2026-01-02",100000,100000,0,50), EquityPoint("2026-01-03",100100,0,2000,50.05))
 r=(ReturnPoint("2026-01-03",0.001),)
 return BaselineResult("tqqq_growth_income_research_baseline_v1","v1","a"*64,"b"*64,e,r,2,1)

def test_zero_normalization_and_current_wire_digest():
 p=EquityPoint("2026-01-02",-0.0,0,0,-0.0)
 assert p.equity == 0.0 and math.copysign(1,p.equity)==1.0
 x=result(); assert x.result_digest == hashlib.sha256(x.to_wire()).hexdigest(); assert BaselineResult.from_wire(x.to_wire()) == x

def test_wire_rejects_wrong_shapes_and_scalars():
 x=result(); wire=json.loads(x.to_wire())
 for key in ("profile", "input_digest", "parameter_digest"):
  bad=dict(wire); bad[key]=1
  with pytest.raises(BaselineBoundaryError): BaselineResult.from_wire(bad)
 bad=dict(wire); bad["equity_curve"]={}
 with pytest.raises(BaselineBoundaryError): BaselineResult.from_wire(bad)
 bad=dict(wire); bad["evaluation_count"]=True
 with pytest.raises(BaselineBoundaryError): BaselineResult.from_wire(bad)

def test_nan_inf_bool_and_mutable_state_fail_closed():
 with pytest.raises(BaselineBoundaryError): EquityPoint("2026-01-02",float("nan"),1,0,1)
 with pytest.raises(BaselineBoundaryError): ReturnPoint("2026-01-03",True)
 x=result()
 with pytest.raises(BaselineBoundaryError): BaselineResult(x.profile,x.contract_version,x.input_digest,x.parameter_digest,list(x.equity_curve),x.daily_returns,2,1)
 object.__setattr__(x,"equity_curve",[{"date":"bad"}])
 with pytest.raises(BaselineBoundaryError): x.to_wire()

def test_safe_integer_bounds_for_numeric_and_counts():
 from us_equity_strategies.research.tqqq_baseline_canonical_boundary import MAX_SAFE_JSON_INTEGER
 assert EquityPoint("2026-01-02",MAX_SAFE_JSON_INTEGER,0,0,1).equity == float(MAX_SAFE_JSON_INTEGER)
 with pytest.raises(BaselineBoundaryError): EquityPoint("2026-01-02",MAX_SAFE_JSON_INTEGER+2,0,0,1)
 x=result(); wire=json.loads(x.to_wire()); wire["evaluation_count"]=MAX_SAFE_JSON_INTEGER+1
 with pytest.raises(BaselineBoundaryError): BaselineResult.from_wire(wire)
