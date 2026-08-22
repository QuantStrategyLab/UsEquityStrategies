from __future__ import annotations

from us_equity_strategies.daily_combo_evidence import consume_daily_combo_evidence


def _record() -> dict[str, object]:
    return {
        "combo_candidate_id": "combo-2026-08-23",
        "combo_revision": "r1",
        "components": [
            {
                "candidate_id": "TQQQ_P3",
                "evidence_digest": "a" * 64,
                "input_digest": "b" * 64,
                "evidence_valid": True,
                "research_eligibility_status": "ELIGIBLE",
            },
            {
                "candidate_id": "SOXL_P3",
                "evidence_digest": "c" * 64,
                "input_digest": "d" * 64,
                "evidence_valid": True,
                "research_eligibility_status": "READY_REPORT_ONLY",
            },
        ],
        "target_weights": {"TQQQ": 0.3, "SOXL": 0.1, "BOXX": 0.6},
        "asset_risk_specs": {
            "TQQQ": {"symbol": "TQQQ", "effective_exposure_factor": 3, "underlying": "NASDAQ100"},
            "SOXL": {"symbol": "SOXL", "effective_exposure_factor": 3, "underlying": "SEMIS"},
            "BOXX": {"symbol": "BOXX", "effective_exposure_factor": 1, "underlying": "USD", "is_cash": True},
        },
        "risk_policy": {
            "cash_symbol": "BOXX",
            "max_effective_risk_exposure": 2,
            "max_symbol_weights": {"TQQQ": 0.4, "SOXL": 0.2},
            "max_underlying_effective_exposure": {"NASDAQ100": 1.2, "SEMIS": 0.8},
        },
    }


def test_daily_consumer_preserves_component_digests_and_no_order_gates() -> None:
    result = consume_daily_combo_evidence(_record())
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["consumer"] == "daily_combo_evidence.v1"
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
    assert result["component_refs"][0]["evidence_digest"] == "a" * 64
    assert result["component_refs"][1]["input_digest"] == "d" * 64
    assert len(result["evidence_digest"]) == 64


def test_missing_component_or_policy_parks_without_retry_signal() -> None:
    record = _record()
    del record["components"]
    result = consume_daily_combo_evidence(record)
    assert result["status"] == "PARKED"
    assert result["reason_codes"] == ("DAILY_RECORD_INVALID",)
    assert result["execution_authorized"] is False
    assert result["promotion_authorized"] is False
