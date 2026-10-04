from urllib.error import HTTPError

import pytest

from scripts import discover_research_v2_inputs as discovery


def test_inventory_resumes_empty_page_and_filters():
    requests = []
    pages = [
        {"items": [], "nextPageToken": "a"},
        {"items": [{"name": "research/v2/input/set/qqqm/prices.csv", "size": "123"},
                   {"name": "research/v2/input/set/unrelated.bin"}]},
    ]

    def fetch(token, limit):
        requests.append((token, limit))
        return pages.pop(0)

    result = discovery.discover(fetch)
    assert requests == [(None, discovery.PAGE_SIZE), ("a", discovery.PAGE_SIZE)]
    assert result["status"] == "LIST_COMPLETE"
    assert result["objects_seen"] == 2
    assert [item["uri"] for item in result["matched_objects"]] == [
        "gs://qsl-research-evidence-831478360303/research/v2/input/set/qqqm/prices.csv"]


def test_hard_budget_keeps_cursor():
    limits = []

    def fetch(token, limit):
        limits.append(limit)
        return {"items": [{"name": "research/v2/input/x"}] * limit,
                "nextPageToken": str(len(limits))}

    result = discovery.discover(fetch)
    assert result["status"] == "RANGE_INCOMPLETE"
    assert result["objects_seen"] == discovery.MAX_OBJECTS
    assert limits == [discovery.PAGE_SIZE] * (discovery.MAX_OBJECTS // discovery.PAGE_SIZE)
    assert result["continuation_token_sha256"] is not None


def test_reject_out_of_scope_item():
    with pytest.raises(ValueError, match="OUT_OF_SCOPE_OBJECT"):
        discovery.discover(lambda _token, _limit: {"items": [{"name": "other/path/qqqm.csv"}]})


def test_permission_denied_has_no_retry(monkeypatch):
    calls = []

    class Credentials:
        def before_request(self, _request, _method, _url, _headers):
            pass

    def deny(_request, timeout):
        calls.append(timeout)
        raise HTTPError("https://storage.googleapis.com", 403, "forbidden", {}, None)

    monkeypatch.setattr(discovery, "urlopen", deny)
    with pytest.raises(ValueError, match="LIST_PERMISSION_DENIED"):
        discovery._list_page(Credentials(), None, 1, object)
    assert len(calls) == 1


def test_transient_http_error_retries_twice(monkeypatch):
    calls = []

    class Credentials:
        def before_request(self, _request, _method, _url, _headers):
            pass

    def unavailable(_request, timeout):
        calls.append(timeout)
        raise HTTPError("https://storage.googleapis.com", 503, "unavailable", {}, None)

    monkeypatch.setattr(discovery, "urlopen", unavailable)
    monkeypatch.setattr(discovery.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="LIST_HTTP_FAILED"):
        discovery._list_page(Credentials(), None, 1, object)
    assert len(calls) == 3


# Synthetic manifest only. No historical private pointer or provider data.
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace


CONFIG_IDENTITY = {
    "GCP_PROJECT_ID": "synthetic-project",
    "GCP_WORKLOAD_IDENTITY_PROVIDER": (
        "projects/123456789012/locations/global/workloadIdentityPools/"
        "synthetic-pool/providers/synthetic-provider"
    ),
    "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": (
        "synthetic-reader@synthetic-project.iam.gserviceaccount.com"
    ),
}


@pytest.fixture(autouse=True)
def synthetic_identity_pins(monkeypatch):
    # Only synthetic identifiers are serialized into public tests. Production
    # validates the separately frozen hashes of its three existing repo vars.
    monkeypatch.setattr(discovery, "EXACT_IDENTITY_SHA256", {
        name: hashlib.sha256(value.encode()).hexdigest() for name, value in CONFIG_IDENTITY.items()
    })
    for name, value in CONFIG_IDENTITY.items():
        monkeypatch.setenv(name, value)

MANIFEST_OBJECT = "research/v2/input/synthetic-fixture/manifest.json"
MANIFEST = b'{"schema":"unreviewed","contact":"private","prices":"never-read.csv"}'


def exact_config(**changes):
    config = {"mode": "exact_manifest_metadata", "bucket": discovery.BUCKET,
              "manifest_object": MANIFEST_OBJECT, "generation": "123456789",
              "manifest_sha256": hashlib.sha256(MANIFEST).hexdigest(),
              "max_manifest_bytes": 1024,
              "project_id": CONFIG_IDENTITY["GCP_PROJECT_ID"],
              "workload_identity_provider": CONFIG_IDENTITY["GCP_WORKLOAD_IDENTITY_PROVIDER"],
              "service_account": CONFIG_IDENTITY["GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT"]}
    config.update(changes)
    return config


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def exact_argv(config=None, **changes):
    config = exact_config(**changes) if config is None else config
    return ["--mode", "exact_manifest_metadata", "--manifest-object", config["manifest_object"],
            "--generation", config["generation"], "--sha256", config["manifest_sha256"],
            "--max-manifest-bytes", str(config["max_manifest_bytes"]),
            "--config-sha256", config_digest(config)]


class Response:
    def __init__(self, body, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = {} if headers is None else headers
        self.body = body
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == 4096
        yield from self.chunks if self.chunks is not None else [self.body]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


def metadata(**changes):
    data = {"name": MANIFEST_OBJECT, "generation": "123456789", "size": str(len(MANIFEST))}
    data.update(changes)
    return json.dumps(data).encode()


def test_exact_success_same_generation_two_gets_redacted():
    responses = [Response(metadata()), Response(MANIFEST)]
    session = Session(responses)
    config = exact_config()
    result = discovery.read_exact_manifest(config, session)
    assert len(session.calls) == 2
    for url, kwargs in session.calls:
        assert url.startswith(f"https://storage.googleapis.com/storage/v1/b/{discovery.BUCKET}/o/")
        assert "synthetic-fixture%2Fmanifest.json" in url
        assert kwargs["params"]["generation"] == config["generation"]
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert kwargs["timeout"] == 45
        assert kwargs["headers"] == {"Accept-Encoding": "identity"}
    assert session.calls[0][1]["params"]["fields"] == "name,generation,size,contentType,updated"
    assert session.calls[1][1]["params"]["alt"] == "media"
    assert result == {
        "status": "MANIFEST_METADATA_VERIFIED", "mode": "exact_manifest_metadata",
        "run_config_sha256": config_digest(config), "requested_generation": "123456789",
        "matched_generation": "123456789", "manifest_sha256": config["manifest_sha256"],
        "manifest_bytes": len(MANIFEST), "max_manifest_bytes": 1024,
        "schema_status": "unknown", "recognized_calendar_claim_present": False,
        "recognized_source_claim_present": False, "recognized_license_claim_present": False,
        "metadata_read_only": True, "execution_authorized": False, "no_order": True,
        "dataset_license_verified": False, "calendar_authority_verified": False,
        "historical_available_at_verified": False,
    }
    assert all(response.closed for response in responses)
    text = json.dumps(result)
    for secret in (MANIFEST_OBJECT, "private", "never-read", "unreviewed", "https://"):
        assert secret not in text


@pytest.mark.parametrize("field,bad", [
    ("manifest_object", "gs://bucket/research/v2/input/set/manifest.json"),
    ("manifest_object", "research/v2/input/../manifest.json"),
    ("manifest_object", "research/v2/input/a/b/manifest.json"),
    ("manifest_object", "research/v2/input/a%2Fb/manifest.json"),
    ("manifest_object", "research/v2/input/set/prices.csv"),
    ("manifest_object", "research/v2/input/set/manifest.json?x=y"),
    ("manifest_object", "research/v2/input/set/manifest.json\n"),
    ("generation", ""), ("generation", "0"), ("generation", "0123"),
    ("generation", "latest"), ("generation", "1.0"), ("generation", "9" * 21),
    ("manifest_sha256", "A" * 64), ("manifest_sha256", "a" * 63),
    ("max_manifest_bytes", 0), ("max_manifest_bytes", 1048577),
])
def test_exact_bad_scope_pre_auth(monkeypatch, capsys, field, bad):
    monkeypatch.setattr(discovery, "_exact_session", lambda _: pytest.fail("AUTH_CALLED"))
    monkeypatch.setenv("GCP_PROJECT_ID", CONFIG_IDENTITY["GCP_PROJECT_ID"])
    for key, value in CONFIG_IDENTITY.items():
        monkeypatch.setenv(key, value)
    assert discovery.main(exact_argv(**{field: bad}) + ["--preflight"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PARKED"
    assert "run_config_sha256" not in output
    assert MANIFEST_OBJECT not in json.dumps(output)


@pytest.mark.parametrize("key", list(CONFIG_IDENTITY))
@pytest.mark.parametrize("value", [None, "different-identity"])
def test_identity_missing_or_changed_pre_auth(monkeypatch, capsys, key, value):
    for name, expected in CONFIG_IDENTITY.items():
        monkeypatch.setenv(name, expected)
    if value is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(discovery, "_exact_session", lambda _: pytest.fail("AUTH_CALLED"))
    assert discovery.main(exact_argv() + ["--preflight"]) == 2
    assert json.loads(capsys.readouterr().out)["reason_code"] == "EXACT_IDENTITY_INVALID"


def test_operator_config_digest_and_preflight(monkeypatch, capsys):
    for name, value in CONFIG_IDENTITY.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(discovery, "_exact_session", lambda _: pytest.fail("AUTH_CALLED"))
    assert discovery.main(exact_argv() + ["--preflight"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["run_config_sha256"] == config_digest(exact_config())
    assert result["status"] == "EXACT_PREFLIGHT_VALIDATED"
    args = exact_argv()
    args[-1] = "a" * 64
    assert discovery.main(args + ["--preflight"]) == 2
    assert json.loads(capsys.readouterr().out)["reason_code"] == "EXACT_CONFIG_DIGEST_MISMATCH"


@pytest.mark.parametrize("args", [
    ["--manifest-object", MANIFEST_OBJECT],
    ["--mode", "exact_manifest_metadata"], ["--mode", "wrong"],
    ["--mode", "exact_manifest_metadata", "--private-unknown", "sensitive-url"],
])
def test_no_exact_fields_or_fallback_in_discovery(monkeypatch, capsys, args):
    monkeypatch.setattr(discovery, "discover", lambda _: pytest.fail("DISCOVERY_CALLED"))
    assert discovery.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PARKED"
    assert "sensitive-url" not in json.dumps(result)


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 404, 429, 500, 502, 503, 504])
@pytest.mark.parametrize("phase", [0, 1])
def test_exact_http_failure_never_retries(status, phase):
    responses = [Response(metadata())] * phase + [Response(b"private", status=status)]
    session = Session(responses)
    with pytest.raises(ValueError, match="EXACT_HTTP_FAILED"):
        discovery.read_exact_manifest(exact_config(), session)
    assert len(session.calls) == phase + 1
    assert all(response.closed for response in responses)


@pytest.mark.parametrize("phase", [0, 1])
def test_exact_transport_failure_never_retries(phase):
    session = Session([Response(metadata())] * phase + [TimeoutError("sensitive-url")])
    with pytest.raises(ValueError, match="EXACT_TRANSPORT_FAILED"):
        discovery.read_exact_manifest(exact_config(), session)
    assert len(session.calls) == phase + 1


@pytest.mark.parametrize("body", [
    metadata(generation="123456788"), metadata(name="other/manifest.json"),
    metadata(size="1048577"), metadata(size="0123"), metadata(size=42),
    b'{"name":"a","name":"b"}', b"[]", b"x" * 16385,
])
def test_metadata_invalid_stops_before_media(body):
    session = Session([Response(body)])
    with pytest.raises(ValueError, match="EXACT_"):
        discovery.read_exact_manifest(exact_config(), session)
    assert len(session.calls) == 1


@pytest.mark.parametrize("body", [b"[]", b'"text"', b'{"a":1,"a":2}', b'{"a":NaN}', b"\xff", b"{"])
def test_verified_but_invalid_json_withholds_metadata(body):
    config = exact_config(manifest_sha256=hashlib.sha256(body).hexdigest())
    session = Session([Response(metadata(size=str(len(body)))), Response(body)])
    with pytest.raises(ValueError, match="EXACT_JSON_INVALID"):
        discovery.read_exact_manifest(config, session)
    assert len(session.calls) == 2


@pytest.mark.parametrize("body,reason", [
    (MANIFEST + b" ", "EXACT_SIZE_MISMATCH"),
    (MANIFEST[:-1], "EXACT_SIZE_MISMATCH"),
    (b"x" * len(MANIFEST), "EXACT_HASH_MISMATCH"),
    (b"x" * 1025, "EXACT_BYTE_LIMIT"),
])
def test_media_limits_and_hash(body, reason):
    session = Session([Response(metadata()), Response(body)])
    with pytest.raises(ValueError, match=reason):
        discovery.read_exact_manifest(exact_config(), session)


def test_chunked_limit_and_encoding_no_unbounded_read():
    response = Response(b"", chunks=[b"x" * 512, b"y" * 512, b"z"])
    session = Session([Response(metadata()), response])
    with pytest.raises(ValueError, match="EXACT_BYTE_LIMIT"):
        discovery.read_exact_manifest(exact_config(), session)
    assert response.closed
    session = Session([Response(metadata(), headers={"Content-Encoding": "gzip"})])
    with pytest.raises(ValueError, match="EXACT_ENCODING_INVALID"):
        discovery.read_exact_manifest(exact_config(), session)
    assert len(session.calls) == 1


def test_workflow_preflight_auth_binding_and_outer_deadline():
    workflow = (Path(discovery.__file__).parents[1] / ".github/workflows/research-v2-input-discovery.yml").read_text()
    assert workflow.index("Validate exact read configuration") < workflow.index("Authenticate to Google Cloud")
    assert "id: gcp-auth" in workflow
    assert "steps.gcp-auth.outputs.credentials_file_path" in workflow
    assert "timeout --signal=TERM --kill-after=5s 180s" in workflow
    assert "default: discovery" in workflow
    assert "qqqm-boxx-raw-20260925-001" not in workflow


def wif_info():
    provider = CONFIG_IDENTITY["GCP_WORKLOAD_IDENTITY_PROVIDER"]
    return {
        "type": "external_account", "audience": f"//iam.googleapis.com/{provider}",
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{CONFIG_IDENTITY['GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT']}:generateAccessToken"
        ),
        "credential_source": {
            "url": "https://synthetic.actions.githubusercontent.com/token?x=1&audience="
                   f"https%3A%2F%2Fiam.googleapis.com%2F{provider.replace('/', '%2F')}",
            "headers": {"Authorization": "Bearer synthetic-only"},
            "format": {"type": "json", "subject_token_field_name": "value"},
        },
    }


def bind_wif(monkeypatch, tmp_path, info=None):
    path = tmp_path / "bound-wif.json"
    path.write_text(json.dumps(wif_info() if info is None else info))
    monkeypatch.setenv("EXACT_AUTH_CREDENTIALS_FILE", str(path))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://synthetic.actions.githubusercontent.com/token?x=1")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "synthetic-only")
    return path


def test_bound_file_validated_without_adc_or_network(monkeypatch, tmp_path):
    bind_wif(monkeypatch, tmp_path)
    assert discovery._bound_wif_info() == wif_info()


@pytest.mark.parametrize("field,bad", [
    ("type", "service_account"), ("audience", "//other-provider"),
    ("subject_token_type", "wrong"), ("token_url", "https://other.example/token"),
    ("service_account_impersonation_url", "https://other.example/generateAccessToken"),
    ("universe_domain", "other.example"), ("credential_source", []),
    ("quota_project_id", "another-project"), ("workforce_pool_user_project", "other"),
])
def test_bound_file_identity_changed_rejected(monkeypatch, tmp_path, field, bad):
    info = wif_info()
    info[field] = bad
    bind_wif(monkeypatch, tmp_path, info)
    with pytest.raises(ValueError, match="EXACT_AUTH_BINDING_INVALID"):
        discovery._bound_wif_info()


@pytest.mark.parametrize("field,bad", [
    ("url", "https://other.example/token"),
    ("url", "https://synthetic.actions.githubusercontent.com/token?x=1&audience=other"),
    ("headers", {"Authorization": "Bearer different"}),
    ("format", {"type": "json", "subject_token_field_name": "id_token"}),
    ("file", "/arbitrary-token-file"), ("executable", {"command": "never-run"}),
])
def test_subject_source_changed_rejected(monkeypatch, tmp_path, field, bad):
    info = wif_info()
    info["credential_source"][field] = bad
    bind_wif(monkeypatch, tmp_path, info)
    with pytest.raises(ValueError, match="EXACT_AUTH_BINDING_INVALID"):
        discovery._bound_wif_info()


@pytest.mark.parametrize("case", ["absent", "different", "relative", "symlink", "oversized", "duplicate"])
def test_auth_file_binding_and_size_fail_closed(monkeypatch, tmp_path, case):
    path = bind_wif(monkeypatch, tmp_path)
    if case == "absent":
        monkeypatch.delenv("EXACT_AUTH_CREDENTIALS_FILE")
    elif case == "different":
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "other.json"))
    elif case == "relative":
        monkeypatch.setenv("EXACT_AUTH_CREDENTIALS_FILE", "relative.json")
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "relative.json")
    elif case == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        monkeypatch.setenv("EXACT_AUTH_CREDENTIALS_FILE", str(link))
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(link))
    elif case == "oversized":
        path.write_bytes(b"x" * 65537)
    else:
        path.write_bytes(b'{"type":"external_account","type":"external_account"}')
    with pytest.raises(ValueError, match="EXACT_(AUTH_BINDING|JSON)_INVALID"):
        discovery._bound_wif_info()


def test_authorized_session_no_401_refresh_retry_or_redirect(monkeypatch, tmp_path):
    import sys
    from types import ModuleType

    bind_wif(monkeypatch, tmp_path)
    calls = []

    class HttpSession:
        def mount(self, prefix, adapter):
            calls.append(("mount", prefix, adapter))

        def request(self, *args, **kwargs):
            calls.append(("auth_request", args, kwargs))

        def close(self):
            calls.append(("closed",))

    credentials = object()

    def from_info(info, *, scopes):
        assert info == wif_info()
        assert scopes == ["https://www.googleapis.com/auth/devstorage.read_only"]
        return credentials

    def auth_request_factory(*, session):
        return lambda **kwargs: session.request(**kwargs)

    def authorized_session(actual_credentials, **kwargs):
        assert actual_credentials is credentials
        assert kwargs["refresh_status_codes"] == ()
        assert kwargs["max_refresh_attempts"] == 0
        # Legitimate library security lookup is retained, without endpoint blocking.
        kwargs["auth_request"]("https://iamcredentials.googleapis.com/security-metadata", method="GET",
                               headers={"x-allowed-locations": "synthetic-policy"}, timeout=3)
        return HttpSession()

    google = ModuleType("google")
    auth = ModuleType("google.auth")
    auth.identity_pool = SimpleNamespace(Credentials=SimpleNamespace(from_info=from_info))
    google.auth = auth
    transport = ModuleType("google.auth.transport.requests")
    transport.Request = auth_request_factory
    transport.AuthorizedSession = authorized_session
    requests = ModuleType("requests")
    requests.Session = HttpSession
    adapters = ModuleType("requests.adapters")
    adapters.HTTPAdapter = lambda *, max_retries: ("retries", max_retries)
    for name, module in {"google": google, "google.auth": auth,
                         "google.auth.transport.requests": transport, "requests": requests,
                         "requests.adapters": adapters}.items():
        monkeypatch.setitem(sys.modules, name, module)
    with discovery._exact_session(exact_config()):
        pass
    assert [call[2] for call in calls if call[0] == "mount"] == [("retries", 0)] * 2
    auth_call = next(call for call in calls if call[0] == "auth_request")
    assert auth_call[2]["allow_redirects"] is False
    assert auth_call[2]["timeout"] == 3
    assert auth_call[2]["headers"]["x-allowed-locations"] == "synthetic-policy"
    assert len([call for call in calls if call[0] == "closed"]) == 2


def test_exact_main_failure_redacted_and_no_discovery(monkeypatch, capsys):
    for name, value in CONFIG_IDENTITY.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(discovery, "discover", lambda _: pytest.fail("DISCOVERY_CALLED"))

    def failure(_config):
        raise RuntimeError("private-contact https://private.example/prices.csv")

    monkeypatch.setattr(discovery, "_exact_session", failure)
    assert discovery.main(exact_argv()) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["reason_code"] == "EXACT_READ_FAILED"
    assert "private" not in output
    assert "https://" not in output


def test_default_main_original_output_preserved(monkeypatch, capsys):
    import sys
    from types import ModuleType

    auth = ModuleType("google.auth")
    auth.default = lambda *, scopes: (object(), "ignored-project")
    google = ModuleType("google")
    google.auth = auth
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.auth", auth)
    monkeypatch.setattr(discovery, "_list_page", lambda _c, _t, _l: {"items": []})
    assert discovery.main() == 0
    assert json.loads(capsys.readouterr().out) == discovery.discover(lambda _t, _l: {"items": []})

    def deny(_c, _t, _l):
        raise ValueError("LIST_PERMISSION_DENIED")

    monkeypatch.setattr(discovery, "_list_page", deny)
    assert discovery.main([]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "PARKED", "reason_code": "LIST_PERMISSION_DENIED", "scope": discovery.PREFIX,
        "execution_authorized": False, "no_order": True,
    }


@pytest.mark.parametrize("field,value", [
    ("mode", "discovery"), ("bucket", "other-bucket"),
    ("manifest_object", "research/v2/input/fixture/prices.csv"),
    ("manifest_object", "other/manifest.json"), ("manifest_object", 1),
    ("generation", "18446744073709551616"), ("generation", 1),
    ("manifest_sha256", "a" * 63), ("manifest_sha256", 1),
    ("max_manifest_bytes", True), ("max_manifest_bytes", "1024"),
    ("max_manifest_bytes", 1048577), ("max_manifest_bytes", 0),
    ("project_id", "other"), ("service_account", "other"),
    ("workload_identity_provider", "other"), ("extra-field", "sensitive"),
])
def test_constructed_config_cannot_bypass_scope(field, value):
    session = Session([])
    with pytest.raises(ValueError, match="EXACT_INPUT_INVALID"):
        discovery.read_exact_manifest(exact_config(**{field: value}), session)
    assert session.calls == []


def test_config_missing_field_cannot_bypass_scope():
    config = exact_config()
    del config["generation"]
    session = Session([])
    with pytest.raises(ValueError, match="EXACT_INPUT_INVALID"):
        discovery.read_exact_manifest(config, session)
    assert session.calls == []


def test_claims_and_unrecognized_fields_cannot_become_authority():
    body = json.dumps({"schema": "claims-production-ready", "calendar_authority": "verified",
                       "dataset_license_verified": True, "provider": "https://private.example",
                       "contacts": ["private@example.test"], "members": [{"path": "prices.csv"}],
                       "account_id": "never-output"}).encode()
    config = exact_config(manifest_sha256=hashlib.sha256(body).hexdigest())
    result = discovery.read_exact_manifest(config, Session([
        Response(metadata(size=str(len(body)))), Response(body)]))
    assert result["schema_status"] == "unknown"
    assert result["calendar_authority_verified"] is False
    assert result["dataset_license_verified"] is False
    assert result["historical_available_at_verified"] is False
    assert result["recognized_calendar_claim_present"] is False
    assert all(value not in json.dumps(result) for value in (
        "private", "production-ready", "prices.csv", "never-output"))


def test_stream_error_response_cleanup():
    class BrokenResponse(Response):
        def iter_content(self, chunk_size):
            yield b"small"
            raise TimeoutError("private-path")

    response = BrokenResponse(b"")
    session = Session([response])
    with pytest.raises(ValueError, match="EXACT_TRANSPORT_FAILED"):
        discovery.read_exact_manifest(exact_config(), session)
    assert response.closed
    assert len(session.calls) == 1


def test_maximum_byte_ceiling_accepted_and_overflow_rejected():
    body = b'{"padding":"' + b'x' * (1048576 - 14) + b'"}'
    assert len(body) == 1048576
    config = exact_config(manifest_sha256=hashlib.sha256(body).hexdigest(), max_manifest_bytes=1048576)
    result = discovery.read_exact_manifest(config, Session([
        Response(metadata(size=str(len(body)))), Response(body, chunks=[body[i:i+4096] for i in range(0, len(body), 4096)])]))
    assert result["manifest_bytes"] == 1048576
    assert result["schema_status"] == "unknown"


def test_nested_duplicate_json_keys_rejected():
    body = b'{"nested":{"a":1,"a":2}}'
    config = exact_config(manifest_sha256=hashlib.sha256(body).hexdigest())
    with pytest.raises(ValueError, match="EXACT_JSON_INVALID"):
        discovery.read_exact_manifest(config, Session([
            Response(metadata(size=str(len(body)))), Response(body)]))


def test_workflow_manual_only_fixed_actions_no_materialization():
    workflow = (Path(discovery.__file__).parents[1] / ".github/workflows/research-v2-input-discovery.yml").read_text()
    for trigger in ("push:", "pull_request:", "schedule:", "workflow_run:"):
        assert trigger not in workflow
    assert "google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093" in workflow
    assert "google-github-actions/setup-gcloud@e427ad8a34f8676edf47cf7d7925499adf3eb74f" in workflow
    for forbidden in ("upload-artifact", "gcloud storage", "gsutil", "run_batch_a", "compare", "backtest"):
        assert forbidden not in workflow
    assert "config_sha256:" in workflow


def workflow_inputs(config=None):
    config = exact_config() if config is None else config
    return {"mode": config["mode"], "manifest_object": config["manifest_object"],
            "generation": config["generation"], "manifest_sha256": config["manifest_sha256"],
            "max_manifest_bytes": str(config["max_manifest_bytes"]), "config_sha256": config_digest(config)}


def test_workflow_event_preflight_no_private_env_or_log(monkeypatch, tmp_path, capsys):
    for name, value in CONFIG_IDENTITY.items():
        monkeypatch.setenv(name, value)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": workflow_inputs()}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setattr(discovery, "_exact_session", lambda _: pytest.fail("AUTH_CALLED"))
    assert discovery.main(["--workflow-inputs", "--preflight"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["run_config_sha256"] == config_digest(exact_config())
    assert MANIFEST_OBJECT not in output
    workflow = (Path(discovery.__file__).parents[1] / ".github/workflows/research-v2-input-discovery.yml").read_text()
    for expression in ("${{ inputs.manifest_object }}", "${{ inputs.generation }}",
                       "${{ inputs.manifest_sha256 }}", "${{ inputs.max_manifest_bytes }}"):
        assert expression not in workflow


@pytest.mark.parametrize("inputs", [
    {"mode": "discovery", "manifest_object": MANIFEST_OBJECT},
    {"mode": "exact_manifest_metadata"}, {"mode": "other"},
    {"mode": True}, {"mode": "discovery", "arbitrary": "private-contact"}, [],
])
def test_workflow_event_bad_scope_rejected_before_auth(monkeypatch, tmp_path, capsys, inputs):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": inputs}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setattr(discovery, "_exact_session", lambda _: pytest.fail("AUTH_CALLED"))
    monkeypatch.setattr(discovery, "discover", lambda _: pytest.fail("DISCOVERY_CALLED"))
    assert discovery.main(["--workflow-inputs", "--preflight"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "PARKED"
    assert "private-contact" not in output


def test_discovery_workflow_preflight_no_auth(monkeypatch, tmp_path, capsys):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"mode": "discovery", "manifest_object": ""}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setattr(discovery, "discover", lambda _: pytest.fail("DISCOVERY_CALLED"))
    assert discovery.main(["--workflow-inputs", "--preflight"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "DISCOVERY_PREFLIGHT_VALIDATED"


@pytest.mark.parametrize("name", list(CONFIG_IDENTITY))
def test_credential_binding_rechecks_same_identity_hashes(monkeypatch, tmp_path, name):
    bind_wif(monkeypatch, tmp_path)
    monkeypatch.setenv(name, "different-in-process-identity")
    with pytest.raises(ValueError, match="EXACT_IDENTITY_INVALID"):
        discovery._bound_wif_info()


@pytest.mark.parametrize("status", [100, 301, 401, 403, 404, 429, 500, 599])
@pytest.mark.parametrize("phase", [0, 1])
def test_exact_http_status_receipt_contains_only_fixed_numeric_diagnostic(monkeypatch, capsys, status, phase):
    from contextlib import contextmanager

    responses = [Response(metadata())] * phase + [Response(
        b"private-contact https://private.example/prices.csv", status=status,
        headers={"Location": "https://private.example", "Authorization": "never-output"})]
    session = Session(responses)

    @contextmanager
    def fake_session(_config):
        yield session

    monkeypatch.setattr(discovery, "_exact_session", fake_session)
    assert discovery.main(exact_argv()) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "status": "PARKED", "reason_code": "EXACT_HTTP_FAILED", "http_status": status,
        "execution_authorized": False, "no_order": True,
    }
    assert len(session.calls) == phase + 1
    assert all(response.closed for response in responses)
    assert "private" not in output
    assert "never-output" not in output
    assert "https://" not in output


@pytest.mark.parametrize("status", [True, False, None, "401 private", 401.0, 99, 600])
def test_malformed_http_status_omitted_without_echo(monkeypatch, capsys, status):
    from contextlib import contextmanager

    session = Session([Response(b"private-body", status=status)])

    @contextmanager
    def fake_session(_config):
        yield session

    monkeypatch.setattr(discovery, "_exact_session", fake_session)
    assert discovery.main(exact_argv()) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "PARKED", "reason_code": "EXACT_HTTP_FAILED",
                      "execution_authorized": False, "no_order": True}
    assert len(session.calls) == 1


def test_exact_http_200_success_receipt_remains_unchanged(monkeypatch, capsys):
    from contextlib import contextmanager

    config = exact_config()
    session = Session([Response(metadata()), Response(MANIFEST)])

    @contextmanager
    def fake_session(_config):
        yield session

    monkeypatch.setattr(discovery, "_exact_session", fake_session)
    assert discovery.main(exact_argv()) == 0
    output = capsys.readouterr().out.strip()
    result = json.loads(output)
    # Frozen from authenticated PR550 source SHA256 c5d160ab, not this implementation.
    assert hashlib.sha256(output.encode()).hexdigest() == '5550b6aee9f55f7f5b8a0a225aea15a3930629fb4554c75baee7f3e24ab9f7d4'
    assert "http_status" not in result
    assert len(session.calls) == 2


def test_default_discovery_http_failure_output_remains_unchanged(monkeypatch, capsys):
    import sys
    from types import ModuleType

    auth = ModuleType("google.auth")
    auth.default = lambda *, scopes: (object(), "ignored")
    google = ModuleType("google")
    google.auth = auth
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.auth", auth)

    def unavailable(_c, _t, _l):
        raise ValueError("LIST_HTTP_FAILED")

    monkeypatch.setattr(discovery, "_list_page", unavailable)
    assert discovery.main([]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "PARKED", "reason_code": "LIST_HTTP_FAILED", "scope": discovery.PREFIX,
        "execution_authorized": False, "no_order": True,
    }


@pytest.mark.parametrize("status", [True, False, None, "private-modified", 401.0, 99, 600])
def test_constructed_exception_status_revalidated_at_receipt(monkeypatch, capsys, status):
    error = discovery._ExactHttpFailure(401)
    error.http_status = status

    def fail(_config):
        raise error

    monkeypatch.setattr(discovery, "_exact_session", fail)
    assert discovery.main(exact_argv()) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "PARKED", "reason_code": "EXACT_HTTP_FAILED",
                                 "execution_authorized": False, "no_order": True}
    assert "private" not in output
