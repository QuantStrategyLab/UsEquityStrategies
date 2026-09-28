from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from us_equity_strategies.research.batch_a_dataset import (
    SCHEMA,
    BatchADatasetError,
    extract_adjusted_close_simple_returns,
    load_price_snapshot_v2,
)
from us_equity_strategies.research.batch_a_member_pack import (
    CASH_RETURN_POLICY,
    SCHEMA_VERSION as PACK_SCHEMA,
    BatchAMemberPackError,
    materialize_batch_a_member_pack,
    validate_batch_a_member_pack,
)
from us_equity_strategies.research.c3_batch_a_existing_member_baseline import (
    evaluate_batch_a_existing_member_baselines,
)


def _dates(count: int, start: str = "2024-01-02") -> list[str]:
    current = date.fromisoformat(start)
    out: list[str] = []
    while len(out) < count:
        if current.weekday() < 5:
            out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def _csv_bytes(symbols: tuple[str, ...], dates: list[str], *, base: float = 100.0) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for index, as_of in enumerate(dates):
        for offset, symbol in enumerate(symbols):
            close = base + index + offset * 10
            open_ = close - 0.5
            high = close + 1.0
            low = close - 1.0
            lines.append(
                ",".join(
                    (
                        symbol,
                        as_of,
                        format(open_, ".17g"),
                        format(high, ".17g"),
                        format(low, ".17g"),
                        format(close, ".17g"),
                        "1000",
                    )
                )
            )
    # canonical order is as_of then symbol
    body = lines[1:]
    body.sort(key=lambda line: (line.split(",")[1], line.split(",")[0]))
    return ("\n".join([lines[0], *body]) + "\n").encode()


def _write_dataset(
    root: Path,
    *,
    dataset_id: str,
    symbols: tuple[str, ...],
    dates: list[str],
    generation: str = "1234567890",
    schema: str = SCHEMA,
    mutate_bytes: bytes | None = None,
) -> Path:
    dataset_dir = root / dataset_id.replace("/", "_")
    dataset_dir.mkdir(parents=True)
    artifact = mutate_bytes if mutate_bytes is not None else _csv_bytes(symbols, dates)
    artifact_path = dataset_dir / "prices.csv"
    artifact_path.write_bytes(artifact)
    manifest = {
        "schema": schema,
        "research_only": True,
        "dataset_id": dataset_id,
        "provider": "alpaca",
        "feed": "sip",
        "price_field": "adjusted_close",
        "adjustment": "all",
        "calendar": "XNYS",
        "timezone": "America/New_York",
        "license_retention": "research_private_gcs_only",
        "code_version": "test-fixture",
        "source_revision": "fixture",
        "retrieved_at": "2026-09-22T00:00:00Z",
        "symbols": list(symbols),
        "request": {"start": dates[0], "end_exclusive": "2099-01-01"},
        "gcs": {
            "bucket": "qsl-research-evidence-831478360303",
            "object": f"research/v2/input/{dataset_id}/prices.csv",
            "generation": generation,
            "bytes": len(artifact),
            "sha256": hashlib.sha256(artifact).hexdigest(),
        },
        "counts": {symbol: len(dates) for symbol in symbols},
        "coverage": {symbol: {"start": dates[0], "end": dates[-1]} for symbol in symbols},
    }
    (dataset_dir / "prices.csv.manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    (dataset_dir / "object_identity.json").write_text(
        json.dumps({"generation": generation}, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return dataset_dir


def test_load_price_snapshot_v2_accepts_aligned_fixture(tmp_path: Path) -> None:
    dates = _dates(5)
    dataset = _write_dataset(tmp_path, dataset_id="demo-soxl", symbols=("SOXX", "SOXL"), dates=dates)
    snapshot = load_price_snapshot_v2(dataset)
    assert snapshot.dataset_id == "demo-soxl"
    assert len(snapshot.rows) == 10
    assert snapshot.manifest["schema"] == SCHEMA


def test_rejects_v1_schema(tmp_path: Path) -> None:
    dates = _dates(3)
    dataset = _write_dataset(
        tmp_path,
        dataset_id="demo-v1",
        symbols=("SOXX", "SOXL"),
        dates=dates,
        schema="qsl.research.price_snapshot.v1",
    )
    with pytest.raises(BatchADatasetError, match="PRICE_SNAPSHOT_V1_REJECTED"):
        load_price_snapshot_v2(dataset)


def test_rejects_generation_mismatch(tmp_path: Path) -> None:
    dates = _dates(3)
    dataset = _write_dataset(
        tmp_path, dataset_id="demo-gen", symbols=("SOXX", "SOXL"), dates=dates, generation="1"
    )
    (dataset / "object_identity.json").write_text(
        json.dumps({"generation": "999"}, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(BatchADatasetError, match="GCS_GENERATION_MISMATCH"):
        load_price_snapshot_v2(dataset)


def test_rejects_content_mismatch(tmp_path: Path) -> None:
    dates = _dates(3)
    dataset = _write_dataset(tmp_path, dataset_id="demo-bytes", symbols=("SOXX", "SOXL"), dates=dates)
    manifest_path = dataset / "prices.csv.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gcs"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(BatchADatasetError, match="GCS_CONTENT_MISMATCH"):
        load_price_snapshot_v2(dataset)


def test_rejects_object_root_mismatch(tmp_path: Path) -> None:
    dates = _dates(3)
    dataset = _write_dataset(tmp_path, dataset_id="demo-root", symbols=("SOXX", "SOXL"), dates=dates)
    manifest_path = dataset / "prices.csv.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gcs"]["object"] = "research/v2/input/other/prices.csv"
    manifest_path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(BatchADatasetError, match="GCS_OBJECT_ROOT_MISMATCH"):
        load_price_snapshot_v2(dataset)


def test_rejects_incomplete_symbol_dates(tmp_path: Path) -> None:
    dates = _dates(4)
    artifact = _csv_bytes(("SOXX", "SOXL"), dates)
    # drop last SOXL row
    lines = artifact.decode().splitlines()
    kept = [lines[0]] + [line for line in lines[1:] if not line.startswith("SOXL," + dates[-1])]
    broken = ("\n".join(kept) + "\n").encode()
    dataset = _write_dataset(
        tmp_path,
        dataset_id="demo-incomplete",
        symbols=("SOXX", "SOXL"),
        dates=dates,
        mutate_bytes=broken,
    )
    # rewrite counts to match broken file length pretence but keep both symbols declared
    with pytest.raises(BatchADatasetError, match="DATES_INCOMPLETE|COUNTS_MISMATCH|ARTIFACT_CANONICAL"):
        load_price_snapshot_v2(dataset)


def test_member_pack_materialize_and_digest(tmp_path: Path) -> None:
    dates = _dates(210)
    soxl = load_price_snapshot_v2(
        _write_dataset(tmp_path, dataset_id="batch-a-soxl", symbols=("SOXX", "SOXL"), dates=dates)
    )
    tqqq = load_price_snapshot_v2(
        _write_dataset(tmp_path, dataset_id="batch-a-tqqq", symbols=("QQQ", "TQQQ"), dates=dates)
    )
    pack = materialize_batch_a_member_pack(soxl_snapshot=soxl, tqqq_snapshot=tqqq)
    assert pack["schema_version"] == PACK_SCHEMA
    assert pack["cash_return_policy"] == CASH_RETURN_POLICY
    assert pack["pack_digest"]
    validated = validate_batch_a_member_pack(
        {
            **{key: value for key, value in pack.items() if key != "members"},
            "members": [dict(member) for member in pack["members"]],
        }
    )
    assert validated["pack_digest"] == pack["pack_digest"]
    cash = next(member for member in pack["members"] if member["member_id"] == "cash_sleeve")
    assert all(abs(float(item)) <= 1e-15 for item in cash["returns"])
    # strategy evaluation excludes SMA warmup — not raw bar buy/hold length
    assert len(cash["dates"]) < len(dates)
    assert len(cash["dates"]) == soxl.manifest["counts"]["SOXL"] - 200


def test_member_pack_rejects_v1_and_digest_mismatch() -> None:
    with pytest.raises(BatchAMemberPackError, match="FROZEN_MEMBER_PACK_V1_REJECTED"):
        validate_batch_a_member_pack(
            {
                "schema_version": "qsl.c3-batch-a-frozen-member-pack.v1",
                "research_only": True,
                "execution_authorized": False,
                "cash_return_policy": "ASSUMED_ZERO_CASH_SLEEVE",
                "members": [],
            }
        )
    member = {
        "member_id": "cash_sleeve",
        "evidence_digest": "a" * 64,
        "input_digest": "b" * 64,
        "dates": ["2026-01-02", "2026-01-05"],
        "returns": [0.0, 0.0],
        "as_of": "2026-01-05",
        "quote_currency": "USD",
        "capital_basis_digest": "c" * 64,
        "cost_model_digest": "d" * 64,
        "risk_policy_digest": "e" * 64,
        "data_scope_digest": "f" * 64,
    }
    soxl = dict(member, member_id="soxl_core", returns=[0.01, -0.02])
    tqqq = dict(member, member_id="tqqq_core", returns=[0.02, -0.01])
    with pytest.raises(BatchAMemberPackError, match="PACK_DIGEST_MISMATCH"):
        validate_batch_a_member_pack(
            {
                "schema_version": PACK_SCHEMA,
                "research_only": True,
                "execution_authorized": False,
                "cash_return_policy": CASH_RETURN_POLICY,
                "members": [member, soxl, tqqq],
                "pack_digest": "0" * 64,
            }
        )


def test_member_pack_rejects_duplicate_members() -> None:
    member = {
        "member_id": "cash_sleeve",
        "evidence_digest": "a" * 64,
        "input_digest": "b" * 64,
        "dates": ["2026-01-02", "2026-01-05"],
        "returns": [0.0, 0.0],
        "as_of": "2026-01-05",
        "quote_currency": "USD",
        "capital_basis_digest": "c" * 64,
        "cost_model_digest": "d" * 64,
        "risk_policy_digest": "e" * 64,
        "data_scope_digest": "f" * 64,
    }
    pack = {
        "schema_version": PACK_SCHEMA,
        "research_only": True,
        "execution_authorized": False,
        "cash_return_policy": CASH_RETURN_POLICY,
        "members": [member, dict(member), dict(member)],
        "pack_digest": "0" * 64,
    }
    with pytest.raises(BatchAMemberPackError, match="DUPLICATE_MEMBER_ID"):
        validate_batch_a_member_pack(pack)


def test_batch_a_gate_parks_without_pack_and_accepts_v2(tmp_path: Path) -> None:
    parked = evaluate_batch_a_existing_member_baselines()
    assert parked["status"] == "PARKED"
    assert parked["batch_a_accepted"] is False
    assert "BATCH_A_LEGACY_R3_ENTRY_EXITED" in parked["reason_codes"]
    assert parked["boundaries"]["cash_symbol"] == "USD_CASH_NOT_BOXX"

    dates = _dates(210)
    soxl = load_price_snapshot_v2(
        _write_dataset(tmp_path, dataset_id="gate-soxl", symbols=("SOXX", "SOXL"), dates=dates)
    )
    tqqq = load_price_snapshot_v2(
        _write_dataset(tmp_path, dataset_id="gate-tqqq", symbols=("QQQ", "TQQQ"), dates=dates)
    )
    pack = materialize_batch_a_member_pack(soxl_snapshot=soxl, tqqq_snapshot=tqqq)
    # compare_fixed_member_budget_baselines requires members as mappings with returns;
    # rehydrate list form for the gate.
    gate_pack = {
        key: value for key, value in pack.items() if key != "members"
    }
    gate_pack["members"] = [dict(member) for member in pack["members"]]
    result = evaluate_batch_a_existing_member_baselines(frozen_member_pack=gate_pack)
    assert result["status"] == "READY_RESEARCH_ONLY"
    assert result["batch_a_accepted"] is True
    assert result["cash_return_policy"] == CASH_RETURN_POLICY
    assert result["execution_authorized"] is False
    assert "BOXX" not in json.dumps(result["declared_baselines"])


def test_batch_a_gate_rejects_v1_pack() -> None:
    result = evaluate_batch_a_existing_member_baselines(
        frozen_member_pack={
            "schema_version": "qsl.c3-batch-a-frozen-member-pack.v1",
            "research_only": True,
            "execution_authorized": False,
            "cash_return_policy": "ASSUMED_ZERO_CASH_SLEEVE",
            "members": [],
        }
    )
    assert result["status"] == "PARKED"
    assert "FROZEN_MEMBER_PACK_V1_REJECTED" in result["reason_codes"]


def _flat_csv(symbol: str, points: tuple[tuple[str, float], ...]) -> bytes:
    lines = ["symbol,as_of,open,high,low,close,volume"]
    for as_of, close in points:
        text = format(close, ".17g")
        lines.append(",".join((symbol, as_of, text, text, text, text, "1")))
    return ("\n".join(lines) + "\n").encode()


def test_extracts_hand_calculated_adjusted_close_returns(tmp_path: Path) -> None:
    dates = _dates(3)
    dataset = _write_dataset(
        tmp_path, dataset_id="demo-returns", symbols=("SOXX", "SOXL"), dates=dates
    )
    extracted = extract_adjusted_close_simple_returns(dataset)
    loaded = load_price_snapshot_v2(dataset)

    assert extracted["input_digest"] == loaded.input_digest
    assert extracted["return_definition"] == "adjusted_close_t_over_previous_close_minus_one"
    assert extracted["first_price_date"] == dates[0]
    assert extracted["source_observation_dates"] == dates
    assert "quote_currency" not in extracted
    assert "GENERATION_MATCH_DOES_NOT_PROVE_CLOUD_SOURCE" in extracted["limitations"]
    panel = extracted["asset_returns"]
    assert set(panel) == {"SOXX", "SOXL"}
    assert [item["date"] for item in panel["SOXX"]] == dates[1:]
    assert [item["date"] for item in panel["SOXL"]] == dates[1:]
    assert panel["SOXX"][0]["simple_return"] == pytest.approx(101.0 / 100.0 - 1.0)
    assert panel["SOXX"][1]["simple_return"] == pytest.approx(102.0 / 101.0 - 1.0)
    assert panel["SOXL"][0]["simple_return"] == pytest.approx(111.0 / 110.0 - 1.0)
    assert panel["SOXL"][1]["simple_return"] == pytest.approx(112.0 / 111.0 - 1.0)


def test_return_extraction_rejects_identity_generation_and_bytes(tmp_path: Path) -> None:
    dates = _dates(3)
    missing = _write_dataset(tmp_path, dataset_id="demo-missing-id", symbols=("SOXL",), dates=dates)
    (missing / "object_identity.json").unlink()
    with pytest.raises(BatchADatasetError, match="OBJECT_IDENTITY_MISSING"):
        extract_adjusted_close_simple_returns(missing)

    mismatched = _write_dataset(
        tmp_path, dataset_id="demo-return-gen", symbols=("SOXL",), dates=dates, generation="4"
    )
    (mismatched / "object_identity.json").write_text(
        json.dumps({"generation": "5"}),
        encoding="utf-8",
    )
    with pytest.raises(BatchADatasetError, match="GCS_GENERATION_MISMATCH"):
        extract_adjusted_close_simple_returns(mismatched)

    changed = _write_dataset(tmp_path, dataset_id="demo-return-bytes", symbols=("SOXL",), dates=dates)
    artifact = (changed / "prices.csv").read_bytes().replace(b"1000", b"1001", 1)
    (changed / "prices.csv").write_bytes(artifact)
    with pytest.raises(BatchADatasetError, match="GCS_CONTENT_MISMATCH"):
        extract_adjusted_close_simple_returns(changed)

    manifest_dataset = _write_dataset(
        tmp_path, dataset_id="demo-return-manifest", symbols=("SOXL",), dates=dates
    )
    manifest_path = manifest_dataset / "prices.csv.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gcs"]["sha256"] = "1" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BatchADatasetError, match="GCS_CONTENT_MISMATCH"):
        extract_adjusted_close_simple_returns(manifest_dataset)


def test_return_extraction_rejects_unadjusted_prices(tmp_path: Path) -> None:
    dates = _dates(3)
    dataset = _write_dataset(tmp_path, dataset_id="demo-raw-close", symbols=("SOXL",), dates=dates)
    manifest_path = dataset / "prices.csv.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["price_field"] = "close"
    manifest["adjustment"] = "none"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BatchADatasetError, match="PRICE_DEFINITION_REJECTED"):
        extract_adjusted_close_simple_returns(dataset)


@pytest.mark.parametrize(
    "closes",
    (
        (1e-300, 1e308),
        (1e308, 1e-300),
    ),
)
def test_return_extraction_rejects_extreme_finite_closes(
    tmp_path: Path, closes: tuple[float, float]
) -> None:
    dates = _dates(2)
    artifact = _flat_csv("SOXL", tuple(zip(dates, closes, strict=True)))
    dataset = _write_dataset(
        tmp_path,
        dataset_id="demo-extreme",
        symbols=("SOXL",),
        dates=dates,
        mutate_bytes=artifact,
    )
    with pytest.raises(BatchADatasetError, match="ASSET_RETURN_INVALID"):
        extract_adjusted_close_simple_returns(dataset)
