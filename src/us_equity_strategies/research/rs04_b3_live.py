"""Full existing rules on each book's own equity; no core-only overrides.

Repository defaults are a code reference, not an attestation of effective live
configuration. Cloud execution additionally needs the archived effective config,
per-session external context, same-input parity records and raw/action evidence.
No broker, live registry or production configuration is read by this module.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from .kelly_core_return_generator import _filtered_kwargs

PROFILES = {"SOXL": "soxl_soxx_trend_income", "TQQQ": "tqqq_growth_income"}
# Intentionally unfilled: RS-08 resolves code equivalence but the platform-merged
# configuration and its hash method are not archived. A reviewed binding is needed;
# an input manifest cannot attest its own authority or silently select defaults.
EFFECTIVE_CONFIG_SHA256 = {"SOXL": None, "TQQQ": None}
EFFECTIVE_STRATEGY_SOURCE_SHA256 = {
    "SOXL": "4831c9ab84f2373579e2cf118e880e13f369cfdf14141db0bf06424508f1c4ef",
    "TQQQ": "90e8ded53a2e4389f6060d88cfd232046d90e6775dda47379e8d06415b2cbf42",
}
CONFIG_SOURCE = "us_equity_strategies.manifests.*_manifest.default_config"
EVIDENCE_SOURCE = "docs/research/rs08-live-research-parity-report.zh-CN.md sections 1-4, 6"


def default_config(profile: str) -> dict:
    from us_equity_strategies import manifests
    return deepcopy(getattr(manifests, PROFILES[profile]+"_manifest").default_config)


def config_sha256(config: Mapping) -> str:
    return hashlib.sha256(json.dumps(dict(config), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def binding_report(profile: str, config: Mapping | None = None) -> dict:
    from importlib import import_module
    source = Path(import_module("us_equity_strategies.strategies."+PROFILES[profile]).__file__)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    config = default_config(profile) if config is None else config
    sha = config_sha256(config)
    return {"profile": PROFILES[profile], "config_source_name": CONFIG_SOURCE,
            "evidence_source_name": EVIDENCE_SOURCE, "repo_default_sha256": config_sha256(default_config(profile)),
            "candidate_config_sha256": sha, "effective_config_sha256": EFFECTIVE_CONFIG_SHA256[profile],
            "effective_config_verified": EFFECTIVE_CONFIG_SHA256[profile] == sha,
            "repo_strategy_source_sha256": source_sha,
            "strategy_source_verified": EFFECTIVE_STRATEGY_SOURCE_SHA256[profile] == source_sha,
            "reason": "PLATFORM_MERGED_EFFECTIVE_CONFIG_AND_HASH_METHOD_NOT_ARCHIVED",
            "strategy_evidence_revision": "4a3943883cd6",
            "strategy_evidence_date": "2026-10-11",
            "current_live_readback_performed": False,
            "external_market_signals": "DOCUMENTED_UNCONFIGURED_2026_10_11_HISTORY_NOT_ARCHIVED",
            "signal_clock": "COMPLETED_CLOSE_T_MINUS_1_NEXT_CLOSE_FILL",
            "live_clock_parity": "UNVERIFIED_INTRADAY_VS_DAILY_CLOSE_DIFFERENCE",
            "caps": {"total": .97 if profile == "SOXL" else .98,
                     "single": .679 if profile == "SOXL" else None,
                     "cash": .03 if profile == "SOXL" else .02},
            "external_context_required": True, "income_layer_preserved": True}


class LiveRuleAdapter:
    """Callback for Session/ledger. Missing context is not an empty context."""

    def __init__(self, profile: str, config: Mapping):
        if profile not in PROFILES:
            raise ValueError("PROFILE_INVALID")
        self.profile, self.config = profile, deepcopy(dict(config))

    def __call__(self, history: Sequence, book: Mapping) -> dict[str, float]:
        if not history or history[-1].context is None:
            raise ValueError("LIVE_CONTEXT_HISTORY_MISSING")
        row, equity = history[-1], float(book["nav"])
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("LIVE_BOOK_INVALID")
        quantities = dict(book["quantity"])
        values = {s: q*row.prices[s] for s, q in quantities.items() if q}
        def translator(key, **kw):
            return key
        if self.profile == "SOXL":
            from us_equity_strategies.strategies.soxl_soxx_trend_income import build_rebalance_plan
            if row.live_indicators is None:
                raise ValueError("LIVE_INDICATOR_HISTORY_MISSING")
            account = {"available_cash": book["cash"], "market_values": values,
                       "quantities": quantities, "sellable_quantities": quantities,
                       "total_strategy_equity": equity, "cash_sweep_symbol": "BOXX",
                       "metadata": deepcopy(row.context)}
            kwargs = _filtered_kwargs(build_rebalance_plan, self.config,
                                      ("indicators", "account_state", "translator"))
            plan = build_rebalance_plan(deepcopy(row.live_indicators), account,
                                        translator=translator, **kwargs)
            targets = plan["targets"]
        else:
            import pandas as pd
            from quant_platform_kit.common.models import PortfolioSnapshot, Position
            from us_equity_strategies.strategies.tqqq_growth_income import build_rebalance_plan
            if len(history) < 200 or any("QQQ" not in r.signal_closes for r in history):
                raise ValueError("LIVE_SIGNAL_HISTORY_MISSING")
            # Forward split-price levels share one unit. The original function
            # uses ratios, slopes/signs and percentiles; constant rescaling cancels.
            frame = pd.DataFrame({"date": [r.session for r in history],
                                  "close": [r.signal_closes["QQQ"] for r in history]})
            snapshot = PortfolioSnapshot(as_of=datetime.combine(row.session, time(16)),
                total_equity=equity, buying_power=book["cash"], cash_balance=book["cash"],
                positions=tuple(Position(symbol=s, quantity=quantities[s], market_value=v)
                                for s, v in values.items()), metadata=deepcopy(row.context))
            kwargs = _filtered_kwargs(build_rebalance_plan, self.config,
                                      ("qqq_history", "snapshot", "signal_text_fn", "translator"))
            plan = build_rebalance_plan(frame, snapshot, signal_text_fn=lambda s, **kw: str(s),
                                        translator=translator, **kwargs)
            targets = plan["target_values"]
        weights = {s: float(v or 0.)/equity for s, v in targets.items()}
        if any(not math.isfinite(v) or v < 0 for v in weights.values()):
            raise ValueError("LIVE_TARGET_INVALID")
        # The runner rejects cap breaches; actual live risk gates reject rather
        # than clip. Do not rescale desired targets to manufacture parity.
        return weights


def compare_recorded_decisions(adapter: LiveRuleAdapter, records: Sequence[Mapping]) -> dict:
    """Records must contain the exact original decision-time inputs/book/config.

    This checks rule outputs, separately from intraday/daily execution parity.
    Only counts are exported; no dates, holdings, target tape or amounts.
    """
    mismatches = 0
    for r in records:
        actual, expected = adapter(r["history"], r["book"]), r["targets"]
        if any(not math.isclose(actual.get(s, 0.), expected.get(s, 0.), rel_tol=0., abs_tol=1e-9)
               for s in set(actual) | set(expected)):
            mismatches += 1
    return {"status": "UNAVAILABLE" if not records else "MISMATCH" if mismatches else "MATCH",
            "records": len(records), "mismatches": mismatches,
            "scope": "EXACT_RECORDED_INPUT_RULE_OUTPUT_ONLY"}
