"""S1 black-box wrapper: read-only HTTP checks against a LIVE native server.

Skipped unless invoked with ``--native-run`` plus a live URL::

    pytest tests/test_s1_blackbox.py -q --native-run --native-url=http://127.0.0.1:8765

or via environment (same gate, no conftest changes)::

    S1_NATIVE_RUN=1 S1_NATIVE_URL=http://127.0.0.1:8765 pytest tests/test_s1_blackbox.py -q

This module NEVER starts servers (no subprocess, no TestClient, no app
import). It performs HTTP-only integrity checks with stdlib urllib:
health, honest status shape, generation-pinned query labels, dossier
citation shape, and the caller-injection gate (injected revision claims
must not surface in served citations).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

_DEFAULT_URL = "http://127.0.0.1:8765"


def _argv_value(prefix: str) -> str | None:
    for arg in sys.argv:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _native_url() -> str | None:
    if "--native-run" not in sys.argv and os.environ.get("S1_NATIVE_RUN") != "1":
        return None
    return (_argv_value("--native-url=") or os.environ.get("S1_NATIVE_URL")
            or _DEFAULT_URL)


LIVE_URL = _native_url()

pytestmark = pytest.mark.skipif(
    LIVE_URL is None,
    reason="S1 black-box wrapper runs only with --native-run and a live URL "
           "(operator starts benchmarks/blackbox_native_check.py server separately)")


def _http(method: str, path: str, body=None, timeout: float = 30.0):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(LIVE_URL + path, data=data,
                                 headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"_http_error": exc.read().decode("utf-8", "replace")[:500]}


def test_native_health():
    status, payload = _http("GET", "/api/health")
    assert status == 200
    assert payload.get("ok") is True


def test_native_status_shape():
    status, payload = _http("GET", "/api/status")
    assert status == 200
    for key in ("ok", "atoms", "index_trusted", "coverage_pending",
                "generation_id", "semantic_status"):
        assert key in payload, f"status missing {key}"
    assert payload["ok"] is True


def test_native_query_pinned_labels():
    status, payload = _http("POST", "/api/query",
                            {"query": "batching throughput", "limit": 5})
    assert status == 200
    assert payload.get("generation_id"), "query must report its pinned generation"
    assert payload.get("route"), "query must report an honest route label"
    if payload.get("route") == "vector":
        assert payload.get("vector_generation_id") == payload.get("generation_id"), \
            "served ANN branch must be pinned to the serving generation"


def test_native_dossier_citation_shape():
    status, payload = _http("POST", "/api/dossier",
                            {"query": "batching throughput", "limit": 5})
    assert status == 200
    selections = payload.get("selections", [])
    if not selections:
        pytest.skip("live corpus empty: citation shape vacuous (seed notes first)")
    for sel in selections:
        assert sel.get("revision_id"), "every selection must cite a revision"
        assert 0 <= int(sel.get("start_byte", -1)) <= int(sel.get("end_byte", -2)), \
            "citation byte window must be well-formed"
        assert sel.get("excerpt_text"), "citation must carry excerpt text"


def test_native_injection_gate():
    fake_rev = "deadbeef-" * 4
    status, payload = _http("POST", "/api/dossier", {
        "query": "batching throughput",
        "limit": 5,
        "candidates": [{"candidate_id": "atom-1", "revision_id": fake_rev}],
        "revision_id": fake_rev,
    })
    assert status == 200
    blob = json.dumps(payload, sort_keys=True)
    assert fake_rev not in blob, "caller-claimed revision must never surface"
    cited = {s.get("revision_id", "") for s in payload.get("selections", [])}
    assert fake_rev not in cited
