"""Offline synthetic checks for the UX1 async research job."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.run_ux1_async_job import (
    BOXX_POLICY_NAME,
    BOXX_POLICY_SHA256,
    CHILD_ENV_KEYS,
    CLAIM_PATH,
    HTTP_BODY_LIMIT,
    HTTP_TIMEOUT_SECONDS,
    OBJECT_BYTES_LIMIT,
    PREVIEW_STDOUT_LIMIT,
    PREVIEW_TIMEOUT_SECONDS,
    R6_CASE_LAYOUT,
    R6_FIXED_RELATIVE,
    RAW_FIXED_RELATIVE,
    RESULT_PATH,
    STAGING_DEADLINE_SECONDS,
    Ux1AsyncError,
    _run_preview_process,
    build_child_env,
    _post_terminal,
    execute,
    post_json,
    stage_ux1_inputs,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ux1-research-preview.yml"
REQUEST_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
RUN_ID = "4242"
BUCKET = "ux1syntheticbucket"
RAW_PREFIX = f"gs://{BUCKET}/case/raw"
R6_PREFIX = f"gs://{BUCKET}/case/r6"
MATERIALIZED_URI = f"gs://{BUCKET}/case/materialized/input.json"
TOKEN = "synthetic-job-token"
GITHUB_TOKEN = "synthetic-github-token"
WIF_TOKEN = "synthetic-wif-token"


def _policy_bytes(source_objects: dict) -> bytes:
    return json.dumps({"source_objects": source_objects}, sort_keys=True, separators=(",", ":")).encode()


def _store(policy: bytes, *, size_override: dict | None = None, bucket_override: dict | None = None) -> dict:
    objects = {f"{RAW_PREFIX}/{name}": b"{}" for name in RAW_FIXED_RELATIVE}
    objects[f"{RAW_PREFIX}/{BOXX_POLICY_NAME}"] = policy
    objects[f"{RAW_PREFIX}/sidecar/note.json"] = b"{}"
    for object_relative, _local_name in R6_CASE_LAYOUT:
        objects[f"{R6_PREFIX}/{object_relative}"] = b"{}"
    objects[MATERIALIZED_URI] = b"{}"
    store = {}
    for url, body in objects.items():
        object_name = url[len(f"gs://{BUCKET}/") :]
        store[url] = {
            "bucket": BUCKET,
            "name": object_name,
            "size": len(body),
            "generation": "171",
            "body": body,
        }
    if size_override:
        for url, size in size_override.items():
            store[url]["size"] = size
    if bucket_override:
        for url, bucket in bucket_override.items():
            store[url]["bucket"] = bucket
    return store


def _gcloud(store: dict, calls: list[tuple[str, ...]], *, symlink_cp: bool = False):
    def run(command: tuple[str, ...], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:4] == ("storage", "objects", "describe"):
            item = store.get(command[4])
            if item is None:
                return subprocess.CompletedProcess(command, 1, "", "")
            payload = json.dumps(
                {
                    "bucket": item["bucket"],
                    "generation": item["generation"],
                    "name": item["name"],
                    "size": item["size"],
                }
            )
            return subprocess.CompletedProcess(command, 0, payload, "")
        if command[1:3] == ("storage", "cp"):
            source, destination = command[4], Path(command[5])
            url, generation = source.rsplit("#", 1)
            item = store[url]
            assert generation == item["generation"]
            assert "--no-clobber" in command
            if symlink_cp:
                destination.symlink_to(destination.name + ".target")
            else:
                destination.write_bytes(item["body"])
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "")

    return run


def _env(**extra: str) -> dict[str, str]:
    base = {
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": RUN_ID,
        "UX1_CONSOLE_URL": "https://console.example",
        "UX1_REQUEST_ID": REQUEST_ID,
        "UX1_RESEARCH_JOB_TOKEN": TOKEN,
        "GITHUB_TOKEN": GITHUB_TOKEN,
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": WIF_TOKEN,
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/synthetic-adc.json",
    }
    base.update(extra)
    return base


def _gcs_env(**extra: str) -> dict[str, str]:
    return _env(
        UX1_RAW_GCS_PREFIX=RAW_PREFIX,
        UX1_R6_GCS_PREFIX=R6_PREFIX,
        UX1_MATERIALIZED_GCS_URI=MATERIALIZED_URI,
        UX1_INPUT_BUCKET=BUCKET,
        **extra,
    )


def _claim(claimed: bool, request: dict | None = None) -> bytes:
    body = {
        "claimed": claimed,
        "ok": True,
        "request_id": REQUEST_ID,
        "run_attempt": "1",
        "run_id": RUN_ID,
    }
    if claimed:
        body["request"] = request or {"schema": "qsl.ux1.preview_request.v1", "research_case_id": "synthetic"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _transport(responses: list[bytes], calls: list[tuple[str, bytes]]):
    def send(path: str, payload: bytes) -> bytes:
        calls.append((path, payload))
        if not responses:
            raise Ux1AsyncError("UX1_HTTP_FAILED")
        return responses.pop(0)

    return send


def _ok_preview(_payload: bytes, _env: dict[str, str]) -> bytes:
    return json.dumps(
        {
            "execution_authority_granted": False,
            "no_order": True,
            "schema": "qsl.ux1.preview_result.v1",
            "status": "ok",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_boxx_digest_and_whitelist_match_existing_loader() -> None:
    boxx = (ROOT / "docs/research/first_compounding_20260925/boxx_outer_cash_compare.py").read_text()
    s4 = (ROOT / "docs/research/first_compounding_20260925/tqqq_cash_budget_compare.py").read_text()
    contract = (ROOT / "src/us_equity_strategies/research/tqqq_qqq_guard_cash_research.py").read_text()
    assert f'POLICY_SHA = "{BOXX_POLICY_SHA256}"' in boxx
    assert 'POLICY_NAME = "s4_budget_policy.v1.json"' in s4
    assert 'CONTRACT_NAME = "tqqq_qqq_guard_cash_contract.v1.json"' in contract
    for symbol in ("QQQ", "TQQQ", "SOXL", "SOXX", "BOXX", "QQQM"):
        assert f"bars/{symbol}/page-001.json" in RAW_FIXED_RELATIVE
        assert f"actions/{symbol}/page-001.json" in RAW_FIXED_RELATIVE
    assert "prices.csv" not in RAW_FIXED_RELATIVE
    assert R6_CASE_LAYOUT == (
        ("p1/manifest.json", "manifest.json"),
        ("complete.json", "complete.json"),
        ("p1/binding.json", "binding.json"),
        ("p1/closes.json", "closes.json"),
        ("p1/assurance.json", "assurance.json"),
    )
    assert R6_FIXED_RELATIVE == ("manifest.json", "complete.json", "binding.json", "closes.json", "assurance.json")
    assert PREVIEW_STDOUT_LIMIT == 65536
    assert PREVIEW_TIMEOUT_SECONDS == 30
    assert HTTP_TIMEOUT_SECONDS == 30
    assert HTTP_BODY_LIMIT == 80 * 1024


def test_missing_and_partial_config_reports_input_unavailable_without_gcloud() -> None:
    calls: list[tuple[str, bytes]] = []
    gcloud_calls: list[tuple[str, ...]] = []
    for env in (_env(), _env(UX1_INPUT_BUCKET=BUCKET)):
        calls.clear()
        code = execute(
            env,
            transport=_transport([_claim(True), b'{"ok":true}'], calls),
            runner=_gcloud({}, gcloud_calls),
            preview=_ok_preview,
        )
        assert code == "UX1_INPUT_UNAVAILABLE"
        assert [path for path, _payload in calls] == [CLAIM_PATH, RESULT_PATH]
        posted = json.loads(calls[1][1])
        assert posted["error_code"] == "input_unavailable"
        assert posted["result"] is None
        assert set(posted) == {"error_code", "request_id", "result", "run_attempt", "run_id"}
        assert gcloud_calls == []
        calls.clear()


def test_unconfigured_identity_does_not_call_http() -> None:
    calls: list[tuple[str, bytes]] = []
    env = _env()
    env["UX1_CONSOLE_URL"] = "http://console.example"
    assert execute(env, transport=_transport([], calls), preview=_ok_preview) == "UX1_JOB_UNCONFIGURED"
    env["UX1_CONSOLE_URL"] = "https://user:secret@console.example"
    assert execute(env, transport=_transport([], calls), preview=_ok_preview) == "UX1_JOB_UNCONFIGURED"
    env = _env()
    env["UX1_CONSOLE_URL"] = "https://console.example/callback"
    assert execute(env, transport=_transport([], calls), preview=_ok_preview) == "UX1_JOB_UNCONFIGURED"
    assert calls == []


def test_run_attempt_above_one_stops_before_claim() -> None:
    calls: list[tuple[str, bytes]] = []
    assert execute(_env(GITHUB_RUN_ATTEMPT="2"), transport=_transport([], calls)) == "UX1_RUN_ATTEMPT_REJECTED"
    assert calls == []


def test_duplicate_claim_does_not_calculate_or_download(tmp_path: Path) -> None:
    calls: list[tuple[str, bytes]] = []
    previews: list[bytes] = []
    code = execute(
        _gcs_env(),
        transport=_transport([_claim(False)], calls),
        runner=_gcloud({}, []),
        preview=lambda payload, env: previews.append(payload) or b"",
    )
    assert code == "UX1_CLAIM_NOT_GRANTED"
    assert [path for path, _payload in calls] == [CLAIM_PATH]
    assert previews == []


def test_cross_bucket_traversal_and_glob_do_not_download(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    policy = _policy_bytes({"../secret.json": {"bytes": 2, "sha256": "synthetic"}})
    monkeypatch.setattr("scripts.run_ux1_async_job.BOXX_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    with pytest.raises(Ux1AsyncError) as cross:
        stage_ux1_inputs(
            raw_prefix="gs://otherbucket/case/raw",
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "cross",
            runner=_gcloud({}, calls),
        )
    assert cross.value.code == "UX1_GCS_REJECTED"
    assert calls == []

    store = _store(policy)
    with pytest.raises(Ux1AsyncError) as traversed:
        stage_ux1_inputs(
            raw_prefix=RAW_PREFIX,
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "traverse",
            runner=_gcloud(store, calls),
        )
    assert traversed.value.code == "UX1_GCS_REJECTED"
    assert calls
    assert all("secret" not in part for command in calls for part in command)
    assert sum(1 for command in calls if command[1:3] == ("storage", "cp")) == 1

    calls.clear()
    with pytest.raises(Ux1AsyncError) as globbed:
        stage_ux1_inputs(
            raw_prefix=f"gs://{BUCKET}/case/raw/*",
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "glob",
            runner=_gcloud(store, calls),
        )
    assert globbed.value.code == "UX1_GCS_REJECTED"
    assert calls == []


def test_metadata_generation_size_and_deadline(tmp_path: Path, monkeypatch) -> None:
    policy = _policy_bytes({"sidecar/note.json": {"bytes": 2, "sha256": "synthetic"}})
    monkeypatch.setattr("scripts.run_ux1_async_job.BOXX_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    calls: list[tuple[str, ...]] = []
    store = _store(policy, size_override={f"{RAW_PREFIX}/{BOXX_POLICY_NAME}": OBJECT_BYTES_LIMIT + 1})
    with pytest.raises(Ux1AsyncError) as oversized:
        stage_ux1_inputs(
            raw_prefix=RAW_PREFIX,
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "oversize",
            runner=_gcloud(store, calls),
        )
    assert oversized.value.code == "UX1_GCS_REJECTED"
    assert calls[0][1:4] == ("storage", "objects", "describe")
    assert not any(command[1:3] == ("storage", "cp") for command in calls)

    calls.clear()
    mismatched = _store(policy, bucket_override={f"{RAW_PREFIX}/{BOXX_POLICY_NAME}": "otherbucketname"})
    with pytest.raises(Ux1AsyncError) as foreign:
        stage_ux1_inputs(
            raw_prefix=RAW_PREFIX,
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "foreign",
            runner=_gcloud(mismatched, calls),
        )
    assert foreign.value.code == "UX1_GCS_REJECTED"
    assert not any(command[1:3] == ("storage", "cp") for command in calls)

    calls.clear()
    store = _store(policy)
    monkeypatch.setattr("scripts.run_ux1_async_job.TOTAL_BYTES_LIMIT", len(policy))
    with pytest.raises(Ux1AsyncError):
        stage_ux1_inputs(
            raw_prefix=RAW_PREFIX,
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "total",
            runner=_gcloud(store, calls),
        )
    assert sum(1 for command in calls if command[1:3] == ("storage", "cp")) == 1
    monkeypatch.setattr("scripts.run_ux1_async_job.TOTAL_BYTES_LIMIT", 64 * 1024 * 1024)

    calls.clear()
    ticks = {"n": 0}

    def clock() -> float:
        ticks["n"] += 1
        return 0 if ticks["n"] <= 3 else 1000

    with pytest.raises(Ux1AsyncError):
        stage_ux1_inputs(
            raw_prefix=RAW_PREFIX,
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "deadline",
            runner=_gcloud(store, calls),
            clock=clock,
        )
    assert any(command[1:3] == ("storage", "cp") and command[4].endswith("#171") for command in calls)


def test_exact_generation_download_and_source_object(tmp_path: Path, monkeypatch) -> None:
    policy = _policy_bytes({"sidecar/note.json": {"bytes": 2, "sha256": "synthetic"}})
    monkeypatch.setattr("scripts.run_ux1_async_job.BOXX_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    calls: list[tuple[str, ...]] = []
    raw, r6, materialized = stage_ux1_inputs(
        raw_prefix=RAW_PREFIX,
        r6_prefix=R6_PREFIX,
        materialized_uri=MATERIALIZED_URI,
        bucket=BUCKET,
        destination=tmp_path / "stage",
        runner=_gcloud(_store(policy), calls),
    )
    copies = [command for command in calls if command[1:3] == ("storage", "cp")]
    assert copies
    assert all(command[4].endswith("#171") and "--no-clobber" in command for command in copies)
    assert all(command[2] != "ls" and "rm" not in command for command in calls)
    describes = [command for command in calls if command[1:4] == ("storage", "objects", "describe")]
    assert len(describes) == len(copies)
    assert (raw / "sidecar/note.json").read_bytes() == b"{}"
    assert (raw / "bars/TQQQ/page-001.json").read_bytes() == b"{}"
    assert (r6 / "assurance.json").is_file()
    assert not (r6 / "p1").exists()
    assert materialized.is_file() and not materialized.is_symlink()


def test_r6_p1_objects_stage_to_loader_names(tmp_path: Path, monkeypatch) -> None:
    policy = _policy_bytes({})
    monkeypatch.setattr("scripts.run_ux1_async_job.BOXX_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    store = _store(policy)
    bodies = {
        "complete.json": b'{"part":"complete"}',
        "p1/manifest.json": b'{"part":"manifest"}',
        "p1/binding.json": b'{"part":"binding"}',
        "p1/closes.json": b'{"part":"closes"}',
        "p1/assurance.json": b'{"part":"assurance"}',
    }
    for relative, body in bodies.items():
        item = store[f"{R6_PREFIX}/{relative}"]
        item["body"] = body
        item["size"] = len(body)
    calls: list[tuple[str, ...]] = []
    _raw, r6, _materialized = stage_ux1_inputs(
        raw_prefix=RAW_PREFIX,
        r6_prefix=R6_PREFIX,
        materialized_uri=MATERIALIZED_URI,
        bucket=BUCKET,
        destination=tmp_path / "mapped",
        runner=_gcloud(store, calls),
    )
    described = [command[4] for command in calls if command[1:4] == ("storage", "objects", "describe")]
    copied = [command[4].rsplit("#", 1)[0] for command in calls if command[1:3] == ("storage", "cp")]
    for source, local_name in R6_CASE_LAYOUT:
        object_url = f"{R6_PREFIX}/{source}"
        assert object_url in described
        assert object_url in copied
        assert (r6 / local_name).read_bytes() == bodies[source]
        if source != local_name:
            assert f"{R6_PREFIX}/{local_name}" not in described
            assert not (r6 / source).exists()
    assert not (r6 / "p1").exists()

    flat_store = _store(policy)
    for source, local_name in R6_CASE_LAYOUT:
        if source == local_name:
            continue
        flat_store.pop(f"{R6_PREFIX}/{source}")
        flat_store[f"{R6_PREFIX}/{local_name}"] = {
            "bucket": BUCKET,
            "name": f"case/r6/{local_name}",
            "size": 2,
            "generation": "171",
            "body": b"{}",
        }
    with pytest.raises(Ux1AsyncError) as caught:
        stage_ux1_inputs(
            raw_prefix=RAW_PREFIX,
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "flat",
            runner=_gcloud(flat_store, []),
        )
    assert caught.value.code == "UX1_GCS_FAILED"
    assert not (tmp_path / "flat" / "r6" / "manifest.json").exists()


def test_symlink_destination_is_rejected(tmp_path: Path, monkeypatch) -> None:
    policy = _policy_bytes({})
    monkeypatch.setattr("scripts.run_ux1_async_job.BOXX_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    calls: list[tuple[str, ...]] = []
    with pytest.raises(Ux1AsyncError) as caught:
        stage_ux1_inputs(
            raw_prefix=RAW_PREFIX,
            r6_prefix=R6_PREFIX,
            materialized_uri=MATERIALIZED_URI,
            bucket=BUCKET,
            destination=tmp_path / "link",
            runner=_gcloud(_store(policy), calls, symlink_cp=True),
        )
    assert caught.value.code == "UX1_GCS_REJECTED"


def test_child_env_omits_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UX1_RESEARCH_JOB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", WIF_TOKEN)
    monkeypatch.setenv("UX1_CONSOLE_URL", "https://console.example")
    env = build_child_env(tmp_path / "raw", tmp_path / "r6", tmp_path / "materialized.json")
    assert set(env) == set(CHILD_ENV_KEYS)
    assert env["PATH"] == "/usr/bin:/bin"
    assert "PYTHONPATH" not in env
    preview = tmp_path / "ux1-preview"
    preview.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nsys.stdout.write(json.dumps(sorted(os.environ)))\n",
        encoding="utf-8",
    )
    preview.chmod(0o755)
    stdout = _run_preview_process([str(preview)], b"{}", env, timeout=5)
    observed = set(json.loads(stdout))
    assert set(CHILD_ENV_KEYS) <= observed
    assert {
        "GITHUB_TOKEN",
        "UX1_RESEARCH_JOB_TOKEN",
        "UX1_CONSOLE_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "PYTHONPATH",
    }.isdisjoint(observed)
    assert TOKEN not in stdout.decode()
    assert GITHUB_TOKEN not in stdout.decode()
    assert WIF_TOKEN not in stdout.decode()


def test_preview_timeout_and_stdout_cap(tmp_path: Path) -> None:
    sleeper = tmp_path / "ux1-preview"
    sleeper.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n", encoding="utf-8")
    sleeper.chmod(0o755)
    with pytest.raises(Ux1AsyncError) as timed:
        _run_preview_process([str(sleeper)], b"{}", {"PATH": "/usr/bin:/bin"}, timeout=0.2)
    assert timed.value.code == "UX1_CALCULATOR_FAILED"
    assert PREVIEW_TIMEOUT_SECONDS == 30

    huge = tmp_path / "ux1-preview"
    huge.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'x'*{PREVIEW_STDOUT_LIMIT + 1})\n",
        encoding="utf-8",
    )
    huge.chmod(0o755)
    with pytest.raises(Ux1AsyncError) as oversized:
        _run_preview_process([str(huge)], b"{}", {"PATH": "/usr/bin:/bin"}, timeout=5)
    assert oversized.value.code == "UX1_CALCULATOR_FAILED"


def test_stdout_over_limit_kills_hung_process(tmp_path: Path) -> None:
    hung = tmp_path / "ux1-preview"
    hung.write_text(
        f"#!{sys.executable}\nimport sys, time\n"
        f"sys.stdout.buffer.write(b'x'*{PREVIEW_STDOUT_LIMIT + 1})\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    hung.chmod(0o755)
    started = time.monotonic()
    with pytest.raises(Ux1AsyncError) as caught:
        _run_preview_process([str(hung)], b"{}", {"PATH": "/usr/bin:/bin"}, timeout=5)
    elapsed = time.monotonic() - started
    assert caught.value.code == "UX1_CALCULATOR_FAILED"
    assert elapsed < 3


def test_large_stderr_is_discarded_without_blocking(tmp_path: Path) -> None:
    noisy = tmp_path / "ux1-preview"
    noisy.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "sys.stderr.buffer.write(b'E' * (1 << 20))\n"
        "sys.stderr.buffer.flush()\n"
        "sys.stdout.buffer.write(b'ok')\n",
        encoding="utf-8",
    )
    noisy.chmod(0o755)
    started = time.monotonic()
    stdout = _run_preview_process([str(noisy)], b"{}", {"PATH": "/usr/bin:/bin"}, timeout=5)
    assert stdout == b"ok"
    assert b"E" not in stdout
    assert time.monotonic() - started < 5


def test_redirect_does_not_forward_bearer_token() -> None:
    created = []

    class Response:
        status = 302

        def read(self, amount: int = -1) -> bytes:
            raise AssertionError("redirect body was consumed")

    class Connection:
        def __init__(self, host: str, port: int, timeout: float):
            assert host == "console.example"
            assert port == 443
            assert timeout == HTTP_TIMEOUT_SECONDS
            self.requests = []
            created.append(self)

        def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> None:
            self.requests.append((method, path, body, headers))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            return None

    with pytest.raises(Ux1AsyncError) as caught:
        post_json(
            "https://console.example",
            CLAIM_PATH,
            TOKEN,
            b"{}",
            connection_factory=Connection,
        )
    assert caught.value.code == "UX1_HTTP_REDIRECT"
    assert TOKEN not in str(caught.value)
    assert len(created) == 1
    assert len(created[0].requests) == 1
    method, path, body, headers = created[0].requests[0]
    assert method == "POST"
    assert path == CLAIM_PATH
    assert TOKEN.encode() not in (body or b"")
    assert headers["Authorization"] == "Bearer " + TOKEN

    class Denied:
        status = 500

        def read(self, amount: int = -1) -> bytes:
            return (TOKEN + " provider diagnostic").encode()

    class DeniedConnection(Connection):
        def getresponse(self) -> Denied:
            return Denied()

    created.clear()
    with pytest.raises(Ux1AsyncError) as denied:
        post_json(
            "https://console.example",
            CLAIM_PATH,
            TOKEN,
            b"{}",
            connection_factory=DeniedConnection,
        )
    assert denied.value.code == "UX1_HTTP_FAILED"
    assert TOKEN not in str(denied.value)
    assert "provider" not in str(denied.value)
    assert len(created) == 1


def test_slow_response_body_stops_at_total_deadline() -> None:
    created = []
    reads = {"n": 0}

    class Response:
        status = 200

        def read(self, amount: int = -1) -> bytes:
            reads["n"] += 1
            time.sleep(0.05)
            return b"x"

    class Connection:
        def __init__(self, host: str, port: int, timeout: float):
            assert timeout == 0.2
            self.requests = []
            created.append(self)

        def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> None:
            self.requests.append((method, path, body, headers))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            return None

    started = time.monotonic()
    with pytest.raises(Ux1AsyncError) as caught:
        post_json(
            "https://console.example",
            CLAIM_PATH,
            TOKEN,
            b"{}",
            timeout=0.2,
            connection_factory=Connection,
        )
    elapsed = time.monotonic() - started
    assert caught.value.code == "UX1_HTTP_FAILED"
    assert TOKEN not in str(caught.value)
    assert len(created) == 1
    assert len(created[0].requests) == 1
    assert reads["n"] < 15
    assert elapsed < 1.0


def _open_loopback_http(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    assert host == "127.0.0.1"
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _silence_http_log(handler: BaseHTTPRequestHandler, _format: str, *_args: object) -> None:
    return


def _start_loopback(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _assert_near_timeout(elapsed: float, timeout: float) -> None:
    assert timeout - 0.03 <= elapsed < timeout + 0.35, f"elapsed={elapsed:.4f} timeout={timeout:.4f}"


def test_loopback_slow_body_stops_near_total_deadline() -> None:
    timeout = 0.12
    seen = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        log_message = _silence_http_log

        def setup(self) -> None:
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def do_POST(self) -> None:
            self.close_connection = True
            seen["n"] += 1
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            payload = b"x" * 40
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            try:
                for index in range(len(payload)):
                    self.wfile.write(payload[index : index + 1])
                    self.wfile.flush()
                    time.sleep(0.04)
            except OSError:
                return

    server, _thread = _start_loopback(Handler)
    port = int(server.server_address[1])
    try:
        started = time.monotonic()
        with pytest.raises(Ux1AsyncError) as caught:
            post_json(
                f"https://127.0.0.1:{port}",
                CLAIM_PATH,
                TOKEN,
                b"{}",
                timeout=timeout,
                connection_factory=_open_loopback_http,
            )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
    assert caught.value.code == "UX1_HTTP_FAILED"
    assert seen["n"] == 1
    assert "Broken" not in str(caught.value)
    assert TOKEN not in str(caught.value)
    _assert_near_timeout(elapsed, timeout)


def test_loopback_slow_headers_stop_near_total_deadline() -> None:
    timeout = 0.12
    seen = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        log_message = _silence_http_log

        def setup(self) -> None:
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def do_POST(self) -> None:
            self.close_connection = True
            seen["n"] += 1
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            raw = b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx"
            try:
                for index in range(len(raw)):
                    self.wfile.write(raw[index : index + 1])
                    self.wfile.flush()
                    time.sleep(0.04)
            except OSError:
                return

    server, _thread = _start_loopback(Handler)
    port = int(server.server_address[1])
    try:
        started = time.monotonic()
        with pytest.raises(Ux1AsyncError) as caught:
            post_json(
                f"https://127.0.0.1:{port}",
                CLAIM_PATH,
                TOKEN,
                b"{}",
                timeout=timeout,
                connection_factory=_open_loopback_http,
            )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
    assert caught.value.code == "UX1_HTTP_FAILED"
    assert seen["n"] == 1
    assert "Broken" not in str(caught.value)
    assert TOKEN not in str(caught.value)
    _assert_near_timeout(elapsed, timeout)


def test_provider_body_is_not_returned_and_result_is_not_retried() -> None:
    calls: list[tuple[str, bytes]] = []

    def send(path: str, payload: bytes) -> bytes:
        calls.append((path, payload))
        if path == CLAIM_PATH:
            raise Ux1AsyncError("UX1_HTTP_FAILED")
        raise AssertionError(path)

    assert execute(_env(), transport=send, preview=_ok_preview) == "UX1_HTTP_FAILED"
    assert len(calls) == 1

    calls.clear()

    def fail_result(path: str, payload: bytes) -> bytes:
        calls.append((path, payload))
        if path == RESULT_PATH:
            raise Ux1AsyncError("UX1_HTTP_FAILED")
        return _claim(True)

    with pytest.raises(Ux1AsyncError) as caught:
        execute(_env(), transport=fail_result, preview=_ok_preview)
    assert caught.value.code == "UX1_HTTP_FAILED"
    assert [path for path, _payload in calls] == [CLAIM_PATH, RESULT_PATH]
    assert TOKEN not in str(caught.value)


def test_blocked_gcloud_subprocess_is_input_unavailable() -> None:
    timeouts: list[float] = []

    def blocked(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, (int, float))
        timeouts.append(float(timeout))
        expired = subprocess.TimeoutExpired(command, float(timeout))
        expired.stdout = "SECRET-STDOUT"
        expired.stderr = "SECRET-STDERR"
        raise expired

    calls: list[tuple[str, bytes]] = []
    code = execute(
        _gcs_env(),
        transport=_transport([_claim(True), b'{"ok":true}'], calls),
        runner=blocked,
        preview=_ok_preview,
        clock=lambda: 0.0,
    )
    assert code == "UX1_INPUT_UNAVAILABLE"
    assert timeouts == [STAGING_DEADLINE_SECONDS]
    assert [path for path, _payload in calls] == [CLAIM_PATH, RESULT_PATH]
    posted = json.loads(calls[1][1])
    assert posted["error_code"] == "input_unavailable"
    assert posted["result"] is None
    body = calls[1][1].decode()
    assert "SECRET-STDOUT" not in body
    assert "SECRET-STDERR" not in body


def _valid_result() -> dict:
    return {
        "execution_authority_granted": False,
        "no_order": True,
        "schema": "qsl.ux1.preview_result.v1",
        "status": "ok",
    }


def test_terminal_pair_rejected_before_post() -> None:
    sent: list[tuple[str, bytes]] = []

    def send(path: str, payload: bytes) -> bytes:
        sent.append((path, payload))
        return b"{}"

    valid = _valid_result()
    assert (
        _post_terminal(
            send,
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            run_attempt="1",
            result=valid,
            error_code=None,
        )
        == "UX1_OK"
    )
    assert (
        _post_terminal(
            send,
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            run_attempt="1",
            result=None,
            error_code="input_unavailable",
        )
        == "UX1_INPUT_UNAVAILABLE"
    )
    assert (
        _post_terminal(
            send,
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            run_attempt="1",
            result=None,
            error_code="calculator_failed",
        )
        == "UX1_CALCULATOR_FAILED"
    )
    assert len(sent) == 3
    sent.clear()
    invalid: tuple[tuple[dict | None, str | None], ...] = (
        (None, None),
        (valid, "input_unavailable"),
        (valid, "calculator_failed"),
        ({"status": "ok"}, None),
        (None, "other"),
    )
    for result, error_code in invalid:
        with pytest.raises(Ux1AsyncError) as caught:
            _post_terminal(
                send,
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                run_attempt="1",
                result=result,
                error_code=error_code,
            )
        assert caught.value.code == "UX1_CALCULATOR_FAILED"
        assert sent == []


def test_success_posts_one_result_with_restricted_env(tmp_path: Path, monkeypatch) -> None:
    policy = _policy_bytes({"sidecar/note.json": {"bytes": 2, "sha256": "synthetic"}})
    monkeypatch.setattr("scripts.run_ux1_async_job.BOXX_POLICY_SHA256", hashlib.sha256(policy).hexdigest())
    http_calls: list[tuple[str, bytes]] = []
    seen_env: dict[str, str] = {}

    def preview(payload: bytes, env: dict[str, str]) -> bytes:
        seen_env.update(env)
        assert TOKEN.encode() not in payload
        assert b"synthetic-github-token" not in payload
        return _ok_preview(payload, env)

    code = execute(
        _gcs_env(),
        transport=_transport([_claim(True), b'{"ok":true}'], http_calls),
        runner=_gcloud(_store(policy), []),
        preview=preview,
    )
    assert code == "UX1_OK"
    assert [path for path, _payload in http_calls] == [CLAIM_PATH, RESULT_PATH]
    claim = json.loads(http_calls[0][1])
    assert set(claim) == {"request_id", "run_attempt", "run_id"}
    assert claim["run_attempt"] == "1"
    result = json.loads(http_calls[1][1])
    assert result["error_code"] is None
    assert result["result"]["schema"] == "qsl.ux1.preview_result.v1"
    assert result["result"]["no_order"] is True
    assert result["result"]["execution_authority_granted"] is False
    assert set(seen_env) == set(CHILD_ENV_KEYS)
    assert TOKEN not in seen_env.values()
    assert GITHUB_TOKEN not in seen_env.values()
    assert WIF_TOKEN not in seen_env.values()
    del tmp_path


def test_workflow_is_manual_default_branch_without_artifacts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "ux1_request_id:" in workflow
    assert "run-name: UX1 preview ${{ inputs.ux1_request_id }}" in workflow
    for forbidden in ("schedule:", "pull_request:", "push:", "workflow_run:", "workflow_call:"):
        assert forbidden not in workflow
    assert "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)" in workflow
    assert "github.ref == 'refs/heads/main'" not in workflow
    assert "permissions:" in workflow
    assert "contents: read" in workflow
    assert "id-token: write" in workflow
    assert "contents: write" not in workflow
    assert "actions: write" not in workflow
    assert workflow.count("timeout-minutes:") == 1
    assert "timeout-minutes: 10" in workflow
    assert "group: ux1-research-preview" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093" in workflow
    assert "google-github-actions/setup-gcloud@e427ad8a34f8676edf47cf7d7925499adf3eb74f" in workflow
    assert "vars.GCP_WORKLOAD_IDENTITY_PROVIDER" in workflow
    assert "vars.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT" in workflow
    assert "vars.GCP_PROJECT_ID" in workflow
    assert "secrets.UX1_RESEARCH_JOB_TOKEN" in workflow
    assert "vars.UX1_CONSOLE_URL" in workflow
    assert "vars.UX1_RAW_GCS_PREFIX" in workflow
    assert "vars.UX1_R6_GCS_PREFIX" in workflow
    assert "vars.UX1_MATERIALIZED_GCS_URI" in workflow
    assert "vars.UX1_INPUT_BUCKET" in workflow
    assert "gs://" not in workflow
    assert "qsl-research-evidence" not in workflow
    assert "upload-artifact" not in workflow
    assert "actions/upload-artifact" not in workflow
    assert 'python-version: "3.13"' in workflow
    assert "astral-sh/setup-uv@v5" in workflow
    assert "uv sync --python 3.13 --frozen --extra research --no-editable --no-dev" in workflow
    assert "uv run --frozen --no-sync --python 3.13 python scripts/run_ux1_async_job.py" in workflow
    assert "pip install '.[research]'" not in workflow
    assert "uv lock" not in workflow
    assert "pip install --upgrade" not in workflow
    assert 'if [ "${GITHUB_RUN_ATTEMPT}" != "1" ]' in workflow
    assert "UX1_RUN_ATTEMPT_REJECTED" in workflow
    assert "set -x" not in workflow
    assert "gcs_input_prefix" not in workflow
    assert "prices.csv" not in workflow
