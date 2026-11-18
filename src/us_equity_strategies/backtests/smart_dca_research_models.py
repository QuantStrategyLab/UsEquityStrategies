from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import math
from os import PathLike
from pathlib import Path
from typing import Any

import pandas as pd


BITCOIN_GENESIS_DATE = pd.Timestamp("2009-01-03")
SMART_DCA_RESEARCH_ARTIFACT_SCHEMA_VERSION = "smart_dca_research_artifacts.v1"
SUPPORTED_DCA_CADENCES = frozenset({"weekly", "monthly", "quarterly"})


@dataclass(frozen=True)
class SmartDcaCandidate:
    name: str
    family: str
    rule_type: str
    signal_symbols: tuple[str, ...]
    min_history: int
    parameters: Mapping[str, float]


@dataclass(frozen=True)
class DcaResearchResult:
    name: str
    terminal_value: float
    cash: float
    shares: float
    invested: float
    contributions: float
    max_drawdown: float
    max_underwater_days: int
    money_weighted_return: float
    trade_count: int
    skipped_count: int
    deployment_rate: float
    relative_terminal_value_pct: float
    equity_curve: tuple[dict[str, object], ...]
    cash_flows: tuple[dict[str, object], ...]
    trades: tuple[dict[str, object], ...]
    skips: tuple[dict[str, object], ...]
    last_signal_metrics: Mapping[str, object]


@dataclass(frozen=True)
class DcaCandidateEvaluation:
    name: str
    passed: bool
    reasons: tuple[str, ...]
    relative_terminal_value_pct: float
    max_drawdown_delta_pct_points: float
    skipped_buy_ratio: float
    deployment_rate_delta_pct_points: float
    rank_score: float


IBIT_AHR999_PARAMETERS: dict[str, float] = {
    "ahr999_bottom_threshold": 0.45,
    "ahr999_accumulation_threshold": 0.80,
    "ahr999_dca_threshold": 1.20,
    "base_multiplier": 1.0,
    "ahr999_bottom_multiplier": 3.0,
    "ahr999_accumulation_multiplier": 2.25,
    "ahr999_dca_multiplier": 1.50,
    "ahr999_expensive_multiplier": 0.0,
}
IBIT_AHR999_MAYER_PARAMETERS: dict[str, float] = {
    **IBIT_AHR999_PARAMETERS,
    "mayer_deep_discount_threshold": 0.65,
    "mayer_discount_threshold": 0.80,
    "mayer_expensive_threshold": 2.40,
}
IBIT_AHR999_NO_SKIP_PARAMETERS: dict[str, float] = {
    **IBIT_AHR999_PARAMETERS,
    "ahr999_expensive_multiplier": 1.0,
}
IBIT_AHR999_MAYER_NO_SKIP_PARAMETERS: dict[str, float] = {
    **IBIT_AHR999_MAYER_PARAMETERS,
    "ahr999_expensive_multiplier": 1.0,
}
IBIT_AHR999_PERCENTILE_PARAMETERS: dict[str, float] = {
    "ahr999_bottom_percentile_threshold": 0.10,
    "ahr999_accumulation_percentile_threshold": 0.25,
    "ahr999_dca_percentile_threshold": 0.50,
    "ahr999_expensive_percentile_threshold": 0.80,
    "base_multiplier": 1.0,
    "ahr999_bottom_multiplier": 3.0,
    "ahr999_accumulation_multiplier": 2.25,
    "ahr999_dca_multiplier": 1.50,
    "ahr999_expensive_multiplier": 0.0,
}
IBIT_AHR999_GUARDED_PARAMETERS: dict[str, float] = {
    **IBIT_AHR999_PARAMETERS,
    "ahr999_high_percentile_threshold": 0.80,
    "ahr999_rising_slope_threshold": 0.0,
    "guarded_expensive_multiplier": 1.0,
}
NASDAQ_SP500_PRICE_PARAMETERS: dict[str, float] = {
    "mild_drawdown_threshold": 0.08,
    "deep_drawdown_threshold": 0.15,
    "severe_drawdown_threshold": 0.25,
    "mild_discount_gap": 0.05,
    "deep_discount_gap": 0.10,
    "expensive_gap": 0.12,
    "very_expensive_gap": 0.20,
    "shallow_drawdown_threshold": 0.03,
    "overbought_rsi": 70.0,
    "base_multiplier": 1.0,
    "mild_pullback_multiplier": 1.10,
    "deep_pullback_multiplier": 1.25,
    "severe_pullback_multiplier": 1.50,
    "expensive_multiplier": 0.75,
    "very_expensive_multiplier": 0.0,
}
NASDAQ_SP500_PRICE_NO_SKIP_PARAMETERS: dict[str, float] = {
    **NASDAQ_SP500_PRICE_PARAMETERS,
    "expensive_multiplier": 1.0,
    "very_expensive_multiplier": 1.0,
}
NASDAQ_SP500_VALUATION_GUARD_PARAMETERS: dict[str, float] = {
    "cape_expensive_percentile_threshold": 0.85,
    "base_multiplier": 1.0,
    "valuation_guard_multiplier": 0.75,
}
NASDAQ_SP500_VOL_BREADTH_STRESS_PARAMETERS: dict[str, float] = {
    "vix_stress_percentile_threshold": 0.80,
    "breadth_weak_threshold": 0.40,
    "base_multiplier": 1.0,
    "stress_pullback_multiplier": 1.25,
}
NASDAQ_SP500_CAPE_VIX_GUARD_PARAMETERS: dict[str, float] = {
    "cape_expensive_percentile_threshold": 0.85,
    "vix_stress_percentile_threshold": 0.80,
    "base_multiplier": 1.0,
    "valuation_guard_multiplier": 0.75,
    "vix_stress_multiplier": 1.25,
}


PRESET_CANDIDATES: dict[str, SmartDcaCandidate] = {
    "nasdaq_sp500_price_defensive": SmartDcaCandidate(
        name="nasdaq_sp500_price_defensive",
        family="nasdaq_sp500_price",
        rule_type="trend_drawdown",
        signal_symbols=("QQQ", "SPY"),
        min_history=252,
        parameters=NASDAQ_SP500_PRICE_PARAMETERS,
    ),
    "nasdaq_sp500_price_no_skip": SmartDcaCandidate(
        name="nasdaq_sp500_price_no_skip",
        family="nasdaq_sp500_price_variant",
        rule_type="trend_drawdown",
        signal_symbols=("QQQ", "SPY"),
        min_history=252,
        parameters=NASDAQ_SP500_PRICE_NO_SKIP_PARAMETERS,
    ),
    "nasdaq_sp500_precomputed_valuation_guard": SmartDcaCandidate(
        name="nasdaq_sp500_precomputed_valuation_guard",
        family="nasdaq_sp500_external_precomputed_context",
        rule_type="precomputed_nasdaq_sp500_valuation_guard",
        signal_symbols=("cape_percentile",),
        min_history=1,
        parameters=NASDAQ_SP500_VALUATION_GUARD_PARAMETERS,
    ),
    "nasdaq_sp500_precomputed_vol_breadth_stress": SmartDcaCandidate(
        name="nasdaq_sp500_precomputed_vol_breadth_stress",
        family="nasdaq_sp500_external_precomputed_context",
        rule_type="precomputed_nasdaq_sp500_vol_breadth_stress",
        signal_symbols=("vix_percentile", "breadth_above_sma200_pct"),
        min_history=1,
        parameters=NASDAQ_SP500_VOL_BREADTH_STRESS_PARAMETERS,
    ),
    "nasdaq_sp500_precomputed_cape_vix_guard": SmartDcaCandidate(
        name="nasdaq_sp500_precomputed_cape_vix_guard",
        family="nasdaq_sp500_external_precomputed_context",
        rule_type="precomputed_nasdaq_sp500_cape_vix_guard",
        signal_symbols=("cape_percentile", "vix_percentile"),
        min_history=1,
        parameters=NASDAQ_SP500_CAPE_VIX_GUARD_PARAMETERS,
    ),
    "ibit_btc_ahr999_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_cycle",
        family="ibit_btc_ahr999_price",
        rule_type="ahr999",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters=IBIT_AHR999_PARAMETERS,
    ),
    "ibit_btc_ahr999_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_mayer_cycle",
        family="ibit_btc_ahr999_mayer_price",
        rule_type="ahr999_mayer",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
    "ibit_btc_ahr999_mayer_no_skip_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_mayer_no_skip_cycle",
        family="ibit_btc_ahr999_mayer_price_variant",
        rule_type="ahr999_mayer",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters=IBIT_AHR999_MAYER_NO_SKIP_PARAMETERS,
    ),
    "ibit_btc_ahr999_sma_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_ahr999_sma_mayer_cycle",
        family="ibit_btc_ahr999_mayer_price_variant",
        rule_type="ahr999_sma_mayer",
        signal_symbols=("BTC-USD",),
        min_history=200,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_cycle",
        family="ibit_btc_ahr999_precomputed",
        rule_type="precomputed_ahr999",
        signal_symbols=("ahr999",),
        min_history=1,
        parameters=IBIT_AHR999_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_mayer_cycle",
        family="ibit_btc_ahr999_mayer_precomputed",
        rule_type="precomputed_ahr999_mayer",
        signal_symbols=("ahr999", "mayer_multiple"),
        min_history=1,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        family="ibit_btc_ahr999_mayer_precomputed_variant",
        rule_type="precomputed_ahr999_mayer",
        signal_symbols=("ahr999", "mayer_multiple"),
        min_history=1,
        parameters=IBIT_AHR999_MAYER_NO_SKIP_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_sma_mayer_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_sma_mayer_cycle",
        family="ibit_btc_ahr999_mayer_precomputed_variant",
        rule_type="precomputed_ahr999_sma_mayer",
        signal_symbols=("ahr999_sma", "mayer_multiple"),
        min_history=1,
        parameters=IBIT_AHR999_MAYER_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_percentile_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_percentile_cycle",
        family="ibit_btc_ahr999_precomputed_variant",
        rule_type="precomputed_ahr999_percentile",
        signal_symbols=("ahr999_365d_percentile",),
        min_history=1,
        parameters=IBIT_AHR999_PERCENTILE_PARAMETERS,
    ),
    "ibit_btc_precomputed_ahr999_guarded_cycle": SmartDcaCandidate(
        name="ibit_btc_precomputed_ahr999_guarded_cycle",
        family="ibit_btc_ahr999_precomputed_variant",
        rule_type="precomputed_ahr999_guarded",
        signal_symbols=("ahr999", "ahr999_365d_percentile", "ahr999_30d_slope"),
        min_history=1,
        parameters=IBIT_AHR999_GUARDED_PARAMETERS,
    ),
}

CANDIDATE_SETS: dict[str, tuple[str, ...]] = {
    "nasdaq_sp500_production_equivalent": ("nasdaq_sp500_price_no_skip",),
    "nasdaq_sp500_price": ("nasdaq_sp500_price_defensive",),
    "nasdaq_sp500_price_variants": (
        "nasdaq_sp500_price_defensive",
        "nasdaq_sp500_price_no_skip",
    ),
    "nasdaq_sp500_external_precomputed_variants": (
        "nasdaq_sp500_price_no_skip",
        "nasdaq_sp500_precomputed_valuation_guard",
        "nasdaq_sp500_precomputed_vol_breadth_stress",
    ),
    "nasdaq_sp500_cape_vix_precomputed_variants": (
        "nasdaq_sp500_precomputed_cape_vix_guard",
    ),
    "ibit_btc_ahr999_price": ("ibit_btc_ahr999_cycle",),
    "ibit_btc_ahr999_price_variants": (
        "ibit_btc_ahr999_cycle",
        "ibit_btc_ahr999_mayer_cycle",
        "ibit_btc_ahr999_mayer_no_skip_cycle",
        "ibit_btc_ahr999_sma_mayer_cycle",
    ),
    "ibit_btc_ahr999_mayer_price": ("ibit_btc_ahr999_mayer_cycle",),
    "ibit_btc_ahr999_mayer_price_variants": (
        "ibit_btc_ahr999_mayer_cycle",
        "ibit_btc_ahr999_mayer_no_skip_cycle",
        "ibit_btc_ahr999_sma_mayer_cycle",
    ),
    "ibit_btc_ahr999_precomputed": ("ibit_btc_precomputed_ahr999_cycle",),
    "ibit_btc_ahr999_precomputed_variants": (
        "ibit_btc_precomputed_ahr999_cycle",
        "ibit_btc_precomputed_ahr999_mayer_cycle",
        "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        "ibit_btc_precomputed_ahr999_sma_mayer_cycle",
    ),
    "ibit_btc_ahr999_helper_precomputed_variants": (
        "ibit_btc_precomputed_ahr999_cycle",
        "ibit_btc_precomputed_ahr999_percentile_cycle",
        "ibit_btc_precomputed_ahr999_guarded_cycle",
    ),
    "ibit_btc_ahr999_mayer_precomputed": ("ibit_btc_precomputed_ahr999_mayer_cycle",),
    "ibit_btc_ahr999_mayer_precomputed_variants": (
        "ibit_btc_precomputed_ahr999_mayer_cycle",
        "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle",
        "ibit_btc_precomputed_ahr999_sma_mayer_cycle",
    ),
    "all": tuple(PRESET_CANDIDATES),
}

PRODUCTION_EQUIVALENT_CANDIDATES: dict[str, str] = {
    "nasdaq_sp500_smart_dca": "nasdaq_sp500_price_no_skip",
    "ibit_smart_dca": "ibit_btc_precomputed_ahr999_cycle",
}
CANDIDATE_SIGNAL_CONSUMERS: dict[str, tuple[str, ...]] = {
    "nasdaq_sp500_price_defensive": (
        "research:nasdaq_sp500_price_proxy",
    ),
    "nasdaq_sp500_price_no_skip": (
        "research:nasdaq_sp500_price_proxy",
    ),
    "nasdaq_sp500_precomputed_valuation_guard": (
        "research:nasdaq_sp500_external_context_precomputed",
    ),
    "nasdaq_sp500_precomputed_vol_breadth_stress": (
        "research:nasdaq_sp500_external_context_precomputed",
    ),
    "nasdaq_sp500_precomputed_cape_vix_guard": (
        "research:nasdaq_sp500_cape_vix_external_context_precomputed",
    ),
    "ibit_btc_precomputed_ahr999_cycle": (
        "us_equity:ibit_smart_dca",
        "research:ibit_btc_ahr999_precomputed",
    ),
    "ibit_btc_precomputed_ahr999_mayer_cycle": (
        "research:ibit_btc_ahr999_mayer_precomputed",
    ),
    "ibit_btc_precomputed_ahr999_mayer_no_skip_cycle": (
        "research:ibit_btc_ahr999_mayer_precomputed",
    ),
    "ibit_btc_precomputed_ahr999_sma_mayer_cycle": (
        "research:ibit_btc_ahr999_mayer_precomputed_variants",
    ),
    "ibit_btc_precomputed_ahr999_percentile_cycle": (
        "research:ibit_btc_ahr999_helper_precomputed_variants",
    ),
    "ibit_btc_precomputed_ahr999_guarded_cycle": (
        "research:ibit_btc_ahr999_helper_precomputed_variants",
    ),
}


def available_candidate_names() -> tuple[str, ...]:
    """Return the small fixed preset universe used by this research helper."""

    return tuple(PRESET_CANDIDATES)


def production_equivalent_candidate_name(profile: str) -> str:
    """Return the frozen research candidate matching a production smart profile."""

    normalized = str(profile or "").strip()
    try:
        return PRODUCTION_EQUIVALENT_CANDIDATES[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown production smart DCA profile: {profile!r}") from exc


def candidate_signal_consumers(candidate_name: str) -> tuple[str, ...]:
    """Return signal bundle consumers compatible with a frozen research candidate."""

    normalized = str(candidate_name or "").strip()
    if normalized not in PRESET_CANDIDATES:
        raise ValueError(f"unknown smart DCA candidate: {candidate_name!r}")
    return CANDIDATE_SIGNAL_CONSUMERS.get(normalized, ())


def _candidate_production_equivalent_profile(name: str) -> str:
    profiles = [
        profile
        for profile, candidate_name in PRODUCTION_EQUIVALENT_CANDIDATES.items()
        if candidate_name == name
    ]
    return profiles[0] if profiles else ""


def _candidate_role(name: str) -> str:
    return (
        "production_equivalent"
        if _candidate_production_equivalent_profile(name)
        else "research_variant"
    )


def candidate_specs_to_rows(candidate_names: Iterable[str]) -> tuple[dict[str, object], ...]:
    """Return CSV-friendly rows describing frozen preset candidate parameters."""

    names = tuple(candidate_names)
    unknown = [name for name in names if name not in PRESET_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown smart DCA candidates: {unknown}")

    rows: list[dict[str, object]] = []
    for name in names:
        candidate = PRESET_CANDIDATES[name]
        for parameter_name, parameter_value in sorted(candidate.parameters.items()):
            rows.append(
                {
                    "name": candidate.name,
                    "candidate_role": _candidate_role(candidate.name),
                    "production_equivalent_profile": _candidate_production_equivalent_profile(
                        candidate.name
                    ),
                    "family": candidate.family,
                    "rule_type": candidate.rule_type,
                    "signal_symbols": ",".join(candidate.signal_symbols),
                    "min_history": candidate.min_history,
                    "parameter_name": parameter_name,
                    "parameter_value": parameter_value,
                }
            )
    return tuple(rows)


def candidate_summaries_to_rows(candidate_names: Iterable[str]) -> tuple[dict[str, object], ...]:
    """Return candidate-level anti-overfit audit rows for frozen presets."""

    names = tuple(candidate_names)
    unknown = [name for name in names if name not in PRESET_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown smart DCA candidates: {unknown}")

    rows: list[dict[str, object]] = []
    for name in names:
        candidate = PRESET_CANDIDATES[name]
        multiplier_values = _candidate_multiplier_values(candidate)
        rows.append(
            {
                "name": candidate.name,
                "candidate_role": _candidate_role(candidate.name),
                "production_equivalent_profile": _candidate_production_equivalent_profile(
                    candidate.name
                ),
                "family": candidate.family,
                "rule_type": candidate.rule_type,
                "signal_source_mode": _candidate_signal_source_mode(candidate),
                "compatible_signal_consumers": ",".join(
                    candidate_signal_consumers(candidate.name)
                ),
                "signal_symbols": ",".join(candidate.signal_symbols),
                "signal_symbol_count": len(candidate.signal_symbols),
                "min_history": candidate.min_history,
                "parameter_count": len(candidate.parameters),
                "threshold_parameter_count": sum(
                    1
                    for parameter_name in candidate.parameters
                    if not parameter_name.endswith("_multiplier")
                ),
                "multiplier_parameter_count": len(multiplier_values),
                "unique_multiplier_count": len(set(multiplier_values)),
                "min_multiplier": min(multiplier_values) if multiplier_values else float("nan"),
                "max_multiplier": max(multiplier_values) if multiplier_values else float("nan"),
                "zero_multiplier_allowed": any(value <= 0.0 for value in multiplier_values),
                "open_parameter_search": False,
                "candidate_definition_sha256": _candidate_definition_sha256(candidate.name),
            }
        )
    return tuple(rows)


def candidate_set_signal_source_modes(candidate_set: str | Iterable[str]) -> tuple[str, ...]:
    """Return the signal-source modes required by a frozen candidate set."""

    names = _resolve_candidate_names(candidate_set)
    return tuple(
        sorted(
            {
                _candidate_signal_source_mode(PRESET_CANDIDATES[name])
                for name in names
            }
        )
    )


def candidate_set_signal_consumers(candidate_set: str | Iterable[str]) -> tuple[str, ...]:
    """Return signal bundle consumers compatible with a frozen candidate set."""

    names = _resolve_candidate_names(candidate_set)
    return tuple(
        sorted(
            {
                consumer
                for name in names
                for consumer in candidate_signal_consumers(name)
            }
        )
    )


def _candidate_multiplier_values(candidate: SmartDcaCandidate) -> tuple[float, ...]:
    return tuple(
        float(value)
        for parameter_name, value in sorted(candidate.parameters.items())
        if parameter_name.endswith("_multiplier")
    )


def _candidate_signal_source_mode(candidate: SmartDcaCandidate) -> str:
    if candidate.rule_type.startswith("precomputed_nasdaq_sp500_"):
        return "external_precomputed_us_equity_context"
    if candidate.rule_type.startswith("precomputed_"):
        return "external_precomputed_derived_indicators"
    if "ahr999" in candidate.rule_type:
        return "internal_btc_price_derived_indicators"
    return "market_history_price_indicators"


def _resolve_candidate_names(candidate_set: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(candidate_set, str):
        if candidate_set in CANDIDATE_SETS:
            return CANDIDATE_SETS[candidate_set]
        if candidate_set in PRESET_CANDIDATES:
            return (candidate_set,)
        raise ValueError(f"unknown smart DCA candidate_set: {candidate_set}")

    names = tuple(candidate_set)
    unknown = [name for name in names if name not in PRESET_CANDIDATES]
    if unknown:
        raise ValueError(f"unknown smart DCA candidates: {unknown}")
    return names



def _candidate_definition_sha256(name: str) -> str:
    candidate = PRESET_CANDIDATES.get(name)
    if candidate is None:
        return ""
    payload = {
        "name": candidate.name,
        "family": candidate.family,
        "rule_type": candidate.rule_type,
        "signal_symbols": candidate.signal_symbols,
        "min_history": candidate.min_history,
        "parameters": dict(sorted(candidate.parameters.items())),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


