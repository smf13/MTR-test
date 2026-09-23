"""Probe helpers and live HTTP/TCP paths with mocked transports."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app import probes
from app.probes import certificate_details, json_matches, json_path, ping_payload_bytes, run_dns, run_http, run_tcp, status_allowed


def test_certificate_details_from_peer_cert() -> None:
    class FakeSSL:
        def getpeercert(self) -> dict:
            return {
                "subject": ((("commonName", "portal.example.com"),), (("organizationName", "Example Corp"),)),
                "issuer": ((("countryName", "US"),), (("organizationName", "Let's Encrypt"),), (("commonName", "R11"),)),
                "notBefore": "Jun  1 00:00:00 2026 GMT",
                "notAfter": "Aug 30 23:59:59 2026 GMT",
                "serialNumber": "04AB",
                "subjectAltName": (("DNS", "portal.example.com"), ("DNS", "www.example.com"), ("IP Address", "192.0.2.1")),
            }

        def cipher(self) -> tuple:
            return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)

        def version(self) -> str:
            return "TLSv1.3"

    info = certificate_details(FakeSSL())
    assert info is not None
    assert info["subject"] == "portal.example.com" and info["subject_org"] == "Example Corp"
    assert info["issuer"] == "Let's Encrypt" and info["issuer_cn"] == "R11"
    assert info["not_before"] == "2026-06-01T00:00:00Z" and info["not_after"] == "2026-08-30T23:59:59Z" and isinstance(info["days_left"], int)
    assert info["san"] == ["portal.example.com", "www.example.com"] and info["serial"] == "04AB"
    assert info["protocol"] == "TLSv1.3" and info["cipher"] == "TLS_AES_256_GCM_SHA384"

    class Unverified:
        def getpeercert(self) -> dict:
            return {}  # what getpeercert() returns when the certificate was not validated

    assert certificate_details(Unverified()) is None


def test_ping_payload_matches_mtr_packet_size() -> None:
    # 64-byte packets like mtr's default: IPv4 20 + ICMP 8 + 36 payload; IPv6 40 + 8 + 16.
    assert ping_payload_bytes(64, ipv6=False) == 36
    assert ping_payload_bytes(64, ipv6=True) == 16
    assert ping_payload_bytes(28, ipv6=False) == 0 and ping_payload_bytes(20, ipv6=True) == 0


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


async def test_run_http_caps_the_body_it_keeps(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    big = b"needle" + b"x" * (probes.MAX_HTTP_BODY + 100_000)
    monkeypatch.setattr(probes, "_HTTP_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, content=big)))
    o = await run_http({"host": "http://svc.test/big", "type": "http"}, {"keyword": "needle"})
    assert o.reached and o.details["keyword_found"] is True
    assert o.details["truncated"] is True and o.details["bytes"] == len(big)


async def test_run_dns_random_prefix_measures_uncached_lookups(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.asyncresolver
    import dns.resolver

    queried: list[str] = []

    class FakeResolver:
        def __init__(self, configure: bool = True) -> None:
            self.nameservers: list[str] = []
            self.lifetime = self.timeout = 0.0

        async def resolve(self, name: str, rtype: str, tcp: bool = False) -> None:
            queried.append(name)
            raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.asyncresolver, "Resolver", FakeResolver)
    target = {"host": "example.test", "type": "dns"}

    o = await run_dns(target, {"random_prefix": True})
    assert o.ok and o.reached and o.loss_pct == 0.0 and o.avg_ms is not None
    assert o.details["rcode"] == "NXDOMAIN" and o.details["random_prefix"] is True and o.details["answers"] == []
    assert queried[0].endswith(".example.test") and len(queried[0]) > len("example.test") + 1 and o.details["queried_name"] == queried[0]
    again = await run_dns(target, {"random_prefix": True})
    assert again.details["queried_name"] != o.details["queried_name"]  # a fresh label every run

    strict = await run_dns(target, {"random_prefix": True, "expected": "192.0.2"})
    assert strict.ok and not strict.reached  # an expected answer still has to be present
    plain = await run_dns(target, {})
    assert plain.ok and not plain.reached and "NXDOMAIN" in (plain.error or "") and queried[-1] == "example.test"


def _dns_reply(wire: bytes, rcode: int = 0) -> bytes:
    import dns.message
    import dns.rrset

    query = dns.message.from_wire(wire)
    reply = dns.message.make_response(query)
    reply.set_rcode(rcode)
    if rcode == 0:
        reply.answer.append(dns.rrset.from_text(query.question[0].name, 60, "IN", "A", "192.0.2.53"))
    return reply.to_wire()


class _DnsUdp(asyncio.DatagramProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.transport.sendto(_dns_reply(data), addr)  # type: ignore[attr-defined]


async def _dns_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    # RFC 1035 4.2.2 (and RFC 7858 for TLS): every message is preceded by its two-byte length.
    size = int.from_bytes(await reader.readexactly(2), "big")
    reply = _dns_reply(await reader.readexactly(size))
    writer.write(len(reply).to_bytes(2, "big") + reply)
    await writer.drain()
    writer.close()


async def test_run_dns_over_udp_tcp_and_tls(live: None, tmp_path) -> None:
    import shutil
    import ssl
    import subprocess

    loop = asyncio.get_running_loop()
    udp, _ = await loop.create_datagram_endpoint(_DnsUdp, local_addr=("127.0.0.1", 0))
    udp_port = udp.get_extra_info("sockname")[1]
    tcp_server = await asyncio.start_server(_dns_stream, "127.0.0.1", 0)
    tcp_port = tcp_server.sockets[0].getsockname()[1]
    target = {"host": "example.test", "type": "dns"}
    try:
        o = await run_dns(target, {"transport": "udp", "resolver": "127.0.0.1", "resolver_port": udp_port})
        assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["transport"] == "udp" and o.details["port"] == udp_port
        assert o.details["nameserver"] == "127.0.0.1" and o.command == f"dns A example.test @127.0.0.1 -p {udp_port}"

        o = await run_dns(target, {"transport": "tcp", "resolver": "127.0.0.1", "resolver_port": tcp_port})
        assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["transport"] == "tcp" and o.command.endswith("+tcp")

        # Nothing listens for UDP on the TCP server's port pair: a UDP query there times out instead of silently using TCP.
        o = await run_dns(target, {"transport": "udp", "resolver": "127.0.0.1", "resolver_port": tcp_port, "timeout_sec": 0.5})
        assert o.ok and not o.reached and o.loss_pct == 100.0

        if shutil.which("openssl") is None:
            pytest.skip("openssl is needed to make a certificate for the DNS over TLS server")
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1",
             "-subj", "/CN=dot.test", "-addext", "subjectAltName=IP:127.0.0.1"],
            check=True, capture_output=True,
        )
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(cert, key)
        tls_server = await asyncio.start_server(_dns_stream, "127.0.0.1", 0, ssl=ctx)
        tls_port = tls_server.sockets[0].getsockname()[1]
        try:
            # A self-signed certificate fails verification, which is exactly what the check is there to catch...
            o = await run_dns(target, {"transport": "dot", "resolver": "127.0.0.1", "resolver_port": tls_port, "timeout_sec": 2})
            assert o.ok and not o.reached and o.error
            # ...and passes with verification switched off.
            o = await run_dns(target, {"transport": "dot", "resolver": "127.0.0.1", "resolver_port": tls_port, "verify_tls": False})
            assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["transport"] == "dot" and o.command.endswith("+tls")
        finally:
            tls_server.close()
    finally:
        udp.close()
        tcp_server.close()


async def test_run_dns_over_https_and_error_codes(live: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.asyncquery
    import dns.message

    calls: list[dict] = []
    rcode = {"value": 0}

    async def fake_https(q: dns.message.Message, where: str, **kw) -> dns.message.Message:
        calls.append({"url": where, **kw})
        return dns.message.from_wire(_dns_reply(q.to_wire(), rcode["value"]))

    async def fake_resolve(host: str, ip_version: str = "auto") -> str:
        return {"doh.test": "192.0.2.80"}[host]

    monkeypatch.setattr(dns.asyncquery, "https", fake_https)
    monkeypatch.setattr(probes, "resolve_host", fake_resolve)
    target = {"host": "example.test", "type": "dns"}

    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test"})
    assert o.reached and o.details["answers"] == ["192.0.2.53"] and o.details["resolver"] == "https://doh.test/dns-query"
    assert calls[-1]["url"] == "https://doh.test/dns-query" and calls[-1]["bootstrap_address"] == "192.0.2.80" and calls[-1]["verify"] is True
    assert o.details["nameserver"] == "192.0.2.80" and o.details["port"] == 443 and o.command.endswith("+https")

    o = await run_dns(target, {"transport": "doh", "resolver": "https://doh.test:8443/q", "verify_tls": False})
    assert o.reached and calls[-1]["url"] == "https://doh.test:8443/q" and calls[-1]["verify"] is False and o.details["port"] == 8443

    rcode["value"] = 3  # NXDOMAIN
    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test"})
    assert o.ok and not o.reached and o.details["rcode"] == "NXDOMAIN"
    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test", "random_prefix": True})
    assert o.reached and o.details["rcode"] == "NXDOMAIN"

    rcode["value"] = 2  # SERVFAIL
    o = await run_dns(target, {"transport": "doh", "resolver": "doh.test"})
    assert o.ok and not o.reached and o.details["rcode"] == "SERVFAIL" and "SERVFAIL" in (o.error or "")


def test_dns_options_validation() -> None:
    from app.models import validate_options

    assert validate_options("dns", {})["transport"] == "udp"
    assert validate_options("dns", {"transport": "doh", "resolver": " https://dns.example/dns-query "})["resolver"] == "https://dns.example/dns-query"
    for bad in (
        {"transport": "dot"},  # the system resolver has no TLS endpoint
        {"transport": "doh"},
        {"transport": "tcp", "resolver": "https://dns.example/dns-query"},
        {"transport": "doh", "resolver": "http://dns.example/dns-query"},
        {"transport": "doh", "resolver": "https://dns.example:99999/dns-query"},
        {"transport": "doh", "resolver": "https:///dns-query"},
        {"resolver_port": 5353},
        {"transport": "smtp", "resolver": "192.0.2.1"},
    ):
        with pytest.raises(ValueError):
            validate_options("dns", bad)


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
