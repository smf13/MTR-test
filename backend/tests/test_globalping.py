"""Globalping probe type: request building, result parsing for all five measurements, API errors (HTTP mocked) and the simulated end-to-end path."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest
from httpx import AsyncClient

from app import globalping
from app.globalping import (
    build_request,
    describe,
    hop_from_result,
    hop_from_traceroute,
    parse_dns_probes,
    parse_http_probes,
    parse_ping_probes,
    probe_label,
    run_globalping,
    run_globalping_path,
    tls_info_from_result,
)
from helpers import wait_for_runs

# Shapes below were captured from real measurements (api.globalping.io, September 2026).
PROBE = {"continent": "EU", "region": "Western Europe", "country": "DE", "state": None, "city": "Falkenstein", "asn": 24940, "network": "Hetzner Online", "tags": []}
PING_RESULT = {
    "status": "finished", "rawOutput": "PING cdn.jsdelivr.net (151.101.129.229) 56(84) bytes of data.", "resolvedAddress": "151.101.129.229", "resolvedHostname": "cdn.jsdelivr.net",
    "timings": [{"ttl": 57, "rtt": 5.23}, {"ttl": 57, "rtt": 5.0}, {"ttl": 57, "rtt": 4.99}],
    "stats": {"min": 4.99, "max": 5.228, "avg": 5.073, "total": 3, "loss": 0, "rcv": 3, "drop": 0},
}
MTR_RESULT = {
    "status": "finished", "rawOutput": "Host ...", "resolvedAddress": "104.17.207.5", "resolvedHostname": "cdn.jsdelivr.net",
    "hops": [
        {"asn": [24940], "resolvedAddress": "213.133.117.180", "resolvedHostname": "_gateway",
         "stats": {"min": 0.357, "max": 0.96, "avg": 0.7, "total": 2, "loss": 0, "rcv": 2, "drop": 0, "stDev": 0.3, "jMin": 0.6, "jMax": 0.6, "jAvg": 0.6}, "timings": [{"rtt": 0.357}, {"rtt": 0.96}]},
        {"asn": [], "resolvedAddress": "*", "resolvedHostname": "*",
         "stats": {"min": 0, "max": 0, "avg": 0, "total": 2, "loss": 100, "rcv": 0, "drop": 2, "stDev": 0, "jMin": 0, "jMax": 0, "jAvg": 0}, "timings": []},
        {"asn": [13335], "resolvedAddress": "104.17.207.5", "resolvedHostname": "cdn.jsdelivr.net",
         "stats": {"min": 5.593, "max": 5.598, "avg": 5.6, "total": 2, "loss": 0, "rcv": 2, "drop": 0, "stDev": 0, "jMin": 0, "jMax": 0, "jAvg": 0}, "timings": [{"rtt": 5.593}, {"rtt": 5.598}]},
    ],
}
TRACEROUTE_RESULT = {
    "rawOutput": "traceroute to cdn.jsdelivr.net (151.101.129.229), 20 hops max, 60 byte packets", "status": "finished", "resolvedAddress": "151.101.129.229", "resolvedHostname": "cdn.jsdelivr.net",
    "hops": [
        {"resolvedAddress": "172.31.1.1", "resolvedHostname": "_gateway", "timings": [{"rtt": 3.673}, {"rtt": 4.453}]},
        {"resolvedAddress": None, "resolvedHostname": None, "timings": []},
        {"resolvedAddress": "213.239.224.70", "resolvedHostname": "core1.fra.hetzner.com", "timings": [{"rtt": 8.452}]},
        {"resolvedAddress": "151.101.129.229", "resolvedHostname": "cdn.jsdelivr.net", "timings": [{"rtt": 9.089}, {"rtt": 9.085}]},
    ],
}
DNS_RESULT = {
    "status": "finished", "rawOutput": "; <<>> DiG 9.18.49 <<>> -t A cdn.jsdelivr.net @1.1.1.1", "statusCodeName": "NOERROR", "statusCode": 0,
    "answers": [
        {"name": "cdn.jsdelivr.net.", "type": "CNAME", "ttl": 2, "class": "IN", "value": "cdn.jsdelivr.net.cdn.cloudflare.net."},
        {"name": "cdn.jsdelivr.net.cdn.cloudflare.net.", "type": "A", "ttl": 122, "class": "IN", "value": "104.17.208.5"},
    ],
    "timings": {"total": 8}, "resolver": "1.1.1.1",
}
DNS_NXDOMAIN = {"status": "finished", "rawOutput": "...", "statusCodeName": "NXDOMAIN", "statusCode": 3, "answers": [], "timings": {"total": 31}, "resolver": "1.1.1.1"}
HTTP_RESULT = {
    "status": "finished", "resolvedAddress": "104.17.207.5",
    "headers": {"date": "Thu, 17 Sep 2026 04:02:58 GMT", "content-type": "text/plain; charset=utf-8", "location": "https://www.jsdelivr.com", "server": "cloudflare"},
    "rawHeaders": "Date: ...", "rawBody": "Moved Permanently. Redirecting to https://www.jsdelivr.com", "rawOutput": "HTTP/1.1 301", "truncated": False,
    "statusCode": 301, "statusCodeName": "Moved Permanently", "timings": {"total": 48, "dns": 4, "tcp": 10, "tls": 21, "firstByte": 13, "download": 0},
    "tls": {
        "authorized": True, "protocol": "TLSv1.3", "cipherName": "TLS_AES_256_GCM_SHA384", "createdAt": "2026-04-22T00:00:00.000Z", "expiresAt": "2026-11-06T23:59:59.000Z",
        "issuer": {"C": "GB", "O": "Sectigo Limited", "CN": "Sectigo ECC Domain Validation Secure Server CA"},
        "subject": {"CN": "jsdelivr.net", "alt": "DNS:jsdelivr.net, DNS:*.jsdelivr.net"},
        "keyType": "EC", "keyBits": 256, "serialNumber": "0A:1B", "fingerprint256": "AB:CD",
    },
}


def fake_api(kind: str, results: list[dict[str, Any]], *, post_status: int = 202, post_body: dict[str, Any] | None = None, polls_before_finish: int = 2) -> tuple[Callable[[httpx.Request], httpx.Response], dict[str, Any]]:
    """MockTransport handler: POST creates measurement 'm1'; GET reports in-progress, then finished with one entry per result."""
    state: dict[str, Any] = {"gets": 0, "posts": [], "auth": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["auth"].append(request.headers.get("authorization"))
        if request.method == "POST":
            state["posts"].append(json.loads(request.content))
            if post_status != 202:
                return httpx.Response(post_status, json=post_body or {"error": {"type": "x", "message": "nope"}}, headers={"x-ratelimit-reset": "1200"})
            return httpx.Response(202, json={"id": "m1", "probesCount": len(results)}, headers={"x-ratelimit-limit": "250", "x-ratelimit-remaining": "249", "x-ratelimit-reset": "3600"})
        state["gets"] += 1
        finished = state["gets"] >= polls_before_finish
        probes = [{**PROBE, "city": f"City{i}"} for i in range(len(results))]
        return httpx.Response(
            200,
            json={"id": "m1", "type": kind, "status": "finished" if finished else "in-progress", "target": "cdn.jsdelivr.net", "probesCount": len(results),
                  "results": [{"probe": p, "result": r if finished else {"status": "in-progress"}} for p, r in zip(probes, results)]},
        )

    return handler, state


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(globalping, "_FORCE_LIVE", True)
    monkeypatch.setattr(globalping, "POLL_INTERVAL", 0.01)


def _mock(monkeypatch: pytest.MonkeyPatch, kind: str, results: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    handler, state = fake_api(kind, results, **kw)
    monkeypatch.setattr(globalping, "_TRANSPORT", httpx.MockTransport(handler))
    return state


def test_build_request_per_measurement() -> None:
    body = build_request({"host": "cdn.jsdelivr.net", "count": 40, "ip_version": "6", "protocol": "tcp", "port": 443}, {"measurement": "mtr", "location": "Germany", "probes": 3})
    assert body["type"] == "mtr" and body["limit"] == 1 and body["locations"] == [{"magic": "Germany"}]
    assert body["measurementOptions"] == {"packets": 16, "ipVersion": 6, "protocol": "TCP", "port": 443}

    body = build_request({"host": "1.1.1.1", "count": 4, "ip_version": "4"}, {"measurement": "ping", "location": "world", "probes": 2})
    assert body["type"] == "ping" and body["limit"] == 2 and "locations" not in body and body["measurementOptions"] == {"packets": 4}  # no ipVersion for an IP literal

    body = build_request({"host": "cdn.jsdelivr.net", "count": 4, "protocol": "udp", "port": 33434}, {"measurement": "traceroute", "location": "EU", "probes": 5})
    assert body["limit"] == 1 and body["measurementOptions"] == {"protocol": "UDP", "port": 33434}

    body = build_request({"host": "cdn.jsdelivr.net"}, {"measurement": "dns", "record_type": "AAAA", "resolver": "1.1.1.1", "probes": 3})
    assert body["limit"] == 3 and body["measurementOptions"] == {"query": {"type": "AAAA"}, "resolver": "1.1.1.1", "protocol": "UDP"}
    body = build_request({"host": "cdn.jsdelivr.net"}, {"measurement": "dns", "dns_transport": "tcp"})
    assert body["measurementOptions"] == {"query": {"type": "A"}, "protocol": "TCP"}
    assert describe(body, "EU").endswith("+tcp (1 probe)")

    body = build_request({"host": "cdn.jsdelivr.net", "port": 8443}, {"measurement": "http", "path": "/health", "http_method": "HEAD", "http_protocol": "HTTP2"})
    assert body["target"] == "cdn.jsdelivr.net" and body["measurementOptions"] == {"request": {"path": "/health", "method": "HEAD"}, "protocol": "HTTP2", "port": 8443}
    body = build_request({"host": "https://portal.example.com/status?x=1"}, {"measurement": "http"})
    assert body["target"] == "portal.example.com" and body["measurementOptions"]["request"] == {"path": "/status?x=1", "method": "GET"}  # a pasted URL is split


def test_parsers() -> None:
    assert probe_label(PROBE) == "Falkenstein, DE · AS24940 Hetzner Online"
    hops = [hop_from_result(i, h) for i, h in enumerate(MTR_RESULT["hops"])]
    assert [h.hop_no for h in hops] == [1, 2, 3]
    assert hops[0].ip == "213.133.117.180" and hops[0].hostname == "_gateway" and hops[0].asn == "AS24940" and hops[0].avg_ms == 0.7 and hops[0].last_ms == 0.96
    assert hops[1].ip is None and hops[1].hostname is None and hops[1].loss_pct == 100.0 and hops[1].avg_ms is None and hops[1].received == 0
    assert hops[2].asn == "AS13335" and hops[2].jitter_avg_ms == 0 and hops[2].best_ms == 5.593

    tr = [hop_from_traceroute(i, h, 2) for i, h in enumerate(TRACEROUTE_RESULT["hops"])]
    assert tr[0].ip == "172.31.1.1" and tr[0].sent == 2 and tr[0].received == 2 and tr[0].avg_ms == pytest.approx(4.063) and tr[0].best_ms == 3.673 and tr[0].jitter_avg_ms is None
    assert tr[1].ip is None and tr[1].loss_pct == 100.0 and tr[1].avg_ms is None
    assert tr[2].received == 1 and tr[2].loss_pct == 50.0 and tr[2].avg_ms == 8.452
    assert tr[3].hostname == "cdn.jsdelivr.net" and tr[3].loss_pct == 0.0

    probes = parse_ping_probes({"results": [{"probe": PROBE, "result": PING_RESULT}]})
    assert probes[0]["label"].startswith("Falkenstein") and probes[0]["avg"] == 5.073 and probes[0]["rtts"] == [5.23, 5.0, 4.99] and probes[0]["resolved"] == "151.101.129.229"

    dns = parse_dns_probes({"results": [{"probe": PROBE, "result": DNS_RESULT}, {"probe": PROBE, "result": DNS_NXDOMAIN}]}, expected="104.17.")
    assert dns[0]["passed"] and dns[0]["rcode"] == "NOERROR" and dns[0]["total_ms"] == 8.0 and dns[0]["answers"][1]["value"] == "104.17.208.5" and dns[0]["resolver"] == "1.1.1.1"
    assert not dns[1]["passed"] and dns[1]["reason"] == "NXDOMAIN"
    assert not parse_dns_probes({"results": [{"probe": PROBE, "result": DNS_RESULT}]}, expected="192.0.2")[0]["passed"]

    http = parse_http_probes({"results": [{"probe": PROBE, "result": HTTP_RESULT}]})
    assert not http[0]["passed"] and http[0]["reason"] == "HTTP 301 not in expected 200-299" and http[0]["status_code"] == 301 and http[0]["total_ms"] == 48.0
    assert http[0]["timings"]["tls"] == 21 and http[0]["server"] == "cloudflare" and http[0]["resolved"] == "104.17.207.5"
    assert parse_http_probes({"results": [{"probe": PROBE, "result": HTTP_RESULT}]}, expected_status="301", keyword="redirecting")[0]["passed"]
    assert parse_http_probes({"results": [{"probe": PROBE, "result": HTTP_RESULT}]}, expected_status="301", keyword="welcome")[0]["reason"] == "keyword 'welcome' not found"
    tls = tls_info_from_result(HTTP_RESULT["tls"])
    assert tls is not None and tls["subject"] == "jsdelivr.net" and tls["issuer"] == "Sectigo Limited" and tls["issuer_cn"].startswith("Sectigo ECC")
    assert tls["san"] == ["jsdelivr.net", "*.jsdelivr.net"] and tls["not_after"] == "2026-11-06T23:59:59Z" and isinstance(tls["days_left"], int)
    assert tls["protocol"] == "TLSv1.3" and tls["cipher"] == "TLS_AES_256_GCM_SHA384" and tls["serial"] == "0A:1B" and tls["authorized"] is True


async def test_run_globalping_ping(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _mock(monkeypatch, "ping", [PING_RESULT])
    t = {"host": "cdn.jsdelivr.net", "count": 3, "ip_version": "auto", "type": "globalping"}
    o = await run_globalping(t, {"measurement": "ping", "location": "Germany", "probes": 1}, {"globalping_token": "tok"})
    assert o.ok and o.reached and o.loss_pct == 0.0 and o.sent == 3 and o.dst_ip == "151.101.129.229"
    assert o.avg_ms == pytest.approx(5.073, abs=0.01) and o.best_ms == 4.99 and o.worst_ms == 5.23
    assert o.details["url"].endswith("m1") and o.details["rate_limit"]["remaining"] == "249" and o.details["probes"][0]["city"] == "City0"
    assert state["posts"][0]["locations"] == [{"magic": "Germany"}] and state["auth"][0] == "Bearer tok" and state["gets"] == 2
    assert "globalping ping cdn.jsdelivr.net from Germany" in (o.command or "")


async def test_run_globalping_dns_partial_failure_is_degraded(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, "dns", [DNS_RESULT, DNS_NXDOMAIN])
    o = await run_globalping({"host": "cdn.jsdelivr.net", "type": "globalping"}, {"measurement": "dns", "location": "EU", "probes": 2, "resolver": "1.1.1.1"}, {})
    assert o.ok and o.reached and o.loss_pct == 50.0 and o.sent == 2 and o.avg_ms == 8.0
    assert o.warnings == ["1 of 2 probes failed (City1, DE · AS24940 Hetzner Online: NXDOMAIN)"] and o.error is None
    assert o.details["answers"] == ["cdn.jsdelivr.net.cdn.cloudflare.net.", "104.17.208.5"] and o.details["rcode"] == "NOERROR" and o.details["record_type"] == "A"
    assert o.details["resolver"] == "1.1.1.1" and "globalping dns A cdn.jsdelivr.net from EU @1.1.1.1" in (o.command or "")

    _mock(monkeypatch, "dns", [DNS_NXDOMAIN])
    o = await run_globalping({"host": "nope.example", "type": "globalping"}, {"measurement": "dns"}, {})
    assert o.ok and not o.reached and o.loss_pct == 100.0 and "NXDOMAIN" in (o.error or "") and not o.warnings


async def test_run_globalping_http(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _mock(monkeypatch, "http", [HTTP_RESULT])
    t = {"host": "cdn.jsdelivr.net", "type": "globalping", "port": None}
    o = await run_globalping(t, {"measurement": "http", "location": "Germany", "path": "/", "expected_status": "301"}, {})
    assert o.ok and o.reached and o.avg_ms == 48.0 and o.dst_ip == "104.17.207.5" and o.sent == 1
    assert o.details["status"] == 301 and o.details["request"] == "GET https://cdn.jsdelivr.net/" and o.details["timings"]["firstByte"] == 13
    assert o.details["tls"]["issuer"] == "Sectigo Limited" and o.details["tls_expires_in_days"] == o.details["tls"]["days_left"]
    assert state["posts"][0]["measurementOptions"] == {"request": {"path": "/", "method": "GET"}, "protocol": "HTTPS"}

    o = await run_globalping(t, {"measurement": "http", "location": "Germany"}, {})
    assert o.ok and not o.reached and "HTTP 301 not in expected 200-299" in (o.error or "")


async def test_run_globalping_api_errors(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, "ping", [PING_RESULT], post_status=422, post_body={"error": {"type": "no_probes_found", "message": "No matching IPv4 probes available."}})
    o = await run_globalping({"host": "x.test", "count": 3, "type": "globalping"}, {"location": "nowhere"}, {})
    assert not o.ok and "No matching IPv4 probes" in (o.error or "") and o.details["location"] == "nowhere"

    _mock(monkeypatch, "ping", [PING_RESULT], post_status=429, post_body={"error": {"type": "too_many_requests", "message": "Too Many Requests"}})
    o = await run_globalping({"host": "x.test", "count": 3, "type": "globalping"}, {}, {})
    assert not o.ok and "rate limit" in (o.error or "") and "1200 s" in (o.error or "")

    res = await run_globalping_path({"host": "x.test", "count": 3, "type": "globalping"}, {"measurement": "mtr"}, {})
    assert not res.ok and "rate limit" in (res.error or "")


async def test_run_globalping_path_mtr_and_traceroute(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _mock(monkeypatch, "mtr", [MTR_RESULT])
    t = {"host": "cdn.jsdelivr.net", "count": 2, "ip_version": "auto", "protocol": "icmp", "port": None, "type": "globalping"}
    res = await run_globalping_path(t, {"measurement": "mtr", "location": "DE"}, {})
    assert res.ok and res.dst_ip == "104.17.207.5" and len(res.hops) == 3 and res.hops[-1].ip == res.dst_ip
    assert res.src == "City0, DE · AS24940 Hetzner Online" and res.details["probe"]["label"] == res.src and res.details["measurement"] == "mtr"
    assert state["posts"][0]["measurementOptions"] == {"packets": 2, "protocol": "ICMP"} and state["posts"][0]["limit"] == 1

    state = _mock(monkeypatch, "traceroute", [TRACEROUTE_RESULT])
    res = await run_globalping_path(t, {"measurement": "traceroute", "location": "DE"}, {})
    assert res.ok and res.dst_ip == "151.101.129.229" and len(res.hops) == 4 and res.hops[-1].ip == res.dst_ip and res.hops[1].ip is None
    assert res.hops[0].sent == 2 and res.hops[2].loss_pct == 50.0 and res.details["measurement"] == "traceroute"
    assert state["posts"][0]["measurementOptions"] == {"protocol": "ICMP"} and "globalping traceroute" in res.command


async def test_globalping_targets_end_to_end(client: AsyncClient) -> None:
    """Simulation mode: every measurement type produces the right kind of run."""
    specs = {
        "ping": {"measurement": "ping", "location": "Germany", "probes": 2},
        "traceroute": {"measurement": "traceroute", "location": "EU"},
        "mtr": {"measurement": "mtr", "location": "aws-eu-west-1"},
        "dns": {"measurement": "dns", "location": "US", "probes": 2, "record_type": "A", "expected": "192.0.2"},
        "http": {"measurement": "http", "location": "world", "path": "health", "expected_status": "200"},
    }
    ids: dict[str, int] = {}
    for m, options in specs.items():
        r = await client.post("/api/targets", json={"name": f"GP {m}", "host": "cdn.jsdelivr.net", "type": "globalping", "interval_sec": 60, "count": 3, "options": options})
        assert r.status_code == 201, r.text
        ids[m] = r.json()["id"]
    assert (await client.get(f"/api/targets/{ids['http']}")).json()["options"]["path"] == "/health"  # normalised

    runs = {m: (await client.get(f"/api/runs/{(await wait_for_runs(client, tid, 1))[0]['id']}")).json() for m, tid in ids.items()}
    ping, dns, http = runs["ping"], runs["dns"], runs["http"]
    assert ping["status"] == "ok" and ping["reached"] and ping["hop_count"] == 0 and len(ping["details"]["probes"]) == 2 and ping["avg_ms"] is not None
    assert dns["status"] == "ok" and dns["reached"] and dns["details"]["answers"] == ["192.0.2.10"] and dns["details"]["probes"][1]["rcode"] == "NOERROR" and dns["avg_ms"] is not None
    assert http["status"] == "ok" and http["details"]["request"] == "GET https://cdn.jsdelivr.net/health" and http["details"]["tls"]["issuer"] == "Simulated CA" and http["details"]["timings"]["dns"] is not None
    for m in ("traceroute", "mtr"):
        run = runs[m]
        assert run["status"] == "ok" and run["hop_count"] > 0 and len(run["hops"]) == run["hop_count"] and run["hops"][-1]["ip"] == run["dst_ip"], m
        assert "·" in run["src"] and run["details"]["measurement"] == m and run["details"]["probe"]["label"] == run["src"]
    assert all(h["jitter_avg_ms"] is None for h in runs["traceroute"]["hops"])
    summary = (await client.get(f"/api/targets/{ids['traceroute']}/hops/summary?range=1h")).json()
    assert summary["total_runs"] >= 1 and summary["hops"][-1]["primary"]["ip"] == runs["traceroute"]["dst_ip"]

    assert (await client.post("/api/targets", json={"name": "bad", "host": "x.test", "type": "globalping", "options": {"probes": 11}})).status_code == 422
    assert (await client.post("/api/targets", json={"name": "bad", "host": "x.test", "type": "globalping", "options": {"measurement": "icmp"}})).status_code == 422
    assert (await client.post("/api/targets", json={"name": "bad", "host": "x.test", "type": "globalping", "options": {"measurement": "http", "expected_status": "abc"}})).status_code == 422
