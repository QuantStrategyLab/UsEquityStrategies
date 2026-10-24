"""Three-level offline previews using existing QP, compose and tail functions.

Only precomputed artificial moments/scenarios are evaluated. No evaluator,
fitter, backtest, broker, settings writer or new optimization algorithm.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass, replace
import hashlib
import json
import math

from quant_platform_kit.research_stats.security_netting import SecurityNettingSpec, compose_security_targets
from .auto_portfolio_account import adapt_ibkr_account_input, digest, utc
from .capital_adaptive_risk import LEVELS, preview_capital_adaptive_target
from .cvar_exposure_constraint import Action, DecisionBook
from .portfolio_kelly_walk_forward import constrained_kelly_qp
from .kelly_candidate_math import prepare_kelly_quadratic


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("finite numeric value required")
    return float(value)


def _hash(value):
    def encode(item):
        if is_dataclass(item):
            return asdict(item)
        raise TypeError("unsupported snapshot type")
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False, default=encode).encode()).hexdigest()
    except (TypeError, ValueError):
        return None


def generate_joint_candidate(moments, *, c, scope_ref, currency, cutoff_utc, math_contract=None):
    result = dict(status="CANDIDATE_UNAVAILABLE", gaps=[], execution_authorized=False,
                  optimizer="EXISTING_CONSTRAINED_KELLY_QP", fitted=False, cost_net_optimal=False,
                  candidate_contract_version="qsl.joint-candidate.v2",exact_log_optimal=False)
    gaps = result["gaps"]
    if not isinstance(moments, Mapping):
        gaps.append("MOMENTS_WIRE_INVALID")
        return result
    if moments.get("synthetic") is not True:
        gaps.append("SYNTHETIC_MOMENTS_REQUIRED")
    if moments.get("scope_ref") != scope_ref or moments.get("currency") != currency:
        gaps.append("MOMENTS_SCOPE_MISMATCH")
    if moments.get("unit") != "SHRUNKEN_EXCESS_SIMPLE_RETURN_PER_SESSION":
        gaps.append("MOMENT_UNIT_UNSUPPORTED")
    if any(not isinstance(moments.get(k),str) or not moments[k].strip() for k in ("source","estimator_version")):
        gaps.append("MOMENT_SOURCE_UNBOUND")
    try:
        if not utc(moments.get("training_end")) <= utc(moments.get("available_at")) <= utc(cutoff_utc):
            raise ValueError("future moments")
    except (TypeError, ValueError):
        gaps.append("MOMENTS_TIME_INVALID")
    ids = moments.get("strategy_ids")
    if (not isinstance(ids, list) or not 2 <= len(ids) <= 8
            or any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids)):
        gaps.append("STRATEGY_SET_UNSUPPORTED")
        return result
    versions = moments.get("strategy_versions", {})
    eligible = moments.get("eligible_strategies", {})
    if (not isinstance(versions,Mapping) or not isinstance(eligible,Mapping)
            or not isinstance(moments.get("eligibility_source"),str) or not moments["eligibility_source"].strip()
            or set(versions) != set(ids)
            or any(not isinstance(versions.get(s),str) or not versions[s] or eligible.get(s) != versions[s] for s in ids)):
        gaps.append("STRATEGY_ELIGIBILITY_UNBOUND")
    try:
        if c not in (.10, .25, .40) or isinstance(c, bool):
            raise ValueError("unknown research c")
        mu = [_number(x) for x in moments["mu_excess"]]
        covariance = [[_number(x) for x in row] for row in moments["covariance"]]
        n = len(ids)
        if len(mu) != n or len(covariance) != n or any(len(row) != n for row in covariance):
            raise ValueError("moment dimensions")
    except (TypeError, KeyError, ValueError, ArithmeticError):
        gaps.append("MOMENTS_OR_KELLY_CONTRACT_INVALID")
    if gaps:
        return result
    terms = prepare_kelly_quadratic(mu,covariance,math_contract=math_contract,moments=moments,
        scope_ref=scope_ref,currency=currency,cutoff_utc=cutoff_utc)
    result["math"] = terms
    gaps.extend(terms["gaps"])
    if gaps:
        return result
    weights = constrained_kelly_qp(mu, terms["quadratic_matrix"], c)
    if weights is None or any(not math.isfinite(x) or x < 0 for x in weights):
        gaps.append("QP_NO_CERTIFIED_CANDIDATE")
        return result
    result.update(status="CANDIDATE_COMPUTED", c=c, strategy_weights=dict(zip(ids, weights)),
        strategy_versions=dict(versions), correlation=[[
            covariance[i][j]/math.sqrt(covariance[i][i]*covariance[j][j]) for j in range(n)] for i in range(n)])
    return result


def _compose(candidate, composition, universe, *, account, bps):
    versions = candidate["strategy_versions"]
    if (composition.get("scope_ref") != account["scope_ref"] or composition.get("currency") != account["currency"]
            or composition.get("strategy_versions") != versions or not composition.get("source")
            or utc(composition.get("available_at")) > utc(account["cutoff_utc"])):
        raise ValueError("composition identity/time")
    rows = composition.get("weights")
    if not isinstance(rows, Mapping) or set(rows) != set(versions):
        raise ValueError("composition coverage")
    spec = SecurityNettingSpec(bps_per_side=bps, outer_cash_mode="separate")
    result = dict.fromkeys(universe, 0.)
    for strategy, fraction in candidate["strategy_weights"].items():
        if not set(rows[strategy]) <= set(universe):
            raise ValueError("unknown composition security")
        securities, _ = compose_security_targets(f=fraction, inner_weights=rows[strategy], spec=spec)
        for symbol, value in securities.items():
            result[symbol] += value
    # Each sleeve's outer-cash role overlaps: never sum those roles across sleeves.
    return result


def _p4_preview(*, definition, definition_hash, request_id, level, account, candidate,
                draft, gaps, es, input_hashes):
    requested = dict(definition_version=definition["definition_version"], request_id=request_id,
        scope_ref=account.get("scope_ref") or "UNBOUND_SCOPE", mode="simple", level=level,
        overrides={}, restore_system_recommendation=False, research_only=True, execution_authorized=False)
    supported = {} if draft is None else {
        "two_session_cvar95_budget": draft["tail_budget_ratio"], "kelly_fraction": candidate["c"],
        "strategy_ids": list(draft["strategy_weights"]), "strategy_weights": draft["strategy_weights"],
        "strategy_weight_mode": "explicit" if candidate.get("target_source") == "USER_EXPLICIT_TARGET" else "system_recommended",
    }
    comparison = {name: dict(recommended_value=supported.get(name), override_value=None,
        has_override=False, effective_value=None,
        limiting_sources=["SYNTHETIC_CALCULATION_ONLY"] if name in supported else ["EFFECTIVE_POLICY_FIELD_UNBOUND"],
        risk_direction="UNKNOWN_OR_MIXED", confirmation_required=False) for name in definition["parameters"]}
    if "kelly_fraction" in supported:
        comparison["kelly_fraction"]["limiting_sources"].append("QP_CURVATURE_PARAMETER_NOT_POST_SCALE_FRACTION")
    units = dict(g="natural_log_per_session", delta_g_vs_frozen_reference="natural_log_per_session",
        max_drawdown="positive_nav_loss_ratio", daily_cvar95_loss="daily_signed_loss_ratio",
        two_session_cvar95_loss="two_session_signed_loss_ratio", turnover_l1="sum_abs_net_trade_over_pretrade_nav")

    def unavailable_metrics(reason):
        return {key: dict(status="UNCOMPUTABLE", value=None, unit=unit, reason_codes=[reason]) for key,unit in units.items()}

    metrics = unavailable_metrics("NO_QUALIFIED_RETURN_PATH")
    if es is not None and draft is not None:
        metrics["two_session_cvar95_loss"] = dict(status="COMPUTED",value=es,
            unit=units["two_session_cvar95_loss"],reason_codes=[])
    preview = dict(contract_version="r-risk-01.p4-preview.v1", definition_version=definition["definition_version"],
        definition_sha256=definition_hash, request_id=request_id, scope_ref=requested["scope_ref"],
        mode="simple", level=level, requested_settings=requested, parameter_comparison=comparison,
        application_status="NOT_APPLIED",effective_parameters_sha256=None,effective_observed_at_utc=None,
        confirmation_state="NOT_REQUIRED",cooldown_until_utc=None,
        confirmation_binding=dict(account_budget_sha256=None,preview_sha256=None,previous_effective_sha256=None,
            requested_parameters_sha256=digest(requested),server_confirmation_ref=None,confirmed_at_utc=None,expires_at_utc=None),
        reason_codes=sorted(set(gaps+["SYNTHETIC_ONLY","NOT_APPLIED","NO_FINANCIAL_QUALIFICATION"])),
        evidence=dict(classification="SYNTHETIC" if account["synthetic"] else "DESIGN_ONLY",estimated=True,
            data_cutoff_utc=account.get("cutoff_utc"),start_session=None,end_session=None,currency=account.get("currency"),
            data_sha256=digest(input_hashes),model_sha256=digest(dict(moments=input_hashes["moments"],
                kelly_math_contract=input_hashes["kelly_math_contract"],
                candidate_preferences=input_hashes["candidate_preferences"])),cost_sha256=input_hashes["cost"],
            cash_contract_sha256=account.get("cash_contract_sha256"),frozen_reference_sha256=None,trial_set_sha256=None,
            comparable=False,limitation_codes=["ARTIFICIAL_INPUTS_ONLY","NO_REAL_ACCOUNT_POLICY","NO_ACTUAL_CONSUMER",
                "QUADRATIC_MOMENT_SURROGATE_NOT_EXACT_LOG_KELLY","C_IS_QP_CURVATURE_NOT_POST_SCALE_ALLOCATION"]),
        estimates=dict(system_recommended=metrics,with_overrides=unavailable_metrics("OVERRIDES_NOT_REQUESTED"),
            override_minus_recommended=unavailable_metrics("NO_COMPARABLE_OVERRIDE_PATH")),
        research_only=True,execution_authorized=False,live_ready=False)
    body = dict(preview)
    body["confirmation_binding"] = {key:value for key,value in preview["confirmation_binding"].items()
        if key not in ("preview_sha256","server_confirmation_ref","confirmed_at_utc","expires_at_utc")}
    preview["confirmation_binding"]["preview_sha256"] = digest(body)
    return preview


def build_three_level_previews(*, facts, account_contract, moments, composition,
                              risk_inputs, definition_bytes, request_id, math_contract=None, candidate_preferences=None):
    """Normalize a frozen account, generate candidates, and render unarmed P4 JSON.

    Settled cash funds gross purchases plus all fees. Simulated sale proceeds do
    not bridge funding. A failed funding/attribution check leaves the draft null;
    it is not a claim that no other feasible allocation exists.
    """
    account = adapt_ibkr_account_input(facts, account_contract)
    definition = json.loads(definition_bytes)
    definition_hash = hashlib.sha256(definition_bytes).hexdigest()
    hashes = dict(facts=account.get("facts_sha256"),cash=account.get("cash_contract_sha256"),
        moments=_hash(moments),composition=_hash(composition),risk=_hash(risk_inputs),cost=_hash(risk_inputs.get("bps_spec")),
        kelly_math_contract=_hash(math_contract),candidate_preferences=_hash(candidate_preferences))
    preference_gaps = []
    if candidate_preferences is not None:
        if (not isinstance(candidate_preferences,Mapping)
                or candidate_preferences.get("level") not in LEVELS
                or set(candidate_preferences) - {"level","c","strategy_weights","source_request_sha256"}):
            preference_gaps.append("CANDIDATE_PREFERENCE_INVALID")
    previews = {}
    for level in LEVELS:
        gaps = list(account["gaps"])+preference_gaps
        if any(value is None for value in hashes.values()):
            gaps.append("INPUT_BINDING_UNAVAILABLE")
        candidate = dict(status="CANDIDATE_NOT_RUN",gaps=[])
        draft, composed, constraint, es, funding = None, None, None, None, None
        if not gaps:
            c = definition["parameters"]["kelly_fraction"]["defaults_by_level"].get(level)
            preferences = candidate_preferences if candidate_preferences and candidate_preferences.get("level") == level else {}
            c = preferences.get("c",c)
            candidate = generate_joint_candidate(moments,c=c,scope_ref=account["scope_ref"],
                currency=account["currency"],cutoff_utc=account["cutoff_utc"],math_contract=math_contract)
            gaps.extend(candidate["gaps"])
            candidate["target_source"] = "SYSTEM_QP"
            candidate["optimizer_applies_to_final_weights"] = candidate["status"] == "CANDIDATE_COMPUTED"
            if not gaps and "strategy_weights" in preferences:
                try:
                    weights = preferences["strategy_weights"]
                    if not isinstance(weights,Mapping) or set(weights) != set(candidate["strategy_weights"]):
                        raise ValueError("explicit strategy coverage")
                    weights = {s:_number(value) for s,value in weights.items()}
                    if any(value < 0 for value in weights.values()) or math.fsum(weights.values()) > 1.:
                        raise ValueError("unfunded explicit target")
                    candidate["system_strategy_weights"] = dict(candidate["strategy_weights"])
                    candidate["strategy_weights"] = weights
                    candidate["target_source"] = "USER_EXPLICIT_TARGET"
                    candidate["optimizer_applies_to_final_weights"] = False
                    candidate["math"]["role"] = "REFERENCE_ONLY_FOR_USER_TARGET"
                except (TypeError,ValueError,ArithmeticError):
                    gaps.append("CANDIDATE_PREFERENCE_INVALID")
        if not gaps:
            try:
                scenarios = risk_inputs["scenario_set"]
                universe = scenarios.symbols
                if (scenarios.descriptor.scope != account["scope_ref"] or scenarios.descriptor.currency != account["currency"]
                        or utc(risk_inputs.get("scenario_available_at")) > utc(account["cutoff_utc"])):
                    raise ValueError("scenario scope/time")
                if not set(account["current_weights"]) <= set(universe):
                    raise ValueError("held security missing from scenarios")
                if scenarios.descriptor.benchmark != math_contract["cash_numeraire"]:
                    gaps.append("KELLY_SCENARIO_CASH_BENCHMARK_MISMATCH")
                else:
                    composed = _compose(candidate,composition,universe,account=account,bps=risk_inputs["bps_spec"].bps_by_symbol)
            except (TypeError,ValueError,KeyError,AttributeError):
                gaps.append("COMPOSITION_UNBOUND")
        if not gaps:
            try:
                curve = risk_inputs.get("curve")
                if curve is None:
                    gaps.append("CAPITAL_CURVE_UNBOUND")
                else:
                    args = {key:risk_inputs[key] for key in (
                        "presets","account_policy","approved_anchor","scenario_set","bps_spec","hard_policy",
                        "absolute_tail_loss_limit","previous_tail_budget_ratio","forced_target") if key in risk_inputs}
                    constraint = preview_capital_adaptive_target(level=level,
                        curve=replace(curve,protected_capital=account["protected_capital_basis"],currency=account["currency"]),
                        proposal=Action("TARGET_WEIGHTS",composed),
                        decision_book=DecisionBook({s:account["current_weights"].get(s,0.) for s in universe},
                            account["nav"],account["currency"],0.),**args)
                    decision = constraint["decision"]
                    if decision is None or decision.action is None or decision.evaluation is None:
                        gaps.append(constraint["status"])
                    else:
                        target = dict(decision.action.target_weights)
                        rows = decision.evaluation.scenarios
                        required = max(account["nav"]*row.G*(math.fsum(max(target[s]-row.pre_fill_weights[s],0.) for s in universe)+row.k) for row in rows)
                        funding = dict(max_gross_buy_plus_fees=required,spendable_cash=account["spendable_cash"],sell_proceeds_used=False)
                        if required > account["spendable_cash"]:
                            gaps.append("SETTLED_CASH_INSUFFICIENT_NO_SELL_BRIDGE")
                        if decision.status == "PROPOSAL_FEASIBLE":
                            scalar = 1.
                        else:
                            anchor = risk_inputs.get("approved_anchor")
                            scalar = decision.lambda_value if anchor and anchor.action.target_weights == dict.fromkeys(universe,0.) else None
                        if scalar is None:
                            gaps.append("STRATEGY_ATTRIBUTION_UNBOUND")
                        if not gaps:
                            es = decision.evaluation.es
                            draft = dict(level=level,strategy_weights={s:v*scalar for s,v in candidate["strategy_weights"].items()},
                                security_target_weights=target,pre_fee_target_cash_weight=1.-math.fsum(target.values()),
                                post_fee_cash_weight_by_scenario={row.scenario_id:row.cash_after_fee/(1.-row.k) for row in rows},
                                tail_budget_ratio=constraint["effective_tail_budget_ratio"],
                                capital_factor=constraint["capital_factor"],execution_authorized=False,settings_applied=False)
            except (TypeError,ValueError,KeyError,AttributeError,ArithmeticError):
                gaps.append("RISK_INPUT_CONTRACT_UNBOUND")
        p4 = _p4_preview(definition=definition,definition_hash=definition_hash,request_id=f"{request_id}:{level}",
            level=level,account=account,candidate=candidate,draft=draft,gaps=gaps,es=es,input_hashes=hashes)
        previews[level] = dict(status="READY_SYNTHETIC_PREVIEW" if draft else "PREVIEW_HAS_GAPS",gaps=sorted(set(gaps)),
            candidate=candidate,composed_proposal=composed,draft_settings=draft,funding=funding,
            constraint_status=constraint["status"] if constraint else None,p4_preview=p4,
            diagnostics={name:dict(status="UNCOMPUTABLE",value=None,reason="NO_CLOSED_TRADES" if name!="sharpe" else "NO_QUALIFIED_EXCESS_RETURN_SERIES") for name in ("sharpe","win_rate","payoff_ratio")})
    return dict(schema_version="qsl.auto-portfolio-offline-preview.v3",account_input=account,
        input_hashes=hashes,previews=previews,execution_authorized=False,settings_applied=False,
        account_policy_bound=False,financial_risk_certified=False)
