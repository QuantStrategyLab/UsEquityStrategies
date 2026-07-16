"""Controls-disabled, zero-cost TQQQ research baseline (not production-equivalent)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from .tqqq_offline_input_contract import OfflineInput

PROFILE = "tqqq_growth_income_research_baseline_v1"
CONTRACT_VERSION = "tqqq_growth_income_research_baseline.v1"
PARAMETERS = {"sma_window": 200, "execution_timing": "next_observed_open", "commission_bps": 0, "slippage_bps": 0, "controls_disabled": True}

class BaselineContractError(ValueError):
    """Sanitized baseline contract failure."""

def _fail() -> None:
    raise BaselineContractError("invalid research baseline input") from None

def _finite(value: float) -> float:
    try: number = float(value)
    except (TypeError, ValueError, OverflowError): _fail()
    if not math.isfinite(number): _fail()
    return 0.0 if number == 0.0 else number

def _positive(value: float) -> float:
    number = _finite(value)
    if number <= 0:
        _fail()
    return number

def _json_bytes(value: Any) -> bytes:
    try: return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    except (TypeError, ValueError, OverflowError): _fail()

@dataclass(frozen=True)
class BaselineResult:
    profile: str
    contract_version: str
    input_digest: str
    parameter_digest: str
    equity_curve: tuple[dict[str, float | str], ...]
    daily_returns: tuple[dict[str, float | str], ...]
    execution_count: int
    controls_disabled: bool = True
    provider_completeness: str = "unverified"
    calendar_authority: str = "unverified"
    result_digest: str = ""

    def to_wire(self) -> bytes:
        payload={"profile":self.profile,"contract_version":self.contract_version,"input_digest":self.input_digest,"parameter_digest":self.parameter_digest,"equity_curve":self.equity_curve,"daily_returns":self.daily_returns,"execution_count":self.execution_count,"controls_disabled":self.controls_disabled,"provider_completeness":self.provider_completeness,"calendar_authority":self.calendar_authority}
        return _json_bytes(payload)

def run_controls_disabled_baseline(input_data: OfflineInput) -> BaselineResult:
    if not isinstance(input_data, OfflineInput) or len(input_data.rows) < 400:
        _fail()
    if len({(r.symbol, r.as_of) for r in input_data.rows}) != len(input_data.rows):
        _fail()
    dates=sorted({r.as_of for r in input_data.rows})
    by={(r.symbol,r.as_of):r for r in input_data.rows}
    if any(("QQQ",d) not in by or ("TQQQ",d) not in by for d in dates): _fail()
    if len(dates) < 201: _fail()
    parameter_digest=hashlib.sha256(_json_bytes(PARAMETERS)).hexdigest()
    cash=100000.0; qty=0.0; curve=[]; returns=[]; previous=None; executions=0
    for i in range(199,len(dates)-1):
        signal_date=dates[i]; execution_date=dates[i+1]
        qqq_closes=[_finite(by[("QQQ",d)].close) for d in dates[i-199:i+1]]
        sma=_finite(sum(qqq_closes)/200.0)
        signal_close=_finite(by[("QQQ",signal_date)].close)
        target=1.0 if signal_close >= sma else 0.0
        open_price=_positive(by[("TQQQ",execution_date)].open)
        prior_equity=_finite(cash + qty*open_price)
        target_qty=_finite((prior_equity*target)/open_price)
        cash=_finite(prior_equity-target_qty*open_price); qty=target_qty
        close_price=_finite(by[("TQQQ",execution_date)].close)
        equity=_finite(cash+qty*close_price)
        row={"date":execution_date,"equity":equity}; curve.append(row)
        if previous is not None: returns.append({"date":execution_date,"return":_finite(equity/previous-1.0)})
        previous=equity; executions+=1
    if not curve: _fail()
    payload={"profile":PROFILE,"contract_version":CONTRACT_VERSION,"input_digest":input_data.input_digest,"parameter_digest":parameter_digest,"equity_curve":curve,"daily_returns":returns,"execution_count":executions,"controls_disabled":True,"provider_completeness":"unverified","calendar_authority":"unverified"}
    result_digest=hashlib.sha256(_json_bytes(payload)).hexdigest()
    return BaselineResult(PROFILE,CONTRACT_VERSION,input_data.input_digest,parameter_digest,tuple(curve),tuple(returns),executions,result_digest=result_digest)
