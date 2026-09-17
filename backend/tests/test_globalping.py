"""Globalping probe type: request building, result parsing, API errors (HTTP mocked) and the simulated end-to-end path."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest
from httpx import AsyncClient

from app import globalping
from app.globalping import build_request, hop_from_result, parse_ping_probes, probe_label, run_globalping, run_globalping_mtr
from helpers import wait_for_runs

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


def fake_api(kind: str, result: dict[str, Any], *, post_status: int = 202, post_body: dict[str, Any] | None = None, polls_before_finish: int = 2) -> tuple[Callable[[httpx.Request], httpx.Response], dict[str, Any]]:
    """MockTransport handler: POST creates measurement 'm1'; GET reports in-progress, then finished."""
    state: dict[str, Any] = {"gets": 0, "posts": [], "auth": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["auth"].append(request.headers.get("authorization"))
        if request.method == "POST":
            state["posts"].append(json.loads(request.content))
            if post_status != 202:
                return httpx.Response(post_status, json=post_body or {"error": {"type": "x", "message": "nope"}}, headers={"x-ratelimit-reset": "1200"})
            return httpx.Response(202, json={"id": "m1", "probesCount": 1}, headers={"x-ratelimit-limit": "250", "x-ratelimit-remaining": "249", "x-ratelimit-reset": "3600"})
        state["gets"] += 1
        finished = state["gets"] >= polls_before_finish
        return httpx.Response(
            200,
            json={"id": "m1", "type": kind, "status": "finished" if finished else "in-progress", "target": "cdn.jsdelivr.net", "probesCount": 1,
                  "results": [{"probe": PROBE, "result": result if finished else {"status": "in-progress"}}]},
        )

    return handler, state


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(globalping, "_FORCE_LIVE", True)
    monkeypatch.setattr(globalping, "POLL_INTERVAL", 0.01)


def test_build_request() -> None:
    body = build_request({"host": "cdn.jsdelivr.net", "count": 40, "ip_version": "6", "protocol": "tcp", "port": 443}, {"measurement": "mtr", "location": "Germany", "probes": 3})
    assert body["type"] == "mtr" and body["limit"] == 1 and body["locations"] == [{"magic": "Germany"}]
    assert body["measurementOptions"] == {"packets": 16, "ipVersion": 6, "protocol": "TCP", "port": 443}
    body = build_request({"host": "1.1.1.1", "count": 4, "ip_version": "4"}, {"measurement": "ping", "location": "world", "probes": 2})
    assert body["type"] == "ping" and body["limit"] == 2 and "locations" not in body
    assert body["measurementOptions"] == {"packets": 4}  # ipVersion is only meaningful for host names


def test_parsers() -> None:
    assert probe_label(PROBE) == "Falkenstein, DE · AS24940 Hetzner Online"
    hops = [hop_from_result(i, h) for i, h in enumerate(MTR_RESULT["hops"])]
    assert [h.hop_no for h in hops] == [1, 2, 3]
    assert hops[0].ip == "213.133.117.180" and hops[0].hostname == "_gateway" and hops[0].asn == "AS24940" and hops[0].avg_ms == 0.7 and hops[0].last_ms == 0.96
    assert hops[1].ip is None and hops[1].hostname is None and hops[1].loss_pct == 100.0 and hops[1].avg_ms is None and hops[1].received == 0
    assert hops[2].asn == "AS13335" and hops[2].jitter_avg_ms == 0 and hops[2].best_ms == 5.593
    probes = parse_ping_probes({"results": [{"probe": PROBE, "result": PING_RESULT}]})
    assert probes[0]["label"].startswith("Falkenstein") and probes[0]["avg"] == 5.073 and probes[0]["rtts"] == [5.23, 5.0, 4.99] and probes[0]["resolved"] == "151.101.129.229"


async def test_run_globalping_ping(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    handler, state = fake_api("ping", PING_RESULT)
    monkeypatch.setattr(globalping, "_TRANSPORT", httpx.MockTransport(handler))
    t = {"host": "cdn.jsdelivr.net", "count": 3, "ip_version": "auto", "type": "globalping"}
    o = await run_globalping(t, {"measurement": "ping", "location": "Germany", "probes": 1}, {"globalping_token": "tok"})
    assert o.ok and o.reached and o.loss_pct == 0.0 and o.sent == 3 and o.dst_ip == "151.101.129.229"
    assert o.avg_ms == pytest.approx(5.073, abs=0.01) and o.best_ms == 4.99 and o.worst_ms == 5.23
    assert o.details["url"].endswith("m1") and o.details["rate_limit"]["remaining"] == "249" and o.details["probes"][0]["city"] == "Falkenstein"
    assert state["posts"][0]["locations"] == [{"magic": "Germany"}] and state["auth"][0] == "Bearer tok" and state["gets"] == 2
    assert "globalping ping cdn.jsdelivr.net from Germany" in (o.command or "")


async def test_run_globalping_api_errors(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    handler, _ = fake_api("ping", PING_RESULT, post_status=422, post_body={"error": {"type": "no_probes_found", "message": "No matching IPv4 probes available."}})
    monkeypatch.setattr(globalping, "_TRANSPORT", httpx.MockTransport(handler))
    o = await run_globalping({"host": "x.test", "count": 3, "type": "globalping"}, {"location": "nowhere"}, {})
    assert not o.ok and "No matching IPv4 probes" in (o.error or "") and o.details["location"] == "nowhere"

    handler, _ = fake_api("ping", PING_RESULT, post_status=429, post_body={"error": {"type": "too_many_requests", "message": "Too Many Requests"}})
    monkeypatch.setattr(globalping, "_TRANSPORT", httpx.MockTransport(handler))
    o = await run_globalping({"host": "x.test", "count": 3, "type": "globalping"}, {}, {})
    assert not o.ok and "rate limit" in (o.error or "") and "1200 s" in (o.error or "")

    res = await run_globalping_mtr({"host": "x.test", "count": 3, "type": "globalping"}, {"measurement": "mtr"}, {})
    assert not res.ok and "rate limit" in (res.error or "")


async def test_run_globalping_mtr(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    handler, state = fake_api("mtr", MTR_RESULT)
    monkeypatch.setattr(globalping, "_TRANSPORT", httpx.MockTransport(handler))
    t = {"host": "cdn.jsdelivr.net", "count": 2, "ip_version": "auto", "protocol": "icmp", "port": None, "type": "globalping"}
    res = await run_globalping_mtr(t, {"measurement": "mtr", "location": "DE"}, {})
    assert res.ok and res.dst_ip == "104.17.207.5" and len(res.hops) == 3 and res.hops[-1].ip == res.dst_ip
    assert res.src == "Falkenstein, DE · AS24940 Hetzner Online" and res.details["probe"]["label"] == res.src and res.details["measurement"] == "mtr"
    assert state["posts"][0]["measurementOptions"] == {"packets": 2, "protocol": "ICMP"} and state["posts"][0]["limit"] == 1


async def test_globalping_targets_end_to_end(client: AsyncClient) -> None:
    """Simulation mode: a ping target becomes a summary run with per-probe details, an mtr target a path run with hops."""
    ping = await client.post("/api/targets", json={"name": "GP ping", "host": "cdn.jsdelivr.net", "type": "globalping", "interval_sec": 60, "count": 4, "options": {"measurement": "ping", "location": "Germany", "probes": 2}})
    assert ping.status_code == 201, ping.text
    assert ping.json()["alert_latency_ms"] == 200 and ping.json()["options"] == {"measurement": "ping", "location": "Germany", "probes": 2}
    mtr = await client.post("/api/targets", json={"name": "GP mtr", "host": "cdn.jsdelivr.net", "type": "globalping", "interval_sec": 60, "count": 3, "options": {"measurement": "mtr", "location": "aws-eu-west-1"}})
    assert mtr.status_code == 201, mtr.text

    run = (await client.get(f"/api/runs/{(await wait_for_runs(client, ping.json()['id'], 1))[0]['id']}")).json()
    assert run["status"] == "ok" and run["reached"] and run["hop_count"] == 0 and run["target_type"] == "globalping"
    assert len(run["details"]["probes"]) == 2 and run["details"]["measurement"] == "ping" and run["avg_ms"] is not None

    run = (await client.get(f"/api/runs/{(await wait_for_runs(client, mtr.json()['id'], 1))[0]['id']}")).json()
    assert run["status"] == "ok" and run["hop_count"] > 0 and len(run["hops"]) == run["hop_count"] and run["hops"][-1]["ip"] == run["dst_ip"]
    assert "·" in run["src"] and run["details"]["measurement"] == "mtr" and run["details"]["probe"]["label"] == run["src"]
    summary = (await client.get(f"/api/targets/{mtr.json()['id']}/hops/summary?range=1h")).json()
    assert summary["total_runs"] >= 1 and summary["hops"][-1]["primary"]["ip"] == run["dst_ip"]

    assert (await client.post("/api/targets", json={"name": "bad", "host": "x.test", "type": "globalping", "options": {"probes": 11}})).status_code == 422
    assert (await client.post("/api/targets", json={"name": "bad", "host": "x.test", "type": "globalping", "options": {"measurement": "http"}})).status_code == 422
