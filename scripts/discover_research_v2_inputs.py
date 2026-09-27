#!/usr/bin/env python3
"""Bounded read-only metadata inventory using the runner's existing identity."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        raise ValueError("NO_INPUTS_ALLOWED")
    try:
        import google.auth

        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
        payload = discover(lambda token, limit: _list_page(credentials, token, limit))
        code = 0
    except Exception as exc:
        known = {"LIST_FORMAT_UNRECOGNIZED", "LIST_PERMISSION_DENIED", "LIST_HTTP_FAILED",
                 "LIST_TRANSPORT_FAILED", "OUT_OF_SCOPE_OBJECT", "PAGE_BUDGET_OR_FORMAT_INVALID",
                 "PAGE_TOKEN_INVALID"}
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in known else "LIST_FAILED"
        payload = {"status": "PARKED", "reason_code": reason,
                   "scope": PREFIX, "execution_authorized": False, "no_order": True}
        code = 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
