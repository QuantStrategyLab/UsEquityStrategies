#!/usr/bin/env python3
"""Claim one UX1 research preview, stage fixed inputs, and run ux1-preview.

The calculator remains the installed ``ux1-preview`` entry. This script does
not import or change the research bundle. Cloud locations are read only from
the process environment and are not given defaults. Network and subprocess
calls are not retried.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

REQUEST_SCHEMA = "qsl.ux1.preview_request.v1"
RESULT_SCHEMA = "qsl.ux1.preview_result.v1"
CLAIM_PATH = "/api/ux1/jobs/claim"
RESULT_PATH = "/api/ux1/jobs/result"

BOXX_POLICY_NAME = "boxx_outer_cash_policy.v1.json"
# Existing frozen digest in boxx_outer_cash_compare. Operators cannot override it.
BOXX_POLICY_SHA256 = "cfed32767cb367ccce7ef880c3c15f82d50b99fe805d542e85839fc67270c905"

_SYMBOLS = ("QQQ", "TQQQ", "SOXL", "SOXX", "BOXX", "QQQM")
_RAW_POLICIES = (
    "manifest.json",
    "tqqq_qqq_guard_cash_contract.v1.json",
    BOXX_POLICY_NAME,
    "s4_budget_policy.v1.json",
)
RAW_FIXED_RELATIVE = _RAW_POLICIES + tuple(
    f"{kind}/{symbol}/page-001.json" for kind in ("bars", "actions") for symbol in _SYMBOLS
)
# Object path under the R6 prefix, then the flat name the existing loader reads.
# complete.json stays at the candidate root; the other four stay under p1/.
R6_CASE_LAYOUT = (
    ("p1/manifest.json", "manifest.json"),
    ("complete.json", "complete.json"),
    ("p1/binding.json", "binding.json"),
    ("p1/closes.json", "closes.json"),
    ("p1/assurance.json", "assurance.json"),
)
R6_FIXED_RELATIVE = tuple(local_name for _object_name, local_name in R6_CASE_LAYOUT)

PREVIEW_STDOUT_LIMIT = 65536
PREVIEW_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 30
HTTP_BODY_LIMIT = 80 * 1024
OBJECT_BYTES_LIMIT = 16 * 1024 * 1024
TOTAL_BYTES_LIMIT = 64 * 1024 * 1024
OBJECT_COUNT_LIMIT = 40
STAGING_DEADLINE_SECONDS = 120

CHILD_ENV_KEYS = (
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "UX1_MATERIALIZED",
    "UX1_R6_ROOT",
    "UX1_RAW_ROOT",
)
_CHILD_PATH = "/usr/bin:/bin"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_RUN_ID = re.compile(r"^[1-9][0-9]{0,18}$")
_RELATIVE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,63}/)*[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_SAFE_CODE = re.compile(r"^UX1_[A-Z0-9_]{1,40}$")
_GCS_KEYS = (
    "UX1_RAW_GCS_PREFIX",
    "UX1_R6_GCS_PREFIX",
    "UX1_MATERIALIZED_GCS_URI",
    "UX1_INPUT_BUCKET",
)

_Runner = Callable[..., subprocess.CompletedProcess[str]]
_Transport = Callable[[str, bytes], bytes]
_Preview = Callable[[bytes, Mapping[str, str]], bytes]
_Clock = Callable[[], float]


class Ux1AsyncError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _safe_relative(name: object) -> str:
    if not isinstance(name, str) or name != name.strip() or name != os.path.normpath(name):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if any(part in {".", ".."} for part in name.split("/")):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if any(token in name for token in ("\\", "\x00", "*", "?", "[", "]", "~", ":")):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if _RELATIVE.fullmatch(name) is None:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    return name


def _require_bucket(value: str) -> str:
    if not isinstance(value, str) or _BUCKET.fullmatch(value) is None or ".." in value:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    return value


def _object_name(uri: str, bucket: str) -> str:
    prefix = f"gs://{bucket}/"
    if (
        not isinstance(uri, str)
        or uri != uri.strip()
        or not uri.startswith(prefix)
        or uri.endswith("/")
        or "//" in uri[len("gs://") :]
    ):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if any(token in uri for token in ("*", "?", "#", "\\", "\x00", " ", "..")):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    return _safe_relative(uri[len(prefix) :])


def _positive_id(value: object) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise Ux1AsyncError("UX1_JOB_UNCONFIGURED")
    return value


def _origin(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise Ux1AsyncError("UX1_JOB_UNCONFIGURED")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not parsed.hostname
    ):
        raise Ux1AsyncError("UX1_JOB_UNCONFIGURED")
    host = parsed.hostname
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    rebuilt = f"https://{netloc}"
    if value.rstrip("/") != rebuilt:
        raise Ux1AsyncError("UX1_JOB_UNCONFIGURED")
    return rebuilt


def _token(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Ux1AsyncError("UX1_JOB_UNCONFIGURED")
    if any(char in value for char in "\r\n\x00 "):
        raise Ux1AsyncError("UX1_JOB_UNCONFIGURED")
    return value


def _request_id(value: object) -> str:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        raise Ux1AsyncError("UX1_JOB_UNCONFIGURED")
    return value


def build_child_env(raw_root: Path, r6_root: Path, materialized: Path) -> dict[str, str]:
    """Environment allowlist for the calculator. Parent credentials are omitted."""

    env = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": _CHILD_PATH,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "UX1_RAW_ROOT": str(raw_root),
        "UX1_R6_ROOT": str(r6_root),
        "UX1_MATERIALIZED": str(materialized),
    }
    if tuple(sorted(env)) != tuple(sorted(CHILD_ENV_KEYS)):
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
    return env


def post_json(
    origin: str,
    path: str,
    token: str,
    payload: bytes,
    *,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    connection_factory: Callable[..., http.client.HTTPConnection] | None = None,
) -> bytes:
    """POST one JSON body. Redirects are not followed, so the bearer token stays put."""

    if path not in {CLAIM_PATH, RESULT_PATH} or len(payload) > HTTP_BODY_LIMIT:
        raise Ux1AsyncError("UX1_HTTP_FAILED")
    parsed = urlsplit(_origin(origin))
    factory = connection_factory or http.client.HTTPSConnection
    deadline = time.monotonic() + timeout
    conn = factory(parsed.hostname, parsed.port or 443, timeout=timeout)
    aborted = {"value": False}
    tracked: dict[str, socket.socket | None] = {"sock": None}
    timer = _arm_http_deadline(conn, deadline, aborted, tracked)
    try:
        _arm_http_timeout(conn, _http_remaining(deadline), tracked)
        conn.request(
            "POST",
            path,
            body=payload,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        _remember_http_socket(conn, tracked)
        _raise_if_http_deadline(deadline, aborted)
        _arm_http_timeout(conn, _http_remaining(deadline), tracked)
        response = conn.getresponse()
        _remember_http_socket(conn, tracked, response)
        _raise_if_http_deadline(deadline, aborted)
        status = response.status
        if 300 <= status < 400:
            raise Ux1AsyncError("UX1_HTTP_REDIRECT")
        raw = bytearray()
        while len(raw) <= HTTP_BODY_LIMIT:
            _raise_if_http_deadline(deadline, aborted)
            _arm_http_timeout(conn, _http_remaining(deadline), tracked)
            chunk = response.read(min(8192, HTTP_BODY_LIMIT + 1 - len(raw)))
            _raise_if_http_deadline(deadline, aborted)
            if not chunk:
                break
            raw.extend(chunk)
        _raise_if_http_deadline(deadline, aborted)
        if status != 200 or len(raw) > HTTP_BODY_LIMIT:
            raise Ux1AsyncError("UX1_HTTP_FAILED")
        return bytes(raw)
    except Ux1AsyncError:
        raise
    except Exception:
        raise Ux1AsyncError("UX1_HTTP_FAILED") from None
    finally:
        timer.cancel()
        try:
            conn.close()
        except Exception:
            pass


def _http_remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Ux1AsyncError("UX1_HTTP_FAILED")
    return remaining


def _raise_if_http_deadline(deadline: float, aborted: dict[str, bool]) -> None:
    if aborted["value"] or time.monotonic() >= deadline:
        raise Ux1AsyncError("UX1_HTTP_FAILED")


def _socket_of(obj: object) -> socket.socket | None:
    sock = getattr(obj, "sock", None)
    if isinstance(sock, socket.socket):
        return sock
    raw = getattr(getattr(obj, "fp", None), "raw", None)
    while raw is not None and not isinstance(getattr(raw, "_sock", None), socket.socket):
        raw = getattr(raw, "raw", None)
    found = getattr(raw, "_sock", None) if raw is not None else None
    return found if isinstance(found, socket.socket) else None


def _remember_http_socket(
    conn: http.client.HTTPConnection,
    tracked: dict[str, socket.socket | None],
    response: object | None = None,
) -> None:
    for candidate in (_socket_of(conn), None if response is None else _socket_of(response)):
        if candidate is not None:
            tracked["sock"] = candidate


def _shutdown_socket(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _abort_http_connection(
    conn: http.client.HTTPConnection,
    aborted: dict[str, bool],
    tracked: dict[str, socket.socket | None],
) -> None:
    aborted["value"] = True
    _shutdown_socket(tracked.get("sock"))
    _shutdown_socket(_socket_of(conn))


def _arm_http_deadline(
    conn: http.client.HTTPConnection,
    deadline: float,
    aborted: dict[str, bool],
    tracked: dict[str, socket.socket | None],
) -> threading.Timer:
    delay = deadline - time.monotonic()
    timer = threading.Timer(max(0.0, delay), _abort_http_connection, args=(conn, aborted, tracked))
    timer.daemon = True
    timer.start()
    return timer


def _arm_http_timeout(
    conn: http.client.HTTPConnection,
    remaining: float,
    tracked: dict[str, socket.socket | None] | None = None,
) -> None:
    conn.timeout = remaining
    seen: set[int] = set()
    for sock in (
        getattr(conn, "sock", None),
        None if tracked is None else tracked.get("sock"),
    ):
        if not isinstance(sock, socket.socket):
            continue
        identity = id(sock)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            sock.settimeout(remaining)
        except OSError:
            pass


def _gcloud(args: Sequence[str], runner: _Runner, *, timeout: float) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    try:
        result = runner(
            ("gcloud", "storage", *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise Ux1AsyncError("UX1_GCS_REJECTED") from None
    except (FileNotFoundError, OSError):
        raise Ux1AsyncError("UX1_GCS_FAILED") from None
    if result.returncode != 0 or len(result.stdout) > PREVIEW_STDOUT_LIMIT:
        raise Ux1AsyncError("UX1_GCS_FAILED")
    return result


def _check_deadline(clock: _Clock, started: float) -> float:
    remaining = STAGING_DEADLINE_SECONDS - (clock() - started)
    if remaining <= 0:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    return remaining


def _metadata_size(value: object) -> int:
    if isinstance(value, bool):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        number = int(value)
    else:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if number <= 0 or number > OBJECT_BYTES_LIMIT:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    return number


def _metadata_generation(value: object) -> str:
    if isinstance(value, bool):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    text = str(value) if isinstance(value, int) else value
    if not isinstance(text, str) or _RUN_ID.fullmatch(text) is None:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    return text


def _prepare_file(root: Path, relative: str) -> Path:
    _safe_relative(relative)
    if root.is_symlink() or not root.is_dir():
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            raise Ux1AsyncError("UX1_GCS_REJECTED")
    if current.exists():
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    parent = current.parent
    if parent.is_symlink():
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    parent.mkdir(parents=True, exist_ok=True)
    parent.chmod(0o700)
    return current


def _download(
    url: str,
    destination: Path,
    *,
    bucket: str,
    object_name: str,
    expected_bytes: int | None,
    runner: _Runner,
    clock: _Clock,
    started: float,
    usage: dict[str, int],
) -> None:
    timeout = _check_deadline(clock, started)
    if usage["count"] >= OBJECT_COUNT_LIMIT:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    described = _gcloud(("objects", "describe", url, "--format=json"), runner, timeout=timeout)
    try:
        metadata = json.loads(described.stdout)
    except json.JSONDecodeError:
        raise Ux1AsyncError("UX1_GCS_FAILED") from None
    if not isinstance(metadata, dict):
        raise Ux1AsyncError("UX1_GCS_FAILED")
    if metadata.get("bucket") not in {None, bucket} or metadata.get("name") not in {None, object_name}:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    size = _metadata_size(metadata.get("size"))
    generation = _metadata_generation(metadata.get("generation"))
    if expected_bytes is not None and size != expected_bytes:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if usage["bytes"] + size > TOTAL_BYTES_LIMIT:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    usage["count"] += 1
    usage["bytes"] += size
    timeout = _check_deadline(clock, started)
    _gcloud(("cp", "--no-clobber", f"{url}#{generation}", str(destination)), runner, timeout=timeout)
    if destination.is_symlink() or not destination.is_file():
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    if destination.stat().st_size != size:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    destination.chmod(0o644)


def _source_objects(policy_path: Path) -> list[tuple[str, int]]:
    digest = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    if digest != BOXX_POLICY_SHA256:
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise Ux1AsyncError("UX1_GCS_REJECTED") from None
    raw = policy.get("source_objects") if isinstance(policy, dict) else None
    if not isinstance(raw, dict):
        raise Ux1AsyncError("UX1_GCS_REJECTED")
    selected = []
    for name, spec in raw.items():
        relative = _safe_relative(name)
        if (
            not isinstance(spec, dict)
            or isinstance(spec.get("bytes"), bool)
            or not isinstance(spec.get("bytes"), int)
            or spec["bytes"] <= 0
            or spec["bytes"] > OBJECT_BYTES_LIMIT
        ):
            raise Ux1AsyncError("UX1_GCS_REJECTED")
        selected.append((relative, spec["bytes"]))
    return selected


def stage_ux1_inputs(
    *,
    raw_prefix: str,
    r6_prefix: str,
    materialized_uri: str,
    bucket: str,
    destination: Path,
    runner: _Runner,
    clock: _Clock = time.monotonic,
) -> tuple[Path, Path, Path]:
    """Copy the fixed UX1 case into ``destination`` using gcloud storage."""

    checked_bucket = _require_bucket(bucket)
    raw_prefix = raw_prefix.rstrip("/")
    r6_prefix = r6_prefix.rstrip("/")
    _object_name(raw_prefix + "/manifest.json", checked_bucket)
    _object_name(r6_prefix + "/complete.json", checked_bucket)
    materialized_name = _object_name(materialized_uri, checked_bucket)
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    raw_root = destination / "raw"
    r6_root = destination / "r6"
    raw_root.mkdir(mode=0o700)
    r6_root.mkdir(mode=0o700)
    started = clock()
    usage = {"count": 0, "bytes": 0}
    fixed = set(RAW_FIXED_RELATIVE)

    def fetch(
        root: Path,
        prefix: str,
        relative: str,
        expected_bytes: int | None,
        staged_as: str | None = None,
    ) -> None:
        relative = _safe_relative(relative)
        local_name = relative if staged_as is None else _safe_relative(staged_as)
        url = f"{prefix}/{relative}"
        object_name = _object_name(url, checked_bucket)
        if object_name != relative and not object_name.endswith("/" + relative):
            raise Ux1AsyncError("UX1_GCS_REJECTED")
        _download(
            url,
            _prepare_file(root, local_name),
            bucket=checked_bucket,
            object_name=object_name,
            expected_bytes=expected_bytes,
            runner=runner,
            clock=clock,
            started=started,
            usage=usage,
        )

    fetch(raw_root, raw_prefix, BOXX_POLICY_NAME, None)
    for relative, expected_bytes in _source_objects(raw_root / BOXX_POLICY_NAME):
        existing = raw_root / relative
        if existing.is_symlink():
            raise Ux1AsyncError("UX1_GCS_REJECTED")
        if relative == BOXX_POLICY_NAME or existing.is_file():
            continue
        fetch(raw_root, raw_prefix, relative, None if relative in fixed else expected_bytes)
    for relative in RAW_FIXED_RELATIVE:
        if relative == BOXX_POLICY_NAME or (raw_root / relative).is_file():
            continue
        fetch(raw_root, raw_prefix, relative, None)
    for object_relative, local_name in R6_CASE_LAYOUT:
        fetch(r6_root, r6_prefix, object_relative, None, local_name)
    materialized_root = destination / "materialized"
    materialized_root.mkdir(mode=0o700)
    materialized_path = _prepare_file(materialized_root, Path(materialized_name).name)
    _download(
        materialized_uri,
        materialized_path,
        bucket=checked_bucket,
        object_name=materialized_name,
        expected_bytes=None,
        runner=runner,
        clock=clock,
        started=started,
        usage=usage,
    )
    return raw_root, r6_root, materialized_path


def _reject_json_constant(_name: str) -> None:
    raise Ux1AsyncError("UX1_CALCULATOR_FAILED")


def classify_preview(stdout: bytes) -> tuple[dict | None, str | None]:
    if len(stdout) > PREVIEW_STDOUT_LIMIT:
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
    try:
        text = stdout.decode("utf-8")
    except UnicodeError:
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED") from None
    if text.endswith("\n"):
        text = text[:-1]
    try:
        parsed = json.loads(text, parse_constant=_reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, Ux1AsyncError):
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED") from None
    if (
        not isinstance(parsed, dict)
        or parsed.get("schema") != RESULT_SCHEMA
        or parsed.get("no_order") is not True
        or parsed.get("execution_authority_granted") is not False
    ):
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
    status = parsed.get("status")
    if status == "ok":
        return parsed, None
    if status == "missing_input":
        return None, "input_unavailable"
    raise Ux1AsyncError("UX1_CALCULATOR_FAILED")


def _read_pipe(stream: object) -> bytes | None:
    try:
        chunk = os.read(stream.fileno(), 65536)  # type: ignore[attr-defined]
    except BlockingIOError:
        return b""
    except OSError:
        return None
    if not chunk:
        return None
    return chunk


def _reap_preview(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _run_preview_process(argv: Sequence[str], payload: bytes, env: Mapping[str, str], *, timeout: float) -> bytes:
    if len(argv) != 1 or Path(argv[0]).name != "ux1-preview":
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        shell=False,
    )
    stdin = proc.stdin
    stdout = proc.stdout
    stderr = proc.stderr
    assert stdin is not None and stdout is not None and stderr is not None
    for stream in (stdin, stdout, stderr):
        os.set_blocking(stream.fileno(), False)
    pending = payload
    out = bytearray()
    deadline = time.monotonic() + timeout
    stdout_open = True
    stderr_open = True
    try:
        while stdout_open or stderr_open or pending:
            if len(out) > PREVIEW_STDOUT_LIMIT:
                proc.kill()
                raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
            readers = []
            if stdout_open:
                readers.append(stdout)
            if stderr_open:
                readers.append(stderr)
            writers = [stdin] if pending else []
            readable, writable, _ = select.select(readers, writers, [], remaining)
            if not readable and not writable:
                proc.kill()
                raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
            if stdin in writable and pending:
                try:
                    written = os.write(stdin.fileno(), pending)
                except (BrokenPipeError, BlockingIOError, OSError):
                    pending = b""
                else:
                    pending = pending[written:]
                    if written == 0:
                        pending = b""
                if not pending:
                    stdin.close()
            if stdout in readable:
                chunk = _read_pipe(stdout)
                if chunk is None:
                    stdout_open = False
                elif chunk:
                    out.extend(chunk)
                    if len(out) > PREVIEW_STDOUT_LIMIT:
                        proc.kill()
                        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
            if stderr in readable:
                chunk = _read_pipe(stderr)
                if chunk is None:
                    stderr_open = False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
        if proc.wait(timeout=remaining) != 0 or len(out) > PREVIEW_STDOUT_LIMIT:
            raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
        return bytes(out)
    except Ux1AsyncError:
        raise
    except subprocess.TimeoutExpired:
        proc.kill()
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED") from None
    except Exception:
        proc.kill()
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED") from None
    finally:
        _reap_preview(proc)
        for stream in (stdin, stdout, stderr):
            try:
                stream.close()
            except Exception:
                pass


def _default_preview(payload: bytes, env: Mapping[str, str]) -> bytes:
    binary = shutil.which("ux1-preview")
    if binary is None:
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
    return _run_preview_process([binary], payload, env, timeout=PREVIEW_TIMEOUT_SECONDS)


def _valid_success_result(result: object) -> bool:
    return (
        isinstance(result, dict)
        and result.get("schema") == RESULT_SCHEMA
        and result.get("no_order") is True
        and result.get("execution_authority_granted") is False
        and result.get("status") == "ok"
    )


def _require_terminal_pair(result: dict | None, error_code: str | None) -> None:
    if error_code is None:
        if not _valid_success_result(result):
            raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
        return
    if error_code not in {"input_unavailable", "calculator_failed"} or result is not None:
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")


def _callback_body(
    *,
    request_id: str,
    run_id: str,
    run_attempt: str,
    result: dict | None,
    error_code: str | None,
) -> bytes:
    _require_terminal_pair(result, error_code)
    body = {
        "error_code": error_code,
        "request_id": request_id,
        "result": result,
        "run_attempt": run_attempt,
        "run_id": run_id,
    }
    if set(body) != {"error_code", "request_id", "result", "run_attempt", "run_id"}:
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
    try:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError):
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED") from None
    if len(encoded) > HTTP_BODY_LIMIT:
        if result is None and error_code == "calculator_failed":
            raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
        return _callback_body(
            request_id=request_id,
            run_id=run_id,
            run_attempt=run_attempt,
            result=None,
            error_code="calculator_failed",
        )
    return encoded


def _parse_claim(body: bytes, *, request_id: str, run_id: str, run_attempt: str) -> dict | None:
    try:
        parsed = json.loads(body, parse_constant=_reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, Ux1AsyncError):
        raise Ux1AsyncError("UX1_HTTP_FAILED") from None
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        raise Ux1AsyncError("UX1_HTTP_FAILED")
    if (
        parsed.get("request_id") != request_id
        or parsed.get("run_id") != run_id
        or parsed.get("run_attempt") != run_attempt
    ):
        raise Ux1AsyncError("UX1_HTTP_FAILED")
    if parsed.get("claimed") is False:
        return None
    if parsed.get("claimed") is not True:
        raise Ux1AsyncError("UX1_HTTP_FAILED")
    request = parsed.get("request")
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
    return request


def _gcs_values(env: Mapping[str, str]) -> dict[str, str] | None:
    values = {key: env.get(key, "") for key in _GCS_KEYS}
    if any(not isinstance(item, str) or item == "" for item in values.values()):
        return None
    return values


def execute(
    env: Mapping[str, str],
    *,
    transport: _Transport | None = None,
    runner: _Runner | None = None,
    preview: _Preview | None = None,
    clock: _Clock = time.monotonic,
) -> str:
    attempt = env.get("GITHUB_RUN_ATTEMPT", "")
    if attempt != "1":
        if _RUN_ID.fullmatch(attempt) is None:
            return "UX1_JOB_UNCONFIGURED"
        return "UX1_RUN_ATTEMPT_REJECTED"
    try:
        origin = _origin(env.get("UX1_CONSOLE_URL"))
        token = _token(env.get("UX1_RESEARCH_JOB_TOKEN"))
        request_id = _request_id(env.get("UX1_REQUEST_ID"))
        run_id = _positive_id(env.get("GITHUB_RUN_ID"))
    except Ux1AsyncError as exc:
        return exc.code

    def default_transport(path: str, payload: bytes) -> bytes:
        return post_json(origin, path, token, payload, timeout=HTTP_TIMEOUT_SECONDS)

    send = transport or default_transport
    claim = _callback_ids(request_id, run_id, attempt)
    try:
        claim_body = send(CLAIM_PATH, claim)
    except Ux1AsyncError as exc:
        return exc.code
    try:
        request = _parse_claim(claim_body, request_id=request_id, run_id=run_id, run_attempt=attempt)
    except Ux1AsyncError as exc:
        if exc.code == "UX1_CALCULATOR_FAILED":
            return _post_terminal(
                send,
                request_id=request_id,
                run_id=run_id,
                run_attempt=attempt,
                result=None,
                error_code="calculator_failed",
            )
        return exc.code
    if request is None:
        return "UX1_CLAIM_NOT_GRANTED"

    result: dict | None = None
    error_code: str | None = None
    try:
        gcs = _gcs_values(env)
        if gcs is None:
            raise Ux1AsyncError("UX1_INPUT_UNAVAILABLE")
        gcloud_runner = runner or subprocess.run
        calculator = preview or _default_preview
        with tempfile.TemporaryDirectory(prefix="ux1-async-") as temporary:
            raw_root, r6_root, materialized = stage_ux1_inputs(
                raw_prefix=gcs["UX1_RAW_GCS_PREFIX"].rstrip("/"),
                r6_prefix=gcs["UX1_R6_GCS_PREFIX"].rstrip("/"),
                materialized_uri=gcs["UX1_MATERIALIZED_GCS_URI"],
                bucket=gcs["UX1_INPUT_BUCKET"],
                destination=Path(temporary),
                runner=gcloud_runner,
                clock=clock,
            )
            payload = json.dumps(
                request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
            if len(payload) > PREVIEW_STDOUT_LIMIT:
                raise Ux1AsyncError("UX1_CALCULATOR_FAILED")
            stdout = calculator(payload, build_child_env(raw_root, r6_root, materialized))
            result, error_code = classify_preview(stdout)
    except Ux1AsyncError as exc:
        result = None
        if exc.code in {"UX1_INPUT_UNAVAILABLE", "UX1_GCS_REJECTED", "UX1_GCS_FAILED"}:
            error_code = "input_unavailable"
        else:
            error_code = "calculator_failed"
    return _post_terminal(
        send,
        request_id=request_id,
        run_id=run_id,
        run_attempt=attempt,
        result=result,
        error_code=error_code,
    )


def _callback_ids(request_id: str, run_id: str, run_attempt: str) -> bytes:
    encoded = json.dumps(
        {"request_id": request_id, "run_attempt": run_attempt, "run_id": run_id},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(encoded) > HTTP_BODY_LIMIT:
        raise Ux1AsyncError("UX1_HTTP_FAILED")
    return encoded


def _post_terminal(
    send: _Transport,
    *,
    request_id: str,
    run_id: str,
    run_attempt: str,
    result: dict | None,
    error_code: str | None,
) -> str:
    _require_terminal_pair(result, error_code)
    payload = _callback_body(
        request_id=request_id,
        run_id=run_id,
        run_attempt=run_attempt,
        result=result,
        error_code=error_code,
    )
    send(RESULT_PATH, payload)
    if error_code is None:
        return "UX1_OK"
    if error_code == "input_unavailable":
        return "UX1_INPUT_UNAVAILABLE"
    return "UX1_CALCULATOR_FAILED"


def main() -> int:
    code = "UX1_CALCULATOR_FAILED"
    try:
        code = execute(os.environ)
    except Ux1AsyncError as exc:
        code = exc.code if _SAFE_CODE.fullmatch(exc.code) else "UX1_CALCULATOR_FAILED"
    except Exception:
        code = "UX1_CALCULATOR_FAILED"
    sys.stdout.write(code + "\n")
    return 0 if code == "UX1_OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
