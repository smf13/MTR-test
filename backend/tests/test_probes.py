"""Probe helpers and live HTTP/TCP paths with mocked transports."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app import probes
from app.probes import json_matches, json_path, run_http, run_tcp, status_allowed


def test_status_allowed() -> None:
    assert status_allowed(204, None) and status_allowed(299, "200-299") and not status_allowed(301, "200-299")
    assert status_allowed(301, "200,301,302") and status_allowed(404, "404")


def test_json_path_and_match() -> None:
    data = {"data": {"items": [{"status": "ok", "count": 5, "flag": True, "nothing": None}]}}
    assert json_path(data, "data.items[0].status") == (True, "ok")
    assert json_path(data, "data.items[1].status") == (False, None)
    assert json_path(data, "data.missing") == (False, None)
    assert json_matches("ok", "ok") and not json_matches("ok", "OK")
    assert json_matches(5, ">= 5") and json_matches(5, "!= 6") and not json_matches(5, "> 5")
    assert json_matches("healthy-node", "~HEALTHY") and json_matches(True, "true") and json_matches(None, "null")


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(probes, "_FORCE_LIVE", True)
    yield


async def test_run_http_checks(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "degraded", "queue": 12}, headers={"server": "unit"})
        if request.url.path == "/page":
            return httpx.Response(200, text="<html>Welcome to MTR Tracker</html>")
        return httpx.Response(503, text="nope")

    monkeypatch.setattr(probes, "_HTTP_TRANSPORT", httpx.MockTransport(handler))
    base = {"host": "http://svc.test/page", "type": "http"}

    ok = await run_http(base, {"keyword": "mtr tracker"})
    assert ok.ok and ok.reached and ok.details["status"] == 200 and ok.details["keyword_found"] is True and ok.avg_ms is not None

    missing = await run_http(base, {"keyword": "absent text"})
    assert missing.ok and not missing.reached and "not found" in (missing.error or "")

    absent = await run_http(base, {"keyword": "Welcome", "keyword_absent": True})
    assert not absent.reached and "must be absent" in (absent.error or "")

    js = await run_http({"host": "http://svc.test/health", "type": "http"}, {"json_path": "status", "json_expected": "ok"})
    assert not js.reached and "expected ok" in (js.error or "") and js.details["json_value"] == "degraded"

    js2 = await run_http({"host": "http://svc.test/health", "type": "http"}, {"json_path": "queue", "json_expected": "< 20"})
    assert js2.reached and js2.details["json_ok"] is True and js2.details["server"] == "unit"

    bad = await run_http({"host": "http://svc.test/down", "type": "http"}, {})
    assert not bad.reached and bad.loss_pct == 100.0 and "503" in (bad.error or "")

    allowed = await run_http({"host": "http://svc.test/down", "type": "http"}, {"expected_status": "503"})
    assert allowed.reached


async def test_run_tcp_local(live: None) -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        o = await run_tcp({"host": "127.0.0.1", "port": port, "type": "tcp"}, {"timeout_sec": 2})
        assert o.ok and o.reached and o.details["connected"] and o.avg_ms is not None
    finally:
        server.close()
        await server.wait_closed()
    closed = await run_tcp({"host": "127.0.0.1", "port": port, "type": "tcp"}, {"timeout_sec": 2})
    assert closed.ok and not closed.reached and closed.loss_pct == 100.0
    assert not (await run_tcp({"host": "127.0.0.1", "port": None, "type": "tcp"}, {})).ok
