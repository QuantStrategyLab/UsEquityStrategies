#!/usr/bin/env python3
"""Bounded read-only metadata inventory using the runner's existing identity."""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sysconfig
from pathlib import Path
import hashlib
import os
import re
import json
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

BUCKET = "qsl-research-evidence-831478360303"
OBJECT_PREFIX = "research/v2/input/"
PREFIX = f"gs://{BUCKET}/{OBJECT_PREFIX}"
MAX_OBJECTS = 5000
PAGE_SIZE = 500
MAX_PAGES = 20
MAX_RETRIES = 2
MATCHES = ("qqqm", "boxx", "regime", "taco", "crisis", "macro", "manifest", "schema", "catalog", "index")


def _list_page(
    credentials: object,
    token: str | None,
    limit: int,
    auth_request_factory: Callable[[], object] | None = None,
) -> dict[str, object]:
    if auth_request_factory is None:
        from google.auth.transport.requests import Request as AuthRequest

        auth_request_factory = AuthRequest
    query = {"prefix": OBJECT_PREFIX, "maxResults": limit,
             "fields": "nextPageToken,items(name,generation,size,md5Hash,crc32c,updated)"}
    if token:
        query["pageToken"] = token
    url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o?{urlencode(query)}"
    for attempt in range(MAX_RETRIES + 1):
        headers: dict[str, str] = {}
        credentials.before_request(auth_request_factory(), "GET", url, headers)
        try:
            with urlopen(Request(url, headers=headers), timeout=45) as response:
                page = json.load(response)
                if not isinstance(page, dict):
                    raise ValueError("LIST_FORMAT_UNRECOGNIZED")
                return page
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError("LIST_PERMISSION_DENIED") from None
            if exc.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES:
                raise ValueError("LIST_HTTP_FAILED") from None
        except (TimeoutError, URLError):
            if attempt == MAX_RETRIES:
                raise ValueError("LIST_TRANSPORT_FAILED") from None
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _match(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        raise ValueError("LIST_FORMAT_UNRECOGNIZED")
    name = item.get("name")
    if not isinstance(name, str) or not name.startswith(OBJECT_PREFIX):
        raise ValueError("OUT_OF_SCOPE_OBJECT")
    if not any(term in name.lower() for term in MATCHES):
        return None
    return {"uri": f"gs://{BUCKET}/{name}", "generation": item.get("generation"),
            "size": item.get("size"), "md5Hash": item.get("md5Hash"),
            "crc32c": item.get("crc32c"), "updated": item.get("updated")}


def discover(fetch_page: Callable[[str | None, int], dict[str, object]]) -> dict[str, object]:
    count = 0
    token: str | None = None
    matches: list[dict[str, object]] = []
    pages = 0
    while count < MAX_OBJECTS and pages < MAX_PAGES:
        limit = min(PAGE_SIZE, MAX_OBJECTS - count)
        page = fetch_page(token, limit)
        pages += 1
        items = page.get("items", [])
        if not isinstance(items, list) or len(items) > limit:
            raise ValueError("PAGE_BUDGET_OR_FORMAT_INVALID")
        count += len(items)
        for item in items:
            found = _match(item)
            if found is not None:
                matches.append(found)
        next_token = page.get("nextPageToken")
        if not next_token:
            token = None
            break
        if not isinstance(next_token, str) or next_token == token:
            raise ValueError("PAGE_TOKEN_INVALID")
        token = next_token
    return {"status": "RANGE_INCOMPLETE" if token else "LIST_COMPLETE",
            "scope": PREFIX, "objects_seen": count, "object_budget": MAX_OBJECTS,
            "page_size": PAGE_SIZE, "pages": pages, "page_budget": MAX_PAGES,
            "continuation_token_sha256": hashlib.sha256(token.encode()).hexdigest() if token else None,
            "matched_objects": matches, "raw_content_read_bytes": 0,
            "execution_authorized": False, "no_order": True}


# Exact mode is opt-in. These are the three reviewed existing repository variables,
# not grants or evidence of IAM/dataset authority. Drift requires a new review.
EXACT_IDENTITY_SHA256 = {
    "GCP_PROJECT_ID": "7a71985c426d22eb10abc8fb6eff4703beed3aa8158ea096836807be84c12d35",
    "GCP_WORKLOAD_IDENTITY_PROVIDER": "d8925f1e4bf727c243019cabf3e96995b213ed49b2959990a2e87bd59b45da0c",
    "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": "dc1c53ee0fa115cab5b31c240539da2330326e32b6223c4e905b81dd8de2b236"
}
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 16 * 1024
READ_ONLY_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"
EXACT_REASONS = {
    "EXACT_INPUT_INVALID", "EXACT_IDENTITY_INVALID", "EXACT_CONFIG_DIGEST_MISMATCH",
    "EXACT_AUTH_BINDING_INVALID", "EXACT_AUTH_FAILED",
    "EXACT_HTTP_FAILED", "EXACT_TRANSPORT_FAILED", "EXACT_ENCODING_INVALID",
    "EXACT_BYTE_LIMIT", "EXACT_METADATA_INVALID", "EXACT_SIZE_MISMATCH",
    "EXACT_HASH_MISMATCH", "EXACT_JSON_INVALID", "EXACT_READ_FAILED",
}


class _RedactedParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("EXACT_INPUT_INVALID")


SDK_PATH_ENV = "QSL_DISCOVERY_SDK_PATH"
RUNTIME_REASONS = {"SDK_PATH_INVALID", "SDK_STDLIB_ORIGIN_FAILED", "SDK_LIBRARY_ORIGIN_FAILED"}
SAFE_ERROR_CLASSES = {"TypeError", "ValueError", "KeyError", "OSError", "PermissionError",
                      "TimeoutError", "ImportError", "ModuleNotFoundError", "AttributeError", "RuntimeError"}


def _sdk_directory():
    value = os.environ.get(SDK_PATH_ENV)
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("SDK_PATH_INVALID")
    return path.resolve()


def _argparse_is_stdlib():
    origin = getattr(argparse, "__file__", None)
    roots = {Path(sysconfig.get_path(name)).resolve() for name in ("stdlib", "platstdlib")}
    return origin is not None and any(Path(origin).resolve() == root / "argparse.py" for root in roots)


def _bootstrap_sdk_runtime():
    """Keep stdlib before SDK, and the exact SDK before fallback site-packages.

    The workflow must start without an SDK-first PYTHONPATH. Reject a preloaded
    non-stdlib parser rather than weaken its strict option parsing or reload it.
    """
    sdk = _sdk_directory()
    if sdk is None:
        return
    if not _argparse_is_stdlib():
        raise ValueError("SDK_STDLIB_ORIGIN_FAILED")
    roots = [sysconfig.get_path(name) for name in ("stdlib", "platstdlib")]
    dynamic = sysconfig.get_config_var("DESTSHARED")
    if dynamic:
        roots.append(dynamic)
    zip_name = f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    parents = {Path(path).resolve().parent for path in roots[:2]}
    roots.extend(path for path in sys.path
                 if Path(path).name == zip_name and Path(path).resolve().parent in parents)
    standard = list(dict.fromkeys(str(Path(path).resolve()) for path in roots))
    remaining = [path for path in sys.path if str(Path(path).resolve()) not in {*standard, str(sdk)}]
    sys.path[:] = [*standard, str(sdk), *remaining]


def _ensure_sdk_libraries():
    # No identity fallback, module reload, credential construction or hook change.
    # Existing standalone callers without the workflow SDK retain their behavior.
    sdk = _sdk_directory()
    if sdk is None:
        return
    required = ("google.auth", "google.auth.identity_pool", "google.auth.transport.requests",
                "requests", "requests.adapters", "urllib3")
    for name in required:
        module = importlib.import_module(name)
        origin = getattr(module, "__file__", None)
        if origin is None or not Path(origin).resolve().is_relative_to(sdk):
            raise ValueError("SDK_LIBRARY_ORIGIN_FAILED")
    # Cached submodules can otherwise retain older credential/Session base classes
    # even when the top-level package came from the SDK. Exclude stdlib aliases.
    modules = [module for name, module in tuple(sys.modules.items())
               if name in required or name.startswith(("google.auth.", "google.oauth2.",
                                                       "requests.", "urllib3."))
               and not name.startswith(("requests.packages", "urllib3.packages"))]
    for module in modules:
        origin = getattr(module, "__file__", None)
        if origin is None or not Path(origin).resolve().is_relative_to(sdk):
            raise ValueError("SDK_LIBRARY_ORIGIN_FAILED")


def _runtime_parser_probe():
    """Compare both import orders in isolated processes without reading inputs/auth.

    Reuse the actual parser, not copied parsing rules. Capture all child output;
    return only fixed booleans/classes, never paths or exception messages.
    """
    sdk = _sdk_directory()
    if sdk is None:
        raise ValueError("SDK_PATH_INVALID")
    code = """
import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('discovery_probe',sys.argv[1])
module=importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
    standard=module._argparse_is_stdlib()
    if sys.argv[2]=='repaired':
        module._bootstrap_sdk_runtime()
        module._ensure_sdk_libraries()
    module._arguments([])
    result={'initialized':True,'stdlib':standard,'error_class':None,'sdk_origins':sys.argv[2]=='repaired'}
except Exception as exc:
    name=type(exc).__name__
    result={'initialized':False,'stdlib':locals().get('standard',False),'error_class':name if name in {'TypeError','ValueError','ImportError','ModuleNotFoundError','AttributeError','RuntimeError'} else 'RuntimeError','sdk_origins':False}
print(json.dumps(result,sort_keys=True))
"""
    results = {}
    for kind in ("legacy", "repaired"):
        env = os.environ.copy()
        env[SDK_PATH_ENV] = str(sdk)
        if kind == "legacy":
            env["PYTHONPATH"] = str(sdk)
        else:
            env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "-c", code, str(Path(__file__).resolve()), kind],
                                env=env, capture_output=True, text=True, timeout=30)
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError("SDK_PATH_INVALID")
        item = json.loads(result.stdout)
        if (set(item) != {"initialized", "stdlib", "error_class", "sdk_origins"}
                or any(type(item[key]) is not bool for key in ("initialized", "stdlib", "sdk_origins"))
                or item["error_class"] not in SAFE_ERROR_CLASSES | {None}):
            raise ValueError("SDK_PATH_INVALID")
        results[kind] = item
    return {"status": "RUNTIME_PARSER_PROBE",
            "legacy_parser_initialized": results["legacy"]["initialized"],
            "legacy_argparse_stdlib": results["legacy"]["stdlib"],
            "legacy_exception_class": results["legacy"]["error_class"],
            "repaired_parser_initialized": results["repaired"]["initialized"],
            "repaired_argparse_stdlib": results["repaired"]["stdlib"],
            "repaired_exception_class": results["repaired"]["error_class"],
            "repaired_sdk_library_origins": results["repaired"]["sdk_origins"],
            "auth_requested": False, "object_requested": False,
            "execution_authorized": False, "no_order": True}


def _identity_matches(name, value):
    return (isinstance(value, str)
            and hashlib.sha256(value.encode()).hexdigest() == EXACT_IDENTITY_SHA256[name])


def _validated_identity():
    identity = {name: os.environ.get(name) for name in EXACT_IDENTITY_SHA256}
    if not all(_identity_matches(name, value) for name, value in identity.items()):
        raise ValueError("EXACT_IDENTITY_INVALID")
    return identity


def _arguments(argv):
    parser = _RedactedParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--mode", choices=("discovery", "exact_manifest_metadata"), default="discovery")
    for name in ("manifest-object", "generation", "sha256", "max-manifest-bytes", "config-sha256"):
        parser.add_argument(f"--{name}")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--workflow-inputs", action="store_true")
    args = parser.parse_args(argv)
    if args.workflow_inputs:
        if args.mode != "discovery" or any(getattr(args, field) is not None for field in (
                "manifest_object", "generation", "sha256", "max_manifest_bytes", "config_sha256")):
            raise ValueError("EXACT_INPUT_INVALID")
        _workflow_inputs(args)
    fields = (args.manifest_object, args.generation, args.sha256,
              args.max_manifest_bytes, args.config_sha256)
    if args.mode == "discovery":
        if any(value is not None for value in fields):
            raise ValueError("EXACT_INPUT_INVALID")
        return args, None
    if not all(isinstance(value, str) and value for value in fields):
        raise ValueError("EXACT_INPUT_INVALID")
    if (not re.fullmatch(r"research/v2/input/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/manifest\.json",
                         args.manifest_object)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", args.generation)
            or int(args.generation) > 2**64 - 1
            or not re.fullmatch(r"[0-9a-f]{64}", args.sha256)
            or not re.fullmatch(r"[1-9][0-9]{0,6}", args.max_manifest_bytes)
            or not 1 <= int(args.max_manifest_bytes) <= MAX_MANIFEST_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", args.config_sha256)):
        raise ValueError("EXACT_INPUT_INVALID")
    identity = _validated_identity()
    config = {
        "mode": args.mode, "bucket": BUCKET, "manifest_object": args.manifest_object,
        "generation": args.generation, "manifest_sha256": args.sha256,
        "max_manifest_bytes": int(args.max_manifest_bytes),
        "project_id": identity["GCP_PROJECT_ID"],
        "workload_identity_provider": identity["GCP_WORKLOAD_IDENTITY_PROVIDER"],
        "service_account": identity["GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT"],
    }
    _validate_config(config)
    if _config_digest(config) != args.config_sha256:
        raise ValueError("EXACT_CONFIG_DIGEST_MISMATCH")
    return args, config


def _workflow_inputs(args):
    # Read typed dispatch inputs from the existing runner event, rather than job
    # env or interpolated shell arguments which GitHub can echo into logs.
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path or not os.path.isabs(path):
        raise ValueError("EXACT_INPUT_INVALID")
    with open(path, "rb") as stream:
        body = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(body) > MAX_MANIFEST_BYTES:
        raise ValueError("EXACT_INPUT_INVALID")
    event = _json_object(body)
    inputs = event.get("inputs", {})
    mapping = {"manifest_object": "manifest_object", "generation": "generation",
               "manifest_sha256": "sha256", "max_manifest_bytes": "max_manifest_bytes",
               "config_sha256": "config_sha256"}
    if (not isinstance(inputs, dict) or not set(inputs) <= set(mapping) | {"mode"}
            or any(not isinstance(value, str) for value in inputs.values())
            or inputs.get("mode", "discovery") not in ("discovery", "exact_manifest_metadata")):
        raise ValueError("EXACT_INPUT_INVALID")
    args.mode = inputs.get("mode", "discovery")
    for source, target in mapping.items():
        setattr(args, target, inputs.get(source) or None)


def _validate_config(config):
    # Revalidate the reusable read boundary, including malicious constructed input.
    expected_keys = {"mode", "bucket", "manifest_object", "generation", "manifest_sha256",
                     "max_manifest_bytes", "project_id", "workload_identity_provider", "service_account"}
    if (not isinstance(config, dict) or set(config) != expected_keys
            or config["mode"] != "exact_manifest_metadata" or config["bucket"] != BUCKET
            or not isinstance(config["manifest_object"], str)
            or not re.fullmatch(r"research/v2/input/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/manifest\.json",
                                config["manifest_object"])
            or not isinstance(config["generation"], str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", config["generation"])
            or int(config["generation"]) > 2**64 - 1
            or not isinstance(config["manifest_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", config["manifest_sha256"])
            or type(config["max_manifest_bytes"]) is not int
            or not 1 <= config["max_manifest_bytes"] <= MAX_MANIFEST_BYTES
            or not all(_identity_matches(name, config[field]) for name, field in (
                ("GCP_PROJECT_ID", "project_id"),
                ("GCP_WORKLOAD_IDENTITY_PROVIDER", "workload_identity_provider"),
                ("GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT", "service_account")))):
        raise ValueError("EXACT_INPUT_INVALID")


def _config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _json_object(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("EXACT_JSON_INVALID")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("EXACT_JSON_INVALID")

    try:
        result = json.loads(body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(result, dict):
            raise ValueError("EXACT_JSON_INVALID")
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("EXACT_JSON_INVALID") from None


class _ExactHttpFailure(ValueError):
    """Safe diagnostic only; never retain a response body, URL or headers."""

    def __init__(self, status):
        super().__init__("EXACT_HTTP_FAILED")
        self.http_status = status if type(status) is int and 100 <= status <= 599 else None


def _get_bytes(session, url, params, limit):
    """One GET, with bounded decoded bytes; caller MUST impose a process deadline.

    The workflow uses GNU timeout (180s plus 5s kill grace) across auth and both
    reads. A 45s socket timeout alone cannot stop a slow-dribble response.
    """
    response = None
    try:
        response = session.get(url, params=params, stream=True, allow_redirects=False,
                               timeout=45, headers={"Accept-Encoding": "identity"})
        if response.status_code != 200:
            raise _ExactHttpFailure(response.status_code)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("EXACT_ENCODING_INVALID")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=4096):
            if not isinstance(chunk, bytes) or len(body) + len(chunk) > limit:
                raise ValueError("EXACT_BYTE_LIMIT")
            body.extend(chunk)
        return bytes(body)
    except ValueError as exc:
        if str(exc) in EXACT_REASONS:
            raise
        raise ValueError("EXACT_TRANSPORT_FAILED") from None
    except Exception:
        raise ValueError("EXACT_TRANSPORT_FAILED") from None
    finally:
        if response is not None:
            response.close()


def read_exact_manifest(config, session):
    """Read only one reviewed manifest revision; session must disable storage replay.

    No manifest format has yet been authenticated. Therefore all schema/claim
    statuses stay unknown/false, even if arbitrary manifest text asserts them.
    No referenced object, raw manifest, path or provider/contact value is emitted.
    """
    _validate_config(config)
    url = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/{quote(config['manifest_object'], safe='')}"
    meta = _json_object(_get_bytes(session, url, {
        "generation": config["generation"], "fields": "name,generation,size,contentType,updated",
    }, MAX_METADATA_BYTES))
    size = meta.get("size")
    if (meta.get("name") != config["manifest_object"]
            or meta.get("generation") != config["generation"]
            or not isinstance(size, str) or not re.fullmatch(r"[1-9][0-9]{0,6}", size)
            or int(size) > config["max_manifest_bytes"]):
        raise ValueError("EXACT_METADATA_INVALID")
    body = _get_bytes(session, url, {"generation": config["generation"], "alt": "media"},
                      config["max_manifest_bytes"])
    if len(body) != int(size):
        raise ValueError("EXACT_SIZE_MISMATCH")
    if hashlib.sha256(body).hexdigest() != config["manifest_sha256"]:
        raise ValueError("EXACT_HASH_MISMATCH")
    _json_object(body)  # Validate only after complete length and hash match.
    return {
        "status": "MANIFEST_METADATA_VERIFIED", "mode": "exact_manifest_metadata",
        "run_config_sha256": _config_digest(config),
        "requested_generation": config["generation"], "matched_generation": meta["generation"],
        "manifest_sha256": config["manifest_sha256"], "manifest_bytes": len(body),
        "max_manifest_bytes": config["max_manifest_bytes"], "schema_status": "unknown",
        "recognized_calendar_claim_present": False, "recognized_source_claim_present": False,
        "recognized_license_claim_present": False, "metadata_read_only": True,
        "execution_authorized": False, "no_order": True, "dataset_license_verified": False,
        "calendar_authority_verified": False, "historical_available_at_verified": False,
    }


def _bound_wif_info():
    path = os.environ.get("EXACT_AUTH_CREDENTIALS_FILE", "")
    if (not path or not os.path.isabs(path)
            or path != os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            or os.path.islink(path) or not os.path.isfile(path)):
        raise ValueError("EXACT_AUTH_BINDING_INVALID")
    with open(path, "rb") as stream:
        body = stream.read(65537)
    if len(body) > 65536:
        raise ValueError("EXACT_AUTH_BINDING_INVALID")
    info = _json_object(body)
    identity = _validated_identity()
    provider = identity["GCP_WORKLOAD_IDENTITY_PROVIDER"]
    service_account = identity["GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT"]
    impersonation_url = (
        "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
        f"{service_account}:generateAccessToken"
    )
    source = info.get("credential_source", {})
    required_fields = {"type", "audience", "subject_token_type", "token_url",
                       "service_account_impersonation_url", "credential_source"}
    if (not required_fields <= set(info)
            or not set(info) <= required_fields | {"universe_domain"}
            or info.get("type") != "external_account"
            or info.get("audience") != f"//iam.googleapis.com/{provider}"
            or info.get("token_url") != "https://sts.googleapis.com/v1/token"
            or info.get("subject_token_type") != "urn:ietf:params:oauth:token-type:jwt"
            or info.get("service_account_impersonation_url") != impersonation_url
            or info.get("universe_domain", "googleapis.com") != "googleapis.com"
            or not isinstance(source, dict)):
        raise ValueError("EXACT_AUTH_BINDING_INVALID")
    # The subject source must be the runner's existing OIDC endpoint/authorization,
    # not an operator-selected URL, local file, executable, or alternate identity.
    source_url = source.get("url")
    runner_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    runner_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if not isinstance(source_url, str) or not runner_url or not runner_token:
        raise ValueError("EXACT_AUTH_BINDING_INVALID")
    parsed, runner = urlsplit(source_url), urlsplit(runner_url)
    source_query = parse_qsl(parsed.query, keep_blank_values=True)
    expected_query = [(key, value) for key, value in parse_qsl(runner.query, keep_blank_values=True)
                      if key != "audience"]
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment
            or (parsed.scheme, parsed.netloc, parsed.path) != (runner.scheme, runner.netloc, runner.path)
            or [(key, value) for key, value in source_query if key != "audience"] != expected_query
            or [value for key, value in source_query if key == "audience"] != [
                f"https://iam.googleapis.com/{provider}"]
            or source.get("headers") != {"Authorization": f"Bearer {runner_token}"}
            or source.get("format") != {"type": "json", "subject_token_field_name": "value"}
            or set(source) != {"url", "headers", "format"}):
        raise ValueError("EXACT_AUTH_BINDING_INVALID")
    return info


@contextmanager
def _exact_session(config):
    # Parsing/identity binding precedes any auth request. Avoid google.auth.default
    # and load_credentials_from_file: the latter can discover the project remotely.
    _validate_config(config)
    _ensure_sdk_libraries()
    info = _bound_wif_info()
    from google.auth import identity_pool
    from google.auth.transport.requests import AuthorizedSession, Request as AuthRequest
    import requests
    from requests.adapters import HTTPAdapter

    class NoRedirectSession(requests.Session):
        def request(self, *args, **kwargs):
            kwargs["allow_redirects"] = False
            return super().request(*args, **kwargs)

    auth_http = NoRedirectSession()
    auth_http.mount("https://", HTTPAdapter(max_retries=0))
    base_request = AuthRequest(session=auth_http)

    def auth_request(url, method="GET", **kwargs):
        # Preserve native same-principal IAM regional/trust-boundary policy checks
        # and their headers. Their auth-security reads/retries are separate from
        # the two storage GETs. Native lookup failure can omit the policy header;
        # this mode does not prove effective geographic policy enforcement.
        kwargs.setdefault("timeout", 45)  # Preserve stricter native security-check timeouts.
        return base_request(url=url, method=method, **kwargs)

    credentials = identity_pool.Credentials.from_info(info, scopes=[READ_ONLY_SCOPE])
    session = AuthorizedSession(credentials, refresh_status_codes=(), max_refresh_attempts=0,
                                auth_request=auth_request)
    session.mount("https://", HTTPAdapter(max_retries=0))
    try:
        yield session
    finally:
        session.close()
        auth_http.close()


def main(argv: Sequence[str] | None = None) -> int:
    mode = "unresolved"
    phase = "runtime_bootstrap"
    try:
        if argv == ["--runtime-parser-probe"]:
            payload = _runtime_parser_probe()
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            return 0
        _bootstrap_sdk_runtime()
        phase = "argument_validation"
        args, config = _arguments([] if argv is None else argv)
        mode = args.mode
        phase = "execution"
        if args.preflight:
            payload = {"status": "EXACT_PREFLIGHT_VALIDATED" if config else "DISCOVERY_PREFLIGHT_VALIDATED",
                       "mode": mode, "execution_authorized": False, "no_order": True}
            if config:
                payload["run_config_sha256"] = _config_digest(config)
        elif mode == "exact_manifest_metadata":
            phase = "sdk_libraries"
            _ensure_sdk_libraries()
            phase = "execution"
            with _exact_session(config) as session:
                payload = read_exact_manifest(config, session)
        else:
            phase = "sdk_libraries"
            _ensure_sdk_libraries()
            phase = "execution"
            import google.auth

            credentials, _ = google.auth.default(scopes=[READ_ONLY_SCOPE])
            payload = discover(lambda token, limit: _list_page(credentials, token, limit))
        code = 0
    except Exception as exc:
        known = {"LIST_FORMAT_UNRECOGNIZED", "LIST_PERMISSION_DENIED", "LIST_HTTP_FAILED",
                 "LIST_TRANSPORT_FAILED", "OUT_OF_SCOPE_OBJECT", "PAGE_BUDGET_OR_FORMAT_INVALID",
                 "PAGE_TOKEN_INVALID"} | EXACT_REASONS | RUNTIME_REASONS
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in known else (
            "SDK_RUNTIME_FAILED" if phase in {"runtime_bootstrap", "sdk_libraries"} else
            "ARGUMENT_RUNTIME_FAILED" if phase == "argument_validation" else
            "EXACT_READ_FAILED" if mode == "exact_manifest_metadata" else "LIST_FAILED")
        payload = {"status": "PARKED", "reason_code": reason,
                   "execution_authorized": False, "no_order": True}
        if (reason in RUNTIME_REASONS or reason in {"SDK_RUNTIME_FAILED", "ARGUMENT_RUNTIME_FAILED"}):
            payload["failure_phase"] = "sdk_library_origin" if reason == "SDK_LIBRARY_ORIGIN_FAILED" else phase
            name = type(exc).__name__
            payload["error_class"] = name if name in SAFE_ERROR_CLASSES else "RuntimeError"
        if (mode == "exact_manifest_metadata" and isinstance(exc, _ExactHttpFailure)
                and type(exc.http_status) is int and 100 <= exc.http_status <= 599):
            payload["http_status"] = exc.http_status
        if mode == "discovery" and not reason.startswith("EXACT_") and reason not in RUNTIME_REASONS:
            payload["scope"] = PREFIX
        code = 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
