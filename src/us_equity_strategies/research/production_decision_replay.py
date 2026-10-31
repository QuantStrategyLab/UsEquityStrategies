"""Non-executable, evidence-bound replay of production decision builders.

This module replays frozen caller-provided snapshots; it does not infer fills,
reconstruct portfolios, or establish historical publication authenticity.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.common.strategy_contracts import StrategyContext
from quant_platform_kit.risk.gate.replay import replay_apply_risk_gate
from quant_platform_kit.risk.gate import enrich_decision_risk_diagnostics
from quant_platform_kit.risk.portfolio_diagnostics import extract_portfolio_risk_diagnostics
from quant_platform_kit.risk.contracts import RuntimeRiskLimits, SmallAccountRiskHoldPolicy
from us_equity_strategies.entrypoints import (
    _build_soxl_soxx_trend_income_decision,
    _build_tqqq_growth_income_decision,
)
from us_equity_strategies.manifests import soxl_soxx_trend_income_manifest, tqqq_growth_income_manifest

_BUILDERS = {
    "tqqq_growth_income": _build_tqqq_growth_income_decision,
    "soxl_soxx_trend_income": _build_soxl_soxx_trend_income_decision,
}
_REQUIRED_SOURCE_FILES = (
    "entrypoints/__init__.py", "entrypoints/_common.py", "strategies/tqqq_growth_income.py",
    "strategies/tqqq_dual_drive_core.py", "strategies/soxl_soxx_trend_income.py",
    "tqqq_daily_state.py", "market_regime_control_contract.py", "volatility_delever_retention.py",
)
_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()


def production_source_hashes() -> dict[str, str]:
    """Hash the actual builder and core source files used by this process."""
    return {name: hashlib.sha256((_SOURCE_ROOT / name).read_bytes()).hexdigest()
            for name in _REQUIRED_SOURCE_FILES}


def effective_config_sha256(profile: str, config: Mapping[str, Any]) -> str:
    manifest = {"tqqq_growth_income": tqqq_growth_income_manifest,
                "soxl_soxx_trend_income": soxl_soxx_trend_income_manifest}[profile]
    return canonical_sha256({**dict(manifest.default_config), **dict(config)})


def _clock(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("clock must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("clock must be timezone-aware")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.microsecond:
        raise ValueError("clock must have whole-second precision")
    return parsed


def _positive(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _verified(verifier, *args) -> bool:
    if verifier is None:
        return False
    try:
        return verifier(*args) is True
    except Exception:
        return False


@dataclass(frozen=True)
class ProductionDecisionReplayRequest:
    profile: str
    source_revision: str
    source_hashes: Mapping[str, str]
    config: Mapping[str, Any]
    config_sha256: str
    effective_config_sha256: str
    frozen_input: Mapping[str, Any]
    input_sha256: str
    decision_at: str
    evidence_kind: str
    publication_evidence: Mapping[str, Any] | None
    execution_evidence: Mapping[str, Any] | None


@dataclass(frozen=True)
class ProductionDecisionReplayResult:
    status: str
    gaps: tuple[str, ...]
    trace: Mapping[str, Any] | None
    executable: bool = field(default=False, init=False)
    historical_parity_established: bool = field(default=False, init=False)


def replay_production_decision(
    request: ProductionDecisionReplayRequest,
    *,
    publication_verifier: Callable[[Mapping[str, Any], str, datetime], bool] | None = None,
    source_verifier: Callable[[str, Mapping[str, str]], bool] | None = None,
) -> ProductionDecisionReplayResult:
    """Replay one frozen session through its production builder, never an order path.

    The trusted verifier must independently authenticate immutable publication
    evidence and bind it to the frozen input digest and decision clock.
    """
    gaps: list[str] = []
    if type(request) is not ProductionDecisionReplayRequest or request.profile not in _BUILDERS:
        return ProductionDecisionReplayResult("BLOCKED", ("UNSUPPORTED_PROFILE",), None)
    try:
        clock = _clock(request.decision_at)
        if not re.fullmatch(r"[0-9a-f]{40}", request.source_revision):
            raise ValueError("invalid source revision")
        if dict(request.source_hashes) != production_source_hashes():
            return ProductionDecisionReplayResult("BLOCKED", ("SOURCE_IDENTITY_MISMATCH",), None)
        if not _verified(source_verifier, request.source_revision, request.source_hashes):
            gaps.append("SOURCE_REVISION_UNVERIFIED")
        if canonical_sha256(request.config) != request.config_sha256:
            return ProductionDecisionReplayResult("BLOCKED", ("CONFIG_IDENTITY_MISMATCH",), None)
        if effective_config_sha256(request.profile, request.config) != request.effective_config_sha256:
            return ProductionDecisionReplayResult("BLOCKED", ("EFFECTIVE_CONFIG_IDENTITY_MISMATCH",), None)
        if canonical_sha256(request.frozen_input) != request.input_sha256:
            return ProductionDecisionReplayResult("BLOCKED", ("INPUT_IDENTITY_MISMATCH",), None)
        if request.evidence_kind not in {"synthetic_fixture", "historical"}:
            raise ValueError("invalid evidence kind")
        payload = request.frozen_input
        session = str(payload["session"])
        if datetime.fromisoformat(session).date() > clock.date():
            raise ValueError("future session")
        records = payload["benchmark_history"] if request.profile == "tqqq_growth_income" else []
        history = []
        history_days = []
        for row in records:
            day = datetime.fromisoformat(str(row["session"])).date()
            if day > datetime.fromisoformat(session).date():
                continue
            if _clock(row["available_at"]) > clock:
                raise ValueError("late benchmark observation")
            if not _positive(row["close"]):
                raise ValueError("invalid benchmark close")
            history.append({"close": row["close"]})
            history_days.append(day)
        if request.profile == "tqqq_growth_income" and (len(history) < 200 or sorted(set(history_days)) != history_days or history_days[-1].isoformat() != session):
            raise ValueError("missing or duplicate benchmark history")
        indicator_data = payload.get("derived_indicators", {})
        if request.profile == "soxl_soxx_trend_income" and _clock(payload["indicators_available_at"]) > clock:
            raise ValueError("late indicators")
        portfolio_data = payload["portfolio"]
        portfolio_time = _clock(portfolio_data["as_of"])
        if portfolio_time > clock:
            raise ValueError("portfolio unavailable at decision")
        if not _positive(portfolio_data["total_equity"]) or type(portfolio_data["buying_power"]) not in (int, float) or not math.isfinite(portfolio_data["buying_power"]) or portfolio_data["buying_power"] < 0:
            raise ValueError("invalid portfolio")
        if any(not isinstance(row, Mapping) or type(row.get("symbol")) is not str
               or set(row) - {"symbol", "quantity", "market_value", "average_cost", "currency", "account_id"}
               or type(row.get("quantity")) not in (int, float) or not math.isfinite(row["quantity"])
               or type(row.get("market_value")) not in (int, float) or not math.isfinite(row["market_value"])
               or (row.get("average_cost") is not None and not _positive(row["average_cost"]))
               or type(row.get("currency", "USD")) is not str or not row.get("currency", "USD")
               or (row.get("account_id") is not None and type(row["account_id"]) is not str)
               for row in portfolio_data["positions"]):
            raise ValueError("invalid position")
        if len({row["symbol"] for row in portfolio_data["positions"]}) != len(portfolio_data["positions"]):
            raise ValueError("duplicate position symbol")
        positions = tuple(Position(symbol=row["symbol"], quantity=float(row["quantity"]),
            market_value=float(row["market_value"]), average_cost=row.get("average_cost"),
            currency=row.get("currency", "USD"), account_id=row.get("account_id"))
            for row in portfolio_data["positions"])
        portfolio = PortfolioSnapshot(as_of=portfolio_time, total_equity=float(portfolio_data["total_equity"]),
            buying_power=float(portfolio_data["buying_power"]), cash_balance=portfolio_data.get("cash_balance"), positions=positions,
            metadata=dict(portfolio_data.get("metadata") or {}))
        market_data = {"benchmark_history": history} if request.profile == "tqqq_growth_income" else {"derived_indicators": indicator_data}
        capabilities = dict(payload.get("capabilities") or {})
        for key, model in (("runtime_risk_limits", RuntimeRiskLimits),
                           ("small_account_hold_policy", SmallAccountRiskHoldPolicy)):
            if key in capabilities:
                if not isinstance(capabilities[key], Mapping):
                    raise ValueError("invalid frozen typed risk input")
                capabilities[key] = model(**dict(capabilities[key]))
        ctx = StrategyContext(as_of=session, market_data=market_data, portfolio=portfolio,
                              runtime_config=dict(request.config), state=dict(payload.get("state") or {}),
                              capabilities=capabilities,
                              artifacts=dict(payload.get("artifacts") or {}))
        decision = _BUILDERS[request.profile](ctx)
        portfolio_diag = extract_portfolio_risk_diagnostics(portfolio)
        risk_input = enrich_decision_risk_diagnostics(decision,
            unrealized_pnl_pct=portfolio_diag.get("unrealized_pnl_pct"),
            consecutive_losses=portfolio_diag.get("consecutive_losses"))
        capabilities = ctx.capabilities
        risk_options = {key: capabilities[key] for key in ("capital_base", "capital_base_binding",
            "runtime_risk_limits", "small_account_hold_policy", "current_portfolio_weights", "cash_only_execution")
            if key in capabilities}
        gate = replay_apply_risk_gate(risk_input, evaluation_time=clock, max_single_weight=.20,
            portfolio_snapshot=portfolio, market_data=market_data,
            enforce_value_target_exposure=request.profile == "soxl_soxx_trend_income", **risk_options)
        if gate.executable is not False:
            raise ValueError("offline gate returned executable authority")
        targets = {position.symbol: position.target_value for position in decision.positions}
        if len(targets) != len(decision.positions):
            raise ValueError("duplicate decision symbols")
        prices = payload.get("instrument_prices")
        required = set(targets) | {position.symbol for position in positions}
        if not isinstance(prices, Mapping) or any(symbol not in prices or not _positive(prices[symbol]) for symbol in required):
            gaps.append("ACTUAL_INSTRUMENT_PRICES_MISSING")
        price_clock = payload.get("instrument_prices_available_at")
        if not isinstance(price_clock, Mapping) or any(symbol not in price_clock or _clock(price_clock[symbol]) > clock for symbol in required):
            gaps.append("INSTRUMENT_PRICE_AVAILABILITY_UNVERIFIED")
        if request.profile == "soxl_soxx_trend_income" and not all(key in capabilities for key in ("capital_base", "capital_base_binding")):
            gaps.append("RISK_GATE_INPUTS_INCOMPLETE")
        if request.evidence_kind == "historical":
            if (not isinstance(request.publication_evidence, Mapping)
                    or not _verified(publication_verifier, request.publication_evidence, request.input_sha256, clock)):
                gaps.append("INDEPENDENT_PUBLICATION_EVIDENCE_UNVERIFIED")
        execution = request.execution_evidence
        required_execution = {"quantity_rounding", "minimum_trade", "cash_settlement", "corporate_actions"}
        if not isinstance(execution, Mapping) or any(execution.get(key) != "complete" for key in required_execution):
            gaps.append("EXECUTION_ASSUMPTIONS_INCOMPLETE")
        # This boundary has no fills/holdings loop; declarations are not proof of parity.
        gaps.append("COMPLETE_SOURCE_DEPENDENCY_CLOSURE_UNVERIFIED")
        gaps.append("EXECUTION_SIMULATION_NOT_IMPLEMENTED")
        trace = {"profile": request.profile, "source_revision": request.source_revision,
                 "config_sha256": request.config_sha256, "effective_config_sha256": request.effective_config_sha256,
                 "input_sha256": request.input_sha256,
                 "decision_at": clock.isoformat(), "signal_session": session,
                 "decision_targets": targets, "decision_risk_flags": list(decision.risk_flags),
                 "offline_risk_decision": gate.decision_record,
                 "evidence_kind": request.evidence_kind, "executable": False, "execution_authorized": False,
                 "promotion_eligible": False, "live_ready": False}
        return ProductionDecisionReplayResult("BLOCKED", tuple(gaps), trace)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return ProductionDecisionReplayResult("BLOCKED", ("INVALID_OR_INCOMPLETE_INPUT",),
                                              {"error_type": type(exc).__name__, "execution_authorized": False})


def replay_production_decision_series(
    requests: tuple[ProductionDecisionReplayRequest, ...], *,
    publication_verifier: Callable[[Mapping[str, Any], str, datetime], bool] | None = None,
    source_verifier: Callable[[str, Mapping[str, str]], bool] | None = None,
) -> tuple[ProductionDecisionReplayResult, ...]:
    """Replay ordered frozen snapshots; no portfolio or execution continuity is inferred."""
    if not isinstance(requests, tuple) or not requests:
        raise ValueError("requests must be a non-empty tuple")
    clocks = tuple(_clock(request.decision_at) for request in requests)
    if tuple(sorted(set(clocks))) != clocks:
        raise ValueError("decision clocks must be unique and increasing")
    return tuple(replay_production_decision(request, publication_verifier=publication_verifier,
                                           source_verifier=source_verifier) for request in requests)
