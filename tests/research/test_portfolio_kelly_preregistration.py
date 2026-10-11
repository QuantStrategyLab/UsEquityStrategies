from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from us_equity_strategies.research import fractional_kelly_preregistration as single
from us_equity_strategies.research import portfolio_kelly_preregistration as pk

P = pk.PREREGISTRATIONS["rs06-kelly-portfolio-v1"]


def _dates(n: int) -> list[date]:
    out, d = [], P.window_start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _call(auth, **over):
    dates = over.pop("dates", None)
    if dates is None:
        dates = _dates(10)
        dates[-1] = P.window_end
    kw = {"plan": P.plan, "fractions": P.fractions, "methods": P.methods, "cost_bps_per_side": 5.0,
          "multipliers": P.multipliers, "assets": ("SOXL", "TQQQ"), "dates": dates,
          "report_context": {"manifest_sha256": P.data_manifest_sha256, "source": P.data_source,
                             "license": P.data_license, "signal_cutoff": P.signal_cutoff,
                             "execution": P.execution}}
    kw.update(over)
    return pk.authorize_portfolio_real_data(auth, **kw)


def _auth(**over):
    m = single.DataManifest(P.data_manifest_sha256, P.data_source, P.data_license, "XNYS",
                            P.window_start, P.window_end, 10)
    return pk.PortfolioRealDataAuthorization("rs06-kelly-portfolio-v1", dataclasses.replace(m, **over))


def test_frozen_parameters_match_rs04e() -> None:
    for key, cid in P.components:
        comp = single.PREREGISTRATIONS[cid]
        assert comp.strategy_key == key
        assert comp.data_manifest_sha256 == P.data_manifest_sha256
        assert (comp.window_start, comp.window_end) == (P.window_start, P.window_end)
        assert comp.plan == P.plan and comp.fractions == P.fractions
        assert comp.base_cost_bps_per_side == P.base_cost_bps_per_side
        assert comp.multipliers == P.multipliers and comp.execution == P.execution
    assert P.variant == single.VARIANT_NO_PLUGIN
    assert P.primary_method == pk.METHOD_QP and P.methods == (pk.METHOD_QP, pk.METHOD_CLIP_SCALE)
    assert P.data_manifest_sha256.startswith("b5a3dd40")
    assert "NO_BUDGET_INCREASE" in P.gates and "SOXL_252_SHADOW_SESSIONS_STILL_REQUIRED" in P.gates
    assert len(P.comparators) == len(set(P.comparators)) == 13


def test_authorizes_exact_match() -> None:
    assert _call(_auth()) is P


@pytest.mark.parametrize(("over", "code"), [
    ({"fractions": (0.25, 0.75)}, "PARAMETERS_DIFFER"),
    ({"methods": ("qp",)}, "PARAMETERS_DIFFER"),
    ({"cost_bps_per_side": 2.0}, "PARAMETERS_DIFFER"),
    ({"assets": ("TQQQ", "SOXL")}, "ASSETS_DIFFER"),
    ({"report_context": None}, "REPORT_CONTEXT_REQUIRED"),
    ({"dates": _dates(10)}, "DOES_NOT_MATCH_SERIES"),
])
def test_rejects_parameter_mismatch(over, code) -> None:
    with pytest.raises(ValueError, match=code):
        _call(_auth(), **over)


@pytest.mark.parametrize(("over", "code"), [
    ({"sha256": "0" * 64}, "DATA_MANIFEST_DIFFERS"),
    ({"license": "other"}, "DATA_MANIFEST_DIFFERS"),
    ({"end_date": date(2026, 8, 24)}, "DATA_WINDOW_DIFFERS"),
])
def test_rejects_manifest_mismatch(over, code) -> None:
    with pytest.raises(ValueError, match=code):
        _call(_auth(**over))


def test_rejects_missing_or_unknown_authorization() -> None:
    with pytest.raises(ValueError, match="REAL_DATA_REQUIRES_APPROVAL"):
        _call(None)
    with pytest.raises(ValueError, match="PREREGISTRATION_ID_INVALID"):
        _call(dataclasses.replace(_auth(), preregistration_id="rs06-other"))


def test_rejects_unconfirmed_component(monkeypatch) -> None:
    comps = dict(single.PREREGISTRATIONS)
    comps["rs04-kelly-tqqq-v1"] = dataclasses.replace(comps["rs04-kelly-tqqq-v1"], data_confirmed_variants=())
    monkeypatch.setattr(single, "PREREGISTRATIONS", comps)
    with pytest.raises(ValueError, match="COMPONENT_PREREGISTRATION_NOT_CONFIRMED:rs04-kelly-tqqq-v1"):
        _call(_auth())
