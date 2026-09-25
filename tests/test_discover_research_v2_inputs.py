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
        discovery._list_page(Credentials(), None, 1)
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
        discovery._list_page(Credentials(), None, 1)
    assert len(calls) == 3
